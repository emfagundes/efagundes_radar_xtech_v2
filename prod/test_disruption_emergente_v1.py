#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_disruption_emergente_v1.py — seleção de candidatas a disrupção,
degradação graciosa e teto de K.

Uso:
  python test_disruption_emergente_v1.py
"""

from __future__ import annotations

import json
import sys

import disruption_emergente_v1 as de


def _tech(id_, phase, trend, score, n, signal="contexto de teste"):
    return {"id": id_, "frente": "EnergyTech", "phase": phase, "trend": trend,
            "score": score, "n": n, "signal": signal, "color": "#2FA87C"}


def test_exige_fase_inicial_e_aceleracao() -> None:
    hcl = [
        _tech("MaduraAcelerando", "Infraestrutura Crítica", "↗", 6.0, 50),   # fase madura — fora
        _tech("EmergenteEstavel", "Sinal Emergente", "→", 8.0, 10),          # sem aceleração — fora
        _tech("EmergenteAcelerando", "Sinal Emergente", "↗", 5.0, 5),        # candidata válida
        _tech("NarrativaAcelerando", "Narrativa Exponencial", "↗", 7.0, 3),  # candidata válida
    ]
    candidatos = de.selecionar_candidatos_disrupcao(hcl)
    ids = {c["id"] for c in candidatos}
    assert ids == {"EmergenteAcelerando", "NarrativaAcelerando"}, ids
    print("  ✓ test_exige_fase_inicial_e_aceleracao")


def test_piso_baixo_mas_exclui_n1() -> None:
    hcl = [
        _tech("UmSinalSo", "Sinal Emergente", "↗", 9.0, 1),   # n=1, ruído — fora
        _tech("DoisSinais", "Sinal Emergente", "↗", 5.0, 2),  # n=2, no piso — entra
    ]
    candidatos = de.selecionar_candidatos_disrupcao(hcl)
    ids = {c["id"] for c in candidatos}
    assert ids == {"DoisSinais"}, ids
    print("  ✓ test_piso_baixo_mas_exclui_n1")


def test_teto_de_candidatas_e_ordenacao_por_score() -> None:
    hcl = [_tech(f"Tech{i}", "Sinal Emergente", "↗", float(i), 3) for i in range(8)]
    candidatos = de.selecionar_candidatos_disrupcao(hcl, max_k=4)
    assert len(candidatos) == 4
    scores = [c["score"] for c in candidatos]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 7.0  # Tech7 tem o maior score
    print("  ✓ test_teto_de_candidatas_e_ordenacao_por_score")


def test_degradacao_graciosa_quando_llm_falha() -> None:
    hcl = [_tech("FalhaTest", "Sinal Emergente", "↗", 5.0, 3, signal="<strong>Sinal de teste</strong> específico")]

    def _mock_falha(prompt: str) -> str:
        raise RuntimeError("timeout simulado")

    original = de._chamar_claude_julgamento
    de._chamar_claude_julgamento = _mock_falha
    try:
        resultado = de.build_disrupcao_emergente({"hype_cycle_live": hcl})
    finally:
        de._chamar_claude_julgamento = original

    assert len(resultado) == 1, "candidata sumiu quando o LLM falhou — degradação graciosa quebrada"
    r = resultado[0]
    assert r["tecnologia"] == "FalhaTest"
    assert r["tese_disrupcao_curta"], "deveria ter síntese estatística no lugar da tese"
    assert "<strong>" not in r["tese_disrupcao_curta"], "tags HTML deveriam ter sido removidas do fallback"
    print("  ✓ test_degradacao_graciosa_quando_llm_falha")


def test_julgamento_real_mockado() -> None:
    hcl = [_tech("JulgadaTest", "Narrativa Exponencial", "↗", 8.0, 4)]

    def _mock_chamar(prompt: str) -> str:
        return json.dumps({
            "tese_disrupcao_curta": "Tese de teste específica sobre esta tecnologia.",
            "o_que_observar": "Observar X nos próximos ciclos.",
            "tecnologia_dominante_interceptada": "Tecnologia Madura Teste",
        })

    original = de._chamar_claude_julgamento
    de._chamar_claude_julgamento = _mock_chamar
    try:
        resultado = de.build_disrupcao_emergente({"hype_cycle_live": hcl})
    finally:
        de._chamar_claude_julgamento = original

    assert len(resultado) == 1
    r = resultado[0]
    assert r["tese_disrupcao_curta"] == "Tese de teste específica sobre esta tecnologia."
    assert r["tecnologia_dominante_interceptada"] == "Tecnologia Madura Teste"
    print("  ✓ test_julgamento_real_mockado")


def main() -> int:
    testes = [
        test_exige_fase_inicial_e_aceleracao,
        test_piso_baixo_mas_exclui_n1,
        test_teto_de_candidatas_e_ordenacao_por_score,
        test_degradacao_graciosa_quando_llm_falha,
        test_julgamento_real_mockado,
    ]
    falhas = 0
    for teste in testes:
        try:
            teste()
        except AssertionError as e:
            falhas += 1
            print(f"  ✗ {teste.__name__} — FALHOU: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
