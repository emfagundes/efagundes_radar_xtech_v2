#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyzer_v30_agent.py — Efagundes Intelligence Engine | Radar Estratégico 9.3 — Agentic Loop

v30 (sobre v29):
  ARQUITETURA AGENTE — substitui orquestração imperativa por loop autônomo com
  auto-validação embutida. Interface idêntica ao v29:
    Entrada:  feed_limpo.json
    Saída:    intel_output.json
  run_pipeline_v23.py não requer nenhuma alteração.

  MUDANÇAS PRINCIPAIS:
  1. Classe RadarAgent encapsula estado completo do ciclo (substitui variáveis soltas no main).
  2. Loop de retry autônomo (MAX_RETRIES=2) para fases críticas 3, 4 e 6.
  3. Advisor checks via Opus em dois checkpoints:
       - Pós-Fase 3 (vetores): valida coerência e completude dos vetores.
       - Pós-Fase 4 (briefing): valida alinhamento briefing↔vetores dominantes.
  4. Fase 5.5 (auditoria Python) mantida — agente absorve os logs e decide
     se refaz a Fase 3.5 ou aceita com ajuste.
  5. Fallback automático: se advisor rejeitar e retries esgotarem, agente
     registra degradation_log e prossegue com melhor resultado disponível.
  6. SEM MUDANÇAS em: schemas de saída, prompts, funções utilitárias, classificadores
     3.7 e 3.8, sanitização. Output v30 é 100% compatível com gerar_homepage.py.

Pipeline agente (8 steps + 2 advisor checks):
  Step 1  — Ingestão + triagem                    (Python puro)
  Step 2  — Análise Tática em lote                (Haiku × N)
  Step 3  — Fatos Canônicos                       (Haiku × 1)
  Step 4  — Cenários + Clusters                   (Sonnet × 3)
  Step 5  — Vetores Estratégicos + Thesis         (Sonnet × 2) [retry=2]
  CHECK A — Advisor Opus: valida vetores
  Step 6  — Classificadores 3.7 + 3.8            (Python puro)
  Step 7  — Briefing Executivo                    (Opus × 1)   [retry=1]
  CHECK B — Advisor Opus: valida briefing
  Step 8  — Dashboard + Auditoria + Visualização  (Python + Opus × 1)

Schema de saída: "v30-radar-estrategico-agentic-loop"
"""

import html
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    override=True,
)

INPUT_FILE  = "feed_limpo.json"
OUTPUT_FILE = "intel_output.json"
HOJE_ISO    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
HOJE_PT_BR  = datetime.now(timezone.utc).strftime("%d/%m/%Y")

# ── Modelos ───────────────────────────────────────────────────────────────────
MODELO_TATICO       = "claude-haiku-4-5"
MODELO_ESTRATEGICO  = "claude-sonnet-4-6"
MODELO_BRIEFING     = "claude-opus-4-8"
MODELO_VISUALIZACAO = "claude-opus-4-8"
MODELO_ADVISOR      = "claude-opus-4-8"   # advisor checks

# ── Parâmetros do agente ──────────────────────────────────────────────────────
MAX_RETRIES_VETORES  = 2    # tentativas para Fase 5 (vetores + thesis)
MAX_RETRIES_BRIEFING = 1    # tentativas para Fase 7 (briefing)
ADVISOR_MIN_SCORE    = 0.65 # abaixo disso o advisor rejeita e agente refaz

# ── Constantes (idênticas ao v29) ─────────────────────────────────────────────
SETOR_MAP = {
    "energia & eficiência energética": "energia",
    "ia & automação":                  "ia_automacao",
    "regulação & editais":             "regulacao",
    "modelos de negócio & startups":   "negocios",
    "ciência & pesquisa":              "ciencia",
    "financiamento & inovação":        "financiamento",
    "tecnologias emergentes":          "tecnologias",
    "macroeconomia":                   "macroeconomia",
    "infraestrutura crítica":          "infraestrutura",
}

TIPOS_SINAL_VALIDOS = {
    "Risco Regulatório",
    "Oportunidade de Mercado",
    "Choque Geopolítico",
    "Sinal Tecnológico",
}

URGENCIAS_VALIDAS = {30, 90, 180, 360}

JANELA_X: dict[str, float] = {
    "Imediata":      0.88,
    "Curta":         0.68,
    "Média":         0.42,
    "Longa":         0.15,
    "Monitoramento": 0.05,
}


# ═══════════════════════════════════════════════════════════════════════════════
# CAMADA 1 — Calibração temporal (idêntica ao v29)
# ═══════════════════════════════════════════════════════════════════════════════

CALIBRACAO_TEMPORAL_CAUSAL = f"""
CONTEXTO TEMPORAL DO CICLO:
- Data atual: {HOJE_PT_BR} ({HOJE_ISO}).
- NÃO afirme que dados regulatórios brasileiros (CCEE liquidação MRE/MCSD,
  ANEEL homologações, ONS PMO consolidado, EPE PDE) de meses ainda em curso
  já estão disponíveis. A CCEE publica liquidação e MCSD com ~20 dias úteis
  de lag após o fechamento do mês de referência.
- Se uma fonte mencionar dados do mês corrente ou posterior, trate como
  expectativa/projeção, NUNCA como resultado consolidado.

REGRA DE PARSIMÔNIA CAUSAL:
- NÃO conecte eventos por mera correlação temporal ou geográfica.
- Toda afirmação causal deve nomear o mecanismo de transmissão
  (canal cambial, canal regulatório, canal de preço, canal de oferta,
  canal de oferta de capital). Se o canal não estiver claro nos sinais
  de entrada, escreva 'sem mecanismo de transmissão identificado' e
  NÃO conecte.

VEDAÇÕES FACTUAIS:
- NÃO atribua cargos, partidos, investigações ou condutas a pessoas
  físicas se essa informação não estiver explícita no payload de sinais.
- Em caso de dúvida sobre cargo atual ou status de uma pessoa, use
  'a autoridade competente' ou 'o agente público responsável' em vez de nomear.
- NÃO invente datas, valores, marcos regulatórios ou notas legislativas
  que não constem nos sinais. Em vez de inventar, escreva 'a ser confirmado'.

FORMATAÇÃO DE SAÍDA — TEXTO PURO:
- PROIBIDO usar tags HTML (<em>, <strong>, <b>, etc.) ou marcadores Markdown
  (**negrito**, *itálico*) dentro dos valores JSON.
- Todo texto deve ser plain text sem qualquer markup.
- Estrangeirismos consolidados (curtailment, compliance, standby, roadmap,
  hedge, payback, spread, breakeven) devem aparecer diretamente no texto,
  sem aspas, sem itálico, sem HTML. Siglas em maiúsculas sem marcação
  (PPA, BESS, MRE, MCSD, IPCA, SELIC, CDB, ETF, GW, MW, MWh).
"""

DIRETRIZES_TEMATICAS = """
EQUILÍBRIO TEMÁTICO DO THINK TANK (Tech & Energy):
- NÃO concentre a análise exclusivamente em geração e regulação de energia
  tradicional.
- Explore ativamente a interseção entre os setores.
- EIXOS OBRIGATÓRIOS (incorporar sempre que o contexto permitir):
  transição energética (smart grid, BESS), IA e força de trabalho,
  pesquisa e desenvolvimento (P&D), programas de eficiência, infraestruturas
  críticas, data centers, fabricação de chips, telecomunicações e automação.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Estado do agente
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentState:
    """Estado completo do ciclo. Passado entre steps sem variáveis globais."""
    # Entrada
    feed_raw: list[dict] = field(default_factory=list)
    # Step 2
    itens_analisados: list[dict] = field(default_factory=list)
    # Step 3
    fatos_canonicos: list[dict] = field(default_factory=list)
    # Step 4
    cenarios_list: list[dict] = field(default_factory=list)
    matriz_incertezas: dict = field(default_factory=dict)
    clusters: list[dict] = field(default_factory=list)
    # Step 5
    vetores_estrategicos: list[dict] = field(default_factory=list)
    executive_thesis: dict = field(default_factory=dict)
    # Step 5 advisor
    advisor_vetores_ok: bool = False
    advisor_vetores_score: float = 0.0
    advisor_vetores_notas: str = ""
    # Step 6
    academic_pdi_opportunities: dict = field(default_factory=dict)
    commercial_nmentors_opportunities: dict = field(default_factory=dict)
    # Step 7
    briefing_dict: dict = field(default_factory=dict)
    # Step 7 advisor
    advisor_briefing_ok: bool = False
    advisor_briefing_score: float = 0.0
    advisor_briefing_notas: str = ""
    # Step 8
    mesa_decisao: dict = field(default_factory=dict)
    dashboard: dict = field(default_factory=dict)
    visualizacao: dict = field(default_factory=dict)
    auditoria_logs: list[str] = field(default_factory=list)
    # Registro de degradações
    degradation_log: list[dict] = field(default_factory=list)

    def registrar_degradacao(self, step: str, motivo: str):
        self.degradation_log.append({
            "step": step,
            "motivo": motivo,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        print(f"  [DEGRADAÇÃO] {step}: {motivo}")


# ═══════════════════════════════════════════════════════════════════════════════
# Funções utilitárias (idênticas ao v29)
# ═══════════════════════════════════════════════════════════════════════════════

def normalizar_setor(tema: str) -> str:
    return SETOR_MAP.get(tema.lower().strip(), "outros")

def calcular_quadrante(exposicao_brasil: float, urgencia_dias: int) -> str:
    alta_exposicao = exposicao_brasil >= 0.5
    alta_urgencia  = urgencia_dias <= 90
    if alta_exposicao and alta_urgencia:   return "Agir Agora"
    elif alta_exposicao:                   return "Aproveitar"
    elif alta_urgencia:                    return "Preparar"
    else:                                  return "Monitorar"

def calcular_pressao_estrategica(impacto_brasil, score_final, exposicao_brasil, n_sinais, confianca_analise,
                                  novidade_tecnologica: float = 5.0) -> float:
    # novidade_tecnologica (0-10): bônus para sinais de inovação/startup vs. regulação pura
    raw = (
        impacto_brasil        * 0.28
        + score_final         * 0.22
        + exposicao_brasil * 10 * 0.14
        + min(n_sinais / 10.0, 1.0) * 10 * 0.14
        + confianca_analise * 10 * 0.12
        + novidade_tecnologica * 0.10
    )
    return round(max(0.0, min(10.0, raw)), 2)

def classificar_janela_decisoria(dias: int) -> str:
    if dias <= 30:    return "Imediata"
    elif dias <= 90:  return "Curta"
    elif dias <= 180: return "Média"
    elif dias <= 360: return "Longa"
    else:             return "Monitoramento"

def classificar_quadrante_executivo(pressao: float, janela_dias: int) -> str:
    alta_pressao = pressao >= 5.0
    janela_curta = janela_dias <= 90
    if alta_pressao and janela_curta:      return "Mobilizar Agora"
    elif alta_pressao:                     return "Capturar Vantagem"
    elif janela_curta:                     return "Ruído Operacional"
    else:                                  return "Monitorar Vetores"

def extrair_json_valido(texto: str) -> str:
    texto = texto.strip()
    texto = re.sub(r"^```json\s*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"^```\s*",     "", texto)
    texto = re.sub(r"\s*```$",     "", texto)
    match = re.search(r"(\{.*\})", texto, re.DOTALL)
    if match:
        return match.group(1).strip()
    return texto.strip()

def _reparar_json_llm(texto: str) -> str:
    texto = re.sub(r"/\*.*?\*/", "", texto, flags=re.DOTALL)
    texto = re.sub(r"(?m)^\s*//.*$", "", texto)
    texto = re.sub(r"\s*//[^\"\n]*$", "", texto, flags=re.MULTILINE)
    texto = re.sub(r",\s*([\}\]])", r"\1", texto)
    return texto

def _clamp_float(v, lo=0.0, hi=1.0) -> float:
    try:    return max(lo, min(hi, float(v)))
    except: return (lo + hi) / 2

def _clamp_urgencia(v) -> int:
    try:    n = int(v)
    except: return 180
    return min(URGENCIAS_VALIDAS, key=lambda x: abs(x - n))

def _clamp_tipo_sinal(v: str) -> str:
    if v in TIPOS_SINAL_VALIDOS: return v
    s = str(v).lower()
    if "regulat" in s:                          return "Risco Regulatório"
    if "oportunid" in s or "mercado" in s:      return "Oportunidade de Mercado"
    if "geopolít" in s or "geopolit" in s:      return "Choque Geopolítico"
    return "Sinal Tecnológico"

def _sanitizar_texto(s: str) -> str:
    if not isinstance(s, str): return s
    anterior = None
    atual = s
    for _ in range(3):
        if atual == anterior: break
        anterior = atual
        atual = html.unescape(atual)
    atual = re.sub(r'<[^>]+>', '', atual)
    atual = atual.replace("\u2018", "'").replace("\u2019", "'")
    atual = atual.replace("\u201C", '"').replace("\u201D", '"')
    atual = re.sub(r"—{2,}", "—", atual)
    return atual

def _sanitizar_recursivo(obj):
    if isinstance(obj, str):  return _sanitizar_texto(obj)
    if isinstance(obj, list): return [_sanitizar_recursivo(x) for x in obj]
    if isinstance(obj, dict): return {k: _sanitizar_recursivo(v) for k, v in obj.items()}
    return obj

def _truncar_inteligente(s: str, max_chars: int) -> str:
    if not s or not isinstance(s, str): return s
    if len(s) <= max_chars: return s
    cortado = s[: max_chars - 1].rsplit(" ", 1)[0]
    if not cortado: cortado = s[: max_chars - 1]
    return cortado + "…"

def _formatar_fatos_para_prompt(fatos: list[dict]) -> str:
    if not fatos: return ""
    linhas = ["FATOS QUANTIFICADOS VERIFICADOS (use APENAS estes valores ao citar números):"]
    for f in fatos[:20]:
        linhas.append(f"- {f.get('valor_literal','?')} — {f.get('contexto','?')}")
    linhas.append(
        "Se um valor não estiver nesta lista, OMITA o número e descreva qualitativamente. "
        "NÃO some valores desta lista para criar agregados."
    )
    return "\n".join(linhas)


# ═══════════════════════════════════════════════════════════════════════════════
# Cliente Anthropic
# ═══════════════════════════════════════════════════════════════════════════════

def inicializar_cliente() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY não encontrada no .env")
    return anthropic.Anthropic(api_key=api_key)

def _chamar_claude(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float | None = 0.1,
) -> str:
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if temperature is not None:
        kwargs["temperature"] = temperature
    return client.messages.create(**kwargs).content[0].text


# ═══════════════════════════════════════════════════════════════════════════════
# ADVISOR CHECKS — novos no v30
# ═══════════════════════════════════════════════════════════════════════════════

def advisor_check_vetores(
    client: anthropic.Anthropic,
    state: AgentState,
) -> tuple[bool, float, str]:
    """
    Opus avalia os vetores estratégicos gerados.
    Retorna (aprovado, score 0-1, notas).
    Score < ADVISOR_MIN_SCORE → agente refaz Step 5.
    """
    print(f"  [Advisor A] Validando vetores com {MODELO_ADVISOR}...")

    if not state.vetores_estrategicos:
        return False, 0.0, "Nenhum vetor gerado."

    resumo_vetores = [
        {
            "id": v.get("id"),
            "nome": v.get("nome", ""),
            "pressao": v.get("pressao_estrategica", 0),
            "quadrante": v.get("quadrante_executivo", ""),
            "janela_dias": v.get("janela_decisoria_dias", 180),
            "n_sinais": v.get("n_sinais", 0),
            "decisao": v.get("decisao_recomendada", ""),
        }
        for v in state.vetores_estrategicos
    ]

    thesis_resumo = {
        "frase_central": state.executive_thesis.get("frase_central", ""),
        "n_mudancas": len(state.executive_thesis.get("mudancas_estruturais", [])),
        "n_decisoes": len(state.executive_thesis.get("decisoes_prioritarias", [])),
    }

    prompt_sistema = (
        "Você é um advisor estratégico sênior avaliando a qualidade de um ciclo de inteligência. "
        "Avalie com rigor, mas seja pragmático. Responda APENAS com JSON válido."
    )

    prompt_usuario = f"""Avalie a qualidade dos vetores estratégicos e da executive thesis abaixo.

Vetores ({len(resumo_vetores)} gerados):
{json.dumps(resumo_vetores, ensure_ascii=False, indent=2)}

Executive Thesis:
{json.dumps(thesis_resumo, ensure_ascii=False)}

Retorne EXATAMENTE este JSON:
{{
  "score": 0.0,
  "aprovado": true,
  "dimensoes": {{
    "cobertura_tematica": 0.0,
    "especificidade_decisoes": 0.0,
    "coerencia_thesis_vetores": 0.0,
    "distribuicao_quadrantes": 0.0
  }},
  "notas": "Observações em 2-3 frases sobre pontos fortes e fracos.",
  "recomendacao_refazer": false
}}

CRITÉRIOS:
- score: média ponderada das dimensões (0.0 a 1.0)
- aprovado: true se score >= {ADVISOR_MIN_SCORE}
- cobertura_tematica: os vetores cobrem temas distintos sem sobreposição excessiva?
- especificidade_decisoes: as decisões_recomendadas são acionáveis por C-level?
- coerencia_thesis_vetores: a frase_central da thesis reflete os vetores dominantes?
- distribuicao_quadrantes: há vetores em pelo menos 2 quadrantes distintos?
- recomendacao_refazer: true apenas se score < {ADVISOR_MIN_SCORE} E houver problema estrutural claro
"""

    try:
        texto = _chamar_claude(
            client, MODELO_ADVISOR,
            prompt_sistema, prompt_usuario,
            max_tokens=800, temperature=None,
        )
        resultado = json.loads(extrair_json_valido(texto))
        score = float(resultado.get("score", 0.0))
        aprovado = bool(resultado.get("aprovado", score >= ADVISOR_MIN_SCORE))
        notas = resultado.get("notas", "")
        print(f"     [Advisor A] score={score:.2f} aprovado={aprovado} | {notas[:80]}")
        return aprovado, score, notas
    except Exception as e:
        print(f"  [!] Advisor A falhou: {e} — aprovando por fallback")
        return True, ADVISOR_MIN_SCORE, f"Erro no advisor: {e}"


def advisor_check_briefing(
    client: anthropic.Anthropic,
    state: AgentState,
) -> tuple[bool, float, str]:
    """
    Opus avalia alinhamento do briefing com os vetores dominantes.
    Retorna (aprovado, score 0-1, notas).
    """
    print(f"  [Advisor B] Validando briefing com {MODELO_ADVISOR}...")

    if not state.briefing_dict or not state.briefing_dict.get("titulo"):
        return False, 0.0, "Briefing vazio ou sem título."

    vetores_dominantes = [
        v.get("nome", "")
        for v in sorted(
            state.vetores_estrategicos,
            key=lambda v: v.get("pressao_estrategica", 0), reverse=True
        )[:3]
    ]

    prompt_sistema = (
        "Você é um editor-chefe avaliando a qualidade de um briefing executivo. "
        "Avalie com rigor. Responda APENAS com JSON válido."
    )

    prompt_usuario = f"""Avalie o briefing executivo abaixo em relação aos vetores dominantes do ciclo.

Vetores dominantes (top 3 por pressão):
{json.dumps(vetores_dominantes, ensure_ascii=False)}

Briefing:
- Título: {state.briefing_dict.get('titulo', '')}
- Subtítulo: {state.briefing_dict.get('subtitulo', '')}
- Abertura: {state.briefing_dict.get('frase_de_abertura', '')[:200]}
- Parágrafos: {len(state.briefing_dict.get('paragrafos', []))} horizons
- Implicação cruzada: {state.briefing_dict.get('implicacao_cruzada', '')[:150]}

Retorne EXATAMENTE este JSON:
{{
  "score": 0.0,
  "aprovado": true,
  "dimensoes": {{
    "alinhamento_vetores": 0.0,
    "especificidade_numerica": 0.0,
    "cobertura_horizontes": 0.0,
    "tom_executivo": 0.0
  }},
  "notas": "2-3 frases sobre qualidade editorial.",
  "recomendacao_refazer": false
}}

CRITÉRIOS:
- score: média das dimensões (0.0 a 1.0)
- aprovado: true se score >= {ADVISOR_MIN_SCORE}
- alinhamento_vetores: título/abertura refletem os vetores dominantes?
- especificidade_numerica: há dados quantitativos concretos nos parágrafos?
- cobertura_horizontes: os 3 horizontes (90d, 6m, 12m) estão presentes?
- tom_executivo: linguagem adequada para C-suite / conselho?
"""

    try:
        texto = _chamar_claude(
            client, MODELO_ADVISOR,
            prompt_sistema, prompt_usuario,
            max_tokens=600, temperature=None,
        )
        resultado = json.loads(extrair_json_valido(texto))
        score = float(resultado.get("score", 0.0))
        aprovado = bool(resultado.get("aprovado", score >= ADVISOR_MIN_SCORE))
        notas = resultado.get("notas", "")
        print(f"     [Advisor B] score={score:.2f} aprovado={aprovado} | {notas[:80]}")
        return aprovado, score, notas
    except Exception as e:
        print(f"  [!] Advisor B falhou: {e} — aprovando por fallback")
        return True, ADVISOR_MIN_SCORE, f"Erro no advisor: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Steps do agente — wrappers sobre as funções do v29
# (As funções core são idênticas ao v29; o agente apenas as chama com retry)
# ═══════════════════════════════════════════════════════════════════════════════

# ── IMPORTAÇÃO das funções core do v29 ────────────────────────────────────────
# Para manter o código DRY, as funções de análise são definidas abaixo
# como cópias diretas do v29 (sem alteração). Em produção, pode-se fazer
# from analyzer_v29 import ... se ambos estiverem no mesmo diretório.

def inicializar_cliente() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "Erro: variável de ambiente ANTHROPIC_API_KEY não encontrada. "
            "Adicione-a ao arquivo .env"
        )
    return anthropic.Anthropic(api_key=api_key)


def _chamar_claude(
    client: anthropic.Anthropic,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float | None = 0.1,
) -> str:
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if temperature is not None:
        kwargs["temperature"] = temperature
    return client.messages.create(**kwargs).content[0].text


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 1 — Análise Tática (claude-haiku-4-5)
# ═══════════════════════════════════════════════════════════════════════════════

def analisar_item_tatico(
    client: anthropic.Anthropic,
    item: dict,
    indice: int,
    total: int,
) -> dict | None:

    prompt_sistema = (
        "Você é um analista tático de dados do think tank Tech & Energy. "
        "Regras absolutas: "
        "(1) PROIBIDO usar tags HTML (<em>, <strong>, <b>, etc.) ou Markdown (**texto**, *texto*) "
        "nos valores do JSON. Todo texto deve ser plain text sem nenhum markup. "
        "(2) NUNCA use negrito. "
        "(3) OBRIGATÓRIO: use sempre acentuação e ortografia CORRETAS do português brasileiro. "
        "Exemplos de acentuação obrigatória: análise, energia, regulação, "
        "decisão, informação, ação, avaliação, gestão, conexão, integração, solução, "
        "técnico, específico, crítico, nível, área, seção, função, potência, indústria, "
        "eficiência, estratégia, investimento, negócios, inovação, econômico, público. "
        "Nunca omita acentos: 'analise' está errado, 'análise' está correto. "
        "(4) Ao encontrar títulos em idiomas estrangeiros, traduza-os para o português "
        "de forma executiva e precisa, sem anglicismos desnecessários e mantendo "
        "o sentido técnico original."
        + CALIBRACAO_TEMPORAL_CAUSAL
    )

    prompt_usuario = f"""Extraia metadados do sinal abaixo e avalie seu impacto no Brasil:
Título: {item.get('titulo')}
Link: {item.get('link')}
Conteúdo: {item.get('descricao', item.get('conteudo', ''))}

Retorne EXATAMENTE este JSON (sem texto fora do JSON, sem markdown):
{{
  "titulo_pt": "Título em português brasileiro executivo (se já em pt-BR, mantenha ou refine; se em outro idioma, traduza com precisão técnica).",
  "tema_analisado": "UMA categoria: Energia & Eficiência Energética | IA & Automação | Regulação & Editais | Modelos de Negócio & Startups | Ciência & Pesquisa | Financiamento & Inovação | Tecnologias Emergentes | Macroeconomia | Infraestrutura Crítica",
  "geo_scope": "Nacional | Regional | Global",
  "resumo_executivo": "2-3 frases sem formatação, acentuação correta, foco em impacto para tomadores de decisão.",
  "analise_profunda": "Parágrafo analítico sem negrito, acentuação correta, conectando causas e implicações estratégicas.",
  "vetor_estrategico": "Oportunidade | Tendência | Alerta | Neutro",
  "score_final": 0.0,
  "impacto_brasil": 0.0,
  "exposicao_brasil": 0.0,
  "novidade_tecnologica": 0.0,
  "urgencia_dias": 90,
  "tipo_sinal": "Risco Regulatório",
  "decisao_sugerida": "Verbo no infinitivo + ação concreta para executivos brasileiros em 1 frase.",
  "mecanismo_impacto": "Cadeia causal em 1-2 frases: evento → mecanismo → consequência para o Brasil.",
  "setor_afetado": "Setor primário afetado no Brasil (ex: Energia & Transmissão, Infraestrutura Digital, Setor Financeiro, Regulação Federal).",
  "confianca_analise": 0.0
}}

REGRAS DE PREENCHIMENTO:
- score_final (0-10): relevância global do sinal para C-level de tech/energy.
- impacto_brasil (0-10): "Isso muda alguma decisão de um executivo brasileiro?" Se NÃO, use 0-3. Se SIM e urgente, use 7-10.
- exposicao_brasil (0.0-1.0): quanto o evento afeta energia, dados, indústria, agro, infraestrutura ou regulação nacional.
- novidade_tecnologica (0-10): grau de inovação tecnológica genuína do sinal. 8-10 = nova tecnologia, produto, modelo de negócio, pesquisa disruptiva, startup ou patente relevante. 4-7 = aplicação incremental de tecnologia existente. 0-3 = sinal predominantemente regulatório, burocrático, macroeconômico ou político sem componente tecnológico novo.
- urgencia_dias: EXATAMENTE um destes: 30, 90, 180 ou 360. Janela temporal para decisão.
- tipo_sinal: EXATAMENTE um de: "Risco Regulatório" | "Oportunidade de Mercado" | "Choque Geopolítico" | "Sinal Tecnológico".
- decisao_sugerida: começa com verbo no infinitivo (ex: "Avaliar", "Revisar", "Mapear", "Antecipar").
- confianca_analise (0.0-1.0): qualidade da fonte + consistência do sinal.

VEDAÇÕES MONETÁRIAS (Zero-Shot):
- NUNCA converta ou altere unidades monetárias. Se a fonte usa USD, escreva USD; se usa R$, escreva R$.
- Cite valores exatamente como aparecem na fonte original.
- Exemplos CORRETOS: "US$ 1,765 bilhão" (não "R$ 1,8 bilhão"), "R$ 500 milhões" (não "US$ 100 mi").

FILTRO DE RELEVÂNCIA BUROCRÁTICA (Zero-Shot):
- Se o conteúdo for PURAMENTE burocrático, administrativo interno, comemorativo ou social SEM impacto
  de negócios, use impacto_brasil entre 0.0 e 1.5 e score_final entre 0.0 e 2.0.
- Exemplos para score baixo: pesquisas de comportamento adolescente, efemérides institucionais,
  transmissões ao vivo já encerradas, publicações educativas básicas, portarias de pessoal.
- NÃO eleve o score pela autoridade da fonte. Pontue EXCLUSIVAMENTE pelo conteúdo e impacto real.
"""

    try:
        texto = _chamar_claude(
            client, MODELO_TATICO,
            prompt_sistema, prompt_usuario,
            max_tokens=1200, temperature=0.1,
        )
        return json.loads(extrair_json_valido(texto))
    except Exception as e:
        print(f"  [!] Erro tático item {indice}/{total}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# CAMADA 2 — Fase 1.5: Extração de Fatos Canônicos (claude-haiku-4-5)
# ═══════════════════════════════════════════════════════════════════════════════

def extrair_fatos_canonicos(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
) -> list[dict]:
    """
    Extrai entidades numéricas verificáveis dos sinais top do ciclo.
    Roda APÓS Fase 1 e ANTES de Fase 3, 3.5 e 4.

    Retorna lista canônica que serve de fonte única de verdade para os números
    citados nas fases de síntese — eliminando divergências do tipo
    "R$ 50 bi na thesis" vs "R$ 63,2 bi no briefing".
    """
    print(f"  -> Extraindo Fatos Canônicos ({MODELO_TATICO})...")

    # Seleciona top sinais por impacto_brasil para limitar payload
    top_sinais = sorted(
        itens_analisados,
        key=lambda x: x.get("analise", {}).get("impacto_brasil", 0),
        reverse=True,
    )[:25]

    payload_lines = []
    for idx, item in enumerate(top_sinais):
        an = item.get("analise", {})
        titulo = item.get("titulo_pt") or item.get("titulo", "")
        resumo = an.get("resumo_executivo", "")
        descricao = item.get("descricao", "") or ""
        # Mantém contexto suficiente sem inchar — Haiku é barato mas tem limite
        texto_fonte = f"{titulo}. {resumo} {descricao}"[:600]
        payload_lines.append(f"[{idx}] {texto_fonte}")
    payload = "\n".join(payload_lines)

    prompt_sistema = (
        "Você é um extrator de entidades numéricas para um think tank. "
        "Sua única tarefa: identificar VALORES QUANTIFICADOS explícitos nos sinais. "
        "Não infira, não calcule, não some. Apenas extraia o que está literalmente escrito. "
        "OBRIGATÓRIO: responda APENAS com JSON válido, sem texto fora do JSON, sem markdown. "
        + CALIBRACAO_TEMPORAL_CAUSAL
    )

    prompt_usuario = f"""Identifique os fatos quantificados explícitos nos sinais abaixo.
Cada fato deve ter um valor numérico citado literalmente no sinal de origem.

Sinais (índice|texto):
{payload}

Retorne EXATAMENTE este JSON (sem texto fora do JSON):
{{
  "fatos_canonicos": [
    {{
      "valor_literal": "R$ 50 bilhões",
      "tipo": "monetario",
      "contexto": "Eco Invest 4ª edição — mobilização de capital",
      "sinal_id": 3,
      "confianca": "alta"
    }},
    {{
      "valor_literal": "8 GW",
      "tipo": "capacidade",
      "contexto": "Expansão prevista de geração eólica offshore",
      "sinal_id": 7,
      "confianca": "alta"
    }}
  ]
}}

REGRAS OBRIGATÓRIAS:
- tipo: "monetario" | "percentual" | "capacidade" | "data" | "quantidade" | "tarifa"
- confianca: "alta" se o valor está literalmente escrito no sinal; "media" se inferido a partir do contexto.
- NÃO some, NÃO calcule, NÃO infira valores compostos. Se o sinal diz "R$ 50 bi" e outro diz "R$ 13,2 bi", crie DOIS fatos separados — nunca um terceiro fato de "R$ 63,2 bi".
- contexto: 1 frase curta identificando QUAL evento/programa/empresa o valor descreve.
  CRÍTICO para percentuais: preserve sempre o denominador original.
  Ex CORRETO: "ICMS representa até 60% da carga tributária total sobre data centers"
  Ex ERRADO:  "Carga fiscal de ICMS sobre data centers" (omite o denominador — proibido)
  Ex ERRADO:  "ICMS equivalente a 60% do custo" (troca o denominador — proibido)
  Se o sinal não deixar claro o denominador, escreva: "60% — denominador não especificado na fonte".
- sinal_id: o índice [N] do sinal de onde o valor foi extraído.
- Se não houver valores quantificados claros, retorne lista vazia.
- Máximo 20 fatos. Priorize valores de maior materialidade (R$, GW, %).
"""

    _FALLBACK: list[dict] = []

    try:
        texto = _chamar_claude(
            client, MODELO_TATICO,
            prompt_sistema, prompt_usuario,
            max_tokens=2500, temperature=0.0,
        )
        resultado = json.loads(_reparar_json_llm(extrair_json_valido(texto)))
        fatos = resultado.get("fatos_canonicos", [])
        # Resolve sinal_id (índice no subset top_sinais) para o título real do item
        for f in fatos:
            sid = f.get("sinal_id", -1)
            if isinstance(sid, int) and 0 <= sid < len(top_sinais):
                item_ref = top_sinais[sid]
                f["fonte_titulo"] = item_ref.get("titulo_pt") or item_ref.get("titulo", "")
                f["fonte_url"]    = item_ref.get("url", "")
        # Filtra apenas fatos de confiança alta para uso downstream
        fatos_validos = [f for f in fatos if f.get("confianca") == "alta"]
        print(f"     [OK] {len(fatos_validos)} fatos canônicos de alta confiança "
              f"(de {len(fatos)} extraídos).")
        return fatos_validos
    except Exception as e:
        print(f"  [!] Erro na extração de fatos canônicos: {e}")
        return _FALLBACK


def _formatar_fatos_para_prompt(fatos: list[dict]) -> str:
    """Formata fatos canônicos como bloco de texto para injeção em prompts."""
    if not fatos:
        return ""
    linhas = ["FATOS QUANTIFICADOS VERIFICADOS (use APENAS estes valores ao citar números):"]
    for f in fatos[:20]:
        linhas.append(
            f"- {f.get('valor_literal','?')} — {f.get('contexto','?')}"
        )
    linhas.append(
        "Se um valor não estiver nesta lista, OMITA o número e descreva qualitativamente. "
        "NÃO some valores desta lista para criar agregados."
    )
    return "\n".join(linhas)


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 2 — Arquitetura de Cenários (claude-sonnet-4-6)
# ═══════════════════════════════════════════════════════════════════════════════

def construir_matriz_de_cenarios(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
) -> dict:
    prompt_sistema = (
        "Você é um arquiteto de cenários prospectivos de um think tank de Tech & Energy. "
        "Escopo transversal: transição energética, IA, infraestruturas críticas, "
        "data centers, semicondutores, telecomunicações e regulamentação. "
        "Regras absolutas: "
        "(1) PROIBIDO negrito ou qualquer marcação HTML/Markdown nos valores JSON. "
        "(2) Ortografia e ACENTUAÇÃO CORRETAS do português brasileiro obrigatórias. "
        "(3) Retorne APENAS JSON válido — sem texto fora do JSON."
        + CALIBRACAO_TEMPORAL_CAUSAL
        + DIRETRIZES_TEMATICAS
    )

    sinais_fortes = [
        {
            "id": i,
            "titulo": item.get("titulo_pt") or item["titulo"],
            "resumo": item["analise"]["resumo_executivo"],
            "tipo_sinal": item["analise"].get("tipo_sinal", ""),
            "impacto_brasil": item["analise"].get("impacto_brasil", 0),
        }
        for i, item in enumerate(itens_analisados)
        if item.get("analise", {}).get("score_final", 0) >= 6.0
    ]
    payload = json.dumps(sinais_fortes, ensure_ascii=False)

    fallback_matriz = {
        "eixo_x": {"nome": "Velocidade regulatória", "polo_baixo": "Lenta", "polo_alto": "Acelerada"},
        "eixo_y": {"nome": "Disponibilidade de capital", "polo_baixo": "Restrita", "polo_alto": "Ampla"},
        "justificativa": "",
    }

    # ── Passo A: forças motrizes ──────────────────────────────────────────────
    print(f"  -> Passo A: Mapeando forças motrizes ({len(sinais_fortes)} sinais ≥ 6.0)...")
    prompt_incertezas = f"""
Analise os sinais abaixo cruzando os setores. Identifique as duas variáveis mais incertas.
Sinais: {payload}

Retorne EXATAMENTE este JSON (sem texto fora do JSON):
{{
  "eixo_x": {{
    "nome": "Nome conciso da incerteza 1 (máx 5 palavras)",
    "polo_baixo": "Extremo baixo/negativo (máx 4 palavras)",
    "polo_alto": "Extremo alto/positivo (máx 4 palavras)"
  }},
  "eixo_y": {{
    "nome": "Nome conciso da incerteza 2 (máx 5 palavras)",
    "polo_baixo": "Extremo baixo/negativo (máx 4 palavras)",
    "polo_alto": "Extremo alto/positivo (máx 4 palavras)"
  }},
  "justificativa": "Racional com acentuação correta, sem negrito"
}}
"""
    try:
        texto_inc = _chamar_claude(
            client, MODELO_ESTRATEGICO,
            prompt_sistema, prompt_incertezas,
            max_tokens=1024, temperature=0.2,
        )
        matriz_base = json.loads(extrair_json_valido(texto_inc))
        for eixo_key in ("eixo_x", "eixo_y"):
            v = matriz_base.get(eixo_key)
            if isinstance(v, str):
                matriz_base[eixo_key] = {"nome": v, "polo_baixo": "—", "polo_alto": "—"}
            elif not isinstance(v, dict):
                matriz_base[eixo_key] = fallback_matriz[eixo_key]
        print(f"     [OK] Eixo X: {matriz_base['eixo_x']['nome']} | Eixo Y: {matriz_base['eixo_y']['nome']}")
    except Exception as e:
        print(f"  [!] Erro no mapeamento de incertezas: {e}")
        return {"cenarios_prospectivos": [], "matriz_incertezas": fallback_matriz}

    # ── Passo B: cenários prospectivos ────────────────────────────────────────
    print("  -> Passo B: Projetando cenários...")
    ex = matriz_base.get("eixo_x", {})
    ey = matriz_base.get("eixo_y", {})
    eixo_x_desc = f"{ex.get('nome','Eixo X')} ({ex.get('polo_baixo','—')} ↔ {ex.get('polo_alto','—')})"
    eixo_y_desc = f"{ey.get('nome','Eixo Y')} ({ey.get('polo_baixo','—')} ↔ {ey.get('polo_alto','—')})"

    prompt_cenarios = f"""
Utilize a matriz de incertezas:
  Eixo X: {eixo_x_desc}
  Eixo Y: {eixo_y_desc}

Projete exatamente 3 cenários transversais plausíveis para os próximos 12-24 meses.

Retorne EXATAMENTE este JSON (sem texto fora do JSON):
{{
  "cenarios_prospectivos": [
    {{
      "numero": 1,
      "titulo_cenario": "Nome sintético do cenário (máx 6 palavras)",
      "narrativa_macro": "Parágrafo rico detalhando o cenário, acentuação impecável, SEM NEGRITO. Mínimo 3 frases.",
      "descricao_expandida": "Explicação detalhada em 3-5 frases: o que acontece, por que, e quais as implicações para o Brasil. SEM NEGRITO.",
      "diretriz_acao_brasil": "Ação estratégica concreta para o Brasil, acentuação correta, SEM NEGRITO.",
      "probabilidade": 35,
      "impacto": "Muito Alto",
      "tipo": "Risco",
      "pos_x": 0.75,
      "pos_y": 0.25,
      "ids_sinais": [0, 1, 2]
    }}
  ]
}}

REGRAS:
- numero: sequencial 1, 2, 3
- probabilidade: inteiro 0-100 (os 3 cenários devem somar ≤ 100)
- impacto: "Muito Alto" | "Alto" | "Moderado" | "Baixo"
- tipo: "Risco" | "Oportunidade" | "Misto"
- pos_x e pos_y: floats 0.0-1.0 em quadrantes diferentes
"""
    try:
        texto_cen = _chamar_claude(
            client, MODELO_ESTRATEGICO,
            prompt_sistema, prompt_cenarios,
            max_tokens=3500, temperature=0.4,
        )
        cen_json = json.loads(extrair_json_valido(texto_cen))
        letras = "ABCD"
        for i, c in enumerate(cen_json.get("cenarios_prospectivos", [])):
            if "id" not in c:
                c["id"] = letras[i] if i < len(letras) else f"C{i+1}"
            c["pos_x"] = float(c.get("pos_x", 0.5))
            c["pos_y"] = float(c.get("pos_y", 0.5))
            c.setdefault("numero", i + 1)
            c.setdefault("probabilidade", 33)
            c.setdefault("impacto", "Alto")
            c.setdefault("tipo", "Misto")
            c.setdefault("descricao_expandida", c.get("narrativa_macro", ""))
        return {
            "cenarios_prospectivos": cen_json.get("cenarios_prospectivos", []),
            "matriz_incertezas": {
                "eixo_x":        matriz_base.get("eixo_x", fallback_matriz["eixo_x"]),
                "eixo_y":        matriz_base.get("eixo_y", fallback_matriz["eixo_y"]),
                "justificativa": matriz_base.get("justificativa", ""),
            },
        }
    except Exception as e:
        print(f"  [!] Erro na projeção de cenários: {e}")
        return {
            "cenarios_prospectivos": [],
            "matriz_incertezas": {
                "eixo_x":        matriz_base.get("eixo_x", fallback_matriz["eixo_x"]),
                "eixo_y":        matriz_base.get("eixo_y", fallback_matriz["eixo_y"]),
                "justificativa": matriz_base.get("justificativa", ""),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 2c — Clusters Temáticos (claude-sonnet-4-6)
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_clusters(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
) -> list[dict]:
    print(f"  -> Passo C: Identificando clusters temáticos ({MODELO_ESTRATEGICO})...")

    _FALLBACK: list[dict] = []

    top_items = [
        {
            "titulo": item.get("titulo_pt") or item.get("titulo", ""),
            "tema": item["analise"].get("tema_analisado", ""),
            "tipo_sinal": item["analise"].get("tipo_sinal", ""),
            "setor_afetado": item["analise"].get("setor_afetado", ""),
            "impacto_brasil": item["analise"].get("impacto_brasil", 0),
        }
        for item in itens_analisados
        if item.get("score_final", 0) >= 6.5
    ][:50]

    if len(top_items) < 5:
        return _FALLBACK

    prompt_sistema = (
        "Você é um analista estratégico sênior do think tank Tech & Energy. "
        "Identifique convergências temáticas entre sinais para orientar decisões executivas. "
        "OBRIGATÓRIO: use acentuação e ortografia CORRETAS do português brasileiro em TODO o JSON. "
        "Exemplos corretos: Regulação (não Regulacao), Estratégia (não Estrategia), "
        "Decisão (não Decisao), Regulatório (não Regulatorio), Energética (não Energetica), "
        "Crítico (não Critico), Indústria (não Industria), Produção (não Producao), "
        "Transição (não Transicao), Industrialização (não Industrializacao), "
        "Economia (correto), Ação (não Acao), Regulamentação (não Regulamentacao), "
        "Avaliação (não Avaliacao), Implementação (não Implementacao). "
        "Regras: sem negrito, PROIBIDO HTML/Markdown nos valores JSON, "
        "responda APENAS com JSON válido."
        + CALIBRACAO_TEMPORAL_CAUSAL
        + DIRETRIZES_TEMATICAS
    )

    prompt_usuario = f"""Analise os {len(top_items)} sinais abaixo e identifique 3 clusters \
temáticos emergentes com maior convergência estratégica para o Brasil.

Sinais:
{json.dumps(top_items, ensure_ascii=False)}

Retorne EXATAMENTE este JSON:
{{
  "clusters": [
    {{
      "nome": "Nome do cluster (máx 6 palavras, ex: 'Infraestrutura Digital & Energia Limpa')",
      "convergencia": "Alta",
      "n_sinais": 12,
      "tese": "Tese executiva do cluster em 1-2 frases, acentuação correta, sem negrito.",
      "titulos_noticias": ["título do sinal 1", "título do sinal 2", "título do sinal 3"]
    }}
  ]
}}

REGRAS:
- convergencia: "Alta" ou "Média"
- n_sinais: número real de sinais que se encaixam no cluster (use os dados acima)
- titulos_noticias: TODOS os títulos EXATOS dos sinais acima que pertencem ao cluster (sem limite — liste todos os relevantes)
- Os 3 clusters devem cobrir temas distintos sem sobreposição
"""

    try:
        texto = _chamar_claude(
            client, MODELO_ESTRATEGICO,
            prompt_sistema, prompt_usuario,
            max_tokens=2048, temperature=0.3,
        )
        resultado = json.loads(extrair_json_valido(texto))
        clusters = resultado.get("clusters", [])
        print(f"     [OK] {len(clusters)} clusters identificados.")
        return clusters
    except Exception as e:
        print(f"  [!] Erro na geração de clusters: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 3 — Vetores Estratégicos Consolidados (claude-sonnet-4-6)
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_vetores_estrategicos(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
    clusters: list[dict],
    fatos_canonicos: list[dict] | None = None,
) -> list[dict]:
    """
    Consolida sinais individuais em vetores estratégicos para visão executiva.
    v26: recebe fatos_canonicos para reúso entre Fase 3, 3.5 e 4.
    """
    print(f"  -> Gerando Vetores Estratégicos Consolidados ({MODELO_ESTRATEGICO})...")

    sinais_validos = [
        item for item in itens_analisados
        if item.get("analise", {}).get("impacto_brasil", 0) >= 3.0
        and item.get("analise", {}).get("confianca_analise", 0) >= 0.40
        and item.get("analise", {}).get("score_final", 0) >= 4.5
    ]

    sinais_validos_sorted = sorted(
        sinais_validos,
        key=lambda x: x.get("analise", {}).get("impacto_brasil", 0),
        reverse=True,
    )[:60]

    if len(sinais_validos_sorted) < 3:
        print("  [!] Sinais insuficientes para geração de vetores. Usando fallback.")
        return _vetores_fallback_from_itens(itens_analisados)

    # Formata payload — usa truncamento inteligente para evitar cortes no meio de palavra
    linhas_sinais = []
    for idx, item in enumerate(sinais_validos_sorted):
        an = item.get("analise", {})
        titulo   = _truncar_inteligente(item.get("titulo_pt") or item.get("titulo", ""), 80)
        mecanismo = _truncar_inteligente(an.get("mecanismo_impacto", "") or "", 120)
        linhas_sinais.append(
            f"{idx}|{an.get('tipo_sinal','')}|{an.get('tema_analisado','')}|"
            f"{an.get('setor_afetado','')}|{round(an.get('impacto_brasil',0),1)}|"
            f"{an.get('urgencia_dias',180)}|{titulo}|{mecanismo}"
        )
    sinais_txt = "\n".join(linhas_sinais)

    clusters_txt = ""
    if clusters:
        c_list = []
        for c in clusters[:3]:
            c_list.append(f"- {c.get('nome','')} ({c.get('n_sinais',0)} sinais): {c.get('tese','')}")
        clusters_txt = "Clusters identificados:\n" + "\n".join(c_list)

    fatos_txt = _formatar_fatos_para_prompt(fatos_canonicos or [])

    prompt_sistema = (
        "Você é um estrategista executivo sênior do Think Tank Tech & Energy. "
        "Sua tarefa é consolidar sinais de inteligência em vetores estratégicos para "
        "conselho de administração e C-suite. "
        "OBRIGATÓRIO: use acentuação e ortografia CORRETAS do português brasileiro em TODO o JSON — "
        "incluindo nomes de vetores, descrições, decisões e consequências. "
        "Exemplos CORRETOS: Regulação (não Regulacao), Estratégia (não Estrategia), "
        "Regulatório (não Regulatorio), Energética (não Energetica), Crítico (não Critico), "
        "Indústria (não Industria), Produção (não Producao), Transição (não Transicao), "
        "Industrialização (não Industrializacao), Ação (não Acao), Decisão (não Decisao), "
        "Tecnológica (não Tecnologica), Econômica (não Economica), Descarbonização (não Descarbonizacao), "
        "Negócio (não Negocio), Armazenamento (correto), Tarifária (não Tarifaria). "
        "Regras absolutas: "
        "(1) PROIBIDO usar tags HTML ou Markdown nos valores JSON — plain text apenas. "
        "(2) Sem negrito. "
        "(3) Responda APENAS com JSON válido, sem texto fora do JSON."
        + CALIBRACAO_TEMPORAL_CAUSAL
        + DIRETRIZES_TEMATICAS
    )

    prompt_usuario = f"""Analise os {len(sinais_validos_sorted)} sinais abaixo e consolide em 5 a 8 VETORES ESTRATÉGICOS.

Um vetor estratégico agrupa sinais que compartilham:
- mecanismo causal similar
- setor ou cadeia de valor afetada de forma convergente
- horizonte de decisão próximo
- convergência temática ou geopolítica

Formato dos sinais (índice|tipo_sinal|tema|setor|impacto_brasil|urgencia_dias|titulo|mecanismo):
{sinais_txt}

{clusters_txt}

{fatos_txt}

Retorne EXATAMENTE este JSON (sem texto fora do JSON, sem blocos ```):
{{
  "vetores_estrategicos": [
    {{
      "id": "VE-001",
      "nome": "Nome executivo do vetor — máx 80 caracteres, frase completa sem truncamento",
      "tipo": "Risco Regulatório",
      "descricao_executiva": "2-3 frases explicando o vetor, o mecanismo e por que pressiona o Brasil agora. Acentuação impecável, sem negrito.",
      "pressao_estrategica": 7.5,
      "janela_decisoria_dias": 90,
      "janela_decisoria_categoria": "Curta",
      "custo_espera": "Alto",
      "irreversibilidade": "Alta",
      "momento_vetor": "Acelerando",
      "intensidade_momento": 0.75,
      "impacto_brasil": 7.5,
      "exposicao_brasil": 0.80,
      "confianca_analise": 0.85,
      "setores_afetados": ["Energia", "Infraestrutura"],
      "materialidade": {{
        "capex": "Alta",
        "opex": "Média",
        "regulatorio": "Alta",
        "competitividade": "Alta",
        "reputacional": "Média"
      }},
      "mecanismo_causal": "Cadeia causal em 1-2 frases: evento → mecanismo → consequência para o Brasil.",
      "consequencia_inacao": "O que acontece se a empresa não agir. 1 frase objetiva e concreta.",
      "decisao_recomendada": "Verbo no infinitivo + ação concreta para C-level brasileiro. 1 frase direta.",
      "sinais_ids": [0, 1, 2],
      "n_sinais": 3,
      "quadrante_executivo": "Mobilizar Agora"
    }}
  ]
}}

REGRAS OBRIGATÓRIAS:
- tipo: "Risco Regulatório" | "Oportunidade de Mercado" | "Choque Geopolítico" | "Sinal Tecnológico" | "Misto"
- pressao_estrategica: 0-10. Combine impacto_brasil médio × 0.30 + score médio × 0.25 + exposição × 1.5 + normalização de sinais × 1.5 + confiança × 1.5
- janela_decisoria_dias: EXATAMENTE um de: 30, 90, 180, 360
- janela_decisoria_categoria: "Imediata" (≤30) | "Curta" (31-90) | "Média" (91-180) | "Longa" (181-360) | "Monitoramento" (>360)
- custo_espera: "Baixo" | "Médio" | "Alto" | "Crítico"
- irreversibilidade: "Baixa" | "Média" | "Alta"
- momento_vetor: "Acelerando" | "Estável" | "Desacelerando" | "Emergente" | "Crítico"
- intensidade_momento: float 0.0-1.0
- quadrante_executivo: "Mobilizar Agora" (pressao>=5 e janela<=90) | "Capturar Vantagem" (pressao>=5 e janela>90) | "Ruído Operacional" (pressao<5 e janela<=90) | "Monitorar Vetores" (pressao<5 e janela>90)
- sinais_ids: índices dos sinais acima (coluna 0) que compõem este vetor
- n_sinais: comprimento real do array sinais_ids
- Cada sinal pertence a no máximo 1 vetor
- Sinais de impacto_brasil < 3.0 DEVEM ser omitidos
- materialidade: cada campo pode ser "Alta" | "Média" | "Baixa"
- Gere entre 5 e 8 vetores. Não menos que 5.
"""

    try:
        texto = _chamar_claude(
            client, MODELO_ESTRATEGICO,
            prompt_sistema, prompt_usuario,
            max_tokens=8000, temperature=0.3,
        )
        json_str = _reparar_json_llm(extrair_json_valido(texto))
        resultado = json.loads(json_str)
        vetores_raw = resultado.get("vetores_estrategicos", [])

        # Post-processamento determinístico (idêntico ao v25)
        vetores_processados = []
        for i, v in enumerate(vetores_raw):
            if not v.get("id"):
                v["id"] = f"VE-{i+1:03d}"

            sinais_ids = v.get("sinais_ids", [])
            n = max(len(sinais_ids), v.get("n_sinais", 1))

            # Resolve índices do subset para títulos reais (fix index-mismatch bug)
            if not v.get("sinais_relacionados"):
                v["sinais_relacionados"] = [
                    sinais_validos_sorted[sid].get("titulo_pt")
                    or sinais_validos_sorted[sid].get("titulo", "")
                    for sid in sinais_ids
                    if isinstance(sid, int) and 0 <= sid < len(sinais_validos_sorted)
                ]

            sinais_ref = [sinais_validos_sorted[sid] for sid in sinais_ids
                          if isinstance(sid, int) and 0 <= sid < len(sinais_validos_sorted)]
            if sinais_ref:
                nov_med = sum(_clamp_float(s.get("analise", {}).get("novidade_tecnologica", 5.0), 0, 10)
                              for s in sinais_ref) / len(sinais_ref)
            else:
                nov_med = 5.0
            pressao_recalc = calcular_pressao_estrategica(
                impacto_brasil=_clamp_float(v.get("impacto_brasil", 5.0), 0, 10),
                score_final=_clamp_float(v.get("pressao_estrategica", 5.0), 0, 10),
                exposicao_brasil=_clamp_float(v.get("exposicao_brasil", 0.5)),
                n_sinais=n,
                confianca_analise=_clamp_float(v.get("confianca_analise", 0.7)),
                novidade_tecnologica=nov_med,
            )
            v["pressao_estrategica"] = pressao_recalc

            janela_dias = int(v.get("janela_decisoria_dias", 90))
            janela_dias = min(URGENCIAS_VALIDAS, key=lambda x: abs(x - janela_dias))
            v["janela_decisoria_dias"] = janela_dias
            v["janela_decisoria_categoria"] = classificar_janela_decisoria(janela_dias)

            v["quadrante_executivo"] = classificar_quadrante_executivo(
                pressao_recalc, janela_dias
            )

            v["n_sinais"] = n

            v.setdefault("custo_espera", "Médio")
            v.setdefault("irreversibilidade", "Média")
            v.setdefault("momento_vetor", "Estável")
            v.setdefault("intensidade_momento", 0.5)
            v.setdefault("setores_afetados", [])
            v.setdefault("mecanismo_causal", "")
            v.setdefault("consequencia_inacao", "")
            v.setdefault("decisao_recomendada", "")
            v.setdefault("materialidade", {})
            v.setdefault("nome", f"Vetor Estratégico {i+1}")
            v["nome"] = _truncar_inteligente(v["nome"], 80)

            vetores_processados.append(v)

        _ORDEM = {"Mobilizar Agora": 0, "Capturar Vantagem": 1,
                  "Monitorar Vetores": 2, "Ruído Operacional": 3}
        vetores_processados.sort(
            key=lambda v: (
                _ORDEM.get(v.get("quadrante_executivo", "Monitorar Vetores"), 3),
                -v.get("pressao_estrategica", 0),
            )
        )

        print(f"     [OK] {len(vetores_processados)} vetores estratégicos gerados.")
        return vetores_processados

    except Exception as e:
        print(f"  [!] Erro no parsing de vetores estratégicos: {e}")
        try:
            print("  -> Tentando recuperação parcial de vetores do texto bruto...")
            matches = re.findall(r'\{[^{}]*"nome"[^{}]*\}', texto, re.DOTALL)
            parciais = []
            for m in matches[:8]:
                try:
                    obj = json.loads(_reparar_json_llm(m))
                    if obj.get("nome") and isinstance(obj.get("nome"), str):
                        parciais.append(obj)
                except Exception:
                    pass
            if len(parciais) >= 3:
                print(f"  -> Recuperados {len(parciais)} vetores parciais.")
                for i, v in enumerate(parciais):
                    if not v.get("id"):
                        v["id"] = f"VE-{i+1:03d}"
                    sinais_ids = v.get("sinais_ids", [])
                    n = max(len(sinais_ids), v.get("n_sinais", 1), 1)
                    if not v.get("sinais_relacionados"):
                        v["sinais_relacionados"] = [
                            sinais_validos_sorted[sid].get("titulo_pt")
                            or sinais_validos_sorted[sid].get("titulo", "")
                            for sid in sinais_ids
                            if isinstance(sid, int) and 0 <= sid < len(sinais_validos_sorted)
                        ]
                    sinais_ref2 = [sinais_validos_sorted[sid] for sid in sinais_ids
                                   if isinstance(sid, int) and 0 <= sid < len(sinais_validos_sorted)]
                    nov2 = (sum(_clamp_float(s.get("analise", {}).get("novidade_tecnologica", 5.0), 0, 10)
                                for s in sinais_ref2) / len(sinais_ref2)) if sinais_ref2 else 5.0
                    pressao = calcular_pressao_estrategica(
                        _clamp_float(v.get("impacto_brasil", 5.0), 0, 10),
                        _clamp_float(v.get("pressao_estrategica", 5.0), 0, 10),
                        _clamp_float(v.get("exposicao_brasil", 0.5)),
                        n,
                        _clamp_float(v.get("confianca_analise", 0.7)),
                        novidade_tecnologica=nov2,
                    )
                    v["pressao_estrategica"] = pressao
                    janela = int(v.get("janela_decisoria_dias", 90))
                    janela = min(URGENCIAS_VALIDAS, key=lambda x: abs(x - janela))
                    v["janela_decisoria_dias"] = janela
                    v["janela_decisoria_categoria"] = classificar_janela_decisoria(janela)
                    v["quadrante_executivo"] = classificar_quadrante_executivo(pressao, janela)
                    v["n_sinais"] = n
                    for campo in ("custo_espera", "irreversibilidade", "momento_vetor",
                                  "setores_afetados", "mecanismo_causal",
                                  "consequencia_inacao", "decisao_recomendada", "materialidade"):
                        v.setdefault(campo, [] if campo == "setores_afetados" else
                                     {} if campo == "materialidade" else "Médio"
                                     if campo in ("custo_espera",) else
                                     "Média" if campo == "irreversibilidade" else
                                     "Estável" if campo == "momento_vetor" else "")
                _ORDEM = {"Mobilizar Agora": 0, "Capturar Vantagem": 1,
                          "Monitorar Vetores": 2, "Ruído Operacional": 3}
                parciais.sort(key=lambda v: (
                    _ORDEM.get(v.get("quadrante_executivo", "Monitorar Vetores"), 3),
                    -v.get("pressao_estrategica", 0),
                ))
                return parciais
        except Exception as e2:
            print(f"  [!] Recuperação parcial falhou: {e2}")
        print("  -> Usando fallback por tipo_sinal.")
        return _vetores_fallback_from_itens(itens_analisados)


def _vetores_fallback_from_itens(itens: list[dict]) -> list[dict]:
    """Fallback: cria vetores simples agrupando por tipo_sinal quando o LLM falha."""
    por_tipo: dict[str, list] = defaultdict(list)
    for item in itens:
        ts = item.get("analise", {}).get("tipo_sinal", "Sinal Tecnológico")
        if item.get("analise", {}).get("impacto_brasil", 0) >= 3.0:
            por_tipo[ts].append(item)

    vetores = []
    tipo_map = {
        "Risco Regulatório":       ("VE-001", "Risco Regulatório"),
        "Oportunidade de Mercado": ("VE-002", "Oportunidade de Mercado"),
        "Choque Geopolítico":      ("VE-003", "Choque Geopolítico"),
        "Sinal Tecnológico":       ("VE-004", "Sinal Tecnológico"),
    }
    for tipo, grupo in por_tipo.items():
        if not grupo:
            continue
        ve_id, ve_tipo = tipo_map.get(tipo, (f"VE-{len(vetores)+1:03d}", "Misto"))
        scores_imp = [i.get("analise", {}).get("impacto_brasil", 0) for i in grupo]
        imp_med = sum(scores_imp) / len(scores_imp)
        exp_med = sum(i.get("analise", {}).get("exposicao_brasil", 0.5) for i in grupo) / len(grupo)
        conf_med = sum(i.get("analise", {}).get("confianca_analise", 0.7) for i in grupo) / len(grupo)
        score_med = sum(i.get("score_final", 5.0) for i in grupo) / len(grupo)
        nov_med3 = sum(_clamp_float(i.get("analise", {}).get("novidade_tecnologica", 5.0), 0, 10)
                       for i in grupo) / len(grupo)
        urg_min = min(i.get("analise", {}).get("urgencia_dias", 180) for i in grupo)
        pressao = calcular_pressao_estrategica(imp_med, score_med, exp_med, len(grupo), conf_med,
                                               novidade_tecnologica=nov_med3)
        janela_cat = classificar_janela_decisoria(urg_min)
        quadrante = classificar_quadrante_executivo(pressao, urg_min)

        vetores.append({
            "id": ve_id,
            "nome": tipo,
            "tipo": ve_tipo,
            "descricao_executiva": f"Consolidação de {len(grupo)} sinais de {tipo} com impacto relevante para o Brasil.",
            "pressao_estrategica": pressao,
            "janela_decisoria_dias": urg_min,
            "janela_decisoria_categoria": janela_cat,
            "custo_espera": "Alto" if urg_min <= 90 else "Médio",
            "irreversibilidade": "Alta" if urg_min <= 30 else "Média",
            "momento_vetor": "Acelerando" if len(grupo) > 5 else "Estável",
            "intensidade_momento": round(min(len(grupo) / 10.0, 1.0), 2),
            "impacto_brasil": round(imp_med, 1),
            "exposicao_brasil": round(exp_med, 2),
            "confianca_analise": round(conf_med, 2),
            "setores_afetados": list({i.get("analise", {}).get("setor_afetado", "") for i in grupo if i.get("analise", {}).get("setor_afetado")})[:4],
            "materialidade": {"capex": "Média", "opex": "Média", "regulatorio": "Média", "competitividade": "Média", "reputacional": "Baixa"},
            "mecanismo_causal": f"Múltiplos sinais convergem em {tipo} com janela decisória de {urg_min} dias.",
            "consequencia_inacao": "Perda de janela de decisão e aumento do custo de adequação.",
            "decisao_recomendada": "Revisar exposição e acionar comitê executivo para priorização.",
            "sinais_ids": list(range(min(len(grupo), 10))),
            "n_sinais": len(grupo),
            "quadrante_executivo": quadrante,
        })

    _ORDEM = {"Mobilizar Agora": 0, "Capturar Vantagem": 1, "Monitorar Vetores": 2, "Ruído Operacional": 3}
    vetores.sort(key=lambda v: (_ORDEM.get(v.get("quadrante_executivo", "Monitorar Vetores"), 3), -v.get("pressao_estrategica", 0)))
    return vetores


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 4 — Briefing Executivo (claude-opus-4-7)
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_briefing_diario(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
    fatos_canonicos: list[dict] | None = None,
) -> dict:
    """temperatura=None (Opus 4.7 constraint)."""
    print(f"  -> Redigindo Briefing Executivo Diário ({MODELO_BRIEFING})...")

    _FALLBACK = {
        "titulo": "Síntese do ciclo indisponível.",
        "subtitulo": "",
        "frase_de_abertura": "",
        "paragrafos": [],
        "implicacao_cruzada": "",
    }

    prompt_sistema = (
        "Você é o editor-chefe de um Think Tank de Tech & Energy "
        "especializado em governança corporativa e conselhos de administração. "
        "Regras absolutas: "
        "(1) PROIBIDO negrito, tags HTML (<em>, <strong>, <b>, etc.) ou Markdown "
        "(**texto**, *texto*) dentro dos valores JSON. Todo texto deve ser plain text. "
        "(2) Ortografia e ACENTUAÇÃO CORRETAS do português brasileiro — obrigatório. "
        "(3) Tom analítico, denso e executivo: cada frase deve carregar decisão ou implicação. "
        "(4) Retorne APENAS JSON válido — sem texto fora do JSON, sem blocos ```."
        + CALIBRACAO_TEMPORAL_CAUSAL
        + DIRETRIZES_TEMATICAS
    )

    sinais = [
        f"- {item['analise'].get('titulo_pt') or item.get('titulo_pt') or item['titulo']}: "
        f"{item['analise']['resumo_executivo']}"
        for item in itens_analisados
        if item.get("analise", {}).get("score_final", 0) >= 7.0
    ]

    if not sinais:
        _FALLBACK["titulo"] = "Nenhum sinal crítico identificado neste ciclo."
        return _FALLBACK

    fatos_txt = _formatar_fatos_para_prompt(fatos_canonicos or [])

    prompt_usuario = f"""Com base nos {len(sinais)} sinais críticos do ciclo abaixo, \
produza o Briefing Executivo Diário.

Sinais Críticos:
{chr(10).join(sinais[:20])}

{fatos_txt}

Retorne EXATAMENTE este JSON (sem texto fora do JSON, sem markdown, sem ```):
{{
  "titulo": "Título declarativo do ciclo — 8 a 14 palavras que nomeiam o vetor dominante",
  "subtitulo": "1 frase que amplia o título sem repeti-lo",
  "frase_de_abertura": "Lide executivo de 2 a 3 frases posicionando contexto macro e vetor dominante para um CEO ou conselheiro",
  "paragrafos": [
    {{
      "horizonte_decisao": "90d",
      "texto": "Parágrafo denso com implicações de curto prazo (próximos 90 dias). Mínimo 3 frases analíticas.",
      "decisao_implicada": "Diretriz de ação executiva específica para 90 dias",
      "fontes_ids": []
    }},
    {{
      "horizonte_decisao": "6m",
      "texto": "Parágrafo denso com implicações de médio prazo (6 meses). Mínimo 3 frases analíticas.",
      "decisao_implicada": "Diretriz de ação executiva específica para 6 meses",
      "fontes_ids": []
    }},
    {{
      "horizonte_decisao": "12m",
      "texto": "Parágrafo denso com implicações de longo prazo (12 meses). Mínimo 3 frases analíticas.",
      "decisao_implicada": "Diretriz de ação executiva específica para 12 meses",
      "fontes_ids": []
    }}
  ],
  "implicacao_cruzada": "1 frase declarativa que conecta infraestrutura, tecnologia, energia e regulação em síntese para C-level"
}}
"""

    try:
        texto = _chamar_claude(
            client, MODELO_BRIEFING,
            prompt_sistema, prompt_usuario,
            max_tokens=4096, temperature=None,
        )
        briefing = json.loads(extrair_json_valido(texto))
        print("     [OK] Briefing estruturado concluído.")
        return briefing
    except Exception as e:
        print(f"  [!] Erro na geração do briefing: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# Mesa de Decisão por Vetores (v25 mantido, agora com 360d)
# ═══════════════════════════════════════════════════════════════════════════════

def gerar_mesa_decisao_por_vetores(vetores: list[dict]) -> dict:
    """
    Monta mesa_decisao a partir de vetores estratégicos.
    v26: agora inclui coluna 360_dias para alinhar com horizontes da thesis.
    """
    mesa: dict = {"30_dias": [], "90_dias": [], "180_dias": [], "360_dias": []}

    _ORDEM = {"Mobilizar Agora": 0, "Capturar Vantagem": 1, "Monitorar Vetores": 2, "Ruído Operacional": 3}
    vetores_ordenados = sorted(
        vetores,
        key=lambda v: (
            _ORDEM.get(v.get("quadrante_executivo", "Monitorar Vetores"), 3),
            -v.get("pressao_estrategica", 0),
        ),
    )

    for v in vetores_ordenados:
        if v.get("quadrante_executivo") == "Ruído Operacional":
            continue
        if v.get("pressao_estrategica", 0) < 4.0:
            continue

        janela  = v.get("janela_decisoria_dias", 360)
        custo   = v.get("custo_espera", "Médio")
        acao    = v.get("decisao_recomendada", "").strip()
        detalhe = v.get("consequencia_inacao", "").strip()
        nome    = v.get("nome", "").strip()

        if not acao:
            continue

        entrada = {"acao": acao, "detalhe": detalhe, "vetor_nome": nome}

        if janela <= 30 or custo == "Crítico":
            if len(mesa["30_dias"]) < 5:
                mesa["30_dias"].append(entrada)
        elif janela <= 90:
            if len(mesa["90_dias"]) < 5:
                mesa["90_dias"].append(entrada)
        elif janela <= 180:
            if len(mesa["180_dias"]) < 5:
                mesa["180_dias"].append(entrada)
        elif janela <= 360:
            if len(mesa["360_dias"]) < 5:
                mesa["360_dias"].append(entrada)

    return mesa


# ═══════════════════════════════════════════════════════════════════════════════
# Validação e Geração — Executive Thesis
# ═══════════════════════════════════════════════════════════════════════════════

def validar_executive_thesis(thesis: dict) -> bool:
    """Valida campos obrigatórios da executive_thesis (req. v32 §5.3)."""
    if not thesis.get("frase_central"):
        return False
    if len(thesis.get("mudancas_estruturais", [])) < 3:
        return False
    if len(thesis.get("decisoes_prioritarias", [])) < 3:
        return False
    for d in thesis["decisoes_prioritarias"]:
        if not d.get("acao") or not d.get("horizonte"):
            return False
        if d["horizonte"] not in ("30d", "90d", "180d", "360d"):
            return False
    return True


def gerar_executive_thesis(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
    vetores: list[dict],
    fatos_canonicos: list[dict] | None = None,
) -> dict:
    """Gera o objeto executive_thesis via Sonnet."""
    print(f"  -> Gerando Executive Thesis ({MODELO_ESTRATEGICO})...")

    _FALLBACK: dict = {
        "frase_central": "",
        "mudancas_estruturais": [],
        "decisoes_prioritarias": [],
    }

    sinais_top = [
        {
            "titulo": i.get("titulo_pt") or i.get("titulo", ""),
            "resumo": i.get("analise", {}).get("resumo_executivo", ""),
            "impacto_brasil": i.get("analise", {}).get("impacto_brasil", 0),
            "tipo_sinal": i.get("analise", {}).get("tipo_sinal", ""),
            "decisao": i.get("analise", {}).get("decisao_sugerida", ""),
        }
        for i in sorted(itens_analisados,
                        key=lambda x: x.get("analise", {}).get("impacto_brasil", 0),
                        reverse=True)[:15]
    ]

    vetores_resumo = [
        {
            "nome": v.get("nome", ""),
            "pressao": v.get("pressao_estrategica", 0),
            "quadrante": v.get("quadrante_executivo", ""),
            "decisao": v.get("decisao_recomendada", ""),
            "janela_cat": v.get("janela_decisoria_categoria", ""),
            "janela_dias": v.get("janela_decisoria_dias", 180),
        }
        for v in sorted(vetores, key=lambda v: v.get("pressao_estrategica", 0), reverse=True)[:8]
    ]

    prompt_sistema = (
        "Você é um estrategista sênior de um think tank de Tech & Energy. "
        "Sintetize a tese executiva do ciclo de inteligência com precisão e assertividade. "
        "OBRIGATÓRIO: use sempre acentuação e ortografia CORRETAS do português brasileiro. "
        "Exemplos: análise, decisão, regulação, estratégia, armazenamento, gestão, ação, "
        "integração, posição, solução, avaliação, nível, técnico, específico, público. "
        "PROIBIDO: negrito, tags HTML, Markdown, texto fora do JSON. "
        "Todo texto nos valores JSON deve ser plain text sem qualquer markup."
        + CALIBRACAO_TEMPORAL_CAUSAL
        + DIRETRIZES_TEMATICAS
    )

    fatos_txt = _formatar_fatos_para_prompt(fatos_canonicos or [])

    payload = json.dumps(
        {"sinais_criticos": sinais_top, "vetores_estrategicos": vetores_resumo},
        ensure_ascii=False,
    )

    prompt_usuario = f"""Com base nos sinais e vetores estratégicos do ciclo abaixo, gere o objeto executive_thesis:

{payload}

{fatos_txt}

REGRA DE COERÊNCIA COM A MESA DE DECISÃO:
Cada decisão prioritária deve ter horizonte ALINHADO com a janela de um vetor real.
Se nenhum vetor tem janela_dias <= 30, NÃO crie decisão com horizonte "30d".
Use os horizontes dos vetores acima como guia: {sorted(set(v["janela_dias"] for v in vetores_resumo)) if vetores_resumo else [90, 180, 360]}.

Retorne EXATAMENTE este JSON (sem texto fora do JSON, sem markdown):
{{
  "frase_central": "Uma frase de 20-30 palavras que sintetiza a mudança estrutural do ciclo. Começa com o que está mudando — não com 'O ciclo mostra' ou 'Observa-se'. Deve ser assertiva, específica ao Brasil, e nomear pelo menos um vetor concreto.",
  "mudancas_estruturais": [
    "Frase 1: substantivo concreto (empresa/regulação/tecnologia/mercado) + transformação real em curso. Máximo 25 palavras.",
    "Frase 2: idem.",
    "Frase 3: idem."
  ],
  "decisoes_prioritarias": [
    {{"acao": "Verbo no infinitivo + ação concreta, nível board. Diferente das ações operacionais da mesa CxO.", "horizonte": "90d"}},
    {{"acao": "...", "horizonte": "180d"}},
    {{"acao": "...", "horizonte": "360d"}}
  ]
}}

REGRAS OBRIGATÓRIAS:
- frase_central: específica ao Brasil, nomeia ator ou evento real do ciclo.
- mudancas_estruturais: exatamente 3 frases, cada uma com substantivo concreto no início.
- decisoes_prioritarias: exatamente 3 objetos. Horizonte obrigatório: "30d", "90d", "180d" ou "360d".
- Acentuação e ortografia CORRETAS em todo o JSON.
"""

    try:
        texto = _chamar_claude(
            client, MODELO_ESTRATEGICO,
            prompt_sistema, prompt_usuario,
            max_tokens=1500, temperature=0.2,
        )
        texto_rep = _reparar_json_llm(texto)
        thesis = json.loads(extrair_json_valido(texto_rep))

        thesis.setdefault("mudancas_estruturais", [])
        thesis.setdefault("decisoes_prioritarias", [])
        while len(thesis["mudancas_estruturais"]) < 3:
            thesis["mudancas_estruturais"].append("Transformação estrutural em andamento.")
        while len(thesis["decisoes_prioritarias"]) < 3:
            hor = ["90d", "180d", "360d"][len(thesis["decisoes_prioritarias"])]
            thesis["decisoes_prioritarias"].append({"acao": "Avaliar impacto no ciclo.", "horizonte": hor})

        if validar_executive_thesis(thesis):
            print(f"  [OK] Executive Thesis gerada — frase_central: {thesis['frase_central'][:60]}...")
            return thesis
        else:
            print("  [!] Executive Thesis inválida — usando fallback vazio")
            return _FALLBACK
    except Exception as e:
        print(f"  [!] Erro ao gerar Executive Thesis: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 5 — Dashboard Assembly (Python puro)
# ═══════════════════════════════════════════════════════════════════════════════

def _descricao_pressao(score: float) -> str:
    if score >= 80:
        return "Muito elevado"
    if score >= 70:
        return "Alta pressão"
    if score >= 60:
        return "Moderado"
    return "Em observação"


def _mesa_decisao_from_itens(itens: list[dict]) -> dict:
    """Fallback: monta mesa por urgencia_dias quando não há vetores."""
    mesa: dict = {"30_dias": [], "90_dias": [], "180_dias": [], "360_dias": []}
    sorted_itens = sorted(
        itens,
        key=lambda x: x.get("analise", {}).get("impacto_brasil", 0),
        reverse=True,
    )
    for item in sorted_itens:
        analise = item.get("analise", {})
        urgencia = analise.get("urgencia_dias", 180)
        acao     = analise.get("decisao_sugerida", "").strip()
        detalhe  = analise.get("mecanismo_impacto", "").strip()
        if not acao:
            continue
        key = f"{urgencia}_dias"
        if key in mesa and len(mesa[key]) < 5:
            entrada: dict = {"acao": acao}
            if detalhe:
                entrada["detalhe"] = detalhe
            mesa[key].append(entrada)
    return mesa


def _texto_briefing_executivo(briefing_dict: dict) -> str:
    partes = [briefing_dict.get("frase_de_abertura", "")]
    for p in briefing_dict.get("paragrafos", []):
        partes.append(p.get("texto", ""))
    conclusao = briefing_dict.get("implicacao_cruzada", "")
    if conclusao:
        partes.append(conclusao)
    return " ".join(s for s in partes if s)


def montar_dashboard(
    itens: list[dict],
    cenarios: list[dict],
    clusters: list[dict],
    briefing: dict,
    mesa_decisao: dict,
    executive_thesis: dict | None = None,
    vetores_dominantes: list[str] | None = None,
    fatos_canonicos: list[dict] | None = None,
) -> dict:
    por_tipo: dict[str, list] = defaultdict(list)
    for item in itens:
        ts = item.get("analise", {}).get("tipo_sinal", "Sinal Tecnológico")
        por_tipo[ts].append(item)

    def _ips_entry(tipo_sinal: str) -> dict:
        grupo = por_tipo.get(tipo_sinal, [])
        n_total = len(grupo)
        if n_total == 0:
            return {"score": 0, "sinais": 0, "tendencia": 0, "descricao": "Em observação"}
        # Filtra apenas itens com impacto real (>= 3.0) para não diluir o score
        relevantes = [i for i in grupo
                      if i.get("analise", {}).get("impacto_brasil", 0) >= 3.0]
        n_rel = len(relevantes)
        if n_rel == 0:
            return {"score": 0, "sinais": n_total, "tendencia": 0, "descricao": "Em observação"}
        scores_br = [i.get("analise", {}).get("impacto_brasil", 0) for i in relevantes]
        score = round(sum(scores_br) / n_rel * 10)
        urgentes = sum(
            1 for i in relevantes
            if i.get("analise", {}).get("urgencia_dias", 360) <= 90
        )
        return {
            "score":     score,
            "sinais":    n_total,   # total bruto (inclui baixo impacto) — transparência
            "tendencia": urgentes,  # urgentes entre os relevantes (impacto >= 3)
            "descricao": _descricao_pressao(score),
        }

    indices_pressao = {
        "risco_regulatorio":    _ips_entry("Risco Regulatório"),
        "oportunidade_mercado": _ips_entry("Oportunidade de Mercado"),
        "choque_geopolitico":   _ips_entry("Choque Geopolítico"),
        "sinal_tecnologico":    _ips_entry("Sinal Tecnológico"),
    }

    total_sinais = len(itens)
    scores_brasil = [
        i.get("analise", {}).get("impacto_brasil", 0)
        for i in itens
        if i.get("analise", {}).get("impacto_brasil", 0) > 0
    ]
    score_ips_medio = round(sum(scores_brasil) / len(scores_brasil), 1) if scores_brasil else 0.0

    cenarios_dash = []
    for c in cenarios:
        cenarios_dash.append({
            "numero":              c.get("numero", 1),
            "nome":                c.get("titulo_cenario", c.get("nome", "Cenário")),
            "probabilidade":       c.get("probabilidade", 33),
            "impacto":             c.get("impacto", "Alto"),
            "tipo":                c.get("tipo", "Misto"),
            "descricao_expandida": c.get("descricao_expandida", c.get("narrativa_macro", "")),
        })

    return {
        "ciclo":               HOJE_ISO,
        "versao":              "v27",
        "total_sinais":        total_sinais,
        "score_ips_medio":     score_ips_medio,
        "fontes_monitoradas":  102,  # collector_v6 v11.0 — 102 fontes ativas
        "paises_cobertos":     27,
        "indices_pressao":     indices_pressao,
        "clusters":            clusters,
        "cenarios":            cenarios_dash,
        "briefing_executivo":  _texto_briefing_executivo(briefing),
        "mesa_decisao":        mesa_decisao,
        "executive_thesis":    executive_thesis or {},
        "vetores_dominantes":  vetores_dominantes or [],
        "fatos_canonicos":     fatos_canonicos or [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CAMADA 3 — Fase 5.5: Auditoria de Coerência (Python puro)
# ═══════════════════════════════════════════════════════════════════════════════

def auditar_coerencia_thesis_mesa(
    thesis: dict,
    mesa: dict,
    vetores: list[dict],
) -> tuple[dict, list[str]]:
    """
    Garante que cada horizonte citado em executive_thesis.decisoes_prioritarias
    tenha contraparte real na mesa_decisao.

    Estratégia: rebaixar o horizonte da thesis para a janela mais próxima com
    vetor disponível (decisão mais honesta que inventar uma ação inexistente).

    Retorna (thesis_ajustada, lista_de_logs).
    """
    logs: list[str] = []

    if not thesis or not thesis.get("decisoes_prioritarias"):
        return thesis, logs

    # Mapeia horizontes da thesis para chaves da mesa
    MAPA_HOR_MESA = {"30d": "30_dias", "90d": "90_dias",
                     "180d": "180_dias", "360d": "360_dias"}

    # Quais horizontes têm pelo menos 1 vetor na mesa?
    horizontes_disponiveis = [
        hor for hor, key in MAPA_HOR_MESA.items()
        if mesa.get(key) and len(mesa[key]) > 0
    ]

    # Quais horizontes têm vetor real (pressão >= 4.0) na lista de vetores?
    janelas_com_vetor = sorted({
        v.get("janela_decisoria_dias", 360)
        for v in vetores
        if v.get("pressao_estrategica", 0) >= 4.0
    })

    if not horizontes_disponiveis and not janelas_com_vetor:
        logs.append("Mesa de decisão e vetores vazios — thesis preservada sem ajuste.")
        return thesis, logs

    HOR_PARA_DIAS = {"30d": 30, "90d": 90, "180d": 180, "360d": 360}
    DIAS_PARA_HOR = {30: "30d", 90: "90d", 180: "180d", 360: "360d"}

    for d in thesis["decisoes_prioritarias"]:
        hor_original = d.get("horizonte", "")
        if hor_original not in MAPA_HOR_MESA:
            continue

        # Se a mesa tem entrada para este horizonte, está coerente
        if hor_original in horizontes_disponiveis:
            continue

        # Mesa não tem — buscar o horizonte mais próximo (próximo do mais
        # restrito para o mais folgado) que tenha vetor disponível
        dias_original = HOR_PARA_DIAS[hor_original]

        if janelas_com_vetor:
            # Próxima janela igual ou maior; se nada igual ou maior, próxima menor
            maiores = [j for j in janelas_com_vetor if j >= dias_original]
            if maiores:
                novo_dias = min(maiores)
            else:
                novo_dias = max(janelas_com_vetor)
            novo_hor = DIAS_PARA_HOR.get(novo_dias, hor_original)
        else:
            novo_hor = horizontes_disponiveis[0]

        if novo_hor != hor_original:
            d["horizonte"] = novo_hor
            d["_ajuste_auditoria"] = (
                f"Rebaixado de {hor_original} para {novo_hor}: "
                f"ausência de vetor com pressão suficiente na janela {hor_original}."
            )
            logs.append(
                f"[thesis↔mesa] decisão '{d.get('acao','')[:50]}...' "
                f"horizonte {hor_original} → {novo_hor}"
            )

    return thesis, logs


def auditar_widget_titulos_dashboard(dashboard: dict, vetores: list[dict]) -> list[str]:
    """
    Verifica regras de coerência v2.0 do Radar Estratégico:
    - vetores dominantes citados no dashboard devem existir na lista de vetores
    - briefing não deve referenciar mesa vazia

    Retorna lista de warnings (não bloqueia execução).
    """
    logs: list[str] = []

    nomes_vetores = {v.get("nome", "") for v in vetores}
    for nome_dom in dashboard.get("vetores_dominantes", []):
        if nome_dom and nome_dom not in nomes_vetores:
            logs.append(f"[coerência] vetor dominante '{nome_dom}' não encontrado na lista de vetores.")

    mesa = dashboard.get("mesa_decisao", {})
    total_mesa = sum(len(v) for v in mesa.values() if isinstance(v, list))
    if total_mesa == 0:
        logs.append("[coerência] mesa_decisao está completamente vazia — verificar geração de vetores.")

    return logs


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 6 — Visualização Dinâmica (claude-opus-4-7)
# ═══════════════════════════════════════════════════════════════════════════════

_TIPOS_VIZ_SCHEMAS = """
TODOS OS TIPOS incluem o campo obrigatório:
  "justificativa": "1-2 frases explicando por que ESTE tipo foi escolhido para o ciclo atual"

TIPOS DE VISUALIZAÇÃO DISPONÍVEIS (escolha UM):

1. matriz_2x2 — Use quando dois eixos de incerteza dominantes estruturam cenários distintos.
   Ideal para: ciclos com 2-4 cenários claramente diferenciados.
   Schema:
   {
     "tipo": "matriz_2x2",
     "titulo": "Título da visualização (máx 8 palavras)",
     "subtitulo": "1 frase contextualizando o ciclo",
     "justificativa": "Por que este tipo foi escolhido para o ciclo",
     "eixos": {
       "x": {"nome": "Nome conciso eixo X", "polo_baixo": "Extremo baixo", "polo_alto": "Extremo alto"},
       "y": {"nome": "Nome conciso eixo Y", "polo_baixo": "Extremo baixo", "polo_alto": "Extremo alto"}
     },
     "pontos": [
       {
         "id": "A",
         "titulo": "Título do cenário (máx 5 palavras)",
         "x": 0.75,
         "y": 0.25,
         "cor": "#C4521A",
         "descricao": "1 frase sobre o cenário"
       }
     ]
   }
   REGRA: x e y são floats 0-1. Cada ponto em quadrante diferente.

2. barras_urgencia — Use quando múltiplos temas com scores contrastantes emergem do ciclo.
   Schema:
   {
     "tipo": "barras_urgencia",
     "titulo": "Título da visualização",
     "subtitulo": "1 frase contextualizando",
     "justificativa": "Por que este tipo foi escolhido para o ciclo",
     "barras": [
       {
         "label": "Tema ou item (máx 5 palavras)",
         "valor": 8.5,
         "cor": "#C4521A",
         "descricao": "1 frase sobre o tema"
       }
     ]
   }
   REGRA: valor é float 0-10. Ordene do maior para o menor.

3. radar_setorial — Use quando sinais se distribuem por 4+ setores com intensidade variada.
   Schema:
   {
     "tipo": "radar_setorial",
     "titulo": "Título da visualização",
     "subtitulo": "1 frase contextualizando",
     "justificativa": "Por que este tipo foi escolhido",
     "eixos": ["Energia", "IA", "Regulação", "Infraestrutura", "Financiamento"],
     "series": [
       {
         "nome": "Ciclo Atual",
         "valores": [7.2, 8.1, 6.3, 7.5, 5.9],
         "cor": "#C4521A"
       }
     ]
   }
   REGRA CRÍTICA: valores[] deve ter EXATAMENTE o mesmo número de itens que eixos[].

4. timeline_regulatoria — Use quando eventos regulatórios temporais dominam o ciclo.
   Schema:
   {
     "tipo": "timeline_regulatoria",
     "titulo": "Título da visualização",
     "subtitulo": "1 frase contextualizando",
     "justificativa": "Por que este tipo foi escolhido",
     "eventos": [
       {
         "data": "2026-Q2",
         "titulo": "Evento (máx 5 palavras)",
         "tipo": "regulatorio",
         "impacto": "alto",
         "descricao": "1 frase sobre o evento"
       }
     ]
   }
   REGRA: tipo em {regulatorio, politico, tecnologico, mercado}. impacto em {alto, médio, baixo}.

5. scatter_convergencia — Use quando sinais de múltiplos setores convergem em alta urgência.
   Schema:
   {
     "tipo": "scatter_convergencia",
     "titulo": "Título da visualização",
     "subtitulo": "1 frase contextualizando",
     "justificativa": "Por que este tipo foi escolhido",
     "eixo_x_label": "Score global (0-10)",
     "eixo_y_label": "Relevância decisória Brasil (0-10)",
     "grupos": ["Alerta", "Oportunidade", "Tendência"],
     "pontos": [
       {
         "titulo": "Título curto (máx 4 palavras)",
         "x": 8.2,
         "y": 7.5,
         "grupo": "Alerta",
         "descricao": "1 frase"
       }
     ]
   }
"""


def gerar_visualizacao(
    client: anthropic.Anthropic,
    itens_analisados: list[dict],
    matriz_incertezas: dict,
) -> dict:
    print(f"  -> Gerando Visualização Dinâmica ({MODELO_VISUALIZACAO})...")

    ex = matriz_incertezas.get("eixo_x", {"nome": "Eixo X", "polo_baixo": "—", "polo_alto": "—"})
    ey = matriz_incertezas.get("eixo_y", {"nome": "Eixo Y", "polo_baixo": "—", "polo_alto": "—"})

    _FALLBACK = {
        "tipo": "matriz_2x2",
        "titulo": "Matriz de Incertezas do Ciclo",
        "subtitulo": "Forças motrizes identificadas no ciclo",
        "eixos": {"x": ex, "y": ey},
        "pontos": [],
    }

    contagem_setores: dict[str, int] = {}
    contagem_vetores: dict[str, int] = {}
    contagem_regulatorio = 0
    top_itens: list[dict] = []

    for item in itens_analisados:
        analise = item.get("analise", {})
        score   = analise.get("score_final", 0)
        tema    = analise.get("tema_analisado", "Outros")
        vetor   = analise.get("vetor_estrategico", "Neutro")
        contagem_setores[tema] = contagem_setores.get(tema, 0) + 1
        contagem_vetores[vetor] = contagem_vetores.get(vetor, 0) + 1
        if "regulação" in tema.lower() or "edital" in tema.lower():
            contagem_regulatorio += 1
        if score >= 7.5:
            top_itens.append({
                "titulo": _truncar_inteligente(
                    item.get("titulo_pt") or analise.get("titulo_pt") or item.get("titulo", ""),
                    70
                ),
                "score":  round(score, 1),
                "setor":  tema,
                "vetor":  vetor,
                "resumo": _truncar_inteligente(analise.get("resumo_executivo", ""), 100),
            })

    top_itens_sorted = sorted(top_itens, key=lambda x: -x["score"])[:12]
    n_setores_ativos = len([k for k, v in contagem_setores.items() if v >= 2])

    prompt_sistema = (
        "Você é o cientista de dados chefe do Think Tank Tech & Energy. "
        "Escolha o tipo de visualização mais impactante para os dados do ciclo "
        "e gere os dados completos para renderização SVG. "
        "Regras absolutas: "
        "(1) Retorne APENAS JSON válido — sem texto fora do JSON, sem blocos ```. "
        "(2) Siga rigorosamente o schema do tipo escolhido. "
        "(3) Títulos e labels em português do Brasil."
        + CALIBRACAO_TEMPORAL_CAUSAL
    )

    prompt_usuario = f"""Analise os dados do ciclo e gere a visualização mais adequada.

ESTATÍSTICAS:
- Total de sinais: {len(itens_analisados)}
- Setores com 2+ sinais: {n_setores_ativos}
- Distribuição setorial: {json.dumps(contagem_setores, ensure_ascii=False)}
- Distribuição de vetores: {json.dumps(contagem_vetores, ensure_ascii=False)}
- Sinais regulatórios/editais: {contagem_regulatorio}

EIXOS (Fase 2):
- Eixo X: {ex.get('nome','?')} ({ex.get('polo_baixo','?')} ↔ {ex.get('polo_alto','?')})
- Eixo Y: {ey.get('nome','?')} ({ey.get('polo_baixo','?')} ↔ {ey.get('polo_alto','?')})

TOP SINAIS (score ≥ 7.5):
{json.dumps(top_itens_sorted, ensure_ascii=False, indent=2)}

{_TIPOS_VIZ_SCHEMAS}

CRITÉRIOS:
- matriz_2x2      → 2-4 cenários distintos com eixos de incerteza claros
- barras_urgencia → 4-8 temas com urgência contrastante
- radar_setorial  → n_setores_ativos >= 4
- timeline_regulatoria → contagem_regulatorio >= 4
- scatter_convergencia → 6+ sinais multissetoriais convergentes

Retorne APENAS o JSON da visualização escolhida, seguindo rigorosamente o schema.
"""

    try:
        texto = _chamar_claude(
            client, MODELO_VISUALIZACAO,
            prompt_sistema, prompt_usuario,
            max_tokens=3000, temperature=None,
        )
        viz = json.loads(extrair_json_valido(texto))
        tipo = viz.get("tipo", "desconhecido")
        print(f"     [OK] Visualização: {tipo}")

        if tipo == "radar_setorial":
            eixos_r = viz.get("eixos", [])
            for s in viz.get("series", []):
                atual = s.get("valores", [])
                s["valores"] = (atual + [5.0] * len(eixos_r))[:len(eixos_r)]

        return viz

    except Exception as e:
        print(f"  [!] Erro na geração da visualização: {e}")
        return _FALLBACK


# ═══════════════════════════════════════════════════════════════════════════════
# Orquestração principal
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# Fase 3.7 — Classificação Acadêmica de P&D (Python puro)
# ═══════════════════════════════════════════════════════════════════════════════

ACADEMIC_PDI_MIN_SCORE = 0.82
ACADEMIC_PDI_PREFERRED_SCORE = 0.88

ACADEMIC_TECH_KEYWORDS = {
    "armazenamento", "bess", "bateria", "baterias", "grid", "rede", "transmissão",
    "distribuição", "data center", "data centers", "inteligência artificial",
    "automação", "semicondutor", "chips", "hidrogênio", "biometano", "gás natural",
    "eólica", "solar", "fotovoltaica", "offshore", "eficiência", "smart grid",
    "curtailment", "despacho", "hidrologia", "reservatórios", "clima", "telecom",
    "fibra", "cibersegurança", "p&d", "pesquisa", "modelagem", "simulação",
}

# Termos que identificam contexto real de IA/data centers — nunca usar "ia" isolado
_IA_CONTEXT_TERMS = {
    "inteligência artificial", "data center", "data centers", "datacenter",
    "gpu", "llm", "inferência computacional", "treinamento de modelo",
    "ia soberana", "computação em nuvem", "workload", "cluster computacional",
}

def _has_ia_context(texto: str) -> bool:
    """Verifica presença de IA/data-center sem falso-positivo por substring 'ia'."""
    return any(term in texto for term in _IA_CONTEXT_TERMS)

ACADEMIC_METHOD_KEYWORDS = {
    "modelo", "modelagem", "simulação", "otimização", "previsão", "série temporal",
    "correlação", "regressão", "benchmark", "framework", "arquitetura", "metodologia",
    "métrica", "indicador", "matriz", "cenário", "cenários", "planejamento",
}

ACADEMIC_FIELD_DATA_KEYWORDS = {
    "ons", "aneel", "epe", "ccee", "ibge", "inmet", "inpe", "ana", "mapbiomas",
    "carga", "demanda", "geração", "consumo", "pld", "reservatório", "reservatórios",
    "transmissão", "distribuição", "tarifa", "rap", "pib", "temperatura", "chuva",
    "vento", "radiação", "município", "municipal", "regional", "subsistema",
}

ACADEMIC_BLOG_RISK_KEYWORDS = {
    "anuncia", "lança", "participa", "recebe selo", "notícias do dia", "confira",
    "estreia", "lista de", "exibe normativo", "cartilha", "evento", "missão técnica",
}


def _texto_vetor_para_academico(vetor: dict) -> str:
    partes = [
        vetor.get("nome", ""),
        vetor.get("tipo", ""),
        vetor.get("descricao_executiva", ""),
        vetor.get("mecanismo_causal", ""),
        vetor.get("consequencia_inacao", ""),
        vetor.get("decisao_recomendada", ""),
        " ".join(vetor.get("setores_afetados", []) or []),
    ]
    return " ".join(str(p) for p in partes if p).lower()


def _match_ratio(texto: str, keywords: set[str]) -> float:
    if not texto:
        return 0.0
    hits = sum(1 for kw in keywords if kw in texto)
    return min(1.0, hits / 6.0)


def _score_densidade_tecnica(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    base = 0.35 + 0.45 * _match_ratio(texto, ACADEMIC_TECH_KEYWORDS)
    tipo = str(vetor.get("tipo", "")).lower()
    if "tecnológico" in tipo or "oportunidade" in tipo or "regulatório" in tipo:
        base += 0.10
    if vetor.get("n_sinais", 0) >= 5:
        base += 0.08
    return round(min(1.0, base), 3)


def _score_lacuna_pesquisa(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    gatilhos = [
        "ausência", "lacuna", "não transformou", "incerteza", "indefinição",
        "carece", "necessidade", "definir", "modelo", "critérios", "metodologia",
        "planejamento", "risco", "sem marco", "não previsto",
    ]
    hits = sum(1 for g in gatilhos if g in texto)
    base = 0.40 + min(0.42, hits * 0.07)
    if vetor.get("irreversibilidade") == "Alta":
        base += 0.06
    if vetor.get("custo_espera") in {"Alto", "Crítico"}:
        base += 0.06
    return round(min(1.0, base), 3)


def _score_aplicabilidade_pdi(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    base = 0.35 + 0.35 * _match_ratio(texto, ACADEMIC_TECH_KEYWORDS)
    base += 0.20 * _match_ratio(texto, ACADEMIC_METHOD_KEYWORDS)
    if any(s in texto for s in ["energia", "infraestrutura", "regulação", "telecom", "ia"]):
        base += 0.08
    return round(min(1.0, base), 3)


def _score_fonte_academica_provavel(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    base = 0.30 + 0.40 * _match_ratio(texto, ACADEMIC_TECH_KEYWORDS)
    base += 0.18 * _match_ratio(texto, ACADEMIC_METHOD_KEYWORDS)
    if any(k in texto for k in ["bess", "data center", "smart grid", "hidrologia", "solar", "eólica", "gás natural"]) or _has_ia_context(texto):
        base += 0.10
    return round(min(1.0, base), 3)


def _score_dados_reais(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    base = 0.25 + 0.55 * _match_ratio(texto, ACADEMIC_FIELD_DATA_KEYWORDS)
    if any(k in texto for k in ["ons", "aneel", "epe", "ccee", "ibge"]):
        base += 0.12
    if any(k in texto for k in ["carga", "demanda", "transmissão", "reservatórios", "pib", "temperatura"]):
        base += 0.08
    return round(min(1.0, base), 3)


def _score_originalidade(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    base = 0.42
    if "converg" in texto:
        base += 0.14
    if "redefine" in texto or "paradigma" in texto or "arquitetura" in texto:
        base += 0.12
    if vetor.get("pressao_estrategica", 0) >= 7.0:
        base += 0.10
    if vetor.get("n_sinais", 0) >= 7:
        base += 0.10
    base += 0.10 * _match_ratio(texto, ACADEMIC_METHOD_KEYWORDS)
    return round(min(1.0, base), 3)


def _score_potencial_visual(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    base = 0.45
    if any(k in texto for k in ["carga", "demanda", "preço", "pld", "pib", "temperatura", "capacidade"]):
        base += 0.16
    if any(k in texto for k in ["arquitetura", "mecanismo", "canal", "cadeia", "grid", "rede"]):
        base += 0.16
    if vetor.get("n_sinais", 0) >= 5:
        base += 0.10
    return round(min(1.0, base), 3)


def _score_robustez_temporal(vetor: dict) -> float:
    janela = int(vetor.get("janela_decisoria_dias", 180) or 180)
    base = 0.72 if janela >= 90 else 0.62
    if vetor.get("irreversibilidade") == "Alta":
        base += 0.08
    if vetor.get("n_sinais", 0) >= 5:
        base += 0.08
    return round(min(1.0, base), 3)


def _score_anti_blog(vetor: dict) -> float:
    texto = _texto_vetor_para_academico(vetor)
    risk_hits = sum(1 for kw in ACADEMIC_BLOG_RISK_KEYWORDS if kw in texto)
    base = 0.90 - min(0.35, risk_hits * 0.08)
    if vetor.get("n_sinais", 0) >= 5:
        base += 0.08
    if _match_ratio(texto, ACADEMIC_METHOD_KEYWORDS) >= 0.3:
        base += 0.05
    return round(max(0.0, min(1.0, base)), 3)


def _datasets_recomendados(texto: str) -> list[str]:
    texto = texto.lower()
    datasets: list[str] = []
    def add(nome: str):
        if nome not in datasets:
            datasets.append(nome)
    if any(k in texto for k in ["carga", "ons", "reservatório", "despacho", "térmica", "pld", "transmissão"]):
        add("ONS - carga, geração, reservatórios e operação do SIN")
    if any(k in texto for k in ["aneel", "tarifa", "rap", "distribuição", "transmissão", "leilão", "bess"]):
        add("ANEEL - dados regulatórios, transmissão, distribuição, qualidade e P&D")
    if any(k in texto for k in ["planejamento", "pde", "epe", "demanda", "consumo", "expansão"]):
        add("EPE - séries energéticas, PDE, BEN e estudos de expansão")
    if any(k in texto for k in ["mercado", "pld", "contrato", "ppa", "liquidação", "ccee"]):
        add("CCEE - PLD, mercado livre, consumo, geração e liquidação")
    if any(k in texto for k in ["pib", "indústria", "municipal", "regional", "população", "ibge", "data center"]):
        add("IBGE - PIB municipal, população, indústria, território e SIDRA")
    if any(k in texto for k in ["clima", "temperatura", "chuva", "vento", "radiação", "hidrologia"]):
        add("INMET/INPE/ANA - clima, hidrologia, séries meteorológicas e recursos hídricos")
    if not datasets:
        add("dados.gov.br - catálogo de bases abertas federais aplicáveis ao tema")
    return datasets[:6]


def _queries_academicas(vetor: dict) -> list[str]:
    texto = _texto_vetor_para_academico(vetor)
    nome = vetor.get("nome", "")
    queries: list[str] = []
    if _has_ia_context(texto):
        queries.extend([
            "AI data centers electricity demand grid flexibility",
            "data center energy consumption battery energy storage demand response",
            "artificial intelligence workloads power grid planning",
        ])
    if "bess" in texto or "armazenamento" in texto or "bateria" in texto:
        queries.extend([
            "battery energy storage systems grid flexibility capacity markets",
            "technology neutral energy storage auctions power systems",
            "BESS ancillary services transmission planning renewable curtailment",
        ])
    if "gás" in texto or "biometano" in texto:
        queries.extend([
            "natural gas biogas biomethane energy transition power systems",
            "gas infrastructure flexibility renewable energy integration",
        ])
    if "clima" in texto or "hidrologia" in texto or "reservatório" in texto:
        queries.extend([
            "climate change hydropower reservoir operation electricity demand Brazil",
            "temperature electricity load forecasting hydrological risk power systems",
        ])
    if "solar" in texto or "autoprodução" in texto:
        queries.extend([
            "distributed solar self generation battery storage electricity markets",
            "photovoltaic prosumers energy storage distribution grid impact",
        ])
    if not queries:
        queries.append(f"{nome} energy infrastructure research development")
    # dedup preservando ordem
    out=[]
    for q in queries:
        if q not in out:
            out.append(q)
    return out[:6]


def _visualizacoes_recomendadas(vetor: dict) -> list[str]:
    texto = _texto_vetor_para_academico(vetor)
    vis = ["tabela de literatura revisada", "fluxograma metodológico"]
    if any(k in texto for k in ["carga", "demanda", "pld", "temperatura", "pib", "reservatório"]):
        vis.append("gráfico de série temporal com dados oficiais")
    if any(k in texto for k in ["arquitetura", "grid", "rede", "data center", "bess"]):
        vis.append("diagrama de arquitetura técnica")
    if any(k in texto for k in ["risco", "regulação", "tarifa", "rap"]):
        vis.append("matriz de risco regulatório")
    if any(k in texto for k in ["regional", "municipal", "amazônia", "nordeste", "são paulo"]):
        vis.append("mapa ou matriz regional de evidências")
    return vis[:5]


def _formular_titulo_academico(vetor: dict) -> str:
    nome = vetor.get("nome", "Tema técnico")
    texto = _texto_vetor_para_academico(vetor)
    # Verificar tema primário do vetor antes de temas secundários mencionados na descrição
    if "cabo submarino" in texto or ("conectividade" in texto and "telecom" in texto):
        return "Infraestrutura de conectividade internacional, cabos submarinos e soberania digital: implicações para o Brasil"
    if "mineral" in texto or "terra rara" in texto:
        return "Minerais críticos e terras raras na transição energética: posicionamento estratégico do Brasil na cadeia global"
    if "tarifário" in texto or "solvência" in texto or "inadimplência" in texto:
        return "Estresse tarifário, risco de solvência e modelagem de adequação financeira no setor elétrico brasileiro"
    if "cibersegurança" in texto or "soberania digital" in texto:
        return "Cibersegurança em infraestruturas críticas de energia: lacunas regulatórias e agenda de P&D para o Brasil"
    if "mercado livre" in texto or "mobilidade elétrica" in texto:
        return "Abertura do mercado livre de energia, mobilidade elétrica e novos modelos de negócio: implicações regulatórias e técnicas"
    if "fiscal" in texto or "custo de capital" in texto:
        return "Ambiente macroeconômico, custo de capital e viabilidade de investimentos em infraestrutura energética no Brasil"
    if _has_ia_context(texto):
        return "Demanda energética de data centers de inteligência artificial e impactos sobre o planejamento de redes elétricas no Brasil"
    if "armazenamento" in texto or "bess" in texto:
        return "Critérios técnico-regulatórios para contratação de armazenamento de energia e flexibilidade em sistemas elétricos"
    if "gás" in texto or "biometano" in texto:
        return "Integração de gás natural e biometano como recursos de flexibilidade na transição energética brasileira"
    if "clima" in texto or "hidrologia" in texto:
        return "Risco climático, hidrologia e segurança energética: análise aplicada ao planejamento do sistema elétrico brasileiro"
    if "solar" in texto or "autoprodução" in texto:
        return "Autoprodução solar, armazenamento e impactos na contratação de energia por grandes consumidores no Brasil"
    return f"{nome}: uma análise técnico-científica aplicada a projetos de P&D"


def _formular_problema_pesquisa(vetor: dict) -> str:
    texto = _texto_vetor_para_academico(vetor)
    if _has_ia_context(texto):
        return "Como cargas computacionais intensivas em IA podem alterar requisitos de capacidade, confiabilidade e flexibilidade das redes elétricas brasileiras?"
    if "armazenamento" in texto or "bess" in texto:
        return "Quais atributos operativos devem orientar a contratação de armazenamento de energia para reduzir curtailment e aumentar a confiabilidade do sistema elétrico?"
    if "gás" in texto or "biometano" in texto:
        return "Como gás natural e biometano podem atuar como recursos de flexibilidade e transição em sistemas com alta penetração renovável?"
    if "clima" in texto or "hidrologia" in texto:
        return "Como variáveis climáticas e hidrológicas alteram o risco operativo, o despacho térmico e a segurança energética no médio prazo?"
    if "solar" in texto or "autoprodução" in texto:
        return "Quais impactos técnicos e econômicos da autoprodução solar com armazenamento sobre redes de distribuição e modelos de contratação de energia?"
    if "cibersegurança" in texto or "soberania digital" in texto:
        return "Quais lacunas regulatórias e técnicas comprometem a resiliência cibernética de infraestruturas críticas de energia no Brasil?"
    if "tarifário" in texto or "solvência" in texto or "inadimplência" in texto:
        return "Como modelar o risco de solvência de agentes do setor elétrico em cenários de estresse tarifário e inadimplência no mercado de curto prazo?"
    if "mercado livre" in texto or "mobilidade elétrica" in texto:
        return "Quais barreiras regulatórias e técnicas limitam a adoção de novos modelos de negócio na abertura do mercado livre e na mobilidade elétrica no Brasil?"
    if "mineral" in texto or "terra rara" in texto:
        return "Como o acesso a minerais críticos e terras raras pode condicionar a competitividade do Brasil na cadeia global de transição energética?"
    if "cabo submarino" in texto or "conectividade" in texto:
        return "Como a expansão de cabos submarinos e a infraestrutura de conectividade internacional afetam a soberania digital e a competitividade da economia brasileira?"
    if "fiscal" in texto or "custo de capital" in texto:
        return "Como a trajetória da dívida pública e o custo de capital afetam a atratividade de investimentos em infraestrutura energética no Brasil?"
    return "Qual lacuna técnica, metodológica ou regulatória impede a conversão do sinal estratégico em projeto de P&D aplicado?"


def _formular_hipotese_tecnica(vetor: dict) -> str:
    texto = _texto_vetor_para_academico(vetor)
    if _has_ia_context(texto):
        return "A combinação de contratação renovável, BESS e resposta da demanda pode reduzir a pressão de cargas de IA sobre a expansão da rede e aumentar a resiliência energética regional."
    if "armazenamento" in texto or "bess" in texto:
        return "Leilões orientados por atributos operativos, e não por tecnologia, tendem a ampliar competição, reduzir custos sistêmicos e acelerar a contratação de flexibilidade."
    if "gás" in texto or "biometano" in texto:
        return "A integração planejada de gás natural e biometano pode reduzir risco de suprimento e complementar a variabilidade de fontes renováveis em horizontes críticos de operação."
    if "clima" in texto or "hidrologia" in texto:
        return "A incorporação de cenários climáticos e séries hidrológicas melhora a estimativa de despacho térmico, exposição ao PLD e necessidade de recursos de firming."
    if "solar" in texto or "autoprodução" in texto:
        return "Modelos de autoprodução solar com BESS podem reduzir custos de energia para grandes consumidores, mas exigem novos critérios de planejamento tarifário e operacional."
    if "cibersegurança" in texto or "soberania digital" in texto:
        return "A adoção de frameworks baseados em IEC 62443 e LGPD, combinada a planos nacionais de resposta a incidentes, reduz a superfície de ataque e aumenta a resiliência operacional de infraestruturas críticas."
    if "tarifário" in texto or "solvência" in texto or "inadimplência" in texto:
        return "Modelos de adequação financeira baseados em séries históricas de PLD, inadimplência e bandeiras tarifárias podem antecipar risco de solvência e orientar mecanismos regulatórios preventivos."
    if "mercado livre" in texto or "mobilidade elétrica" in texto:
        return "A remoção de barreiras regulatórias ao mercado livre em baixa tensão e a padronização de protocolos de recarga de veículos elétricos podem acelerar a adoção de novos modelos de negócio e reduzir custos para consumidores."
    if "mineral" in texto or "terra rara" in texto:
        return "A estruturação de uma cadeia nacional de processamento de minerais críticos, apoiada por regulação de exportação e parcerias estratégicas, pode posicionar o Brasil como fornecedor preferencial na transição energética global."
    if "cabo submarino" in texto or "conectividade" in texto:
        return "O desenvolvimento de infraestrutura de conectividade soberana, com cabos diversificados e pontos de presença nacionais, reduz a dependência de rotas estrangeiras e aumenta a resiliência da economia digital brasileira."
    if "fiscal" in texto or "custo de capital" in texto:
        return "A redução do custo de capital via instrumentos de desrisco (garantias, debêntures de infraestrutura, PPPs) pode viabilizar projetos de energia com TIR abaixo do custo de oportunidade atual do mercado brasileiro."
    return "A formalização de um framework técnico e a validação por dados oficiais podem converter o sinal estratégico em agenda de P&D aplicada."


def _blocking_flags_academicos(vetor: dict, componentes: dict) -> list[str]:
    flags: list[str] = []
    texto = _texto_vetor_para_academico(vetor)
    if componentes["technical_density_score"] < 0.55:
        flags.append("blocked_insufficient_technical_density")
    if componentes["research_gap_score"] < 0.55:
        flags.append("blocked_no_research_gap")
    if componentes["pdi_applicability_score"] < 0.55:
        flags.append("blocked_no_pdi_application")
    if componentes["academic_source_likelihood_score"] < 0.55:
        flags.append("blocked_low_academic_source_likelihood")
    if componentes["anti_blog_score"] < 0.65:
        flags.append("blocked_blog_like")
    if vetor.get("n_sinais", 0) <= 1 and any(k in texto for k in ACADEMIC_BLOG_RISK_KEYWORDS):
        flags.append("blocked_single_news_event")
    return flags


def classificar_oportunidades_academicas_pdi(
    vetores_estrategicos: list[dict],
    clusters: list[dict] | None = None,
    fatos_canonicos: list[dict] | None = None,
) -> dict:
    """
    Classifica oportunidades acadêmicas de P&D sem alterar a lógica executiva do Radar.

    A saída é uma camada adicional em intel_output.json para uso por um sistema acadêmico
    independente. Esta função não consulta bases acadêmicas nem altera mesa de decisão,
    briefing, dashboard ou vetores estratégicos.
    """
    candidates: list[dict] = []
    for idx, vetor in enumerate(vetores_estrategicos or [], 1):
        texto = _texto_vetor_para_academico(vetor)
        componentes = {
            "technical_density_score": _score_densidade_tecnica(vetor),
            "research_gap_score": _score_lacuna_pesquisa(vetor),
            "pdi_applicability_score": _score_aplicabilidade_pdi(vetor),
            "academic_source_likelihood_score": _score_fonte_academica_provavel(vetor),
            "field_data_potential_score": _score_dados_reais(vetor),
            "sector_relevance_score": round(min(1.0, (vetor.get("impacto_brasil", 0) or 0) / 8.0), 3),
            "originality_score": _score_originalidade(vetor),
            "methodological_potential_score": round(min(1.0, 0.40 + 0.50 * _match_ratio(texto, ACADEMIC_METHOD_KEYWORDS)), 3),
            "visualization_potential_score": _score_potencial_visual(vetor),
            "temporal_robustness_score": _score_robustez_temporal(vetor),
            "anti_blog_score": _score_anti_blog(vetor),
        }
        score = (
            0.15 * componentes["technical_density_score"]
            + 0.14 * componentes["research_gap_score"]
            + 0.13 * componentes["pdi_applicability_score"]
            + 0.12 * componentes["academic_source_likelihood_score"]
            + 0.12 * componentes["field_data_potential_score"]
            + 0.10 * componentes["sector_relevance_score"]
            + 0.08 * componentes["originality_score"]
            + 0.07 * componentes["methodological_potential_score"]
            + 0.05 * componentes["visualization_potential_score"]
            + 0.04 * componentes["temporal_robustness_score"]
        )
        score = round(max(0.0, min(1.0, score)), 3)
        flags = _blocking_flags_academicos(vetor, componentes)
        if flags:
            decision = "blocked_" + flags[0].replace("blocked_", "")
        elif score >= ACADEMIC_PDI_MIN_SCORE:
            decision = "eligible_full_article"
        elif score >= 0.70:
            decision = "eligible_pdi_note"
        elif score >= 0.55:
            decision = "watchlist"
        else:
            decision = "discard"
        candidate = {
            "id_academic_topic": f"PDI-{HOJE_ISO}-{idx:03d}",
            "source_cycle_id": HOJE_ISO,
            "source_vector_id": vetor.get("id"),
            "titulo_tecnico_sugerido": _formular_titulo_academico(vetor),
            "tema_origem": vetor.get("nome"),
            "vetores_relacionados": [vetor.get("nome")] if vetor.get("nome") else [],
            "sinais_ids": vetor.get("sinais_ids", []),
            "problema_de_pesquisa": _formular_problema_pesquisa(vetor),
            "hipotese_tecnica": _formular_hipotese_tecnica(vetor),
            "academic_pdi_score": score,
            "decision": decision,
            "recommended_article_type": "artigo técnico-científico para periódico" if decision == "eligible_full_article" else "nota técnica de P&D",
            **componentes,
            "recommended_academic_queries": _queries_academicas(vetor),
            "recommended_open_datasets": _datasets_recomendados(texto),
            "recommended_visualizations": _visualizacoes_recomendadas(vetor),
            "blocking_flags": flags,
            "selection_reason": (
                "Tema apresenta densidade técnica, lacuna aplicada, potencial de P&D e disponibilidade provável de fontes acadêmicas e dados oficiais."
                if decision == "eligible_full_article"
                else "Tema ainda não atende plenamente ao corte para artigo técnico-científico completo."
            ),
        }
        candidates.append(candidate)

    eligible = [c for c in candidates if c["decision"] == "eligible_full_article" and not c.get("blocking_flags")]
    eligible.sort(
        key=lambda c: (
            c.get("academic_pdi_score", 0),
            c.get("research_gap_score", 0),
            c.get("pdi_applicability_score", 0),
            c.get("field_data_potential_score", 0),
            c.get("academic_source_likelihood_score", 0),
        ),
        reverse=True,
    )
    watchlist = [c for c in candidates if c["decision"] in {"eligible_pdi_note", "watchlist"}]
    watchlist.sort(key=lambda c: c.get("academic_pdi_score", 0), reverse=True)
    blocked = [c for c in candidates if c.get("blocking_flags") or c["decision"].startswith("blocked") or c["decision"] == "discard"]
    selected = eligible[0] if eligible else None
    return {
        "enabled": True,
        "system_boundary": "Camada de classificação apenas. A coleta acadêmica e a geração de artigos pertencem ao sistema acadêmico independente.",
        "min_score": ACADEMIC_PDI_MIN_SCORE,
        "preferred_score": ACADEMIC_PDI_PREFERRED_SCORE,
        "selection_policy": "Selecionar no máximo um tema por ciclo: maior academic_pdi_score entre elegíveis sem blocking_flags.",
        "selected_topic": selected,
        "eligible_topics": eligible,
        "watchlist_topics": watchlist[:10],
        "blocked_topics": blocked[:10],
        "summary": {
            "total_candidates": len(candidates),
            "eligible_count": len(eligible),
            "watchlist_count": len(watchlist),
            "blocked_count": len(blocked),
            "selected_topic_id": selected.get("id_academic_topic") if selected else None,
            "selected_score": selected.get("academic_pdi_score") if selected else None,
            "no_eligible_reason": None if selected else "Nenhum tema atingiu o corte acadêmico mínimo sem flags de bloqueio.",
        },
        "recommended_next_pipeline": "run_research_pipeline_v1.py",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Fase 3.8 — Classificação Comercial nMentors (Python puro)
# ═══════════════════════════════════════════════════════════════════════════════

NMENTORS_CAPABILITIES: dict[str, dict] = {
    "pmo_agetico": {
        "keywords": {"projeto", "cronograma", "gestão", "coordenação", "entrega",
                     "pmo", "contratação", "licitação", "edital", "p&d", "aneel",
                     "governança", "regulatório", "prazo", "custo"},
        "weight": 1.0,
        "description": "PMO Agêntico com agentes de IA integrados a ERP/CRM via API",
        "cases": ["CPFL nas Universidades"],
    },
    "automacao_scada": {
        "keywords": {"scada", "automação", "controle", "plc", "supervisão",
                     "operação", "usina", "planta", "comissionamento", "despacho",
                     "monitoramento", "sistema de controle"},
        "weight": 0.9,
        "description": "Automação e SCADA para plantas de energia",
        "cases": ["Araucária", "Belo Monte"],
    },
    "ciberseguranca_ot": {
        "keywords": {"cibersegurança", "ot", "iec 62443", "segurança",
                     "vulnerabilidade", "resiliência", "iot", "iiot", "aiiot",
                     "soberania digital", "infraestrutura crítica", "ataque"},
        "weight": 0.9,
        "description": "Cibersegurança OT segundo IEC 62443",
        "cases": ["Belo Monte (Norte Energia)"],
    },
    "eficiencia_energetica": {
        "keywords": {"eficiência", "consumo", "tarifa", "custo de energia",
                     "grande consumidor", "industrial", "m&v", "iso 50001",
                     "tarifário", "solvência", "inadimplência", "bandeira"},
        "weight": 0.8,
        "description": "Programas de eficiência energética corporativa",
        "cases": ["CPFL nas Universidades", "Henry Borden"],
    },
    "ia_iot_pred": {
        "keywords": {"inteligência artificial", "data center", "predição",
                     "manutenção", "sensor", "iot", "iiot", "aiiot", "modelo preditivo",
                     "digital twin", "gêmeo digital", "machine learning"},
        "weight": 1.0,
        "description": "IA e IoT para predição de falhas e otimização de plantas",
        "cases": ["Henry Borden (EMAE/Mackenzie)"],
    },
    "proposta_inteligente": {
        "keywords": {"edital", "proposta", "licitação", "p&d", "aneel",
                     "oportunidade", "novo produto", "serviço", "inovação",
                     "mercado livre", "abertura", "modelo de negócio"},
        "weight": 0.85,
        "description": "Elaboração de propostas com produtos disruptivos via Radar",
        "cases": [],
    },
}

COMMERCIAL_MIN_SCORE = 0.70
COMMERCIAL_PREFERRED_SCORE = 0.80

_LARGE_CONSUMER_SECTORS = {
    "energia & transmissão", "indústria eletrointensiva", "mineração",
    "infraestrutura crítica", "mobilidade elétrica", "infraestrutura digital",
    "telecomunicações", "energia & eficiência energética",
}

_BLOCKED_COMMERCIAL_SECTORS = {
    "setor financeiro", "macroeconomia", "comércio exterior",
}


def _score_client_pain(vetor: dict) -> float:
    """Quanto o cliente dói agora — baseado em urgência e custo de espera."""
    score = 0.40
    if vetor.get("custo_espera") == "Crítico":
        score += 0.35
    elif vetor.get("custo_espera") == "Alto":
        score += 0.20
    if vetor.get("irreversibilidade") == "Alta":
        score += 0.15
    if vetor.get("janela_decisoria_dias", 999) <= 90:
        score += 0.10
    return round(min(1.0, score), 3)


def _score_nmentors_capability(vetor: dict) -> float:
    """Sobreposição entre o vetor e as capacidades reais da nMentors."""
    texto = _texto_vetor_para_academico(vetor)
    best = 0.0
    for cap in NMENTORS_CAPABILITIES.values():
        hits = sum(1 for kw in cap["keywords"] if kw in texto)
        ratio = min(1.0, hits / max(1, len(cap["keywords"]) * 0.25))
        weighted = ratio * cap["weight"]
        if weighted > best:
            best = weighted
    return round(min(1.0, 0.20 + 0.80 * best), 3)


def _score_contract_clarity(vetor: dict) -> float:
    """Quão delimitável é o escopo de um contrato a partir do vetor."""
    decisao = str(vetor.get("decisao_recomendada", "")).lower()
    score = 0.35
    action_verbs = ["implementar", "desenvolver", "contratar", "avaliar",
                    "auditar", "modelar", "estruturar", "implantar", "integrar",
                    "monitorar", "qualificar", "medir", "diagnosticar"]
    hits = sum(1 for v in action_verbs if v in decisao)
    score += min(0.40, hits * 0.10)
    texto = _texto_vetor_para_academico(vetor)
    if _match_ratio(texto, ACADEMIC_METHOD_KEYWORDS) >= 0.25:
        score += 0.15
    if vetor.get("n_sinais", 0) >= 5:
        score += 0.10
    return round(min(1.0, score), 3)


def _score_urgency_commercial(vetor: dict) -> float:
    """Urgência de mercado — quanto mais urgente, mais fácil vender."""
    janela = int(vetor.get("janela_decisoria_dias", 360) or 360)
    score = max(0.0, 1.0 - janela / 360)
    if vetor.get("momento_vetor") == "Crítico":
        score += 0.20
    elif vetor.get("momento_vetor") == "Aceleração":
        score += 0.10
    return round(min(1.0, score), 3)


def _score_market_size_proxy(vetor: dict) -> float:
    """Proxy de tamanho de mercado — sinais e setores de grande porte."""
    score = 0.30
    n = int(vetor.get("n_sinais", 0) or 0)
    score += min(0.35, n * 0.05)
    setores = {s.lower() for s in (vetor.get("setores_afetados") or [])}
    overlap = len(setores & _LARGE_CONSUMER_SECTORS)
    score += min(0.25, overlap * 0.10)
    blocked = len(setores & _BLOCKED_COMMERCIAL_SECTORS)
    score -= blocked * 0.15
    return round(max(0.0, min(1.0, score)), 3)


def _score_ai_first_angle(vetor: dict) -> float:
    """Presença de ângulo AI-first / PMO agêntico / automação de processo."""
    texto = _texto_vetor_para_academico(vetor)
    ai_pmo_terms = {"inteligência artificial", "agente", "automação", "digitalização",
                    "gestão de projeto", "pmo", "data center", "iot", "iiot",
                    "digital twin", "modelo preditivo", "processo automatizado"}
    hits = sum(1 for t in ai_pmo_terms if t in texto)
    return round(min(1.0, 0.30 + hits * 0.12), 3)


def _matched_capabilities(vetor: dict) -> list[str]:
    """Retorna lista de capacidades nMentors relevantes para o vetor."""
    texto = _texto_vetor_para_academico(vetor)
    matched = []
    for cap_id, cap in NMENTORS_CAPABILITIES.items():
        hits = sum(1 for kw in cap["keywords"] if kw in texto)
        if hits >= max(1, len(cap["keywords"]) * 0.15):
            matched.append(cap_id)
    return matched


def _commercial_decision(score: float, vetor: dict, capabilities: list[str]) -> str:
    """Decisão comercial baseada no score e nas capacidades matched."""
    setores = {s.lower() for s in (vetor.get("setores_afetados") or [])}
    if setores & _BLOCKED_COMMERCIAL_SECTORS and not (setores - _BLOCKED_COMMERCIAL_SECTORS):
        return "blocked_no_nmentors_fit"
    if not capabilities:
        return "blocked_no_nmentors_fit"
    if score >= COMMERCIAL_PREFERRED_SCORE:
        if any(c in capabilities for c in ["pmo_agetico", "proposta_inteligente"]):
            return "eligible_brief_pdi"
        return "eligible_service_note"
    if score >= COMMERCIAL_MIN_SCORE:
        if "eficiencia_energetica" in capabilities or "ciberseguranca_ot" in capabilities:
            return "eligible_service_note"
        if "pmo_agetico" in capabilities or "proposta_inteligente" in capabilities:
            return "eligible_brief_pdi"
        return "eligible_case_angle"
    if score >= 0.55:
        return "watchlist_commercial"
    return "blocked_no_nmentors_fit"


def _commercial_content_format(decision: str) -> str:
    mapping = {
        "eligible_brief_pdi": "B1 — Brief de Oportunidade PDI",
        "eligible_service_note": "B2 — Nota Técnica de Implementação",
        "eligible_case_angle": "B3 — Renarração de Case",
        "watchlist_commercial": "—",
        "blocked_no_nmentors_fit": "—",
    }
    return mapping.get(decision, "—")


def classificar_oportunidades_comerciais_nmentors(
    vetores_estrategicos: list[dict],
    clusters: list[dict] | None = None,
) -> dict:
    """
    Classifica vetores estratégicos como oportunidades comerciais para nMentors.

    Operação Python pura — sem chamadas LLM. Produz o bloco
    'commercial_nmentors_opportunities' no intel_output.json.
    Não interfere com vetores, dashboard, briefing ou Fase 3.7 acadêmica.
    """
    candidates: list[dict] = []
    for idx, vetor in enumerate(vetores_estrategicos or [], 1):
        componentes = {
            "client_pain_score":       _score_client_pain(vetor),
            "nmentors_capability_score": _score_nmentors_capability(vetor),
            "contract_clarity_score":  _score_contract_clarity(vetor),
            "urgency_score":           _score_urgency_commercial(vetor),
            "market_size_proxy":       _score_market_size_proxy(vetor),
            "ai_first_angle_score":    _score_ai_first_angle(vetor),
        }
        score = round(
            0.25 * componentes["client_pain_score"]
            + 0.25 * componentes["nmentors_capability_score"]
            + 0.20 * componentes["contract_clarity_score"]
            + 0.15 * componentes["urgency_score"]
            + 0.10 * componentes["market_size_proxy"]
            + 0.05 * componentes["ai_first_angle_score"],
            3,
        )
        capabilities = _matched_capabilities(vetor)
        decision = _commercial_decision(score, vetor, capabilities)
        candidates.append({
            "id_commercial_topic": f"COM-{HOJE_ISO}-{idx:03d}",
            "source_vector_id": vetor.get("id"),
            "source_cycle_id": HOJE_ISO,
            "vector_name": vetor.get("nome", ""),
            "pressao_estrategica": vetor.get("pressao_estrategica", 0),
            "commercial_relevance_score": score,
            "score_components": componentes,
            "decision": decision,
            "content_format": _commercial_content_format(decision),
            "matched_capabilities": capabilities,
            "recommended_cases": list({
                case
                for cap_id in capabilities
                for case in NMENTORS_CAPABILITIES.get(cap_id, {}).get("cases", [])
            }),
            "vector_decisao_recomendada": vetor.get("decisao_recomendada", ""),
            "setores_afetados": vetor.get("setores_afetados", []),
            "janela_decisoria_categoria": vetor.get("janela_decisoria_categoria", ""),
        })

    eligible = [c for c in candidates if c["decision"] in {
        "eligible_brief_pdi", "eligible_service_note", "eligible_case_angle"
    }]
    eligible.sort(key=lambda c: c["commercial_relevance_score"], reverse=True)
    watchlist = [c for c in candidates if c["decision"] == "watchlist_commercial"]
    watchlist.sort(key=lambda c: c["commercial_relevance_score"], reverse=True)
    blocked = [c for c in candidates if c["decision"] == "blocked_no_nmentors_fit"]

    # Seleciona até 2 por ciclo: prioridade para brief_pdi, depois service_note
    selected: list[dict] = []
    for fmt in ["eligible_brief_pdi", "eligible_service_note", "eligible_case_angle"]:
        for c in eligible:
            if c["decision"] == fmt and len(selected) < 2:
                selected.append(c)

    return {
        "enabled": True,
        "system_boundary": (
            "Classificação comercial para nMentors.com.br. "
            "Não interfere com Radar Estratégico, briefing executivo ou Trilha A acadêmica."
        ),
        "min_score": COMMERCIAL_MIN_SCORE,
        "preferred_score": COMMERCIAL_PREFERRED_SCORE,
        "selection_policy": "Até 2 tópicos por ciclo: prioridade brief_pdi > service_note > case_angle.",
        "selected_topics": selected,
        "eligible_topics": eligible,
        "watchlist_topics": watchlist[:8],
        "blocked_topics": blocked[:8],
        "summary": {
            "total_candidates": len(candidates),
            "eligible_count": len(eligible),
            "watchlist_count": len(watchlist),
            "blocked_count": len(blocked),
            "selected_ids": [c["id_commercial_topic"] for c in selected],
            "selected_formats": [c["content_format"] for c in selected],
        },
        "recommended_next_pipeline": "run_commercial_pipeline_v1.py",
    }





# ═══════════════════════════════════════════════════════════════════════════════
# LOOP DO AGENTE — orquestração autônoma com retry e advisor checks
# ═══════════════════════════════════════════════════════════════════════════════

class RadarAgent:
    """
    Agente autônomo do Radar Estratégico v30.
    Interface pública: RadarAgent(client).run()
    Drop-in para analyzer_v29.py — mesma entrada/saída.
    """

    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self.state  = AgentState()

    def step1_ingestao(self) -> bool:
        print("\n[Step 1] Ingestão + triagem")
        if not os.path.exists(INPUT_FILE):
            print(f"  [!] Arquivo não encontrado: {INPUT_FILE}")
            return False
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            self.state.feed_raw = json.load(f)
        print(f"  [OK] {len(self.state.feed_raw)} itens carregados de {INPUT_FILE}")
        return True

    def step2_analise_tatica(self):
        total = len(self.state.feed_raw)
        print(f"\n[Step 2] Análise Tática — {total} itens ({MODELO_TATICO})")
        for idx, entrada in enumerate(self.state.feed_raw, 1):
            print(f"  -> [{idx:03d}/{total}] {entrada.get('titulo', '')[:60]}", end="\r")
            analise = analisar_item_tatico(self.client, entrada, idx, total)
            if analise:
                analise["exposicao_brasil"]  = _clamp_float(analise.get("exposicao_brasil", 0.5))
                analise["impacto_brasil"]    = _clamp_float(analise.get("impacto_brasil", 0), 0, 10)
                analise["urgencia_dias"]     = _clamp_urgencia(analise.get("urgencia_dias", 180))
                analise["tipo_sinal"]        = _clamp_tipo_sinal(analise.get("tipo_sinal", ""))
                analise["confianca_analise"] = _clamp_float(analise.get("confianca_analise", 0.5))
                analise["quadrante"]         = calcular_quadrante(
                    analise["exposicao_brasil"], analise["urgencia_dias"]
                )
                entrada["analise"]           = analise
                entrada["score_final"]       = analise.get("score_final", 0)
                entrada["geo_scope"]         = analise.get("geo_scope", "Nacional")
                entrada["setor_normalizado"] = normalizar_setor(analise.get("tema_analisado", ""))
                if analise.get("titulo_pt"):
                    entrada["titulo_pt"] = analise["titulo_pt"]
                self.state.itens_analisados.append(entrada)
            time.sleep(0.05)
        print(f"\n  [OK] {len(self.state.itens_analisados)}/{total} itens analisados.")

    def step3_fatos_canonicos(self):
        print(f"\n[Step 3] Fatos Canônicos ({MODELO_TATICO})")
        self.state.fatos_canonicos = extrair_fatos_canonicos(
            self.client, self.state.itens_analisados
        )

    def step4_cenarios_clusters(self):
        print(f"\n[Step 4] Cenários + Clusters ({MODELO_ESTRATEGICO})")
        fase2 = construir_matriz_de_cenarios(self.client, self.state.itens_analisados)
        self.state.cenarios_list     = fase2.get("cenarios_prospectivos", [])
        self.state.matriz_incertezas = fase2.get("matriz_incertezas", {})
        self.state.clusters          = gerar_clusters(self.client, self.state.itens_analisados)
        print(f"  [OK] {len(self.state.cenarios_list)} cenários, {len(self.state.clusters)} clusters.")

    def step5_vetores_thesis(self):
        print(f"\n[Step 5] Vetores + Thesis ({MODELO_ESTRATEGICO}) [max_retries={MAX_RETRIES_VETORES}]")
        melhor_vetores, melhor_thesis, melhor_score = [], {}, 0.0

        for tentativa in range(1, MAX_RETRIES_VETORES + 2):
            if tentativa > 1:
                print(f"  [Retry {tentativa-1}/{MAX_RETRIES_VETORES}] Advisor rejeitou — refazendo...")

            vetores = gerar_vetores_estrategicos(
                self.client, self.state.itens_analisados,
                self.state.clusters, fatos_canonicos=self.state.fatos_canonicos,
            )
            thesis = gerar_executive_thesis(
                self.client, self.state.itens_analisados,
                vetores, fatos_canonicos=self.state.fatos_canonicos,
            )
            self.state.vetores_estrategicos = vetores
            self.state.executive_thesis     = thesis

            aprovado, score, notas = advisor_check_vetores(self.client, self.state)
            self.state.advisor_vetores_score = score
            self.state.advisor_vetores_notas = notas

            if score > melhor_score:
                melhor_score, melhor_vetores, melhor_thesis = score, vetores, thesis

            if aprovado:
                self.state.advisor_vetores_ok = True
                print(f"  [OK] Vetores aprovados (score={score:.2f}, tentativa {tentativa}).")
                break
            elif tentativa > MAX_RETRIES_VETORES:
                self.state.registrar_degradacao(
                    "step5_vetores",
                    f"Advisor score={melhor_score:.2f} após {MAX_RETRIES_VETORES} retries. Usando melhor disponível.",
                )

        self.state.vetores_estrategicos = melhor_vetores
        self.state.executive_thesis     = melhor_thesis
        n_mob = sum(1 for v in melhor_vetores if v.get("quadrante_executivo") == "Mobilizar Agora")
        n_cap = sum(1 for v in melhor_vetores if v.get("quadrante_executivo") == "Capturar Vantagem")
        print(f"  [OK] {len(melhor_vetores)} vetores — Mobilizar:{n_mob} Capturar:{n_cap}")

    def step6_classificadores(self):
        print("\n[Step 6] Classificadores P&D (3.7) + Comercial (3.8) — Python puro")
        self.state.academic_pdi_opportunities = classificar_oportunidades_academicas_pdi(
            self.state.vetores_estrategicos,
            clusters=self.state.clusters,
            fatos_canonicos=self.state.fatos_canonicos,
        )
        self.state.commercial_nmentors_opportunities = classificar_oportunidades_comerciais_nmentors(
            self.state.vetores_estrategicos,
            clusters=self.state.clusters,
        )
        n_pdi = self.state.academic_pdi_opportunities.get("summary", {}).get("eligible_count", 0)
        n_com = self.state.commercial_nmentors_opportunities.get("summary", {}).get("eligible_count", 0)
        print(f"  [OK] P&D elegíveis: {n_pdi} | Comercial elegíveis: {n_com}")

    def step7_briefing(self):
        print(f"\n[Step 7] Briefing Executivo ({MODELO_BRIEFING}) [max_retries={MAX_RETRIES_BRIEFING}]")
        melhor_briefing, melhor_score = {}, 0.0

        for tentativa in range(1, MAX_RETRIES_BRIEFING + 2):
            if tentativa > 1:
                print(f"  [Retry {tentativa-1}/{MAX_RETRIES_BRIEFING}] Advisor rejeitou — refazendo...")

            briefing = gerar_briefing_diario(
                self.client, self.state.itens_analisados,
                fatos_canonicos=self.state.fatos_canonicos,
            )
            self.state.briefing_dict = briefing

            aprovado, score, notas = advisor_check_briefing(self.client, self.state)
            self.state.advisor_briefing_score = score
            self.state.advisor_briefing_notas = notas

            if score > melhor_score:
                melhor_score, melhor_briefing = score, briefing

            if aprovado:
                self.state.advisor_briefing_ok = True
                print(f"  [OK] Briefing aprovado (score={score:.2f}, tentativa {tentativa}).")
                break
            elif tentativa > MAX_RETRIES_BRIEFING:
                self.state.registrar_degradacao(
                    "step7_briefing",
                    f"Advisor score={melhor_score:.2f} após retry. Usando melhor disponível.",
                )

        self.state.briefing_dict = melhor_briefing

    def step8_dashboard_visualizacao(self):
        print("\n[Step 8] Dashboard + Auditoria + Visualização")

        if self.state.vetores_estrategicos:
            self.state.mesa_decisao = gerar_mesa_decisao_por_vetores(self.state.vetores_estrategicos)
            mesa_origem = "vetores"
        else:
            self.state.mesa_decisao = _mesa_decisao_from_itens(self.state.itens_analisados)
            mesa_origem = "itens"

        vetores_dominantes = [
            v.get("nome", "")
            for v in sorted(
                self.state.vetores_estrategicos,
                key=lambda v: v.get("pressao_estrategica", 0), reverse=True,
            )[:3]
            if v.get("nome")
        ]

        self.state.dashboard = montar_dashboard(
            self.state.itens_analisados, self.state.cenarios_list,
            self.state.clusters, self.state.briefing_dict, self.state.mesa_decisao,
            executive_thesis=self.state.executive_thesis,
            vetores_dominantes=vetores_dominantes,
            fatos_canonicos=self.state.fatos_canonicos,
        )

        # Auditoria (agente absorve logs — Fase 5.5 embutida)
        thesis_ajustada, logs_thesis = auditar_coerencia_thesis_mesa(
            self.state.executive_thesis,
            self.state.mesa_decisao,
            self.state.vetores_estrategicos,
        )
        logs_dash = auditar_widget_titulos_dashboard(
            self.state.dashboard, self.state.vetores_estrategicos
        )
        self.state.auditoria_logs   = logs_thesis + logs_dash
        self.state.executive_thesis = thesis_ajustada
        self.state.dashboard["executive_thesis"] = thesis_ajustada

        if self.state.auditoria_logs:
            print(f"  [!] {len(self.state.auditoria_logs)} ajustes de auditoria.")
        else:
            print("  [OK] Coerência thesis↔mesa validada.")

        self.state.visualizacao = gerar_visualizacao(
            self.client, self.state.itens_analisados, self.state.matriz_incertezas
        )
        total_mesa = sum(len(v) for v in self.state.mesa_decisao.values())
        print(f"  [OK] Mesa: {total_mesa} itens ({mesa_origem}) | "
              f"Viz: {self.state.visualizacao.get('tipo', '?')}")

    def _gerar_output(self) -> dict:
        s = self.state
        return {
            "gerado_em":             datetime.now(timezone.utc).isoformat(),
            "ciclo_id":              HOJE_ISO,
            "versao":                "v30-radar-estrategico-agentic-loop",
            "modelos": {
                "tatico":      MODELO_TATICO,
                "estrategico": MODELO_ESTRATEGICO,
                "briefing":    MODELO_BRIEFING,
                "visualizacao":MODELO_VISUALIZACAO,
                "advisor":     MODELO_ADVISOR,
            },
            "total_itens":           len(s.itens_analisados),
            "fatos_canonicos":       s.fatos_canonicos,
            "dashboard":             s.dashboard,
            "vetores_estrategicos":  s.vetores_estrategicos,
            "briefing_diario":       s.briefing_dict,
            "cenarios_prospectivos": s.cenarios_list,
            "matriz_incertezas":     s.matriz_incertezas,
            "visualizacao":          s.visualizacao,
            "academic_pdi_opportunities":        s.academic_pdi_opportunities,
            "commercial_nmentors_opportunities": s.commercial_nmentors_opportunities,
            "itens":                 s.itens_analisados,
            "auditoria_logs":        s.auditoria_logs,
            "agent_metadata": {
                "advisor_vetores":  {"aprovado": s.advisor_vetores_ok,  "score": s.advisor_vetores_score,  "notas": s.advisor_vetores_notas},
                "advisor_briefing": {"aprovado": s.advisor_briefing_ok, "score": s.advisor_briefing_score, "notas": s.advisor_briefing_notas},
                "degradation_log":  s.degradation_log,
            },
        }

    def run(self):
        print("=" * 60)
        print(" Radar Estratégico 9.3 — Agentic Loop (v30)")
        print(f" Tático:      {MODELO_TATICO}")
        print(f" Estratégico: {MODELO_ESTRATEGICO}")
        print(f" Briefing:    {MODELO_BRIEFING}")
        print(f" Advisor:     {MODELO_ADVISOR}")
        print(f" Data:        {HOJE_PT_BR}")
        print("=" * 60)

        if not self.step1_ingestao():
            return

        self.step2_analise_tatica()
        self.step3_fatos_canonicos()
        self.step4_cenarios_clusters()
        self.step5_vetores_thesis()       # Advisor Check A embutido
        self.step6_classificadores()
        self.step7_briefing()             # Advisor Check B embutido
        self.step8_dashboard_visualizacao()

        print("\n[Sanitização Final]")
        output = _sanitizar_recursivo(self._gerar_output())
        print("  [OK] Saída sanitizada.")

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        s = self.state
        scores  = [i.get("score_final", 0) for i in s.itens_analisados]
        acima_7 = sum(1 for x in scores if x > 7)
        print(f"\n✓ {OUTPUT_FILE}")
        print(f"  Itens analisados:        {len(s.itens_analisados)}")
        print(f"  Score > 7:               {acima_7}")
        print(f"  Fatos canônicos:         {len(s.fatos_canonicos)}")
        print(f"  Vetores estratégicos:    {len(s.vetores_estrategicos)}")
        print(f"  Advisor vetores:         {'OK' if s.advisor_vetores_ok else 'DEGRADADO'} score={s.advisor_vetores_score:.2f}")
        print(f"  Advisor briefing:        {'OK' if s.advisor_briefing_ok else 'DEGRADADO'} score={s.advisor_briefing_score:.2f}")
        print(f"  Degradações:             {len(s.degradation_log)}")
        print(f"  Versão de saída:         v30-radar-estrategico-agentic-loop")
        print(f"  Ciclo concluído autonomamente.")


# ═══════════════════════════════════════════════════════════════════════════════
# Entrypoint — drop-in compatível com run_pipeline_v23.py
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    client = inicializar_cliente()
    RadarAgent(client).run()


if __name__ == "__main__":
    main()