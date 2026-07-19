#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario_calibration_v1.py — Processo de Auto-Calibração de Cenários

Fecha o loop do scenario_tracker_v1: mede o poder discriminante das
probabilidades atribuídas aos cenários prospectivos, diagnostica o erro de
calibração (ECE) e a separação (poder discriminante), deriva uma função de
recalibração por bins suavizada e a realimenta nos cenários do ciclo
seguinte — sem intervenção manual.

Conceito central (spec Seção 4): calibração ≠ acerto. Um sistema calibrado é
aquele em que, entre os cenários com probabilidade atribuída ~30%, ~30% se
confirmam — independente de quantos "acertam". Isso é o que torna a
probabilidade uma informação confiável para decisão.

Loop de 4 etapas, uma vez por ciclo (chamado por scenario_tracker_v1 depois
de evaluate_open_scenarios, para que a medição use as avaliações já
atualizadas do ciclo corrente):
  1. MEDIR        — curva de calibração por bins de probabilidade_inicial
  2. DIAGNOSTICAR — ECE (erro de calibração) e separação (poder discriminante)
  3. RECALIBRAR   — função de mapeamento bruta→calibrada por bins suavizados
  4. REALIMENTAR  — a função derivada neste ciclo é aplicada apenas a partir
                     do ciclo SEGUINTE (scenario_tracker_v1.persist_snapshots
                     carrega a função do ciclo anterior antes de persistir os
                     novos snapshots) — nunca ao próprio ciclo que a gerou.

Rótulo usado para "confirmado" na medição: o status ATUAL de cada cenário
(scenario_evaluations mais recente). Cenários ainda em_aberto/em_formacao
contam como não confirmados (rótulo provisório) — mesma convenção já usada
em scenario_tracker_v1.build_scenario_tracking_summary()["calibracao"]. A
medição fica mais precisa à medida que mais cenários atingem um desfecho
terminal (confirmado/nao_materializado).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta

BRASILIA = timezone(timedelta(hours=-3))

# Bins de probabilidade_inicial — spec Seção 5, Etapa 1.
# (limite_inferior_inclusive, limite_superior_exclusive, rótulo)
CALIBRATION_BINS: list[tuple[float, float, str]] = [
    (float("-inf"), 25.0, "<25"),
    (25.0, 30.0, "25-30"),
    (30.0, 35.0, "30-35"),
    (35.0, 40.0, "35-40"),
    (40.0, float("inf"), ">40"),
]

# Constante de suavização (shrinkage empírico-bayesiano): peso = n / (n + K).
# Bins com poucos casos puxam menos para o valor observado (regularização em
# direção à diagonal = ao valor originalmente atribuído). K=10 é conservador
# dado o volume inicial (~69 cenários / ~14 por bin).
SHRINKAGE_K = 10

LIMIAR_CONFIRMACAO_LABEL = {"confirmado"}  # demais status contam como 0

ENQUADRAMENTO_CALIBRACAO = (
    "Calibração mede se \"30%\" significa 30% de confirmação observada — não é "
    "taxa de acerto. O sistema audita e corrige a própria calibração a cada ciclo."
)

DDL_CALIBRATION_HISTORY = """
CREATE TABLE IF NOT EXISTS calibration_history (
    ciclo_id                TEXT PRIMARY KEY,
    n_cenarios_avaliados     INTEGER,
    ece                      REAL,
    separacao                REAL,
    curva_bins               TEXT,
    funcao_recalibracao      TEXT,
    justificativa            TEXT,
    criado_em                TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


# ─── Schema ────────────────────────────────────────────────────────────────

def ensure_calibration_schema(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_CALIBRATION_HISTORY)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(scenario_snapshots)")}
    if "probabilidade_bruta" not in cols:
        conn.execute("ALTER TABLE scenario_snapshots ADD COLUMN probabilidade_bruta INTEGER")
        # Para os cenários já existentes (emitidos antes da auto-calibração
        # existir), a probabilidade exibida FOI a bruta — não houve ajuste.
        conn.execute(
            "UPDATE scenario_snapshots SET probabilidade_bruta = probabilidade_inicial "
            "WHERE probabilidade_bruta IS NULL"
        )
    conn.commit()


# ─── Etapa 1 — Medir ────────────────────────────────────────────────────────

def _bin_de(prob: float) -> str:
    for lo, hi, label in CALIBRATION_BINS:
        if lo <= prob < hi:
            return label
    return CALIBRATION_BINS[-1][2]


def _rotulos_atuais(conn: sqlite3.Connection, ciclo_corte_id: str | None = None) -> list[tuple[float, int]]:
    """Retorna [(probabilidade_inicial, rotulo)] usando apenas o que era
    conhecível NO ciclo_corte_id — mesma regra anti-viés do tracker, aplicada
    à própria medição de calibração:
      - só entram cenários com data_emissao <= ciclo_corte_id
      - o rótulo usa o status mais recente com data_avaliacao <= ciclo_corte_id
        (0 se nenhuma avaliação qualifica — ainda não observado NAQUELE ciclo)
    Sem ciclo_corte_id, usa todo o histórico disponível (medição "hoje").
    Isto é essencial para o backfill retroativo: sem o corte, a medição de um
    ciclo antigo vazaria desfechos de avaliações muito mais recentes,
    produzindo uma curva de ECE artificialmente idêntica em todos os ciclos —
    o próprio viés retroativo que este produto existe para evitar."""
    latest_status: dict[str, str] = {}
    for uid, status, data_avaliacao in conn.execute(
        "SELECT scenario_uid, status, data_avaliacao FROM scenario_evaluations "
        "ORDER BY data_avaliacao ASC"
    ):
        if ciclo_corte_id is not None and data_avaliacao > ciclo_corte_id:
            continue
        latest_status[uid] = status  # última linha (ASC, já filtrada) vence

    out: list[tuple[float, int]] = []
    for uid, prob, data_emissao in conn.execute(
        "SELECT scenario_uid, probabilidade_inicial, data_emissao FROM scenario_snapshots"
    ):
        if ciclo_corte_id is not None and data_emissao > ciclo_corte_id:
            continue
        rotulo = 1 if latest_status.get(uid) in LIMIAR_CONFIRMACAO_LABEL else 0
        out.append((float(prob or 0), rotulo))
    return out


def medir_curva_calibracao(conn: sqlite3.Connection, ciclo_corte_id: str | None = None) -> list[dict]:
    """Etapa 1 — agrupa por bin e computa (atribuído_médio, observado, n),
    usando somente o que era conhecível em ciclo_corte_id (ver _rotulos_atuais)."""
    amostras = _rotulos_atuais(conn, ciclo_corte_id)
    por_bin: dict[str, list[tuple[float, int]]] = {b[2]: [] for b in CALIBRATION_BINS}
    for prob, rotulo in amostras:
        por_bin[_bin_de(prob)].append((prob, rotulo))

    curva = []
    for _, _, label in CALIBRATION_BINS:
        itens = por_bin[label]
        n = len(itens)
        atribuido_medio = round(sum(p for p, _ in itens) / n, 1) if n else None
        observado_pct = round(100 * sum(r for _, r in itens) / n, 1) if n else None
        curva.append({
            "bin": label, "n": n,
            "atribuido_medio": atribuido_medio,
            "observado_pct": observado_pct,
        })
    return curva


# ─── Etapa 2 — Diagnosticar ─────────────────────────────────────────────────

def calcular_ece(curva_bins: list[dict]) -> float | None:
    bins_com_dado = [b for b in curva_bins if b["n"] > 0]
    total = sum(b["n"] for b in bins_com_dado)
    if not total:
        return None
    erro_ponderado = sum(
        b["n"] * abs(b["atribuido_medio"] - b["observado_pct"]) for b in bins_com_dado
    )
    return round(erro_ponderado / total / 100, 4)  # normalizado para fração 0-1


def calcular_separacao(conn: sqlite3.Connection, ciclo_corte_id: str | None = None) -> float | None:
    """Diferença entre taxa de confirmação do tércil superior e inferior de
    probabilidade_inicial. None se amostra pequena demais para tercis (<3)."""
    amostras = sorted(_rotulos_atuais(conn, ciclo_corte_id), key=lambda x: x[0])
    n = len(amostras)
    if n < 3:
        return None
    k = max(1, n // 3)
    inferior = amostras[:k]
    superior = amostras[-k:]
    taxa_inf = sum(r for _, r in inferior) / len(inferior)
    taxa_sup = sum(r for _, r in superior) / len(superior)
    return round(taxa_sup - taxa_inf, 4)


# ─── Etapa 3 — Recalibrar ───────────────────────────────────────────────────

def derivar_funcao_recalibracao(curva_bins: list[dict], shrinkage_k: int = SHRINKAGE_K) -> dict:
    bins_calibrados = []
    for b in curva_bins:
        n = b["n"]
        if n == 0 or b["atribuido_medio"] is None:
            bins_calibrados.append({**b, "peso_observado": 0.0, "calibrado": None})
            continue
        peso = n / (n + shrinkage_k)
        calibrado = round(peso * b["observado_pct"] + (1 - peso) * b["atribuido_medio"], 1)
        bins_calibrados.append({**b, "peso_observado": round(peso, 3), "calibrado": calibrado})
    return {"tipo": "bins_suavizado_v1", "shrinkage_k": shrinkage_k, "bins": bins_calibrados}


def aplicar_calibracao(prob_bruta: float, funcao_recalibracao: dict | None) -> int:
    """Etapa 4 — mapeia uma probabilidade_bruta recém-gerada para a
    probabilidade_calibrada, usando a função de um ciclo ANTERIOR. Sem
    função disponível (primeiros ciclos) ou bin sem dado histórico, retorna a
    bruta inalterada (nenhum ajuste possível sem evidência)."""
    if not funcao_recalibracao:
        return int(round(prob_bruta))
    label = _bin_de(float(prob_bruta))
    for b in funcao_recalibracao.get("bins", []):
        if b["bin"] == label and b.get("calibrado") is not None:
            return int(round(max(0, min(100, b["calibrado"]))))
    return int(round(prob_bruta))


# ─── Persistência / orquestração ───────────────────────────────────────────

def carregar_ultima_funcao_recalibracao(conn: sqlite3.Connection, ciclo_atual_id: str) -> dict | None:
    """Função derivada no ciclo anterior mais recente — nunca a do próprio
    ciclo atual (essa ainda não existe neste ponto do pipeline)."""
    row = conn.execute(
        "SELECT funcao_recalibracao FROM calibration_history "
        "WHERE ciclo_id < ? ORDER BY ciclo_id DESC LIMIT 1",
        (ciclo_atual_id,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return json.loads(row[0])


def run_calibration_cycle(conn: sqlite3.Connection, ciclo_id: str) -> dict:
    """Etapas 1+2+3 para o ciclo atual — chamado DEPOIS de
    evaluate_open_scenarios, para medir com as avaliações já atualizadas.
    Grava (ou substitui, se o ciclo for reprocessado) uma linha em
    calibration_history, disponível para aplicação a partir do PRÓXIMO ciclo."""
    ensure_calibration_schema(conn)

    curva = medir_curva_calibracao(conn, ciclo_corte_id=ciclo_id)
    ece = calcular_ece(curva)
    separacao = calcular_separacao(conn, ciclo_corte_id=ciclo_id)
    funcao = derivar_funcao_recalibracao(curva)
    n_total = sum(b["n"] for b in curva)

    justificativa = (
        f"ECE={ece if ece is not None else 'n/d'}, "
        f"separação={separacao if separacao is not None else 'n/d'} "
        f"sobre {n_total} cenário(s). "
        + "; ".join(
            f"{b['bin']}: atribuído={b['atribuido_medio']}, observado={b['observado_pct']}%, "
            f"n={b['n']}, calibrado→{b['calibrado']}"
            for b in funcao["bins"] if b["n"] > 0
        )
    )

    conn.execute(
        """INSERT INTO calibration_history
           (ciclo_id, n_cenarios_avaliados, ece, separacao, curva_bins, funcao_recalibracao, justificativa)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(ciclo_id) DO UPDATE SET
             n_cenarios_avaliados=excluded.n_cenarios_avaliados,
             ece=excluded.ece,
             separacao=excluded.separacao,
             curva_bins=excluded.curva_bins,
             funcao_recalibracao=excluded.funcao_recalibracao,
             justificativa=excluded.justificativa,
             criado_em=CURRENT_TIMESTAMP
        """,
        (
            ciclo_id, n_total, ece, separacao,
            json.dumps(curva, ensure_ascii=False),
            json.dumps(funcao, ensure_ascii=False),
            justificativa,
        ),
    )
    conn.commit()

    return {"ciclo_id": ciclo_id, "n_cenarios_avaliados": n_total, "ece": ece,
            "separacao": separacao, "curva_bins": curva, "funcao_recalibracao": funcao,
            "justificativa": justificativa}


def build_auto_calibracao_summary(conn: sqlite3.Connection) -> dict:
    """Sub-chave scenario_tracking["auto_calibracao"] — spec Seção 7."""
    linhas = conn.execute(
        "SELECT ciclo_id, ece, separacao, curva_bins, justificativa "
        "FROM calibration_history ORDER BY ciclo_id ASC"
    ).fetchall()

    if not linhas:
        return {
            "enquadramento": ENQUADRAMENTO_CALIBRACAO,
            "ece_atual": None, "ece_anterior": None,
            "separacao_atual": None, "separacao_anterior": None,
            "curva_calibracao": [],
            "ajuste_aplicado": "Ainda sem histórico de calibração — primeira medição ocorre no próximo ciclo.",
            "n_ciclos_historico": 0,
        }

    atual = linhas[-1]
    anterior = linhas[-2] if len(linhas) >= 2 else None

    return {
        "enquadramento": ENQUADRAMENTO_CALIBRACAO,
        "ece_atual": atual[1],
        "ece_anterior": anterior[1] if anterior else None,
        "separacao_atual": atual[2],
        "separacao_anterior": anterior[2] if anterior else None,
        "curva_calibracao": json.loads(atual[3]) if atual[3] else [],
        "ajuste_aplicado": atual[4],
        "n_ciclos_historico": len(linhas),
    }
