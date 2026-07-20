#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
formation_curves_v1.py — Curva de formação semanal (utilitário compartilhado)

Conta, semana a semana, quantos raw_items batem em pelo menos `min_hits`
palavras-chave de um conjunto (mecanismo_kws de um cenário, ou keywords
derivadas do texto de um vetor). Usado por dois blocos do Radar:

  - Track Record (H2): curva de formação de cada cenário prospectivo
    confirmado, usando o `mecanismo_kws` já salvo em scenario_snapshots.
  - Horizonte 1: curva de formação do vetor estratégico #1, usando keywords
    derivadas do seu nome + mecanismo_causal.

Duas disciplinas de precisão reaproveitadas de features já validadas nesta
mesma sessão:
  - >= 2 keywords distintas por item (não 1) — a mesma correção que resolveu
    o falso-positivo do scenario_tracker (uma palavra genérica batendo
    sozinha não conta como sinal real).
  - piso de volume mínimo (MIN_VOLUME_TOTAL do acceleration_alerts_v1) — uma
    curva sobre poucos itens é ruído, não formação. Abaixo do piso, a
    função retorna None — quem chama decide omitir o bloco graciosamente,
    nunca exibir uma curva vazia/enganosa.

Uso: importar `curva_formacao_semanal` — não tem CLI própria.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta

from acceleration_alerts_v1 import MIN_VOLUME_TOTAL

SEMANAS_DEFAULT = 16
MIN_HITS_DEFAULT = 2


def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _semanas_range(ciclo_id: str, n_semanas: int) -> list[tuple[date, date]]:
    """n_semanas intervalos de 7 dias, do mais antigo ao mais recente,
    terminando em ciclo_id (inclusive). Blocos fixos de 7 dias, não semana
    ISO calendário — garante exatamente n_semanas pontos completos."""
    fim_total = _parse_date(ciclo_id)
    intervalos = []
    cursor = fim_total
    for _ in range(n_semanas):
        ini = cursor - timedelta(days=6)
        intervalos.append((ini, cursor))
        cursor = ini - timedelta(days=1)
    intervalos.reverse()
    return intervalos


def indice_semana_para_data(ciclo_id: str, semanas: int, data_str: str) -> int | None:
    """Em que índice da curva (0-based) cai uma data — para posicionar os
    marcadores de emissão/confirmação no gráfico. None se a data cair fora
    da janela coberta pela curva."""
    try:
        d = _parse_date(data_str)
    except Exception:
        return None
    for idx, (ini, fim) in enumerate(_semanas_range(ciclo_id, semanas)):
        if ini <= d <= fim:
            return idx
    return None


def curva_formacao_semanal(
    conn: sqlite3.Connection,
    kws: list[str],
    ciclo_id: str,
    semanas: int = SEMANAS_DEFAULT,
    min_hits: int = MIN_HITS_DEFAULT,
    min_volume_total: int = MIN_VOLUME_TOTAL,
) -> list[dict] | None:
    """Retorna [{"semana": "AAAA-MM-DD" (início do bloco), "n": contagem}],
    do mais antigo ao mais recente, ou None se o volume total matched não
    passar o piso mínimo (curva sem massa suficiente para ser honesta)."""
    kws_set = {kw for kw in (kws or []) if kw}
    if not kws_set:
        return None

    intervalos = _semanas_range(ciclo_id, semanas)
    data_inicio, data_fim = intervalos[0][0], intervalos[-1][1]

    rows = conn.execute(
        "SELECT cycle_date, title, description FROM raw_items "
        "WHERE cycle_date >= ? AND cycle_date <= ?",
        (data_inicio.isoformat(), data_fim.isoformat()),
    ).fetchall()

    contagens = [0] * semanas
    total = 0
    for cycle_date, title, desc in rows:
        texto = f"{title or ''} {desc or ''}".lower()
        hits = sum(1 for kw in kws_set if re.search(rf"\b{re.escape(kw)}\b", texto))
        if hits < min_hits:
            continue
        try:
            d = _parse_date(cycle_date)
        except Exception:
            continue
        for idx, (ini, fim) in enumerate(intervalos):
            if ini <= d <= fim:
                contagens[idx] += 1
                total += 1
                break

    if total < min_volume_total:
        return None

    return [
        {"semana": ini.isoformat(), "n": contagens[idx]}
        for idx, (ini, _fim) in enumerate(intervalos)
    ]


def curva_formacao_semanal_por_tema(
    conn: sqlite3.Connection,
    theme: str,
    ciclo_id: str,
    semanas: int = SEMANAS_DEFAULT,
    min_volume_total: int = MIN_VOLUME_TOTAL,
) -> list[dict] | None:
    """Variante por match EXATO de raw_items.theme (vocabulário controlado,
    54 valores) em vez de keyword — usada no fallback do vetor #1 (Mudança 2):
    mais precisa que keyword quando já se sabe o tema exato (ex.: o tema de
    maior saliência do acceleration_alerts_v1 neste ciclo)."""
    if not theme:
        return None
    intervalos = _semanas_range(ciclo_id, semanas)
    data_inicio, data_fim = intervalos[0][0], intervalos[-1][1]

    rows = conn.execute(
        "SELECT cycle_date FROM raw_items WHERE theme = ? AND cycle_date >= ? AND cycle_date <= ?",
        (theme, data_inicio.isoformat(), data_fim.isoformat()),
    ).fetchall()

    contagens = [0] * semanas
    total = 0
    for (cycle_date,) in rows:
        try:
            d = _parse_date(cycle_date)
        except Exception:
            continue
        for idx, (ini, fim) in enumerate(intervalos):
            if ini <= d <= fim:
                contagens[idx] += 1
                total += 1
                break

    if total < min_volume_total:
        return None

    return [
        {"semana": ini.isoformat(), "n": contagens[idx]}
        for idx, (ini, _fim) in enumerate(intervalos)
    ]


def curva_e_plana(curva: list[dict] | None, faixa_minima: int = 3) -> bool:
    """True se a curva não tem variação suficiente para contar uma história
    (ex.: vetor #1 do ciclo com sinal estável, sem "formação" perceptível) —
    critério do fallback da Mudança 2."""
    if not curva:
        return True
    vals = [p["n"] for p in curva]
    return (max(vals) - min(vals)) < faixa_minima
