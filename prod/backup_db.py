#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup_db.py — Efagundes Intelligence Engine | Backup do SQLite para Google Drive

Copia intel.sqlite para o Drive com timestamp e aplica política de retenção:
  - Backups diários: mantém 30 dias
  - Backups semanais: mantém 52 semanas (domingo de cada semana)
  - Backups mensais: mantém permanentemente (1º do mês)

Também faz backup das notas Zettelkasten em Markdown (se existirem).

Uso:
    python backup_db.py
    python backup_db.py --db /outro/intel.sqlite
    python backup_db.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

ROOT_DIR = Path(__file__).resolve().parent
BRASILIA = timezone(timedelta(hours=-3))

DEFAULT_DB = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"

DRIVE_BASE = (
    Path.home()
    / "Library" / "CloudStorage"
    / "GoogleDrive-eduardo.mfagundes@gmail.com"
    / "Meu Drive"
    / "efagundes_intel"
)
BACKUP_SQLITE_DIR  = DRIVE_BASE / "backups" / "sqlite"
BACKUP_ZETTEL_DIR  = DRIVE_BASE / "backups" / "zettelkasten"

RETENTION_DAILY_DAYS    = 30
RETENTION_WEEKLY_WEEKS  = 52


class BackupResult(NamedTuple):
    dest: Path
    size_kb: float
    is_new: bool


def now_br() -> datetime:
    return datetime.now(BRASILIA)


def backup_filename(dt: datetime) -> str:
    return f"intel_{dt.strftime('%Y%m%d_%H%M')}.sqlite"


def is_weekly(dt: datetime) -> bool:
    return dt.weekday() == 6  # domingo


def is_monthly(dt: datetime) -> bool:
    return dt.day == 1


def copy_db(src: Path, dest: Path, dry_run: bool = False) -> BackupResult:
    if dry_run:
        print(f"  [dry-run] Copiaria {src} → {dest}")
        return BackupResult(dest, 0.0, True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return BackupResult(dest, dest.stat().st_size / 1024, False)
    shutil.copy2(str(src), str(dest))
    return BackupResult(dest, dest.stat().st_size / 1024, True)


def apply_retention(backup_dir: Path, dry_run: bool = False) -> int:
    """Remove backups diários além da janela de retenção. Preserva semanais e mensais."""
    removed = 0
    cutoff_daily = now_br() - timedelta(days=RETENTION_DAILY_DAYS)

    for f in sorted(backup_dir.glob("intel_????????_????.sqlite")):
        stem = f.stem  # intel_20260611_0900
        try:
            dt = datetime.strptime(stem, "intel_%Y%m%d_%H%M")
        except ValueError:
            continue

        # Manter se for mensal (dia 1) ou semanal (domingo) dentro da janela semanal
        dt_naive = dt.replace(tzinfo=None)
        cutoff_naive = cutoff_daily.replace(tzinfo=None)
        if dt.day == 1:
            continue  # Mensal — nunca remover
        weeks_ago = (now_br().replace(tzinfo=None) - dt_naive).days // 7
        if dt.weekday() == 6 and weeks_ago <= RETENTION_WEEKLY_WEEKS:
            continue  # Semanal dentro da janela — manter

        if dt_naive < cutoff_naive:
            if dry_run:
                print(f"  [dry-run] Removeria: {f.name}")
            else:
                f.unlink()
            removed += 1

    return removed


def export_zettels_markdown(db_path: Path, dest_dir: Path, dry_run: bool = False) -> int:
    """Exporta notas Zettelkasten ativas como arquivos Markdown."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    notes = conn.execute(
        "SELECT * FROM zettel_notes WHERE status='active' ORDER BY cycle_date DESC"
    ).fetchall()
    conn.close()

    if not notes:
        return 0

    today_str = now_br().strftime("%Y-%m-%d")
    day_dir   = dest_dir / today_str

    if dry_run:
        print(f"  [dry-run] Exportaria {len(notes)} notas Zettelkasten para {day_dir}")
        return len(notes)

    day_dir.mkdir(parents=True, exist_ok=True)
    for note in notes:
        fname = f"{note['id']}.md"
        content = f"""---
id: {note['id']}
title: {note['title'] or ''}
cycle_date: {note['cycle_date'] or ''}
themes: {note['themes'] or ''}
entities: {note['entities'] or ''}
strength: {note['strength'] or 0}
persistence_days: {note['persistence_days'] or 0}
status: {note['status'] or 'active'}
first_seen: {note['first_seen'] or ''}
last_seen: {note['last_seen'] or ''}
expires_at: {note['expires_at'] or ''}
---

# {note['title'] or ''}

{note['body'] or ''}

## Interpretação

{note['interpretation'] or ''}

## Reforça

{note['supports'] or '_Nenhuma tese registrada._'}

## Contradiz

{note['contradicts'] or '_Nenhuma contradição registrada._'}
"""
        (day_dir / fname).write_text(content, encoding="utf-8")

    return len(notes)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backup do SQLite e notas Zettelkasten para Google Drive.")
    p.add_argument("--db",      default=str(DEFAULT_DB))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args    = parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"Banco não encontrado: {db_path}")
        return 1

    if not DRIVE_BASE.exists():
        print(f"Diretório Drive não encontrado: {DRIVE_BASE}")
        print("  Verifique se o Google Drive está montado e o caminho está correto.")
        return 1

    dt = now_br()
    dest_file = BACKUP_SQLITE_DIR / backup_filename(dt)

    # Cópia principal
    result = copy_db(db_path, dest_file, args.dry_run)
    if result.is_new:
        print(f"  ✓ Backup criado: {dest_file.name} ({result.size_kb:.1f} KB)")
    else:
        print(f"  · Backup já existe: {dest_file.name}")

    # Aplicar retenção
    removed = apply_retention(BACKUP_SQLITE_DIR, args.dry_run)
    if removed:
        print(f"  · {removed} backups antigos removidos pela política de retenção")

    # Export Zettelkasten
    n_zettels = export_zettels_markdown(db_path, BACKUP_ZETTEL_DIR, args.dry_run)
    if n_zettels:
        today_str = dt.strftime("%Y-%m-%d")
        print(f"  ✓ {n_zettels} notas Zettelkasten exportadas → {BACKUP_ZETTEL_DIR / today_str}")

    # Sumário
    if not args.dry_run:
        backups = list(BACKUP_SQLITE_DIR.glob("intel_*.sqlite"))
        print(f"  · Total de backups no Drive: {len(backups)}")
        total_mb = sum(f.stat().st_size for f in backups) / (1024 * 1024)
        print(f"  · Espaço total: {total_mb:.1f} MB")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
