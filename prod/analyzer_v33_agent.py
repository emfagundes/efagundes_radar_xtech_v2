#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyzer_v33_agent.py — Efagundes Intelligence Engine | Radar xTech 1.2

v33_agent (sobre v32_agent):
  Corrige DEFAULT_DB para caminho relativo ao container efagundes_radar_xtech/.
  Elimina dependência de ~/efagundes_intel/db/ — banco agora resolvido como
  Path(__file__).parent.parent / "db" / "intel.sqlite".
  Isso restaura hist_data (memórias + zettels + entidades) no Grafo de Inteligência.

v32_agent (sobre v31_agent):
  Corrige contagem de sinais e fatos por entidade em hist_data.entities.

  Problema resolvido:
    A query anterior só trazia id/name/entity_type/importance_score — sem sinais nem
    fatos — porque não havia JOIN com raw_items/canonical_facts via signal_entity_links
    (que pode estar despovoada). Resultado: BNDES, ONS, ANEEL etc. apareciam com
    sinais=None e fatos=None no Grafo de Inteligência.

  Solução (v32):
    Subquery de contagem por busca textual no título/descrição de raw_items e no
    value_literal de canonical_facts. Independe de signal_entity_links estar populada.
    Também adiciona aliases da tabela entities para ampliar o match
    (ex.: "BNDES" captura "Banco Nacional de Desenvolvimento").

  Todos os blocos da Fase 6.5 e 6.6 (v31_agent) são preservados sem alteração.

  Pipeline:
    Passo A — Roda analyzer_v30.py (análise tática + vetores + briefing)
    Passo B — Fase 6.5: Enriquecimento Radar xTech (herdado de v30_agent)
      6.5.1  hero
      6.5.2  impacto_xtech
      6.5.3  graph_anchors
      6.5.4  fatos_duros
      6.5.5  cenarios xTech
      6.5.6  convergencia
      6.5.7  cta
      6.5.8  score_badge
      6.5.9  aplicacoes_corporativas
      6.5.10 lente_decisao
      6.5.11 snapshot histórico
      6.5.13 MICMAC — variáveis estruturais + quadrantes Godet (micmac_mactor_v1)
      6.5.14 MACTOR — posicionamento de atores por cenário (micmac_mactor_v1)
    Passo C — Fase 6.6: Enriquecimento Horizonte 1 [NOVO]
      6.6.1  sala_situacao_com_acao  (Sonnet × 1 — bloco SCR)

Schema de saída: "v33-xtech-horizons"
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from micmac_mactor_v1 import gerar_micmac, gerar_mactor

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    override=True,
)

OUTPUT_FILE = "intel_output.json"
HOJE_ISO = datetime.now(timezone.utc).strftime("%Y-%m-%d")
DEFAULT_DB = Path(__file__).resolve().parent.parent / "db" / "intel.sqlite"

MODELO_ENRIQUECIMENTO = "claude-sonnet-4-6"

XTECHS = ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]

# ── Mapeamento xTech ──────────────────────────────────────────────────────────

XTECH_KEYWORDS: dict[str, list[str]] = {
    "AgriTech":   ["agro", "agric", "safra", "soja", "milho", "pecuária", "irrigação", "agropecuária", "colheita", "rural", "fazenda", "lavoura"],
    "CleanTech":  ["solar", "eólica", "eolica", "hidrogênio", "hidrogênio verde", "cleantech",
                   "carbono", "emissão", "descarbonização", "biocombustível", "biomassa", "offshore"],
    "EnergyTech": ["energia", "transmissão", "ons", "aneel", "ccee", "bess", "grid", "tarifa",
                   "geração", "distribuição", "elétrico", "mre", "mcsd", "usina"],
    "FinTech":    ["fintech", "banco", "crédito", "financi", "bolsa", "mercado financeiro",
                   "tokeniz", "crypto", "blockchain", "pagamento", "câmbio", "selic", "ipca"],
    "DeepTech":   ["inteligência artificial", " ia ", "deeptech", "semicondutor", "chip",
                   "quantum", "robô", "automação", "data center", "machine learning", "llm",
                   "biotecnologia", "nanotecnologia", "computação"],
}

SETOR_TO_XTECH: dict[str, str] = {
    "energia":        "EnergyTech",
    "infraestrutura": "EnergyTech",
    "regulacao":      "EnergyTech",
    "ia_automacao":   "DeepTech",
    "tecnologias":    "DeepTech",
    "ciencia":        "DeepTech",
    "financiamento":  "FinTech",
    "negocios":       "FinTech",
    "macroeconomia":  "FinTech",
    "outros":         "DeepTech",
}

MATERIALIDADE_LABELS: dict[str, str] = {
    "regulatorio":     "Regulatório",
    "competitividade": "Competitividade",
    "capex":           "CAPEX",
    "reputacional":    "Reputacional",
    "opex":            "OPEX",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Utilitários
# ═══════════════════════════════════════════════════════════════════════════════

def _setor_to_xtech(setor: str, titulo: str = "", tema: str = "") -> str:
    texto = f"{setor} {titulo} {tema}".lower()
    for xtech, keywords in XTECH_KEYWORDS.items():
        if any(kw in texto for kw in keywords):
            return xtech
    return SETOR_TO_XTECH.get(setor, "DeepTech")


def _query_sqlite(db_path: Path, query: str, params: tuple = ()) -> list[tuple]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def _chamar_sonnet(client: anthropic.Anthropic, system: str, user: str, max_tokens: int = 2048) -> str:
    resp = client.messages.create(
        model=MODELO_ENRIQUECIMENTO,
        max_tokens=max_tokens,
        temperature=0.3,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _extrair_json(texto: str) -> str:
    texto = texto.strip()
    texto = re.sub(r"^```json\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"^```\s*", "", texto)
    texto = re.sub(r"\s*```$", "", texto)
    match = re.search(r"(\{.*\})", texto, re.DOTALL)
    return match.group(1).strip() if match else texto.strip()


def _reparar_json(texto: str) -> str:
    texto = re.sub(r"^```(?:json)?\s*", "", texto.strip(), flags=re.IGNORECASE)
    texto = re.sub(r"\s*```$", "", texto)
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.DOTALL)
    texto = re.sub(r"(?m)^\s*//.*$", "", texto)
    texto = re.sub(r",\s*([\}\]])", r"\1", texto)
    return texto.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers — snapshot histórico (6.5.11)
# ═══════════════════════════════════════════════════════════════════════════════

_FRENTE_THEMES_SQL: dict[str, str] = {
    "EnergyTech": "('Energia','EnergyTech','Energia & Eficiência Energética')",
    "CleanTech":  "('CleanTech')",
    "AgriTech":   "('AgroTech')",
    "DeepTech":   "('IA & Automação','DeepTech','Deep Tech & Semicondutores','Data Centers & Infraestrutura','Data Centers & Infra')",
    "FinTech":    "('FinTech','Financiamento & Inovação','Modelos de Negócio & Startups')",
}


def _snap_themes_to_fronts(themes: list[str], title: str = "") -> list[str]:
    corpus = " ".join(themes + [title]).lower()
    fronts: set[str] = set()
    if any(k in corpus for k in ["energia", "energét", "energeti", "elétric", "eletric",
                                   "solar", "bess", "transmiss", "eólica", "eolica",
                                   "geração", "geracao", "hidro", "renovável", "renovavel",
                                   "armazenamento", "despacho", "pld", "aneel", "ccee",
                                   "lrcap", "leilão", "leilao", "ons ", "smart grid"]):
        fronts.add("EnergyTech")
    if any(k in corpus for k in ["hidrogênio", "hidrogenio", "h2v", "verde", "sustentab",
                                   "descarboniz", "clean", "carbon", "lítio", "litio",
                                   "mobilidade elétrica", "mobilidade eletrica",
                                   "transição energética", "transicao energetica"]):
        fronts.add("CleanTech")
    if any(k in corpus for k in ["agroneg", "agro", "agric", "rural", "embrapa", "safra",
                                   "soja", "milho", "pecuária", "pecuaria", "colheita",
                                   "crédito rural", "credito rural"]):
        fronts.add("AgriTech")
    if any(k in corpus for k in ["inteligência artificial", "inteligencia artificial",
                                   "ia ", "data center", "digital", "5g", "robótica",
                                   "robotica", "deeptech", "cibersegur", "semicondutor",
                                   "iot", "satélit", "satelit"]):
        fronts.add("DeepTech")
    if any(k in corpus for k in ["financ", "rating", "spread", "fintech", "pix",
                                   "open finance", "tokeniz", "câmbio", "cambio",
                                   "banco central", "bcb", "cvm", "custo de capital",
                                   "esgotamento fiscal"]):
        fronts.add("FinTech")
    return list(fronts) if fronts else ["EnergyTech"]


def _snap_entity_to_fronts(name: str, entity_type: str = "") -> list[str]:
    nl = (name + " " + entity_type).lower()
    fronts: set[str] = set()
    if any(k in nl for k in ["ons", "aneel", "ccee", "epe", "mme", "pld", "bess",
                               "solar", "eólica", "transmiss", "energi", "petrobras"]):
        fronts.add("EnergyTech")
    if any(k in nl for k in ["bess", "hidrogênio", "hidrogenio", "carbono", "clean"]):
        fronts.add("CleanTech")
    if any(k in nl for k in ["bndes", "banco", "bcb", "cvm", "financ", "pagament",
                               "fintech", "open finance", "crédito", "credito"]):
        fronts.add("FinTech")
    if any(k in nl for k in ["embrapa", "agro", "rural", "agric"]):
        fronts.add("AgriTech")
    if any(k in nl for k in ["anatel", "cade", "digital", "ia", "5g", "semicondutor"]):
        fronts.add("DeepTech")
    return list(fronts) if fronts else ["EnergyTech"]


def gerar_snapshot_historico(db_path: Path) -> dict:
    """Lê SQLite e retorna snapshot completo para embutir no intel_output.json."""
    if not db_path or not Path(db_path).exists():
        return {}

    result: dict = {}
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # pressure_weeks — score médio por frente nas últimas 10 semanas
        try:
            weeks: dict = {}
            for frente, themes_sql in _FRENTE_THEMES_SQL.items():
                cur.execute(f"""
                    SELECT strftime('%Y-W%W', cycle_date) as semana,
                           ROUND(AVG(CAST(score AS REAL)), 2) as val
                    FROM raw_items
                    WHERE theme IN {themes_sql}
                      AND cycle_date >= date('now', '-75 days')
                      AND score IS NOT NULL
                    GROUP BY semana ORDER BY semana
                """)
                for row in cur.fetchall():
                    sem = row["semana"]
                    if sem not in weeks:
                        weeks[sem] = {"semana": sem}
                    weeks[sem][frente] = row["val"] or 0.0
            result["pressure_weeks"] = sorted(weeks.values(), key=lambda r: r["semana"])[-10:]
        except Exception:
            pass

        # tech_signals — contagem e score por tecnologia
        try:
            cur.execute("""
                SELECT keyword, COUNT(*) as n, ROUND(AVG(CAST(score AS REAL)),2) as score
                FROM (
                  SELECT CAST(score AS REAL) as score,
                    CASE
                      WHEN lower(title) LIKE '%solar%' OR lower(title) LIKE '%fotovoltai%' THEN 'Solar GD'
                      WHEN lower(title) LIKE '%bess%' OR lower(title) LIKE '%armazenamento de energia%' THEN 'BESS'
                      WHEN lower(title) LIKE '%hidrogênio%' OR lower(title) LIKE '%hydrogen%' THEN 'H₂ Verde'
                      WHEN lower(title) LIKE '%data center%' THEN 'Data Centers IA'
                      WHEN lower(title) LIKE '%nuclear%' OR lower(title) LIKE '%smr%' THEN 'Nuclear / SMR'
                      WHEN lower(title) LIKE '%veículo elétrico%' OR lower(title) LIKE '%electric vehicle%' THEN 'EV / Mobilidade Elétrica'
                      WHEN lower(title) LIKE '%inteligência artificial%' OR lower(title) LIKE '%artificial intel%' THEN 'IA Generativa'
                      WHEN lower(title) LIKE '%lítio%' OR lower(title) LIKE '%litio%' OR lower(title) LIKE '%lithium%' THEN 'Lítio & Mineração'
                      WHEN lower(title) LIKE '%eólica%' OR lower(title) LIKE '%wind%' THEN 'Eólica Onshore'
                      WHEN lower(title) LIKE '%5g%' THEN '5G Industrial'
                      WHEN lower(title) LIKE '%pix%' OR lower(title) LIKE '%open finance%' THEN 'Open Finance / Pix'
                      WHEN lower(title) LIKE '%transmissão%' THEN 'Transmissão Elétrica'
                      WHEN lower(title) LIKE '%robótica%' OR lower(title) LIKE '%robot%' THEN 'Robótica Industrial'
                      WHEN lower(title) LIKE '%satélite%' OR lower(title) LIKE '%satellite%' THEN 'Satélites LEO'
                      WHEN lower(title) LIKE '%semicondutor%' OR lower(title) LIKE '%semiconductor%' THEN 'Semicondutores Brasil'
                    END as keyword
                  FROM raw_items WHERE title IS NOT NULL AND score IS NOT NULL
                ) t WHERE keyword IS NOT NULL
                GROUP BY keyword
            """)
            tech_signals: dict = {}
            tech_scores: dict = {}
            for row in cur.fetchall():
                tech_signals[row["keyword"]] = row["n"]
                tech_scores[row["keyword"]] = row["score"] or 0.0
            result["tech_signals"] = tech_signals
            result["tech_scores"]  = tech_scores
        except Exception:
            pass

        # memories — memórias estratégicas ativas
        try:
            cur.execute("""
                SELECT id, title, thesis, themes, supporting_facts, strength
                FROM strategic_memory WHERE status='active' ORDER BY strength DESC
            """)
            memories = []
            for row in cur.fetchall():
                try:
                    themes_list = json.loads(row["themes"] or "[]")
                except Exception:
                    themes_list = []
                fronts  = _snap_themes_to_fronts(themes_list, title=row["title"] or "")
                analise = "Temas: " + ", ".join(themes_list[:4]) if themes_list else ""
                memories.append({
                    "id": row["id"], "title": row["title"],
                    "fronts": fronts, "strength": row["strength"] or 0,
                    "desc": (row["thesis"] or "")[:280], "analise": analise,
                })
            result["memories"] = memories
        except Exception:
            pass

        # zettels — notas zettel ativas
        try:
            cur.execute("""
                SELECT id, title, themes, supports, interpretation, body, strength
                FROM zettel_notes WHERE status='active'
            """)
            zettels = []
            for row in cur.fetchall():
                try:
                    themes_list = json.loads(row["themes"] or "[]")
                except Exception:
                    themes_list = []
                fronts = _snap_themes_to_fronts(themes_list, title=row["title"] or "")
                body   = row["body"] or ""
                body_short = body[:300].rsplit(". ", 1)[0] + "." if len(body) > 300 else body
                zettels.append({
                    "id": row["id"], "title": row["title"],
                    "fronts": fronts, "memory": row["supports"],
                    "strength": float(row["strength"] or 5.0),
                    "desc": (row["interpretation"] or "")[:280], "analise": body_short,
                })
            result["zettels"] = zettels
        except Exception:
            pass

        # entities — entidades canônicas top 10 com contagem real de sinais e fatos
        # v32: subquery por busca textual em raw_items e canonical_facts;
        # independe de signal_entity_links estar populada.
        try:
            cur.execute("""
                SELECT
                    e.id,
                    e.name,
                    e.aliases,
                    e.entity_type,
                    e.importance_score,
                    (
                        SELECT COUNT(*)
                        FROM raw_items r
                        WHERE r.title   LIKE '%' || e.name || '%'
                           OR r.description LIKE '%' || e.name || '%'
                    ) AS sinais_nome,
                    (
                        SELECT COUNT(*)
                        FROM canonical_facts f
                        WHERE f.value_literal LIKE '%' || e.name || '%'
                    ) AS fatos_nome
                FROM entities e
                ORDER BY e.importance_score DESC
                LIMIT 10
            """)
            entities = []
            for row in cur.fetchall():
                name   = row["name"] or ""
                etype  = row["entity_type"] or ""
                fronts = _snap_entity_to_fronts(name, etype)

                # Contagem primária: match pelo nome canônico
                sinais = int(row["sinais_nome"] or 0)
                fatos  = int(row["fatos_nome"]  or 0)

                # Complemento: match pelos aliases (ex.: "Banco Nacional de Desenvolvimento")
                aliases_raw = row["aliases"] or ""
                for alias in [a.strip() for a in aliases_raw.split(",") if a.strip() and a.strip().lower() != name.lower()]:
                    try:
                        cur.execute(
                            "SELECT COUNT(*) FROM raw_items WHERE title LIKE ? OR description LIKE ?",
                            (f"%{alias}%", f"%{alias}%"),
                        )
                        sinais += int(cur.fetchone()[0] or 0)
                        cur.execute(
                            "SELECT COUNT(*) FROM canonical_facts WHERE value_literal LIKE ?",
                            (f"%{alias}%",),
                        )
                        fatos += int(cur.fetchone()[0] or 0)
                    except Exception:
                        pass

                # Texto analítico: memória estratégica mais forte que menciona a entidade
                analise = ""
                acao    = ""
                desc    = f"Tipo: {etype}" if etype else ""
                termos  = [name] + [a.strip() for a in (row["aliases"] or "").split(",") if a.strip()]
                like_clauses = " OR ".join(
                    ["(sm.title LIKE ? OR sm.thesis LIKE ?)"] * len(termos)
                )
                like_params = [p for t in termos for p in (f"%{t}%", f"%{t}%")]
                try:
                    cur.execute(
                        f"""
                        SELECT sm.title, sm.thesis, sm.supporting_facts, sm.strength
                        FROM strategic_memory sm
                        WHERE sm.status = 'active'
                          AND ({like_clauses})
                        ORDER BY sm.strength DESC
                        LIMIT 1
                        """,
                        like_params,
                    )
                    mem_row = cur.fetchone()
                    if mem_row:
                        thesis = (mem_row["thesis"] or "")[:400]
                        if thesis:
                            analise = thesis
                        # desc: tipo e nome canônico da entidade (sem título de publicação)
                        desc = f"{name} · {etype}"
                except Exception:
                    pass

                # Fallback analise: zettel mais recente que menciona a entidade
                if not analise:
                    like_z = " OR ".join(
                        ["(zn.title LIKE ? OR zn.body LIKE ?)"] * len(termos)
                    )
                    like_z_params = [p for t in termos for p in (f"%{t}%", f"%{t}%")]
                    try:
                        cur.execute(
                            f"""
                            SELECT zn.title, zn.interpretation, zn.body
                            FROM zettel_notes zn
                            WHERE zn.status = 'active'
                              AND ({like_z})
                            ORDER BY zn.strength DESC, zn.last_seen DESC
                            LIMIT 1
                            """,
                            like_z_params,
                        )
                        z_row = cur.fetchone()
                        if z_row:
                            body = (z_row["body"] or "")
                            analise = (z_row["interpretation"] or body)[:380]
                            if not analise:
                                analise = ""  # título de zettel não entra no corpus
                    except Exception:
                        pass

                # Ação: resumo analítico do sinal mais relevante (sem citar título)
                try:
                    cur.execute(
                        """
                        SELECT description, score
                        FROM raw_items
                        WHERE (title LIKE ? OR description LIKE ?)
                        ORDER BY score DESC, collected_at DESC
                        LIMIT 1
                        """,
                        (f"%{name}%", f"%{name}%"),
                    )
                    sig_row = cur.fetchone()
                    if sig_row:
                        _desc = (sig_row['description'] or '').strip()
                        # Usa a primeira frase completa da descrição como contexto analítico
                        _frases = re.split(r'(?<=[.!?])\s+', _desc)
                        _acao_texto = _frases[0] if _frases and len(_frases[0]) > 30 else _desc[:300]
                        # Garante que não termina no meio de uma palavra
                        if len(_acao_texto) >= 300 and not _acao_texto[-1] in '.!?':
                            _acao_texto = _acao_texto.rsplit(' ', 1)[0] + '…'
                        acao = f"Contexto recente (score {sig_row['score'] or 0:.1f}): {_acao_texto}"
                except Exception:
                    pass

                entities.append({
                    "id": row["id"], "label": name,
                    "fronts": fronts, "importance": float(row["importance_score"] or 0.5),
                    "sinais": sinais, "fatos": fatos,
                    "desc": desc, "analise": analise, "acao": acao,
                })
            result["entities"] = entities
        except Exception:
            pass

        # cockpit totals
        _simple = [
            ("total_acumulado",  "SELECT COUNT(*) FROM raw_items"),
            ("fontes_acumuladas","SELECT COUNT(DISTINCT source) FROM raw_items"),
            ("fatos_acumulados", "SELECT COUNT(*) FROM canonical_facts"),
            ("memorias_ativas",  "SELECT COUNT(*) FROM strategic_memory WHERE status='active'"),
            ("entidades",        "SELECT COUNT(*) FROM entities"),
        ]
        for key, sql in _simple:
            try:
                cur.execute(sql)
                result[key] = cur.fetchone()[0]
            except Exception:
                pass
        try:
            cur.execute("""SELECT strftime('%Y-W%W', cycle_date) as sem, COUNT(*) as n
                FROM raw_items WHERE cycle_date >= date('now','-90 days')
                GROUP BY sem ORDER BY sem""")
            result["weekly_counts"] = [(r[0], r[1]) for r in cur.fetchall()]
        except Exception:
            pass
        try:
            cur.execute("""SELECT date(cycle_date), COUNT(*),
                    SUM(CASE WHEN CAST(score AS REAL) >= 8 THEN 1 ELSE 0 END)
                FROM raw_items WHERE cycle_date >= date('now','-15 days')
                  AND cycle_date IS NOT NULL
                GROUP BY date(cycle_date) ORDER BY date(cycle_date)""")
            result["daily_counts"] = [(r[0], r[1], r[2] or 0) for r in cur.fetchall()]
        except Exception:
            pass
        try:
            cur.execute("SELECT MIN(cycle_date), MAX(cycle_date) FROM raw_items")
            r = cur.fetchone()
            result["data_inicio"] = r[0] or ""
            result["data_fim"]    = r[1] or ""
        except Exception:
            pass

        con.close()
    except Exception as exc:
        print(f"  [!] gerar_snapshot_historico: {exc}")

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers — Lente de Decisão (6.5.10)
# ═══════════════════════════════════════════════════════════════════════════════

_LENTE_SYSTEM = (
    "Você receberá os dados de um ciclo do Radar xTech.\n"
    "Gere o conteúdo para os 6 cards da seção \"Lente de Decisão\".\n"
    "Para cada perfil abaixo, produza exatamente três campos:\n"
    "  - sinal: uma frase sobre o sinal mais relevante do ciclo para este perfil\n"
    "  - decisao: uma frase sobre a ação que não pode esperar (semanas, não trimestres)\n"
    "  - risco: uma frase sobre o risco que provavelmente ainda não está no modelo deste perfil\n"
    "Perfis:\n"
    "1. Gestor de Risco / CRO\n"
    "2. Empreendedor / Fundador\n"
    "3. Especialista Técnico / Engenheiro\n"
    "4. Investidor / Alocador\n"
    "5. Head de Compliance / ESG / Jurídico\n"
    "6. Conselheiro / Executivo\n"
    "Regras:\n"
    "- Cada frase: máximo 25 palavras\n"
    "- Sem jargão vazio — cada frase deve conter uma afirmação específica e verificável\n"
    "- Não repetir o mesmo sinal em perfis diferentes\n"
    "- Retornar JSON puro, sem markdown, sem explicação\n"
    "Formato: {\"lente_decisao\": [{\"perfil\": \"...\", \"sinal\": \"...\", \"decisao\": \"...\", \"risco\": \"...\"}, ...]}"
)

_LENTE_PERFIS = [
    "Gestor de Risco / CRO",
    "Empreendedor / Fundador",
    "Especialista Técnico / Engenheiro",
    "Investidor / Alocador",
    "Head de Compliance / ESG / Jurídico",
    "Conselheiro / Executivo",
]


def gerar_lente_decisao(client: anthropic.Anthropic, output: dict) -> list[dict]:
    """6.5.10 — Gera 6 cards da Lente de Decisão via LLM."""
    print("  -> [6.5.10] Lente de Decisão (Sonnet — 6 perfis de decisor)...")
    dash     = output.get("dashboard") or {}
    briefing = output.get("briefing_diario") or {}
    tese     = dash.get("executive_thesis") or {}
    vetores  = (output.get("vetores_estrategicos") or [])[:4]
    convergencias = (output.get("convergencia") or [])[:3]

    ctx = {
        "ciclo":               dash.get("ciclo") or output.get("ciclo_id"),
        "executive_thesis":    tese.get("frase_central") or briefing.get("titulo") or "",
        "mudancas_estruturais":(tese.get("mudancas_estruturais") or [])[:3],
        "vetores_top": [
            {k: v.get(k) for k in ("titulo", "nome", "quadrante_executivo",
                                    "decisao_recomendada", "custo_espera")}
            for v in vetores
        ],
        "convergencias": [
            {k: c.get(k) for k in ("tema", "nivel", "sinais_relacionados")}
            for c in convergencias
        ],
    }

    _FALLBACK = [
        {"perfil": p, "sinal": "—", "decisao": "—", "risco": "—"}
        for p in _LENTE_PERFIS
    ]

    try:
        texto = _chamar_sonnet(
            client, _LENTE_SYSTEM,
            f"Dados do ciclo:\n{json.dumps(ctx, ensure_ascii=False)}",
            max_tokens=1200,
        )
        resultado = json.loads(_reparar_json(_extrair_json(texto)))
        cards = resultado.get("lente_decisao", [])
        if len(cards) == 5:
            print("     [OK] Lente de Decisão — 5 cards gerados.")
            return cards
        print(f"  [!] Lente: retornou {len(cards)} cards, esperado 5. Usando fallback.")
        return _FALLBACK
    except Exception as exc:
        print(f"  [!] Lente de Decisão falhou: {exc}. Usando fallback.")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.1 — Hero
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_hero(client: anthropic.Anthropic, output: dict) -> dict:
    """Monta hero com título editorial próprio do Radar + briefing gerado por LLM."""
    itens = output.get("itens", [])
    if not itens:
        return {}

    top = max(itens, key=lambda x: x.get("score_final", 0))
    an  = top.get("analise", {})

    xtech = _setor_to_xtech(
        top.get("setor_normalizado", ""),
        top.get("titulo_pt") or top.get("titulo", ""),
        an.get("tema_analisado", ""),
    )
    score_ips = round(an.get("score_final", 0), 1)

    # sinal_por_xtech: melhor item por frente
    xtech_tops: dict[str, dict] = {}
    for item in itens:
        an_i  = item.get("analise", {})
        xt_i  = _setor_to_xtech(
            item.get("setor_normalizado", ""),
            item.get("titulo_pt") or item.get("titulo", ""),
            an_i.get("tema_analisado", ""),
        )
        score_i = an_i.get("score_final", 0)
        if xt_i not in xtech_tops or score_i > xtech_tops[xt_i].get("_score", 0):
            xtech_tops[xt_i] = {
                "manchete": item.get("titulo_pt") or item.get("titulo", ""),
                "score":    round(score_i, 1),
                "resumo":   an_i.get("contexto_decisao", "")[:160],
                "_score":   score_i,
            }
    sinal_por_xtech = {
        xt: {k: v for k, v in d.items() if k != "_score"}
        for xt, d in xtech_tops.items()
    }

    # ── Gera título editorial + briefing via LLM ────────────────────────────
    # Prepara contexto: top-5 itens por score + briefing_diario
    top5 = sorted(itens, key=lambda x: x.get("score_final", 0), reverse=True)[:5]
    sinais_ctx = "\n".join(
        f'- [{round(i.get("score_final",0),1)}] {i.get("titulo_pt") or i.get("titulo","")} '
        f'({i.get("analise",{}).get("tema_analisado","")})'
        for i in top5
    )
    briefing_abertura = output.get("briefing_diario", {}).get("frase_de_abertura", "")

    # Formata data por extenso para o kicker (ex: "26 Jun 2026")
    try:
        from datetime import datetime as _dt
        _meses = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
        _d = _dt.fromisoformat(HOJE_ISO)
        _data_kicker = f"{_d.day} {_meses[_d.month-1]} {_d.year}"
    except Exception:
        _data_kicker = HOJE_ISO

    prompt = f"""Você é o editor do Radar xTech, uma plataforma de inteligência estratégica para executivos brasileiros.
O Radar monitorou centenas de sinais hoje ({HOJE_ISO}) e identificou os seguintes como mais relevantes:

{sinais_ctx}

Contexto do ciclo: {briefing_abertura[:300]}

---
Sua tarefa: gere um JSON com QUATRO campos:

- "kicker": linha de contexto no formato "[RISCO | OPORTUNIDADE | NEUTRO] · {_data_kicker}"
  Use apenas um dos três termos (RISCO, OPORTUNIDADE ou NEUTRO) baseado no tom dominante dos sinais.
  PROIBIDO: nomear alguma xTech específica no kicker (a manchete pode afetar mais de uma frente).

- "manchete": título editorial de NO MÁXIMO 9 PALAVRAS com voz própria do Radar — não copie os títulos acima.
  OBRIGATÓRIO: verbo ativo no presente; uma única ideia central; somente português brasileiro; número concreto em posição inicial quando disponível.
  PROIBIDO: duas ideias na mesma frase; sigla isolada sem âncora; inglês misturado; início com subordinada ("Enquanto", "Apesar"); parênteses com dado; nomear xTech específica.
  OBRIGATÓRIO para eventos ainda não operacionais: use "previsto para", "sinaliza" ou "abre caminho para" — nunca afirme fato futuro como consumado.
  Exemplo correto (6 palavras): "Leilão de BESS previsto para dezembro abre oportunidade."
  Exemplo proibido (15 palavras): "Primeiro leilão de BESS previsto para dezembro de 2026 abre caminho para nova arquitetura de flexibilidade energética."

- "deck": 1 a 2 frases (máx 50 palavras). Inclua dado numérico concreto quando disponível. Finalize com implicação clara para o leitor.
  Exemplo: "Regulamentação aprovada reduz custo de implantação em 30%. Gestores de portfólio devem revisar premissas de capex antes do Q3."

- "briefing": parágrafo único de 3-5 frases (80-130 palavras) explicando o que está acontecendo, por que importa e qual é a implicação para tomadores de decisão. Linguagem executiva, direta, sem jargão.

Responda APENAS com o JSON, sem markdown, sem explicações."""

    manchete_orig = top.get("titulo_pt") or top.get("titulo", "")
    briefing_orig = briefing_abertura

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        parsed = json.loads(_reparar_json(raw))
        manchete_final = parsed.get("manchete") or manchete_orig
        kicker_final   = parsed.get("kicker") or f"ANÁLISE · {_data_kicker}"
        deck_final     = parsed.get("deck") or ""
        briefing_final = parsed.get("briefing") or briefing_orig
    except Exception as e:
        print(f"     ⚠ hero LLM falhou ({e}), usando título original.")
        manchete_final = manchete_orig
        kicker_final   = f"ANÁLISE · {_data_kicker}"
        deck_final     = ""
        briefing_final = briefing_orig

    return {
        "manchete":        manchete_final,
        "kicker":          kicker_final,
        "deck":            deck_final,
        "score":           score_ips,
        "xtech":           xtech,
        "briefing":        briefing_final,
        "sinal_por_xtech": sinal_por_xtech,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.2 — Impacto por xTech
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_impacto_xtech(client: anthropic.Anthropic, output: dict, db_path: Path) -> dict:
    """Gera impacto por frente xTech com comparação histórica do SQLite."""
    print("  -> [6.5.2] Impacto por xTech (Sonnet + SQLite)...")

    manchete = output.get("hero", {}).get("manchete", "")
    briefing = output.get("hero", {}).get("briefing", "")[:600]

    # Consulta ciclos anteriores do SQLite
    historico_rows = _query_sqlite(
        db_path,
        "SELECT cycle_date, title FROM briefing_cycles ORDER BY cycle_date DESC LIMIT 10",
    )
    historico_txt = "\n".join(f"- {r[0]}: {r[1]}" for r in historico_rows) if historico_rows else "Histórico não disponível."

    system = (
        "Você é analista sênior do Radar xTech. "
        "Analise o impacto da manchete do ciclo sobre cada uma das 5 frentes tecnológicas. "
        "Use acentuação correta do português brasileiro. "
        "Retorne APENAS JSON válido, sem texto fora do JSON."
    )

    user = f"""Manchete do ciclo: {manchete}

Contexto do ciclo: {briefing}

Histórico de ciclos recentes:
{historico_txt}

Para cada uma das 5 xTechs, avalie o impacto da manchete do ciclo.
Se o impacto é recorrente (aparece no histórico de ciclos), indique isso na análise.

Retorne EXATAMENTE este JSON:
{{
  "EnergyTech": {{
    "sinal_referencia": "Manchete do sinal mais importante para EnergyTech neste ciclo (pode ser diferente da manchete global)",
    "impacto": "Impacto do sinal em 1 frase. Racional em 1 frase. Máx 2 frases.",
    "direcao": "positivo",
    "urgencia": "imediata"
  }},
  "CleanTech": {{
    "sinal_referencia": "...",
    "impacto": "...",
    "direcao": "neutro",
    "urgencia": "médio_prazo"
  }},
  "FinTech": {{
    "sinal_referencia": "...",
    "impacto": "...",
    "direcao": "negativo",
    "urgencia": "monitorar"
  }},
  "DeepTech": {{
    "sinal_referencia": "...",
    "impacto": "...",
    "direcao": "positivo",
    "urgencia": "imediata"
  }},
  "AgriTech": {{
    "sinal_referencia": "...",
    "impacto": "...",
    "direcao": "ambíguo",
    "urgencia": "monitorar"
  }}
}}

REGRAS:
- direcao: "positivo" | "negativo" | "neutro" | "ambíguo"
- urgencia: "imediata" | "médio_prazo" | "monitorar"
- Se o impacto for recorrente em ciclos anteriores, acrescente nota como "(3º ciclo consecutivo)" ou "(recorrente desde 2026-05-20)"
"""

    _FALLBACK = {
        x: {"impacto": "Impacto não avaliado neste ciclo.", "direcao": "neutro", "urgencia": "monitorar"}
        for x in XTECHS
    }

    try:
        texto = _chamar_sonnet(client, system, user, max_tokens=2000)
        resultado = json.loads(_reparar_json(_extrair_json(texto)))
        for xtech in XTECHS:
            if xtech not in resultado:
                resultado[xtech] = _FALLBACK[xtech]
        print(f"     [OK] Impacto gerado para {len(resultado)} xTechs.")
        return resultado
    except Exception as e:
        print(f"  [!] Erro impacto_xtech: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.3 — Âncoras Narrativas dos Gráficos
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_graph_anchors(client: anthropic.Anthropic, output: dict, db_path: Path) -> dict:
    """Gera frases-âncora para os 4 gráficos do Radar com comparação histórica."""
    print("  -> [6.5.3] Graph anchors (Sonnet + SQLite)...")

    dashboard  = output.get("dashboard", {})
    vetores    = output.get("vetores_estrategicos", [])
    n_mobilizar = sum(1 for v in vetores if v.get("quadrante_executivo") == "Mobilizar Agora")

    # Tendência 60 dias — conta ciclos com alta pressão
    ciclos_pressao = _query_sqlite(
        db_path,
        """SELECT cycle_date, title FROM briefing_cycles
           ORDER BY cycle_date DESC LIMIT 15""",
    )
    n_ciclos = len(ciclos_pressao)

    # xTech dominante baseada no top item
    xtech_dominante = output.get("hero", {}).get("xtech", "EnergyTech")

    # Clusters para convergência
    clusters = dashboard.get("clusters", [])
    cluster_top = clusters[0].get("nome", "") if clusters else ""

    # Ciclos consecutivos — heurística: contamos ciclos com mesmo xTech dominante
    ciclos_historico = _query_sqlite(
        db_path,
        "SELECT cycle_date FROM briefing_cycles ORDER BY cycle_date DESC LIMIT 5",
    )
    n_consecutivos = len(ciclos_historico) + 1  # +1 inclui ciclo atual

    system = (
        "Você é redator estratégico do Radar xTech. "
        "Gere frases-âncora concisas para os 4 gráficos do Radar. "
        "Cada frase deve ter exatamente 1 linha com um dado comparativo concreto. "
        "Acentuação correta do português brasileiro. "
        "Retorne APENAS JSON válido."
    )

    user = f"""Dados do ciclo {HOJE_ISO}:
- xTech dominante: {xtech_dominante}
- Vetores em "Mobilizar Agora": {n_mobilizar}
- Ciclos históricos disponíveis: {n_ciclos}
- Ciclos consecutivos (estimativa): {n_consecutivos}
- Cluster de maior convergência: {cluster_top}

Gere exatamente 4 frases-âncora, uma por gráfico:

Retorne EXATAMENTE este JSON:
{{
  "maturity_curve": "Frase de 1 linha sobre a curva de maturidade com dado comparativo. Ex: 'BESS avançou para Early Majority pelo {n_consecutivos}º ciclo consecutivo'",
  "pressure_map": "Frase de 1 linha sobre o mapa de pressão. Ex: '{n_mobilizar} vetores em Mobilizar Agora — acima da média dos últimos {n_ciclos} ciclos'",
  "trend_60d": "Frase de 1 linha sobre tendência de 60 dias. Ex: '{xtech_dominante} mantém pressão crescente pelo {n_consecutivos}º ciclo'",
  "xtech_graph": "Frase de 1 linha sobre o grafo de convergência. Ex: 'Convergência DeepTech × {xtech_dominante} com {n_mobilizar} sinais compartilhados'",
  "periodo_coberto": "Período legível para o rodapé dos gráficos. Ex: 'Abril–Junho 2026'. Sem referências a demonstração ou SQLite."
}}

REGRAS:
- Cada frase deve ter um número ou dado comparativo concreto
- Sem aspas internas desnecessárias
- Máximo 120 caracteres por frase
"""

    _FALLBACK = {
        "maturity_curve": f"Ciclo {HOJE_ISO}: monitorar posição das xTechs na curva de maturidade.",
        "pressure_map":   f"{n_mobilizar} vetores em Mobilizar Agora no ciclo de {HOJE_ISO}.",
        "trend_60d":      f"{xtech_dominante} em destaque no ciclo de {HOJE_ISO}.",
        "xtech_graph":    f"Grafo de convergência atualizado para o ciclo {HOJE_ISO}.",
        "periodo_coberto": f"Abril–Junho {HOJE_ISO[:4]}",
    }

    try:
        texto = _chamar_sonnet(client, system, user, max_tokens=600)
        resultado = json.loads(_reparar_json(_extrair_json(texto)))
        for k in _FALLBACK:
            if k not in resultado or not resultado[k]:
                resultado[k] = _FALLBACK[k]
        print("     [OK] 4 âncoras narrativas geradas.")
        return resultado
    except Exception as e:
        print(f"  [!] Erro graph_anchors: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.4 — Fatos Duros do Ciclo
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_fatos_duros(output: dict) -> list[dict]:
    """Seleciona 3 a 6 fatos quantitativos do ciclo e mapeia para xTech."""
    fatos_raw = output.get("fatos_canonicos", [])

    # Filtra fatos com dados numéricos concretos (exclui datas genéricas isoladas)
    TIPOS_QUANTITATIVOS = {"monetario", "percentual", "capacidade", "quantidade", "tarifa"}
    fatos_quant = [
        f for f in fatos_raw
        if f.get("tipo", "") in TIPOS_QUANTITATIVOS and f.get("confianca") == "alta"
    ]

    # Ordena por tipo preferido (monetário > capacidade > percentual > quantidade)
    TIPO_PRIO = {"monetario": 0, "capacidade": 1, "percentual": 2, "quantidade": 3, "tarifa": 4}
    fatos_quant.sort(key=lambda f: TIPO_PRIO.get(f.get("tipo", ""), 9))

    # Limita a 6
    fatos_selecionados = fatos_quant[:6]

    # Garante mínimo de 3 incluindo outros tipos se necessário
    if len(fatos_selecionados) < 3:
        extras = [f for f in fatos_raw if f not in fatos_selecionados]
        fatos_selecionados.extend(extras[:3 - len(fatos_selecionados)])

    # Mapeia para xTech — usa fonte_titulo (resolvido pelo analyzer) para lookup correto
    itens = output.get("itens", [])
    titulo_idx: dict[str, dict] = {
        (it.get("titulo_pt") or it.get("titulo", "")).lower(): it
        for it in itens
    }

    resultado = []
    for f in fatos_selecionados:
        fonte_titulo = f.get("fonte_titulo", "")
        item = titulo_idx.get(fonte_titulo.lower(), {}) if fonte_titulo else {}
        xtech = _setor_to_xtech(
            item.get("setor_normalizado", ""),
            fonte_titulo or item.get("titulo_pt") or item.get("titulo", ""),
            item.get("analise", {}).get("tema_analisado", ""),
        )
        resultado.append({
            "valor":    f.get("valor_literal", ""),
            "contexto": f.get("contexto", ""),
            "fonte_titulo": fonte_titulo,
            "fonte_url":    f.get("fonte_url", ""),
            "xtech":    xtech,
        })

    return resultado[:6]


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.5 — Cenários por xTech
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_cenarios_xtech(client: anthropic.Anthropic, output: dict, db_path: Path) -> dict:
    """Gera cenários prospectivos por xTech com memória de ciclos anteriores."""
    print("  -> [6.5.5] Cenários por xTech (Sonnet + SQLite)...")

    vetores   = output.get("vetores_estrategicos", [])
    manchete  = output.get("hero", {}).get("manchete", "")
    xtech_dom = output.get("hero", {}).get("xtech", "EnergyTech")

    # Busca ciclos anteriores para referência
    ciclos_hist = _query_sqlite(
        db_path,
        "SELECT cycle_date, title, executive_thesis FROM briefing_cycles ORDER BY cycle_date DESC LIMIT 5",
    )
    memoria_txt = "\n".join(
        f"- {r[0]}: {r[1]}" for r in ciclos_hist
    ) if ciclos_hist else "Histórico não disponível."

    # Resumo de vetores por xTech
    vetores_resumo = []
    for v in vetores[:8]:
        xtech_v = _setor_to_xtech(
            " ".join(v.get("setores_afetados", [])),
            v.get("nome", ""),
        )
        vetores_resumo.append(f"[{xtech_v}] {v.get('nome', '')} — pressão {v.get('pressao_estrategica', 0):.1f}")

    system = (
        "Você é analista prospectivo do Radar xTech. "
        "Gere 3 cenários (pessimista, realista, otimista) para 6-12 meses por frente tecnológica. "
        "Use o histórico de ciclos para mostrar EVOLUÇÃO ao longo do tempo. "
        "Cada cenário deve ter nome, descrição clara e gatilho que o confirmaria. "
        "REGRA DE PRECISÃO TEMPORAL: distingua sempre entre (a) fato já ocorrido — afirmação direta; "
        "(b) evento previsto/regulamentado mas não contratado — use 'previsto para [data]', 'se aprovado', 'caso X ocorra'; "
        "(c) cenário prospectivo — use 'pode', 'tende a', 'abre caminho para'. "
        "Nunca afirme como fato consumado algo que ainda não ocorreu. "
        "Use acentuação correta do português brasileiro. "
        "Retorne APENAS JSON válido."
    )

    _CENARIO_SCHEMA = (
        '"pessimista": {"titulo": "Nome pessimista (máx 5 palavras)", "descricao": "O que acontece no pior caso razoável. 2-3 frases.", "gatilho": "Evento que confirmaria este cenário."},'
        '"realista":   {"titulo": "Nome realista (máx 5 palavras)",   "descricao": "Trajetória mais provável dado o contexto atual. 2-3 frases.", "gatilho": "Evento que confirmaria este cenário."},'
        '"otimista":   {"titulo": "Nome otimista (máx 5 palavras)",   "descricao": "O que acontece no melhor caso razoável. 2-3 frases.", "gatilho": "Evento que confirmaria este cenário."}'
    )

    user = f"""Ciclo atual: {HOJE_ISO}
Manchete dominante: {manchete}

Vetores estratégicos do ciclo:
{chr(10).join(vetores_resumo)}

Memória de ciclos anteriores:
{memoria_txt}

Gere 3 cenários (pessimista/realista/otimista) para cada xTech para os próximos 6-12 meses.
Use o histórico para contextualizar (ex: "pelo Xº ciclo consecutivo").

Retorne EXATAMENTE este JSON:
{{
  "EnergyTech": {{ {_CENARIO_SCHEMA} }},
  "CleanTech":  {{ {_CENARIO_SCHEMA} }},
  "FinTech":    {{ {_CENARIO_SCHEMA} }},
  "DeepTech":   {{ {_CENARIO_SCHEMA} }},
  "AgriTech":   {{ {_CENARIO_SCHEMA} }}
}}
"""

    def _fallback_xtech(x: str) -> dict:
        return {
            "pessimista": {"titulo": f"{x} em retração", "descricao": f"Cenário pessimista para {x}: pressão regulatória aumenta, investimentos recuam.", "gatilho": "Reversão das políticas de incentivo setorial."},
            "realista":   {"titulo": f"{x} em consolidação", "descricao": f"Trajetória de {x} segue o ciclo atual sem grandes rupturas.", "gatilho": "Manutenção dos indicadores de pressão atuais."},
            "otimista":   {"titulo": f"{x} em aceleração", "descricao": f"Cenário otimista para {x}: confluência de fatores favorece expansão acelerada.", "gatilho": "Novo ciclo regulatório ou anúncio de investimento âncora."},
        }

    _FALLBACK = {x: _fallback_xtech(x) for x in XTECHS}

    try:
        texto = _chamar_sonnet(client, system, user, max_tokens=4000)
        resultado = json.loads(_reparar_json(_extrair_json(texto)))
        for xtech in XTECHS:
            if xtech not in resultado:
                resultado[xtech] = _FALLBACK[xtech]
        print(f"     [OK] Cenários gerados para {len(resultado)} xTechs.")
        return resultado
    except Exception as e:
        print(f"  [!] Erro cenarios_xtech: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.6 — Motor de Convergência
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_convergencia(output: dict) -> list[dict]:
    """Mapeia clusters do dashboard para o formato de convergência da spec."""
    dashboard = output.get("dashboard", {})
    clusters  = dashboard.get("clusters", [])

    convergencias = []
    for c in clusters:
        titulos = c.get("titulos_noticias", [])

        # Determina xTechs envolvidas pelos títulos e pelo nome do cluster
        nome_cluster = c.get("nome", "")
        xtech_set: set[str] = set()
        for titulo in titulos[:10]:
            xtech_set.add(_setor_to_xtech("", titulo))
        xtech_set.add(_setor_to_xtech("", nome_cluster))

        nivel_raw = c.get("convergencia", "Média")
        nivel = nivel_raw if nivel_raw in ("Alta", "Média", "Baixa") else "Média"

        convergencias.append({
            "titulo":           nome_cluster,
            "nivel":            nivel,
            "num_sinais":       len(titulos),
            "xtech_envolvidas": sorted(xtech_set),
            "narrativa":        c.get("tese", ""),
            "sinais_relacionados": titulos,   # todos os sinais, sem corte
        })

    return convergencias


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.7 — CTA Dinâmico
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_cta(client: anthropic.Anthropic, output: dict) -> dict:
    """Gera CTA dinâmico baseado no tema do dia."""
    print("  -> [6.5.7] CTA dinâmico (Sonnet)...")

    manchete  = output.get("hero", {}).get("manchete", "")
    xtech_dom = output.get("hero", {}).get("xtech", "EnergyTech")
    briefing  = output.get("hero", {}).get("briefing", "")[:400]

    system = (
        "Você é copywriter estratégico do Radar xTech. "
        "Gere um CTA dinâmico baseado no tema do dia. "
        "Tom: direto, executivo, sem jargão vago. "
        "Acentuação correta do português brasileiro. "
        "Retorne APENAS JSON válido."
    )

    # Próxima quinta-feira para a live
    hoje = datetime.now(timezone(timedelta(hours=-3)))
    dias_para_quinta = (3 - hoje.weekday()) % 7
    if dias_para_quinta == 0:
        dias_para_quinta = 7
    proxima_quinta = (hoje + timedelta(days=dias_para_quinta)).strftime("%d/%m/%Y")

    user = f"""Tema do dia: {manchete}
xTech dominante: {xtech_dom}
Briefing: {briefing}

Gere um CTA para a página do Radar xTech com duas colunas: sessão individual (empresas) e live semanal.

Retorne EXATAMENTE este JSON:
{{
  "tema_do_dia": "Tema do dia em 5-8 palavras que capture a essência do ciclo",
  "frase_guia": "Versão contextualizada da frase de condução para o ciclo do dia. Use como modelo: 'O Radar xTech parte de sinais, identifica clusters, mede pressão, define janela de decisão e transforma o ciclo em cenários de negócio.' Adapte para o contexto do dia (máx 2 frases).",
  "cta_empresa": {{
    "headline": "Pergunta de impacto ligada ao tema do dia para C-level. Ex: 'Sua empresa está exposta ao risco de {xtech_dom} de 2026?'",
    "descricao": "2 frases conectando o tema do dia à sessão de diagnóstico de 30 minutos.",
    "botao": "Agendar sessão — 30 minutos"
  }},
  "cta_live": {{
    "headline": "Convite para a live semanal conectado ao tema do dia. 1 frase.",
    "data_live": "{proxima_quinta}",
    "botao": "Participar da live desta semana"
  }}
}}
"""

    _FALLBACK = {
        "tema_do_dia": manchete[:60] if manchete else f"Análise xTech — {HOJE_ISO}",
        "cta_empresa": {
            "headline":   f"Sua empresa está preparada para o cenário de {xtech_dom}?",
            "descricao":  "Em 30 minutos de sessão diagnóstico identificamos os vetores de risco e oportunidade relevantes para o seu negócio.",
            "botao":      "Agendar sessão — 30 minutos",
        },
        "cta_live": {
            "headline":  f"Debate ao vivo: o que o ciclo de hoje significa para {xtech_dom}.",
            "data_live": proxima_quinta,
            "botao":     "Participar da live desta semana",
        },
    }

    try:
        texto = _chamar_sonnet(client, system, user, max_tokens=800)
        resultado = json.loads(_reparar_json(_extrair_json(texto)))
        print("     [OK] CTA gerado.")
        return resultado
    except Exception as e:
        print(f"  [!] Erro cta: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.8 — Score Badge nos Vetores
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_score_badge(vetor: dict) -> str:
    """Gera badge de pressão temática integrado para card de vetor."""
    mat = vetor.get("materialidade", {})
    score_map = {"Alta": 80, "Média": 55, "Baixa": 30}

    best_tipo  = None
    best_score = 0
    for tipo, nivel in mat.items():
        s = score_map.get(nivel, 40)
        if s > best_score:
            best_score = s
            best_tipo  = MATERIALIDADE_LABELS.get(tipo, tipo.capitalize())

    pressao_100 = round(vetor.get("pressao_estrategica", 5.0) * 10)

    if best_tipo:
        return f"{best_tipo} {pressao_100}/100"
    return f"Pressão {pressao_100}/100"


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.9 — Aplicações Corporativas [NOVO]
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_aplicacoes_corporativas(client: anthropic.Anthropic, output: dict) -> dict:
    """Gera aplicações corporativas contextualizadas para o ciclo do dia."""
    print("  -> [6.5.9] Aplicações corporativas (Sonnet)...")

    manchete  = output.get("hero", {}).get("manchete", "")
    xtech_dom = output.get("hero", {}).get("xtech", "EnergyTech")
    vetores   = output.get("vetores_estrategicos", [])[:3]
    vet_txt   = " | ".join(v.get("nome", "") for v in vetores)

    system = (
        "Você é consultor de transformação estratégica. "
        "Gere aplicações corporativas ESPECÍFICAS para o ciclo do dia — não genéricas. "
        "Acentuação correta do português brasileiro. "
        "Retorne APENAS JSON válido."
    )

    user = f"""Ciclo {HOJE_ISO}: {manchete}
xTech dominante: {xtech_dom}
Vetores do dia: {vet_txt}

Liste 6 a 8 aplicações corporativas ESPECÍFICAS para este ciclo.
Cada aplicação deve ser conectada ao tema do dia — não genérica.

Exemplos de tipos de uso: revisão de riscos regulatórios, priorização de CAPEX, planejamento de P&D,
monitoramento de concorrentes, avaliação de tecnologias emergentes, preparação para reuniões de conselho,
construção de cenários setoriais, identificação de parcerias e pilotos.

Retorne EXATAMENTE este JSON:
{{
  "contexto": "2 frases conectando o ciclo do dia ao uso prático do Radar.",
  "aplicacoes": [
    {{
      "uso": "Nome da aplicação (3-5 palavras)",
      "como": "Como o Radar apoia este uso no contexto do ciclo atual. 1 frase específica."
    }}
  ]
}}
"""

    _FALLBACK = {
        "contexto": f"O Radar xTech de {HOJE_ISO} consolidou {xtech_dom} como frente dominante. Use a análise para orientar decisões de curto prazo.",
        "aplicacoes": [
            {"uso": "Revisão de riscos", "como": "Identifique os vetores em 'Mobilizar Agora' e avalie exposição da empresa."},
            {"uso": "Priorização de CAPEX", "como": "Alinhe investimentos com os vetores de maior pressão estratégica do ciclo."},
            {"uso": "Preparação de conselho", "como": "Use os cenários por xTech como base para apresentação executiva."},
            {"uso": "Monitoramento setorial", "como": "Acompanhe os gatilhos de confirmação dos cenários prospectivos."},
            {"uso": "Identificação de parceiros", "como": "Mapeie entidades no grafo de convergência para parcerias estratégicas."},
            {"uso": "Planejamento de P&D", "como": "Oriente projetos para as tecnologias na fase de Early Majority na curva de maturidade."},
        ]
    }

    try:
        texto = _chamar_sonnet(client, system, user, max_tokens=1500)
        resultado = json.loads(_reparar_json(_extrair_json(texto)))
        print(f"     [OK] {len(resultado.get('aplicacoes', []))} aplicações geradas.")
        return resultado
    except Exception as e:
        print(f"  [!] Erro aplicacoes_corporativas: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# 6.5.12 — Auditoria de enriquecimento
# ═══════════════════════════════════════════════════════════════════════════════

def _auditar_enriquecimento(output: dict) -> dict:
    """Detecta blocos de enriquecimento que retornaram fallback genérico.

    Critério degraded: >= 3 dos 7 blocos principais sem conteúdo real.
    Salvo em output['enrichment_audit'] para uso pelo detector de freshness
    do gerar_radar_xtechs_v10.py e pelo run_pipeline_v1.py.
    """
    fallback: list[str] = []

    # Hero: fallback se manchete vazia ou igual ao título bruto (não tem "Radar" ou tese)
    hero = output.get("hero") or {}
    if not hero.get("manchete") or not hero.get("briefing"):
        fallback.append("hero")

    # Impacto xTech: fallback se nenhuma frente tem sinal_referencia preenchido
    impacto = output.get("impacto_xtech") or {}
    if not any(
        (v.get("sinal_referencia") or "").strip() not in ("", "Impacto não avaliado neste ciclo.")
        for v in impacto.values()
    ):
        fallback.append("impacto_xtech")

    # Cenários: fallback se os títulos são genéricos do padrão _fallback_xtech
    cenarios = output.get("cenarios") or {}
    if not cenarios or all(
        (c.get("realista") or {}).get("titulo", "").endswith("em consolidação")
        for c in cenarios.values()
    ):
        fallback.append("cenarios_xtech")

    # CTA: fallback se tema_do_dia não foi gerado
    cta = output.get("cta") or {}
    if not cta.get("tema_do_dia") or not cta.get("cta_empresa"):
        fallback.append("cta")

    # Aplicações corporativas: fallback se contexto vazio
    aplic = output.get("aplicacoes_corporativas") or {}
    if not aplic.get("contexto") or len(aplic.get("aplicacoes") or []) < 4:
        fallback.append("aplicacoes_corporativas")

    # Lente de decisão: fallback se todos os cards têm sinal "—"
    lente = output.get("lente_decisao") or []
    if not lente or all(c.get("sinal", "—") in ("—", "", None) for c in lente):
        fallback.append("lente_decisao")

    # Sala de Situação: fallback se situacao vazia
    sala = (output.get("v31_horizonte1") or {}).get("sala_situacao_com_acao") or {}
    if not sala.get("situacao"):
        fallback.append("sala_situacao_scr")

    return {
        "fallback_count":      len(fallback),
        "fallback_components": fallback,
        "radar_quality":       "degraded" if len(fallback) >= 3 else "ok",
        "total_blocks":        7,
        "gerado_em":           datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 6.5 — Orquestrador do enriquecimento
# ═══════════════════════════════════════════════════════════════════════════════

def fase_65_enriquecimento_radar_xtech(client: anthropic.Anthropic, db_path: Path) -> None:
    """Lê intel_output.json, adiciona blocos Radar xTech e salva."""
    sep = "=" * 66
    print(f"\n{sep}")
    print("  [Fase 6.5] Enriquecimento Radar xTech")
    print(f"  {datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S')}")
    print(sep)

    if not os.path.exists(OUTPUT_FILE):
        raise FileNotFoundError(f"intel_output.json não encontrado: {OUTPUT_FILE}")

    with open(OUTPUT_FILE, encoding="utf-8") as f:
        output = json.load(f)

    t0 = time.time()

    # 6.5.1 hero
    print("  -> [6.5.1] Hero (LLM — título editorial + briefing)...")
    output["hero"] = gerar_hero(client, output)
    print("     [OK] Hero montado.")

    # 6.5.2 impacto_xtech
    output["impacto_xtech"] = gerar_impacto_xtech(client, output, db_path)

    # 6.5.3 graph_anchors
    output["graph_anchors"] = gerar_graph_anchors(client, output, db_path)

    # 6.5.4 fatos_duros
    print("  -> [6.5.4] Fatos duros (Python puro)...")
    evidencias = gerar_fatos_duros(output)
    output["evidencias_ciclo"] = evidencias
    output["fatos_duros"] = evidencias  # backward compat
    print(f"     [OK] {len(output['evidencias_ciclo'])} evidências do ciclo selecionadas.")

    # 6.5.5 cenarios por xTech
    output["cenarios"] = gerar_cenarios_xtech(client, output, db_path)

    # 6.5.6 convergencia
    print("  -> [6.5.6] Convergência (Python puro)...")
    output["convergencia"] = gerar_convergencia(output)
    print(f"     [OK] {len(output['convergencia'])} convergências mapeadas.")

    # 6.5.7 CTA
    output["cta"] = gerar_cta(client, output)

    # 6.5.8 score_badge nos vetores
    print("  -> [6.5.8] Score badge nos vetores (Python puro)...")
    for v in output.get("vetores_estrategicos", []):
        v["score_badge"] = gerar_score_badge(v)
    print(f"     [OK] Badges injetados em {len(output.get('vetores_estrategicos', []))} vetores.")

    # 6.5.9 Aplicações corporativas [NOVO]
    try:
        output["aplicacoes_corporativas"] = gerar_aplicacoes_corporativas(client, output)
    except Exception as e:
        print(f"  ⚠ [6.5.9] Aplicações corporativas falhou: {e}. Usando fallback vazio.")
        output["aplicacoes_corporativas"] = {"contexto": "", "aplicacoes": []}

    # 6.5.10 lente_decisao
    try:
        output["lente_decisao"] = gerar_lente_decisao(client, output)
    except Exception as e:
        print(f"  ⚠ [6.5.10] Lente de Decisão falhou: {e}. Usando fallback vazio.")
        output["lente_decisao"] = [
            {"perfil": p, "sinal": "—", "decisao": "—", "risco": "—"}
            for p in _LENTE_PERFIS
        ]

    # 6.5.11 snapshot histórico — DEVE ser o último passo (captura estado final do banco)
    print("  -> [6.5.11] Snapshot histórico (SQLite → intel_output.json)...")
    output["hist_data"] = gerar_snapshot_historico(db_path)
    n_mem = len(output["hist_data"].get("memories", []))
    n_zt  = len(output["hist_data"].get("zettels", []))
    n_ent = len(output["hist_data"].get("entities", []))
    print(f"     [OK] {n_mem} memórias, {n_zt} zettels, {n_ent} entidades embutidos.")

    # 6.5.13 MICMAC — variáveis estruturais + quadrantes Godet
    try:
        output["micmac"] = gerar_micmac(client, output)
        _mic = output["micmac"]
        _eixos = _mic.get("eixos_recomendados", {})
        print(
            f"     [OK] MICMAC: eixos recomendados → "
            f"{_eixos.get('eixo_x', {}).get('nome', '?')} × "
            f"{_eixos.get('eixo_y', {}).get('nome', '?')}"
        )
    except Exception as e:
        print(f"  ⚠ [6.5.13] MICMAC falhou: {e}. Pulando.")
        output["micmac"] = {"fallback": True, "ciclo": HOJE_ISO}

    # 6.5.14 MACTOR — posicionamento de atores por cenário
    try:
        output["mactor"] = gerar_mactor(client, output)
        _mac = output["mactor"]
        print(
            f"     [OK] MACTOR: {len(_mac.get('posicionamento_atores', []))} atores | "
            f"{len(_mac.get('aliancas', []))} alianças | "
            f"{len(_mac.get('conflitos', []))} conflitos"
        )
        # Enriquece cenários com resultado MACTOR
        if _mac.get("cenarios_xtech_enriquecidos"):
            output["cenarios"] = _mac["cenarios_xtech_enriquecidos"]
    except Exception as e:
        print(f"  ⚠ [6.5.14] MACTOR falhou: {e}. Pulando.")
        output["mactor"] = {"fallback": True, "ciclo": HOJE_ISO}

    # 6.5.12 Auditoria de enriquecimento — detecta blocos em fallback
    print("  -> [6.5.12] Auditoria de enriquecimento (Python puro)...")
    _enrichment_audit = _auditar_enriquecimento(output)
    output["enrichment_audit"] = _enrichment_audit
    _eq = _enrichment_audit["radar_quality"]
    _fc = _enrichment_audit["fallback_count"]
    if _eq == "degraded":
        print(
            f"  ⚠ Enriquecimento DEGRADADO: {_fc} blocos em fallback: "
            f"{', '.join(_enrichment_audit['fallback_components'])}"
        )
    else:
        print(f"     [OK] Enriquecimento: {_eq} ({_fc} fallbacks).")

    # Atualiza versão e salva
    output["versao"] = "v33-xtech-horizons-partial"  # fase 6.6 atualiza para v33-xtech-horizons
    output["gerado_em"] = datetime.now(timezone.utc).isoformat()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ intel_output.json enriquecido em {time.time() - t0:.1f}s")
    print(f"  ✓ Versão: {output['versao']}")
    print(f"  ✓ Blocos adicionados: hero, impacto_xtech, graph_anchors, evidencias_ciclo,")
    print(f"    cenarios v1.2, convergencia, cta, score_badge, aplicacoes_corporativas,")
    print(f"    lente_decisao (6 perfis), hist_data (snapshot SQLite autocontido)")
    print(f"  ✓ score_badge injetado em {len(output.get('vetores_estrategicos', []))} vetores")
    print(sep + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 6.6 — Enriquecimento Horizonte 1 (v31) [NOVO]
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_sala_situacao_com_acao(client: anthropic.Anthropic, output: dict) -> dict:
    """6.6.1 — Gera bloco SCR (Situação-Complicação-Resolução) para o Horizonte 1.

    Calcula urgência com base no maior IPS dos vetores estratégicos do ciclo.
    Retorna dict compatível com v31_horizonte1.sala_situacao_com_acao.
    """
    print("  -> [6.6.1] Sala de Situação com Ação (Sonnet — SCR Horizonte 1)...")

    HORIZONTE_ACAO_DIAS = 30

    # Calcula o maior IPS do ciclo para definir urgência
    ips_max = 0.0
    for v in output.get("vetores_estrategicos", []):
        try:
            ips = float(v.get("pressao_estrategica") or v.get("ips") or 0)
            if ips > ips_max:
                ips_max = ips
        except (ValueError, TypeError):
            pass
    # Fallback: tentar score nos itens
    if ips_max == 0.0:
        for item in output.get("itens", []):
            try:
                s = float(item.get("score_final") or 0)
                if s > ips_max:
                    ips_max = s
            except (ValueError, TypeError):
                pass

    if ips_max >= 8.0:
        urgencia_calc = "alta"
    elif ips_max >= 6.0:
        urgencia_calc = "media"
    else:
        urgencia_calc = "baixa"

    # Contexto resumido para o prompt
    ctx: dict = {
        "ciclo_id": output.get("ciclo_id") or output.get("gerado_em", "")[:10],
        "ips_maximo": round(ips_max, 2),
        "urgencia_calculada": urgencia_calc,
        "horizonte_acao_dias": HORIZONTE_ACAO_DIAS,
        "vetores_top3": [
            {
                "nome": v.get("nome") or v.get("tema") or "",
                "pressao": v.get("pressao_estrategica") or "",
                "quadrante": v.get("quadrante_executivo") or "",
            }
            for v in output.get("vetores_estrategicos", [])[:3]
        ],
        "fatos_duros": [
            f.get("fato") or f.get("titulo") or ""
            for f in (output.get("fatos_duros") or output.get("evidencias_ciclo") or [])[:5]
        ],
        "tese": (output.get("briefing_diario") or {}).get("tese_executiva") or
                (output.get("hero") or {}).get("manchete") or "",
    }

    system_prompt = (
        "Você é analista sênior do think tank efagundes.com. "
        "Responda APENAS com JSON válido, sem markdown, sem explicações."
    )

    user_prompt = f"""Com base nos sinais do ciclo atual do Radar xTech, gere o bloco SCR \
(Situação-Complicação-Resolução) em português brasileiro.

SITUAÇÃO (80–100 palavras): o que está acontecendo de mais relevante agora, \
com pelo menos um número concreto.

COMPLICAÇÃO (80–100 palavras): por que a situação não é simples — a tensão ou contradição \
que o executivo precisa entender.

AÇÃO RECOMENDADA (3 itens numerados, 20–30 palavras cada): o que um executivo deveria \
fazer nos próximos {HORIZONTE_ACAO_DIAS} dias. Comece cada item com verbo no infinitivo.

URGÊNCIA: use exatamente "{urgencia_calc}" (calculado com base no IPS máximo do ciclo: {ips_max:.2f}).

Dados do ciclo:
{json.dumps(ctx, ensure_ascii=False)}

Formato de saída (JSON puro, sem markdown):
{{
  "situacao": "...",
  "complicacao": "...",
  "acao_recomendada": ["1. ...", "2. ...", "3. ..."],
  "urgencia": "{urgencia_calc}",
  "horizonte_acao_dias": {HORIZONTE_ACAO_DIAS}
}}"""

    fallback = {
        "situacao": "",
        "complicacao": "",
        "acao_recomendada": [],
        "urgencia": urgencia_calc,
        "horizonte_acao_dias": HORIZONTE_ACAO_DIAS,
    }

    try:
        raw = _chamar_sonnet(client, system_prompt, user_prompt, max_tokens=1024)
        parsed = json.loads(_extrair_json(raw))
        # Garante urgência válida
        if parsed.get("urgencia") not in ("alta", "media", "baixa"):
            parsed["urgencia"] = urgencia_calc
        parsed.setdefault("horizonte_acao_dias", HORIZONTE_ACAO_DIAS)
        print(f"     [OK] SCR gerado — urgência: {parsed['urgencia']} (IPS máx: {ips_max:.2f})")
        return parsed
    except Exception as exc:
        print(f"  ⚠ [6.6.1] Falhou: {exc}. Usando fallback vazio.")
        return fallback


def fase_66_horizonte1(client: anthropic.Anthropic) -> None:
    """Lê intel_output.json, adiciona v31_horizonte1 e salva.

    Executada após fase_65_enriquecimento_radar_xtech — o arquivo já deve existir.
    """
    sep = "=" * 66
    print(f"\n{sep}")
    print("  [Fase 6.6] Enriquecimento Horizonte 1 — SCR + Ação")
    print(f"  {datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M:%S')}")
    print(sep)

    if not os.path.exists(OUTPUT_FILE):
        raise FileNotFoundError(f"intel_output.json não encontrado: {OUTPUT_FILE}")

    with open(OUTPUT_FILE, encoding="utf-8") as f:
        output = json.load(f)

    t0 = time.time()

    # 6.6.1 sala_situacao_com_acao
    sala = gerar_sala_situacao_com_acao(client, output)
    output.setdefault("v31_horizonte1", {})["sala_situacao_com_acao"] = sala

    # Atualiza versão e salva
    output["versao"] = "v33-xtech-horizons"
    output["gerado_em"] = datetime.now(timezone.utc).isoformat()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✓ intel_output.json atualizado em {time.time() - t0:.1f}s")
    print(f"  ✓ Versão: {output['versao']}")
    print(f"  ✓ Blocos adicionados: v31_horizonte1.sala_situacao_com_acao")
    print(sep + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# main — Passo A: analyzer_v30.py → Passo B: Fase 6.5 → Passo C: Fase 6.6
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    # Passo A — roda o analyzer base (v30 agentic loop)
    base_script = Path(__file__).parent / "analyzer_v30.py"
    if not base_script.exists():
        raise FileNotFoundError(f"analyzer_v30.py não encontrado: {base_script}")

    print("\n" + "=" * 66)
    print("  ANALYZER v33_agent — Radar xTech 1.2 (DB relativo ao container)")
    print("  Passo A: analyzer_v30.py (análise tática + vetores + briefing)")
    print("=" * 66)

    spec = importlib.util.spec_from_file_location("analyzer_v30_base", str(base_script))
    if spec is None or spec.loader is None:
        raise ImportError(f"Não foi possível carregar: {base_script}")
    mod = importlib.util.module_from_spec(spec)

    saved_argv = sys.argv[:]
    try:
        sys.argv = [str(base_script)]
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            mod.main()
    finally:
        sys.argv = saved_argv

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não encontrada no .env")
    client = anthropic.Anthropic(api_key=api_key)
