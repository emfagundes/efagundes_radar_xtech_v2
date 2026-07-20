#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scenario_tracker_v1.py — Rastreador de Cenários Prospectivos (Scenario Tracker)

A cada ciclo:
  1. Persiste um snapshot imutável de cada cenário emitido em cenarios_prospectivos
     (tabela scenario_snapshots).
  2. Avalia os cenários de ciclos anteriores contra a evidência do ciclo ATUAL —
     vetores_estrategicos, hero e fatos_canonicos — gravando o veredito em
     scenario_evaluations.
  3. Injeta o resumo auditável (Seção 8 da spec) em
     intel_output.json["scenario_tracking"].

Regra anti-viés inegociável: um cenário emitido no ciclo T só é avaliado com
evidência de ciclos >= T+1. Nunca usa dados do próprio ciclo de emissão
(ver evaluate_open_scenarios).

Gatilhos v1 — estratégia 6b (matching por keyword, spec Seção 6b):
  Palavras-chave do mecanismo causal (extraídas de narrativa_macro) são
  cruzadas, em ciclos posteriores, contra 3 fontes já coletadas pelo pipeline.
  Cada fonte é um "gatilho" sintético:
    - vetor_top3     nome de vetor estratégico no top-3 por pressão_estratégica
    - hero           hero.manchete + kicker + deck
    - fato_canonico  fatos_canonicos[].contexto + valor_literal
  Estratégia 6a (gatilhos gerados no prompt de cenários) fica para uma versão
  futura — o campo `gatilhos` já existe no schema e é persistido se presente
  em cenarios_prospectivos[].gatilhos, mas não é usado no matching desta v1.

Uso:
  python scenario_tracker_v1.py
  python scenario_tracker_v1.py --input intel_output.json --db ../db/intel.sqlite
  python scenario_tracker_v1.py --dry-run
  python scenario_tracker_v1.py --horizonte-dias 30
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

import scenario_calibration_v1 as calibration
import formation_curves_v1 as fc

BRASILIA = timezone(timedelta(hours=-3))

ROOT_DIR      = Path(__file__).resolve().parent
DEFAULT_DB    = Path.home() / "efagundes_intel" / "db" / "intel.sqlite"
DEFAULT_INPUT = ROOT_DIR / "intel_output.json"

HORIZONTE_DIAS_DEFAULT = 30
LIMIAR_CONFIRMACAO     = 0.66

ENQUADRAMENTO = (
    "Cenário prospectivo não é previsão pontual — é preparação estruturada com "
    "probabilidade revisável (definição de Godet/OCDE). A métrica que importa "
    "é antecedência e calibração, não \"% de futuros adivinhados\"."
)

DDL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS scenario_snapshots (
    scenario_uid            TEXT PRIMARY KEY,
    data_emissao             TEXT NOT NULL,
    titulo_cenario           TEXT NOT NULL,
    tipo                     TEXT,
    probabilidade_inicial    INTEGER,
    impacto                  TEXT,
    pos_x                    REAL,
    pos_y                    REAL,
    eixo_x_nome              TEXT,
    eixo_y_nome              TEXT,
    gatilhos                 TEXT,
    mecanismo_kws            TEXT,
    horizonte_dias           INTEGER DEFAULT 30,
    criado_em                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

DDL_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS scenario_evaluations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_uid        TEXT NOT NULL,
    data_avaliacao      TEXT NOT NULL,
    gatilhos_acionados  TEXT,
    fracao_confirmada   REAL,
    evidencia           TEXT,
    status              TEXT NOT NULL,
    criado_em           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scenario_uid, data_avaliacao),
    FOREIGN KEY(scenario_uid) REFERENCES scenario_snapshots(scenario_uid)
)
"""

DDL_INDEX = "CREATE INDEX IF NOT EXISTS idx_scenario_eval_uid ON scenario_evaluations(scenario_uid)"


# ─── Schema ────────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_SNAPSHOTS)
    conn.execute(DDL_EVALUATIONS)
    conn.execute(DDL_INDEX)
    conn.commit()
    calibration.ensure_calibration_schema(conn)


# ─── Extração de palavras-chave do mecanismo causal ────────────────────────

STOPWORDS_PT: set[str] = {
    # gramaticais / conectores
    "para", "como", "mais", "muito", "também", "ainda", "entre", "sobre",
    "esse", "essa", "esses", "essas", "este", "esta", "estes", "estas",
    "isso", "isto", "aquele", "aquela", "aqueles", "aquelas", "seu", "sua",
    "seus", "suas", "ele", "ela", "eles", "elas", "mas", "porque", "quando",
    "onde", "enquanto", "assim", "tanto", "apenas", "outro", "outra",
    "outros", "outras", "novo", "nova", "novos", "novas", "dessa", "desse",
    "nessa", "nesse", "pela", "pelo", "pelas", "pelos", "diante", "frente",
    "meio", "forma", "modo", "parte", "caso", "vez", "vezes", "dado", "dada",
    "vem", "vêm", "neste", "nesta", "sendo", "estar", "haver", "houve",
    "havia", "ter", "tem", "têm", "são", "foi", "ser", "que", "com", "não",
    "uma", "uns", "umas", "das", "dos", "nos", "nas", "aos", "à", "às", "é",
    "já", "até", "pode", "podem", "cada", "sob", "após", "antes", "durante",
    "dentro", "fora", "atr", "todo", "toda", "todos", "todas", "algum",
    "alguma", "alguns", "algumas", "nenhum", "nenhuma", "qual", "quais",
    "quanto", "quanta", "quantos", "quantas",
    # verbos genéricos de narrativa prospectiva — aparecem em quase qualquer
    # cenário, independente do mecanismo causal específico
    "tende", "tendem", "gera", "gerar", "gerando", "geram", "cria", "criar",
    "criando", "criam", "abre", "abrir", "abrindo", "abrem", "torna",
    "tornam", "tornando", "permite", "permitem", "exige", "exigem", "segue",
    "seguem", "mantém", "manter", "consolida", "consolidam", "elevando",
    "elevam", "eleva", "aprofunda", "aprofundam", "persiste", "persistem",
    "avança", "avançam", "agrava", "agravam", "intensifica", "intensificam",
    "acelera", "aceleram", "reduz", "reduzem", "aumenta", "aumentam",
    "compromete", "comprometem", "amplia", "ampliam", "reforça", "reforçam",
    "sustenta", "sustentam", "projeta", "projetam", "aponta", "apontam",
    "indica", "indicam", "revela", "revelam", "mostra", "mostram",
    "sinaliza", "sinalizam", "comprimindo", "pressiona", "pressionam",
    "impacta", "impactam", "afeta", "afetam", "expõe", "expõem", "resulta",
    "resultam", "decorre", "decorrem", "convive", "convivem", "enfrenta",
    "enfrentam", "adiam", "adia", "atrasa", "atrasam", "acentua", "acentuam",
    # substantivos/adjetivos genéricos — descrevem qualquer cenário, não um
    # mecanismo causal específico
    "cenário", "cenários", "brasil", "ambiente", "processo", "processos",
    "severo", "severa", "críticos", "crítico", "crítica", "críticas",
    "relevante", "relevantes", "significativo", "significativa",
    "significativos", "significativas", "importante", "importantes",
    "principal", "principais", "especialmente", "particularmente",
    "sobretudo", "notadamente", "sistema", "sistêmico", "sistêmica",
    "sistemas", "nacional", "nacionais", "internacional", "internacionais",
    "global", "globais", "geral", "gerais", "atual", "atuais", "atualmente",
    "recente", "recentes", "recentemente", "próximo", "próximos", "próxima",
    "próximas", "curto", "longo", "patamar", "patamares", "nível", "níveis",
    "contexto", "contextos", "condição", "condições", "situação",
    "situações", "momento", "momentos", "período", "períodos", "fase",
    "fases", "etapa", "etapas", "aspecto", "aspectos", "elemento",
    "elementos", "fator", "fatores", "ponto", "pontos", "questão",
    "questões", "tema", "temas", "área", "áreas", "país", "países", "mundo",
    "mundial", "mundiais", "brasileiro", "brasileira", "brasileiros",
    "brasileiras", "medida", "medidas", "ação", "ações", "decisão",
    "decisões", "impacto", "impactos", "tendência", "tendências",
    "capacidade", "capacidades", "necessidade", "necessidades", "conjunto",
    "conjuntos", "combinação", "conforme", "diversos", "diversas", "certa",
    "certo", "certos", "certas", "possível", "possíveis", "provável",
    "prováveis",
}

_PALAVRA_RE = re.compile(r"[a-zà-öø-ÿ]{4,}", re.IGNORECASE)

# Nº mínimo de keywords distintas que precisam bater numa fonte para o
# gatilho ser considerado acionado. Uma única palavra genérica coincidindo
# (ex: "sistema") não deve, sozinha, confirmar um cenário — exigir >=2
# reduz drasticamente falso-positivo por ruído lexical (validado no
# backfill: 1 hit gerava confirmação em ~85% dos cenários em ~3 dias).
MIN_HITS_POR_GATILHO = 2


def extrair_mecanismo_kws(texto: str, max_kws: int = 15) -> list[str]:
    """Extrai palavras-chave significativas de narrativa_macro (fallback 6b,
    mais grosseiro que gatilhos gerados no prompt — spec Seção 6)."""
    if not texto:
        return []
    palavras = _PALAVRA_RE.findall(texto.lower())
    vistas: set[str] = set()
    kws: list[str] = []
    for p in palavras:
        if p in STOPWORDS_PT or p in vistas:
            continue
        vistas.add(p)
        kws.append(p)
        if len(kws) >= max_kws:
            break
    return kws


# ─── Matching 6b — 3 gatilhos sintéticos ───────────────────────────────────

def _top3_vetores(vetores_estrategicos: list[dict]) -> list[dict]:
    return sorted(
        vetores_estrategicos or [],
        key=lambda v: float(v.get("pressao_estrategica") or 0),
        reverse=True,
    )[:3]


def checar_gatilhos_ciclo(
    mecanismo_kws: list[str],
    vetores_estrategicos: list[dict],
    hero: dict,
    fatos_canonicos: list[dict],
) -> tuple[dict, list[str], float, dict]:
    """Cruza mecanismo_kws contra as 3 fontes do ciclo atual. Retorna
    (gatilhos, acionados, fracao, evidencia)."""
    kws_set = set(mecanismo_kws or [])
    top3 = _top3_vetores(vetores_estrategicos)

    texto_vetores = " ".join((v.get("nome") or "") for v in top3).lower()
    hero = hero or {}
    texto_hero = " ".join([
        hero.get("manchete") or "",
        hero.get("kicker") or "",
        hero.get("deck") or "",
    ]).lower()
    texto_fatos = " ".join(
        f"{f.get('contexto', '')} {f.get('valor_literal', '')}"
        for f in (fatos_canonicos or [])
    ).lower()

    def _hits(texto: str) -> list[str]:
        return sorted(
            kw for kw in kws_set
            if re.search(rf"\b{re.escape(kw)}\b", texto, flags=re.UNICODE)
        )

    hits_vetores = _hits(texto_vetores)
    hits_hero    = _hits(texto_hero)
    hits_fatos   = _hits(texto_fatos)

    gatilhos = {
        "vetor_top3":    {"acionado": len(hits_vetores) >= MIN_HITS_POR_GATILHO,
                           "keywords": hits_vetores,
                           "fonte": "vetores_estrategicos[top3].nome"},
        "hero":          {"acionado": len(hits_hero) >= MIN_HITS_POR_GATILHO,
                           "keywords": hits_hero,
                           "fonte": "hero.manchete/kicker/deck"},
        "fato_canonico": {"acionado": len(hits_fatos) >= MIN_HITS_POR_GATILHO,
                           "keywords": hits_fatos,
                           "fonte": "fatos_canonicos[].contexto/valor_literal"},
    }
    acionados = [k for k, v in gatilhos.items() if v["acionado"]]
    fracao = len(acionados) / 3.0

    evidencia: dict[str, Any] = {}
    if hits_vetores:
        evidencia["vetor_top3"] = [
            {"id": v.get("id"), "nome": v.get("nome"),
             "pressao_estrategica": v.get("pressao_estrategica")}
            for v in top3 if any(kw in (v.get("nome") or "").lower() for kw in hits_vetores)
        ]
    if hits_hero:
        evidencia["hero"] = {"keywords": hits_hero}
    if hits_fatos:
        evidencia["fato_canonico"] = [
            {"sinal_id": f.get("sinal_id"), "tipo": f.get("tipo")}
            for f in (fatos_canonicos or [])
            if any(kw in f"{f.get('contexto', '')} {f.get('valor_literal', '')}".lower() for kw in hits_fatos)
        ]

    return gatilhos, acionados, fracao, evidencia


# ─── Máquina de estados (spec Seção 7) ─────────────────────────────────────

def determinar_status(fracao: float, dias_desde_emissao: int, horizonte_dias: int) -> str:
    if fracao >= LIMIAR_CONFIRMACAO:
        return "confirmado"
    if dias_desde_emissao > horizonte_dias:
        return "nao_materializado"
    if fracao > 0:
        return "em_formacao"
    return "em_aberto"


def _parse_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


# ─── Persistência de snapshots ─────────────────────────────────────────────

def scenario_uid(ciclo_id: str, cenario_id: Any) -> str:
    return f"{ciclo_id}_{cenario_id}"


def persist_snapshots(
    conn: sqlite3.Connection,
    cenarios_prospectivos: list[dict],
    matriz_incertezas: dict,
    ciclo_id: str,
    horizonte_dias: int = HORIZONTE_DIAS_DEFAULT,
    funcao_recalibracao: dict | None = None,
) -> int:
    """funcao_recalibracao (opcional) é a função derivada num ciclo ANTERIOR
    por scenario_calibration_v1 — nunca a do próprio ciclo_id sendo
    persistido agora (essa só existe depois, calculada sobre avaliações que
    ainda vão rodar). probabilidade_bruta grava o que o modelo gerou;
    probabilidade_inicial grava o valor calibrado exibido (igual à bruta
    quando ainda não há função de recalibração disponível)."""
    matriz_incertezas = matriz_incertezas or {}
    eixo_x_nome = (matriz_incertezas.get("eixo_x") or {}).get("nome", "")
    eixo_y_nome = (matriz_incertezas.get("eixo_y") or {}).get("nome", "")

    inseridos = 0
    for i, c in enumerate(cenarios_prospectivos or []):
        cid = c.get("id") or c.get("numero") or (i + 1)
        uid = scenario_uid(ciclo_id, cid)
        mecanismo_kws = extrair_mecanismo_kws(c.get("narrativa_macro", ""))
        prob_bruta = int(c.get("probabilidade") or 0)
        prob_calibrada = calibration.aplicar_calibracao(prob_bruta, funcao_recalibracao)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO scenario_snapshots
              (scenario_uid, data_emissao, titulo_cenario, tipo, probabilidade_inicial,
               probabilidade_bruta, impacto, pos_x, pos_y, eixo_x_nome, eixo_y_nome,
               gatilhos, mecanismo_kws, horizonte_dias)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uid, ciclo_id, c.get("titulo_cenario", ""), c.get("tipo", "Misto"),
                prob_calibrada, prob_bruta, c.get("impacto", "Alto"),
                float(c.get("pos_x") or 0.5), float(c.get("pos_y") or 0.5),
                eixo_x_nome, eixo_y_nome,
                json.dumps(c.get("gatilhos") or [], ensure_ascii=False),
                json.dumps(mecanismo_kws, ensure_ascii=False),
                int(horizonte_dias),
            ),
        )
        if cur.rowcount:
            inseridos += 1
    conn.commit()
    return inseridos


# ─── Avaliação de cenários abertos ─────────────────────────────────────────

def evaluate_open_scenarios(
    conn: sqlite3.Connection,
    ciclo_atual_id: str,
    vetores_estrategicos: list[dict],
    hero: dict,
    fatos_canonicos: list[dict],
) -> int:
    """Avalia todo scenario_snapshot ainda não-terminal contra a evidência do
    ciclo atual. Regra anti-viés: pula qualquer snapshot cuja data_emissao seja
    >= ciclo_atual_id (nunca avalia um cenário com evidência do próprio ciclo
    de emissão ou de ciclos anteriores a ele)."""
    data_atual = _parse_date(ciclo_atual_id)

    rows = conn.execute(
        "SELECT scenario_uid, data_emissao, mecanismo_kws, horizonte_dias FROM scenario_snapshots"
    ).fetchall()

    avaliados = 0
    for uid, data_emissao, mecanismo_kws_json, horizonte_dias in rows:
        data_emi = _parse_date(data_emissao)
        if data_atual <= data_emi:
            continue  # regra anti-viés — nunca avaliar com evidência <= data_emissao

        ultimo = conn.execute(
            """SELECT status FROM scenario_evaluations
               WHERE scenario_uid=? ORDER BY data_avaliacao DESC LIMIT 1""",
            (uid,),
        ).fetchone()
        if ultimo and ultimo[0] in ("confirmado", "nao_materializado"):
            continue  # estado terminal — congelado

        ja_avaliado = conn.execute(
            "SELECT 1 FROM scenario_evaluations WHERE scenario_uid=? AND data_avaliacao=?",
            (uid, ciclo_atual_id),
        ).fetchone()
        if ja_avaliado:
            continue  # idempotência — já avaliado neste ciclo

        mecanismo_kws = json.loads(mecanismo_kws_json or "[]")
        gatilhos, acionados, fracao, evidencia = checar_gatilhos_ciclo(
            mecanismo_kws, vetores_estrategicos, hero, fatos_canonicos
        )
        dias_desde_emissao = (data_atual - data_emi).days
        status = determinar_status(fracao, dias_desde_emissao, horizonte_dias)

        conn.execute(
            """INSERT INTO scenario_evaluations
               (scenario_uid, data_avaliacao, gatilhos_acionados, fracao_confirmada, evidencia, status)
               VALUES (?,?,?,?,?,?)""",
            (
                uid, ciclo_atual_id, json.dumps(acionados, ensure_ascii=False),
                round(fracao, 4), json.dumps(evidencia, ensure_ascii=False), status,
            ),
        )
        avaliados += 1

    conn.commit()
    return avaliados


# ─── Resumo auditável (spec Seção 8) ───────────────────────────────────────

def build_scenario_tracking_summary(conn: sqlite3.Connection) -> dict:
    snaps = conn.execute(
        "SELECT scenario_uid, titulo_cenario, tipo, probabilidade_inicial, data_emissao "
        "FROM scenario_snapshots"
    ).fetchall()

    latest_eval: dict[str, tuple[str, str]] = {}
    for uid, status, data_avaliacao in conn.execute(
        "SELECT scenario_uid, status, data_avaliacao FROM scenario_evaluations "
        "ORDER BY data_avaliacao ASC"
    ):
        latest_eval[uid] = (status, data_avaliacao)  # última linha (ASC) vence

    por_tipo: dict[str, dict[str, int]] = {}
    por_estado = {"em_aberto": 0, "em_formacao": 0, "confirmado": 0, "nao_materializado": 0}
    antecedencias: list[int] = []
    nao_materializados: list[dict] = []
    confirmados_alta = confirmados_baixa = total_alta = total_baixa = 0

    # Corte "alta vs. baixa probabilidade" pela MEDIANA das probabilidades já
    # emitidas, não por um limiar fixo (ex: 50) — os 3 cenários de cada ciclo
    # somam <= 100%, então probabilidade_inicial raramente passa de ~40 neste
    # produto. Um corte fixo em 50 deixaria o bucket "alta" sempre vazio.
    probs = sorted((s[3] or 0) for s in snaps)
    if probs:
        meio = len(probs) // 2
        mediana = probs[meio] if len(probs) % 2 else (probs[meio - 1] + probs[meio]) / 2
    else:
        mediana = 0

    for uid, titulo, tipo, prob_inicial, data_emissao in snaps:
        tipo = tipo or "Misto"
        por_tipo.setdefault(tipo, {"emitidos": 0, "confirmados": 0})
        por_tipo[tipo]["emitidos"] += 1

        estado = "em_aberto"
        if uid in latest_eval:
            status, data_aval = latest_eval[uid]
            estado = status
            if status == "confirmado":
                por_tipo[tipo]["confirmados"] += 1
                antecedencias.append((_parse_date(data_aval) - _parse_date(data_emissao)).days)
            elif status == "nao_materializado":
                nao_materializados.append({
                    "scenario_uid": uid, "titulo_cenario": titulo, "tipo": tipo,
                    "probabilidade_inicial": prob_inicial, "data_emissao": data_emissao,
                })
        por_estado[estado] = por_estado.get(estado, 0) + 1

        alta = (prob_inicial or 0) >= mediana
        if alta:
            total_alta += 1
            if estado == "confirmado":
                confirmados_alta += 1
        else:
            total_baixa += 1
            if estado == "confirmado":
                confirmados_baixa += 1

    dias_medios = round(sum(antecedencias) / len(antecedencias), 1) if antecedencias else None

    return {
        "enquadramento": ENQUADRAMENTO,
        "total_emitidos": len(snaps),
        "por_tipo": por_tipo,
        "por_estado": por_estado,
        "dias_medios_antecedencia_confirmados": dias_medios,
        "calibracao": {
            "corte_mediana_probabilidade_inicial": mediana,
            "taxa_confirmacao_alta_probabilidade": round(confirmados_alta / total_alta, 3) if total_alta else None,
            "taxa_confirmacao_baixa_probabilidade": round(confirmados_baixa / total_baixa, 3) if total_baixa else None,
            "n_alta_probabilidade": total_alta,
            "n_baixa_probabilidade": total_baixa,
        },
        "nao_materializados": nao_materializados,
        "atualizado_em": datetime.now(BRASILIA).isoformat(timespec="seconds"),
    }


# ─── Track Record — curvas de formação dos cenários resolvidos ────────────

N_TRACK_RECORD_DESTAQUE = 4  # decisão do cliente — dose acionável, não os 35


def _resumir_evidencia(evidencia_json: str | None) -> str:
    """Resumo textual curto de qual(is) gatilho(s) confirmaram o cenário —
    mesma evidência já auditável em scenario_evaluations.evidencia, só
    tornada legível para o card do Track Record."""
    if not evidencia_json:
        return ""
    try:
        ev = json.loads(evidencia_json)
    except Exception:
        return ""
    fontes = []
    if ev.get("vetor_top3"):
        fontes.append("vetor estratégico no top-3")
    if ev.get("hero"):
        fontes.append("manchete do ciclo")
    if ev.get("fato_canonico"):
        fontes.append("fato canônico registrado")
    if not fontes:
        return ""
    return "Confirmado via " + ", ".join(fontes) + "."


_TR_BUFFER_SEMANAS_ANTES = 3
_TR_MIN_SEMANAS_JANELA   = 8
_TR_MAX_SEMANAS_JANELA   = 16


def _janela_relativa_ao_cenario(data_emissao: str, data_referencia: str) -> tuple[str, int]:
    """Fim + nº de semanas de uma curva ANCORADA no ciclo de vida do cenário
    (emissão -> desfecho), não no fim do banco. Sem isso, um cenário emitido
    há poucas semanas tem a maior parte de uma janela fixa de 16 semanas
    ocupada por volume de meses antes de ele sequer existir — dominando
    visualmente a curva sem relação com o cenário (achado ao validar contra
    dado real: curvas caindo desde o início em vez de subir até a
    confirmação, porque a maior parte da janela era história irrelevante)."""
    emi = _parse_date(data_emissao)
    ref = _parse_date(data_referencia)
    dias = (ref - emi).days + _TR_BUFFER_SEMANAS_ANTES * 7
    semanas = max(_TR_MIN_SEMANAS_JANELA, min(_TR_MAX_SEMANAS_JANELA, (dias // 7) + 1))
    return data_referencia, semanas


def build_track_record(conn: sqlite3.Connection, ciclo_id: str) -> list[dict]:
    """Um item por cenário com desfecho terminal (confirmado ou
    nao_materializado) — não só os 4 de destaque: a lista completa é o que
    sustenta o "ver todos" no template. Cada item traz a curva de formação
    semanal (None se o volume de sinais correspondentes não passar o piso —
    nunca uma curva fabricada). `destaque=True` marca os N_TRACK_RECORD_DESTAQUE
    escolhidos por maior dias_antecedencia entre os CONFIRMADOS com curva
    válida (curadoria da Seção — curva nítida > curva ruidosa)."""
    rows = conn.execute(
        """
        SELECT ss.scenario_uid, ss.titulo_cenario, ss.tipo, ss.data_emissao,
               ss.mecanismo_kws, se.data_avaliacao, se.status, se.evidencia
        FROM scenario_snapshots ss
        JOIN scenario_evaluations se ON se.scenario_uid = ss.scenario_uid
        WHERE se.status IN ('confirmado', 'nao_materializado')
        ORDER BY ss.data_emissao ASC
        """
    ).fetchall()

    itens = []
    for uid, titulo, tipo, data_emissao, kws_json, data_avaliacao, status, evidencia in rows:
        kws = json.loads(kws_json or "[]")
        dias_antecedencia = (_parse_date(data_avaliacao) - _parse_date(data_emissao)).days

        # Curva ancorada no ciclo de vida do PRÓPRIO cenário (emissão -> desfecho
        # + margem), não numa janela fixa de 16 semanas terminando hoje.
        data_fim_curva = data_avaliacao if status == "confirmado" else ciclo_id
        fim_curva, semanas_curva = _janela_relativa_ao_cenario(data_emissao, data_fim_curva)
        curva = fc.curva_formacao_semanal(conn, kws, fim_curva, semanas=semanas_curva)

        item = {
            "scenario_uid": uid,
            "titulo": titulo,
            "tipo": tipo or "Misto",
            "status": status,
            "data_emissao": data_emissao,
            "data_confirmacao": data_avaliacao if status == "confirmado" else None,
            "dias_antecedencia": dias_antecedencia,
            "curva_formacao": curva,
            "idx_emissao": fc.indice_semana_para_data(fim_curva, semanas_curva, data_emissao) if curva else None,
            "idx_confirmacao": (
                fc.indice_semana_para_data(fim_curva, semanas_curva, data_avaliacao)
                if curva and status == "confirmado" else None
            ),
            "evidencia_resumo": _resumir_evidencia(evidencia),
            "destaque": False,
        }
        itens.append(item)

    # Curadoria dos destaques: só confirmados com curva válida (nítida),
    # maior dias_antecedencia primeiro — "preferir os confirmados de maior
    # antecedência, que tendem a ter formação mais nítida".
    candidatos_destaque = [
        it for it in itens if it["status"] == "confirmado" and it["curva_formacao"] is not None
    ]
    candidatos_destaque.sort(key=lambda it: it["dias_antecedencia"], reverse=True)
    for it in candidatos_destaque[:N_TRACK_RECORD_DESTAQUE]:
        it["destaque"] = True

    return itens


# ─── Cenários ainda em formação (Horizonte 1) ──────────────────────────────

N_EM_FORMACAO_DESTAQUE = 6


def build_scenarios_em_formacao(conn: sqlite3.Connection, ciclo_id: str, limite: int = N_EM_FORMACAO_DESTAQUE) -> list[dict]:
    """Cenários ainda ATIVOS — status atual em_aberto ou em_formacao, nunca
    resolvidos. Para o H1 (o que está acontecendo AGORA): só o marcador de
    emissão (âmbar) faz sentido aqui, nunca o de confirmação — ainda não
    aconteceu. O arco completo emissão→confirmação (os dois marcadores)
    é conteúdo de auditoria retrospectiva e mora no Track Record (H2),
    não no estado atual do H1."""
    latest: dict[str, tuple[str, float, str]] = {}
    for uid, status, fracao, data_avaliacao in conn.execute(
        "SELECT scenario_uid, status, fracao_confirmada, data_avaliacao FROM scenario_evaluations "
        "ORDER BY data_avaliacao ASC"
    ):
        latest[uid] = (status, fracao, data_avaliacao)  # última linha (ASC) vence

    rows = conn.execute(
        "SELECT scenario_uid, titulo_cenario, tipo, data_emissao, mecanismo_kws FROM scenario_snapshots"
    ).fetchall()

    itens = []
    for uid, titulo, tipo, data_emissao, kws_json in rows:
        st = latest.get(uid)
        if not st or st[0] not in ("em_aberto", "em_formacao"):
            continue
        status, fracao, _data_avaliacao = st
        kws = json.loads(kws_json or "[]")
        fim_curva, semanas_curva = _janela_relativa_ao_cenario(data_emissao, ciclo_id)
        curva = fc.curva_formacao_semanal(conn, kws, fim_curva, semanas=semanas_curva)
        itens.append({
            "scenario_uid": uid,
            "titulo": titulo,
            "tipo": tipo or "Misto",
            "status": status,
            "data_emissao": data_emissao,
            "data_confirmacao": None,
            "dias_em_formacao": (_parse_date(ciclo_id) - _parse_date(data_emissao)).days,
            "fracao_confirmada": fracao or 0.0,
            "curva_formacao": curva,
            "idx_emissao": fc.indice_semana_para_data(fim_curva, semanas_curva, data_emissao) if curva else None,
            "idx_confirmacao": None,  # nunca — cenário ainda não confirmado
        })

    # Curadoria: prioriza quem já mostra alguma fração de gatilhos acionados
    # (sinal real de movimento em direção à confirmação) sobre quem ainda
    # está em zero; empate por mais tempo acumulando sinal.
    candidatos = [it for it in itens if it["curva_formacao"] is not None]
    candidatos.sort(key=lambda it: (it["fracao_confirmada"], it["dias_em_formacao"]), reverse=True)
    return candidatos[:limite]


# ─── Curva de formação do vetor #1 (Horizonte 1) ───────────────────────────

def _sort_vetores_prioridade(vetores: list[dict]) -> list[dict]:
    """Deve espelhar sort_vetores_for_priority em gerar_radar_xtechs_v11.py —
    mesma ordem que decide qual vetor aparece como #1 na Sala de Situação.
    Duplicado (não importado do template) por direção de dependência: o
    pipeline não deve depender do renderer."""
    quad_rank = {"Mobilizar Agora": 4, "Capturar Vantagem": 3, "Monitorar Vetores": 2, "Ruído Operacional": 1}
    return sorted(
        vetores,
        key=lambda v: (
            quad_rank.get(v.get("quadrante_executivo", ""), 0),
            float(v.get("pressao_estrategica") or 0),
            -int(v.get("janela_decisoria_dias") or 999),
            float(v.get("intensidade_momento") or 0),
        ),
        reverse=True,
    )


def aplicar_curva_formacao_vetor1(conn: sqlite3.Connection, intel: dict, ciclo_id: str) -> None:
    """Mudança 2 — embute curva_formacao no vetor #1 (o mesmo já exibido em
    destaque na Sala de Situação, não vetores_estrategicos[0] bruto: o
    template reordena por prioridade executiva antes de exibir). Fallback
    para o tema de maior saliência do ciclo (mesmo funil do
    acceleration_alerts_v1) se a série do vetor #1 não passar o piso de
    volume ou for muito plana; sem isso também, omite graciosamente (sem
    gravar o campo) — nunca uma curva vazia/enganosa."""
    vetores = intel.get("vetores_estrategicos") or []
    if not vetores:
        return
    vetor1 = _sort_vetores_prioridade(vetores)[0]

    kws = extrair_mecanismo_kws(f"{vetor1.get('nome', '')} {vetor1.get('mecanismo_causal', '')}")
    curva = fc.curva_formacao_semanal(conn, kws, ciclo_id)
    titulo = vetor1.get("nome", "")
    origem = "vetor"

    if fc.curva_e_plana(curva):
        try:
            import acceleration_alerts_v1 as aa
            candidatos = aa.calcular_saliencia_temas(conn, ciclo_id)
        except Exception:
            candidatos = []
        if candidatos:
            top_tema = candidatos[0]["theme"]
            curva_fallback = fc.curva_formacao_semanal_por_tema(conn, top_tema, ciclo_id)
            if not fc.curva_e_plana(curva_fallback):
                curva, titulo, origem = curva_fallback, top_tema, "tema_saliente"

    if curva is None:
        return  # nada usável nem no vetor #1 nem no fallback — omite, nunca curva vazia

    vetor1["curva_formacao"] = curva
    vetor1["curva_formacao_titulo"] = titulo
    vetor1["curva_formacao_origem"] = origem


# ─── Entrypoint (usado pelo pipeline e pelo CLI) ───────────────────────────

def run_scenario_tracker(
    intel_path: Path,
    db_path: Path,
    dry_run: bool = False,
    horizonte_dias_default: int = HORIZONTE_DIAS_DEFAULT,
) -> dict:
    intel = json.loads(intel_path.read_text(encoding="utf-8"))
    ciclo_id = intel.get("ciclo_id") or datetime.now(BRASILIA).strftime("%Y-%m-%d")
    cenarios = intel.get("cenarios_prospectivos") or []
    matriz   = intel.get("matriz_incertezas") or {}
    vetores  = intel.get("vetores_estrategicos") or []
    hero     = intel.get("hero") or {}
    fatos    = intel.get("fatos_canonicos") or []

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        ensure_schema(conn)
        if dry_run:
            print(f"  [dry-run] {len(cenarios)} cenário(s) seriam persistidos para o ciclo {ciclo_id}")
        else:
            # A função de recalibração usada aqui é sempre de um ciclo ANTERIOR
            # (carregar_ultima_funcao_recalibracao exclui o ciclo_id atual) —
            # nenhum cenário é calibrado com dado do próprio ciclo em que nasce.
            funcao_recal = calibration.carregar_ultima_funcao_recalibracao(conn, ciclo_id)
            n_snap = persist_snapshots(conn, cenarios, matriz, ciclo_id, horizonte_dias_default,
                                        funcao_recalibracao=funcao_recal)
            print(f"  ✓ scenario_snapshots — {n_snap} novo(s) cenário(s) (ciclo {ciclo_id})")
            n_eval = evaluate_open_scenarios(conn, ciclo_id, vetores, hero, fatos)
            print(f"  ✓ scenario_evaluations — {n_eval} avaliação(ões) registrada(s)")

            calib = calibration.run_calibration_cycle(conn, ciclo_id)
            ece_str = f"{calib['ece']:.3f}" if calib["ece"] is not None else "n/d"
            sep_str = f"{calib['separacao']:.3f}" if calib["separacao"] is not None else "n/d"
            print(f"  ✓ calibration_history — ECE={ece_str} separação={sep_str} "
                  f"(n={calib['n_cenarios_avaliados']})")

        tracking = build_scenario_tracking_summary(conn)
        tracking["auto_calibracao"] = calibration.build_auto_calibracao_summary(conn)
        tracking["track_record"] = build_track_record(conn, ciclo_id)
        tracking["em_formacao"] = build_scenarios_em_formacao(conn, ciclo_id)
        if not dry_run:
            aplicar_curva_formacao_vetor1(conn, intel, ciclo_id)
    finally:
        conn.close()

    if not dry_run:
        intel["scenario_tracking"] = tracking
        intel_path.write_text(json.dumps(intel, ensure_ascii=False, indent=2), encoding="utf-8")

    return tracking


# ─── CLI ────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rastreador de Cenários Prospectivos — snapshot imutável + avaliação por ciclo."
    )
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--horizonte-dias", type=int, default=HORIZONTE_DIAS_DEFAULT)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    tracking = run_scenario_tracker(
        Path(args.input), Path(args.db),
        dry_run=args.dry_run, horizonte_dias_default=args.horizonte_dias,
    )
    print(
        f"  · scenario_tracking: {tracking['total_emitidos']} emitidos, "
        f"{tracking['por_estado'].get('confirmado', 0)} confirmados, "
        f"{tracking['por_estado'].get('nao_materializado', 0)} não-materializados"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
