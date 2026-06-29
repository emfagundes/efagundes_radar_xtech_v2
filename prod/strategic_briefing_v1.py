#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategic_briefing_v1.py — Gerador de Briefing Estratégico e Pergunta do Mentor

Lê intel_output.json + market_signals.json e usa Claude (claude-opus-4-8 + adaptive
thinking) para gerar:

  pergunta_estrategica   — pergunta executiva que o mentor deve fazer ao mentorado
  contexto_pergunta      — por que essa pergunta importa esta semana (1–2 frases)
  correlacao_mercado     — narrativa que cruza sinal textual + dados quantitativos
  insight_executivo      — manchete de uma linha para o hero da efagundes.com
  sinais_atencao         — lista de 3 sinais com título e contextualização curta

Output:
  Injeta intel_output.json["strategic_briefing"] (in-place)
  Salva strategic_briefing_{ciclo}.json (audit separado)

Uso:
  python strategic_briefing_v1.py
  python strategic_briefing_v1.py --input intel_output.json --market market_signals.json
  python strategic_briefing_v1.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BRASILIA = timezone(timedelta(hours=-3))
MODEL    = "claude-opus-4-8"

ROOT_DIR       = Path(__file__).resolve().parent
DEFAULT_INTEL  = str(ROOT_DIR / "intel_output.json")
DEFAULT_MARKET = str(ROOT_DIR / "market_signals.json")


# ─── Extração de contexto ─────────────────────────────────────────────────────

def _top_vetor(intel: Dict[str, Any]) -> Dict[str, Any]:
    vetores = intel.get("vetores_estrategicos") or []
    quad_rank = {"Mobilizar Agora": 4, "Capturar Vantagem": 3,
                 "Monitorar Vetores": 2, "Ruído Operacional": 1}
    return sorted(
        vetores,
        key=lambda v: (
            quad_rank.get(v.get("quadrante_executivo", ""), 0),
            float(v.get("pressao_estrategica") or 0),
        ),
        reverse=True,
    )[0] if vetores else {}


def _top_sinais(intel: Dict[str, Any], n: int = 5) -> List[Dict[str, Any]]:
    itens = intel.get("itens") or []
    return sorted(itens, key=lambda x: float(x.get("score_final") or 0), reverse=True)[:n]


def _market_context(market: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai os indicadores mais relevantes do market_signals.json."""
    sinais = market.get("sinais") or []
    idx = {s["indicador"]: s for s in sinais if s.get("valor") is not None}

    chaves = [
        "USD/BRL", "Selic", "Selic Real", "IPCA", "Brent",
        "Cobre", "Ibovespa", "IEE proxy (CPFE3)", "UTIL (Utilidades)",
        "Pressão Cambial CAPEX (30d)", "IEE vs Ibovespa (30d)", "Brent Tendência 30d",
    ]
    resultado = {}
    for k in chaves:
        if k in idx:
            s = idx[k]
            resultado[k] = {
                "valor": s.get("valor"),
                "variacao_30d_pct": s.get("variacao_30d_pct"),
                "variacao_7d_pct": s.get("variacao_7d_pct"),
                "relevancia": s.get("relevancia", ""),
                "unidade": s.get("unidade", ""),
            }
    return resultado


def _montar_prompt(intel: Dict[str, Any], market: Dict[str, Any]) -> str:
    vetor  = _top_vetor(intel)
    sinais = _top_sinais(intel, 5)
    mkt    = _market_context(market)
    ciclo  = intel.get("ciclo_id") or (intel.get("dashboard") or {}).get("ciclo") or "?"
    total  = intel.get("total_itens") or len(intel.get("itens") or [])

    vetor_txt = "\n".join([
        f"  xTech: {vetor.get('xtech') or vetor.get('frente') or 'não identificado'}",
        f"  Quadrante: {vetor.get('quadrante_executivo', '')}",
        f"  Pressão estratégica: {vetor.get('pressao_estrategica', '')} / 10",
        f"  Janela decisória: {vetor.get('janela_decisoria_dias', '')} dias",
        f"  Tese central: {(vetor.get('tese_central') or vetor.get('resumo') or '')[:300]}",
    ])

    sinais_txt = "\n".join(
        f"  [{s.get('score_final', '')}] {s.get('titulo') or s.get('title', '')} "
        f"({s.get('tipo_sinal', '')})"
        for s in sinais
    )

    mkt_txt = "\n".join(
        f"  {k}: valor={v['valor']} {v['unidade']} | 30d={v.get('variacao_30d_pct')}% | {v['relevancia']}"
        for k, v in mkt.items()
    )

    return f"""Você é o analista-chefe do Radar xTechs — Efagundes Think Tank.
Esta semana você analisou {total} sinais de inteligência tecnológica e cruzou com dados de mercado.

CICLO: {ciclo}

=== VETOR ESTRATÉGICO DE MAIOR MOMENTUM ===
{vetor_txt}

=== TOP 5 SINAIS TEXTUAIS (por score) ===
{sinais_txt}

=== SINAIS QUANTITATIVOS DE MERCADO ===
{mkt_txt}

Sua tarefa é gerar um briefing estratégico completo que será usado em dois canais:
1. efagundes.com — plataforma think tank para líderes executivos
2. nMentors.com.br — plataforma de mentoria estratégica executiva

O briefing deve CRUZAR os sinais textuais com os dados de mercado para produzir
inteligência que não existe em nenhum relatório pago — a correlação entre o que
está acontecendo no mundo real (regulação, investimentos, movimentos de empresas)
e o que os números do mercado estão sinalizando.

REGRA CRÍTICA — CAUSALIDADE CORRETA:
Antes de afirmar que um indicador de mercado "torna X mais competitivo" ou "favorece Y",
verifique a DIREÇÃO CAUSAL com rigor. Exemplos de erros comuns a evitar:

- Brent caindo NÃO torna BESS mais competitivo frente a termelétricas.
  O correto: Brent caindo barateia o combustível das termelétricas, reduz o PLD,
  e PRESSIONA a receita de projetos BESS no mercado spot. BESS fica mais difícil
  de fechar sem PPA de longo prazo.

- Selic alta NÃO favorece projetos de capital intensivo. O correto: eleva o custo
  de capital e o retorno mínimo exigido, dificultando o fechamento financeiro.

- USD/BRL alto AUMENTA o custo de equipamentos importados (turbinas, módulos, baterias)
  e pode inviabilizar projetos dependentes de CAPEX em dólar.

Ao cruzar mercado × vetor, sempre responda: "Se este indicador subiu/caiu, o efeito
DIRETO sobre o vetor é X — não o efeito que seria conveniente afirmar."

Responda em JSON com exatamente esta estrutura:

{{
  "insight_executivo": "<uma frase impactante que resume O QUE está acontecendo E POR QUÊ importa agora — máx 180 caracteres>",

  "correlacao_mercado": "<2–3 frases que conectam o vetor principal com os dados quantitativos de mercado, com causalidade CORRETA e verificada. Use os números reais. Não inverta a direção dos efeitos.>",

  "pergunta_estrategica": "<pergunta executiva de alto impacto que um mentor sênior (nível McKinsey/BCG) deve fazer ao seu mentorado nesta semana. Deve ser específica, não retórica, e abrir uma decisão real. Use dados concretos do Radar. Máx 200 caracteres.>",

  "contexto_pergunta": "<1–2 frases explicando de onde vem a urgência desta pergunta — o dado de mercado + o sinal textual que a motivam. Formato: 'A pergunta surge da confluência entre X e Y.' Máx 150 caracteres.>",

  "sinais_atencao": [
    {{
      "titulo": "<título do sinal — máx 80 chars>",
      "contexto": "<1 frase explicando a implicação estratégica — o que isso muda para executivos e mentores. Máx 140 chars.>",
      "tipo": "oportunidade" | "risco" | "ruptura"
    }}
  ]
}}

Os "sinais_atencao" devem ser exatamente 3 itens, escolhidos pela maior relevância
executiva — não necessariamente os de maior score numérico.

Seja direto, específico e sem hedging. Este conteúdo compete com relatórios de
consultorias globais. Use os números reais dos dados fornecidos."""


# ─── Chamada Anthropic ────────────────────────────────────────────────────────

def _call_claude(prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic não instalado. pip install anthropic")

    import os
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não configurada no .env")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def _parse_json(text: str) -> Dict[str, Any]:
    import re
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.rstrip().endswith("```"):
            text = "\n".join(text.rstrip().split("\n")[:-1])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# ─── Pipeline principal ───────────────────────────────────────────────────────

def gerar_strategic_briefing(
    intel_path: Path,
    market_path: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:

    intel  = json.loads(intel_path.read_text(encoding="utf-8"))
    market = json.loads(market_path.read_text(encoding="utf-8")) if market_path.exists() else {}

    ciclo = (intel.get("ciclo_id") or
             (intel.get("dashboard") or {}).get("ciclo") or
             datetime.now(BRASILIA).strftime("%Y-%m-%d"))

    print(f"  · Ciclo: {ciclo}")
    print(f"  · Sinais: {intel.get('total_itens', '?')} | "
          f"Market signals: {market.get('total_sinais', 0)}")

    prompt = _montar_prompt(intel, market)

    if dry_run:
        print("  · [dry-run] Claude não chamado")
        briefing = {
            "insight_executivo": "[dry-run] Insight executivo aqui",
            "correlacao_mercado": "[dry-run] Correlação mercado aqui",
            "pergunta_estrategica": "[dry-run] Pergunta estratégica aqui?",
            "contexto_pergunta": "[dry-run] Contexto da pergunta aqui",
            "sinais_atencao": [
                {"titulo": "Sinal 1", "contexto": "Contexto 1", "tipo": "oportunidade"},
                {"titulo": "Sinal 2", "contexto": "Contexto 2", "tipo": "risco"},
                {"titulo": "Sinal 3", "contexto": "Contexto 3", "tipo": "ruptura"},
            ],
        }
    else:
        t0 = time.time()
        print(f"  · [{MODEL}] Gerando briefing estratégico...")
        raw = _call_claude(prompt)
        briefing = _parse_json(raw)
        print(f"  ✓ Briefing gerado em {time.time() - t0:.1f}s")

    briefing["ciclo"]        = ciclo
    briefing["gerado_em"]    = datetime.now(BRASILIA).isoformat()
    briefing["modelo"]       = MODEL
    briefing["market_data"]  = _market_context(market)

    if not dry_run:
        # Injeta no intel_output.json
        intel["strategic_briefing"] = briefing
        intel_path.write_text(
            json.dumps(intel, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("  ✓ intel_output.json atualizado com strategic_briefing")

        # Salva audit separado
        audit_path = intel_path.parent / f"strategic_briefing_{ciclo}.json"
        audit_path.write_text(
            json.dumps(briefing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  ✓ Audit: {audit_path.name}")

    return briefing


def main() -> int:
    parser = argparse.ArgumentParser(description="Strategic Briefing v1 — Pergunta do Mentor + Hero")
    parser.add_argument("--input",  default=DEFAULT_INTEL,  help="intel_output.json")
    parser.add_argument("--market", default=DEFAULT_MARKET, help="market_signals.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"\n{'=' * 66}")
    print("  STRATEGIC BRIEFING v1")
    print(f"  Modelo: {MODEL} | adaptive thinking")
    print(f"  {'=' * 62}")

    briefing = gerar_strategic_briefing(
        intel_path=Path(args.input),
        market_path=Path(args.market),
        dry_run=args.dry_run,
    )

    print(f"\n  Insight executivo:")
    print(f"  → {briefing.get('insight_executivo', '')}")
    print(f"\n  Pergunta estratégica:")
    print(f"  → {briefing.get('pergunta_estrategica', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
