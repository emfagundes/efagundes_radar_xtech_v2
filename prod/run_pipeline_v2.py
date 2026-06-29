#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_pipeline_v2.py — Efagundes Radar xTech | Pipeline Otimizado

Fluxo simplificado (engine core preservado):
  1.   Coleta       collector_v6.py
  1.5  Docs PDF     doc_collector_v1.py
  2.   Limpeza      cleaner_v2.py
  3.   Análise      analyzer_v33_agent.py  [+ critic_agent_v1 multi-LLM se disponível]
  4.   Arquivamento arquivar_intel.py
  5a.  SQLite       ingest_to_sqlite.py
  5b.  Zettelkasten update_zettels.py
  5c.  Memória      update_memory_v2.py
  5d.  Contradições detect_contradictions.py
  5.5  Hype Cycle        hype_cycle_updater.py
  5.6  Strategic Briefing strategic_briefing_v1.py  → strategic_briefing injeta em intel_output.json
  6.   Radar + Hero      gerar_radar_xtechs_v11.py  → radar-xtechs-{ciclo}.html + hero-{ciclo}.html + hero-nmentors-{ciclo}.html
  7.   Backup       backup_db.py

Outputs finais:
  outputs/radar/radar-xtechs-AAAA-MM-DD.html
  outputs/radar/hero-AAAA-MM-DD.html          ← embed em efagundes.com (Sala de Situação)
  outputs/radar/hero-nmentors-AAAA-MM-DD.html ← embed em nMentors.com.br (Briefing do Mentor)

Uso:
  python run_pipeline_v2.py
  python run_pipeline_v2.py --skip-coleta
  python run_pipeline_v2.py --skip-docs
  python run_pipeline_v2.py --only-docs
  python run_pipeline_v2.py --only-radar
  python run_pipeline_v2.py --only-memory
  python run_pipeline_v2.py --skip-memory
  python run_pipeline_v2.py --backfill
  python run_pipeline_v2.py --dry-run-memory
  python run_pipeline_v2.py --skip-critic    # desativa multi-LLM nesse ciclo
  python run_pipeline_v2.py --skip-market    # pula coleta de sinais quantitativos
  python run_pipeline_v2.py --only-market    # executa apenas market_collector
  python run_pipeline_v2.py --skip-briefing  # pula Strategic Briefing (sem pergunta estratégica)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError as exc:
    raise SystemExit("Dependência ausente: python-dotenv. pip install python-dotenv") from exc

load_dotenv()

# ─── Configuração ─────────────────────────────────────────────────────────────

BRASILIA  = timezone(timedelta(hours=-3))
SEP       = "=" * 66

ROOT_DIR    = Path(__file__).resolve().parent      # prod/
PROJECT_DIR = ROOT_DIR.parent                       # efagundes_radar_xtech/
OUTPUTS_RADAR = PROJECT_DIR / "outputs" / "radar"
DOCS_DIR    = PROJECT_DIR / "docs"
DEFAULT_DB  = PROJECT_DIR / "db" / "intel.sqlite"

FEED_BRUTO   = "feed_bruto.json"
FEED_LIMPO   = "feed_limpo.json"
INTEL_OUTPUT = "intel_output.json"

# Scripts
COLETOR               = "collector_v7.py"
DOC_COLETOR           = "doc_collector_v1.py"
LIMPADOR              = "cleaner_v2.py"
ANALISADOR            = "analyzer_v33_agent.py"
CRITIC_AGENT          = "critic_agent_v1.py"
MARKET_COLLECTOR      = "market_collector_v1.py"
MARKET_SIGNALS        = "market_signals.json"
ARQUIVADOR            = "arquivar_intel.py"
INIT_DB               = "init_db.py"
INGEST_SQLITE         = "ingest_to_sqlite.py"
UPDATE_ZETTELS        = "update_zettels.py"
UPDATE_MEMORY         = "update_memory_v2.py"
DETECT_CONTRADICTIONS = "detect_contradictions.py"
RADAR                 = "gerar_radar_xtechs_v11.py"
HYPE_CYCLE_UPDATER    = "hype_cycle_updater.py"
STRATEGIC_BRIEFING    = "strategic_briefing_v1.py"
BACKUP_DB             = "backup_db.py"


# ─── Utilitários ──────────────────────────────────────────────────────────────

def header(bloco: str, titulo: str) -> None:
    ts = datetime.now(BRASILIA).strftime("%d/%m/%Y %H:%M:%S")
    print(f"\n{SEP}\n  {bloco} — {titulo}\n  {ts}\n{SEP}")

def ok(msg: str)   -> None: print(f"  ✓ {msg}")
def warn(msg: str) -> None: print(f"  ⚠ {msg}")
def info(msg: str) -> None: print(f"  · {msg}")
def duracao(s: float) -> str: return f"{s:.1f}s"

def as_path(p: str | Path) -> Path:
    return ROOT_DIR / p

def existe(p: str | Path) -> bool:
    return Path(p).exists() if Path(p).is_absolute() else as_path(p).exists()

def exigir(caminho: str | Path) -> None:
    p = as_path(caminho)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo obrigatório não encontrado: {p}")

def has_flag(argv: Iterable[str], flag: str) -> bool:
    return flag in set(argv)

def carregar_modulo(script: str, nome: str):
    path = as_path(script)
    if not path.exists():
        raise FileNotFoundError(f"Script não encontrado: {path}")
    spec = importlib.util.spec_from_file_location(nome, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Não foi possível carregar: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def run_main(mod, argv: list[str]) -> None:
    if not hasattr(mod, "main"):
        raise AttributeError("Módulo não possui função main()")
    saved = sys.argv[:]
    try:
        sys.argv = argv
        result = mod.main()
        if isinstance(result, int) and result != 0:
            raise RuntimeError(f"main() retornou código {result}")
    finally:
        sys.argv = saved

def json_info(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return "[não gerado]"
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, list): return f"{len(obj)} registros"
        if isinstance(obj, dict): return f"{len(obj)} chaves"
    except Exception:
        pass
    return f"{p.stat().st_size / 1024:.1f} KB"

def file_kb(path: str | Path) -> str:
    p = Path(path)
    return f"{p.stat().st_size / 1024:.1f} KB" if p.exists() else "[não gerado]"


# ─── Etapas ───────────────────────────────────────────────────────────────────

def garantir_banco() -> None:
    if not DEFAULT_DB.exists():
        info(f"Banco não encontrado. Inicializando: {DEFAULT_DB}")
        DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
        mod = carregar_modulo(INIT_DB, "init_db_pipeline")
        run_main(mod, [INIT_DB, "--db", str(DEFAULT_DB)])


def etapa_coleta() -> None:
    header("1/7", f"Coleta de Sinais — {COLETOR}")
    mod = carregar_modulo(COLETOR, "collector_pipeline")
    t0 = time.time()
    if hasattr(mod, "coletar"):
        mod.coletar()
    else:
        run_main(mod, [COLETOR])
    ok(f"Coleta concluída em {duracao(time.time() - t0)}")


def etapa_doc_coleta() -> None:
    header("1.5/7", f"Ingestão de Documentos PDF — {DOC_COLETOR}")
    if not as_path(DOC_COLETOR).exists():
        warn(f"{DOC_COLETOR} não encontrado — etapa ignorada")
        return
    garantir_banco()
    mod = carregar_modulo(DOC_COLETOR, "doc_collector_pipeline")
    t0 = time.time()
    if hasattr(mod, "run_as_module"):
        result = mod.run_as_module(
            inbox=str(DOCS_DIR / "inbox"),
            feed=str(as_path(FEED_BRUTO)),
            db=str(DEFAULT_DB),
        )
        if isinstance(result, int) and result != 0:
            warn(f"doc_collector retornou código {result}")
    else:
        run_main(mod, [DOC_COLETOR,
                       "--inbox", str(DOCS_DIR / "inbox"),
                       "--feed",  FEED_BRUTO,
                       "--db",    str(DEFAULT_DB)])
    ok(f"Ingestão de docs concluída em {duracao(time.time() - t0)}")


def etapa_market_collector() -> None:
    header("1.6/7", f"Market & Macro Signals — {MARKET_COLLECTOR}")
    if not as_path(MARKET_COLLECTOR).exists():
        warn(f"{MARKET_COLLECTOR} não encontrado — etapa ignorada")
        return
    mod = carregar_modulo(MARKET_COLLECTOR, "market_collector_pipeline")
    t0 = time.time()
    run_main(mod, [MARKET_COLLECTOR, "--output",
                   str(as_path(MARKET_SIGNALS))])
    ok(f"Market signals coletados em {duracao(time.time() - t0)}")


def etapa_limpeza() -> None:
    header("2/7", f"Limpeza de Ruído — {LIMPADOR}")
    exigir(FEED_BRUTO)
    mod = carregar_modulo(LIMPADOR, "cleaner_pipeline")
    t0 = time.time()
    if hasattr(mod, "limpar"):
        mod.limpar()
    else:
        run_main(mod, [LIMPADOR])
    ok(f"Limpeza concluída em {duracao(time.time() - t0)}")


def etapa_analise(skip_critic: bool = False) -> None:
    header("3/7", f"Análise de IA — {ANALISADOR}")
    exigir(FEED_LIMPO)
    mod = carregar_modulo(ANALISADOR, "analyzer_pipeline")
    t0 = time.time()
    run_main(mod, [ANALISADOR])
    ok(f"Análise (Anthropic) concluída em {duracao(time.time() - t0)}")

    if skip_critic:
        info("--skip-critic: curadoria multi-LLM desativada")
        return

    critic_path = as_path(CRITIC_AGENT)
    if not critic_path.exists():
        info(f"{CRITIC_AGENT} não encontrado — curadoria multi-LLM ignorada")
        return

    header("3.5/7", f"Curadoria Multi-LLM — {CRITIC_AGENT}")
    t1 = time.time()
    try:
        mod_c = carregar_modulo(CRITIC_AGENT, "critic_agent_pipeline")
        run_main(mod_c, [CRITIC_AGENT, "--input", INTEL_OUTPUT])
        ok(f"Curadoria multi-LLM concluída em {duracao(time.time() - t1)}")
    except Exception as e:
        warn(f"Critic agent falhou (análise original mantida): {e}")


def etapa_arquivamento() -> None:
    header("4/7", f"Arquivamento — {ARQUIVADOR}")
    exigir(INTEL_OUTPUT)
    mod = carregar_modulo(ARQUIVADOR, "arquivar_pipeline")
    t0 = time.time()
    if hasattr(mod, "arquivar"):
        destino = mod.arquivar()
        ok(f"Arquivado em {duracao(time.time() - t0)}" + (f" → {destino}" if destino else ""))
    else:
        run_main(mod, [ARQUIVADOR])
        ok(f"Arquivamento concluído em {duracao(time.time() - t0)}")


def etapa_memoria(dry_run: bool = False, backfill: bool = False) -> None:
    garantir_banco()

    header("5a/7", f"Ingestão SQLite — {INGEST_SQLITE}")
    exigir(INTEL_OUTPUT)
    t0 = time.time()
    mod = carregar_modulo(INGEST_SQLITE, "ingest_pipeline")
    argv = [INGEST_SQLITE, "--input", INTEL_OUTPUT, "--db", str(DEFAULT_DB)]
    if backfill:
        argv.append("--backfill")
    run_main(mod, argv)
    ok(f"Ingestão SQLite concluída em {duracao(time.time() - t0)}")

    header("5b/7", f"Notas Zettelkasten — {UPDATE_ZETTELS}")
    t0 = time.time()
    mod = carregar_modulo(UPDATE_ZETTELS, "zettels_pipeline")
    argv = [UPDATE_ZETTELS, "--input", INTEL_OUTPUT, "--db", str(DEFAULT_DB)]
    if dry_run: argv.append("--dry-run")
    run_main(mod, argv)
    ok(f"Zettelkasten atualizado em {duracao(time.time() - t0)}")

    header("5c/7", f"Memória Estratégica — {UPDATE_MEMORY}")
    t0 = time.time()
    mod = carregar_modulo(UPDATE_MEMORY, "memory_pipeline")
    argv = [UPDATE_MEMORY, "--input", INTEL_OUTPUT, "--db", str(DEFAULT_DB)]
    if dry_run: argv.append("--dry-run")
    run_main(mod, argv)
    ok(f"Memória estratégica atualizada em {duracao(time.time() - t0)}")

    header("5d/7", f"Contradições — {DETECT_CONTRADICTIONS}")
    t0 = time.time()
    mod = carregar_modulo(DETECT_CONTRADICTIONS, "contradictions_pipeline")
    argv = [DETECT_CONTRADICTIONS, "--input", INTEL_OUTPUT, "--db", str(DEFAULT_DB)]
    if dry_run: argv.append("--dry-run")
    run_main(mod, argv)
    ok(f"Contradições detectadas em {duracao(time.time() - t0)}")


def etapa_hype_cycle() -> None:
    header("5.5/7", f"Hype Cycle Dinâmico — {HYPE_CYCLE_UPDATER}")
    exigir(INTEL_OUTPUT)
    if not as_path(HYPE_CYCLE_UPDATER).exists():
        warn(f"{HYPE_CYCLE_UPDATER} não encontrado — etapa ignorada")
        return
    mod = carregar_modulo(HYPE_CYCLE_UPDATER, "hype_cycle_pipeline")
    t0 = time.time()
    run_main(mod, [HYPE_CYCLE_UPDATER])
    ok(f"Hype Cycle atualizado em {duracao(time.time() - t0)}")


def etapa_strategic_briefing() -> None:
    header("5.6/7", f"Strategic Briefing — {STRATEGIC_BRIEFING}")
    exigir(INTEL_OUTPUT)
    if not as_path(STRATEGIC_BRIEFING).exists():
        warn(f"{STRATEGIC_BRIEFING} não encontrado — etapa ignorada (heroes sem pergunta estratégica)")
        return
    try:
        from strategic_briefing_v1 import gerar_strategic_briefing
        t0 = time.time()
        briefing = gerar_strategic_briefing(
            intel_path=as_path(INTEL_OUTPUT),
            market_path=as_path(MARKET_SIGNALS),
        )
        ok(f"Briefing gerado em {duracao(time.time() - t0)}")
        ok(f"→ {briefing.get('insight_executivo', '')[:80]}")
    except Exception as exc:
        warn(f"strategic_briefing_v1 falhou: {exc} — heroes serão gerados sem pergunta estratégica")


def etapa_radar() -> None:
    header("6/7", f"Radar xTech HTML + Hero — {RADAR}")
    exigir(INTEL_OUTPUT)
    OUTPUTS_RADAR.mkdir(parents=True, exist_ok=True)
    mod = carregar_modulo(RADAR, "radar_pipeline")
    t0 = time.time()
    run_main(mod, [RADAR, "--input", INTEL_OUTPUT,
                   "--output-dir", str(OUTPUTS_RADAR)])
    ok(f"Radar + Hero gerados em {duracao(time.time() - t0)}")


def etapa_backup() -> None:
    header("7/7", f"Backup SQLite — {BACKUP_DB}")
    if not DEFAULT_DB.exists():
        warn("Banco não encontrado — pulando backup")
        return
    mod = carregar_modulo(BACKUP_DB, "backup_pipeline")
    t0 = time.time()
    run_main(mod, [BACKUP_DB, "--db", str(DEFAULT_DB)])
    ok(f"Backup concluído em {duracao(time.time() - t0)}")


# ─── Resumo ───────────────────────────────────────────────────────────────────

def imprimir_resumo(t_total: float) -> None:
    ciclo = datetime.now(BRASILIA).strftime("%Y-%m-%d")
    print(f"\n{SEP}")
    print(f"  PIPELINE v2 CONCLUÍDO — {duracao(t_total)}")
    print(f"  {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"  {'─' * 62}")

    print("  Intermediários:")
    for label, path in [
        ("Feed bruto",      FEED_BRUTO),
        ("Feed limpo",      FEED_LIMPO),
        ("Intel output",    INTEL_OUTPUT),
        ("Market signals",  MARKET_SIGNALS),
    ]:
        p = as_path(path)
        status = "✓" if p.exists() else "✗"
        print(f"  {status} {label:<20} {json_info(p)}")

    print(f"  {'─' * 62}")
    print(f"  SQLite: {DEFAULT_DB} ({file_kb(DEFAULT_DB)})")
    print(f"  {'─' * 62}")
    print("  Outputs:")

    radares = sorted(OUTPUTS_RADAR.glob("radar-xtechs-*.html")) if OUTPUTS_RADAR.exists() else []
    if radares:
        p = radares[-1]
        print(f"  ✓ {'Radar xTechs':<24} {p.name} ({file_kb(p)})")
    else:
        print(f"  ✗ {'Radar xTechs':<24} [não gerado]")

    heroes_ef = sorted(OUTPUTS_RADAR.glob("hero-[0-9]*.html")) if OUTPUTS_RADAR.exists() else []
    if heroes_ef:
        p = heroes_ef[-1]
        print(f"  ✓ {'Hero efagundes.com':<24} {p.name} ({file_kb(p)})")
    else:
        print(f"  ✗ {'Hero efagundes.com':<24} [não gerado]")

    heroes_nm = sorted(OUTPUTS_RADAR.glob("hero-nmentors-*.html")) if OUTPUTS_RADAR.exists() else []
    if heroes_nm:
        p = heroes_nm[-1]
        print(f"  ✓ {'Hero nMentors':<24} {p.name} ({file_kb(p)})")
    else:
        print(f"  ✗ {'Hero nMentors':<24} [não gerado]")

    print(SEP + "\n")


# ─── Orquestrador ─────────────────────────────────────────────────────────────

def main() -> None:
    os.chdir(ROOT_DIR)
    t0   = time.time()
    argv = sys.argv[1:]

    skip_coleta    = has_flag(argv, "--skip-coleta")
    skip_docs      = has_flag(argv, "--skip-docs")
    skip_memory    = has_flag(argv, "--skip-memory")
    skip_critic    = has_flag(argv, "--skip-critic")
    skip_market    = has_flag(argv, "--skip-market")
    skip_briefing  = has_flag(argv, "--skip-briefing")
    only_docs    = has_flag(argv, "--only-docs")
    only_radar   = has_flag(argv, "--only-radar")
    only_memory  = has_flag(argv, "--only-memory")
    only_market  = has_flag(argv, "--only-market")
    backfill     = has_flag(argv, "--backfill")
    dry_run      = has_flag(argv, "--dry-run-memory")

    print(f"\n{SEP}")
    print("  EFAGUNDES RADAR xTech — Pipeline v2")
    print("  Coleta → Análise Multi-LLM → Radar HTML + Hero block")
    print(f"  SQLite: {DEFAULT_DB}")
    print(SEP)

    # Modos exclusivos
    if only_market:
        info("Modo --only-market: apenas Market & Macro Signals")
        try: etapa_market_collector()
        except Exception as e: warn(f"Market collector falhou: {e}")
        imprimir_resumo(time.time() - t0)
        return

    if only_docs:
        info("Modo --only-docs: apenas PDFs do inbox")
        try: etapa_doc_coleta()
        except Exception as e: warn(f"Doc coleta falhou: {e}")
        imprimir_resumo(time.time() - t0)
        return

    if only_memory:
        info("Modo --only-memory: camada de memória completa")
        try: etapa_memoria(dry_run=dry_run, backfill=backfill)
        except Exception as e: warn(f"Memória falhou: {e}")
        imprimir_resumo(time.time() - t0)
        return

    if only_radar:
        info("Modo --only-radar: Strategic Briefing + Radar xTech HTML + Heroes")
        if not skip_briefing:
            try: etapa_strategic_briefing()
            except Exception as e: warn(f"Strategic Briefing falhou: {e}")
        try: etapa_radar()
        except Exception as e: warn(f"Radar falhou: {e}")
        imprimir_resumo(time.time() - t0)
        return

    # Fluxo completo
    etapas: list[tuple] = []

    if skip_coleta:
        info("--skip-coleta: reutilizando feed_bruto.json existente")
    else:
        etapas.append((etapa_coleta, "Coleta"))

    if skip_docs:
        info("--skip-docs: ingestão de PDFs ignorada")
    else:
        etapas.append((etapa_doc_coleta, "Docs PDF"))

    if skip_market:
        info("--skip-market: Market & Macro Signals ignorados")
    else:
        etapas.append((etapa_market_collector, "Market & Macro Signals"))

    etapas += [
        (etapa_limpeza,      "Limpeza"),
        (lambda: etapa_analise(skip_critic=skip_critic), "Análise + Critic"),
        (etapa_arquivamento, "Arquivamento"),
    ]

    if skip_memory:
        info("--skip-memory: etapas de memória desativadas")
    else:
        etapas.append((lambda: etapa_memoria(dry_run=dry_run, backfill=backfill), "Memória"))

    etapas.append((etapa_hype_cycle, "Hype Cycle Dinâmico"))

    if skip_briefing:
        info("--skip-briefing: Strategic Briefing ignorado (heroes sem pergunta estratégica)")
    else:
        etapas.append((etapa_strategic_briefing, "Strategic Briefing + Pergunta do Mentor"))

    etapas.append((etapa_radar, "Radar xTech HTML + Heroes"))

    if not skip_memory:
        etapas.append((etapa_backup, "Backup"))

    for fn, label in etapas:
        try:
            fn()
        except Exception as exc:
            warn(f"{label} falhou: {exc}")

    imprimir_resumo(time.time() - t0)


if __name__ == "__main__":
    main()
