#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doc_collector_v1.py — Efagundes Intelligence Engine | Ingestão de Documentos PDF

Processa PDFs depositados em efagundes_intel/docs/inbox/ e os converte em itens
compatíveis com o schema do collector_v6.py (feed_bruto.json), para que o restante
do pipeline (cleaner → analyzer → zettels → memory) os processe normalmente.

Fluxo:
  1. Varre docs/inbox/ em busca de *.pdf
  2. Para cada arquivo:
     a. Calcula SHA256 do conteúdo — pula se já presente em doc_sources
     b. Extrai texto com pdfplumber (página a página)
     c. Quebra em chunks semânticos (≤ MAX_CHUNK_CHARS, respeitando parágrafos)
     d. Cada chunk vira um item no schema de feed_bruto.json
     e. Move o PDF para docs/processed/ com prefixo de data
     f. Registra em doc_sources no SQLite
  3. Mescla os itens gerados em feed_bruto.json (append, sem sobrescrever)

Uso standalone:
    python doc_collector_v1.py
    python doc_collector_v1.py --inbox /caminho/alternativo
    python doc_collector_v1.py --dry-run   # não move arquivos nem grava no DB
    python doc_collector_v1.py --reprocess  # ignora hashes já vistos

Integração no pipeline (run_pipeline_v29.py):
    Etapa 1.5 — chamada após collector_v6 e antes de cleaner_v2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _import_pdfplumber():
    try:
        import pdfplumber as _pdfplumber
        return _pdfplumber
    except ImportError as exc:
        raise ImportError(
            "Dependência ausente: pdfplumber. Instale com: pip install pdfplumber"
        ) from exc

# ─── Caminhos ────────────────────────────────────────────────────────────────

ROOT_DIR      = Path(__file__).resolve().parent
INTEL_ROOT    = ROOT_DIR.parent
DOCS_ROOT     = INTEL_ROOT / "docs"
DEFAULT_INBOX = DOCS_ROOT / "inbox"
PROCESSED_DIR = DOCS_ROOT / "processed"
REJECTED_DIR  = DOCS_ROOT / "rejected"
DEFAULT_FEED  = ROOT_DIR / "feed_bruto.json"
DEFAULT_DB    = ROOT_DIR.parent / "db" / "intel.sqlite"

# ─── Parâmetros de chunking ──────────────────────────────────────────────────

MAX_CHUNK_CHARS = 1_100   # tamanho máximo de cada chunk
MIN_CHUNK_CHARS = 120     # chunks menores que isso são descartados (cabeçalhos, rodapés)
OVERLAP_CHARS   = 80      # sobreposição entre chunks consecutivos

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _md5_item(titulo: str, link: str) -> str:
    return hashlib.md5(f"{titulo.lower().strip()}{link}".encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ─── Extração de texto do PDF ────────────────────────────────────────────────

def extrair_texto_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    """Extrai texto completo e metadata de um PDF.

    Retorna (texto_completo, metadata_dict).
    """
    texto_paginas: list[str] = []
    meta: dict[str, Any] = {}

    pdfplumber = _import_pdfplumber()
    with pdfplumber.open(str(path)) as pdf:
        # Metadata do arquivo
        raw_meta = pdf.metadata or {}
        meta = {
            "titulo_doc": (raw_meta.get("Title") or raw_meta.get("title") or path.stem).strip(),
            "autor":      (raw_meta.get("Author") or raw_meta.get("author") or "").strip(),
            "criado_em":  (raw_meta.get("CreationDate") or "").strip(),
            "n_paginas":  len(pdf.pages),
        }

        for i, page in enumerate(pdf.pages, start=1):
            t = (page.extract_text() or "").strip()
            if t:
                texto_paginas.append(f"[p.{i}] {t}")

    texto_completo = "\n\n".join(texto_paginas)
    return texto_completo, meta


# ─── Chunking semântico ──────────────────────────────────────────────────────

def _split_chunks(texto: str) -> list[str]:
    """Divide o texto em chunks respeitando quebras de parágrafo."""
    # Divide por parágrafos (dupla quebra de linha)
    paragrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]

    chunks: list[str] = []
    buffer = ""

    for para in paragrafos:
        if len(buffer) + len(para) + 2 <= MAX_CHUNK_CHARS:
            buffer = f"{buffer}\n\n{para}".strip()
        else:
            if buffer and len(buffer) >= MIN_CHUNK_CHARS:
                chunks.append(buffer)
                # overlap: últimos OVERLAP_CHARS do buffer anterior
                buffer = buffer[-OVERLAP_CHARS:] + "\n\n" + para
            else:
                # parágrafo único muito longo — quebra na força
                buffer = para

    if buffer and len(buffer) >= MIN_CHUNK_CHARS:
        chunks.append(buffer)

    return chunks


# ─── Conversão chunk → item (schema feed_bruto.json) ────────────────────────

def chunk_para_item(
    chunk: str,
    idx: int,
    meta: dict[str, Any],
    pdf_path: Path,
    tema: str,
) -> dict[str, Any]:
    """Converte um chunk de texto em item compatível com feed_bruto.json."""
    titulo_doc = meta.get("titulo_doc") or pdf_path.stem
    autor      = meta.get("autor") or "Documento externo"
    n_pag      = meta.get("n_paginas") or "?"

    titulo = f"{titulo_doc} [{idx+1}/{meta.get('_n_chunks', '?')}]"
    # Primeira linha do chunk como descrição curta
    primeira_linha = chunk.split("\n")[0][:200]

    item = {
        "hash":       _md5_item(titulo, str(pdf_path)),
        "titulo":     titulo,
        "titulo_pt":  titulo,
        "link":       str(pdf_path),
        "descricao":  primeira_linha,
        "body":       chunk,               # texto completo do chunk
        "fonte":      autor,
        "tema":       tema,
        "peso":       2,
        "tipo_fonte": "documento",         # distingue de rss/scraping
        "data":       _today(),
        "coletado_em": _now_iso(),
        # campos extras de rastreabilidade documental
        "doc_source_type": "pdf",
        "doc_titulo":      titulo_doc,
        "doc_autor":       autor,
        "doc_n_paginas":   n_pag,
        "doc_arquivo":     pdf_path.name,
    }
    return item


# ─── SQLite — tabela doc_sources ─────────────────────────────────────────────

DDL_DOC_SOURCES = """
CREATE TABLE IF NOT EXISTS doc_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    content_hash    TEXT NOT NULL UNIQUE,
    titulo_doc      TEXT,
    autor           TEXT,
    n_paginas       INTEGER,
    n_chunks        INTEGER DEFAULT 0,
    tema            TEXT,
    ciclo_id        TEXT,
    status          TEXT DEFAULT 'ok',
    processado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _ensure_doc_sources(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_DOC_SOURCES)
    conn.commit()


def _ja_processado(conn: sqlite3.Connection, sha: str) -> bool:
    row = conn.execute(
        "SELECT id FROM doc_sources WHERE content_hash=?", (sha,)
    ).fetchone()
    return row is not None


def _registrar(
    conn: sqlite3.Connection,
    path: Path,
    sha: str,
    meta: dict[str, Any],
    n_chunks: int,
    tema: str,
    status: str = "ok",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO doc_sources
            (filename, content_hash, titulo_doc, autor, n_paginas, n_chunks, tema, ciclo_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            path.name,
            sha,
            meta.get("titulo_doc") or path.stem,
            meta.get("autor") or "",
            meta.get("n_paginas") or 0,
            n_chunks,
            tema,
            _today(),
            status,
        ),
    )
    conn.commit()


# ─── Inferência de tema ───────────────────────────────────────────────────────

_TEMA_KEYWORDS: dict[str, list[str]] = {
    "Energia":         ["energia", "solar", "fotovoltaic", "eólica", "hidrelétric", "bess", "transmissão", "geração"],
    "IA & DeepTech":   ["inteligência artificial", "machine learning", "llm", "automação", "scada", "digital"],
    "FinTech":         ["fintech", "financ", "crédito", "blockchain", "pagamento", "investimento"],
    "CleanTech":       ["hidrogênio", "carbono", "sustentabilid", "descarboniz", "eficiência energética"],
    "AgriTech":        ["agro", "safra", "rural", "irrigação", "agricultura"],
    "Regulação":       ["aneel", "ons", "ccee", "regulação", "regulatório", "p&d", "anatel", "cvm"],
    "Ecossistemas":    ["ecosystem", "ecossistema", "plataforma", "orchestrator", "data sharing"],
    "Cases & Mercado": ["estudo de caso", "case study", "resultado", "cliente", "parceria"],
}

def _inferir_tema(texto: str, filename: str) -> str:
    corpus = (texto[:2000] + " " + filename).lower()
    scores: dict[str, int] = {}
    for tema, keywords in _TEMA_KEYWORDS.items():
        scores[tema] = sum(1 for kw in keywords if kw in corpus)
    melhor = max(scores, key=lambda t: scores[t])
    return melhor if scores[melhor] > 0 else "Cases & Mercado"


# ─── Processamento de um PDF ─────────────────────────────────────────────────

def processar_pdf(
    path: Path,
    conn: sqlite3.Connection,
    dry_run: bool = False,
    reprocess: bool = False,
) -> list[dict[str, Any]]:
    """Processa um PDF e retorna lista de itens.

    Retorna lista vazia se o PDF já foi processado (e reprocess=False).
    """
    sha = _sha256(path)

    if not reprocess and _ja_processado(conn, sha):
        print(f"  ↷ já processado — pulando: {path.name}")
        return []

    print(f"  → processando: {path.name}")

    try:
        texto, meta = extrair_texto_pdf(path)
    except Exception as exc:
        print(f"  ✗ erro ao extrair texto: {exc}")
        if not dry_run:
            _registrar(conn, path, sha, {}, 0, "desconhecido", status="erro")
            shutil.move(str(path), str(REJECTED_DIR / path.name))
        return []

    if not texto.strip():
        print(f"  ✗ PDF sem texto extraível (pode ser imagem escaneada): {path.name}")
        if not dry_run:
            _registrar(conn, path, sha, meta, 0, "desconhecido", status="sem_texto")
            shutil.move(str(path), str(REJECTED_DIR / path.name))
        return []

    tema = _inferir_tema(texto, path.name)
    chunks = _split_chunks(texto)
    meta["_n_chunks"] = len(chunks)

    itens = [chunk_para_item(c, i, meta, path, tema) for i, c in enumerate(chunks)]

    print(f"     {meta['n_paginas']} páginas · {len(chunks)} chunks · tema: {tema}")

    if not dry_run:
        # Move para processed/ com prefixo de data
        dest_name = f"{_today()}_{path.name}"
        dest = PROCESSED_DIR / dest_name
        # Se já existe dest com mesmo nome, adiciona sufixo
        if dest.exists():
            dest = PROCESSED_DIR / f"{_today()}_{sha[:8]}_{path.name}"
        shutil.move(str(path), str(dest))
        _registrar(conn, path, sha, meta, len(chunks), tema)
        print(f"     ✓ movido para processed/{dest.name}")

    return itens


# ─── Merge com feed_bruto.json ───────────────────────────────────────────────

def mesclar_feed(novos_itens: list[dict[str, Any]], feed_path: Path) -> int:
    """Acrescenta novos_itens ao feed_bruto.json existente.

    Retorna número de itens acrescentados (sem duplicatas por hash).
    """
    existentes: list[dict[str, Any]] = []
    if feed_path.exists():
        try:
            existentes = json.loads(feed_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existentes = []

    hashes_existentes = {item.get("hash") for item in existentes}
    unicos = [it for it in novos_itens if it.get("hash") not in hashes_existentes]

    if unicos:
        combinado = existentes + unicos
        feed_path.write_text(
            json.dumps(combinado, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return len(unicos)


# ─── main ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingestão de PDFs em efagundes_intel/docs/inbox/")
    p.add_argument("--inbox",     default=str(DEFAULT_INBOX), help="Pasta de entrada dos PDFs")
    p.add_argument("--feed",      default=str(DEFAULT_FEED),  help="Arquivo feed_bruto.json de saída")
    p.add_argument("--db",        default=str(DEFAULT_DB),    help="Banco SQLite")
    p.add_argument("--dry-run",   action="store_true", help="Não move arquivos nem grava no DB")
    p.add_argument("--reprocess", action="store_true", help="Reprocessa PDFs já vistos")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    inbox_dir = Path(args.inbox)
    feed_path = Path(args.feed)
    db_path   = Path(args.db)

    # Garante estrutura de pastas
    for d in [inbox_dir, PROCESSED_DIR, REJECTED_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(inbox_dir.glob("*.pdf"))
    if not pdfs:
        print("doc_collector: nenhum PDF encontrado em", inbox_dir)
        return 0

    print(f"\n{'='*60}")
    print(f"  doc_collector_v1 | {len(pdfs)} PDF(s) em {inbox_dir.name}/")
    print(f"{'='*60}")

    if not db_path.exists():
        print(f"  ⚠ banco não encontrado: {db_path}")
        print("  Execute init_db.py primeiro.")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _ensure_doc_sources(conn)

    todos_itens: list[dict[str, Any]] = []
    for pdf in pdfs:
        itens = processar_pdf(pdf, conn, dry_run=args.dry_run, reprocess=args.reprocess)
        todos_itens.extend(itens)

    conn.close()

    if not todos_itens:
        print("\n  Nenhum item novo gerado.")
        return 0

    if args.dry_run:
        print(f"\n  [dry-run] {len(todos_itens)} item(s) gerados — não gravados no feed")
    else:
        n = mesclar_feed(todos_itens, feed_path)
        print(f"\n  ✓ {n} item(s) adicionados a {feed_path.name}")

    print(f"{'='*60}\n")
    return 0


# Ponto de entrada para chamada como módulo pelo run_pipeline
def run_as_module(inbox: str | None = None, feed: str | None = None, db: str | None = None) -> int:
    """Chamada direta pelo run_pipeline sem subprocess."""
    saved = sys.argv[:]
    argv = ["doc_collector_v1.py"]
    if inbox:
        argv += ["--inbox", inbox]
    if feed:
        argv += ["--feed", feed]
    if db:
        argv += ["--db", db]
    sys.argv = argv
    try:
        return main()
    finally:
        sys.argv = saved


if __name__ == "__main__":
    raise SystemExit(main())
