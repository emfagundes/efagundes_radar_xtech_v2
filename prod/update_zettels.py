#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_zettels.py — Efagundes Intelligence Engine | Notas Zettelkasten

Transforma vetores estratégicos do intel_output.json em notas atômicas
persistentes na tabela zettel_notes do SQLite.

Regras:
  - Uma nota por vetor estratégico por ciclo.
  - Se nota com mesmo ID de vetor já existir e estiver ativa, atualiza last_seen e strength.
  - Se o vetor apresentar mudança relevante, cria nova nota e encerra a anterior.
  - Usa LLM (Haiku) para gerar corpo da nota e interpretação.
  - Notas expiradas (expires_at < hoje) são marcadas status='expired'.

Uso:
    python update_zettels.py
    python update_zettels.py --input intel_output.json --cycle-date 2026-06-11
    python update_zettels.py --db /outro/intel.sqlite --dry-run
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
MODELO_ZETTEL = "claude-haiku-4-5"


def now_iso() -> str:
    return datetime.now(BRASILIA).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(BRASILIA).strftime("%Y-%m-%d")


def expires_from(days: int) -> str:
    return (datetime.now(BRASILIA) + timedelta(days=days)).strftime("%Y-%m-%d")


def zettel_id(vetor_id: str, cycle_date: str) -> str:
    slug = vetor_id.lower().replace(" ", "_").replace("-", "_")[:30]
    return f"zk_{cycle_date.replace('-', '_')}_{slug}"


def persistence_from_pressure(pressure: float) -> int:
    if pressure >= 8.5:
        return 90
    if pressure >= 7.0:
        return 30
    if pressure >= 5.0:
        return 14
    return 7


def strength_from_pressure(pressure: float) -> float:
    return min(round(pressure / 10.0, 2), 1.0)


ZETTEL_SCHEMA = {
    "type": "object",
    "properties": {
        "title":          {"type": "string"},
        "body":           {"type": "string"},
        "interpretation": {"type": "string"},
        "supports":       {"type": "string"},
        "contradicts":    {"type": "string"},
        "themes":         {"type": "array", "items": {"type": "string"}},
        "entities":       {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "body", "interpretation", "supports", "themes", "entities"],
}


def generate_zettel_body(client: anthropic.Anthropic, vetor: dict[str, Any], cycle_date: str) -> dict[str, Any]:
    nome         = vetor.get("nome") or vetor.get("id") or "Vetor sem título"
    descricao    = vetor.get("descricao_executiva") or ""
    mecanismo    = vetor.get("mecanismo_causal") or ""
    consequencia = vetor.get("consequencia_inacao") or ""
    decisao      = vetor.get("decisao_recomendada") or ""
    setores      = json.dumps(vetor.get("setores_afetados") or [], ensure_ascii=False)
    pressao      = vetor.get("pressao_estrategica") or 0
    quadrante    = vetor.get("quadrante_executivo") or ""

    system = (
        "Você é um analista estratégico especializado em energia, infraestrutura digital e regulação no Brasil. "
        "Você escreve notas Zettelkasten: atômicas, densas, diretas. "
        "Cada nota deve ser autossuficiente: um leitor sem contexto prévio deve entendê-la completamente. "
        "Escreva em português. Evite jargão genérico. Use nomes específicos de entidades, valores e mecanismos. "
        "Responda exclusivamente com JSON válido conforme o schema solicitado."
    )

    user = f"""Crie uma nota Zettelkasten para o seguinte vetor estratégico detectado em {cycle_date}:

Vetor: {nome}
Pressão estratégica: {pressao}/10
Quadrante: {quadrante}
Setores afetados: {setores}
Descrição executiva: {descricao}
Mecanismo causal: {mecanismo}
Consequência da inação: {consequencia}
Decisão recomendada: {decisao}

Retorne um objeto JSON com estes campos (todos obrigatórios, strings curtas):
- title: título da nota (máx 70 chars)
- body: corpo da nota (2 parágrafos curtos, máx 300 chars cada)
- interpretation: frase única (máx 150 chars) resumindo o que muda
- supports: teses reforçadas (máx 100 chars, pode ser vazio string)
- contradicts: teses contraditas (máx 100 chars, pode ser vazio string)
- themes: lista de 2–4 temas curtos ex: ["energia", "BESS"]
- entities: lista de 2–5 entidades ex: ["ONS", "ANEEL"]"""

    msg = client.messages.create(
        model=MODELO_ZETTEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
    return json.loads(text)


class ZettelDB:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def get_active_zettel_by_vector(self, vetor_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM zettel_notes WHERE id LIKE ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (f"%{vetor_id.lower().replace('-', '_')}%",),
        ).fetchone()

    def note_exists_for_cycle(self, zid: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM zettel_notes WHERE id = ?", (zid,)
        ).fetchone()
        return row is not None

    def insert_note(self, note: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO zettel_notes
              (id, title, body, cycle_date, themes, entities, interpretation,
               supports, contradicts, persistence_days, strength, status,
               first_seen, last_seen, expires_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                note["id"], note["title"], note["body"], note["cycle_date"],
                note["themes"], note["entities"], note["interpretation"],
                note.get("supports", ""), note.get("contradicts", ""),
                note["persistence_days"], note["strength"], "active",
                note["cycle_date"], note["cycle_date"], note["expires_at"],
                now_iso(), now_iso(),
            ),
        )

    def refresh_note(self, zid: str, cycle_date: str, new_strength: float) -> None:
        self.conn.execute(
            "UPDATE zettel_notes SET last_seen=?, strength=?, updated_at=? WHERE id=?",
            (cycle_date, new_strength, now_iso(), zid),
        )

    def expire_old_notes(self) -> int:
        cur = self.conn.execute(
            "UPDATE zettel_notes SET status='expired' WHERE status='active' AND expires_at < ?",
            (today(),),
        )
        return cur.rowcount

    def commit(self) -> None:
        self.conn.commit()


def process_vector(
    db: ZettelDB,
    client: anthropic.Anthropic,
    vetor: dict[str, Any],
    cycle_date: str,
    dry_run: bool = False,
) -> str:
    """Returns 'created', 'refreshed', or 'skipped'."""
    vetor_id  = vetor.get("id") or vetor.get("nome") or str(uuid.uuid4())[:8]
    pressao   = float(vetor.get("pressao_estrategica") or 0)
    pers      = persistence_from_pressure(pressao)
    strength  = strength_from_pressure(pressao)
    zid       = zettel_id(vetor_id, cycle_date)

    # Já processado neste ciclo
    if db.note_exists_for_cycle(zid):
        return "skipped"

    # Verificar se existe nota ativa para este vetor de ciclo anterior
    existing = db.get_active_zettel_by_vector(vetor_id)
    if existing:
        # Apenas atualiza força e last_seen — não gera nova nota com LLM
        if not dry_run:
            db.refresh_note(existing["id"], cycle_date, strength)
        return "refreshed"

    # Nova nota — gerar com LLM
    if dry_run:
        print(f"    [dry-run] Geraria nota para: {vetor_id}")
        return "created"

    try:
        body_data = generate_zettel_body(client, vetor, cycle_date)
    except Exception as exc:
        print(f"    ✗ LLM falhou para {vetor_id}: {exc}")
        return "skipped"

    note = {
        "id": zid,
        "title": body_data.get("title") or vetor.get("nome") or vetor_id,
        "body": body_data.get("body") or "",
        "cycle_date": cycle_date,
        "themes": json.dumps(body_data.get("themes") or [], ensure_ascii=False),
        "entities": json.dumps(body_data.get("entities") or [], ensure_ascii=False),
        "interpretation": body_data.get("interpretation") or "",
        "supports": body_data.get("supports") or "",
        "contradicts": body_data.get("contradicts") or "",
        "persistence_days": pers,
        "strength": strength,
        "expires_at": expires_from(pers),
    }
    db.insert_note(note)
    return "created"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atualiza notas Zettelkasten a partir dos vetores estratégicos.")
    p.add_argument("--input",      default=str(DEFAULT_INPUT))
    p.add_argument("--db",         default=str(DEFAULT_DB))
    p.add_argument("--cycle-date", default=None)
    p.add_argument("--dry-run",    action="store_true")
    return p.parse_args()


def main() -> int:
    args  = parse_args()
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
    vetores = [v for v in (data.get("vetores_estrategicos") or []) if isinstance(v, dict)]

    print(f"Zettelkasten | ciclo {cycle_date} | {len(vetores)} vetores")

    client = anthropic.Anthropic()
    db     = ZettelDB(db_path)
    counts = {"created": 0, "refreshed": 0, "skipped": 0}

    try:
        expired = db.expire_old_notes()
        if expired:
            print(f"  · {expired} notas expiradas marcadas como 'expired'")

        for vetor in vetores:
            nome   = vetor.get("nome") or vetor.get("id") or "?"
            result = process_vector(db, client, vetor, cycle_date, args.dry_run)
            counts[result] += 1
            symbol = "+" if result == "created" else ("↺" if result == "refreshed" else "·")
            print(f"  {symbol} [{result}] {nome[:70]}")

        db.commit()
    finally:
        db.close()

    print(f"\n  Resultado: {counts['created']} criadas | {counts['refreshed']} atualizadas | {counts['skipped']} ignoradas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
