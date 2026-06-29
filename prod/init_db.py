#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_db.py — Efagundes Intelligence Engine | Inicialização do banco SQLite

Cria ~/efagundes_intel/db/intel.sqlite com todas as tabelas do sistema
de inteligência acumulativa. Seguro para rodar múltiplas vezes (IF NOT EXISTS).

Uso:
    python init_db.py
    python init_db.py --db /caminho/alternativo/intel.sqlite
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS processed_files (
        id          TEXT PRIMARY KEY,
        file_path   TEXT UNIQUE NOT NULL,
        file_hash   TEXT,
        cycle_date  TEXT,
        status      TEXT DEFAULT 'ok',
        processed_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_items (
        id           TEXT PRIMARY KEY,
        cycle_date   TEXT NOT NULL,
        source_file  TEXT,
        title        TEXT,
        source       TEXT,
        url          TEXT,
        description  TEXT,
        theme        TEXT,
        collected_at TEXT,
        published_at TEXT,
        score        REAL,
        hash         TEXT UNIQUE,
        raw_json     TEXT,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS canonical_facts (
        id               TEXT PRIMARY KEY,
        raw_item_id      TEXT,
        cycle_date       TEXT,
        value_literal    TEXT,
        fact_type        TEXT,
        context          TEXT,
        source_url       TEXT,
        source_name      TEXT,
        confidence       TEXT,
        persistence_days INTEGER DEFAULT 7,
        active           INTEGER DEFAULT 1,
        first_seen       TEXT,
        last_seen        TEXT,
        expires_at       TEXT,
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(raw_item_id) REFERENCES raw_items(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        entity_type     TEXT,
        aliases         TEXT,
        importance_score REAL DEFAULT 0.5,
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_entity_links (
        id          TEXT PRIMARY KEY,
        raw_item_id TEXT,
        entity_id   TEXT,
        relevance   REAL DEFAULT 0.5,
        FOREIGN KEY(raw_item_id) REFERENCES raw_items(id),
        FOREIGN KEY(entity_id)   REFERENCES entities(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS zettel_notes (
        id               TEXT PRIMARY KEY,
        title            TEXT,
        body             TEXT,
        cycle_date       TEXT,
        themes           TEXT,
        entities         TEXT,
        source_fact_ids  TEXT,
        source_urls      TEXT,
        interpretation   TEXT,
        supports         TEXT,
        contradicts      TEXT,
        persistence_days INTEGER DEFAULT 30,
        strength         REAL DEFAULT 0.5,
        status           TEXT DEFAULT 'active',
        first_seen       TEXT,
        last_seen        TEXT,
        expires_at       TEXT,
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategic_memory (
        id               TEXT PRIMARY KEY,
        title            TEXT,
        thesis           TEXT,
        themes           TEXT,
        supporting_facts TEXT,
        supporting_zettels TEXT,
        opposing_facts   TEXT,
        status           TEXT DEFAULT 'active',
        strength         REAL DEFAULT 0.5,
        persistence_days INTEGER DEFAULT 30,
        first_seen       TEXT,
        last_seen        TEXT,
        expires_at       TEXT,
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS thesis_evolution (
        id                  TEXT PRIMARY KEY,
        memory_id           TEXT,
        cycle_date          TEXT,
        previous_thesis     TEXT,
        new_thesis          TEXT,
        change_type         TEXT,
        reason              TEXT,
        supporting_fact_ids TEXT,
        created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(memory_id) REFERENCES strategic_memory(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contradictions (
        id          TEXT PRIMARY KEY,
        title       TEXT,
        signal_a    TEXT,
        signal_b    TEXT,
        memory_ids  TEXT,
        resolution  TEXT,
        priority    TEXT DEFAULT 'media',
        status      TEXT DEFAULT 'active',
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refs (
        id               TEXT PRIMARY KEY,
        title            TEXT,
        source           TEXT,
        url              TEXT,
        canonical_url    TEXT,
        canonical_url_status TEXT DEFAULT 'unresolved',
        pub_date         TEXT,
        topic            TEXT,
        credibility_score REAL DEFAULT 0.5,
        raw_item_id      TEXT,
        notes            TEXT,
        created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(raw_item_id) REFERENCES raw_items(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS briefing_cycles (
        id                  TEXT PRIMARY KEY,
        cycle_date          TEXT,
        title               TEXT,
        executive_thesis    TEXT,
        input_context_json  TEXT,
        output_markdown     TEXT,
        output_docx_path    TEXT,
        output_html_path    TEXT,
        model_used          TEXT,
        token_estimate      INTEGER,
        created_at          TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # ── Tabelas v31 — memória persistente de vetores e cenários (REQ-03 / REQ-04) ──
    """
    CREATE TABLE IF NOT EXISTS vetores_historico (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ciclo_id        TEXT NOT NULL,
        data_ciclo      DATE NOT NULL,
        vetor_nome      TEXT NOT NULL,
        ips             REAL NOT NULL,
        rank_no_ciclo   INTEGER,
        frente_xtech    TEXT,
        criado_em       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_vetores_historico_nome
        ON vetores_historico (vetor_nome, data_ciclo)
    """,
    """
    CREATE TABLE IF NOT EXISTS cenarios_historico (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        cenario_id               TEXT NOT NULL UNIQUE,
        frente_xtech             TEXT NOT NULL,
        descricao                TEXT NOT NULL,
        probabilidade_inicial    REAL,
        ciclos_em_monitoramento  INTEGER DEFAULT 1,
        indicadores_confirmados  INTEGER DEFAULT 0,
        indicadores_totais       INTEGER DEFAULT 0,
        status_confirmacao       TEXT DEFAULT 'projetado',
        primeira_projecao        DATE NOT NULL,
        ultima_atualizacao       DATE NOT NULL,
        criado_em                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS validacoes (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        ciclo_id_origem     TEXT NOT NULL,
        no_origem           TEXT NOT NULL,
        no_destino          TEXT NOT NULL,
        confirmado          BOOLEAN NOT NULL,
        ciclo_confirmacao   TEXT,
        observacao          TEXT,
        criado_em           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Índices para queries frequentes
    "CREATE INDEX IF NOT EXISTS idx_raw_items_cycle    ON raw_items(cycle_date)",
    "CREATE INDEX IF NOT EXISTS idx_raw_items_hash     ON raw_items(hash)",
    "CREATE INDEX IF NOT EXISTS idx_facts_cycle        ON canonical_facts(cycle_date)",
    "CREATE INDEX IF NOT EXISTS idx_facts_active       ON canonical_facts(active)",
    "CREATE INDEX IF NOT EXISTS idx_memory_status      ON strategic_memory(status)",
    "CREATE INDEX IF NOT EXISTS idx_memory_strength    ON strategic_memory(strength)",
    "CREATE INDEX IF NOT EXISTS idx_zettel_status      ON zettel_notes(status)",
    "CREATE INDEX IF NOT EXISTS idx_zettel_cycle       ON zettel_notes(cycle_date)",
    "CREATE INDEX IF NOT EXISTS idx_contradictions_st  ON contradictions(status)",
    "CREATE INDEX IF NOT EXISTS idx_refs_url           ON refs(url)",
]

# Entidades canônicas do domínio — semeadas na primeira execução
SEED_ENTITIES = [
    ("ent_ons",         "ONS",           "orgao_regulador",     "Operador Nacional do Sistema Elétrico", 0.95),
    ("ent_aneel",       "ANEEL",         "orgao_regulador",     "Agência Nacional de Energia Elétrica",  0.95),
    ("ent_ccee",        "CCEE",          "orgao_regulador",     "Câmara de Comercialização de Energia",  0.90),
    ("ent_epe",         "EPE",           "orgao_regulador",     "Empresa de Pesquisa Energética",        0.90),
    ("ent_mme",         "MME",           "orgao_governo",       "Ministério de Minas e Energia",         0.90),
    ("ent_cmse",        "CMSE",          "orgao_regulador",     None,                                    0.85),
    ("ent_bndes",       "BNDES",         "instituicao_financeira", None,                                 0.85),
    ("ent_petrobras",   "Petrobras",     "empresa",             None,                                    0.80),
    ("ent_pld",         "PLD",           "indicador",           "Preço de Liquidação das Diferenças",    0.85),
    ("ent_bess",        "BESS",          "tecnologia",          "Battery Energy Storage System",         0.80),
    ("ent_lrcap",       "LRCap",         "mecanismo_regulatorio", None,                                  0.80),
    ("ent_solar",       "energia solar", "tema",                "solar, fotovoltaico, GD",               0.75),
    ("ent_eolica",      "energia eólica","tema",                "eólica, wind",                          0.75),
    ("ent_data_centers","data centers",  "tema",                "datacenter, DC",                        0.75),
    ("ent_hidro",       "hidrogênio",    "tema",                "H2, hidrogênio verde",                  0.70),
    ("ent_el_nino",     "El Niño",       "fenomeno_climatico",  None,                                    0.70),
    ("ent_ormuz",       "Estreito de Ormuz","geopolitica",      None,                                    0.70),
]


def create_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        for stmt in DDL:
            conn.execute(stmt)
        _seed_entities(conn)
        conn.commit()
    finally:
        conn.close()


def _seed_entities(conn: sqlite3.Connection) -> None:
    for eid, name, etype, aliases, score in SEED_ENTITIES:
        conn.execute(
            """
            INSERT OR IGNORE INTO entities (id, name, entity_type, aliases, importance_score)
            VALUES (?, ?, ?, ?, ?)
            """,
            (eid, name, etype, aliases, score),
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inicializa o banco SQLite do sistema de inteligência.")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Caminho para o arquivo SQLite")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    print(f"Inicializando banco: {db_path}")
    create_schema(db_path)
    print(f"  ✓ Schema criado com sucesso — {db_path}")
    # Verificar tabelas criadas
    conn = sqlite3.connect(str(db_path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    conn.close()
    print(f"  ✓ Tabelas: {', '.join(tables)}")
    print(f"  ✓ Banco: {db_path.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
