#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disruption_emergente_v1.py — Novas Curvas S / Sinais de Disrupção (Christensen)

Cruza duas fontes já existentes do pipeline pra achar candidatas a disrupção:
tecnologias em estágio INICIAL da curva de maturidade (hype_cycle_live.phase
∈ {Sinal Emergente, Narrativa Exponencial}) E com sinal de ACELERAÇÃO
(hype_cycle_live.trend == '↗'). Uma tecnologia jovem na curva S e acelerando
é candidata a interceptar as dominantes por baixo — o padrão de disrupção
descrito por Christensen (1997): desempenho inicialmente inferior, em
mercado adjacente, por isso incumbentes ignoram até ser tarde.

Não é previsão de qual tecnologia VAI disromper — é sinalização de qual
JÁ TEM as duas condições estruturais (estágio inicial + aceleração) que
precedem disrupções históricas. O julgamento do LLM (tese_disrupcao_curta)
interpreta o mecanismo específico, não prevê o resultado.

Piso de amostra deliberadamente baixo (min_n=2, não o piso=10 usado em
outras features): "sinal emergente" por definição tem poucos sinais — exigir
volume alto aqui eliminaria a categoria inteira que o card existe pra cobrir.
Ainda assim, n=1 é descartado (ruído de um único sinal, não um padrão).

Degradação graciosa: se o julgamento do LLM falhar para uma tecnologia, ela
ainda aparece no resultado com uma síntese estatística no lugar da tese —
nunca é descartada silenciosamente (mesma disciplina do acceleration_alerts_v1).

Uso:
  python disruption_emergente_v1.py
  python disruption_emergente_v1.py --input intel_output.json
  python disruption_emergente_v1.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

ROOT_DIR      = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT_DIR / "intel_output.json"

FASES_INICIAIS = {"Sinal Emergente", "Narrativa Exponencial"}
MIN_N_SINAIS   = 2      # piso de amostra deliberadamente baixo — ver docstring
MAX_CANDIDATOS = 4      # mesmo limite cognitivo dos outros cards de julgamento

MODELO_JULGAMENTO = "claude-sonnet-5"

ENQUADRAMENTO = (
    "Tecnologias jovens acelerando podem interceptar as dominantes por baixo — o padrão de "
    "disrupção de Christensen. Isto sinaliza condição estrutural (estágio inicial + aceleração), "
    "não prevê qual tecnologia vai disromper."
)


# ─── Seleção de candidatas ──────────────────────────────────────────────────

def selecionar_candidatos_disrupcao(
    hype_cycle_live: list[dict], min_n: int = MIN_N_SINAIS, max_k: int = MAX_CANDIDATOS
) -> list[dict]:
    candidatos = [
        t for t in (hype_cycle_live or [])
        if t.get("phase") in FASES_INICIAIS
        and t.get("trend") == "↗"
        and (t.get("n") or 0) >= min_n
    ]
    return sorted(candidatos, key=lambda t: float(t.get("score") or 0), reverse=True)[:max_k]


# ─── Julgamento por LLM ─────────────────────────────────────────────────────

def _montar_prompt_disrupcao(tech: dict) -> str:
    return f"""Você está avaliando se a tecnologia "{tech.get('id','')}" (frente {tech.get('frente','—')}), hoje na fase "{tech.get('phase','')}" da curva de maturidade (tendência de aceleração, score {tech.get('score','—')}/10, baseado em {tech.get('n', 0)} sinal(is) monitorado(s) neste ciclo), pode INTERCEPTAR tecnologias dominantes e maduras — o padrão descrito por Christensen: disrupção nasce fora da curva dominante, com desempenho inicialmente inferior, em mercado adjacente, por isso incumbentes ignoram até ser tarde.

Contexto observado sobre esta tecnologia neste ciclo:
{tech.get('signal','') or 'Sem contexto adicional disponível.'}

Retorne EXATAMENTE este JSON (sem texto fora do JSON):
{{
  "tese_disrupcao_curta": "1-2 frases: por que ESTA tecnologia, especificamente, tem potencial de interceptar a(s) dominante(s) — mecanismo concreto, não genérico",
  "o_que_observar": "1 frase: o sinal concreto que confirmaria a tese se aparecesse nos próximos ciclos",
  "tecnologia_dominante_interceptada": "nome de uma tecnologia madura/dominante que esta poderia interceptar (mesma frente ou adjacente), ou null se não for claro"
}}

REGRAS:
- Se a evidência for fraca demais pra sustentar uma tese específica, diga isso explicitamente na tese em vez de inventar um mecanismo — honestidade sobre incerteza vale mais que uma tese genérica.
- acentuação correta, SEM NEGRITO.
"""


def _extrair_json_valido(texto: str) -> str:
    texto = texto.strip()
    if texto.startswith("```"):
        linhas = texto.split("\n")
        texto = "\n".join(linhas[1:])
        if texto.rstrip().endswith("```"):
            texto = "\n".join(texto.rstrip().split("\n")[:-1])
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    return m.group(0) if m else texto


def _chamar_claude_julgamento(prompt: str) -> str:
    import os
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não configurada no .env")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODELO_JULGAMENTO,
        max_tokens=900,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def julgar_disrupcao(tech: dict) -> dict:
    prompt = _montar_prompt_disrupcao(tech)
    texto = _chamar_claude_julgamento(prompt)
    veredito = json.loads(_extrair_json_valido(texto))
    return {
        "tese_disrupcao_curta": veredito.get("tese_disrupcao_curta") or "",
        "o_que_observar": veredito.get("o_que_observar") or "",
        "tecnologia_dominante_interceptada": veredito.get("tecnologia_dominante_interceptada"),
    }


# ─── Orquestração ───────────────────────────────────────────────────────────

def build_disrupcao_emergente(hist_data: dict, min_n: int = MIN_N_SINAIS, max_k: int = MAX_CANDIDATOS) -> list[dict]:
    hcl = hist_data.get("hype_cycle_live") or []
    candidatos = selecionar_candidatos_disrupcao(hcl, min_n=min_n, max_k=max_k)

    resultado = []
    for tech in candidatos:
        try:
            veredito = julgar_disrupcao(tech)
        except Exception as exc:
            print(f"  ⚠ julgamento de disrupção falhou para '{tech.get('id')}': {exc} — usando síntese estatística")
            sinal = (tech.get("signal") or "")
            sinal_limpo = re.sub(r"<[^>]+>", "", sinal).strip()
            veredito = {
                "tese_disrupcao_curta": sinal_limpo[:220] or "Sem julgamento disponível neste ciclo.",
                "o_que_observar": "Acompanhar evolução de score e volume de sinais nos próximos ciclos.",
                "tecnologia_dominante_interceptada": None,
            }
        resultado.append({
            "tecnologia": tech.get("id"),
            "frente": tech.get("frente"),
            "fase_curva_s": tech.get("phase"),
            "aceleracao": tech.get("trend"),
            "score": tech.get("score"),
            "n_sinais": tech.get("n"),
            "cor": tech.get("color"),
            **veredito,
        })
    return resultado


def run_disruption(intel_path: Path, dry_run: bool = False) -> list[dict]:
    intel = json.loads(intel_path.read_text(encoding="utf-8"))
    hist_data = intel.get("hist_data") or {}

    if dry_run:
        candidatos = selecionar_candidatos_disrupcao(hist_data.get("hype_cycle_live") or [])
        print(f"  [dry-run] {len(candidatos)} candidata(s) a disrupção (nenhuma chamada LLM feita)")
        for c in candidatos:
            print(f"    {c['id']}: fase={c['phase']} trend={c['trend']} score={c['score']} n={c['n']}")
        return []

    resultado = build_disrupcao_emergente(hist_data)
    print(f"  ✓ disrupcao_emergente — {len(resultado)} candidata(s) julgada(s)")

    intel["disrupcao_emergente"] = resultado
    intel_path.write_text(json.dumps(intel, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultado


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Novas Curvas S / Sinais de Disrupção (Christensen).")
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    resultado = run_disruption(Path(args.input), dry_run=args.dry_run)
    if resultado:
        print(f"  · disrupcao_emergente: {[r['tecnologia'] for r in resultado]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
