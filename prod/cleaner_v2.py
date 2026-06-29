#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleaner_v2.py — Filtro de Ruído Estratégico
Higieniza feed_bruto.json antes de passar ao analyzer.

v2 (sobre v1):
  WHITELIST ampliada com termos das cinco categorias xTech:
    EnergyTech, CleanTech, FinTech, DeepTech, AgroTech.
  BLACKLIST: ajuste do termo "surto de" para evitar falso-positivo
    em notícias legítimas de AgroTech (ex: surto de praga agrícola).
    Substituído por lista específica de surtos de saúde pública.
  FONTES_COM_FILTRO_ESTRITO: adicionadas CanalRural Tech e GreenBiz,
    fontes de escopo amplo incorporadas no collector_v6.py.

Camadas de filtragem (em ordem de aplicação):
  1. Whitelist de salvaguarda — sinais estratégicos explícitos passam sem análise adicional
  2. Deduplicação — remove títulos duplicados por hash MD5
  3. Blacklist semântica — ruído administrativo / educacional / saúde / cerimonial
  4. Filtros por fonte — sub-triagem obrigatória para fontes estatísticas amplas (IBGE, etc.)

Uso:
  python cleaner_v2.py                    lê feed_bruto.json, salva feed_limpo.json
"""

import hashlib
import json
import os
import re

INPUT_FILE  = "feed_bruto.json"
OUTPUT_FILE = "feed_limpo.json"


# ─────────────────────────────────────────────────────────────────────────────
# WHITELIST — termos que forçam a passagem de qualquer item
# (prioridade absoluta sobre blacklist e filtros de fonte)
# ─────────────────────────────────────────────────────────────────────────────
WHITELIST: list[str] = [
    # ── Energia & Transição Energética ───────────────────────────────────────
    "energia solar", "energia eólica", "energia renovável", "biomassa",
    "hidrelétrica", "termelétrica", "leilão de energia", "leilão aneel",
    "ppa ", " ppa", "bess", "armazenamento de energia", "smart grid",
    "microrredes", "transmissão de energia", "setor elétrico",
    "mercado livre de energia", "mercado de energia", "tarifas de energia",
    "aneel", "ccee", "ons ", " mme", "transição energética",
    "descarbonização", "hidrogênio verde", "biocombustível",
    "eólica offshore", "solar fotovoltaico",

    # ── EnergyTech (Segunda) ─────────────────────────────────────────────────
    "energytech", "energy tech", "energy startup",
    "virtual power plant", "planta virtual de energia",
    "distributed energy", "energia distribuída",
    "smart meter", "medidor inteligente", "advanced metering",
    "demand response", "gestão de demanda energética",
    "vehicle-to-grid", "v2g", "vehicle to grid",
    "microgrid", "power-to-x", "power to x",
    "energy management system", "sistema de gestão de energia",

    # ── CleanTech (Terça) ────────────────────────────────────────────────────
    "cleantech", "clean tech", "clean technology",
    "mercado de carbono", "crédito de carbono", "carbon credit",
    "renovabio", "cbios", "redd", "redd+",
    "economia circular", "circular economy",
    " esg", "esg ", "relatório de sustentabilidade",
    "net zero", "neutralidade de carbono", "carbon neutral",
    "emissão de co2", "emissões de gases", "scope 1", "scope 2", "scope 3",
    "green bond", "título verde", "debênture verde",
    "rastreabilidade de carbono", "inventário de emissões",
    "biomateriais", "bioplástico", "economia de baixo carbono",
    "eficiência hídrica", "reuso de água", "tratamento de efluentes",

    # ── FinTech (Quarta) ─────────────────────────────────────────────────────
    "fintech", "fintechs",
    "open finance", "open banking", "finance aberto",
    "pix ", " pix", "sistema de pagamento",
    "embedded finance", "banking as a service", "baas",
    "insurtech", "seguro digital", "seguro paramétrico",
    "banco digital", "neobank", "conta digital",
    "crédito digital", "crédito alternativo", "buy now pay later", "bnpl",
    "pagamentos instantâneos", "instant payment",
    "criptoativo", "criptomoeda", "stablecoin",
    "cbdc", "real digital", "moeda digital de banco central",
    "tokenização de ativos", "defi", "finanças descentralizadas",
    "kyc digital", "onboarding digital", "anti-fraude",
    "regulação fintech", "sandbox regulatório",
    "receita recorrente financeira", "gestão financeira digital",

    # ── DeepTech (Quinta) ────────────────────────────────────────────────────
    "deeptech", "deep tech", "deep technology",
    "computação quântica", "quantum computing", "quantum",
    "biotecnologia", "biotech", "terapia gênica", "edição genética", "crispr",
    "nanotecnologia", "nanomaterial", "nanoeletrônica",
    "materiais avançados", "advanced materials", "metamaterial",
    "semicondutor", "chip de ia", "processador neuromorphic",
    "robótica avançada", "exoesqueleto", "robô cirúrgico",
    "computação neuromórfica", "brain-computer interface",
    "fotônica", "laser de alta potência",
    "fusão nuclear", "fissão avançada", "small modular reactor", "smr",
    "tecnologia espacial", "satélite de órbita baixa", "leo satellite",
    "impressão 3d avançada", "manufatura aditiva",
    "digital twin", "gêmeo digital",

    # ── AgroTech (Sexta) ─────────────────────────────────────────────────────
    "agtech", "agrotech", "agro tech",
    "agricultura de precisão", "precision agriculture",
    "drone agrícola", "pulverização por drone", "mapeamento aéreo",
    "sensor de solo", "monitoramento de lavoura", "iot rural",
    "rastreabilidade de alimentos", "food traceability",
    "biotech agrícola", "bioinsumo", "biodefensivo", "biofertilizante",
    "agronegócio digital", "digitalização do campo",
    "crédito rural digital", "fintech rural", "agrifintech",
    "seguro agrícola paramétrico", "seguro rural",
    "previsão de safra", "yield prediction",
    "irrigação inteligente", "smart irrigation",
    "blockchain agro", "certificação de origem",
    "proteína alternativa", "cultured meat", "carne cultivada",
    "vertical farming", "agricultura vertical", "indoor farming",
    "embrapa", "agrofoodtech",

    # ── IA, Tecnologia & Infraestrutura Digital ──────────────────────────────
    "inteligência artificial", "machine learning", "deep learning",
    "large language model", "llm", "computação em nuvem", "cloud computing",
    "data center", "datacenter", "fabricação de chips",
    "processador", "gpu", "automação industrial", "robótica",
    "internet das coisas", "iot", "5g", "6g", "fibra óptica",
    "cibersegurança", "infraestrutura digital", "telecomunicações",

    # ── Macroeconomia Estratégica ─────────────────────────────────────────────
    "contas externas", "déficit externo", "superávit", "balança comercial",
    "exportação de commodities", "exportação de tecnologia",
    "importação de equipamentos", "ipca", "selic", "pib",
    "crescimento econômico", "investimento estrangeiro direto",
    "desinvestimento", "ipo", "fusão e aquisição",

    # ── P&D, Inovação & Financiamento ────────────────────────────────────────
    "pesquisa e desenvolvimento", "p&d", "pintec", "inovação",
    "startup", "venture capital", "fundo de investimento",
    "financiamento de projetos", "debênture", "bndes",

    # ── Infraestrutura Crítica & Regulação ───────────────────────────────────
    "regulação", "marco regulatório", "legislação setorial",
    "projeto de lei", "medida provisória", "resolução normativa",
    "infraestrutura crítica", "porto", "ferrovia", "rodovia federal",

    # ── Geopolítica Estratégica ───────────────────────────────────────────────
    "geopolítica", "sanção comercial", "tarifas de importação",
    "guerra comercial", "opep", "petróleo", "gás natural", "gnl", "refinaria",
    "reshoring", "nearshoring", "cadeia de suprimentos",
]


# ─────────────────────────────────────────────────────────────────────────────
# BLACKLIST — termos que indicam ruído sem valor estratégico para o think tank
# ─────────────────────────────────────────────────────────────────────────────
BLACKLIST: list[str] = [
    # ── Saúde pública / epidemiologia ────────────────────────────────────────
    "pense 2024", "pense 2025", "pense 2026",
    "pesquisa nacional de saúde do escolar",
    "comportamento sexual", "iniciação sexual",
    "gravidez na adolescência", "saúde do adolescente",
    "tabaco entre adolescentes", "uso de drogas entre",
    "obesidade infantil", "vacinação infantil", "mortalidade infantil",
    "epidemiologia da",
    # "surto de" removido em v2: falso-positivo em AgroTech
    # (ex: "surto de praga agrícola", "surto de ferrugem da soja")
    # Substituído por termos específicos de surtos de saúde pública:
    "surto de dengue", "surto de zika", "surto de chikungunya",
    "surto de botulismo", "surto de sarampo", "surto de meningite",
    "surto de covid", "surto de influenza", "surto de hepatite",
    "dengue", "zika", "chikungunya",
    "saúde bucal",
    # ── Educação não-técnica ─────────────────────────────────────────────────
    "para onde vai o nosso lixo", "onde vai o lixo",
    "ensino médio integrado", "ensino fundamental",
    "educação básica", "merenda escolar",
    "enem ", "vestibular", "alfabetização",
    "sala de aula", "escola pública",
    "rede de ensino", "material didático",
    # ── Burocracia / Recursos Humanos institucional ───────────────────────────
    "nomeação de servidor", "concurso público de",
    "portaria de pessoal", "ressarcimento de despesas",
    "diárias e passagens", "pregão eletrônico de",
    "licitação de serviço de limpeza", "licitação de vigilância",
    "certame", "edital de licitação de serviços gerais",
    # ── Comemorativo / efeméride ─────────────────────────────────────────────
    " 90 anos", " 100 anos", " 50 anos", " 75 anos",
    "aniversário de fundação", "aniversário de criação",
    "semana comemorativa", "dia nacional de",
    "especial comemorativo", "celebração do",
    # ── Eventos passados / transmissões ao vivo encerradas ────────────────────
    "acompanhe nesta segunda-feira",
    "acompanhe nesta terça-feira",
    "acompanhe nesta quarta-feira",
    "acompanhe nesta quinta-feira",
    "acompanhe nesta sexta-feira",
    "abertura desta segunda", "abertura desta terça",
    "cerimônia de abertura do evento",
    "transmissão ao vivo",
    # ── Conteúdo institucional interno / FAQ ─────────────────────────────────
    "como funciona o ibge", "você sabia que o ibge",
    "o que é o ibge", "história do ibge",
    "expediente", "feriado nacional",
    "nota de pesar", "falecimento de",
]


# ─────────────────────────────────────────────────────────────────────────────
# FONTES COM FILTRO ESTRITO
# Fontes de escopo amplo que só passam se o texto contiver ao menos 1 termo obrigatório
# ─────────────────────────────────────────────────────────────────────────────
FONTES_COM_FILTRO_ESTRITO: dict[str, list[str]] = {
    "IBGE": [
        "pintec", "inovação", "contas externas", "déficit", "superávit",
        "balança comercial", "pib", "ipca", "exportação", "importação",
        "indústria", "produção industrial", "investimento",
    ],
    "Agência Brasil": [
        "energia", "tecnologia", "infraestrutura", "regulação", "aneel",
        "petróleo", "gás", "data center", "inteligência artificial",
        "startup", "inovação", "5g", "telecom", "investimento",
        "bndes", "exportação", "câmbio", "selic",
    ],
    "Portal Gov BR": [
        "energia", "regulação", "tecnologia", "infraestrutura",
        "inovação", "investimento", "indústria",
    ],
    # ── Novas fontes amplas incorporadas no collector_v6 ─────────────────────
    "CanalRural Tech": [
        "tecnologia", "startup", "agtech", "agrotech", "drone", "precisão",
        "rastreabilidade", "digitalização", "iot", "sensor", "bioinsumo",
        "biodefensivo", "irrigação inteligente", "monitoramento",
        "agricultura de precisão", "crédito rural digital",
    ],
    "Globo Rural Tech": [
        "tecnologia", "inovação", "startup", "agtech", "drone", "sensor",
        "precisão", "digitalização", "rastreabilidade", "bioinsumo",
    ],
    "GreenBiz": [
        "cleantech", "carbono", "energia", "esg", "descarbonização",
        "startup", "investimento", "tecnologia", "net zero", "economia circular",
        "green bond", "emissão", "sustentabilidade corporativa",
    ],
    "Conexão Planeta": [
        "cleantech", "carbono", "startup", "tecnologia", "economia circular",
        "energia renovável", "sustentabilidade", "inovação",
    ],
    "Climate Home News": [
        "energia", "carbono", "emissão", "renovável", "política climática",
        "net zero", "cop", "financiamento climático", "adaptação climática",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Funções auxiliares
# ─────────────────────────────────────────────────────────────────────────────

_PREFIXOS_UI = re.compile(
    r'^(Ver mais|Leia mais|Saiba mais|Minuto\w*|Ver\s+mais|Negócios|Mercado|'
    r'Política|Opinião|Análise|Edital|Pesquisa e desenvolvimento|Biocombustíveis|'
    r'Eletricidade|Anistia a geradores|Revogam)\s*',
    re.IGNORECASE,
)

def _normalizar_titulo(titulo: str) -> str:
    """Remove prefixos de UI/navegação colados ao título pelo scraper."""
    titulo = titulo.strip()
    # Remove prefixos de navegação repetidos (ex: "Ver maisNegóciosTítulo real")
    for _ in range(4):
        novo = _PREFIXOS_UI.sub("", titulo).strip()
        if novo == titulo:
            break
        titulo = novo
    return titulo


def _hash_titulo(titulo: str) -> str:
    return hashlib.md5(titulo.lower().strip().encode()).hexdigest()


def _contem_whitelist(texto: str) -> bool:
    t = texto.lower()
    return any(term in t for term in WHITELIST)


def _contem_blacklist(texto: str) -> bool:
    t = texto.lower()
    return any(term in t for term in BLACKLIST)


def _passou_filtro_de_fonte(item: dict) -> bool:
    """
    Retorna False se a fonte exigir filtro estrito E nenhum termo obrigatório
    estiver no texto do item.
    """
    fonte = item.get("fonte", "").strip()
    if fonte not in FONTES_COM_FILTRO_ESTRITO:
        return True
    texto = (item.get("titulo", "") + " " + item.get("descricao", "")).lower()
    termos = FONTES_COM_FILTRO_ESTRITO[fonte]
    return any(t in texto for t in termos)


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

def limpar() -> None:
    if not os.path.exists(INPUT_FILE):
        print(f"  [!] {INPUT_FILE} não encontrado.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_antes = len(data)
    limpo: list[dict] = []
    hashes_vistos: set[str] = set()

    stats = {
        "whitelist":      0,
        "duplicata":      0,
        "blacklist":      0,
        "fonte_estrita":  0,
        "passaram":       0,
    }

    for item in data:
        titulo    = _normalizar_titulo(item.get("titulo", ""))
        item["titulo"] = titulo  # grava título limpo de volta no item
        descricao = item.get("descricao", item.get("conteudo", ""))
        texto_completo = f"{titulo} {descricao}"

        # ── 1. Whitelist: sinal estratégico explícito → passa imediatamente ──
        if _contem_whitelist(texto_completo):
            h = _hash_titulo(titulo)
            if h in hashes_vistos:
                stats["duplicata"] += 1
                continue
            hashes_vistos.add(h)
            limpo.append(item)
            stats["whitelist"] += 1
            stats["passaram"]  += 1
            continue

        # ── 2. Deduplicação por título ─────────────────────────────────────
        h = _hash_titulo(titulo)
        if h in hashes_vistos:
            stats["duplicata"] += 1
            continue
        hashes_vistos.add(h)

        # ── 3. Blacklist semântica ──────────────────────────────────────────
        if _contem_blacklist(texto_completo):
            stats["blacklist"] += 1
            continue

        # ── 4. Filtro por fonte ─────────────────────────────────────────────
        if not _passou_filtro_de_fonte(item):
            stats["fonte_estrita"] += 1
            continue

        limpo.append(item)
        stats["passaram"] += 1

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(limpo, f, ensure_ascii=False, indent=2)

    taxa = round(100 * (total_antes - stats["passaram"]) / max(total_antes, 1))
    print(f"  ✓ Limpeza concluída.")
    print(f"    Feed bruto:            {total_antes:>4} sinais")
    print(f"    Passou (whitelist):    {stats['whitelist']:>4}")
    print(f"    Removido (blacklist):  {stats['blacklist']:>4}")
    print(f"    Removido (fonte):      {stats['fonte_estrita']:>4}")
    print(f"    Removido (dupl.):      {stats['duplicata']:>4}")
    print(f"    ─────────────────────────────────────")
    print(f"    Feed limpo:            {stats['passaram']:>4} sinais → {OUTPUT_FILE}")
    print(f"    Taxa de filtragem:     {taxa}%")


if __name__ == "__main__":
    limpar()