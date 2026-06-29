#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_contradictions.py — Efagundes Intelligence Engine | Detecção de Contradições

Cruza sinais novos do ciclo atual com memórias estratégicas ativas para identificar
tensões analíticas que devem ser explicitadas no briefing.

Lógica:
  1. Para cada vetor estratégico do ciclo, verificar se contradiz memórias ativas.
  2. Para cada par (sinal novo, memória) com tensão detectada, criar/atualizar contradição.
  3. Contradições resolvidas há mais de 7 dias podem ser reabertas se tensão persistir.
  4. Usa LLM (Haiku) em lote: um prompt por par candidato.

Uso:
    python detect_contradictions.py
    python detect_contradictions.py --cycle-date 2026-06-11
    python detect_contradictions.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

ROOT_DIR      = Path(__file__).resolve().parent
DEFAULT_DB    = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"
DEFAULT_INPUT = ROOT_DIR / "intel_output.json"
BRASILIA      = timezone(timedelta(hours=-3))
MODELO        = "claude-haiku-4-5"
MAX_PAIRS     = 20  # limite de pares (vetor × memória) avaliados por ciclo


def now_iso() -> str:
    return datetime.now(BRASILIA).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(BRASILIA).strftime("%Y-%m-%d")


def contradiction_id(signal_title: str, memory_id: str) -> str:
    slug_s = signal_title.lower().replace(" ", "_")[:20]
    slug_m = memory_id[:20]
    return f"ctr_{slug_s}_vs_{slug_m}"


class ContradictionDB:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def get_active_memories(self, min_strength: float = 0.4) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM strategic_memory WHERE status='active' AND strength >= ? ORDER BY strength DESC",
            (min_strength,),
        ).fetchall()

    def get_contradiction(self, cid: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM contradictions WHERE id=?", (cid,)
        ).fetchone()

    def upsert_contradiction(self, c: dict[str, Any]) -> None:
        existing = self.get_contradiction(c["id"])
        if existing:
            self.conn.execute(
                """UPDATE contradictions
                   SET title=?, signal_a=?, signal_b=?, resolution=?,
                       priority=?, status='active', updated_at=?
                   WHERE id=?""",
                (c["title"], c["signal_a"], c["signal_b"], c["resolution"],
                 c["priority"], now_iso(), c["id"]),
            )
        else:
            self.conn.execute(
                """INSERT INTO contradictions
                   (id, title, signal_a, signal_b, memory_ids, resolution, priority, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (c["id"], c["title"], c["signal_a"], c["signal_b"],
                 c.get("memory_ids", ""), c["resolution"], c["priority"],
                 "active", now_iso(), now_iso()),
            )

    def get_active_contradictions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM contradictions WHERE status='active' ORDER BY priority DESC, updated_at DESC"
        ).fetchall()

    def commit(self) -> None:
        self.conn.commit()


def evaluate_contradiction(
    client: anthropic.Anthropic,
    signal_title: str,
    signal_desc: str,
    memory_title: str,
    memory_thesis: str,
) -> dict[str, Any]:
    system = (
        "Você é um analista estratégico especializado em identificar tensões analíticas. "
        "Dado um sinal novo e uma tese estratégica acumulada, você avalia se existe contradição real "
        "ou apenas complementaridade. Responda exclusivamente com JSON válido."
    )
    user = f"""Sinal novo:
  Título: {signal_title}
  Descrição: {signal_desc}

Tese estratégica acumulada:
  Título: {memory_title}
  Tese: {memory_thesis}

Avalie e retorne JSON com:
- has_contradiction: true | false
- priority: "alta" | "media" | "baixa"
- title: título curto da contradição (máx 60 chars)
- signal_a: descrição objetiva do sinal novo em 1 frase
- signal_b: descrição objetiva da tese anterior em 1 frase
- resolution: síntese que resolve a tensão sem eliminar nenhum dos lados (máx 150 chars)
- reason: por que é contradição ou por que não é (1 frase)

Só marque has_contradiction=true se os dois sinais apontarem em direções opostas para a mesma decisão.
Complementaridade, nuances e coexistência NÃO são contradições."""

    msg = client.messages.create(
        model=MODELO,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return json.loads(text)


def build_candidates(vetores: list[dict], memories: list[sqlite3.Row]) -> list[tuple[dict, sqlite3.Row]]:
    """
    Heurística rápida (sem LLM) para pré-filtrar pares com alta chance de contradição.
    Reduz o número de chamadas LLM ao limite MAX_PAIRS.
    """
    # Palavras que sinalizam tensão temática
    TENSION_PAIRS = [
        ({"solar", "custo", "capex", "barata"}, {"curtailment", "corte", "ons", "geração"}),
        ({"renovável", "eólica", "solar"},       {"despacho térmico", "termoelétrica", "térmica"}),
        ({"investimento", "anunciado"},           {"judicialização", "judicial", "embargo"}),
        ({"demanda crescente", "data centers"},   {"infraestrutura", "transmissão", "rede"}),
        ({"descarbonização", "zero"},             {"emissões", "thermal", "gás natural"}),
    ]

    candidates: list[tuple[dict, sqlite3.Row]] = []
    for vetor in vetores:
        v_text = (
            (vetor.get("nome") or "") + " " +
            (vetor.get("descricao_executiva") or "") + " " +
            (vetor.get("mecanismo_causal") or "")
        ).lower()

        for mem in memories:
            m_text = ((mem["title"] or "") + " " + (mem["thesis"] or "")).lower()
            combined = v_text + " " + m_text

            # Verifica se há pelo menos um par de tensão temática
            has_tension = any(
                any(a in combined for a in side_a) and any(b in combined for b in side_b)
                for side_a, side_b in TENSION_PAIRS
            )
            # Ou se os dois textos compartilham entidades mas têm sentido oposto
            v_tokens = set(v_text.split())
            m_tokens = set(m_text.split())
            overlap = len(v_tokens & m_tokens)
            if has_tension or overlap >= 4:
                candidates.append((vetor, mem))

    # Limitar ao máximo de pares
    return candidates[:MAX_PAIRS]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Detecta contradições entre sinais novos e memórias ativas.")
    p.add_argument("--input",      default=str(DEFAULT_INPUT))
    p.add_argument("--db",         default=str(DEFAULT_DB))
    p.add_argument("--cycle-date", default=None)
    p.add_argument("--dry-run",    action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"Banco não encontrado: {db_path}. Execute init_db.py primeiro.")
        return 1

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT_DIR / input_path
    if not input_path.exists():
        print(f"Arquivo não encontrado: {input_path}")
        return 1

    data = json.loads(input_path.read_text(encoding="utf-8"))
    cycle_date = args.cycle_date or data.get("ciclo_id") or today()
    vetores    = [v for v in (data.get("vetores_estrategicos") or []) if isinstance(v, dict)]

    db     = ContradictionDB(db_path)
    client = anthropic.Anthropic()
    found  = 0
    total_pairs = 0

    try:
        memories = db.get_active_memories(min_strength=0.4)
        print(f"Contradições | ciclo {cycle_date} | {len(vetores)} vetores × {len(memories)} memórias ativas")

        if not memories:
            print("  · Nenhuma memória ativa ainda. Pulando detecção.")
            return 0

        candidates = build_candidates(vetores, memories)
        print(f"  · {len(candidates)} pares candidatos identificados por heurística")

        for vetor, mem in candidates:
            total_pairs += 1
            v_nome   = vetor.get("nome") or vetor.get("id") or "?"
            v_desc   = vetor.get("descricao_executiva") or vetor.get("mecanismo_causal") or ""
            m_title  = mem["title"] or ""
            m_thesis = mem["thesis"] or ""

            if args.dry_run:
                print(f"  [dry-run] Avaliaria: [{v_nome[:40]}] × [{m_title[:40]}]")
                continue

            try:
                result = evaluate_contradiction(client, v_nome, v_desc, m_title, m_thesis)
            except Exception as exc:
                print(f"  ✗ Erro no par [{v_nome[:30]}] × [{m_title[:30]}]: {exc}")
                continue

            if not result.get("has_contradiction"):
                print(f"  · Sem contradição: [{v_nome[:40]}] × [{m_title[:40]}]")
                continue

            cid = contradiction_id(v_nome, mem["id"])
            c = {
                "id": cid,
                "title": result.get("title") or f"{v_nome[:30]} vs {m_title[:30]}",
                "signal_a": result.get("signal_a") or v_nome,
                "signal_b": result.get("signal_b") or m_thesis,
                "memory_ids": mem["id"],
                "resolution": result.get("resolution") or "",
                "priority": result.get("priority") or "media",
            }
            db.upsert_contradiction(c)
            found += 1
            print(f"  ⚡ [{result.get('priority','?').upper()}] {c['title'][:70]}")
            print(f"      A: {c['signal_a'][:70]}")
            print(f"      B: {c['signal_b'][:70]}")
            if c["resolution"]:
                print(f"      ↔ {c['resolution'][:80]}")

        db.commit()
    finally:
        db.close()

    print(f"\n  Resultado: {found} contradições detectadas de {total_pairs} pares avaliados")

    # Sumário de contradições ativas
    db2 = ContradictionDB(db_path)
    try:
        all_active = db2.get_active_contradictions()
        print(f"  Total ativas no banco: {len(all_active)}")
    finally:
        db2.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
