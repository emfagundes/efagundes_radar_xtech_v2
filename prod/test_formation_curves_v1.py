#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_formation_curves_v1.py — curva de formação semanal: contagem correta,
piso de volume, disciplina de >=2 keywords, índice de semana para marcadores.

Uso:
  python test_formation_curves_v1.py
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta

import formation_curves_v1 as fc

CICLO = "2026-07-19"


def _nova_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE raw_items (
            id TEXT PRIMARY KEY, cycle_date TEXT, title TEXT, description TEXT
        )
    """)
    return conn


def _seed(conn: sqlite3.Connection, cycle_date: str, title: str, desc: str = "", n: int = 1) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO raw_items (id, cycle_date, title, description) VALUES (?,?,?,?)",
            (f"{cycle_date}_{title}_{i}", cycle_date, title, desc),
        )
    conn.commit()


def test_curva_conta_por_semana_com_2_hits() -> None:
    conn = _nova_conn()
    fim = date(2026, 7, 19)
    # semana mais recente (13-19/jul): 12 itens com 2+ keywords batendo
    _seed(conn, "2026-07-15", "Jabutis tarifários freiam investimentos", n=12)
    # semana anterior (06-12/jul): 3 itens
    _seed(conn, "2026-07-08", "Jabutis tarifários geram incerteza", n=3)
    # ruído: só 1 keyword bate (não conta, exige >=2)
    _seed(conn, "2026-07-15", "Apenas jabutis mencionados sem mais nada relevante aqui", n=5)

    curva = fc.curva_formacao_semanal(conn, ["jabutis", "tarifários", "investimentos"], CICLO, semanas=4)
    assert curva is not None, "curva não deveria ser None (volume total = 15 >= piso)"
    assert len(curva) == 4
    assert curva[-1]["n"] == 12, f"semana mais recente deveria ter 12, veio {curva[-1]['n']}"
    assert curva[-2]["n"] == 3, f"semana anterior deveria ter 3, veio {curva[-2]['n']}"
    print("  ✓ test_curva_conta_por_semana_com_2_hits")


def test_piso_de_volume_retorna_none() -> None:
    conn = _nova_conn()
    _seed(conn, "2026-07-15", "Tema pequeno aqui", n=3)  # abaixo do piso (MIN_VOLUME_TOTAL=10)
    curva = fc.curva_formacao_semanal(conn, ["tema", "pequeno"], CICLO, semanas=4, min_volume_total=10)
    assert curva is None, "curva com volume abaixo do piso deveria retornar None"
    print("  ✓ test_piso_de_volume_retorna_none")


def test_kws_vazio_retorna_none() -> None:
    conn = _nova_conn()
    _seed(conn, "2026-07-15", "Qualquer coisa", n=20)
    assert fc.curva_formacao_semanal(conn, [], CICLO) is None
    assert fc.curva_formacao_semanal(conn, None, CICLO) is None
    print("  ✓ test_kws_vazio_retorna_none")


def test_indice_semana_para_data() -> None:
    # 4 semanas terminando em 2026-07-19: [06-28..07-04, 07-05..07-11, 07-12..07-18, 07-19..07-19+6? ]
    # última semana termina exatamente em 2026-07-19.
    idx_recente = fc.indice_semana_para_data(CICLO, 4, "2026-07-19")
    assert idx_recente == 3, f"esperado índice 3 (última semana), veio {idx_recente}"

    idx_antiga = fc.indice_semana_para_data(CICLO, 4, "2026-06-25")
    assert idx_antiga == 0, f"esperado índice 0 (primeira semana), veio {idx_antiga}"

    idx_fora = fc.indice_semana_para_data(CICLO, 4, "2026-01-01")
    assert idx_fora is None, "data muito antiga deveria cair fora da janela (None)"
    print("  ✓ test_indice_semana_para_data")


def main() -> int:
    testes = [
        test_curva_conta_por_semana_com_2_hits,
        test_piso_de_volume_retorna_none,
        test_kws_vazio_retorna_none,
        test_indice_semana_para_data,
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
