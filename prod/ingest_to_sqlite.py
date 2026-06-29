#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest_to_sqlite.py — Efagundes Intelligence Engine | Ingestão histórica

Lê intel_output.json do ciclo atual (ou arquivos históricos) e popula o SQLite:
  - raw_items        ← itens individuais analisados
  - canonical_facts  ← fatos canônicos extraídos pelo analyzer
  - refs             ← referências/URLs coletadas
  - processed_files  ← controle de deduplicação de arquivos

Não reenvia arquivo já processado (checa processed_files por file_path + file_hash).

Uso:
    python ingest_to_sqlite.py
    python ingest_to_sqlite.py --input intel_output.json
    python ingest_to_sqlite.py --backfill          # processa todo o arquivo histórico
    python ingest_to_sqlite.py --db /outro/intel.sqlite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

ROOT_DIR    = Path(__file__).resolve().parent
ARQUIVO_DIR = ROOT_DIR / "arquivo"
DEFAULT_DB  = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"
DEFAULT_INPUT = ROOT_DIR / "intel_output.json"
BRASILIA = timezone(timedelta(hours=-3))

# Score de credibilidade por fonte conhecida
SOURCE_CREDIBILITY: dict[str, float] = {
    "ons": 0.95, "aneel": 0.95, "ccee": 0.95, "epe": 0.95, "mme": 0.95,
    "cmse": 0.90, "bndes": 0.90, "ibge": 0.90, "banco central": 0.90,
    "valor": 0.80, "valor econômico": 0.80, "folha": 0.78, "estadão": 0.78,
    "g1": 0.72, "uol": 0.65, "pv magazine": 0.82, "agência brasil": 0.85,
}

PERSISTENCE_BY_FACT_TYPE: dict[str, int] = {
    "percentual": 14, "monetário": 14, "monetario": 14,
    "capacidade": 30, "data": 7, "quantidade": 14,
    "tarifa": 30, "decisão regulatória": 30, "decisao regulatoria": 30,
    "evento judicial": 14, "evento geopolitico": 7, "evento geopolítico": 7,
}


def now_iso() -> str:
    return datetime.now(BRASILIA).isoformat(timespec="seconds")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def content_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def source_credibility(source: str) -> float:
    s = (source or "").lower()
    for key, score in SOURCE_CREDIBILITY.items():
        if key in s:
            return score
    return 0.60


def fact_persistence(fact_type: str) -> int:
    ft = (fact_type or "").lower()
    return PERSISTENCE_BY_FACT_TYPE.get(ft, 14)


def expires_from(days: int) -> str:
    return (datetime.now(BRASILIA) + timedelta(days=days)).strftime("%Y-%m-%d")


class IngestDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def is_file_processed(self, file_path: str, fhash: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM processed_files WHERE file_path = ? AND file_hash = ?",
            (file_path, fhash),
        ).fetchone()
        return row is not None

    def mark_file_processed(self, file_path: str, fhash: str, cycle_date: str) -> None:
        fid = str(uuid.uuid4())[:8]
        self.conn.execute(
            "INSERT OR REPLACE INTO processed_files (id, file_path, file_hash, cycle_date, status) VALUES (?,?,?,?,'ok')",
            (fid, file_path, fhash, cycle_date),
        )

    def item_exists(self, item_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM raw_items WHERE hash = ?", (item_hash,)
        ).fetchone()
        return row is not None

    def insert_item(self, item_id: str, cycle_date: str, source_file: str, item: dict[str, Any]) -> None:
        analise = item.get("analise") or {}
        title = (
            item.get("titulo_pt") or item.get("titulo") or
            analise.get("titulo_pt") or analise.get("titulo") or
            item.get("title") or ""
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO raw_items
              (id, cycle_date, source_file, title, source, url, description,
               theme, collected_at, published_at, score, hash, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item_id,
                cycle_date,
                source_file,
                title,
                item.get("fonte"),
                item.get("link"),
                item.get("descricao") or analise.get("resumo_executivo"),
                item.get("tema") or analise.get("tema_analisado"),
                item.get("coletado_em"),
                item.get("data"),
                float(item.get("score_final") or analise.get("score_final") or 0),
                item.get("hash"),
                json.dumps(item, ensure_ascii=False),
            ),
        )

    def insert_fact(self, fact: dict[str, Any], cycle_date: str, raw_item_id: str | None, source_file: str) -> None:
        val = str(fact.get("valor_literal") or fact.get("value") or "")
        ctx = str(fact.get("contexto") or fact.get("context") or "")
        ftype = str(fact.get("tipo") or fact.get("type") or "")
        conf = str(fact.get("confianca") or fact.get("confidence") or "media")
        pers = fact_persistence(ftype)

        fid = "fact_" + content_hash(f"{cycle_date}_{val}_{ctx}")[:12]
        self.conn.execute(
            """
            INSERT OR IGNORE INTO canonical_facts
              (id, raw_item_id, cycle_date, value_literal, fact_type, context,
               confidence, persistence_days, active, first_seen, last_seen, expires_at)
            VALUES (?,?,?,?,?,?,?,?,1,?,?,?)
            """,
            (fid, raw_item_id, cycle_date, val, ftype, ctx, conf, pers,
             cycle_date, cycle_date, expires_from(pers)),
        )

    def upsert_ref(self, item: dict[str, Any], raw_item_id: str, cycle_date: str) -> None:
        url = item.get("link") or ""
        if not url:
            return
        title = item.get("titulo_pt") or item.get("titulo") or item.get("title") or ""
        source = item.get("fonte") or ""
        pub_date = item.get("data") or cycle_date
        cred = source_credibility(source)
        analise = item.get("analise") or {}
        topic = item.get("tema") or analise.get("tema_analisado") or ""

        rid = "ref_" + content_hash(url)[:12]
        self.conn.execute(
            """
            INSERT OR IGNORE INTO refs
              (id, title, source, url, pub_date, topic, credibility_score, raw_item_id)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (rid, title, source, url, pub_date, topic, cred, raw_item_id),
        )

    def commit(self) -> None:
        self.conn.commit()


def derive_cycle_date(data: dict[str, Any], filepath: Path) -> str:
    cid = data.get("ciclo_id") or data.get("cycle") or ""
    if cid and len(cid) == 10:
        return cid
    # Derive from filename: 2026-06-11_09-05.json
    stem = filepath.stem
    if len(stem) >= 10:
        candidate = stem[:10]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
            return candidate
        except ValueError:
            pass
    return datetime.now(BRASILIA).strftime("%Y-%m-%d")


def ingest_file(db: IngestDB, filepath: Path, verbose: bool = True) -> tuple[int, int, int]:
    """Returns (items_new, facts_new, refs_new)."""
    fhash = file_hash(filepath)
    fpath_str = str(filepath)

    if db.is_file_processed(fpath_str, fhash):
        if verbose:
            print(f"  · Já processado: {filepath.name}")
        return 0, 0, 0

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  ✗ Erro ao ler {filepath.name}: {exc}")
        return 0, 0, 0

    cycle_date = derive_cycle_date(data, filepath)
    items_new = facts_new = refs_new = 0

    # Mapear sinais_ids do analyzer (índice numérico → hash do item)
    items_list = data.get("itens") or []
    idx_to_id: dict[int, str] = {}

    for idx, item in enumerate(items_list):
        if not isinstance(item, dict):
            continue
        item_hash = item.get("hash") or content_hash(json.dumps(item, sort_keys=True))[:16]
        if db.item_exists(item_hash):
            idx_to_id[idx] = "raw_" + item_hash[:12]
            continue
        item_id = "raw_" + item_hash[:12]
        idx_to_id[idx] = item_id
        db.insert_item(item_id, cycle_date, filepath.name, item)
        db.upsert_ref(item, item_id, cycle_date)
        items_new += 1
        refs_new += 1

    # Fatos canônicos do arquivo (podem vir de data.fatos_canonicos ou data.dashboard.fatos_canonicos)
    facts_raw = data.get("fatos_canonicos") or []
    if not facts_raw and isinstance(data.get("dashboard"), dict):
        facts_raw = data["dashboard"].get("fatos_canonicos") or []

    for fact in facts_raw:
        if not isinstance(fact, dict):
            continue
        sinal_idx = fact.get("sinal_id")
        raw_item_id = idx_to_id.get(sinal_idx) if isinstance(sinal_idx, int) else None
        db.insert_fact(fact, cycle_date, raw_item_id, filepath.name)
        facts_new += 1

    db.mark_file_processed(fpath_str, fhash, cycle_date)
    db.commit()

    if verbose:
        print(f"  ✓ {filepath.name} [{cycle_date}] → {items_new} itens, {facts_new} fatos, {refs_new} refs")
    return items_new, facts_new, refs_new


def collect_archive_files(archive_dir: Path | None = None) -> list[Path]:
    base = archive_dir if archive_dir else ARQUIVO_DIR
    files: list[Path] = []
    for year_dir in sorted(base.glob("20*")):
        for month_dir in sorted(year_dir.glob("*")):
            for f in sorted(month_dir.glob("????-??-??_??-??.json")):
                files.append(f)
    return files


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingere intel_output.json no SQLite.")
    p.add_argument("--input",       default=str(DEFAULT_INPUT), help="Arquivo intel_output.json (ciclo atual)")
    p.add_argument("--db",          default=str(DEFAULT_DB),    help="Caminho para o SQLite")
    p.add_argument("--backfill",    action="store_true",        help="Processar todo o arquivo histórico")
    p.add_argument("--archive-dir", default=None,               help="Diretório raiz do arquivo histórico (substitui o padrão)")
    p.add_argument("--verbose",     action="store_true", default=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"Banco não encontrado em {db_path}. Execute init_db.py primeiro.")
        return 1

    db = IngestDB(db_path)
    total_items = total_facts = total_refs = 0

    try:
        if args.backfill:
            archive_dir = Path(args.archive_dir) if args.archive_dir else None
            files = collect_archive_files(archive_dir)
            print(f"Backfill: {len(files)} arquivos históricos encontrados.")
            for f in files:
                i, fa, r = ingest_file(db, f, args.verbose)
                total_items += i; total_facts += fa; total_refs += r
        else:
            input_path = Path(args.input)
            if not input_path.is_absolute():
                input_path = ROOT_DIR / input_path
            if not input_path.exists():
                print(f"Arquivo não encontrado: {input_path}")
                return 1
            total_items, total_facts, total_refs = ingest_file(db, input_path, args.verbose)

    finally:
        db.close()

    print(f"\n  Total ingerido: {total_items} itens | {total_facts} fatos | {total_refs} refs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
