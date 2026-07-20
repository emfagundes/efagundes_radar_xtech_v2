#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
acceleration_alerts_v1.py — Alerta de Aceleração Anômala com Julgamento por LLM

Princípio: o estatístico toca o sino, o LLM lê o enredo. Um gatilho barato
(agregação SQL de raw_items por theme) chama atenção para temas que se
movem; o LLM julga a NATUREZA do movimento (Risco/Oportunidade/Ruído) — não
uma regra de palavra-chave — e, se for risco, devolve mitigação e o custo da
inação.

Fluxo por ciclo:
  1. Funil (barato)   — top-N temas por incidência bruta na janela de referência.
  2. Saliência (barato) — escore = f(aceleração, patamar sustentado), ambos
     normalizados por SHARE (não volume bruto) para não confundir crescimento
     do tema com crescimento do volume total do ciclo.
       aceleração = share(sub-período recente) / share(sub-período anterior)
       patamar    = share(sub-período recente) / share(baseline histórico do
                     próprio tema, antes da janela) — captura um tema que
                     subiu e SE INSTALOU (aceleração ≈ 1, mas patamar alto).
  3. Janela de silêncio — um tema alertado nos últimos N ciclos só reentra se
     o escore saltar o suficiente (evita realertar por inércia).
  4. Julgamento por LLM (caro, custo controlado) — só os top-K temas
     sobreviventes vão ao LLM, com o cluster de sinais do tema como contexto.
  5. Degradação graciosa — se o LLM falhar, o alerta ainda é gravado com os
     componentes estatísticos e natureza="não avaliada". Nunca some.

Uso:
  python acceleration_alerts_v1.py
  python acceleration_alerts_v1.py --input intel_output.json --db ../db/intel.sqlite
  python acceleration_alerts_v1.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

BRASILIA = timezone(timedelta(hours=-3))

ROOT_DIR      = Path(__file__).resolve().parent
DEFAULT_DB    = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"
DEFAULT_INPUT = ROOT_DIR / "intel_output.json"

# ─── Parâmetros (spec Seção 4 e 13 — defaults recomendados pelo próprio spec) ─

N_FUNIL             = 18     # top-N temas por incidência bruta (spec: 15-20)
K_ALERTAS           = 4      # teto rígido de julgamentos de LLM por ciclo
MIN_VOLUME_TOTAL    = 10     # piso de incidência total na janela — abaixo disso, aceleração/
                              # patamar são ruído de amostra pequena (uma razão com denominador
                              # ~0 bate no RATIO_CAP artificialmente e domina o escore sem
                              # representar um movimento real). Ver Seção 6 do spec.
JANELA_DIAS         = 28     # janela móvel de referência (4 semanas)
PESO_ACELERACAO     = 0.5
PESO_PATAMAR        = 0.5
SILENCIO_CICLOS     = 3      # não realertar o mesmo tema nos últimos N ciclos...
SALTO_MINIMO        = 0.15   # ...salvo se o escore subir > 15% sobre o último alerta
RATIO_CAP           = 5.0    # teto para razões com denominador zero (tema novo)
MAX_ITENS_PROMPT    = 20     # cluster de sinais enviado ao LLM por tema

MODELO_JULGAMENTO = "claude-sonnet-5"

ENQUADRAMENTO = (
    "Temas cujo sinal acelerou ou se instalou em patamar elevado neste ciclo, "
    "com a natureza do movimento avaliada. Volume de sinal mede atenção, não "
    "probabilidade — a leitura de risco é interpretação do conteúdo, não do número."
)

NATUREZAS_VALIDAS = {"Risco", "Oportunidade", "Ruído"}
MOMENTOS_VALIDOS = {"Mobilizar Agora", "Capturar Vantagem", "Monitorar Vetores", "Ruído Operacional"}

DDL_ALERTS = """
CREATE TABLE IF NOT EXISTS acceleration_alerts (
    alert_uid             TEXT PRIMARY KEY,
    ciclo_id               TEXT NOT NULL,
    theme                  TEXT NOT NULL,
    escore_saliencia        REAL,
    comp_aceleracao         REAL,
    comp_patamar            REAL,
    enredo                  TEXT,
    natureza                TEXT,
    natureza_secundaria     TEXT,
    acao_mitigacao          TEXT,
    consequencia_inacao     TEXT,
    momento                 TEXT,
    confianca_llm           REAL,
    evidencia               TEXT,
    criado_em               TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_ALERTS)
    conn.commit()


# ─── Funil + saliência (Seções 5 e 6) ──────────────────────────────────────

def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _janelas(ciclo_id: str, janela_dias: int = JANELA_DIAS) -> dict[str, tuple]:
    fim = _parse_date(ciclo_id)
    metade = janela_dias // 2
    inicio_recente = fim - timedelta(days=metade - 1)
    fim_anterior = inicio_recente - timedelta(days=1)
    inicio_total = fim - timedelta(days=janela_dias - 1)
    fim_baseline = inicio_total - timedelta(days=1)
    return {
        "total":    (inicio_total.isoformat(), fim.isoformat()),
        "recente":  (inicio_recente.isoformat(), fim.isoformat()),
        "anterior": (inicio_total.isoformat(), fim_anterior.isoformat()),
        "baseline": (None, fim_baseline.isoformat()),  # sem limite inferior — todo histórico disponível
    }


def _contagem_por_tema(conn: sqlite3.Connection, inicio: str | None, fim: str) -> dict[str, int]:
    if inicio is None:
        rows = conn.execute(
            "SELECT theme, COUNT(*) FROM raw_items "
            "WHERE cycle_date <= ? AND theme IS NOT NULL AND theme != '' "
            "GROUP BY theme",
            (fim,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT theme, COUNT(*) FROM raw_items "
            "WHERE cycle_date >= ? AND cycle_date <= ? AND theme IS NOT NULL AND theme != '' "
            "GROUP BY theme",
            (inicio, fim),
        ).fetchall()
    return {tema: n for tema, n in rows}


def _share(contagens: dict[str, int], tema: str) -> float:
    total = sum(contagens.values())
    if total == 0:
        return 0.0
    return contagens.get(tema, 0) / total


def selecionar_temas_topN(
    contagens_total: dict[str, int], n: int = N_FUNIL, min_volume: int = MIN_VOLUME_TOTAL
) -> list[str]:
    elegiveis = {t: c for t, c in contagens_total.items() if c >= min_volume}
    return [t for t, _ in sorted(elegiveis.items(), key=lambda kv: kv[1], reverse=True)[:n]]


def _razao_com_teto(numerador: float, denominador: float, cap: float = RATIO_CAP) -> float:
    if denominador <= 0:
        return cap if numerador > 0 else 1.0
    return min(numerador / denominador, cap)


def calcular_saliencia_temas(
    conn: sqlite3.Connection,
    ciclo_id: str,
    n: int = N_FUNIL,
    janela_dias: int = JANELA_DIAS,
    peso_aceleracao: float = PESO_ACELERACAO,
    peso_patamar: float = PESO_PATAMAR,
    min_volume: int = MIN_VOLUME_TOTAL,
) -> list[dict]:
    """Etapas do funil + gatilho de saliência. Retorna candidatos ordenados
    por escore_saliencia descendente, com os componentes para auditoria."""
    janelas = _janelas(ciclo_id, janela_dias)
    cont_total    = _contagem_por_tema(conn, *janelas["total"])
    cont_recente  = _contagem_por_tema(conn, *janelas["recente"])
    cont_anterior = _contagem_por_tema(conn, *janelas["anterior"])
    cont_baseline = _contagem_por_tema(conn, *janelas["baseline"])

    temas = selecionar_temas_topN(cont_total, n, min_volume)

    brutos = []
    for tema in temas:
        share_recente  = _share(cont_recente, tema)
        share_anterior = _share(cont_anterior, tema)
        share_baseline = _share(cont_baseline, tema)
        aceleracao = _razao_com_teto(share_recente, share_anterior)
        patamar    = _razao_com_teto(share_recente, share_baseline)
        brutos.append({
            "theme": tema,
            "contagem_total": cont_total.get(tema, 0),
            "share_recente": round(share_recente, 5),
            "share_anterior": round(share_anterior, 5),
            "share_baseline": round(share_baseline, 5),
            "comp_aceleracao": round(aceleracao, 3),
            "comp_patamar": round(patamar, 3),
        })

    if not brutos:
        return []

    acs = [b["comp_aceleracao"] for b in brutos]
    pats = [b["comp_patamar"] for b in brutos]
    ac_min, ac_max = min(acs), max(acs)
    pat_min, pat_max = min(pats), max(pats)

    def _norm(x, lo, hi):
        return 0.5 if hi <= lo else (x - lo) / (hi - lo)

    for b in brutos:
        norm_ac = _norm(b["comp_aceleracao"], ac_min, ac_max)
        norm_pat = _norm(b["comp_patamar"], pat_min, pat_max)
        b["escore_saliencia"] = round(peso_aceleracao * norm_ac + peso_patamar * norm_pat, 4)

    return sorted(brutos, key=lambda b: b["escore_saliencia"], reverse=True)


# ─── Janela de silêncio + seleção top-K (Seção 11) ─────────────────────────

def _ciclos_recentes_com_alerta(conn: sqlite3.Connection, ciclo_atual_id: str, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ciclo_id FROM acceleration_alerts WHERE ciclo_id < ? "
        "ORDER BY ciclo_id DESC LIMIT ?",
        (ciclo_atual_id, n),
    ).fetchall()
    return [r[0] for r in rows]


def _ultimo_alerta_no_silencio(conn: sqlite3.Connection, tema: str, ciclos_silencio: list[str]) -> float | None:
    if not ciclos_silencio:
        return None
    placeholders = ",".join("?" for _ in ciclos_silencio)
    row = conn.execute(
        f"SELECT escore_saliencia FROM acceleration_alerts "
        f"WHERE theme = ? AND ciclo_id IN ({placeholders}) "
        f"ORDER BY ciclo_id DESC LIMIT 1",
        (tema, *ciclos_silencio),
    ).fetchone()
    return row[0] if row else None


def selecionar_top_k(
    conn: sqlite3.Connection,
    ciclo_id: str,
    candidatos_ordenados: list[dict],
    k: int = K_ALERTAS,
    silencio_ciclos: int = SILENCIO_CICLOS,
    salto_minimo: float = SALTO_MINIMO,
) -> list[dict]:
    ciclos_silencio = _ciclos_recentes_com_alerta(conn, ciclo_id, silencio_ciclos)
    selecionados = []
    for cand in candidatos_ordenados:
        escore_anterior = _ultimo_alerta_no_silencio(conn, cand["theme"], ciclos_silencio)
        if escore_anterior is not None and cand["escore_saliencia"] <= escore_anterior * (1 + salto_minimo):
            continue  # em janela de silêncio, sem salto suficiente — pula, próximo candidato preenche a vaga
        selecionados.append(cand)
        if len(selecionados) >= k:
            break
    return selecionados


# ─── Cluster de evidências ──────────────────────────────────────────────────

def coletar_evidencias(conn: sqlite3.Connection, tema: str, ciclo_id: str,
                        janela_dias: int = JANELA_DIAS, max_itens: int = MAX_ITENS_PROMPT) -> list[dict]:
    inicio, fim = _janelas(ciclo_id, janela_dias)["total"]
    rows = conn.execute(
        "SELECT id, title, description, source, url, score FROM raw_items "
        "WHERE theme = ? AND cycle_date >= ? AND cycle_date <= ? "
        "ORDER BY COALESCE(score, 0) DESC LIMIT ?",
        (tema, inicio, fim, max_itens),
    ).fetchall()
    return [
        {"id": r[0], "title": r[1] or "", "description": r[2] or "", "source": r[3] or "", "url": r[4] or ""}
        for r in rows
    ]


# ─── Julgamento por LLM (Seção 7) ──────────────────────────────────────────

def _montar_prompt_julgamento(tema: str, itens: list[dict]) -> str:
    linhas = [f"- [{it['source'] or '?'}] {it['title']} — {it['description'][:220]}" for it in itens]
    corpo = "\n".join(linhas)
    return f"""Você está analisando um cluster de sinais coletados sobre o tema "{tema}", cuja participação no volume total de sinais monitorados acelerou e/ou se instalou em patamar elevado neste ciclo.

Sinais do cluster ({len(itens)} itens, ordenados por relevância):
{corpo}

O número de sinais NÃO indica probabilidade nem importância por si só — sobe igual para inovação, crise, pânico político e ruído. Sua tarefa é ler o CONTEÚDO e julgar a NATUREZA do movimento, não a contagem.

Retorne EXATAMENTE este JSON (sem texto fora do JSON):
{{
  "enredo": "1-2 frases: qual é a história/causa por trás deste movimento — a causa, não a contagem",
  "natureza_dominante": "Risco",
  "natureza_secundaria": null,
  "acao_mitigacao": null,
  "consequencia_inacao": null,
  "momento": "Monitorar Vetores",
  "confianca": 0.7
}}

REGRAS:
- natureza_dominante: "Risco" | "Oportunidade" | "Ruído" (Ruído = tema político ou de mídia sem consequência material concreta para o Brasil)
- natureza_secundaria: mesma lista, ou null se o tema não for misto
- acao_mitigacao e consequencia_inacao: preencha SOMENTE se natureza_dominante == "Risco" (senão null) — ação estratégica concreta e consequência concreta de não agir
- momento: "Mobilizar Agora" (agir já) | "Capturar Vantagem" (relevante, sem urgência imediata) | "Monitorar Vetores" (ainda incerto) | "Ruído Operacional" (sem ação necessária)
- confianca: float 0.0-1.0, sua confiança própria neste veredito (usado para calibração futura)
- acentuação correta, SEM NEGRITO
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
        max_tokens=1200,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def julgar_tema(tema: str, itens: list[dict]) -> dict:
    """Chama o LLM e retorna o veredito validado. Levanta exceção se algo
    falhar — quem chama decide a degradação graciosa (Seção 11)."""
    prompt = _montar_prompt_julgamento(tema, itens)
    texto = _chamar_claude_julgamento(prompt)
    veredito = json.loads(_extrair_json_valido(texto))

    natureza = veredito.get("natureza_dominante")
    if natureza not in NATUREZAS_VALIDAS:
        natureza = "Ruído"
    momento = veredito.get("momento")
    if momento not in MOMENTOS_VALIDOS:
        momento = None
    natureza_sec = veredito.get("natureza_secundaria")
    if natureza_sec not in NATUREZAS_VALIDAS:
        natureza_sec = None

    return {
        "enredo": veredito.get("enredo") or "",
        "natureza": natureza,
        "natureza_secundaria": natureza_sec,
        "acao_mitigacao": veredito.get("acao_mitigacao") if natureza == "Risco" else None,
        "consequencia_inacao": veredito.get("consequencia_inacao") if natureza == "Risco" else None,
        "momento": momento,
        "confianca_llm": max(0.0, min(1.0, float(veredito.get("confianca", 0.5)))),
    }


# ─── Persistência + orquestração ───────────────────────────────────────────

def alert_uid(ciclo_id: str, tema: str) -> str:
    tema_slug = re.sub(r"\s+", "_", tema.strip())
    return f"{ciclo_id}_{tema_slug}"


def _ja_processado(conn: sqlite3.Connection, uid: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM acceleration_alerts WHERE alert_uid = ?", (uid,)
    ).fetchone() is not None


def processar_alertas_ciclo(
    conn: sqlite3.Connection,
    ciclo_id: str,
    n: int = N_FUNIL,
    k: int = K_ALERTAS,
    janela_dias: int = JANELA_DIAS,
) -> list[dict]:
    """Roda o funil completo e grava (idempotente) até k alertas para o
    ciclo. Retorna a lista de alertas emitidos/gravados neste ciclo (novos ou
    já existentes, para compor a saída do JSON)."""
    candidatos = calcular_saliencia_temas(conn, ciclo_id, n=n, janela_dias=janela_dias)
    selecionados = selecionar_top_k(conn, ciclo_id, candidatos, k=k)

    alertas = []
    for cand in selecionados:
        tema = cand["theme"]
        uid = alert_uid(ciclo_id, tema)

        if _ja_processado(conn, uid):
            row = conn.execute(
                "SELECT theme, escore_saliencia, comp_aceleracao, comp_patamar, enredo, natureza, "
                "natureza_secundaria, acao_mitigacao, consequencia_inacao, momento, confianca_llm, evidencia "
                "FROM acceleration_alerts WHERE alert_uid = ?", (uid,),
            ).fetchone()
            cols = ["theme", "escore_saliencia", "comp_aceleracao", "comp_patamar", "enredo", "natureza",
                    "natureza_secundaria", "acao_mitigacao", "consequencia_inacao", "momento",
                    "confianca_llm", "evidencia"]
            alertas.append(dict(zip(cols, row)))
            continue

        evidencias = coletar_evidencias(conn, tema, ciclo_id, janela_dias=janela_dias)
        ids_evidencia = [e["id"] for e in evidencias]

        try:
            veredito = julgar_tema(tema, evidencias) if evidencias else {
                "enredo": "", "natureza": "não avaliada", "natureza_secundaria": None,
                "acao_mitigacao": None, "consequencia_inacao": None, "momento": None,
                "confianca_llm": None,
            }
        except Exception as exc:
            # Degradação graciosa (Seção 11): o alerta nunca some — grava com
            # os componentes estatísticos e natureza "não avaliada".
            print(f"  ⚠ julgamento LLM falhou para '{tema}': {exc} — gravando sem veredito")
            veredito = {
                "enredo": "", "natureza": "não avaliada", "natureza_secundaria": None,
                "acao_mitigacao": None, "consequencia_inacao": None, "momento": None,
                "confianca_llm": None,
            }

        registro = {
            "theme": tema,
            "escore_saliencia": cand["escore_saliencia"],
            "comp_aceleracao": cand["comp_aceleracao"],
            "comp_patamar": cand["comp_patamar"],
            "evidencia": json.dumps(ids_evidencia, ensure_ascii=False),
            **veredito,
        }
        conn.execute(
            """INSERT OR IGNORE INTO acceleration_alerts
               (alert_uid, ciclo_id, theme, escore_saliencia, comp_aceleracao, comp_patamar,
                enredo, natureza, natureza_secundaria, acao_mitigacao, consequencia_inacao,
                momento, confianca_llm, evidencia)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uid, ciclo_id, tema, registro["escore_saliencia"], registro["comp_aceleracao"],
                registro["comp_patamar"], registro["enredo"], registro["natureza"],
                registro["natureza_secundaria"], registro["acao_mitigacao"],
                registro["consequencia_inacao"], registro["momento"], registro["confianca_llm"],
                registro["evidencia"],
            ),
        )
        conn.commit()
        alertas.append(registro)

    return alertas


def build_alertas_output(ciclo_id: str, alertas: list[dict]) -> dict:
    saida = []
    for a in alertas:
        evidencia = a["evidencia"]
        n_evidencias = len(json.loads(evidencia)) if isinstance(evidencia, str) else len(evidencia or [])
        saida.append({
            "theme": a["theme"],
            "escore_saliencia": a["escore_saliencia"],
            "comp_aceleracao": a["comp_aceleracao"],
            "comp_patamar": a["comp_patamar"],
            "enredo": a["enredo"],
            "natureza": a["natureza"],
            "natureza_secundaria": a["natureza_secundaria"],
            "acao_mitigacao": a["acao_mitigacao"],
            "consequencia_inacao": a["consequencia_inacao"],
            "momento": a["momento"],
            "confianca_llm": a["confianca_llm"],
            "n_evidencias": n_evidencias,
        })
    return {"enquadramento": ENQUADRAMENTO, "ciclo_id": ciclo_id, "alertas": saida}


# ─── Entrypoint (pipeline + CLI) ───────────────────────────────────────────

def run_acceleration_alerts(intel_path: Path, db_path: Path, dry_run: bool = False) -> dict:
    intel = json.loads(intel_path.read_text(encoding="utf-8"))
    ciclo_id = intel.get("ciclo_id") or datetime.now(BRASILIA).strftime("%Y-%m-%d")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        if dry_run:
            candidatos = calcular_saliencia_temas(conn, ciclo_id)
            selecionados = selecionar_top_k(conn, ciclo_id, candidatos)
            print(f"  [dry-run] {len(candidatos)} candidato(s) no funil, "
                  f"{len(selecionados)} selecionado(s) para julgamento LLM (nenhuma chamada feita)")
            for s in selecionados:
                print(f"    {s['theme']}: escore={s['escore_saliencia']} "
                      f"aceleração={s['comp_aceleracao']} patamar={s['comp_patamar']}")
            conn.close()
            return {}

        alertas = processar_alertas_ciclo(conn, ciclo_id)
        print(f"  ✓ acceleration_alerts — {len(alertas)} alerta(s) para o ciclo {ciclo_id}")
        saida = build_alertas_output(ciclo_id, alertas)
    finally:
        conn.close()

    if not dry_run:
        intel["alertas_aceleracao"] = saida
        intel_path.write_text(json.dumps(intel, ensure_ascii=False, indent=2), encoding="utf-8")

    return saida


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Alerta de Aceleração Anômala com Julgamento por LLM.")
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    saida = run_acceleration_alerts(Path(args.input), Path(args.db), dry_run=args.dry_run)
    if saida:
        print(f"  · alertas_aceleracao: {len(saida['alertas'])} tema(s) — "
              f"{[a['theme'] for a in saida['alertas']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
