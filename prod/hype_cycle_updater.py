#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hype_cycle_updater.py — Atualiza Hype Cycle xTechs dinamicamente a cada ciclo.

Lê intel_output.json (precisa de hist_data populado pela Fase 6.5.11 do analyzer),
compara scores e contagem de sinais com os defaults estáticos de HYPE_TECHS,
regenera phase/trend/signal via Sonnet para tecnologias com variação significativa
e salva hype_cycle_live em intel_output.json para uso pelo gerar_radar_xtechs_v10.py.

Chamado pelo run_pipeline_v1.py como etapa 5.5, após memória e antes do Radar.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=Path(__file__).resolve().parent / ".env",
    override=False,
)

ROOT_DIR     = Path(__file__).resolve().parent
OUTPUT_FILE  = ROOT_DIR / "intel_output.json"
SONNET_MODEL = "claude-sonnet-4-6"

# Limiar de variação para disparar regeneração via LLM
SCORE_DELTA_THRESHOLD = 0.5   # unidades de score
N_DELTA_PCT_THRESHOLD = 0.20  # 20% de variação em contagem de sinais

# Fases da curva de maturidade e seus cx midpoints
PHASE_CX: dict[str, float] = {
    "Sinal Emergente":        0.07,
    "Narrativa Exponencial":  0.22,
    "Pico de Especulação":    0.38,
    "Fricção Operacional":    0.54,
    "Escala Econômica":       0.70,
    "Infraestrutura Crítica": 0.85,
    "Commoditização":         0.95,
}
VALID_PHASES  = list(PHASE_CX.keys())
VALID_TRENDS  = ("↗", "→", "↘")

_SYSTEM_PROMPT = (
    "Você é analista sênior do Radar xTech (think tank efagundes.com). "
    "Recebe tecnologias com scores e contagens de sinais do banco de inteligência. "
    "Para cada tecnologia:\n"
    "  1. Determine a fase atual da curva de maturidade com base no score e tendência de score.\n"
    "  2. Determine a tendência (↗ subindo >0.3, → estável ±0.3, ↘ descendo >0.3).\n"
    "  3. Escreva um parágrafo signal em HTML (80-120 palavras) em português brasileiro. "
    "Abra com '<strong>Fase: [nome da fase] ([tendência]).</strong>' e explique "
    "o estado atual da tecnologia no Brasil com dados concretos.\n"
    "REGRA DE PRECISÃO TEMPORAL OBRIGATÓRIA: separe sempre entre "
    "(a) fato já ocorrido — afirmação direta com data; "
    "(b) evento regulamentado mas ainda não executado — use 'previsto para [data]', 'agendado para', 'portaria publicada, certame previsto para'; "
    "(c) expectativa ou projeção — use 'pode', 'tende a', 'abre caminho para'. "
    "Nunca use presente simples para descrever evento futuro. "
    "Exemplos proibidos: 'redefine a arquitetura', 'leilão foi realizado' (se ainda previsto), 'passa a estabilizar a rede' (se ainda em agenda). "
    "Exemplos corretos: 'leilão previsto para dezembro de 2026', 'abre caminho para nova arquitetura de flexibilidade'.\n"
    "Responda APENAS com JSON válido, sem markdown."
)


def _extrair_json_lista(texto: str) -> str:
    texto = texto.strip()
    texto = re.sub(r"^```json\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"^```\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    m = re.search(r"(\[.*\])", texto, re.DOTALL)
    return m.group(1).strip() if m else texto.strip()


def _reparar_json(texto: str) -> str:
    texto = re.sub(r",\s*([\}\]])", r"\1", texto)
    return texto.strip()


def _chamar_sonnet(client: anthropic.Anthropic, user: str) -> str:
    resp = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=4000,
        temperature=0.2,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _carregar_hype_techs() -> list[dict]:
    """Importa HYPE_TECHS do módulo gerador do Radar."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gerar_radar_xtechs_v11",
        str(ROOT_DIR / "gerar_radar_xtechs_v11.py"),
    )
    if spec is None or spec.loader is None:
        raise ImportError("Não foi possível carregar gerar_radar_xtechs_v11.py")
    mod = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv[:]
    try:
        sys.argv = ["gerar_radar_xtechs_v11.py"]
        spec.loader.exec_module(mod)
    finally:
        sys.argv = saved_argv
    return mod.HYPE_TECHS


def _determinar_trend(score_live: float, score_default: float) -> str:
    delta = score_live - score_default
    if delta > 0.3:
        return "↗"
    if delta < -0.3:
        return "↘"
    return "→"


def atualizar_hype_cycle(
    client: anthropic.Anthropic,
    intel_output: dict[str, Any],
    hype_techs: list[dict],
) -> list[dict]:
    """
    Compara dados vivos do banco com defaults estáticos.
    Regenera phase/trend/signal via Sonnet para tecnologias com variação.
    Retorna hype_cycle_live no formato esperado por render_curva_convergencia().
    """
    hist = intel_output.get("hist_data") or {}
    tech_signals: dict[str, int]   = hist.get("tech_signals") or {}
    tech_scores:  dict[str, float] = hist.get("tech_scores")  or {}
    ciclo_id = intel_output.get("ciclo_id") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    changed: list[dict] = []
    stable:  list[dict] = []

    for i, t in enumerate(hype_techs):
        tid        = t["id"]
        n_live     = tech_signals.get(tid, t["default_n"])
        score_live = round(float(tech_scores.get(tid, t["score_default"])), 2)
        n_default  = max(t["default_n"], 1)

        score_delta  = abs(score_live - t["score_default"])
        n_delta_pct  = abs(n_live - n_default) / n_default

        entry = {
            **t,
            "n_live":     n_live,
            "score_live": score_live,
            "idx":        i,
        }

        if score_delta >= SCORE_DELTA_THRESHOLD or n_delta_pct >= N_DELTA_PCT_THRESHOLD:
            changed.append(entry)
        else:
            stable.append(entry)

    print(f"  [HypeCycle] {len(changed)} tecnologias com variação, {len(stable)} estáveis.")

    result_map: dict[str, dict] = {}

    # Tecnologias estáveis: mantém narrativa hardcoded, atualiza n/score
    for t in stable:
        result_map[t["id"]] = {
            "id":      t["id"],
            "frente":  t["frente"],
            "color":   t["color"],
            "cx":      t["cx"],
            "n":       t["n_live"],
            "score":   t["score_live"],
            "trend":   t["trend"],
            "phase":   t.get("phase", ""),
            "signal":  t["signal"],
            "updated": False,
            "above":   t["idx"] % 2 == 0,
        }

    # Tecnologias com variação: regenera via Sonnet (1 chamada batch)
    if changed:
        ctx = [
            {
                "id":               t["id"],
                "frente":           t["frente"],
                "score_anterior":   t["score_default"],
                "score_atual":      t["score_live"],
                "delta_score":      round(t["score_live"] - t["score_default"], 2),
                "n_sinais_anterior": t["default_n"],
                "n_sinais_atual":   t["n_live"],
                "phase_anterior":   t.get("phase", ""),
                "trend_anterior":   t["trend"],
            }
            for t in changed
        ]

        user_prompt = f"""Ciclo: {ciclo_id}
Fases válidas: {VALID_PHASES}
Tendências válidas: {list(VALID_TRENDS)}

Tecnologias com variação significativa neste ciclo:
{json.dumps(ctx, ensure_ascii=False, indent=2)}

Para cada tecnologia, retorne:
- id: mesmo id recebido (não altere)
- phase: uma das fases da lista acima
- trend: "↗", "→" ou "↘"
- signal: parágrafo HTML 80-120 palavras iniciando com "<strong>Fase: [fase] ([trend]).</strong>"

Retorne EXATAMENTE este JSON (array, sem chave externa):
[
  {{
    "id": "...",
    "phase": "...",
    "trend": "...",
    "signal": "..."
  }}
]"""

        try:
            t_start = time.time()
            raw = _chamar_sonnet(client, user_prompt)
            updates: list[dict] = json.loads(_reparar_json(_extrair_json_lista(raw)))
            update_map = {u["id"]: u for u in updates}
            print(
                f"  [HypeCycle] Sonnet: {len(updates)} narrativas em "
                f"{time.time() - t_start:.1f}s"
            )

            for t in changed:
                upd   = update_map.get(t["id"]) or {}
                phase = upd.get("phase") or t.get("phase", "")
                trend = upd.get("trend") if upd.get("trend") in VALID_TRENDS else _determinar_trend(t["score_live"], t["score_default"])
                signal = upd.get("signal") or t["signal"]
                # Reposiciona cx se a fase mudou
                cx = PHASE_CX.get(phase, t["cx"]) if phase != t.get("phase") else t["cx"]

                result_map[t["id"]] = {
                    "id":      t["id"],
                    "frente":  t["frente"],
                    "color":   t["color"],
                    "cx":      cx,
                    "n":       t["n_live"],
                    "score":   t["score_live"],
                    "trend":   trend,
                    "phase":   phase,
                    "signal":  signal,
                    "updated": True,
                    "above":   t["idx"] % 2 == 0,
                }

        except Exception as exc:
            print(f"  [!] HypeCycle Sonnet falhou: {exc}. Mantendo dados estáticos.")
            for t in changed:
                result_map[t["id"]] = {
                    "id":      t["id"],
                    "frente":  t["frente"],
                    "color":   t["color"],
                    "cx":      t["cx"],
                    "n":       t["n_live"],
                    "score":   t["score_live"],
                    "trend":   _determinar_trend(t["score_live"], t["score_default"]),
                    "phase":   t.get("phase", ""),
                    "signal":  t["signal"],
                    "updated": False,
                    "above":   t["idx"] % 2 == 0,
                }

    # Reordena na mesma ordem de HYPE_TECHS
    ordered = [result_map[t["id"]] for t in hype_techs if t["id"] in result_map]
    n_updated = sum(1 for x in ordered if x["updated"])
    print(
        f"  [HypeCycle] hype_cycle_live: {len(ordered)} tecnologias "
        f"({n_updated} com narrativa regenerada, {len(ordered) - n_updated} estáticas)"
    )
    return ordered


def main() -> None:
    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(f"intel_output.json não encontrado: {OUTPUT_FILE}")

    print("\n" + "=" * 66)
    print("  HYPE CYCLE UPDATER — Radar xTech")
    print(f"  {datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 66)

    intel_output = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))

    hist = intel_output.get("hist_data") or {}
    has_live_signals = bool(hist.get("tech_signals"))
    has_live_scores  = bool(hist.get("tech_scores"))

    if not has_live_signals or not has_live_scores:
        print(
            "  ⚠ hist_data sem tech_signals/tech_scores — "
            "banco provavelmente não alimentou Fase 6.5.11. "
            "hype_cycle_live não será gerado."
        )
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não encontrada no .env")

    client     = anthropic.Anthropic(api_key=api_key)
    hype_techs = _carregar_hype_techs()

    t0 = time.time()
    hype_cycle_live = atualizar_hype_cycle(client, intel_output, hype_techs)

    # Salva em hist_data para que gerar_radar_xtechs_v10.py acesse via hist_data
    intel_output.setdefault("hist_data", {})["hype_cycle_live"] = hype_cycle_live
    intel_output["hype_cycle_updater_ran_at"] = datetime.now(timezone.utc).isoformat()

    OUTPUT_FILE.write_text(
        json.dumps(intel_output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    n_updated = sum(1 for x in hype_cycle_live if x["updated"])
    print(f"\n  ✓ intel_output.json atualizado em {time.time() - t0:.1f}s")
    print(f"  ✓ {n_updated} narrativas regeneradas via Sonnet | {len(hype_cycle_live) - n_updated} estáticas")
    print("=" * 66 + "\n")


if __name__ == "__main__":
    main()
