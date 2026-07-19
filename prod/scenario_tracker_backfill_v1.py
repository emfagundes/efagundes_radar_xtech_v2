#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario_tracker_backfill_v1.py — Backfill retroativo do Scenario Tracker

Script one-off (não faz parte do run_pipeline_v3.py). Varre os ciclos já
arquivados em arquivo/AAAA/MM/*.json, deduplica por ciclo_id (mantém o
arquivo mais recente quando há reprocessamento no mesmo dia) e reproduz,
em ordem cronológica, exatamente o que o estágio 5.7 do pipeline teria feito
a cada ciclo: persistir os snapshots do ciclo e avaliar os cenários abertos
de ciclos anteriores contra a evidência do ciclo corrente.

A regra anti-viés (scenario_tracker_v1.evaluate_open_scenarios) já garante,
por construção, que nenhum cenário é avaliado com evidência do próprio ciclo
em que foi emitido — processar os arquivos em ordem cronológica é suficiente
para o backfill respeitar a Seção 4 do spec sem lógica adicional.

Uso:
  python scenario_tracker_backfill_v1.py
  python scenario_tracker_backfill_v1.py --archive-dir ../arquivo --db ../db/intel.sqlite
  python scenario_tracker_backfill_v1.py --update-live-intel-output intel_output.json
  python scenario_tracker_backfill_v1.py --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import scenario_tracker_v1 as st
import scenario_calibration_v1 as calibration

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_ARCHIVE_DIR = ROOT_DIR.parent / "arquivo"
DEFAULT_DB = ROOT_DIR.parent / "db" / "intel.sqlite"


def descobrir_ciclos(archive_dir: Path) -> list[tuple[str, Path]]:
    """Retorna [(ciclo_id, path)] únicos por ciclo_id, ordenados
    cronologicamente. Quando há mais de um arquivo no mesmo dia (reprocesso),
    mantém o de nome lexicograficamente maior (HH-MM mais tarde = mais final)."""
    arquivos = sorted(archive_dir.glob("*/*/*.json"))
    por_ciclo: dict[str, Path] = {}
    for p in arquivos:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠ {p.name}: não foi possível ler ({e}) — pulando")
            continue
        ciclo_id = data.get("ciclo_id")
        if not ciclo_id:
            continue
        # último arquivo do dia (ordem lexicográfica de path == ordem temporal) vence
        por_ciclo[ciclo_id] = p
    return sorted(por_ciclo.items(), key=lambda kv: kv[0])


def rodar_backfill(archive_dir: Path, db_path: Path, dry_run: bool = False) -> dict:
    ciclos = descobrir_ciclos(archive_dir)
    print(f"  · {len(ciclos)} ciclo(s) únicos encontrados em {archive_dir}")
    if not ciclos:
        raise SystemExit("Nenhum ciclo arquivado encontrado — nada a fazer.")
    print(f"  · intervalo: {ciclos[0][0]} → {ciclos[-1][0]}")

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        st.ensure_schema(conn)
        if dry_run:
            print("  [dry-run] nenhuma escrita será feita")
            for ciclo_id, path in ciclos:
                data = json.loads(path.read_text(encoding="utf-8"))
                n_cen = len(data.get("cenarios_prospectivos") or [])
                print(f"    {ciclo_id}  ({path.name})  — {n_cen} cenário(s) prospectivo(s)")
            conn.close()
            return {}

        total_snap = total_eval = 0
        for ciclo_id, path in ciclos:
            data = json.loads(path.read_text(encoding="utf-8"))
            cenarios = data.get("cenarios_prospectivos") or []
            matriz   = data.get("matriz_incertezas") or {}
            vetores  = data.get("vetores_estrategicos") or []
            hero     = data.get("hero") or {}
            fatos    = data.get("fatos_canonicos") or []

            # Mesma regra do pipeline: a função de recalibração usada para os
            # cenários deste ciclo é sempre de um ciclo anterior (processados
            # em ordem cronológica, então "anterior" aqui é sempre um ciclo já
            # visto nesta mesma passada do backfill).
            funcao_recal = calibration.carregar_ultima_funcao_recalibracao(conn, ciclo_id)
            n_snap = st.persist_snapshots(conn, cenarios, matriz, ciclo_id,
                                           funcao_recalibracao=funcao_recal)
            n_eval = st.evaluate_open_scenarios(conn, ciclo_id, vetores, hero, fatos)
            calib = calibration.run_calibration_cycle(conn, ciclo_id)
            total_snap += n_snap
            total_eval += n_eval
            ece_str = f"{calib['ece']:.3f}" if calib["ece"] is not None else "n/d"
            print(f"    {ciclo_id}  +{n_snap} snapshot(s)  +{n_eval} avaliação(ões)  "
                  f"ECE={ece_str}")

        print(f"\n  ✓ Total: {total_snap} snapshots, {total_eval} avaliações "
              f"em {len(ciclos)} ciclos processados")
        resumo = st.build_scenario_tracking_summary(conn)
        resumo["auto_calibracao"] = calibration.build_auto_calibracao_summary(conn)
    finally:
        conn.close()

    return resumo


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill retroativo do Scenario Tracker.")
    p.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR))
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--update-live-intel-output", default=None,
                    help="Se informado, grava o resumo final em intel["
                         "'scenario_tracking'] deste arquivo intel_output.json.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    resumo = rodar_backfill(Path(args.archive_dir), Path(args.db), dry_run=args.dry_run)

    if resumo:
        print(f"\n  RESUMO (Seção 8)")
        print(f"  {'─' * 60}")
        print(f"  Total emitidos: {resumo['total_emitidos']}")
        print(f"  Por estado: {resumo['por_estado']}")
        print(f"  Dias médios de antecedência (confirmados): "
              f"{resumo['dias_medios_antecedencia_confirmados']}")
        print(f"  Calibração: {resumo['calibracao']}")
        print(f"  Não-materializados: {len(resumo['nao_materializados'])}")

        ac = resumo.get("auto_calibracao") or {}
        print(f"\n  AUTO-CALIBRAÇÃO (Seção 7 — {ac.get('n_ciclos_historico', 0)} ciclo(s) em calibration_history)")
        print(f"  {'─' * 60}")
        print(f"  ECE atual: {ac.get('ece_atual')}  (anterior: {ac.get('ece_anterior')})")
        print(f"  Separação atual: {ac.get('separacao_atual')}  (anterior: {ac.get('separacao_anterior')})")
        print(f"  {ac.get('ajuste_aplicado', '')}")

        if args.update_live_intel_output:
            intel_path = Path(args.update_live_intel_output)
            intel = json.loads(intel_path.read_text(encoding="utf-8"))
            intel["scenario_tracking"] = resumo
            intel_path.write_text(json.dumps(intel, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n  ✓ scenario_tracking gravado em {intel_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
