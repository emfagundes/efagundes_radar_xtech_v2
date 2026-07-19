#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_scenario_tracker_v1.py — Casos explícitos exigidos pela Seção 11 (critérios
de aceite) do spec do Scenario Tracker:

  - regra anti-viés: avaliação nunca usa evidência com data <= data_emissao
  - reprocessar o mesmo ciclo é idempotente (snapshots e avaliações)
  - transições de estado (em_aberto/em_formacao/confirmado/nao_materializado)

Roda contra um SQLite in-memory. Sem dependências externas.

Uso:
  python test_scenario_tracker_v1.py
"""

from __future__ import annotations

import sqlite3
import sys

import scenario_tracker_v1 as st


def _nova_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    st.ensure_schema(conn)
    return conn


CENARIO_BASE = {
    "id": "A",
    "titulo_cenario": "Jabutis Tarifários Freiam Data Centers",
    "narrativa_macro": (
        "A fragmentação regulatória do setor elétrico persiste, com jabutis "
        "tarifários gerando incerteza sobre repasses e freando investimentos "
        "em data centers e infraestrutura de IA no Brasil."
    ),
    "probabilidade": 35,
    "impacto": "Alto",
    "tipo": "Risco",
    "pos_x": 0.75,
    "pos_y": 0.25,
}
MATRIZ_BASE = {
    "eixo_x": {"nome": "Estabilidade regulatória"},
    "eixo_y": {"nome": "Velocidade de integração IA-infraestrutura"},
}


def test_anti_vies_recusa_evidencia_do_proprio_ciclo() -> None:
    conn = _nova_conn()
    ciclo_emissao = "2026-07-01"
    st.persist_snapshots(conn, [CENARIO_BASE], MATRIZ_BASE, ciclo_emissao)

    # Evidência do MESMO ciclo em que o cenário foi emitido — não pode gerar avaliação.
    vetores_mesmo_ciclo = [{"id": "VE-001", "nome": "Jabutis tarifários freiam data centers",
                             "pressao_estrategica": 8.0}]
    n = st.evaluate_open_scenarios(conn, ciclo_emissao, vetores_mesmo_ciclo, {}, [])
    assert n == 0, f"esperado 0 avaliações no próprio ciclo de emissão, veio {n}"
    total = conn.execute("SELECT COUNT(*) FROM scenario_evaluations").fetchone()[0]
    assert total == 0, "avaliação vazou para o ciclo de emissão — regra anti-viés violada"

    # Evidência de um ciclo ANTERIOR à emissão (dado hipotético/corrompido) — também recusa.
    n = st.evaluate_open_scenarios(conn, "2026-06-20", vetores_mesmo_ciclo, {}, [])
    assert n == 0, f"esperado 0 avaliações em ciclo anterior à emissão, veio {n}"

    conn.close()
    print("  ✓ test_anti_vies_recusa_evidencia_do_proprio_ciclo")


def test_anti_vies_aceita_evidencia_de_ciclo_posterior() -> None:
    conn = _nova_conn()
    ciclo_emissao = "2026-07-01"
    st.persist_snapshots(conn, [CENARIO_BASE], MATRIZ_BASE, ciclo_emissao)

    vetores_t1 = [{"id": "VE-001", "nome": "Jabutis tarifários freiam data centers",
                   "pressao_estrategica": 8.0}]
    n = st.evaluate_open_scenarios(conn, "2026-07-02", vetores_t1, {}, [])
    assert n == 1, f"esperado 1 avaliação em T+1, veio {n}"

    row = conn.execute(
        "SELECT data_avaliacao, status FROM scenario_evaluations WHERE scenario_uid=?",
        (st.scenario_uid(ciclo_emissao, "A"),),
    ).fetchone()
    assert row is not None and row[0] == "2026-07-02"

    conn.close()
    print("  ✓ test_anti_vies_aceita_evidencia_de_ciclo_posterior")


def test_idempotencia_snapshot_e_avaliacao() -> None:
    conn = _nova_conn()
    ciclo_emissao = "2026-07-01"

    n1 = st.persist_snapshots(conn, [CENARIO_BASE], MATRIZ_BASE, ciclo_emissao)
    n2 = st.persist_snapshots(conn, [CENARIO_BASE], MATRIZ_BASE, ciclo_emissao)
    assert n1 == 1 and n2 == 0, f"reprocessar o ciclo duplicou snapshot: n1={n1} n2={n2}"
    total_snaps = conn.execute("SELECT COUNT(*) FROM scenario_snapshots").fetchone()[0]
    assert total_snaps == 1

    vetores_t1 = [{"id": "VE-001", "nome": "jabutis tarifários", "pressao_estrategica": 5.0}]
    e1 = st.evaluate_open_scenarios(conn, "2026-07-02", vetores_t1, {}, [])
    e2 = st.evaluate_open_scenarios(conn, "2026-07-02", vetores_t1, {}, [])
    assert e1 == 1 and e2 == 0, f"reprocessar o ciclo duplicou avaliação: e1={e1} e2={e2}"
    total_evals = conn.execute("SELECT COUNT(*) FROM scenario_evaluations").fetchone()[0]
    assert total_evals == 1

    conn.close()
    print("  ✓ test_idempotencia_snapshot_e_avaliacao")


def test_maquina_de_estados() -> None:
    assert st.determinar_status(0.0, 5, 30) == "em_aberto"
    assert st.determinar_status(0.33, 5, 30) == "em_formacao"
    assert st.determinar_status(0.66, 5, 30) == "confirmado"
    assert st.determinar_status(1.0, 5, 30) == "confirmado"
    assert st.determinar_status(0.33, 31, 30) == "nao_materializado"
    # fracao >= limiar vence mesmo com horizonte estourado no mesmo ciclo
    assert st.determinar_status(0.66, 31, 30) == "confirmado"
    print("  ✓ test_maquina_de_estados")


def test_confirmado_congela_e_nao_e_reavaliado() -> None:
    conn = _nova_conn()
    ciclo_emissao = "2026-07-01"
    st.persist_snapshots(conn, [CENARIO_BASE], MATRIZ_BASE, ciclo_emissao)

    # T+1: 3/3 gatilhos batem (vetor top-3, hero e fato canônico) -> confirmado.
    vetores = [{"id": "VE-001", "nome": "Jabutis tarifários freiam investimentos",
                "pressao_estrategica": 7.0}]
    hero = {"manchete": "Fragmentação regulatória atrasa data centers"}
    fatos = [{"contexto": "Repasse tarifário incerto para grandes cargas",
               "valor_literal": "jabutis", "sinal_id": 0, "tipo": "quantidade"}]
    n = st.evaluate_open_scenarios(conn, "2026-07-02", vetores, hero, fatos)
    assert n == 1
    status = conn.execute(
        "SELECT status FROM scenario_evaluations WHERE data_avaliacao='2026-07-02'"
    ).fetchone()[0]
    assert status == "confirmado", f"esperado confirmado, veio {status}"

    # T+2: mesmo sem nenhuma evidência nova, o cenário confirmado não é reavaliado.
    n2 = st.evaluate_open_scenarios(conn, "2026-07-03", [], {}, [])
    assert n2 == 0, "cenário confirmado (terminal) foi reavaliado — deveria estar congelado"

    conn.close()
    print("  ✓ test_confirmado_congela_e_nao_e_reavaliado")


def test_resumo_seção_8() -> None:
    conn = _nova_conn()
    st.persist_snapshots(conn, [CENARIO_BASE], MATRIZ_BASE, "2026-07-01")
    vetores = [{"id": "VE-001", "nome": "Jabutis tarifários freiam investimentos",
                "pressao_estrategica": 7.0}]
    hero = {"manchete": "Fragmentação regulatória atrasa data centers"}
    fatos = [{"contexto": "Repasse tarifário incerto", "valor_literal": "jabutis"}]
    st.evaluate_open_scenarios(conn, "2026-07-02", vetores, hero, fatos)

    resumo = st.build_scenario_tracking_summary(conn)
    assert resumo["total_emitidos"] == 1
    assert resumo["por_estado"]["confirmado"] == 1
    assert resumo["dias_medios_antecedencia_confirmados"] == 1
    assert "preparação estruturada" in resumo["enquadramento"]
    conn.close()
    print("  ✓ test_resumo_seção_8")


def main() -> int:
    testes = [
        test_anti_vies_recusa_evidencia_do_proprio_ciclo,
        test_anti_vies_aceita_evidencia_de_ciclo_posterior,
        test_idempotencia_snapshot_e_avaliacao,
        test_maquina_de_estados,
        test_confirmado_congela_e_nao_e_reavaliado,
        test_resumo_seção_8,
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
