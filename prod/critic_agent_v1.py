#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
critic_agent_v1.py — Curadoria Multi-LLM para o Radar xTechs

Opção C — Papéis especializados:
  Papel 1: Anthropic claude-opus-4-8   → gerou intel_output.json (via analyzer)
  Papel 2: Google gemini-2.0-flash     → crítico / advocatus diaboli
  Papel 3: Anthropic claude-opus-4-8   → consolidação final

Fluxo:
  1. Lê intel_output.json existente (já gerado pelo analyzer)
  2. Envia para Gemini Flash com prompt de crítica estruturada
  3. Envia análise original + crítica Gemini para Opus consolidador
  4. Atualiza intel_output.json com campos enriquecidos pelo consolidador
  5. Registra audit_trail em intel_output.json["critic_audit"]

Requer:
  pip install anthropic google-genai   # ou google-generativeai
  ANTHROPIC_API_KEY no .env
  GOOGLE_API_KEY no .env

Uso:
  python critic_agent_v1.py --input intel_output.json
  python critic_agent_v1.py --input intel_output.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict

try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv()

BRASILIA = timezone(timedelta(hours=-3))

ANTHROPIC_MODEL_CONSOLIDATOR = "claude-opus-4-8"
GOOGLE_MODEL_CRITIC = "gemini-2.5-flash"

DEFAULT_INPUT = str(Path(__file__).parent / "intel_output.json")


# ─── Prompts ──────────────────────────────────────────────────────────────────

CRITIC_SYSTEM = """Você é um analista sênior de inteligência tecnológica e estratégia empresarial.
Sua função é revisar criticamente uma análise do Radar xTechs elaborada pela Anthropic Claude.

Você deve atuar como advocatus diaboli: questionar premissas, identificar blind spots,
apontar inconsistências e sugerir perspectivas alternativas — sem reescrever o conteúdo,
apenas marcando o que precisa de ajuste e por quê.

Preste atenção especial a CAUSALIDADE INVERTIDA — afirmações onde a direção do efeito
de um indicador de mercado sobre uma xTech está errada. Exemplo clássico a verificar:
"Brent caindo torna armazenamento energético mais competitivo frente a termelétricas"
é ERRADO — Brent caindo barateia termelétricas, reduz o PLD e pressiona projetos BESS.
Flagre qualquer caso semelhante no campo "causalidade_invertida" da sua resposta.

Responda sempre em JSON estruturado, em Português do Brasil."""

CRITIC_PROMPT_TEMPLATE = """Analise criticamente o seguinte Radar xTechs gerado por IA.

CICLO: {ciclo}
FRENTES MONITORADAS: EnergyTech, CleanTech, AgriTech, DeepTech, FinTech

=== ANÁLISE A REVISAR ===
{resumo_analise}

=== VETORES ESTRATÉGICOS (top 5 por prioridade) ===
{vetores_top5}

=== SINAIS DE MAIOR SCORE ===
{sinais_top5}

Responda em JSON com a seguinte estrutura:
{{
  "aprovacao_geral": "aprovado" | "aprovado_com_ressalvas" | "requer_revisao",
  "score_confianca": <0-10>,
  "pontos_fortes": ["<ponto>", ...],
  "blind_spots": ["<blind spot identificado>", ...],
  "causalidade_invertida": ["<caso onde a direção do efeito foi afirmada errada — ex: 'Brent caindo torna BESS mais competitivo' quando o correto é o oposto'>", ...],
  "inconsistencias": ["<inconsistência>", ...],
  "vetores_contestados": [
    {{
      "xtech": "<nome>",
      "motivo": "<por que questionar>",
      "perspectiva_alternativa": "<outra leitura possível>"
    }}
  ],
  "sinais_suspeitos": [
    {{
      "titulo": "<título do sinal>",
      "motivo": "<por que suspeitar>"
    }}
  ],
  "recomendacoes": ["<recomendação para o consolidador>", ...],
  "contexto_global_ignorado": "<perspectiva global relevante não capturada>"
}}"""

CONSOLIDATOR_SYSTEM = """Você é o analista-chefe do Radar xTechs — Efagundes Think Tank.
Você gerou a análise original e agora recebe uma crítica de um revisor externo (Google Gemini).

Sua tarefa é consolidar a análise original com os insights da crítica, produzindo uma
versão refinada e mais robusta. Aceite críticas válidas, rejeite as improcedentes com justificativa.

Responda em JSON estruturado, em Português do Brasil."""

CONSOLIDATOR_PROMPT_TEMPLATE = """Você produziu a análise original do Radar xTechs.
Um revisor externo (Google Gemini) fez uma análise crítica. Consolide as perspectivas.

=== CRÍTICA DO REVISOR EXTERNO ===
{critica_json}

=== VETORES ORIGINAIS (para ajuste se necessário) ===
{vetores_top5}

Com base na crítica, produza um JSON de consolidação:
{{
  "consolidacao_aprovada": true | false,
  "ajustes_aplicados": ["<ajuste aceito>", ...],
  "criticas_rejeitadas": [
    {{
      "critica": "<texto da crítica>",
      "justificativa_rejeicao": "<por que rejeitou>"
    }}
  ],
  "vetores_ajustados": [
    {{
      "xtech": "<nome>",
      "campo": "<campo ajustado>",
      "valor_anterior": "<valor>",
      "valor_novo": "<valor>",
      "motivo": "<motivo do ajuste>"
    }}
  ],
  "nota_qualidade_final": <0-10>,
  "observacao_editorial": "<síntese do processo de curadoria>"
}}"""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def resumir_analise(data: Dict[str, Any]) -> str:
    dash = data.get("dashboard") or {}
    resumo = dash.get("resumo_executivo") or dash.get("contexto_macro") or ""
    sala = data.get("sala_situacao") or {}
    if isinstance(sala, dict):
        resumo += "\n\n" + (sala.get("contexto") or "")
    return resumo.strip()[:3000]


def vetores_top5_txt(data: Dict[str, Any]) -> str:
    from operator import itemgetter
    vetores = data.get("vetores_estrategicos") or []
    quad_rank = {"Mobilizar Agora": 4, "Capturar Vantagem": 3, "Monitorar Vetores": 2, "Ruído Operacional": 1}
    sorted_v = sorted(
        vetores,
        key=lambda v: (
            quad_rank.get(v.get("quadrante_executivo", ""), 0),
            float(v.get("pressao_estrategica") or 0),
        ),
        reverse=True,
    )[:5]
    lines = []
    for v in sorted_v:
        nome = v.get("xtech") or v.get("frente") or "?"
        quad = v.get("quadrante_executivo", "")
        pressao = v.get("pressao_estrategica", 0)
        tese = (v.get("tese_central") or "")[:150]
        lines.append(f"- {nome} | {quad} | pressão={pressao} | {tese}")
    return "\n".join(lines)


def sinais_top5_txt(data: Dict[str, Any]) -> str:
    itens = data.get("itens") or []
    top = sorted(itens, key=lambda x: float(x.get("score_final") or 0), reverse=True)[:5]
    lines = []
    for it in top:
        titulo = it.get("titulo") or it.get("title") or "?"
        score = it.get("score_final", 0)
        tipo = it.get("tipo_sinal", "")
        lines.append(f"- [{score}] {titulo} ({tipo})")
    return "\n".join(lines)


def call_gemini(prompt: str, system: str) -> str:
    """Chama Google Gemini via google-genai SDK (novo)."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError(
            "google-genai não instalado. pip install google-genai"
        )

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY ou GEMINI_API_KEY não configurada no .env")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=GOOGLE_MODEL_CRITIC,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )
    return response.text


def call_anthropic(prompt: str, system: str) -> str:
    """Chama Anthropic Claude via SDK oficial."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic não instalado. pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não configurada no .env")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=ANTHROPIC_MODEL_CONSOLIDATOR,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    # Extrai texto (ignora thinking blocks)
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def parse_json_response(text: str) -> Dict[str, Any]:
    """Extrai JSON da resposta do LLM (tolerante a markdown code fences)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return {"raw_response": text, "parse_error": True}


# ─── Pipeline principal ───────────────────────────────────────────────────────

def run_critic_pipeline(input_path: Path, dry_run: bool = False) -> int:
    print(f"  · Carregando {input_path}")
    data = json.loads(input_path.read_text(encoding="utf-8"))

    ciclo = (data.get("ciclo_id") or
             (data.get("dashboard") or {}).get("ciclo") or
             datetime.now(BRASILIA).strftime("%Y-%m-%d"))

    resumo = resumir_analise(data)
    vetores_txt = vetores_top5_txt(data)
    sinais_txt = sinais_top5_txt(data)

    audit: Dict[str, Any] = {
        "ciclo": ciclo,
        "timestamp": datetime.now(BRASILIA).isoformat(),
        "critic_model": GOOGLE_MODEL_CRITIC,
        "consolidator_model": ANTHROPIC_MODEL_CONSOLIDATOR,
        "dry_run": dry_run,
    }

    # ── Papel 2: Gemini crítico ───────────────────────────────────────────────
    print(f"  · [{GOOGLE_MODEL_CRITIC}] Revisão crítica em andamento...")
    t0 = time.time()

    critic_prompt = CRITIC_PROMPT_TEMPLATE.format(
        ciclo=ciclo,
        resumo_analise=resumo,
        vetores_top5=vetores_txt,
        sinais_top5=sinais_txt,
    )

    if dry_run:
        print("  · [dry-run] Gemini não chamado")
        critica_raw = json.dumps({"aprovacao_geral": "aprovado", "score_confianca": 8,
                                  "pontos_fortes": ["dry-run"], "blind_spots": [],
                                  "inconsistencias": [], "vetores_contestados": [],
                                  "sinais_suspeitos": [], "recomendacoes": [],
                                  "contexto_global_ignorado": ""})
    else:
        critica_raw = call_gemini(critic_prompt, CRITIC_SYSTEM)

    critica = parse_json_response(critica_raw)
    audit["gemini_critica"] = critica
    t_gemini = time.time() - t0
    print(f"  ✓ Gemini concluído em {t_gemini:.1f}s — "
          f"aprovação: {critica.get('aprovacao_geral', '?')} | "
          f"confiança: {critica.get('score_confianca', '?')}")

    if critica.get("blind_spots"):
        for bs in critica["blind_spots"][:3]:
            print(f"    · blind spot: {bs}")

    # ── Papel 3: Anthropic consolidador ──────────────────────────────────────
    print(f"  · [{ANTHROPIC_MODEL_CONSOLIDATOR}] Consolidação em andamento...")
    t1 = time.time()

    consolidator_prompt = CONSOLIDATOR_PROMPT_TEMPLATE.format(
        critica_json=json.dumps(critica, ensure_ascii=False, indent=2),
        vetores_top5=vetores_txt,
    )

    if dry_run:
        print("  · [dry-run] Consolidador não chamado")
        consolidacao_raw = json.dumps({"consolidacao_aprovada": True,
                                       "ajustes_aplicados": [],
                                       "criticas_rejeitadas": [],
                                       "vetores_ajustados": [],
                                       "nota_qualidade_final": 8,
                                       "observacao_editorial": "dry-run"})
    else:
        consolidacao_raw = call_anthropic(consolidator_prompt, CONSOLIDATOR_SYSTEM)

    consolidacao = parse_json_response(consolidacao_raw)
    audit["anthropic_consolidacao"] = consolidacao
    t_consolidator = time.time() - t1
    print(f"  ✓ Consolidador concluído em {t_consolidator:.1f}s — "
          f"nota final: {consolidacao.get('nota_qualidade_final', '?')}")

    # ── Aplica ajustes de vetores no intel_output ─────────────────────────────
    ajustes = consolidacao.get("vetores_ajustados") or []
    n_ajustes = 0
    if ajustes and not dry_run:
        vetores = data.get("vetores_estrategicos") or []
        vetor_map = {
            (v.get("xtech") or v.get("frente") or "").lower(): v
            for v in vetores
        }
        for ajuste in ajustes:
            chave = (ajuste.get("xtech") or "").lower()
            campo = ajuste.get("campo")
            novo_valor = ajuste.get("valor_novo")
            if chave in vetor_map and campo and novo_valor is not None:
                vetor_map[chave][campo] = novo_valor
                n_ajustes += 1

    audit["ajustes_aplicados_count"] = n_ajustes

    # ── Grava resultado de volta no intel_output ──────────────────────────────
    data["critic_audit"] = audit

    if not dry_run:
        input_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✓ intel_output.json atualizado ({n_ajustes} vetores ajustados)")

        # Salva audit completo em arquivo separado
        audit_path = input_path.parent / f"critic_audit_{ciclo}.json"
        audit_path.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  ✓ Audit salvo: {audit_path.name}")
    else:
        print("  · [dry-run] intel_output.json não modificado")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Critic Agent v1 — Curadoria Multi-LLM Radar xTechs")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="intel_output.json")
    parser.add_argument("--dry-run", action="store_true", help="Não chama APIs, não grava")
    args = parser.parse_args()

    print(f"\n{'=' * 66}")
    print("  CRITIC AGENT v1 — Curadoria Multi-LLM")
    print(f"  Critic: {GOOGLE_MODEL_CRITIC} | Consolidador: {ANTHROPIC_MODEL_CONSOLIDATOR}")
    print(f"  {'=' * 62}")

    return run_critic_pipeline(Path(args.input), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
