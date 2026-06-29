import feedparser
import json
import hashlib
import os
import time
import requests
import random
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from urllib.parse import urlparse
import warnings
from urllib3.exceptions import InsecureRequestWarning

# Suprimir warnings de SSL para feeds Google News (comportamento esperado)
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

# collector_v7.py — Fontes auditadas por score médio (intel_output.json)
# v7 vs v6: 19 fontes removidas (score < 3.0 ou conteúdo genérico sem aderência a xTechs)
# Fontes removidas: Portafolio CO, BBC Brasil, DW Brasil, Exame, La Nación AR,
#   El Economista MX, Diário Financiero CL, El Deber BO, IBGE (→ market_collector),
#   Embrapa Inovação (GN), FAPESP (GN), IEEE Spectrum (GN), Distrito Insights,
#   WIPO Tech Trends, InfoMoney Energia, MIT Tech Review BR, Nature News,
#   Globo Rural Tech, Ars Technica IT
# Queries refinadas: ANEEL, CCEE, JOTA Energia, Agência Brasil

OUTPUT_FILE = "feed_bruto.json"
SEEN_FILE   = "seen_hashes.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# ─────────────────────────────────────────────────────────────────────────────
# FONTES ATIVAS — v11.0  (~104 fontes)
# peso: 3=regulação/oficial  2=especializada  1=geral
# modo: "rss" | "scraping"
# tipo_fonte: "regulatorio" | "tecnologia" | "mercado" | "global"
#   regulatorio → fontes governamentais/regulatórias (cap 30% por ciclo)
#   tecnologia  → fontes de inovação, startups, pesquisa, tech pura
#   mercado     → imprensa financeira/negócios generalista
#   global      → fontes internacionais de tech/mercado
#
# Calendário xTech (campo "xtech"):
#   Segunda → EnergyTech  |  Terça  → CleanTech  |  Quarta → FinTech
#   Quinta  → DeepTech    |  Sexta  → AgroTech
#
# O campo "xtech" é usado pelo gerar_xtech_radar_v1.py para filtrar sinais
# por categoria no dia correto da semana.
# ─────────────────────────────────────────────────────────────────────────────

# Teto de sinais regulatórios por ciclo (proporção máxima do total coletado)
CAP_REGULATORIO = 0.30

SOURCES = [

    # ── REGULAÇÃO & POLÍTICAS (BRASIL) ───────────────────────────────────────
    {
        "label": "ANEEL",
        "tema": "Regulação & Políticas",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:aneel.gov.br+resolução+OR+portaria+OR+leilão+OR+audiência+pública+OR+tarifa&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "MME",
        "tema": "Regulação & Políticas",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:gov.br/mme&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "CCEE",
        "tema": "Regulação & Políticas",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:ccee.org.br+PLD+OR+leilão+OR+liquidação+OR+contratação+OR+BESS+OR+armazenamento&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "Banco Central",
        "tema": "Macroeconomia",
        "xtech": "FinTech",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:bcb.gov.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    # IBGE removido (score 1.50 — macro quantitativo coberto pelo market_collector_v1)
    {
        "label": "EPE",
        "tema": "Energia",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:epe.gov.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "JOTA Energia",
        "tema": "Regulação & Políticas",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:jota.info+ANEEL+OR+CCEE+OR+setor+elétrico+OR+regulação+energética+OR+BESS+OR+transmissão&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    {
        "label": "Agência Brasil",
        "tema": "Regulação & Políticas",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
        "limite": 6,
    },

    # ── ENERGIA (BRASIL) ─────────────────────────────────────────────────────
    {
        "label": "Canal Energia",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "scraping",
        "url": "https://www.canalenergia.com.br/",
        "seletor": "h2 a[href*='/noticias/'], h3 a[href*='/noticias/'], a.materia-titulo",
    },
    {
        "label": "EPBR",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:epbr.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "Brasil Energia",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:brasilenergia.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "MegaWhat",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "scraping",
        "url": "https://megawhat.energy/",
        "seletor": "h2 a, h3 a, article a",
    },
    {
        "label": "Valor Infra",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "scraping",
        "url": "https://valor.globo.com/empresas/infraestrutura/",
        "seletor": ".feed-post-link",
    },
    # InfoMoney Energia removido (score 2.50 — conteúdo macro genérico, coberto por market_collector)

    # ── AMÉRICALATINA & CARIBE ───────────────────────────────────────────────
    {
        "label": "OLADE",
        "tema": "América Latina",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:olade.org&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "Energía Estratégica",
        "tema": "América Latina",
        "tipo_fonte": "global",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:energiaestrategica.com&hl=es-419&gl=AR&ceid=AR:es-419",
    },
    {
        "label": "BNamericas Energy",
        "tema": "América Latina",
        "tipo_fonte": "global",
        "peso": 2,
        "modo": "scraping",
        "url": "https://www.bnamericas.com/en/news/electric-power",
        "seletor": "h2 a, h3 a, .news-item__title a, article h3 a",
    },
    # La Nación AR (1.65), Diário Financiero CL (2.00), El Economista MX (1.95),
    # Portafolio CO (1.40), El Deber BO (2.10) — removidos (score < 2.2, conteúdo genérico)

    # ── ENERGIA GLOBAL — RENOVÁVEIS & STORAGE ───────────────────────────────
    {
        "label": "PV Magazine",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.pv-magazine.com/feed/",
    },
    {
        "label": "CleanTechnica",
        "tema": "Energia",
        "xtech": "CleanTech",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://cleantechnica.com/feed/",
    },
    {
        "label": "Renew Economy",
        "tema": "Global",
        "xtech": "CleanTech",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://reneweconomy.com.au/feed/",
    },
    {
        "label": "Energy Storage News",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "tecnologia",
        "peso": 3,
        "modo": "rss",
        "url": "https://www.energy-storage.news/feed/",
    },
    {
        "label": "IRENA",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:irena.org&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "label": "IEA",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "regulatorio",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:iea.org&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "label": "Renewables Now",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "global",
        "peso": 2,
        "modo": "rss",
        "url": "https://renewablesnow.com/news/feed/",
    },
    {
        "label": "Hydrogen Insight",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.hydrogeninsight.com/feed",
    },

    # ── THINK TANKS — ENERGIA ────────────────────────────────────────────────
    {
        "label": "Ember Energy",
        "tema": "Energia",
        "xtech": "EnergyTech",
        "tipo_fonte": "tecnologia",
        "peso": 3,
        "modo": "rss",
        "url": "https://ember-energy.org/feed/",
    },
    # FAPESP via Google News removido (score 1.79 — muito genérico; Agência FAPESP RSS direto mantido no bloco DeepTech)

    # ── MACROECONOMIA & FINANÇAS ─────────────────────────────────────────────
    {
        "label": "Financial Times",
        "tema": "Macroeconomia",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://www.ft.com/rss/home",
    },
    {
        "label": "XP Conteúdos",
        "tema": "Macroeconomia",
        "xtech": "FinTech",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:conteudos.xpi.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    # Exame removido (score 1.80 — generalista, não produz sinais relevantes para xTechs)

    # BBC Brasil (1.40) e DW Brasil (2.56) removidos — geopolítica genérica, zero tração em xTechs

    # ── IA & DATA CENTERS ────────────────────────────────────────────────────
    {
        "label": "Data Center Dynamics",
        "tema": "Data Centers & Infra",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:datacenterdynamics.com&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "label": "The Register DC",
        "tema": "Data Centers & Infra",
        "tipo_fonte": "global",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.theregister.com/data_centre/headlines.atom",
    },

    # ── INFRAESTRUTURA DIGITAL, TELECOM & TI (BRASIL) ──────────────────────
    {
        "label": "Teletime",
        "tema": "Telecom & Infra Digital",
        "tipo_fonte": "mercado",
        "peso": 3,
        "modo": "rss",
        "url": "https://teletime.com.br/feed/",
    },
    {
        "label": "Tele.Síntese",
        "tema": "Telecom & Infra Digital",
        "tipo_fonte": "mercado",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:telesintese.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "Convergência Digital",
        "tema": "Tecnologia & Governo",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:convergenciadigital.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "label": "Baguete",
        "tema": "TI Corporativa & IA",
        "tipo_fonte": "mercado",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.baguete.com.br/rss",
    },
    # MIT Tech Review BR (mittechreview.com.br) removido (score 2.50 — site BR com pouco conteúdo;
    # MIT Technology Review feed direto mantido no bloco DeepTech)

    # ── IA, DATA CENTERS & SEMICONDUTORES (GLOBAL) ───────────────────────────
    {
        "label": "Datacenter Knowledge",
        "tema": "Data Centers & Infra",
        "xtech": "DeepTech",
        "tipo_fonte": "tecnologia",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:datacenterknowledge.com&hl=en-US&gl=US&ceid=US:en",
    },
    # IEEE Spectrum via Google News removido (score 1.62 — conteúdo muito técnico/acadêmico, baixa relevância executiva)
    {
        "label": "Reuters Tech",
        "tema": "IA & Negócios Globais",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:reuters.com/technology&hl=en-US&gl=US&ceid=US:en",
    },
    # Ars Technica IT removido (score 3.25 — conteúdo EUA genérico, pouca aderência ao contexto BR/xTechs)
    {
        "label": "TechCrunch Enterprise",
        "tema": "Investimentos IA & Tech",
        "xtech": "DeepTech",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://techcrunch.com/category/enterprise/feed/",
    },

    # ── THINK TANKS GLOBAIS & CENÁRIOS PROSPECTIVOS ──────────────────────────
    {
        "label": "McKinsey Global Institute",
        "tema": "Deep Tech & Cenários",
        "xtech": "DeepTech",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:168h+site:mckinsey.com/mgi&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "label": "Copenhagen Institute for Futures Studies",
        "tema": "Deep Tech & Cenários",
        "xtech": "DeepTech",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:168h+site:cifs.dk&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "label": "RethinkX",
        "tema": "Deep Tech & Cenários",
        "xtech": "CleanTech",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:168h+site:rethinkx.com&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "label": "World Economic Forum",
        "tema": "Deep Tech & Cenários",
        "xtech": "DeepTech",
        "tipo_fonte": "global",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:168h+site:weforum.org/agenda&hl=en-US&gl=US&ceid=US:en",
    },

    # ── NOVAS FONTES: INOVAÇÃO TECNOLÓGICA & STARTUPS GLOBAIS ───────────────
    # Adicionadas para equilibrar viés regulatório — foco em tecnologia emergente
    {
        "label": "VentureBeat AI",
        "tema": "IA & Tendências",
        "xtech": "DeepTech",
        "tipo_fonte": "tecnologia",
        "peso": 3,
        "modo": "rss",
        "url": "https://venturebeat.com/category/ai/feed/",
        "limite": 10,
    },
    {
        "label": "TechCrunch Startups",
        "tema": "Startups & Inovação",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://techcrunch.com/category/startups/feed/",
        "limite": 8,
    },
    {
        "label": "Crunchbase News",
        "tema": "Startups & Inovação",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:news.crunchbase.com&hl=en-US&gl=US&ceid=US:en",
        "limite": 8,
    },
    {
        "label": "Abstartups",
        "tema": "Startups & Inovação",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:abstartups.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 6,
    },
    # Distrito Insights (1.50) e WIPO Technology Trends (1.50) removidos — Google News retorna resultados genéricos não aderentes
    {
        "label": "Axios Pro Rata",
        "tema": "Startups & Inovação",
        "tipo_fonte": "global",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.axios.com/feeds/feed.rss",
        "limite": 8,
    },
    {
        "label": "Hacker News Tech",
        "tema": "Deep Tech & Cenários",
        "xtech": "DeepTech",
        "tipo_fonte": "tecnologia",
        "peso": 2,
        "modo": "rss",
        "url": "https://hnrss.org/frontpage?points=100",
        "limite": 8,
    },

    # ════════════════════════════════════════════════════════════════════════
    # BLOCO ENERGYTECH — Segunda-feira
    # Startups e empresas de tecnologia para geração, distribuição, storage
    # e gestão de energia elétrica. Brasil = 64% das EnergyTechs da AL.
    # ════════════════════════════════════════════════════════════════════════

    {
        "label": "Distrito EnergyTech",
        "tema": "EnergyTech",
        "xtech": "EnergyTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+energytech+startups+energia+brasil+site:distrito.me&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    {
        "label": "Smart Energy International",
        "tema": "EnergyTech",
        "xtech": "EnergyTech",
        "xtech": "EnergyTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.smart-energy.com/feed/",
        "limite": 8,
    },
    {
        "label": "Energy Monitor",
        "tema": "EnergyTech",
        "xtech": "EnergyTech",
        "xtech": "EnergyTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.energymonitor.ai/feed/",
        "limite": 8,
    },
    {
        "label": "Wood Mackenzie Power",
        "tema": "EnergyTech",
        "xtech": "EnergyTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:woodmac.com+power+renewables&hl=en-US&gl=US&ceid=US:en",
        "limite": 6,
    },
    {
        "label": "S&P Global Energy",
        "tema": "EnergyTech",
        "xtech": "EnergyTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:spglobal.com+energy+renewables&hl=en-US&gl=US&ceid=US:en",
        "limite": 6,
    },

    # ════════════════════════════════════════════════════════════════════════
    # BLOCO CLEANTECH — Terça-feira
    # Descarbonização, mercados de carbono, sustentabilidade corporativa,
    # materiais avançados, economia circular, água e compliance ESG.
    # ════════════════════════════════════════════════════════════════════════

    {
        "label": "GreenBiz",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://www.greenbiz.com/feeds/all.rss",
        "limite": 10,
    },
    {
        "label": "Carbon Brief",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://www.carbonbrief.org/feed/",
        "limite": 8,
    },
    {
        "label": "Climate Home News",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.climatechangenews.com/feed/",
        "limite": 8,
    },
    {
        "label": "Canary Media",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.canarymedia.com/rss.xml",
        "limite": 8,
    },
    {
        "label": "Recharge News",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.rechargenews.com/rss",
        "limite": 8,
    },
    {
        "label": "Mercado de Carbono BR",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+mercado+carbono+brasil+REDD+RenovaBio&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    {
        "label": "ESG Hoje",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:esghoje.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    {
        "label": "Conexão Planeta",
        "tema": "CleanTech",
        "xtech": "CleanTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:conexaoplaneta.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 6,
    },

    # ════════════════════════════════════════════════════════════════════════
    # BLOCO FINTECH — Quarta-feira
    # Pagamentos, crédito, open finance, InsurTech, crypto, infraestrutura
    # bancária. Brasil = maior mercado fintech da AL (21+ unicórnios).
    # ════════════════════════════════════════════════════════════════════════

    {
        "label": "Finsiders Brasil",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://finsidersbrasil.com.br/feed/",
        "limite": 10,
    },
    {
        "label": "Fintechnews Brazil",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+fintech+brasil+OR+brazil+startup+pagamentos&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    {
        "label": "The Fintech Times",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://thefintechtimes.com/feed/",
        "limite": 10,
    },
    {
        "label": "Fintech Magazine",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://fintechmagazine.com/rss.xml",
        "limite": 8,
    },
    {
        "label": "Fintech Americas",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://fintechnews.am/feed/",
        "limite": 8,
    },
    {
        "label": "Fintech Nexus",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:fintechnexus.com&hl=en-US&gl=US&ceid=US:en",
        "limite": 8,
    },
    {
        "label": "PaymentsSource",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:paymentssource.com&hl=en-US&gl=US&ceid=US:en",
        "limite": 8,
    },
    {
        "label": "CB Insights Fintech",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:cbinsights.com+fintech&hl=en-US&gl=US&ceid=US:en",
        "limite": 6,
    },
    {
        "label": "Banco Central Feed",
        "tema": "FinTech",
        "xtech": "FinTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:bcb.gov.br+pix+OR+open+finance+OR+fintech&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 6,
    },

    # ════════════════════════════════════════════════════════════════════════
    # BLOCO DEEPTECH — Quinta-feira
    # IA avançada, computação quântica, biotecnologia, semicondutores,
    # robótica, materiais avançados, espaço. Ciclo longo, disrupção transversal.
    # ════════════════════════════════════════════════════════════════════════

    {
        "label": "Sifted DeepTech",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://sifted.eu/sector/deeptech/feed",
        "limite": 8,
    },
    {
        "label": "TNW DeepTech",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://thenextweb.com/deep-tech/feed/",
        "limite": 8,
    },
    {
        "label": "Hello Tomorrow",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://hello-tomorrow.org/feed/",
        "limite": 6,
    },
    {
        "label": "MIT Technology Review",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://www.technologyreview.com/feed/",
        "limite": 10,
    },
    # Nature News removido (score 2.83 — conteúdo científico puro sem aderência à agenda executiva xTechs)
    {
        "label": "Fierce Biotech",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.fiercebiotech.com/rss/xml",
        "limite": 8,
    },
    {
        "label": "Inovação Tecnológica BR",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.inovacaotecnologica.com.br/rss/rss.php",
        "limite": 8,
    },
    {
        "label": "Agência FAPESP",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://agencia.fapesp.br/rss/noticias.rss",
        "limite": 8,
    },
    {
        "label": "Embrapii",
        "tema": "DeepTech",
        "xtech": "DeepTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:embrapii.org.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 6,
    },

    # ════════════════════════════════════════════════════════════════════════
    # BLOCO AGROTECH — Sexta-feira
    # Agricultura de precisão, drones, IoT rural, rastreabilidade, biotech
    # agrícola, fintech rural. Brasil = 2º maior adotante global (~2.000 startups).
    # ════════════════════════════════════════════════════════════════════════

    {
        "label": "CanalRural Tech",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:canalrural.com.br+tecnologia+OR+startup+OR+agtech&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    {
        "label": "AgroTalento",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:agrotalento.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 8,
    },
    # Globo Rural Tech removido (score 3.00 — conteúdo rural geral sem especificidade em agritech)
    {
        "label": "BrasilAgro",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:48h+site:brasilagro.com.br&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "limite": 10,
    },
    {
        "label": "Brazil AgTech Report",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://brazilagtech.substack.com/feed",
        "limite": 8,
    },
    # Embrapa Inovação via Google News removido (score 1.43 — retorna publicações acadêmicas sem tração comercial)
    {
        "label": "AgFunder News",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://agfunder.com/research/feed/",
        "limite": 8,
    },
    {
        "label": "AgriTech Tomorrow",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://www.agritechtomorrow.com/rss/",
        "limite": 8,
    },
    {
        "label": "iGrow Network",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 2,
        "modo": "rss",
        "url": "https://igrownews.com/feed/",
        "limite": 8,
    },
    {
        "label": "World Agri-Tech South America",
        "tema": "AgroTech",
        "xtech": "AgroTech",
        "peso": 3,
        "modo": "rss",
        "url": "https://news.google.com/rss/search?q=when:72h+site:worldagritechsouthamerica.com&hl=en-US&gl=US&ceid=US:en",
        "limite": 6,
    },
]

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(hashes):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(hashes), f)

def make_hash(titulo, link=""):
    return hashlib.md5(f"{titulo.lower().strip()}{link}".encode()).hexdigest()

def url_absoluta(href, base_url):
    if not href:
        return None
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        p = urlparse(base_url)
        return f"{p.scheme}://{p.netloc}{href}"
    return None

def montar_item(titulo, link, fonte, tema, peso, descricao="", data_publicacao=None, tipo_fonte="mercado", xtech=None):
    item = {
        "hash":        make_hash(titulo, link),
        "titulo":      titulo.strip(),
        "link":        link,
        "descricao":   descricao[:500],
        "fonte":       fonte,
        "tema":        tema,
        "peso":        peso,
        "tipo_fonte":  tipo_fonte,
        "data":        data_publicacao or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "coletado_em": datetime.now(timezone.utc).isoformat(),
    }
    if xtech:
        item["xtech"] = xtech
    return item


# ─────────────────────────────────────────────
# COLETA VIA RSS
# ─────────────────────────────────────────────

_LIMITE_DIAS_RSS = 14

def _extrair_data_rss(entry):
    for campo in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = getattr(entry, campo, None)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d"), (datetime.now(timezone.utc) - dt).days
            except Exception:
                pass
    return None, 0

def coletar_rss(source, seen):
    itens = []
    limite = source.get("limite", 10)
    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20, verify=False)
        feed = feedparser.parse(resp.content)

        for entry in feed.entries[:15]:
            titulo = entry.get("title", "").strip()
            link   = entry.get("link", "").strip()

            if " - " in titulo:
                titulo = titulo.rsplit(" - ", 1)[0]

            if not titulo or not link or len(titulo) < 15:
                continue

            data_pub, dias_atras = _extrair_data_rss(entry)
            if data_pub and dias_atras > _LIMITE_DIAS_RSS:
                continue

            h = make_hash(titulo, link)
            if h in seen:
                continue

            seen.add(h)
            descricao = entry.get("summary", entry.get("description", ""))
            itens.append(montar_item(
                titulo, link, source["label"], source["tema"], source["peso"],
                descricao, data_publicacao=data_pub,
                tipo_fonte=source.get("tipo_fonte", "tecnologia" if source.get("xtech") else "mercado"),
                xtech=source.get("xtech"),
            ))

            if len(itens) >= limite:
                break

    except Exception as e:
        print(f"    ✗ RSS erro em {source['label']}: {e}")

    return itens


# ─────────────────────────────────────────────
# COLETA VIA SCRAPING
# ─────────────────────────────────────────────

def coletar_scraping(source, seen):
    itens = []
    try:
        time.sleep(random.uniform(3.5, 7.2))
        session = requests.Session()
        resp = session.get(source["url"], headers=HEADERS, timeout=20, verify=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        tags = soup.select(source.get("seletor", "h2 a, h3 a, .title a, .entry-title a"))
        vistos_local = set()

        for tag in tags[:25]:
            titulo = tag.get_text(strip=True)
            href   = tag.get("href", "")
            link   = url_absoluta(href, source["url"])

            if not titulo or len(titulo) < 20 or not link:
                continue

            h = make_hash(titulo, link)
            if h in seen or h in vistos_local:
                continue

            vistos_local.add(h)
            seen.add(h)
            itens.append(montar_item(
                titulo, link, source["label"], source["tema"], source["peso"],
                tipo_fonte=source.get("tipo_fonte", "tecnologia" if source.get("xtech") else "mercado"),
                xtech=source.get("xtech"),
            ))

            if len(itens) >= 8:
                break

    except requests.exceptions.RequestException as e:
        print(f"    ✗ Scraping erro em {source['label']}: {e}")
    except Exception as e:
        print(f"    ✗ Erro inesperado em {source['label']}: {e}")

    return itens


# ─────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────

def coletar():
    seen  = load_seen()
    todos = []

    rss_count      = sum(1 for s in SOURCES if s["modo"] == "rss")
    scraping_count = sum(1 for s in SOURCES if s["modo"] == "scraping")

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Iniciando coleta — {len(SOURCES)} fontes...")
    print(f"  RSS: {rss_count} fontes  |  Scraping: {scraping_count} fontes\n")

    for source in SOURCES:
        modo = source["modo"]
        print(f"  → [{modo.upper():8s}] {source['label']}")

        if modo == "rss":
            itens = coletar_rss(source, seen)
        else:
            itens = coletar_scraping(source, seen)
            time.sleep(1)

        print(f"    {len(itens)} itens novos")
        todos.extend(itens)

    vistos = set()
    unicos = []
    for item in todos:
        h = item.get("hash", make_hash(item["titulo"]))
        if h not in vistos:
            vistos.add(h)
            unicos.append(item)

    # ── Cap regulatório: máx CAP_REGULATORIO do total por ciclo ─────────────
    regulatorios = [i for i in unicos if i.get("tipo_fonte") == "regulatorio"]
    outros       = [i for i in unicos if i.get("tipo_fonte") != "regulatorio"]
    cap_max      = max(int(len(unicos) * CAP_REGULATORIO), 10)  # mínimo 10
    if len(regulatorios) > cap_max:
        # Preserva os de maior peso primeiro
        regulatorios.sort(key=lambda x: x.get("peso", 1), reverse=True)
        cortados = len(regulatorios) - cap_max
        regulatorios = regulatorios[:cap_max]
        print(f"\n  ⚠ Cap regulatório: {cortados} sinais removidos "
              f"(mantidos {cap_max}/{cap_max + cortados} — {CAP_REGULATORIO*100:.0f}% do total)")
    unicos = outros + regulatorios

    save_seen(seen)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(unicos, f, ensure_ascii=False, indent=2)

    temas = {}
    tipos = {}
    for item in unicos:
        t = item.get("tema", "?")
        tp = item.get("tipo_fonte", "?")
        temas[t]  = temas.get(t, 0) + 1
        tipos[tp] = tipos.get(tp, 0) + 1

    pct_reg = tipos.get("regulatorio", 0) / max(len(unicos), 1) * 100

    print(f"\n✓ Coleta concluída:")
    print(f"  Total coletado:  {len(todos)}")
    print(f"  Após dedup:      {len(unicos)}")
    print(f"  Salvo em:        {OUTPUT_FILE}")
    print(f"\n  Por tipo_fonte:")
    for tp, count in sorted(tipos.items(), key=lambda x: -x[1]):
        print(f"    {tp:<15} {count:>4} itens")
    print(f"  Regulatório:     {pct_reg:.1f}% do total (cap={CAP_REGULATORIO*100:.0f}%)")
    print(f"\n  Por tema:")
    for tema, count in sorted(temas.items(), key=lambda x: -x[1]):
        print(f"    {tema:<35} {count} itens")
    print()

    return unicos

if __name__ == "__main__":
    inicio = datetime.now()
    coletar()
    elapsed = (datetime.now() - inicio).total_seconds()
    print(f"✓ Coleta finalizada em {elapsed:.2f}s")