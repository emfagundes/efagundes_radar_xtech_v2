#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_memory_v2.py — Efagundes Intelligence Engine | Memória Persistente v31

Extensão do update_memory.py (v1) com suporte às tabelas v31:
  - vetores_historico: histórico de IPS por vetor a cada ciclo
  - cenarios_historico: acompanhamento de cenários com UPSERT

Lógica adicional:
  1. INSERT em vetores_historico para cada vetor do ciclo atual.
  2. UPSERT em cenarios_historico para cada cenário por frente xTech.
  3. Calcula ips_delta e semanas_consecutivas_alta via query no banco.
  4. Popula v31_horizonte1.vetores_com_tendencia no intel_output.json.

Executa a lógica do update_memory.py (v1) antes das novas etapas.

Uso:
    python update_memory_v2.py
    python update_memory_v2.py --input intel_output.json --cycle-date 2026-06-15
    python update_memory_v2.py --dry-run
    python update_memory_v2.py --skip-v1   # pula a lógica do update_memory.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

ROOT_DIR      = Path(__file__).resolve().parent
DEFAULT_DB    = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"
DEFAULT_INPUT = ROOT_DIR / "intel_output.json"
BRASILIA      = timezone(timedelta(hours=-3))


def _today() -> str:
    return datetime.now(BRASILIA).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(BRASILIA).isoformat(timespec="seconds")


# ─── Helpers de mapeamento xTech ──────────────────────────────────────────────

XTECH_KEYWORDS: dict[str, list[str]] = {
    "AgriTech":   ["agro", "agric", "safra", "soja", "milho", "rural", "lavoura"],
    "CleanTech":  ["solar", "eólica", "eolica", "hidrogênio", "carbono", "biocombustível", "biomassa"],
    "EnergyTech": ["energia", "transmissão", "ons", "aneel", "ccee", "bess", "grid", "geração", "elétrico"],
    "FinTech":    ["fintech", "banco", "crédito", "financi", "blockchain", "pagamento", "câmbio", "selic"],
    "DeepTech":   ["inteligência artificial", " ia ", "deeptech", "semicondutor", "chip",
                   "robô", "automação", "data center", "machine learning", "llm"],
}


def _vetor_frente(vetor: dict[str, Any]) -> str:
    """Infere frente xTech a partir do vetor (tipo, nome, setores)."""
    texto = " ".join([
        vetor.get("tipo") or "",
        vetor.get("nome") or "",
        " ".join((vetor.get("setores_afetados") or [])[:3]),
    ]).lower()
    for xtech, keywords in XTECH_KEYWORDS.items():
        if any(kw in texto for kw in keywords):
            return xtech
    return "DeepTech"


# ─── Inserção em vetores_historico ────────────────────────────────────────────

def inserir_vetores_historico(
    conn: sqlite3.Connection,
    vetores: list[dict[str, Any]],
    ciclo_id: str,
    data_ciclo: str,
    dry_run: bool,
) -> int:
    """Insere o snapshot de IPS de cada vetor para o ciclo atual.

    Idempotente: se (ciclo_id, vetor_nome) já existe, ignora.
    Retorna número de linhas inseridas.
    """
    inseridos = 0
    vetores_ordenados = sorted(
        vetores,
        key=lambda v: float(v.get("pressao_estrategica") or 0),
        reverse=True,
    )
    for rank, v in enumerate(vetores_ordenados, start=1):
        nome = (v.get("nome") or v.get("id") or "").strip()
        if not nome:
            continue
        ips = float(v.get("pressao_estrategica") or 0)
        frente = _vetor_frente(v)

        if dry_run:
            print(f"    [dry-run] vetores_historico INSERT {ciclo_id} | {nome[:50]} | IPS={ips} | rank={rank}")
            inseridos += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO vetores_historico
                    (ciclo_id, data_ciclo, vetor_nome, ips, rank_no_ciclo, frente_xtech)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ciclo_id, data_ciclo, nome, ips, rank, frente),
            )
            inseridos += conn.execute("SELECT changes()").fetchone()[0]
        except sqlite3.Error as exc:
            print(f"  ⚠ vetores_historico INSERT falhou [{nome[:40]}]: {exc}")

    return inseridos


# ─── UPSERT em cenarios_historico ─────────────────────────────────────────────

def upsert_cenarios_historico(
    conn: sqlite3.Connection,
    cenarios: dict[str, Any],
    ciclo_id: str,
    dry_run: bool,
) -> int:
    """UPSERT para cada cenário (por frente × tipo) no ciclo atual.

    cenarios é dict {frente: {tipo: {titulo, descricao, ...}}}.
    Retorna número de linhas afetadas.
    """
    afetados = 0
    data_hoje = _today()

    for frente, tipos in cenarios.items():
        if not isinstance(tipos, dict):
            continue
        for tipo, cen in tipos.items():
            if not isinstance(cen, dict):
                continue
            cenario_id = f"{frente}_{tipo}"
            descricao = (cen.get("descricao") or cen.get("titulo") or "")[:500]
            if not descricao:
                continue

            if dry_run:
                print(f"    [dry-run] cenarios_historico UPSERT {cenario_id}")
                afetados += 1
                continue

            try:
                existente = conn.execute(
                    "SELECT id, ciclos_em_monitoramento FROM cenarios_historico WHERE cenario_id=?",
                    (cenario_id,),
                ).fetchone()

                if existente:
                    ciclos = (existente[1] or 1) + 1
                    conn.execute(
                        """
                        UPDATE cenarios_historico
                           SET ciclos_em_monitoramento=?,
                               ultima_atualizacao=?,
                               descricao=?
                         WHERE cenario_id=?
                        """,
                        (ciclos, data_hoje, descricao, cenario_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO cenarios_historico
                            (cenario_id, frente_xtech, descricao,
                             primeira_projecao, ultima_atualizacao)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (cenario_id, frente, descricao, ciclo_id, data_hoje),
                    )
                afetados += 1
            except sqlite3.Error as exc:
                print(f"  ⚠ cenarios_historico UPSERT falhou [{cenario_id}]: {exc}")

    return afetados


# ─── Cálculo de tendência dos vetores ─────────────────────────────────────────

def calcular_tendencia_vetores(
    conn: sqlite3.Connection,
    vetores: list[dict[str, Any]],
    ciclo_id: str,
) -> list[dict[str, Any]]:
    """Para cada vetor, calcula ips_delta e semanas_consecutivas_alta.

    Retorna lista compatível com v31_horizonte1.vetores_com_tendencia.
    """
    resultado = []
    for v in vetores:
        nome = (v.get("nome") or v.get("id") or "").strip()
        if not nome:
            continue
        ips_atual = float(v.get("pressao_estrategica") or 0)

        # Ciclo imediatamente anterior
        try:
            row_ant = conn.execute(
                """
                SELECT ips FROM vetores_historico
                 WHERE vetor_nome=? AND ciclo_id != ?
                 ORDER BY data_ciclo DESC
                 LIMIT 1
                """,
                (nome, ciclo_id),
            ).fetchone()
            ips_anterior = float(row_ant[0]) if row_ant else None
        except sqlite3.Error:
            ips_anterior = None

        ips_delta = round(ips_atual - ips_anterior, 2) if ips_anterior is not None else None

        # Semanas consecutivas com delta positivo
        try:
            historico = conn.execute(
                """
                SELECT ips FROM vetores_historico
                 WHERE vetor_nome=?
                 ORDER BY data_ciclo DESC
                 LIMIT 10
                """,
                (nome,),
            ).fetchall()
            semanas_alta = 0
            ips_series = [float(r[0]) for r in historico]
            for i in range(len(ips_series) - 1):
                if ips_series[i] > ips_series[i + 1]:
                    semanas_alta += 1
                else:
                    break
        except sqlite3.Error:
            semanas_alta = 0

        if ips_delta is None:
            tendencia = "estavel"
        elif ips_delta > 0.1:
            tendencia = "crescente"
        elif ips_delta < -0.1:
            tendencia = "declinante"
        else:
            tendencia = "estavel"

        alerta = semanas_alta >= 3

        resultado.append({
            "vetor": nome,
            "ips_atual": round(ips_atual, 2),
            "ips_ciclo_anterior": round(ips_anterior, 2) if ips_anterior is not None else None,
            "ips_delta": ips_delta,
            "tendencia": tendencia,
            "semanas_consecutivas_alta": semanas_alta,
            "alerta_persistencia": alerta,
        })

    return sorted(resultado, key=lambda x: x["ips_atual"], reverse=True)


# ─── Lógica v1 (update_memory.py) ─────────────────────────────────────────────

def _rodar_update_memory_v1(args_input: str, args_db: str, args_cycle: str | None, dry_run: bool) -> None:
    """Executa update_memory.py (v1) via importação dinâmica."""
    v1_path = ROOT_DIR / "update_memory.py"
    if not v1_path.exists():
        print(f"  ⚠ update_memory.py não encontrado em {v1_path} — pulando lógica v1")
        return

    spec = importlib.util.spec_from_file_location("update_memory_v1", str(v1_path))
    if spec is None or spec.loader is None:
        print("  ⚠ Não foi possível carregar update_memory.py — pulando")
        return

    mod = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv[:]
    try:
        argv = ["update_memory.py", "--input", args_input, "--db", args_db]
        if args_cycle:
            argv += ["--cycle-date", args_cycle]
        if dry_run:
            argv.append("--dry-run")
        sys.argv = argv
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            mod.main()
    finally:
        sys.argv = saved_argv


# ─── main ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Memória persistente v31 — vetores_historico + cenarios_historico.")
    p.add_argument("--input",      default=str(DEFAULT_INPUT))
    p.add_argument("--db",         default=str(DEFAULT_DB))
    p.add_argument("--cycle-date", default=None)
    p.add_argument("--dry-run",    action="store_true")
    p.add_argument("--skip-v1",    action="store_true", help="Pula a lógica do update_memory.py (v1)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db_path    = Path(args.db)
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT_DIR / input_path

    if not db_path.exists():
        print(f"Banco não encontrado: {db_path}. Execute init_db.py primeiro.")
        return 1
    if not input_path.exists():
        print(f"Arquivo não encontrado: {input_path}")
        return 1

    data       = json.loads(input_path.read_text(encoding="utf-8"))
    ciclo_id   = args.cycle_date or data.get("ciclo_id") or _today()
    data_ciclo = ciclo_id[:10]  # garante formato DATE
    vetores    = [v for v in (data.get("vetores_estrategicos") or []) if isinstance(v, dict)]
    cenarios   = data.get("cenarios") or {}

    print(f"\n{'=' * 60}")
    print(f"  update_memory_v2.py | ciclo {ciclo_id}")
    print(f"  {len(vetores)} vetores · {sum(len(t) for t in cenarios.values() if isinstance(t, dict))} cenários")
    print(f"{'=' * 60}")

    # Passo 1 — lógica v1 (strategic_memory existente)
    if not args.skip_v1:
        print("\n  [Passo 1] Executando update_memory.py (v1)...")
        _rodar_update_memory_v1(str(input_path), str(db_path), args.cycle_date, args.dry_run)
    else:
        print("  [Passo 1] Pulado (--skip-v1)")

    # Passo 2 — vetores_historico
    print("\n  [Passo 2] vetores_historico — INSERT ciclo atual...")
    conn = sqlite3.connect(str(db_path))
    try:
        n_vet = inserir_vetores_historico(conn, vetores, ciclo_id, data_ciclo, args.dry_run)
        print(f"     OK — {n_vet} linha(s) inseridas em vetores_historico")

        # Passo 3 — cenarios_historico
        print("\n  [Passo 3] cenarios_historico — UPSERT ciclo atual...")
        n_cen = upsert_cenarios_historico(conn, cenarios, ciclo_id, args.dry_run)
        print(f"     OK — {n_cen} cenário(s) processados em cenarios_historico")

        if not args.dry_run:
            conn.commit()

        # Passo 4 — calcular tendência e popular v31_horizonte1 no JSON
        print("\n  [Passo 4] Calculando tendência de vetores...")
        tendencias = calcular_tendencia_vetores(conn, vetores, ciclo_id)
        alertas = [t for t in tendencias if t["alerta_persistencia"]]
        print(f"     {len(tendencias)} vetores analisados · {len(alertas)} alerta(s) de persistência")
        for t in alertas:
            print(f"     ↑ ALERTA: {t['vetor'][:50]} — {t['semanas_consecutivas_alta']} semanas consecutivas · delta={t['ips_delta']}")

    finally:
        conn.close()

    # Passo 5 — escrever v31_horizonte1.vetores_com_tendencia no intel_output.json
    if not args.dry_run and tendencias:
        print("\n  [Passo 5] Escrevendo v31_horizonte1.vetores_com_tendencia no intel_output.json...")
        data.setdefault("v31_horizonte1", {})["vetores_com_tendencia"] = tendencias
        input_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"     OK — {len(tendencias)} vetores com tendência salvos")
    elif args.dry_run:
        print("\n  [Passo 5] [dry-run] v31_horizonte1.vetores_com_tendencia não escrito")

    print(f"\n  ✓ update_memory_v2 concluído — ciclo {ciclo_id}")
    print(f"{'=' * 60}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
