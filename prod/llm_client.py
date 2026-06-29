#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_client.py — Cliente LLM unificado: Anthropic (primário) + OpenAI (fallback)

Expõe uma única função:
    call_llm(system, user, json_schema, schema_name, model) -> dict

Ordem de prioridade:
  1. ANTHROPIC_API_KEY → Claude (Anthropic) — primário
  2. OPENAI_API_KEY    → GPT  (OpenAI)      — fallback automático

Fallback ativado em: quota esgotada, sobrecarga (429/529/503/overloaded).
Erros de schema ou autenticação propagam a exceção normalmente.

Modelos padrão (configuráveis no .env):
  Anthropic → MODELO_BRIEFING   (default: claude-opus-4-8)
  OpenAI    → OPENAI_MODEL      (default: gpt-5)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ── Constantes ────────────────────────────────────────────────────────────────

ANTHROPIC_DEFAULT_MODEL = os.getenv("MODELO_BRIEFING", "claude-opus-4-8")
OPENAI_DEFAULT_MODEL    = os.getenv("OPENAI_MODEL",    "gpt-5")

# Erros que ativam fallback (quota/disponibilidade — não erros de código)
_FALLBACK_TRIGGERS = (
    "429", "529", "503", "overloaded", "quota", "billing",
    "rate_limit", "capacity", "RESOURCE_EXHAUSTED", "depleted",
)


# ── Anthropic ────────────────────────────────────────────────────────────────

def _schema_to_instruction(json_schema: dict[str, Any]) -> str:
    """Converte JSON Schema em instrução textual compacta para o prompt."""
    return (
        f"Responda EXCLUSIVAMENTE com JSON válido conforme este schema:\n"
        f"```json\n{json.dumps(json_schema, ensure_ascii=False, indent=2)}\n```\n"
        f"Não inclua texto antes ou depois do JSON."
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Extrai o primeiro objeto JSON de uma string, ignorando markdown."""
    # Remover blocos ```json ... ```
    clean = re.sub(r"```(?:json)?\s*", "", text).strip()
    # Encontrar primeiro { ... } de nível raiz
    start = clean.find("{")
    if start == -1:
        raise ValueError(f"Nenhum JSON encontrado na resposta: {text[:200]}")
    # Balancear chaves
    depth, end = 0, -1
    for i, ch in enumerate(clean[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        raise ValueError("JSON incompleto na resposta do modelo.")
    return json.loads(clean[start:end])


def _call_anthropic(
    system: str,
    user: str,
    json_schema: dict[str, Any],
    model: str,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    """Chama Claude via Anthropic SDK com instrução de JSON estruturado."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY não definida no .env")

    client = anthropic.Anthropic(api_key=api_key)

    # Injetar instrução de schema no sistema
    system_with_schema = f"{system}\n\n{_schema_to_instruction(json_schema)}"

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_with_schema,
        messages=[{"role": "user", "content": user}],
    )

    raw = response.content[0].text
    return _extract_json(raw)


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(
    system: str,
    user: str,
    json_schema: dict[str, Any],
    schema_name: str,
    model: str,
) -> dict[str, Any]:
    """Chama OpenAI Responses API com json_schema structured output."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": json_schema,
            }
        },
    )
    raw = getattr(response, "output_text", None)
    if not raw:
        raw = response.output[0].content[0].text  # type: ignore[index]
    return json.loads(raw)


# ── Interface pública ─────────────────────────────────────────────────────────

def call_llm(
    system: str,
    user: str,
    json_schema: dict[str, Any],
    schema_name: str = "response",
    model: str | None = None,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    """
    Chama o LLM disponível com structured JSON output.

    Ordem de prioridade:
      1. Anthropic / Claude  (ANTHROPIC_API_KEY)
      2. OpenAI / GPT        (OPENAI_API_KEY) — fallback automático

    Args:
        system:      System prompt.
        user:        User message / payload serializado.
        json_schema: JSON Schema dict que o modelo deve respeitar.
        schema_name: Nome do schema (usado pelo OpenAI; ignorado pelo Anthropic).
        model:       Override de modelo. Se None, usa o default do provedor.
        max_tokens:  Limite de tokens de saída (default 8192). Use 16384 para
                     outputs longos como briefings completos com markdown extenso.

    Returns:
        dict com a resposta parseada conforme o schema.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openai_key    = os.environ.get("OPENAI_API_KEY", "")

    # Anthropic primário — fallback automático para OpenAI em quota/sobrecarga
    if anthropic_key:
        anthropic_model = model or ANTHROPIC_DEFAULT_MODEL
        try:
            return _call_anthropic(system, user, json_schema, anthropic_model, max_tokens=max_tokens)
        except Exception as exc:
            err_str = str(exc)
            if openai_key and any(k in err_str for k in _FALLBACK_TRIGGERS):
                print(
                    f"[llm_client] Anthropic indisponível ({err_str[:100]}). "
                    f"Fallback → OpenAI.",
                    flush=True,
                )
            else:
                raise  # erro de schema/autenticação — não fazer fallback silencioso

    if openai_key:
        openai_model = model or OPENAI_DEFAULT_MODEL
        return _call_openai(system, user, json_schema, schema_name, openai_model)

    raise RuntimeError(
        "Nenhuma chave de API configurada. "
        "Defina ANTHROPIC_API_KEY ou OPENAI_API_KEY no .env"
    )


def active_provider() -> str:
    """Retorna descrição do provedor ativo e fallback configurado."""
    anthropic = os.environ.get("ANTHROPIC_API_KEY", "")
    openai    = os.environ.get("OPENAI_API_KEY", "")
    if anthropic and openai:
        return f"anthropic ({ANTHROPIC_DEFAULT_MODEL}) → openai fallback"
    if anthropic:
        return f"anthropic ({ANTHROPIC_DEFAULT_MODEL})"
    if openai:
        return f"openai ({OPENAI_DEFAULT_MODEL})"
    return "nenhum"
