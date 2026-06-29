#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_collector_v1.py — Camada Market & Macro Signals · Efagundes Radar xTech

Coleta dados quantitativos em 3 ondas (executadas em sequência, todas em um ciclo):
  Onda 1 — Essencial:   USD/BRL, EUR/BRL, CNY/BRL, Selic, IPCA, Ibovespa, IEE,
                         UTIL, IFIX, Brent, Gás Natural, Cobre, Soja, Milho
  Onda 2 — Estratégica: Nasdaq, S&P 500, DXY, Lítio, Níquel, Ouro, Minério de
                         ferro, IAGRO, IMAT, INDX, IFNC, IGP-M, CDI
  Onda 3 — Diferencial: PLD (CCEE), Bandeira tarifária (ANEEL), Reservatórios (ONS)

Fontes:
  - yfinance   → bolsas, commodities, câmbio FX
  - BCB PTAX   → câmbio oficial (USD/BRL, EUR/BRL, CNY/BRL)
  - BCB SGS    → Selic, CDI, IPCA, IGP-M
  - CCEE API   → PLD atual (Onda 3, falha silenciosa)
  - ANEEL      → Bandeira tarifária (Onda 3, scraping leve)

Output:
  market_signals.json  — um objeto por indicador, com variações temporais

Uso:
  python market_collector_v1.py
  python market_collector_v1.py --output /caminho/market_signals.json
  python market_collector_v1.py --only-onda1
  python market_collector_v1.py --only-onda2
  python market_collector_v1.py --only-onda3
  python market_collector_v1.py --dry-run   # valida estrutura sem gravar
"""

from __future__ import annotations

import argparse
import json
import time
import warnings
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

warnings.filterwarnings("ignore")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BRASILIA = timezone(timedelta(hours=-3))
DEFAULT_OUTPUT = str(Path(__file__).parent / "market_signals.json")

# ─── Definição dos indicadores ────────────────────────────────────────────────

# Câmbio via BCB PTAX (moedas) — siglas BCB
BCB_CURRENCIES = [
    {"indicador": "USD/BRL", "moeda": "USD", "categoria": "cambio",
     "unidade": "BRL por USD", "xtech": ["EnergyTech", "FinTech", "DeepTech", "CleanTech", "AgriTech"],
     "relevancia": "CAPEX de equipamentos importados, dívida externa, insumos industriais"},
    {"indicador": "EUR/BRL", "moeda": "EUR", "categoria": "cambio",
     "unidade": "BRL por EUR", "xtech": ["EnergyTech", "CleanTech"],
     "relevancia": "Exportações de energia, projetos com financiadores europeus"},
    # CNY não disponível na PTAX — coletado via yfinance (ticker CNY=X) abaixo
]

# SGS Banco Central — séries temporais
BCB_SGS = [
    {"indicador": "Selic", "serie": 432, "categoria": "juros",
     "unidade": "% a.a.", "periodicidade": "diaria",
     "xtech": ["EnergyTech", "FinTech", "CleanTech", "AgriTech"],
     "relevancia": "Custo de capital para projetos de infraestrutura e P&D"},
    {"indicador": "CDI", "serie": 4389, "categoria": "juros",
     "unidade": "% a.a.", "periodicidade": "diaria",
     "xtech": ["FinTech", "EnergyTech"],
     "relevancia": "Benchmark de rentabilidade para investimentos alternativos"},
    {"indicador": "IPCA", "serie": 433, "categoria": "inflacao",
     "unidade": "% a.m.", "periodicidade": "mensal",
     "xtech": ["EnergyTech", "AgriTech", "FinTech"],
     "relevancia": "Pressão sobre contratos de energia, insumos e reajustes tarifários"},
    {"indicador": "IGP-M", "serie": 189, "categoria": "inflacao",
     "unidade": "% a.m.", "periodicidade": "mensal",
     "xtech": ["EnergyTech", "AgriTech"],
     "relevancia": "Reajuste de contratos de aluguel, energia e obras de infraestrutura"},
]

# yfinance — bolsas globais, B3 setoriais, commodities
YFINANCE_TICKERS = [
    # Câmbio FX (CNY/BRL via yfinance — BCB PTAX não disponibiliza yuan)
    {"indicador": "CNY/BRL", "ticker": "CNY=X", "categoria": "cambio",
     "unidade": "USD por CNY (proxy)", "onda": 1,
     "xtech": ["DeepTech", "CleanTech", "EnergyTech"],
     "relevancia": "Custo de painéis solares, baterias LFP e equipamentos chineses (CAPEX)"},

    # DXY
    {"indicador": "DXY", "ticker": "DX-Y.NYB", "categoria": "cambio",
     "unidade": "índice", "onda": 2,
     "xtech": ["EnergyTech", "FinTech", "DeepTech"],
     "relevancia": "Força global do dólar; quando sobe, pressiona commodities e emergentes"},

    # Bolsas globais
    {"indicador": "S&P 500", "ticker": "^GSPC", "categoria": "bolsa_global",
     "unidade": "pontos", "onda": 2,
     "xtech": ["DeepTech", "FinTech"],
     "relevancia": "Termômetro de apetite a risco e liquidez global"},
    {"indicador": "Nasdaq", "ticker": "^IXIC", "categoria": "bolsa_global",
     "unidade": "pontos", "onda": 2,
     "xtech": ["DeepTech"],
     "relevancia": "Humor do mercado para tecnologia e startups"},
    {"indicador": "Dow Jones", "ticker": "^DJI", "categoria": "bolsa_global",
     "unidade": "pontos", "onda": 2,
     "xtech": ["EnergyTech", "FinTech"],
     "relevancia": "Empresas tradicionais / industriais de grande porte"},
    {"indicador": "STOXX Europe 600", "ticker": "^STOXX", "categoria": "bolsa_global",
     "unidade": "pontos", "onda": 2,
     "xtech": ["EnergyTech", "CleanTech"],
     "relevancia": "Mercado europeu — regulação ESG e CBAM afetam agenda energética BR"},
    {"indicador": "Nikkei 225", "ticker": "^N225", "categoria": "bolsa_global",
     "unidade": "pontos", "onda": 2,
     "xtech": ["DeepTech", "EnergyTech"],
     "relevancia": "Cadeia industrial Asia; semicondutores, robótica, energia"},

    # B3 — índice geral
    {"indicador": "Ibovespa", "ticker": "^BVSP", "categoria": "bolsa_b3",
     "unidade": "pontos", "onda": 1,
     "xtech": ["FinTech", "EnergyTech"],
     "relevancia": "Humor do mercado brasileiro; base para força relativa setorial"},

    # B3 — índices setoriais estratégicos
    # IEE.SA foi descontinuado no yfinance; usando CPFE3.SA (CPFL) como proxy de EnergyTech B3
    {"indicador": "IEE proxy (CPFE3)", "ticker": "CPFE3.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "BRL/ação", "onda": 1,
     "xtech": ["EnergyTech"],
     "relevancia": "Proxy do setor elétrico B3; CPFL Energia — distribuição, geração e transmissão"},
    {"indicador": "UTIL (Utilidades)", "ticker": "UTIL.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "pontos", "onda": 1,
     "xtech": ["EnergyTech", "CleanTech"],
     "relevancia": "Percepção de infraestrutura defensiva; saneamento, energia, concessões"},
    {"indicador": "IFIX (FIIs)", "ticker": "IFIX.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "pontos", "onda": 1,
     "xtech": ["DeepTech", "FinTech"],
     "relevancia": "Atratividade imobiliária vs Selic; afeta data centers e galpões logísticos"},
    {"indicador": "IMAT (Materiais Básicos)", "ticker": "IMAT.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "pontos", "onda": 2,
     "xtech": ["CleanTech", "EnergyTech"],
     "relevancia": "Minério de ferro, lítio, cobre — cadeia de transição energética"},
    {"indicador": "INDX (Industrial)", "ticker": "INDX.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "pontos", "onda": 2,
     "xtech": ["DeepTech", "EnergyTech"],
     "relevancia": "Indústria pesada, robótica, equipamentos — proxy de demanda de infraestrutura"},
    {"indicador": "SLCE3 (Agronegócio)", "ticker": "SLCE3.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "BRL/ação", "onda": 2,
     "xtech": ["AgriTech"],
     "relevancia": "SLC Agrícola — proxy do agronegócio brasileiro; IAGRO.SA cancelado pela B3"},
    {"indicador": "IFNC (Financeiro)", "ticker": "IFNC.SA", "categoria": "bolsa_b3_setorial",
     "unidade": "pontos", "onda": 2,
     "xtech": ["FinTech"],
     "relevancia": "Crédito, Open Finance, spread bancário — ecossistema FinTech"},

    # Energia e commodities industriais
    {"indicador": "Brent", "ticker": "BZ=F", "categoria": "commodity_energia",
     "unidade": "USD/barril", "onda": 1,
     "xtech": ["EnergyTech", "CleanTech"],
     "relevancia": "Referência global de preço de energia; pressão sobre PLD e termelétricas"},
    {"indicador": "WTI", "ticker": "CL=F", "categoria": "commodity_energia",
     "unidade": "USD/barril", "onda": 2,
     "xtech": ["EnergyTech"],
     "relevancia": "Petróleo americano; spread Brent-WTI indica logística global"},
    {"indicador": "Gás Natural (HH)", "ticker": "NG=F", "categoria": "commodity_energia",
     "unidade": "USD/MMBtu", "onda": 1,
     "xtech": ["EnergyTech", "CleanTech"],
     "relevancia": "Custo de despacho térmico a gás; afeta PLD e descarbonização"},
    {"indicador": "Urânio", "ticker": "URA", "categoria": "commodity_energia",
     "unidade": "USD (ETF)", "onda": 2,
     "xtech": ["EnergyTech", "DeepTech"],
     "relevancia": "Proxy de expectativas para energia nuclear / SMR"},

    # Metais industriais e transição energética
    {"indicador": "Cobre", "ticker": "HG=F", "categoria": "commodity_metal",
     "unidade": "USD/lb", "onda": 1,
     "xtech": ["EnergyTech", "CleanTech", "DeepTech"],
     "relevancia": "Sinal de eletrificação e infraestrutura; BESS, transmissão, EVs"},
    {"indicador": "Alumínio", "ticker": "ALI=F", "categoria": "commodity_metal",
     "unidade": "USD/tonelada", "onda": 2,
     "xtech": ["EnergyTech", "CleanTech"],
     "relevancia": "Estruturas de painéis solares, baterias, veículos leves"},
    {"indicador": "Metais Críticos (REMX)", "ticker": "REMX", "categoria": "commodity_metal",
     "unidade": "USD (ETF)", "onda": 2,
     "xtech": ["CleanTech", "DeepTech"],
     "relevancia": "ETF de metais raros e estratégicos (níquel, cobalto, lítio) — proxy de cadeia de baterias; NI=F removido do Yahoo Finance"},
    {"indicador": "Vale (Minério de Ferro)", "ticker": "VALE3.SA", "categoria": "commodity_metal",
     "unidade": "BRL/ação", "onda": 2,
     "xtech": ["EnergyTech", "AgriTech"],
     "relevancia": "Vale como proxy de minério de ferro e demanda industrial global; TIO=F sem dados confiáveis no Yahoo Finance"},
    {"indicador": "Ouro", "ticker": "GC=F", "categoria": "commodity_metal",
     "unidade": "USD/oz", "onda": 2,
     "xtech": ["FinTech"],
     "relevancia": "Ativo de proteção; sobe quando há incerteza macro e geopolítica"},
    {"indicador": "Prata", "ticker": "SI=F", "categoria": "commodity_metal",
     "unidade": "USD/oz", "onda": 2,
     "xtech": ["EnergyTech", "DeepTech"],
     "relevancia": "Semicondutores, painéis solares e eletroeletrônicos"},
    {"indicador": "Lítio (ETF proxy)", "ticker": "LIT", "categoria": "commodity_metal",
     "unidade": "USD (ETF)", "onda": 2,
     "xtech": ["CleanTech", "EnergyTech", "DeepTech"],
     "relevancia": "Mercado de baterias; reservas brasileiras e cadeia de valor EV"},

    # Commodities agrícolas
    {"indicador": "Soja", "ticker": "ZS=F", "categoria": "commodity_agro",
     "unidade": "USc/bushel", "onda": 1,
     "xtech": ["AgriTech"],
     "relevancia": "Principal commodity de exportação BR; afeta crédito rural e tecnologia agrícola"},
    {"indicador": "Milho", "ticker": "ZC=F", "categoria": "commodity_agro",
     "unidade": "USc/bushel", "onda": 1,
     "xtech": ["AgriTech"],
     "relevancia": "Insumo de etanol e ração; correlaciona com biocombustíveis e AgriTech"},
    {"indicador": "Café (Arábica)", "ticker": "KC=F", "categoria": "commodity_agro",
     "unidade": "USc/lb", "onda": 2,
     "xtech": ["AgriTech"],
     "relevancia": "Brasil é maior exportador mundial; sinal de competitividade agrícola"},
    {"indicador": "Açúcar", "ticker": "SB=F", "categoria": "commodity_agro",
     "unidade": "USc/lb", "onda": 2,
     "xtech": ["AgriTech", "EnergyTech"],
     "relevancia": "Conexão açúcar-etanol; afeta biocombustíveis e mix energético"},
    {"indicador": "Etanol (proxy milho)", "ticker": "ZC=F", "categoria": "commodity_energia",
     "unidade": "USc/bushel (milho proxy)", "onda": 2,
     "xtech": ["AgriTech", "EnergyTech", "CleanTech"],
     "relevancia": "Biocombustíveis e descarbonização do transporte"},
]


# ─── Helpers BCB ──────────────────────────────────────────────────────────────

def _bcb_data_util_recente(dias_atras: int = 7) -> str:
    """Retorna a data útil mais recente no formato MM-DD-YYYY para a PTAX."""
    for i in range(dias_atras):
        dt = date.today() - timedelta(days=i)
        if dt.weekday() < 5:
            return dt.strftime("%m-%d-%Y")
    return date.today().strftime("%m-%d-%Y")


def coletar_ptax(moeda: str) -> Optional[Dict[str, Any]]:
    """Coleta cotação oficial BCB/PTAX para moeda estrangeira vs BRL."""
    data_str = _bcb_data_util_recente()
    url = (
        f"https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
        f"CotacaoMoedaDia(moeda=@moeda,dataCotacao=@dataCotacao)"
        f"?@moeda=%27{moeda}%27&@dataCotacao=%27{data_str}%27"
        f"&$top=1&$format=json"
    )
    try:
        r = requests.get(url, timeout=10)
        vals = r.json().get("value", [])
        if not vals:
            # Fallback para USD
            if moeda == "USD":
                url2 = (
                    f"https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
                    f"CotacaoDolarDia(dataCotacao=@dataCotacao)"
                    f"?@dataCotacao=%27{data_str}%27&$top=1&$format=json"
                )
                r2 = requests.get(url2, timeout=10)
                vals = r2.json().get("value", [])
        if vals:
            v = vals[0]
            return {
                "valor": round(float(v.get("cotacaoVenda") or v.get("cotacaoCompra") or 0), 4),
                "data_referencia": data_str,
                "fonte": "BCB PTAX",
            }
    except Exception as e:
        print(f"    · PTAX {moeda}: {e}")
    return None


def coletar_sgs(serie: int, n_periodos: int = 13) -> Optional[Dict[str, Any]]:
    """Coleta série temporal SGS/BCB. n_periodos define janela para variações."""
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/{n_periodos}?formato=json"
    try:
        r = requests.get(url, timeout=10)
        dados = r.json()
        if not dados:
            return None
        ultimo = dados[-1]
        valor_atual = float(ultimo["valor"].replace(",", "."))

        # Variações: última, há 1 mês, há 12 meses
        var_1 = round(valor_atual - float(dados[-2]["valor"].replace(",", ".")) if len(dados) >= 2 else 0, 4)
        var_12 = round(valor_atual - float(dados[0]["valor"].replace(",", ".")) if len(dados) >= 12 else 0, 4)

        # Acumulado últimos 12 meses (para inflação)
        acum_12 = round(sum(float(d["valor"].replace(",", ".")) for d in dados[-12:]), 4) if len(dados) >= 12 else None

        return {
            "valor": round(valor_atual, 4),
            "data_referencia": ultimo["data"],
            "variacao_periodo_anterior": var_1,
            "variacao_12_periodos": var_12,
            "acumulado_12_periodos": acum_12,
            "fonte": "BCB SGS",
        }
    except Exception as e:
        print(f"    · SGS {serie}: {e}")
    return None


# ─── Helpers yfinance ─────────────────────────────────────────────────────────

def coletar_yfinance(ticker: str) -> Optional[Dict[str, Any]]:
    """Coleta preço atual e variações via yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fi = t.fast_info
        preco = getattr(fi, "last_price", None)
        prev = getattr(fi, "previous_close", None)
        if preco is None:
            return None

        # Variação diária
        var_dia = round((preco - prev) / prev * 100, 2) if prev else None

        # Variação semanal e mensal via histórico
        hist = t.history(period="1y", interval="1d", auto_adjust=True)
        var_7d = var_30d = var_12m = None
        if not hist.empty:
            closes = hist["Close"].dropna()
            if len(closes) >= 5:
                var_7d = round((preco - closes.iloc[-5]) / closes.iloc[-5] * 100, 2)
            if len(closes) >= 21:
                var_30d = round((preco - closes.iloc[-21]) / closes.iloc[-21] * 100, 2)
            if len(closes) >= 252:
                var_12m = round((preco - closes.iloc[-252]) / closes.iloc[-252] * 100, 2)
            elif len(closes) >= 2:
                var_12m = round((preco - closes.iloc[0]) / closes.iloc[0] * 100, 2)

        return {
            "valor": round(preco, 4),
            "data_referencia": date.today().isoformat(),
            "variacao_dia_pct": var_dia,
            "variacao_7d_pct": var_7d,
            "variacao_30d_pct": var_30d,
            "variacao_12m_pct": var_12m,
            "fechamento_anterior": round(prev, 4) if prev else None,
            "fonte": "yfinance",
        }
    except Exception as e:
        print(f"    · yfinance {ticker}: {e}")
    return None


# ─── Onda 3 — Diferencial (CCEE / ANEEL / ONS) ───────────────────────────────

def coletar_pld() -> Optional[Dict[str, Any]]:
    """Tenta coletar PLD atual via CCEE (falha silenciosa se indisponível)."""
    try:
        url = "https://www.ccee.org.br/portal/faces/pages_publico/o-que-fazemos/como_ccee_funciona/precos/precos_medios"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        # Busca padrão de valor numérico próximo a "PLD" ou "R$"
        texto = soup.get_text(" ", strip=True)
        import re
        m = re.search(r"PLD[^R]*R\$\s*([\d.,]+)", texto, re.IGNORECASE)
        if m:
            valor_str = m.group(1).replace(".", "").replace(",", ".")
            return {
                "valor": float(valor_str),
                "data_referencia": date.today().isoformat(),
                "unidade": "R$/MWh",
                "fonte": "CCEE (scraping)",
                "nota": "valor aproximado via scraping — validar com boletim oficial",
            }
    except Exception:
        pass
    return None


def coletar_bandeira_tarifaria() -> Optional[Dict[str, Any]]:
    """Coleta bandeira tarifária ANEEL via scraping leve."""
    try:
        url = "https://www.aneel.gov.br/bandeiras-tarifarias"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok:
            return None
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(r.text, "html.parser")
        texto = soup.get_text(" ", strip=True)
        for bandeira in ["Escassez Hídrica", "Vermelha Patamar 2", "Vermelha Patamar 1", "Amarela", "Verde"]:
            if bandeira.lower() in texto.lower():
                return {
                    "valor": bandeira,
                    "data_referencia": date.today().isoformat(),
                    "unidade": "bandeira",
                    "xtech": ["EnergyTech"],
                    "relevancia": "Custo adicional na fatura de energia; afeta consumidores cativos",
                    "fonte": "ANEEL (scraping)",
                }
    except Exception:
        pass
    return None


# ─── Montagem dos sinais ──────────────────────────────────────────────────────

def montar_sinal(meta: Dict[str, Any], dados: Dict[str, Any]) -> Dict[str, Any]:
    """Combina metadados do indicador com dados coletados."""
    sinal = {
        "data": date.today().isoformat(),
        "coletado_em": datetime.now(BRASILIA).isoformat(),
        "categoria": meta.get("categoria", ""),
        "indicador": meta.get("indicador", ""),
        "onda": meta.get("onda", 1),
        "unidade": meta.get("unidade", ""),
        "xtech": meta.get("xtech", []),
        "relevancia": meta.get("relevancia", ""),
        "fonte": dados.get("fonte", ""),
        "valor": dados.get("valor"),
        "data_referencia": dados.get("data_referencia"),
    }
    # Campos opcionais de variação
    for campo in ["variacao_dia_pct", "variacao_7d_pct", "variacao_30d_pct", "variacao_12m_pct",
                  "variacao_periodo_anterior", "variacao_12_periodos", "acumulado_12_periodos",
                  "fechamento_anterior", "nota"]:
        if campo in dados:
            sinal[campo] = dados[campo]
    return sinal


# ─── Indicadores derivados ────────────────────────────────────────────────────

def calcular_indicadores_derivados(sinais: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calcula spread e ratios estratégicos a partir dos sinais coletados."""
    idx = {s["indicador"]: s for s in sinais if s.get("valor") is not None}
    derivados = []
    agora = datetime.now(BRASILIA).isoformat()

    def derived(indicador, categoria, valor, formula, xtech, relevancia):
        return {
            "data": date.today().isoformat(),
            "coletado_em": agora,
            "categoria": categoria,
            "indicador": indicador,
            "onda": "derivado",
            "unidade": "calculado",
            "xtech": xtech,
            "relevancia": relevancia,
            "fonte": "calculado (market_collector_v1)",
            "valor": round(valor, 4) if valor is not None else None,
            "formula": formula,
        }

    # Selic real (Selic - IPCA acumulado 12m)
    selic = idx.get("Selic", {}).get("valor")
    ipca_acum = idx.get("IPCA", {}).get("acumulado_12_periodos")
    if selic and ipca_acum:
        derivados.append(derived(
            "Selic Real", "juros_derivado",
            selic - ipca_acum,
            "Selic - IPCA acumulado 12 meses",
            ["EnergyTech", "FinTech", "CleanTech"],
            "Custo real de capital; acima de 6% torna projetos de infraestrutura marginais inviáveis",
        ))

    # Força relativa IEE vs Ibovespa (variação 30d)
    iee_30 = idx.get("IEE (Energia Elétrica)", {}).get("variacao_30d_pct")
    ibov_30 = idx.get("Ibovespa", {}).get("variacao_30d_pct")
    if iee_30 is not None and ibov_30 is not None:
        derivados.append(derived(
            "IEE vs Ibovespa (30d)", "ratio_setorial",
            iee_30 - ibov_30,
            "variacao_30d_pct(IEE) - variacao_30d_pct(Ibovespa)",
            ["EnergyTech"],
            "Força relativa do setor elétrico vs mercado; positivo indica preferência por energia",
        ))

    # UTIL vs Ibovespa (30d)
    util_30 = idx.get("UTIL (Utilidades)", {}).get("variacao_30d_pct")
    if util_30 is not None and ibov_30 is not None:
        derivados.append(derived(
            "UTIL vs Ibovespa (30d)", "ratio_setorial",
            util_30 - ibov_30,
            "variacao_30d_pct(UTIL) - variacao_30d_pct(Ibovespa)",
            ["EnergyTech", "CleanTech"],
            "Percepção defensiva de infraestrutura; positivo indica fuga para qualidade",
        ))

    # Spread Brent 30d (pressão energética)
    brent_30 = idx.get("Brent", {}).get("variacao_30d_pct")
    if brent_30 is not None:
        nivel = "alta" if brent_30 > 5 else ("queda" if brent_30 < -5 else "estável")
        derivados.append(derived(
            "Brent Tendência 30d", "energia_derivado",
            brent_30,
            "variacao_30d_pct(Brent)",
            ["EnergyTech", "CleanTech"],
            f"Pressão energética global: {nivel} — impacta PLD e competitividade de renováveis",
        ))

    # Pressão cambial sobre CAPEX (USD/BRL 30d)
    usd_30 = idx.get("USD/BRL", {}).get("variacao_30d_pct")
    if usd_30 is not None:
        derivados.append(derived(
            "Pressão Cambial CAPEX (30d)", "cambio_derivado",
            usd_30,
            "variacao_30d_pct(USD/BRL)",
            ["EnergyTech", "DeepTech", "CleanTech"],
            f"Pressão sobre CAPEX importado: BRL {'enfraqueceu' if usd_30 > 0 else 'fortaleceu'} {abs(usd_30):.1f}% em 30d",
        ))

    return derivados


# ─── Execução principal ───────────────────────────────────────────────────────

def executar_onda1(ondas_ativas: set) -> List[Dict[str, Any]]:
    if 1 not in ondas_ativas:
        return []
    sinais = []
    print("\n  [Onda 1] Câmbio BCB PTAX")
    for cfg in BCB_CURRENCIES:
        print(f"    · {cfg['indicador']}...")
        dados = coletar_ptax(cfg["moeda"])
        if dados:
            s = montar_sinal({**cfg, "onda": 1}, dados)
            sinais.append(s)
            print(f"      ✓ {cfg['indicador']} = {dados['valor']}")

    print("  [Onda 1] BCB SGS — Juros e Inflação")
    for cfg in BCB_SGS:
        print(f"    · {cfg['indicador']}...")
        dados = coletar_sgs(cfg["serie"])
        if dados:
            s = montar_sinal({**cfg, "onda": 1}, dados)
            sinais.append(s)
            print(f"      ✓ {cfg['indicador']} = {dados['valor']} ({dados.get('data_referencia','')})")

    print("  [Onda 1] yfinance — Onda 1 essencial")
    onda1_tickers = [t for t in YFINANCE_TICKERS if t.get("onda", 1) == 1]
    for cfg in onda1_tickers:
        print(f"    · {cfg['indicador']} ({cfg['ticker']})...")
        dados = coletar_yfinance(cfg["ticker"])
        if dados:
            s = montar_sinal(cfg, dados)
            sinais.append(s)
            vd = dados.get("variacao_dia_pct", 0) or 0
            print(f"      ✓ {cfg['indicador']} = {dados['valor']} ({vd:+.2f}% dia)")

    return sinais


def executar_onda2(ondas_ativas: set) -> List[Dict[str, Any]]:
    if 2 not in ondas_ativas:
        return []
    sinais = []
    print("\n  [Onda 2] yfinance — Estratégica")
    onda2_tickers = [t for t in YFINANCE_TICKERS if t.get("onda", 1) == 2]
    for cfg in onda2_tickers:
        print(f"    · {cfg['indicador']} ({cfg['ticker']})...")
        dados = coletar_yfinance(cfg["ticker"])
        if dados:
            s = montar_sinal(cfg, dados)
            sinais.append(s)
            vd = dados.get("variacao_dia_pct", 0) or 0
            print(f"      ✓ {cfg['indicador']} = {dados['valor']} ({vd:+.2f}% dia)")
    return sinais


def executar_onda3(ondas_ativas: set) -> List[Dict[str, Any]]:
    if 3 not in ondas_ativas:
        return []
    sinais = []
    print("\n  [Onda 3] Diferencial — PLD / Bandeira Tarifária")

    print("    · PLD atual (CCEE)...")
    pld = coletar_pld()
    if pld:
        s = {
            "data": date.today().isoformat(),
            "coletado_em": datetime.now(BRASILIA).isoformat(),
            "categoria": "energia_eletrica",
            "indicador": "PLD",
            "onda": 3,
            "xtech": ["EnergyTech"],
            "relevancia": "Preço de liquidação de diferenças; referência para ACL e rentabilidade de geração",
            **pld,
        }
        sinais.append(s)
        print(f"      ✓ PLD = R$ {pld['valor']}/MWh")
    else:
        print("      · PLD não disponível via scraping este ciclo")

    print("    · Bandeira tarifária (ANEEL)...")
    bandeira = coletar_bandeira_tarifaria()
    if bandeira:
        s = {
            "data": date.today().isoformat(),
            "coletado_em": datetime.now(BRASILIA).isoformat(),
            "categoria": "energia_eletrica",
            "indicador": "Bandeira Tarifária",
            "onda": 3,
            **bandeira,
        }
        sinais.append(s)
        print(f"      ✓ Bandeira = {bandeira['valor']}")
    else:
        print("      · Bandeira não disponível via scraping este ciclo")

    return sinais


def main() -> int:
    parser = argparse.ArgumentParser(description="Market Collector v1 — Sinais Quantitativos")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="market_signals.json de saída")
    parser.add_argument("--only-onda1", action="store_true")
    parser.add_argument("--only-onda2", action="store_true")
    parser.add_argument("--only-onda3", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Coleta mas não grava")
    args = parser.parse_args()

    ondas_ativas: set[int] = {1, 2, 3}
    if args.only_onda1: ondas_ativas = {1}
    if args.only_onda2: ondas_ativas = {2}
    if args.only_onda3: ondas_ativas = {3}

    print(f"\n{'=' * 66}")
    print("  MARKET COLLECTOR v1 — Sinais Quantitativos")
    print(f"  Ondas: {sorted(ondas_ativas)} | {datetime.now(BRASILIA).strftime('%d/%m/%Y %H:%M')}")
    print(f"  {'=' * 62}")

    t0 = time.time()
    sinais: List[Dict[str, Any]] = []

    sinais += executar_onda1(ondas_ativas)
    sinais += executar_onda2(ondas_ativas)
    sinais += executar_onda3(ondas_ativas)

    # Indicadores derivados
    print("\n  [Derivados] Calculando indicadores compostos...")
    derivados = calcular_indicadores_derivados(sinais)
    sinais += derivados
    print(f"    ✓ {len(derivados)} indicadores derivados calculados")

    # Estatísticas
    por_categoria = {}
    for s in sinais:
        cat = s.get("categoria", "outro")
        por_categoria[cat] = por_categoria.get(cat, 0) + 1

    output = {
        "coletado_em": datetime.now(BRASILIA).isoformat(),
        "data": date.today().isoformat(),
        "ciclo_id": date.today().isoformat(),
        "total_sinais": len(sinais),
        "ondas_executadas": sorted(ondas_ativas),
        "por_categoria": por_categoria,
        "sinais": sinais,
    }

    elapsed = time.time() - t0
    print(f"\n{'─' * 66}")
    print(f"  Total coletado: {len(sinais)} sinais em {elapsed:.1f}s")
    for cat, n in sorted(por_categoria.items()):
        print(f"    {cat:<35} {n}")

    if not args.dry_run:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  ✓ Salvo: {out_path}")
    else:
        print("\n  · [dry-run] market_signals.json não gravado")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
