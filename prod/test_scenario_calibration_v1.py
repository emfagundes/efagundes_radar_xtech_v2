#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_scenario_calibration_v1.py — Casos explícitos exigidos pela Seção 10
(critérios de aceite) do spec de Auto-Calibração:

  - ECE/separação calculados corretamente sobre amostra sintética conhecida
  - shrinkage reduz o ajuste em bins pequenos (regularização em direção à
    probabilidade originalmente atribuída)
  - regra anti-viés preservada: a função aplicada aos cenários de um ciclo é
    sempre derivada de um ciclo ANTERIOR, nunca do próprio ciclo
  - probabilidade_bruta é preservada mesmo quando a calibrada é ajustada

Roda contra um SQLite in-memory. Sem dependências externas.

Uso:
  python test_scenario_calibration_v1.py
"""

from __future__ import annotations

import sqlite3
import sys

import scenario_tracker_v1 as st
import scenario_calibration_v1 as calibration


def _nova_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    st.ensure_schema(conn)
    return conn


def _cenario(cid: str, prob: int, titulo: str = "Cenário teste") -> dict:
    return {
        "id": cid, "titulo_cenario": titulo,
        "narrativa_macro": "Mecanismo causal genérico de teste para calibração.",
        "probabilidade": prob, "impacto": "Alto", "tipo": "Misto",
        "pos_x": 0.5, "pos_y": 0.5,
    }


MATRIZ = {"eixo_x": {"nome": "X"}, "eixo_y": {"nome": "Y"}}


def test_ece_e_separacao_amostra_sintetica() -> None:
    conn = _nova_conn()
    # 4 cenários no bin 30-35 (atribuído médio 32.5): 2 confirmam, 2 não.
    # Observado = 50%. Insere diretamente em scenario_evaluations (status
    # controlado) em vez de depender do matching por keyword — essa peça já
    # é testada à parte em test_scenario_tracker_v1.py; aqui o alvo é só a
    # aritmética de ECE/separação.
    cenarios = [_cenario("A", 32), _cenario("B", 33), _cenario("C", 31), _cenario("D", 34)]
    st.persist_snapshots(conn, cenarios, MATRIZ, "2026-01-01")

    for cid, status in (("A", "confirmado"), ("B", "confirmado"),
                        ("C", "em_aberto"), ("D", "nao_materializado")):
        conn.execute(
            "INSERT INTO scenario_evaluations "
            "(scenario_uid, data_avaliacao, gatilhos_acionados, fracao_confirmada, evidencia, status) "
            "VALUES (?,?,?,?,?,?)",
            (f"2026-01-01_{cid}", "2026-01-02", "[]", 0.0, "{}", status),
        )
    conn.commit()

    curva = calibration.medir_curva_calibracao(conn)
    bin_alvo = next(b for b in curva if b["bin"] == "30-35")
    assert bin_alvo["n"] == 4, f"esperado 4 cenários no bin 30-35, veio {bin_alvo['n']}"
    assert bin_alvo["observado_pct"] == 50.0, f"esperado 50.0% observado, veio {bin_alvo['observado_pct']}"

    ece = calibration.calcular_ece(curva)
    assert ece is not None
    # erro do único bin com dado = |32.5 - 50| / 100 = 0.175 (atribuido_medio
    # dos 4 é (32+33+31+34)/4 = 32.5)
    assert abs(ece - 0.175) < 0.001, f"ECE inesperado: {ece}"

    conn.close()
    print("  ✓ test_ece_e_separacao_amostra_sintetica")


def test_shrinkage_reduz_ajuste_em_bin_pequeno() -> None:
    curva_bin_pequeno = [{"bin": "<25", "n": 1, "atribuido_medio": 20.0, "observado_pct": 100.0}]
    curva_bin_grande  = [{"bin": "<25", "n": 90, "atribuido_medio": 20.0, "observado_pct": 100.0}]

    funcao_pequeno = calibration.derivar_funcao_recalibracao(curva_bin_pequeno)
    funcao_grande  = calibration.derivar_funcao_recalibracao(curva_bin_grande)

    calibrado_pequeno = funcao_pequeno["bins"][0]["calibrado"]
    calibrado_grande  = funcao_grande["bins"][0]["calibrado"]

    # Mesmo atribuído/observado, mas bin pequeno (n=1) deve ficar muito mais
    # perto do valor originalmente atribuído (20) que o bin grande (n=90),
    # que deve ficar muito mais perto do valor observado (100).
    assert calibrado_pequeno < calibrado_grande, (
        f"shrinkage não reduziu o ajuste em bin pequeno: pequeno={calibrado_pequeno} "
        f"grande={calibrado_grande}"
    )
    assert abs(calibrado_pequeno - 20.0) < abs(calibrado_grande - 20.0)
    print("  ✓ test_shrinkage_reduz_ajuste_em_bin_pequeno")


def test_anti_vies_funcao_recalibracao_nunca_do_proprio_ciclo() -> None:
    conn = _nova_conn()
    # Ciclo 1: sem função anterior disponível — calibrada == bruta.
    funcao = calibration.carregar_ultima_funcao_recalibracao(conn, "2026-01-01")
    assert funcao is None
    st.persist_snapshots(conn, [_cenario("A", 35)], MATRIZ, "2026-01-01", funcao_recalibracao=funcao)

    row = conn.execute(
        "SELECT probabilidade_bruta, probabilidade_inicial FROM scenario_snapshots WHERE scenario_uid=?",
        ("2026-01-01_A",),
    ).fetchone()
    assert row == (35, 35), f"sem função de calibração, bruta deve == inicial: {row}"

    # Mesmo depois de calcular a calibração do PRÓPRIO ciclo 2026-01-01, ela
    # não pode retroagir sobre os cenários que acabaram de ser emitidos nele.
    calibration.run_calibration_cycle(conn, "2026-01-01")
    funcao_mesmo_ciclo = calibration.carregar_ultima_funcao_recalibracao(conn, "2026-01-01")
    assert funcao_mesmo_ciclo is None, (
        "vazamento: a função calculada no ciclo 2026-01-01 apareceu como "
        "disponível para o PRÓPRIO ciclo 2026-01-01"
    )

    # Só no ciclo SEGUINTE a função passa a estar disponível.
    funcao_prox_ciclo = calibration.carregar_ultima_funcao_recalibracao(conn, "2026-01-02")
    assert funcao_prox_ciclo is not None, "função do ciclo anterior deveria estar disponível no ciclo seguinte"

    conn.close()
    print("  ✓ test_anti_vies_funcao_recalibracao_nunca_do_proprio_ciclo")


def test_probabilidade_bruta_preservada_quando_calibrada_ajusta() -> None:
    conn = _nova_conn()
    funcao_recal = {
        "tipo": "bins_suavizado_v1", "shrinkage_k": 10,
        "bins": [{"bin": "30-35", "n": 50, "atribuido_medio": 32.0,
                   "observado_pct": 60.0, "calibrado": 55.0}],
    }
    st.persist_snapshots(conn, [_cenario("A", 32)], MATRIZ, "2026-02-01",
                          funcao_recalibracao=funcao_recal)
    row = conn.execute(
        "SELECT probabilidade_bruta, probabilidade_inicial FROM scenario_snapshots WHERE scenario_uid=?",
        ("2026-02-01_A",),
    ).fetchone()
    assert row == (32, 55), f"esperado bruta=32 calibrada=55, veio {row}"
    conn.close()
    print("  ✓ test_probabilidade_bruta_preservada_quando_calibrada_ajusta")


def test_medicao_retroativa_nao_vaza_avaliacoes_futuras() -> None:
    """Regressão: a primeira versão do backfill recalculava calibration_history
    para TODOS os ciclos usando sempre o status MAIS RECENTE de cada cenário,
    então um ciclo de 17/06 acabava "sabendo" o desfecho de uma avaliação de
    19/07 — todo o histórico saía com o mesmo ECE, idêntico ciclo após ciclo.
    Isso é exatamente o viés retroativo que o produto existe para evitar."""
    conn = _nova_conn()
    st.persist_snapshots(conn, [_cenario("A", 30)], MATRIZ, "2026-03-01")

    conn.execute(
        "INSERT INTO scenario_evaluations "
        "(scenario_uid, data_avaliacao, gatilhos_acionados, fracao_confirmada, evidencia, status) "
        "VALUES ('2026-03-01_A','2026-03-02','[]',0.0,'{}','em_aberto')"
    )
    conn.execute(
        "INSERT INTO scenario_evaluations "
        "(scenario_uid, data_avaliacao, gatilhos_acionados, fracao_confirmada, evidencia, status) "
        "VALUES ('2026-03-01_A','2026-03-10','[]',1.0,'{}','confirmado')"
    )
    conn.commit()

    # Medido "como se estivéssemos" em 03/03 — só a avaliação de 02/03
    # (em_aberto) era conhecível; a confirmação de 10/03 ainda não existia.
    curva_no_passado = calibration.medir_curva_calibracao(conn, ciclo_corte_id="2026-03-03")
    bin_passado = next(b for b in curva_no_passado if b["n"] > 0)
    assert bin_passado["observado_pct"] == 0.0, (
        f"vazamento: medição em 03/03 já viu a confirmação de 10/03 "
        f"(observado={bin_passado['observado_pct']})"
    )

    # Medido hoje (sem corte, ou com corte >= 10/03): já reflete a confirmação.
    curva_hoje = calibration.medir_curva_calibracao(conn, ciclo_corte_id="2026-03-15")
    bin_hoje = next(b for b in curva_hoje if b["n"] > 0)
    assert bin_hoje["observado_pct"] == 100.0

    conn.close()
    print("  ✓ test_medicao_retroativa_nao_vaza_avaliacoes_futuras")


def test_backfill_de_probabilidade_bruta_em_linhas_antigas() -> None:
    conn = sqlite3.connect(":memory:")
    # Simula uma base pré-calibração: só as tabelas do tracker existem,
    # scenario_snapshots ainda sem a coluna probabilidade_bruta.
    conn.execute(st.DDL_SNAPSHOTS)
    conn.execute(st.DDL_EVALUATIONS)
    conn.execute(
        "INSERT INTO scenario_snapshots (scenario_uid, data_emissao, titulo_cenario, "
        "probabilidade_inicial, horizonte_dias) VALUES ('x_A','2026-01-01','T',35,30)"
    )
    conn.commit()

    calibration.ensure_calibration_schema(conn)  # deve adicionar a coluna e fazer o backfill

    row = conn.execute(
        "SELECT probabilidade_bruta, probabilidade_inicial FROM scenario_snapshots WHERE scenario_uid='x_A'"
    ).fetchone()
    assert row == (35, 35), f"backfill de probabilidade_bruta falhou: {row}"
    conn.close()
    print("  ✓ test_backfill_de_probabilidade_bruta_em_linhas_antigas")


def main() -> int:
    testes = [
        test_ece_e_separacao_amostra_sintetica,
        test_shrinkage_reduz_ajuste_em_bin_pequeno,
        test_anti_vies_funcao_recalibracao_nunca_do_proprio_ciclo,
        test_probabilidade_bruta_preservada_quando_calibrada_ajusta,
        test_medicao_retroativa_nao_vaza_avaliacoes_futuras,
        test_backfill_de_probabilidade_bruta_em_linhas_antigas,
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
