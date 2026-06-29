#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
micmac_mactor_v1.py — Análise Prospectiva Godet (MICMAC + MACTOR)

Integrado ao Radar xTech como fases 6.5.13 (MICMAC) e 6.5.14 (MACTOR).

MICMAC Simplificado:
  - 12 variáveis estruturais fixas do domínio Tech & Energy Brasil
  - Saliência por ciclo calculada via Sonnet com base nos sinais coletados
  - Motricidade e dependência calculadas via matriz de influência estática
  - Quadrantes: Motrizes | Relés | Autônomas | Resultado
  - Recomenda eixos para a Matriz de Cenários (substitui escolha livre do LLM)

MACTOR:
  - Atores: entidades do hist_data.entities (SQLite / intel_output.json)
  - Para cada cenário xTech: classifica posição do ator (favorável/neutro/resistente)
  - Identifica convergências e conflitos entre atores
  - Enriquece output["cenarios"] com actor_map por xTech
  - Enriquece cenários macro com probabilidade ajustada por poder de atores

Saídas injetadas em intel_output.json:
  output["micmac"]  — variáveis, quadrantes, eixos recomendados
  output["mactor"]  — mapa de atores, convergências, conflitos, cenários enriquecidos
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import anthropic

# ── Constantes ────────────────────────────────────────────────────────────────

HOJE_ISO = datetime.now(timezone.utc).strftime("%Y-%m-%d")
MODELO   = "claude-sonnet-4-6"

XTECHS = ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]

# ── Variáveis estruturais fixas (MICMAC) ─────────────────────────────────────
# Estas variáveis NÃO mudam entre ciclos — são o backbone longitudinal do Radar.
# A saliência de cada uma é re-calculada por ciclo com base nos sinais coletados.

VARIAVEIS_ESTRUTURAIS: list[dict] = [
    {
        "id": "V01",
        "nome": "Velocidade regulatória",
        "descricao": "Ritmo de aprovação de normas ANEEL/MME/CCEE que viabilizam ou travam projetos de energia e infraestrutura",
        "xtech_primario": "EnergyTech",
    },
    {
        "id": "V02",
        "nome": "Custo de capital",
        "descricao": "Selic + spread bancário — define o retorno mínimo exigido e a viabilidade de fechamento financeiro de projetos de capital intensivo",
        "xtech_primario": "EnergyTech",
    },
    {
        "id": "V03",
        "nome": "Câmbio USD/BRL",
        "descricao": "Taxa de câmbio — afeta CAPEX de equipamentos importados, custos de P&D e competitividade de exportações do agronegócio",
        "xtech_primario": "EnergyTech",
    },
    {
        "id": "V04",
        "nome": "PLD e receita de energia",
        "descricao": "Preço de Liquidação das Diferenças — determina a receita spot de projetos de geração, BESS e flexibilidade de carga",
        "xtech_primario": "EnergyTech",
    },
    {
        "id": "V05",
        "nome": "CAPEX importado",
        "descricao": "Custo de equipamentos com componente dólar: módulos fotovoltaicos, turbinas eólicas, baterias, chips, servidores de IA",
        "xtech_primario": "CleanTech",
    },
    {
        "id": "V06",
        "nome": "Demanda por data centers e IA",
        "descricao": "Crescimento da carga elétrica de data centers e workloads de IA — vetor de demanda transversal para EnergyTech e DeepTech",
        "xtech_primario": "DeepTech",
    },
    {
        "id": "V07",
        "nome": "Disponibilidade hídrica",
        "descricao": "Hidrologia dos reservatórios — afeta PLD, despacho térmico, segurança do sistema elétrico e risco de crise energética",
        "xtech_primario": "EnergyTech",
    },
    {
        "id": "V08",
        "nome": "Preço de commodities críticas",
        "descricao": "Cobre, lítio, alumínio, silício — insumos de infraestrutura elétrica, baterias, módulos solares e semicondutores",
        "xtech_primario": "CleanTech",
    },
    {
        "id": "V09",
        "nome": "Acesso a financiamento público",
        "descricao": "BNDES, BID, GCF, fundos climáticos — capital paciente que viabiliza projetos de longo prazo e P&D estratégico",
        "xtech_primario": "EnergyTech",
    },
    {
        "id": "V10",
        "nome": "Oferta de mão de obra técnica",
        "descricao": "Engenheiros, técnicos especializados em energia, IA e biotech — gargalo de execução para expansão do setor",
        "xtech_primario": "DeepTech",
    },
    {
        "id": "V11",
        "nome": "Pressão geopolítica e tarifária",
        "descricao": "Tarifas de importação, reshoring, disputas de supply chain em semicondutores, energia e alimentos — afeta CAPEX e estratégia de fornecedores",
        "xtech_primario": "DeepTech",
    },
    {
        "id": "V12",
        "nome": "Adoção de IA e automação",
        "descricao": "Penetração de IA em processos industriais, agro e financeiro — vetor transversal de produtividade e pressão sobre mão de obra técnica",
        "xtech_primario": "DeepTech",
    },
]

# ── Matriz de influência estática (MICMAC) ────────────────────────────────────
# M[i][j] = intensidade com que a variável i influencia a variável j.
# Escala: 0=nenhuma | 1=fraca | 2=moderada | 3=forte
# Ordem: V01..V12 (índices 0..11)
# Baseada em conhecimento de domínio — revisada anualmente, não por ciclo.

MATRIZ_INFLUENCIA: list[list[int]] = [
    # V01  V02  V03  V04  V05  V06  V07  V08  V09  V10  V11  V12
    [  0,   1,   0,   3,   1,   1,   0,   0,   2,   0,   0,   1  ],  # V01 Regulatória
    [  1,   0,   0,   1,   3,   1,   0,   0,   3,   0,   0,   0  ],  # V02 Custo capital
    [  0,   1,   0,   1,   3,   0,   0,   2,   1,   0,   2,   0  ],  # V03 Câmbio
    [  1,   1,   0,   0,   0,   1,   2,   0,   2,   0,   0,   0  ],  # V04 PLD
    [  0,   2,   1,   1,   0,   2,   0,   1,   1,   1,   1,   1  ],  # V05 CAPEX importado
    [  1,   0,   0,   2,   1,   0,   0,   0,   0,   1,   0,   2  ],  # V06 Data centers / IA
    [  1,   0,   0,   3,   0,   0,   0,   0,   0,   0,   0,   0  ],  # V07 Hidrologia
    [  0,   1,   1,   1,   3,   0,   0,   0,   0,   0,   2,   0  ],  # V08 Commodities
    [  1,   2,   0,   1,   2,   1,   0,   0,   0,   1,   0,   1  ],  # V09 Financiamento
    [  0,   0,   0,   0,   1,   1,   0,   0,   0,   0,   0,   3  ],  # V10 Mão de obra
    [  1,   0,   2,   0,   2,   0,   0,   2,   0,   0,   0,   1  ],  # V11 Geopolítica
    [  1,   0,   0,   0,   1,   3,   0,   0,   0,   2,   0,   0  ],  # V12 IA / automação
]


def _calcular_quadrantes(variaveis_com_scores: list[dict]) -> list[dict]:
    """Calcula motricidade, dependência e quadrante para cada variável.

    Dois passes:
      1. Computa motricidade e dependência para todas as variáveis.
      2. Usa a mediana de cada dimensão como threshold (fiel ao Godet original —
         o centro do gráfico é o baricentro do conjunto, não um valor fixo).
    """
    n = len(MATRIZ_INFLUENCIA)
    saliencias = {v["id"]: v.get("saliencia_ciclo", 5.0) for v in variaveis_com_scores}
    ids = [v["id"] for v in VARIAVEIS_ESTRUTURAIS]

    # Passe 1 — calcula scores brutos
    scores: list[dict] = []
    for i, var in enumerate(VARIAVEIS_ESTRUTURAIS):
        mot = sum(
            MATRIZ_INFLUENCIA[i][j] * (saliencias.get(ids[j], 5.0) / 10.0)
            for j in range(n) if j != i
        )
        dep = sum(
            MATRIZ_INFLUENCIA[j][i] * (saliencias.get(ids[j], 5.0) / 10.0)
            for j in range(n) if j != i
        )
        # Normaliza para 0-10 em relação ao máximo teórico (3 × 11 influências)
        mot_norm = round(min(mot * 10 / (3 * (n - 1)), 10.0), 2)
        dep_norm = round(min(dep * 10 / (3 * (n - 1)), 10.0), 2)
        scores.append({"var": var, "mot": mot_norm, "dep": dep_norm})

    # Passe 2 — mediana como threshold (baricentro do scatterplot MICMAC)
    all_mot = sorted(s["mot"] for s in scores)
    all_dep = sorted(s["dep"] for s in scores)
    med_m = (all_mot[n // 2 - 1] + all_mot[n // 2]) / 2 if n % 2 == 0 else all_mot[n // 2]
    med_d = (all_dep[n // 2 - 1] + all_dep[n // 2]) / 2 if n % 2 == 0 else all_dep[n // 2]

    resultado = []
    for s in scores:
        mot, dep, var = s["mot"], s["dep"], s["var"]

        # Quadrante Godet:
        #   Motrizes:   alta motricidade, baixa dependência  → alavancas
        #   Relés:      alta motricidade, alta dependência   → amplificadores
        #   Resultado:  baixa motricidade, alta dependência  → indicadores de saída
        #   Autônomas:  baixa motricidade, baixa dependência → desconectadas
        if mot >= med_m and dep < med_d:
            quadrante = "Motriz"
        elif mot >= med_m and dep >= med_d:
            quadrante = "Relé"
        elif mot < med_m and dep >= med_d:
            quadrante = "Resultado"
        else:
            quadrante = "Autônoma"

        base = next((v for v in variaveis_com_scores if v["id"] == var["id"]), {})
        resultado.append({
            **var,
            "saliencia_ciclo":    base.get("saliencia_ciclo", 5.0),
            "saliencia_racional": base.get("saliencia_racional", ""),
            "sinais_evidencia":   base.get("sinais_evidencia", []),
            "motricidade":        mot,
            "dependencia":        dep,
            "quadrante":          quadrante,
            "_threshold_mot":     round(med_m, 3),
            "_threshold_dep":     round(med_d, 3),
        })

    return resultado


def _recomendar_eixos(variaveis_calculadas: list[dict]) -> dict:
    """Seleciona as 2 variáveis motrizes de maior saliência como eixos da matriz."""
    motrizes = sorted(
        [v for v in variaveis_calculadas if v["quadrante"] in ("Motriz", "Relé")],
        key=lambda v: (v["motricidade"] * 0.6 + v["saliencia_ciclo"] * 0.4),
        reverse=True,
    )
    reles = [v for v in variaveis_calculadas if v["quadrante"] == "Relé"]
    # Preferência: 1 Motriz + 1 Relé para maximizar cobertura da matriz
    if motrizes and reles:
        eixo_x = motrizes[0]
        eixo_y = reles[0] if reles[0]["id"] != eixo_x["id"] else (reles[1] if len(reles) > 1 else motrizes[1] if len(motrizes) > 1 else motrizes[0])
    elif len(motrizes) >= 2:
        eixo_x, eixo_y = motrizes[0], motrizes[1]
    else:
        # fallback: top 2 por motricidade
        sorted_all = sorted(variaveis_calculadas, key=lambda v: v["motricidade"], reverse=True)
        eixo_x = sorted_all[0]
        eixo_y = sorted_all[1] if len(sorted_all) > 1 else sorted_all[0]

    return {
        "eixo_x": {
            "variavel_id":  eixo_x["id"],
            "nome":         eixo_x["nome"],
            "polo_baixo":   f"{eixo_x['nome']} lenta/restrita",
            "polo_alto":    f"{eixo_x['nome']} acelerada/ampla",
            "quadrante":    eixo_x["quadrante"],
            "motricidade":  eixo_x["motricidade"],
        },
        "eixo_y": {
            "variavel_id":  eixo_y["id"],
            "nome":         eixo_y["nome"],
            "polo_baixo":   f"{eixo_y['nome']} adversa/restrita",
            "polo_alto":    f"{eixo_y['nome']} favorável/ampla",
            "quadrante":    eixo_y["quadrante"],
            "motricidade":  eixo_y["motricidade"],
        },
        "justificativa": (
            f"{eixo_x['nome']} ({eixo_x['quadrante']}, motricidade {eixo_x['motricidade']:.1f}) e "
            f"{eixo_y['nome']} ({eixo_y['quadrante']}, motricidade {eixo_y['motricidade']:.1f}) "
            f"são as variáveis com maior poder de configurar os cenários do ciclo {HOJE_ISO}."
        ),
    }


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _chamar_sonnet(client: anthropic.Anthropic, system: str, user: str, max_tokens: int = 2048) -> str:
    resp = client.messages.create(
        model=MODELO,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _extrair_json(texto: str) -> str:
    texto = texto.strip()
    if texto.startswith("```"):
        linhas = texto.split("\n")
        texto = "\n".join(linhas[1:])
        if texto.rstrip().endswith("```"):
            texto = "\n".join(texto.rstrip().split("\n")[:-1])
    return texto.strip()


def _parse_json(texto: str) -> Any:
    texto = _extrair_json(texto)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", texto, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# ══════════════════════════════════════════════════════════════════════════════
# MICMAC — gerar_micmac()
# ══════════════════════════════════════════════════════════════════════════════

def gerar_micmac(client: anthropic.Anthropic, output: dict) -> dict:
    """
    6.5.13 — MICMAC Simplificado.

    Calcula saliência das 12 variáveis estruturais com base nos sinais do ciclo,
    aplica a matriz de influência estática e classifica em quadrantes Godet.
    """
    print("  -> [6.5.13] MICMAC — Variáveis estruturais + quadrantes (Sonnet)...")

    # Contexto para o LLM: sinais + vetores + market
    itens = output.get("itens") or []
    top_sinais_txt = "\n".join(
        f"- [{i.get('score_final', 0):.1f}] {i.get('titulo') or i.get('title', '')} ({i.get('tipo_sinal', '')})"
        for i in sorted(itens, key=lambda x: float(x.get("score_final") or 0), reverse=True)[:15]
    )
    vetores = output.get("vetores_estrategicos") or []
    vetores_txt = "\n".join(
        f"- {v.get('xtech') or v.get('frente', '')} | pressão {v.get('pressao_estrategica', 0)} | {(v.get('tese_central') or '')[:120]}"
        for v in vetores[:6]
    )
    market = output.get("strategic_briefing", {}).get("market_data") or {}
    market_txt = "\n".join(
        f"- {k}: {v.get('valor')} {v.get('unidade','')} | 30d: {v.get('variacao_30d_pct')}%"
        for k, v in list(market.items())[:8]
    )

    variaveis_lista = "\n".join(
        f'{v["id"]} — {v["nome"]}: {v["descricao"]}'
        for v in VARIAVEIS_ESTRUTURAIS
    )

    system = (
        "Você é um analista prospectivo especializado no método MICMAC de Michel Godet, "
        "aplicado ao setor de Tech & Energy no Brasil. "
        "Sua tarefa é medir a SALIÊNCIA (nível de atividade/relevância) de cada variável "
        "estrutural no ciclo atual, com base nos sinais fornecidos. "
        "Saliência 0 = variável ausente/inativa este ciclo. "
        "Saliência 10 = variável dominante, mencionada em múltiplos sinais de alto score. "
        "Seja preciso: use os sinais como evidência, não opine livremente. "
        "Retorne APENAS JSON válido, sem texto fora do JSON."
    )

    user = f"""Ciclo: {HOJE_ISO}

=== TOP 15 SINAIS DO CICLO (por score) ===
{top_sinais_txt}

=== VETORES ESTRATÉGICOS (top 6) ===
{vetores_txt}

=== SINAIS DE MERCADO ===
{market_txt}

=== VARIÁVEIS ESTRUTURAIS A AVALIAR ===
{variaveis_lista}

Para cada variável, retorne a saliência deste ciclo (0.0-10.0) e o racional (1 frase),
com os IDs dos sinais ou dados de mercado que justificam.

Retorne EXATAMENTE este JSON:
{{
  "saliencias": [
    {{
      "id": "V01",
      "saliencia_ciclo": 7.5,
      "saliencia_racional": "ANEEL publicou duas resoluções esta semana e MME abriu consulta pública sobre REDATA.",
      "sinais_evidencia": ["título do sinal 1", "título do sinal 2"]
    }}
  ],
  "narrativa_ciclo": "Parágrafo de 2-3 frases descrevendo quais forças motrizes dominam este ciclo e por quê."
}}"""

    try:
        t0 = time.time()
        texto = _chamar_sonnet(client, system, user, max_tokens=3000)
        resultado = _parse_json(texto)
        saliencias_llm = {s["id"]: s for s in resultado.get("saliencias", [])}
        narrativa = resultado.get("narrativa_ciclo", "")
        print(f"     [OK] Saliências calculadas em {time.time() - t0:.1f}s")
    except Exception as e:
        print(f"  [!] MICMAC saliência falhou: {e}. Usando fallback (saliência 5.0).")
        saliencias_llm = {}
        narrativa = "Análise MICMAC indisponível neste ciclo."

    # Monta lista com saliências
    variaveis_com_scores = []
    for var in VARIAVEIS_ESTRUTURAIS:
        llm = saliencias_llm.get(var["id"], {})
        variaveis_com_scores.append({
            **var,
            "saliencia_ciclo":    float(llm.get("saliencia_ciclo", 5.0)),
            "saliencia_racional": llm.get("saliencia_racional", ""),
            "sinais_evidencia":   llm.get("sinais_evidencia", []),
        })

    # Aplica matriz e calcula quadrantes
    variaveis_calculadas = _calcular_quadrantes(variaveis_com_scores)

    # Identifica listas por quadrante
    por_quadrante: dict[str, list[str]] = {
        "Motriz": [], "Relé": [], "Resultado": [], "Autônoma": []
    }
    for v in variaveis_calculadas:
        por_quadrante[v["quadrante"]].append(v["id"])

    # Recomenda eixos para a Matriz de Cenários
    eixos = _recomendar_eixos(variaveis_calculadas)

    print(
        f"     [OK] Motrizes: {por_quadrante['Motriz']} | "
        f"Relés: {por_quadrante['Relé']} | "
        f"Resultado: {por_quadrante['Resultado']} | "
        f"Autônomas: {por_quadrante['Autônoma']}"
    )
    print(f"     [OK] Eixos recomendados: {eixos['eixo_x']['nome']} × {eixos['eixo_y']['nome']}")

    return {
        "ciclo":               HOJE_ISO,
        "metodo":              "MICMAC-Godet-Simplificado-v1",
        "variaveis":           variaveis_calculadas,
        "por_quadrante":       por_quadrante,
        "eixos_recomendados":  eixos,
        "narrativa_ciclo":     narrativa,
        "gerado_em":           datetime.now(timezone(timedelta(hours=-3))).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MACTOR — gerar_mactor()
# ══════════════════════════════════════════════════════════════════════════════

def gerar_mactor(client: anthropic.Anthropic, output: dict) -> dict:
    """
    6.5.14 — MACTOR (Matriz de Alianças e Conflitos, Táticas, Objetivos e Recomendações).

    Posiciona os atores identificados no banco (hist_data.entities) em relação
    aos cenários xTech e macro, identifica alianças e conflitos, e ajusta
    a probabilidade dos cenários com base no poder dos atores.
    """
    print("  -> [6.5.14] MACTOR — Posicionamento de atores por cenário (Sonnet)...")

    # Extrai atores do hist_data
    entities = (output.get("hist_data") or {}).get("entities") or []
    if not entities:
        print("  [!] MACTOR: nenhuma entidade em hist_data. Usando fallback.")
        return _fallback_mactor()

    atores_txt = "\n".join(
        f"- {e['label']} (tipo: {e.get('desc','?')[:60]}, importância: {e.get('importance',0):.2f}, "
        f"sinais: {e.get('sinais',0)}, fronts: {', '.join(e.get('fronts',[]))})"
        for e in entities[:10]
    )

    # Contexto: cenários xTech + macro
    cenarios_xtech = output.get("cenarios") or {}
    cenarios_macro = (output.get("dashboard") or {}).get("cenarios") or []

    cenarios_xtech_txt = ""
    for xtech, cens in cenarios_xtech.items():
        for tipo, c in cens.items():
            cenarios_xtech_txt += f"\n[{xtech}/{tipo}] {c.get('titulo','')}: {(c.get('descricao','') or '')[:120]}"

    cenarios_macro_txt = "\n".join(
        f"[Macro/{c.get('id','?')}] {c.get('nome','')} (prob {c.get('probabilidade',0)}%): {(c.get('narrativa_macro','') or '')[:120]}"
        for c in cenarios_macro[:3]
    )

    system = (
        "Você é um analista do método MACTOR (Michel Godet), especialista em mapear "
        "o posicionamento estratégico de atores institucionais no setor Tech & Energy do Brasil. "
        "Para cada ator, avalie sua POSIÇÃO em relação a cada cenário: "
        "  favorável = o ator se beneficia ou ativamente promove este cenário; "
        "  resistente = o ator é prejudicado ou bloqueia ativamente este cenário; "
        "  neutro     = o ator não tem interesse relevante neste cenário. "
        "Identifique também ALIANÇAS (grupos de atores com objetivos convergentes) e "
        "CONFLITOS (pares de atores com objetivos antagônicos). "
        "Use apenas os dados fornecidos. Não invente atores ou conflitos. "
        "Retorne APENAS JSON válido."
    )

    user = f"""Ciclo: {HOJE_ISO}

=== ATORES IDENTIFICADOS NO BANCO ===
{atores_txt}

=== CENÁRIOS xTECH (pessimista/realista/otimista por frente) ===
{cenarios_xtech_txt[:2000]}

=== CENÁRIOS MACRO ===
{cenarios_macro_txt}

Tarefa:
1. Para cada ator, classifique sua posição nos cenários macro (A, B, C) como favorável/neutro/resistente.
2. Identifique até 3 alianças-chave entre atores (objetivos convergentes).
3. Identifique até 3 conflitos-chave entre pares de atores.
4. Para cada cenário macro, ajuste a probabilidade (+/- pontos) com base no poder dos atores que o apoiam ou resistem.

Retorne EXATAMENTE este JSON:
{{
  "posicionamento_atores": [
    {{
      "ator": "NOME_DO_ATOR",
      "poder_relativo": 8,
      "cenarios_macro": {{
        "A": {{"posicao": "favorável", "racional": "1 frase concisa"}},
        "B": {{"posicao": "neutro",    "racional": "1 frase concisa"}},
        "C": {{"posicao": "resistente","racional": "1 frase concisa"}}
      }}
    }}
  ],
  "aliancas": [
    {{
      "atores": ["Ator1", "Ator2"],
      "objetivo_convergente": "O que os une (1 frase)",
      "forca": "alta|media|baixa",
      "cenario_favorecido": "ID do cenário macro mais provável dado esta aliança"
    }}
  ],
  "conflitos": [
    {{
      "ator_a": "Ator1",
      "ator_b": "Ator2",
      "ponto_de_atrito": "O que os divide (1 frase)",
      "intensidade": "alta|media|baixa",
      "variavel_estrutural": "V01"
    }}
  ],
  "ajuste_probabilidades": [
    {{
      "cenario_id": "A",
      "ajuste_pct": 5,
      "justificativa": "Aliança BNDES+EPE+MME aumenta probabilidade do cenário otimista."
    }}
  ]
}}"""

    try:
        t0 = time.time()
        texto = _chamar_sonnet(client, system, user, max_tokens=4000)
        mactor_raw = _parse_json(texto)
        print(f"     [OK] MACTOR calculado em {time.time() - t0:.1f}s")
    except Exception as e:
        print(f"  [!] MACTOR falhou: {e}. Usando fallback.")
        return _fallback_mactor()

    # Aplica ajuste de probabilidade nos cenários macro
    ajustes = {a["cenario_id"]: a["ajuste_pct"] for a in mactor_raw.get("ajuste_probabilidades", [])}
    cenarios_macro_ajustados = []
    for c in cenarios_macro:
        cid = c.get("id", "")
        ajuste = ajustes.get(cid, 0)
        prob_original = int(c.get("probabilidade", 33))
        c_ajustado = dict(c)
        c_ajustado["probabilidade_mactor"] = max(5, min(90, prob_original + ajuste))
        c_ajustado["ajuste_mactor_pct"]    = ajuste
        cenarios_macro_ajustados.append(c_ajustado)

    # Enriquece cenários xTech com atores relevantes por xTech/tipo
    cenarios_xtech_enriquecidos: dict[str, dict] = {}
    posicionamento = mactor_raw.get("posicionamento_atores", [])

    for xtech, cens in cenarios_xtech.items():
        cenarios_xtech_enriquecidos[xtech] = {}
        for tipo, c in cens.items():
            # Encontra atores alinhados ao tipo de cenário
            if tipo == "pessimista":
                posicao_busca = "resistente"
            elif tipo == "otimista":
                posicao_busca = "favorável"
            else:
                posicao_busca = "neutro"

            # Usa o cenário macro de maior convergência como proxy
            atores_alinhados = []
            for p in posicionamento:
                # Extrai posições macro para inferir alinhamento xTech
                pos_macro = list((p.get("cenarios_macro") or {}).values())
                n_fav = sum(1 for pm in pos_macro if pm.get("posicao") == "favorável")
                n_res = sum(1 for pm in pos_macro if pm.get("posicao") == "resistente")
                if tipo == "otimista" and n_fav > n_res:
                    atores_alinhados.append({"ator": p["ator"], "poder": p.get("poder_relativo", 5)})
                elif tipo == "pessimista" and n_res > n_fav:
                    atores_alinhados.append({"ator": p["ator"], "poder": p.get("poder_relativo", 5)})
                elif tipo == "realista":
                    atores_alinhados.append({"ator": p["ator"], "poder": p.get("poder_relativo", 5)})

            atores_alinhados.sort(key=lambda x: x["poder"], reverse=True)
            cenarios_xtech_enriquecidos[xtech][tipo] = {
                **c,
                "atores_alinhados": atores_alinhados[:3],
            }

    # Resumo de convergências e conflitos por xTech
    aliancas   = mactor_raw.get("aliancas", [])
    conflitos  = mactor_raw.get("conflitos", [])

    n_al = len(aliancas)
    n_co = len(conflitos)
    print(f"     [OK] {len(posicionamento)} atores posicionados | {n_al} alianças | {n_co} conflitos")

    return {
        "ciclo":                    HOJE_ISO,
        "metodo":                   "MACTOR-Godet-v1",
        "posicionamento_atores":    posicionamento,
        "aliancas":                 aliancas,
        "conflitos":                conflitos,
        "ajuste_probabilidades":    mactor_raw.get("ajuste_probabilidades", []),
        "cenarios_macro_ajustados": cenarios_macro_ajustados,
        "cenarios_xtech_enriquecidos": cenarios_xtech_enriquecidos,
        "gerado_em":                datetime.now(timezone(timedelta(hours=-3))).isoformat(),
    }


def _fallback_mactor() -> dict:
    return {
        "ciclo":   HOJE_ISO,
        "metodo":  "MACTOR-Godet-v1",
        "fallback": True,
        "posicionamento_atores":       [],
        "aliancas":                    [],
        "conflitos":                   [],
        "ajuste_probabilidades":       [],
        "cenarios_macro_ajustados":    [],
        "cenarios_xtech_enriquecidos": {},
        "gerado_em": datetime.now(timezone(timedelta(hours=-3))).isoformat(),
    }
