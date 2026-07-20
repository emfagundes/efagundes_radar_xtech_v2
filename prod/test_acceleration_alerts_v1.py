#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_acceleration_alerts_v1.py — Casos explícitos dos critérios de aceite
(spec Seção 12) do Alerta de Aceleração Anômala:

  - funil seleciona por incidência bruta, exclui tema nulo/vazio
  - caso "AgriTech" (aceleração≈1, patamar alto) passa o funil e concorre
    ao top-K — o ajuste crítico da Seção 6
  - normalização por SHARE, não volume bruto
  - teto rígido de K julgamentos de LLM por ciclo (LLM mockado, sem custo real)
  - degradação graciosa se o LLM falhar
  - idempotência (reprocessar o ciclo não redispara o LLM nem duplica linhas)
  - janela de silêncio: tema recém-alertado só reentra com salto de escore

Uso:
  python test_acceleration_alerts_v1.py
"""

from __future__ import annotations

import json
import sqlite3
import sys

import acceleration_alerts_v1 as aa


def _nova_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE raw_items (
            id TEXT PRIMARY KEY, cycle_date TEXT, source_file TEXT, title TEXT,
            source TEXT, url TEXT, description TEXT, theme TEXT,
            collected_at TEXT, published_at TEXT, score REAL, hash TEXT,
            raw_json TEXT, created_at TEXT
        )
    """)
    aa.ensure_schema(conn)
    return conn


def _seed(conn: sqlite3.Connection, cycle_date: str, theme: str, n: int, score: float = 1.0) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO raw_items (id, cycle_date, title, description, source, theme, score) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"{theme}_{cycle_date}_{i}", cycle_date, f"Título {theme} {i}",
             f"Descrição do sinal {i} sobre {theme}.", "FonteTeste", theme, score),
        )
    conn.commit()


CICLO = "2026-07-19"  # janela total: 2026-06-22..2026-07-19
                       # recente: 2026-07-06..2026-07-19 | anterior: 2026-06-22..2026-07-05
                       # baseline: tudo antes de 2026-06-22


def _seed_cenario_agritech_vs_blockchain(conn: sqlite3.Connection) -> None:
    # baseline (antes da janela) — um único dia bem antes é suficiente
    _seed(conn, "2026-05-01", "AgriTest", 5)
    _seed(conn, "2026-05-01", "BlockchainTest", 10)
    _seed(conn, "2026-05-01", "FillerTest", 85)
    # anterior (primeira metade da janela)
    _seed(conn, "2026-06-25", "AgriTest", 30)
    _seed(conn, "2026-06-25", "BlockchainTest", 10)
    _seed(conn, "2026-06-25", "FillerTest", 60)
    # recente (segunda metade da janela, inclui o ciclo atual)
    _seed(conn, "2026-07-10", "AgriTest", 30)
    _seed(conn, "2026-07-10", "BlockchainTest", 40)
    _seed(conn, "2026-07-10", "FillerTest", 30)
    # tema nulo/vazio — deve ser descartado do funil
    conn.execute("INSERT INTO raw_items (id, cycle_date, title, theme) VALUES ('nulo1','2026-07-10','X',NULL)")
    conn.execute("INSERT INTO raw_items (id, cycle_date, title, theme) VALUES ('nulo2','2026-07-10','Y','')")
    conn.commit()


def test_funil_exclui_tema_nulo_e_vazio() -> None:
    conn = _nova_conn()
    _seed_cenario_agritech_vs_blockchain(conn)
    candidatos = aa.calcular_saliencia_temas(conn, CICLO, n=10)
    temas = {c["theme"] for c in candidatos}
    assert None not in temas and "" not in temas, f"tema nulo/vazio vazou pro funil: {temas}"
    assert temas == {"AgriTest", "BlockchainTest", "FillerTest"}
    conn.close()
    print("  ✓ test_funil_exclui_tema_nulo_e_vazio")


def test_agritech_aceleracao_baixa_patamar_alto_concorre_no_topk() -> None:
    """Critério de aceite (Seção 12): dado real de AgriTech (platô alto,
    aceleração ≈1) deve fazê-lo passar o funil e concorrer ao top-K."""
    conn = _nova_conn()
    _seed_cenario_agritech_vs_blockchain(conn)
    candidatos = aa.calcular_saliencia_temas(conn, CICLO, n=10)
    por_tema = {c["theme"]: c for c in candidatos}

    agri = por_tema["AgriTest"]
    assert abs(agri["comp_aceleracao"] - 1.0) < 0.05, f"esperado aceleração≈1, veio {agri['comp_aceleracao']}"
    assert agri["comp_patamar"] > 3.0, f"esperado patamar alto (tema em platô), veio {agri['comp_patamar']}"

    # Mesmo com aceleração baixa, o patamar alto deve manter o AgriTest
    # competitivo — bem acima do tema de enchimento (FillerTest, sem
    # aceleração nem patamar).
    filler = por_tema["FillerTest"]
    assert agri["escore_saliencia"] > filler["escore_saliencia"], (
        "patamar sustentado não pesou no escore — AgriTest deveria bater FillerTest"
    )

    selecionados = aa.selecionar_top_k(conn, CICLO, candidatos, k=2)
    temas_selecionados = {s["theme"] for s in selecionados}
    assert "AgriTest" in temas_selecionados, (
        f"AgriTest (aceleração≈1, patamar alto) não entrou no top-K: {temas_selecionados}"
    )
    conn.close()
    print("  ✓ test_agritech_aceleracao_baixa_patamar_alto_concorre_no_topk")


def test_normalizacao_por_share_nao_volume_bruto() -> None:
    """Um tema que triplica de volume bruto, mas cujo TOTAL do ciclo também
    triplicou na mesma proporção, não deve mostrar aceleração — só share
    importa, não a contagem crua (Seção 6, nota de normalização)."""
    conn = _nova_conn()
    # anterior: tema=10 de um total de 100 (share=0.10)
    _seed(conn, "2026-06-25", "ProporcionalTest", 10)
    _seed(conn, "2026-06-25", "FillerTest", 90)
    # recente: tema=30 (triplicou em bruto) mas total também triplicou pra 300 (share=0.10, igual)
    _seed(conn, "2026-07-10", "ProporcionalTest", 30)
    _seed(conn, "2026-07-10", "FillerTest", 270)

    candidatos = aa.calcular_saliencia_temas(conn, CICLO, n=10)
    prop = next(c for c in candidatos if c["theme"] == "ProporcionalTest")
    assert abs(prop["comp_aceleracao"] - 1.0) < 0.05, (
        f"volume bruto triplicou mas share ficou igual — aceleração deveria ser ≈1, veio {prop['comp_aceleracao']}"
    )
    conn.close()
    print("  ✓ test_normalizacao_por_share_nao_volume_bruto")


def test_piso_de_volume_exclui_amostra_minuscula() -> None:
    """Regressão contra dado real ao vivo: um tema com poucos itens no total
    (ex. 3) pode ter razão recente/anterior batendo no RATIO_CAP só por ter
    denominador ~0 — isso dominava o escore como se fosse um movimento real.
    Um piso de volume total tira esse ruído de amostra pequena do funil."""
    conn = _nova_conn()
    # MinusculoTest: só 3 itens no total (bem abaixo do piso) — não pode
    # concorrer, mesmo com aceleração/patamar artificialmente altos.
    _seed(conn, "2026-07-10", "MinusculoTest", 3)
    _seed(conn, "2026-06-25", "FillerTest", 20)
    _seed(conn, "2026-07-10", "FillerTest", 20)

    candidatos = aa.calcular_saliencia_temas(conn, CICLO, n=10)
    temas = {c["theme"] for c in candidatos}
    assert "MinusculoTest" not in temas, (
        f"tema com volume abaixo do piso ({aa.MIN_VOLUME_TOTAL}) entrou no funil: {temas}"
    )
    assert "FillerTest" in temas
    conn.close()
    print("  ✓ test_piso_de_volume_exclui_amostra_minuscula")


def test_teto_rigido_de_k_chamadas_llm() -> None:
    conn = _nova_conn()
    for i in range(8):
        tema = f"Tema{i}"
        _seed(conn, "2026-06-25", tema, 5 + i)
        _seed(conn, "2026-07-10", tema, 20 + i * 3)  # todos acelerando, K deve limitar

    chamadas = {"n": 0}

    def _mock_chamar(prompt: str) -> str:
        chamadas["n"] += 1
        return json.dumps({
            "enredo": "teste", "natureza_dominante": "Ruído", "natureza_secundaria": None,
            "acao_mitigacao": None, "consequencia_inacao": None,
            "momento": "Ruído Operacional", "confianca": 0.6,
        })

    original = aa._chamar_claude_julgamento
    aa._chamar_claude_julgamento = _mock_chamar
    try:
        alertas = aa.processar_alertas_ciclo(conn, CICLO, n=10, k=aa.K_ALERTAS)
    finally:
        aa._chamar_claude_julgamento = original

    assert len(alertas) <= aa.K_ALERTAS, f"K_ALERTAS={aa.K_ALERTAS} violado: {len(alertas)} alertas"
    assert chamadas["n"] <= aa.K_ALERTAS, f"mais chamadas de LLM que K permite: {chamadas['n']}"
    conn.close()
    print("  ✓ test_teto_rigido_de_k_chamadas_llm")


def test_degradacao_graciosa_quando_llm_falha() -> None:
    conn = _nova_conn()
    _seed(conn, "2026-06-25", "FalhaTest", 10)
    _seed(conn, "2026-07-10", "FalhaTest", 30)

    def _mock_falha(prompt: str) -> str:
        raise RuntimeError("timeout simulado")

    original = aa._chamar_claude_julgamento
    aa._chamar_claude_julgamento = _mock_falha
    try:
        alertas = aa.processar_alertas_ciclo(conn, CICLO, n=10, k=4)
    finally:
        aa._chamar_claude_julgamento = original

    assert len(alertas) == 1, "alerta sumiu quando o LLM falhou — degradação graciosa quebrada"
    a = alertas[0]
    assert a["natureza"] == "não avaliada"
    assert a["escore_saliencia"] is not None and a["comp_aceleracao"] is not None
    row = conn.execute(
        "SELECT natureza, escore_saliencia FROM acceleration_alerts WHERE theme='FalhaTest'"
    ).fetchone()
    assert row == ("não avaliada", a["escore_saliencia"])
    conn.close()
    print("  ✓ test_degradacao_graciosa_quando_llm_falha")


def test_idempotencia_nao_redispara_llm() -> None:
    conn = _nova_conn()
    _seed(conn, "2026-06-25", "IdempTest", 10)
    _seed(conn, "2026-07-10", "IdempTest", 30)

    chamadas = {"n": 0}

    def _mock_chamar(prompt: str) -> str:
        chamadas["n"] += 1
        return json.dumps({
            "enredo": "x", "natureza_dominante": "Oportunidade", "natureza_secundaria": None,
            "acao_mitigacao": None, "consequencia_inacao": None,
            "momento": "Capturar Vantagem", "confianca": 0.8,
        })

    original = aa._chamar_claude_julgamento
    aa._chamar_claude_julgamento = _mock_chamar
    try:
        alertas1 = aa.processar_alertas_ciclo(conn, CICLO, n=10, k=4)
        alertas2 = aa.processar_alertas_ciclo(conn, CICLO, n=10, k=4)
    finally:
        aa._chamar_claude_julgamento = original

    assert chamadas["n"] == 1, f"reprocessar o ciclo rechamou o LLM: {chamadas['n']} chamadas"
    assert len(alertas1) == len(alertas2) == 1
    total = conn.execute("SELECT COUNT(*) FROM acceleration_alerts").fetchone()[0]
    assert total == 1, f"reprocessar duplicou linhas: {total}"
    conn.close()
    print("  ✓ test_idempotencia_nao_redispara_llm")


def test_janela_de_silencio_exige_salto_de_escore() -> None:
    conn = _nova_conn()
    _seed(conn, "2026-06-25", "SilencioTest", 10)
    _seed(conn, "2026-07-10", "SilencioTest", 30)

    def _mock_chamar(prompt: str) -> str:
        return json.dumps({
            "enredo": "x", "natureza_dominante": "Risco", "natureza_secundaria": None,
            "acao_mitigacao": "mitigar", "consequencia_inacao": "consequência",
            "momento": "Mobilizar Agora", "confianca": 0.9,
        })

    original = aa._chamar_claude_julgamento
    aa._chamar_claude_julgamento = _mock_chamar
    try:
        # Ciclo 1: SilencioTest é o único candidato relevante, é selecionado e julgado.
        aa.processar_alertas_ciclo(conn, "2026-07-17", n=10, k=4)
        escore_ciclo1 = conn.execute(
            "SELECT escore_saliencia FROM acceleration_alerts WHERE theme='SilencioTest'"
        ).fetchone()[0]

        # Ciclo 2 (dentro da janela de silêncio de 3): mesmo tema, escore
        # idêntico (sem salto) — não deve ser reselecionado.
        candidatos = aa.calcular_saliencia_temas(conn, "2026-07-18", n=10)
        for c in candidatos:
            if c["theme"] == "SilencioTest":
                c["escore_saliencia"] = escore_ciclo1  # força "sem salto"
        selecionados = aa.selecionar_top_k(conn, "2026-07-18", candidatos, k=4)
        assert "SilencioTest" not in {s["theme"] for s in selecionados}, (
            "tema em janela de silêncio foi reselecionado sem salto de escore"
        )

        # Com salto > 15%, deve poder reentrar.
        for c in candidatos:
            if c["theme"] == "SilencioTest":
                c["escore_saliencia"] = escore_ciclo1 * 1.5
        selecionados_com_salto = aa.selecionar_top_k(conn, "2026-07-18", candidatos, k=4)
        assert "SilencioTest" in {s["theme"] for s in selecionados_com_salto}, (
            "tema com salto de escore > 15% deveria reentrar mesmo na janela de silêncio"
        )
    finally:
        aa._chamar_claude_julgamento = original
    conn.close()
    print("  ✓ test_janela_de_silencio_exige_salto_de_escore")


def main() -> int:
    testes = [
        test_funil_exclui_tema_nulo_e_vazio,
        test_agritech_aceleracao_baixa_patamar_alto_concorre_no_topk,
        test_normalizacao_por_share_nao_volume_bruto,
        test_piso_de_volume_exclui_amostra_minuscula,
        test_teto_rigido_de_k_chamadas_llm,
        test_degradacao_graciosa_quando_llm_falha,
        test_idempotencia_nao_redispara_llm,
        test_janela_de_silencio_exige_salto_de_escore,
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
