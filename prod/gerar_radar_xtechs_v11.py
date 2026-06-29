#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gerar_radar_xtechs_v11.py
Radar xTechs — Painel de Inteligência das 5 Frentes Tecnológicas · efagundes.com

Evolução v11 (sobre v10)
------------------------
  + render_hero(): gera hero-{ciclo}.html — bloco HTML autocontido com inline CSS,
    tema escuro, para embed na homepage de efagundes.com e nMentors.com.br.
    Conteúdo: xTech de maior momentum (headline), top 3 sinais da semana, CTA → Radar.

Uso:
  python gerar_radar_xtechs_v11.py --input intel_output.json [--db intel.db]
  python gerar_radar_xtechs_v11.py --input intel_output.json --output radar-xtechs.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

BRASILIA = timezone(timedelta(hours=-3))
DEFAULT_INPUT = str(Path(__file__).parent / "intel_output.json")
DEFAULT_OUTPUT_DIR = str(Path(__file__).parent.parent / "outputs" / "radar")

# ─── Paleta ───────────────────────────────────────────────────────────────────
C_BG = "#0F1722"
C_BG2 = "#131D2B"
C_PANEL = "#151F2E"
C_PANEL2 = "#1B2A3D"
C_LINE = "rgba(255,255,255,.06)"
C_LINE2 = "rgba(255,255,255,.10)"
C_TEXT = "#E6EDF3"
C_MUTED = "#A8B3C2"
C_WEAK = "#718096"
C_TERRA = "#5B8CFF"
C_TERRA_LIGHT = "#79A3FF"
C_DANGER = "#D96C6C"
C_AMBER = "#D9A441"
C_GREEN = "#2FA87C"
C_TECH = "#5B8CFF"
C_PURPLE = "#9B7BC4"

TYPE_COLORS = {
    "Risco Regulatório": C_DANGER,
    "Oportunidade de Mercado": C_GREEN,
    "Choque Geopolítico": C_AMBER,
    "Sinal Tecnológico": C_TECH,
    "Misto": C_MUTED,
}
QUAD_COLORS = {
    "Mobilizar Agora": C_DANGER,
    "Capturar Vantagem": C_GREEN,
    "Monitorar Vetores": C_TECH,
    "Ruído Operacional": C_WEAK,
}
MAT_KEYS = ["capex", "opex", "regulatorio", "competitividade", "reputacional"]
MAT_LABELS = {
    "capex": "CAPEX",
    "opex": "OPEX",
    "regulatorio": "Regulatório",
    "competitividade": "Competitividade",
    "reputacional": "Reputacional",
}
MAT_SCORE = {"Crítica": 4, "Critica": 4, "Alta": 3, "Média": 2, "Media": 2, "Baixa": 1, "": 0, None: 0}

# ─── Mapeamento de temas para frentes ─────────────────────────────────────────
FRENTE_THEMES = {
    "EnergyTech": "('Energia','EnergyTech','Energia & Eficiência Energética')",
    "CleanTech": "('CleanTech')",
    "AgriTech": "('AgroTech')",
    "DeepTech": "('IA & Automação','DeepTech','Deep Tech & Semicondutores','Data Centers & Infraestrutura','Data Centers & Infra')",
    "FinTech": "('FinTech','Financiamento & Inovação','Modelos de Negócio & Startups')",
}

FRENTE_COLORS = {
    "EnergyTech": "#2FA87C",
    "CleanTech": "#5DCAA5",
    "AgriTech": "#7AB648",
    "DeepTech": "#5B8CFF",
    "FinTech": "#D9A441",
}


HYPE_TECHS = [
    {"id": "Geotermia",            "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.04, "default_n": 2,   "score_default": 8.5,  "trend": "→",  "phase": "Sinal Emergente",
     "signal": "<strong>Fase: Sinal Emergente.</strong> Apenas 2 sinais monitorados, mas score 8.5 — o mais alto dessa fase. "
               "A geotermia desperta interesse científico global como fonte de geração contínua (baseload) sem emissões, porém no Brasil "
               "a ausência de mapeamento geológico detalhado e de quadro regulatório específico mantém o tema no estágio de pesquisa. "
               "Sem leilão, sem investimento, sem roadmap. Catalisador esperado: política de diversificação de matriz pós-2030."},
    {"id": "AgriTech Digital",     "frente": "AgriTech",    "color": "#7AB648", "cx": 0.07, "default_n": 5,   "score_default": 3.3,  "trend": "↗",  "phase": "Sinal Emergente",
     "signal": "<strong>Fase: Sinal Emergente (acelerando ↗).</strong> 5 sinais registrados desde W23, score baixo (3.3) reflete "
               "cobertura ainda exploratória. A pressão de custo de capital no agronegócio brasileiro — crédito rural mais caro, "
               "margem comprimida por câmbio — está criando demanda por precisão digital: sensores IoT, drones de pulverização, "
               "modelagem preditiva de produtividade. Tendência de aceleração: startups agritech captando rodadas semente em 2026."},
    {"id": "CCS / Carbon Capture", "frente": "CleanTech", "color": "#5DCAA5", "cx": 0.10, "default_n": 1,   "score_default": 9.0,  "trend": "→",  "phase": "Sinal Emergente",
     "signal": "<strong>Fase: Sinal Emergente.</strong> 1 sinal com score excepcional 9.0 — remoção de carbono via mineralização "
               "acelerada e captura direta do ar (DAC). Tecnologia validada em escala piloto no exterior, sem tração no Brasil. "
               "Obstáculo estrutural: inexistência de mercado de carbono regulado e precificação de carbono que viabilize o CAPEX. "
               "Janela: se o Brasil antecipar a agenda europeia de carbono — CBAM (fase definitiva 2026), CORSIA, mandatos de combustíveis renováveis e rastreabilidade de cadeias — "
               "esse gatilho muda de fase rapidamente. Exigência de certificação sobe para biocombustíveis, siderurgia e cadeias de baixo carbono."},
    {"id": "Smart Grid",           "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.14, "default_n": 2,   "score_default": 8.0,  "trend": "↗",  "phase": "Narrativa Exponencial",
     "signal": "<strong>Fase: Narrativa Exponencial (acelerando ↗).</strong> Em junho/2026, o ONS criou formalmente uma área dedicada "
               "ao desenvolvimento de ferramentas eletroenergéticas — sinal direto de que a operação da rede precisa de inteligência "
               "digital. O tema permeia 200+ sinais de transmissão sem ser nomeado como 'smart grid'. "
               "A regulação de medição inteligente (AMI) pela ANEEL é o próximo catalisador esperado para mover esse ponto no gráfico."},
    {"id": "Blockchain Energia",   "frente": "FinTech",     "color": "#D9A441", "cx": 0.17, "default_n": 3,   "score_default": 9.0,  "trend": "↗",  "phase": "Narrativa Exponencial",
     "signal": "<strong>Fase: Narrativa Exponencial (acelerando ↗).</strong> 3 sinais com score médio 9.0 — tokenização de créditos de "
               "carbono, certificados de energia renovável (REC) e contratos bilaterais de energia em blockchain. "
               "A ausência de regulação da CVM para tokens de utilidade energética é o gargalo principal. "
               "Movimento esperado: se a CVM avançar com sandbox regulatório para ativos tokenizados em 2026-27, "
               "esse tema escala rapidamente para o Pico de Especulação."},
    {"id": "Lítio & Mineração",    "frente": "CleanTech", "color": "#5DCAA5", "cx": 0.21, "default_n": 20,  "score_default": 7.44, "trend": "↗",  "phase": "Narrativa Exponencial",
     "signal": "<strong>Fase: Narrativa Exponencial (acelerando ↗).</strong> 20 sinais, score 7.44 em alta. "
               "O MME realizou intercâmbio técnico internacional sobre cadeia de valor do lítio (abr/2026), sinalizando "
               "que o governo reconhece o ativo estratégico. O Brasil detém a 5ª maior reserva mundial de lítio "
               "mas ainda extrai < 1% da capacidade. Projetos de beneficiamento em MG e BA em fase de licenciamento. "
               "O movimento global de nearshoring de baterias pode criar demanda por lítio brasileiro antes de 2028."},
    {"id": "Satélites LEO",        "frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.26, "default_n": 34,  "score_default": 5.4,  "trend": "↗",  "phase": "Narrativa Exponencial",
     "signal": "<strong>Fase: Narrativa Exponencial (acelerando ↗).</strong> 34 sinais, score 5.4 crescente. "
               "A conectividade via satélite de órbita baixa (Starlink, OneWeb, Amazon Kuiper) está criando casos de uso "
               "concretos para o Brasil: monitoramento de linhas de transmissão em áreas remotas, precisão agrícola "
               "no cerrado sem cobertura 4G/5G, e comunicação em emergências climáticas. "
               "O custo por terminal caiu 60% em 2 anos. Barreira regulatória da Anatel ainda limita operação autônoma."},
    {"id": "Digital Twin",         "frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.30, "default_n": 10,  "score_default": 8.1,  "trend": "↗",  "phase": "Pico de Especulação",
     "signal": "<strong>Fase: Pico de Especulação (acelerando ↗).</strong> 10 sinais com score elevado 8.1. "
               "Gêmeos digitais de subestações e sistemas de transmissão estão sendo adotados por grandes transmissoras "
               "para simular falhas e otimizar manutenção preditiva. No agro, modelos digitais de lavoura integram "
               "dados de solo, clima e preço. Poucos casos em escala no Brasil, mas pipeline de projetos crescendo. "
               "O ONS está avaliando digital twin do Sistema Interligado Nacional (SIN) — se aprovado, move esse ponto rapidamente."},
    {"id": "Nuclear / SMR",        "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.35, "default_n": 42,  "score_default": 6.21, "trend": "↘",  "phase": "Pico de Especulação",
     "signal": "<strong>Fase: Pico de Especulação (desacelerando ↘).</strong> 42 sinais, mas score caiu de 8.0 (W14) para 3.25 (W23) — "
               "queda de 59% em dois meses. Globalmente, SMRs (Small Modular Reactors) recebem bilhões em P&D "
               "(NuScale, Rolls-Royce, Westinghouse). No Brasil, a realidade é diferente: Angra 3 segue sem prazo "
               "de conclusão, o custo da energia nuclear permanece 3–4x superior às renováveis, e o quadro regulatório "
               "da CNEN não foi atualizado para SMRs. Expectativas globais altas, implementação local indefinida."},
    {"id": "IA Generativa",        "frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.38, "default_n": 116, "score_default": 4.83, "trend": "↘",  "phase": "Pico de Especulação",
     "signal": "<strong>Fase: Pico de Especulação (desacelerando ↘).</strong> 116 sinais — volume expressivo, mas score declina. "
               "O ciclo clássico: 2023-24 foram de euforia (todos os setores anunciando projetos de IA generativa), "
               "2025-26 mostram a ressaca — ROI difícil de medir, alucinações em contextos críticos, "
               "custo de inferência mais alto que o esperado. No Brasil, regulação pelo MJ avança e o PL de IA "
               "está em fase final. Empresas de energia e agro testam pilotos; poucos chegaram à produção."},
    {"id": "Data Centers IA",      "frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.43, "default_n": 201, "score_default": 6.47, "trend": "↘",  "phase": "Pico de Especulação",
     "signal": "<strong>Fase: Pico de Especulação (desacelerando ↘).</strong> 201 sinais — tema mais denso do banco de dados. "
               "Score caiu de 8.38 (W15) para 4.48 (W22), queda de 47% em 7 semanas. "
               "O modelo de negócio dos hyperscale data centers no Brasil encontrou obstáculos concretos: "
               "ISS municipal elevado, tarifas de energia industrial acima da média global, gargalos na rede de "
               "transmissão e falta de mão de obra especializada. Anúncios de investimento continuam, "
               "mas execução está abaixo das promessas. Ponto de inflexão: resolução da tarifa de energia para DCs."},
    {"id": "Robótica Industrial",  "frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.47, "default_n": 53,  "score_default": 6.65, "trend": "↘",  "phase": "Fricção Operacional",
     "signal": "<strong>Fase: Fricção Operacional (desacelerando ↘).</strong> 53 sinais, score 6.65 em queda. "
               "A robótica industrial brasileira avança em setores de alta margem (automotivo, petroquímica), "
               "mas encontra barreira de custo de capital para PMEs industriais. A queda do câmbio BRL/USD elevou "
               "o custo de robôs importados 25–30% em 2025-26. Casos de automação em frigoríficos e colheita "
               "agrícola mecanizada existem, mas adoção em escala ainda é lenta. Reenergização esperada com "
               "programas de crédito BNDES para automação industrial em 2027."},
    {"id": "H₂ Verde",             "frente": "CleanTech", "color": "#5DCAA5", "cx": 0.52, "default_n": 40,  "score_default": 7.16, "trend": "↘",  "phase": "Fricção Operacional",
     "signal": "<strong>Fase: Fricção Operacional (desacelerando ↘).</strong> 40 sinais, score caiu de 8.75 (W18) para 5.75 (W23). "
               "O Brasil tem potencial competitivo real em H₂ verde (custo de eletricidade renovável mais baixo do mundo), "
               "mas a janela de 18 meses para definir política industrial está se fechando. "
               "A falta de infraestrutura de escoamento (dutos, terminais de amônia), a ausência de contrato "
               "de venda firme com importadores europeus e a indefinição de subsídio federal "
               "estão mantendo projetos em standby. Sem portaria do MME com cronograma concreto, esse ponto continua descendo."},
    {"id": "EV / Mobilidade Elétrica", "frente": "CleanTech", "color": "#5DCAA5", "cx": 0.57, "default_n": 56, "score_default": 7.31, "trend": "↘", "phase": "Fricção Operacional",
     "signal": "<strong>Fase: Fricção Operacional (desacelerando ↘).</strong> 56 sinais. "
               "O Brasil cresce 9% em vendas de EVs contra 30% na média global — gap estrutural. "
               "Os obstáculos são conhecidos: infraestrutura de recarga escassa fora das capitais, "
               "preço médio de EV > R$ 150 mil (vs. R$ 80 mil do flex), ausência de V2G (vehicle-to-grid) "
               "e lentidão na isenção de IPVA em todos os estados. "
               "O mercado de frotas corporativas e ônibus elétricos municipais avança mais que o varejo — "
               "esse subsetor pode puxar o tema de volta para Escala Econômica mais cedo que o esperado."},
    {"id": "Semicondutores Brasil","frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.61, "default_n": 22,  "score_default": 5.43, "trend": "→",  "phase": "Fricção Operacional",
     "signal": "<strong>Fase: Fricção Operacional (estável →).</strong> 22 sinais, score 5.43 sem tendência clara. "
               "O debate global sobre soberania de semicondutores (CHIPS Act EUA, EU Chips Act) não encontrou "
               "resposta industrial equivalente no Brasil. A CEITEC S.A. foi mantida mas sem plano de escala. "
               "Design de chips nacionais existe (CESAR, CNPEM), mas fabricação local é economicamente inviável "
               "sem subsídio de escala. Sem catalisador governamental de R$ 10B+ em política industrial, "
               "esse ponto permanece estagnado na fricção por 2–3 anos."},
    {"id": "BESS",                 "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.68, "default_n": 72,  "score_default": 7.56, "trend": "↗",  "phase": "Escala Econômica",
     "signal": "<strong>Fase: Escala Econômica (acelerando ↗).</strong> 72 sinais, 11 fatos canônicos — maior densidade "
               "de fatos verificados do banco. O ponto de inflexão foi preciso: a ANEEL autorizou a primeira "
               "unidade de armazenamento por baterias no Brasil (mai/2026) e publicou portaria regulamentando "
               "leilões de BESS. Custo de BESS caiu 40% desde 2022 (LFP dominante). "
               "A regulação entrou em fase de consolidação: avanço em revenue stacking, dupla tarifação e serviços ancilares está em discussão, "
               "mas ainda não oferece previsibilidade econômica plena para financiamento de longo prazo. "
               "Próximo catalisador: primeiro leilão específico de BESS previsto para 2026-27."},
    {"id": "5G Industrial",        "frente": "DeepTech",    "color": "#5B8CFF", "cx": 0.73, "default_n": 29,  "score_default": 6.74, "trend": "↗",  "phase": "Escala Econômica",
     "signal": "<strong>Fase: Escala Econômica (acelerando ↗).</strong> 29 sinais com tendência de alta. "
               "O 5G industrial (redes privativas 3.5GHz / 26GHz) começa a ter casos reais no Brasil: "
               "corredores logísticos em portos, automação de mineração e comunicação entre subestações. "
               "Diferente do 5G consumidor, o industrial tem ROI mensurável em redução de latência e "
               "substituição de cabos industriais. A Anatel liberou faixas industriais em 2025. "
               "Empresas como Ericsson e Nokia reportam pipeline de projetos industriais crescendo 35% a.a. no Brasil."},
    {"id": "Open Finance / Pix",   "frente": "FinTech",     "color": "#D9A441", "cx": 0.79, "default_n": 40,  "score_default": 4.33, "trend": "→",  "phase": "Infraestrutura Crítica",
     "signal": "<strong>Pix: Infraestrutura Crítica. Open Finance: Escala Econômica (estável →).</strong> 40 sinais. "
               "O Pix atingiu maturidade plena: 9 bilhões de transações/mês, 160 milhões de usuários, "
               "custo zero para pessoa física. Já é infraestrutura crítica, não inovação. "
               "O Open Finance está na escala: compartilhamento de dados bancários com consentimento "
               "está gerando casos reais de crédito rural personalizado, scoring alternativo para MEI "
               "e propostas de seguro parametrizado. O BCB avança com Open Insurance e Open Investments — "
               "esse vetor ainda vai subir antes de estabilizar."},
    {"id": "Transmissão Elétrica", "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.83, "default_n": 70,  "score_default": 8.0,  "trend": "→",  "phase": "Infraestrutura Crítica",
     "signal": "<strong>Fase: Infraestrutura Crítica (estável →).</strong> 70 sinais com score médio 8.0 "
               "— o mais alto score sustentado do banco de dados ao longo de todas as semanas monitoradas. "
               "A expansão da malha de transmissão é condição necessária para integrar as renováveis do Nordeste "
               "e Centro-Oeste ao sudeste consumidor. Os leilões de transmissão da ANEEL são regulares, "
               "o BNDES financia com condições padronizadas e os retornos são previsíveis. "
               "Risco monitorado: conflitos fundiários e licenciamento ambiental que atrasam obras em 18–36 meses."},
    {"id": "Solar GD",             "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.88, "default_n": 447, "score_default": 7.05, "trend": "→",  "phase": "Infraestrutura Crítica",
     "signal": "<strong>Fase: Infraestrutura Crítica (estável →).</strong> 447 sinais — maior volume absoluto do banco. "
               "25 fatos canônicos validados. Custo atual: BRL 2,45/W (-7% a.a.). "
               "A Lei 15.269/2025 consolidou o marco regulatório da geração distribuída. "
               "O Brasil tem 38 GW de capacidade instalada de solar GD — dobrou em 3 anos. "
               "Tecnologia completamente matura: financiamento bancário direto ao consumidor, instalação "
               "padronizada, retorno em 4–6 anos. Próxima fronteira: integração com BESS residencial e V2G."},
    {"id": "Eólica Onshore",       "frente": "EnergyTech",  "color": "#2FA87C", "cx": 0.93, "default_n": 88,  "score_default": 7.12, "trend": "→",  "phase": "Commoditização",
     "signal": "<strong>Fase: Commoditização (estável →).</strong> 88 sinais. Tecnologia de referência de maturidade "
               "no portfólio xTechs. Leilões regulares da ANEEL com volumes crescentes, cadeia fornecedora "
               "local consolidada (torres, pás, nacelles), financiamento BNDES com critérios e taxas padronizadas. "
               "Custo nivelado (LCOE) de BRL 100–140/MWh — competitivo sem subsídio. "
               "A eólica offshore está no Sinal Emergente: o marco regulatório foi aprovado em 2023, "
               "mas os primeiros projetos só entram em operação após 2030."},
]

# Dados históricos hardcoded para fallback (sem --db)
FALLBACK_PRESSURE_WEEKS = [
    {"semana": "2026-W14", "EnergyTech": 7.8, "DeepTech": 6.2, "FinTech": 5.1, "CleanTech": 6.5, "AgriTech": 4.2},
    {"semana": "2026-W15", "EnergyTech": 7.5, "DeepTech": 7.1, "FinTech": 5.3, "CleanTech": 6.8, "AgriTech": 4.5},
    {"semana": "2026-W16", "EnergyTech": 7.2, "DeepTech": 7.4, "FinTech": 5.6, "CleanTech": 6.3, "AgriTech": 4.8},
    {"semana": "2026-W17", "EnergyTech": 7.9, "DeepTech": 6.8, "FinTech": 5.8, "CleanTech": 6.9, "AgriTech": 5.1},
    {"semana": "2026-W18", "EnergyTech": 8.1, "DeepTech": 6.5, "FinTech": 6.0, "CleanTech": 7.2, "AgriTech": 5.0},
    {"semana": "2026-W19", "EnergyTech": 7.6, "DeepTech": 6.9, "FinTech": 5.9, "CleanTech": 7.0, "AgriTech": 5.3},
    {"semana": "2026-W20", "EnergyTech": 7.4, "DeepTech": 7.2, "FinTech": 6.1, "CleanTech": 6.7, "AgriTech": 5.5},
    {"semana": "2026-W21", "EnergyTech": 8.0, "DeepTech": 7.5, "FinTech": 6.3, "CleanTech": 7.1, "AgriTech": 5.2},
    {"semana": "2026-W22", "EnergyTech": 7.8, "DeepTech": 7.8, "FinTech": 6.5, "CleanTech": 7.3, "AgriTech": 5.4},
    {"semana": "2026-W23", "EnergyTech": 8.2, "DeepTech": 7.0, "FinTech": 6.2, "CleanTech": 6.8, "AgriTech": 5.6},
]

FALLBACK_MEMORIES = [
    {"id": 1, "title": "Segurança Jurídica Setor Elétrico", "slabel": "Segurança Jurídica", "fronts": ["EnergyTech", "FinTech"], "strength": 9.0,
     "sinais": 28, "fatos": 4,
     "desc": "Risco jurídico como fator estrutural de precificação no setor elétrico.",
     "analise": "A homologação do LRCap pela ANEEL apesar de liminar estadual no Ceará (jun/2026) criou precedente: a regulação federal resiste, mas o mecanismo de contestação local permanece aberto. Suspensão cautelar de R$ 14,875 mi em pagamento à Continental Comercializadora mostra que contratos CCEE podem ser travados individualmente por decisão judicial. Efeito: prêmio de risco jurídico embutido em novos projetos de geração e transmissão.",
     "acao": "Auditoria de contratos CCEE ativos; incluir cláusulas de proteção contra suspensão cautelar em novos contratos antes do próximo ciclo de liquidação."},
    {"id": 2, "title": "Data Centers & IA — Demanda Elétrica", "fronts": ["EnergyTech", "DeepTech"], "strength": 9.0,
     "sinais": 201, "fatos": 8,
     "desc": "Maior vetor de nova demanda elétrica 2025-30 — 201 sinais, o tema mais denso do banco.",
     "analise": "Score caiu de 8.38 (W15) para 4.48 (W22) — queda de 47% em 7 semanas. Pressões concretas: ISS municipal elevado, tarifa industrial acima da média global, gargalos na rede de transmissão, falta de mão de obra. Mas a demanda estrutural não vai recuar: cada cluster de GPUs consome 50-100 MW contínuos. Brasil com energia renovável barata pode ser competitivo SE resolver tarifas e infraestrutura.",
     "acao": "Pressão regulatória por tarifa especial para DCs junto à ANEEL; parcerias com transmissoras para conexão direta; monitorar regime tributário ISS."},
    {"id": 3, "title": "Transição Solar, BESS & Smart Grid", "fronts": ["EnergyTech", "DeepTech"], "strength": 8.0,
     "sinais": 72, "fatos": 11,
     "desc": "BESS como ponto de inflexão do sistema elétrico — maior densidade de fatos verificados do banco.",
     "analise": "72 sinais sobre BESS, 11 fatos canônicos. ANEEL autorizou primeira unidade armazenadora (mai/2026). Portaria de leilão BESS publicada. Custo LFP caiu 40% desde 2022. Modelo de negócio claro: arbitragem de ponta + suporte de frequência. ONS criou área de ferramentas eletroenergéticas (jun/2026) — smart grid saindo do gatilho tecnológico.",
     "acao": "Estruturar projetos BESS para o primeiro leilão específico (previsto 2026-27); modelar receita combinada de ancilares + arbitragem + capacidade firme."},
    {"id": 4, "title": "Esgotamento Fiscal & Custo de Capital", "fronts": ["CleanTech", "FinTech"], "strength": 8.0,
     "sinais": 42, "fatos": 3,
     "desc": "Compressão estrutural de margem de investimento afeta todas as 5 frentes.",
     "analise": "Ciclo fiscal exaurido comprime capacidade de subsídio governamental para transição energética. SELIC elevada (>13% a.a.) torna custo de capital de projetos renováveis 40-60% mais caro que Europa. Agências internacionais revisaram rating de empresas do setor energético. Decisões de CAPEX nos próximos 90 dias vão carregar esse prêmio embutido.",
     "acao": "Priorizar projetos com financiamento BNDES já aprovado; evitar estruturas com alta alavancagem em dólar; renegociar spreads de financiamento existentes."},
    {"id": 5, "title": "H₂ Verde — Janela de 18 Meses", "fronts": ["CleanTech", "FinTech"], "strength": 7.0,
     "sinais": 40, "fatos": 2,
     "desc": "Oportunidade com prazo definido — janela se fechando sem política industrial federal.",
     "analise": "40 sinais. Score caiu de 8.75 (W18) para 5.75 (W23). Brasil tem potencial competitivo real: menor custo de eletricidade renovável do mundo. Mas sem dutos de escoamento, sem contratos firmes com importadores europeus e sem subsídio federal definido, projetos migram para outros países (Portugal, Marrocos, Austrália). Se o MME não publicar portaria com cronograma concreto até Q1 2027, a janela fecha.",
     "acao": "Pressão por portaria MME com roadmap H₂; engajar BNDES para linha específica; conectar com compradores europeus via mecanismo CBAM."},
]

FALLBACK_ZETTELS = [
    {"id": 101, "title": "Ciclo Fiscal Exaurido & Compressão Capital", "fronts": ["EnergyTech", "AgriTech", "DeepTech", "FinTech"], "strength": 8.5,
     "sinais": 15,
     "desc": "Zettel multifacetado — compressão de margem afeta 4 frentes simultaneamente.",
     "analise": "SELIC + fiscal exaurido + câmbio comprimem espaço para investimento público-privado em infraestrutura. No agro: crédito rural mais caro cria demanda por precisão digital. Em energia: projetos marginais deixam de fechar sem garantias. Em FinTech: spread bancário elevado cria oportunidade para crédito alternativo via Open Finance. Confirmado por revisão de rating soberano.",
     "acao": "Priorizar projetos com garantias firmes de receita (PPA de longo prazo, contratos regulados)."},
    {"id": 102, "title": "Modernização da Rede Elétrica via BESS", "slabel": "Rede Elétrica via BESS", "fronts": ["EnergyTech", "DeepTech"], "strength": 8.2,
     "sinais": 12,
     "desc": "Brasil pode liderar BESS na América Latina até 2028.",
     "analise": "ANEEL reconhece insuficiência do modelo atual e está aberta a marcos inovadores para armazenamento. Custo LFP tornou BESS competitivo sem subsídio em mercados com spread de ponta acima de R$ 200/MWh — Brasil tem esse spread. Primeiro leilão específico BESS previsto para 2026-27 vai definir o modelo de receita. Software de smart grid e automação de rede são gargalos regulatórios, não tecnológicos.",
     "acao": "Estruturar SPE para BESS antes do leilão; modelar receita combinada: ancilares + arbitragem + capacidade firme."},
    {"id": 103, "title": "H₂ Verde: Janela Crítica de Política Industrial", "fronts": ["CleanTech", "DeepTech", "FinTech"], "strength": 7.5,
     "sinais": 9,
     "desc": "Ação federal coordenada Tesouro-BNDES-MME é pré-requisito para capturar a oportunidade.",
     "analise": "A eficácia de garantias soberanas para mobilizar capital privado em transição energética é comprovada (Austrália, Chile). Sem coordenação federal, o capital migra para jurisdições com marcos claros. A urgência da transição global cria oportunidades concentradas em janelas de 3-5 anos — janela atual vai até 2027.",
     "acao": "Articular coalizão setorial para pressão por portaria MME; modelar estrutura de garantia soberana para H₂ verde."},
    {"id": 104, "title": "Infraestrutura de Mobilidade Elétrica & 5G", "slabel": "Mobilidade Elétrica & 5G", "fronts": ["DeepTech", "EnergyTech"], "strength": 7.2,
     "sinais": 8,
     "desc": "5G industrial + V2G como diferencial competitivo para cadeias automotivas.",
     "analise": "Fragmentação de cadeias pós-USMCA cria janelas para jurisdições com infraestrutura digital integrada. 5G industrial em corredores logísticos gera retorno multiplicador via manufatura de alto valor. V2G (vehicle-to-grid) pode transformar frotas de EV em storage distribuído — modelo de negócio emergindo. Integração entre política de mobilidade elétrica, regulação e planejamento de infraestrutura é pré-requisito.",
     "acao": "Engajar Anatel para licenciamento 5G industrial; mapear corredores logísticos para projetos piloto V2G."},
    {"id": 105, "title": "Insegurança Jurídica no Setor Elétrico", "slabel": "Insegurança Jurídica", "fronts": ["EnergyTech"], "strength": 7.0,
     "sinais": 7,
     "desc": "Prêmio de risco jurídico desestimula renováveis e transmissão.",
     "analise": "O precedente LRCap mostrou que a homologação federal resiste, mas o risco mudou: de risco de regra para risco de execução contratual caso a caso. Esse tipo é mais difícil de precificar porque depende do contrato, foro e contraparte. Bancos e fundos estão reprecificando spread de financiamento no setor elétrico. Projetos marginais que dependiam de custo de capital baixo podem não fechar mais.",
     "acao": "Mapear exposição de contratos CCEE a liminares ativas; incluir prêmio de risco jurídico explícito nos modelos de CAPEX e captação."},
    {"id": 106, "title": "Regulação Digital & Tributação de IA", "fronts": ["DeepTech"], "strength": 6.5,
     "sinais": 11,
     "desc": "Janela regulatória ainda aberta — quem antecipa, lidera por 18-24 meses.",
     "analise": "PL de IA em fase final no Congresso — aprovação esperada 2026-27. OCDE pressionando harmonização tributária para plataformas digitais antes de legislação doméstica. CVM avaliando sandbox para tokens de utilidade energética. Antecipação regulatória reduz custos de adaptação: empresas que modelarem conformidade agora terão vantagem competitiva significativa.",
     "acao": "Engajar processo legislativo do PL IA com análise de impacto setorial; preparar framework interno de governança de IA."},
]

FALLBACK_ENTITIES = [
    {"id": 201, "label": "ANEEL", "fronts": ["EnergyTech"], "importance": 9.5,
     "sinais": 120, "fatos": 9,
     "desc": "Agência Nacional de Energia Elétrica — principal ator regulatório do setor.",
     "analise": "Decisões recentes de alto impacto: autorização da primeira unidade de armazenamento por baterias (mai/2026), homologação do LRCap apesar de liminar estadual (jun/2026), portaria regulamentando leilões de BESS. A revisão tarifária periódica das distribuidoras e os leilões de transmissão são os dois maiores vetores de CAPEX do setor.",
     "acao": "Monitorar calendário de leilões ANEEL 2026-27; acompanhar revisão tarifária de distribuidoras chave."},
    {"id": 202, "label": "ONS", "fronts": ["EnergyTech"], "importance": 9.2,
     "sinais": 85, "fatos": 6,
     "desc": "Operador Nacional do Sistema — ator central no despacho e gestão da rede.",
     "analise": "Em junho/2026, o ONS criou área dedicada ao desenvolvimento de ferramentas eletroenergéticas — sinal de modernização institucional e abertura para digital twin do SIN. É o ator central no despacho de geração e gestão de restrições de transmissão. Decisões do ONS sobre despacho térmico afetam diretamente o PLD e a competitividade de BESS.",
     "acao": "Acompanhar publicações técnicas do ONS sobre ferramentas eletroenergéticas e modelos de operação com BESS."},
    {"id": 203, "label": "BNDES", "fronts": ["EnergyTech", "FinTech"], "importance": 8.8,
     "sinais": 60, "fatos": 5,
     "desc": "Banco Nacional de Desenvolvimento — principal financiador de renováveis e infraestrutura.",
     "analise": "Taxas padronizadas (TLP + spread setorial) são referência para modelagem de projetos. Linhas específicas para BESS e smart grid ainda em discussão interna — aprovação esperada até Q4 2026. Ator-chave para viabilização do H₂ verde (linha de garantia soberana necessária). BNDES Agro Digital em consulta pública.",
     "acao": "Acompanhar lançamento de linha BNDES para BESS; engajar gerência de energia para H₂ verde e AgriTech Digital."},
    {"id": 204, "label": "MME", "fronts": ["EnergyTech", "CleanTech"], "importance": 8.5,
     "sinais": 70, "fatos": 5,
     "desc": "Ministério de Minas e Energia — ator-chave para política industrial de H₂ e lítio.",
     "analise": "O MME realizou intercâmbio técnico internacional sobre cadeia de valor do lítio (abr/2026). É o ator-chave para definição da política industrial do H₂ verde — portaria com roadmap ainda pendente. O Plano Decenal de Energia (PDE) define os leilões de expansão de capacidade para os próximos 10 anos. Coordena a posição do Brasil nas negociações do CBAM com a União Europeia.",
     "acao": "Acompanhar PDE 2034; monitorar portaria H₂ verde; engajar GTI Lítio do MME."},
    {"id": 205, "label": "BCB", "fronts": ["FinTech"], "importance": 8.3,
     "sinais": 45, "fatos": 4,
     "desc": "Banco Central do Brasil — indutor do Open Finance e do Pix.",
     "analise": "O BCB é o arquiteto do ecossistema de Open Finance — compartilhamento de dados com consentimento gerando casos reais de crédito rural personalizado e scoring alternativo para MEI. Avança com Open Insurance e Open Investments. A SELIC acima de 13% é o principal fator de custo de capital para projetos de energia. Decisões de política monetária impactam diretamente a viabilidade de projetos de infraestrutura.",
     "acao": "Monitorar reuniões COPOM; acompanhar evolução do Open Finance Phase 4 (dados de investimentos)."},
    {"id": 206, "label": "CVM", "fronts": ["FinTech"], "importance": 7.9,
     "sinais": 22, "fatos": 2,
     "desc": "Comissão de Valores Mobiliários — reguladora de tokens de energia e RECs.",
     "analise": "A CVM está avaliando sandbox regulatório para tokens de utilidade energética e ativos tokenizados — decisão esperada para 2026-27. A aprovação desbloquearia um mercado de tokenização de créditos de carbono, RECs (Renewable Energy Certificates) e contratos bilaterais de energia em blockchain. Score médio de 9.0 nos sinais relacionados reflete alta expectativa do mercado.",
     "acao": "Acompanhar sandbox CVM; preparar estrutura jurídica para emissão de tokens de utilidade energética."},
    {"id": 207, "label": "Embrapa", "fronts": ["AgriTech"], "importance": 7.6,
     "sinais": 18, "fatos": 2,
     "desc": "Empresa Brasileira de Pesquisa Agropecuária — âncora de inovação no AgriTech.",
     "analise": "A Embrapa é a principal âncora de transferência tecnológica para o agronegócio brasileiro. Parcerias com startups agritech para validação de sensores IoT e modelos preditivos de produtividade. A Embrapa Carbon+ é iniciativa relevante para monetização de créditos de carbono no agro. Sua rede de 42 unidades espalhadas pelo Brasil facilita adoção tecnológica em regiões remotas.",
     "acao": "Mapear editais de parceria Embrapa para startups; avaliar Embrapa Carbon+ para créditos de carbono agropecuários."},
    {"id": 208, "label": "Anatel", "fronts": ["DeepTech"], "importance": 7.4,
     "sinais": 29, "fatos": 3,
     "desc": "Agência Nacional de Telecomunicações — reguladora do 5G industrial.",
     "analise": "A Anatel liberou faixas de frequência para redes privativas industriais (3.5 GHz / 26 GHz) em 2025. É o ator-chave para o licenciamento de redes 5G industriais em portos, mineração e corredores logísticos. O avanço da Anatel na regulação de satélites LEO (Starlink, OneWeb) abre novas aplicações para infraestrutura elétrica em regiões remotas.",
     "acao": "Monitorar editais Anatel para redes privativas 5G; avaliar uso de satélites LEO para monitoramento de linhas de transmissão."},
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def h(value: Any) -> str:
    return html.escape(str(value), quote=True) if value is not None else ""


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def first_sentence(text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""
    m = re.search(r"(.+?[.!?])\s+", text)
    return m.group(1).strip() if m else text


def load_json(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {p}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_dashboard(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("dashboard") or {}


def get_vetores(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    v = data.get("vetores_estrategicos") or []
    return v if isinstance(v, list) else []


def get_briefing(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("briefing_diario") or {}


def get_clusters(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    dash = get_dashboard(data)
    c = dash.get("clusters") or data.get("clusters") or []
    return c if isinstance(c, list) else []


def get_cenarios(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    c = data.get("cenarios_prospectivos") or (get_dashboard(data).get("cenarios") or [])
    return c if isinstance(c, list) else []


def get_fatos(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    dash = get_dashboard(data)
    f = dash.get("fatos_canonicos") or data.get("fatos_canonicos") or []
    return f if isinstance(f, list) else []


def get_itens(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    itens = data.get("itens") or []
    return itens if isinstance(itens, list) else []


def type_color(tipo: str) -> str:
    for key, col in TYPE_COLORS.items():
        if key.lower() in (tipo or "").lower():
            return col
    return C_MUTED


def score_color(score: float) -> str:
    if score >= 7.5:
        return C_DANGER
    if score >= 6.0:
        return C_AMBER
    if score >= 4.5:
        return C_TECH
    return C_MUTED


def pressure_level(value: float) -> Tuple[str, str]:
    if value >= 8.0:
        return "Crítica", C_DANGER
    if value >= 7.0:
        return "Elevada", C_AMBER
    if value >= 5.5:
        return "Moderada", C_TECH
    return "Observação", C_MUTED


def sort_vetores_for_priority(vetores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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


def janela_to_x(v: Dict[str, Any]) -> float:
    dias = int(v.get("janela_decisoria_dias") or 180)
    if dias <= 30:
        return 0.88
    if dias <= 90:
        return 0.68
    if dias <= 180:
        return 0.42
    if dias <= 360:
        return 0.16
    return 0.07


def chip(label: str, color: str = C_TERRA_LIGHT) -> str:
    return f'<span class="rte5-chip" style="--chip:{h(color)}">{h(label)}</span>'


def details_block(summary: str, body: str, cls: str = "") -> str:
    return f"""
<details class="rte5-details {h(cls)}">
  <summary>{h(summary)} <span>+</span></summary>
  <div class="rte5-details-body">{body}</div>
</details>"""


def render_text_or_details(text: str, summary: str = "Expandir texto completo", threshold: int = 280) -> str:
    text = clean_text(text)
    if not text:
        return ""
    if len(text) <= threshold:
        return f'<p class="rte5-text-full">{h(text)}</p>'
    return details_block(summary, f'<p class="rte5-text-full">{h(text)}</p>')


def urgent_items_by_type(data: Dict[str, Any], tipo_label: str, limit: int = 8) -> List[Dict[str, Any]]:
    out = []
    for item in get_itens(data):
        analise = item.get("analise") or {}
        tipo = analise.get("tipo_sinal") or ""
        if tipo != tipo_label:
            continue
        try:
            urg = int(analise.get("urgencia_dias") or 999)
            impacto = float(analise.get("impacto_brasil") or item.get("score_final") or 0)
            conf = float(analise.get("confianca_analise") or 0)
        except Exception:
            continue
        titulo = item.get("titulo_pt") or analise.get("titulo_pt") or item.get("titulo") or ""
        if urg <= 90 and impacto >= 3.0 and conf >= 0.40 and titulo:
            out.append(item)
    out.sort(key=lambda it: (float((it.get("analise") or {}).get("impacto_brasil") or 0), float(it.get("score_final") or 0)), reverse=True)
    return out[:limit]


def build_capital_panel(vetores: List[Dict[str, Any]]) -> List[Tuple[str, float, str]]:
    accum = {k: 0.0 for k in MAT_KEYS}
    weights = {k: 0.0 for k in MAT_KEYS}
    for v in vetores:
        press = max(float(v.get("pressao_estrategica") or 0), 1.0)
        mat = v.get("materialidade") or {}
        for k in MAT_KEYS:
            raw = mat.get(k, "")
            score = MAT_SCORE.get(raw, 0)
            accum[k] += score * press
            weights[k] += 4 * press
    rows = []
    for k in MAT_KEYS:
        val = accum[k] / weights[k] if weights[k] else 0.0
        level = "Alta" if val >= 0.63 else "Média" if val >= 0.38 else "Baixa"
        rows.append((k, val, level))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


# ─── Novo: load_historical_data ───────────────────────────────────────────────

def load_historical_data(db_path: str | None) -> Dict[str, Any]:
    """Carrega dados históricos do SQLite. Retorna dict com fallback se db_path=None ou erro."""
    if not db_path:
        return {}

    try:
        import sqlite3
    except ImportError:
        return {}

    result: Dict[str, Any] = {}

    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # ── pressure_weeks ──────────────────────────────────────────────────
        try:
            weeks: Dict[str, Dict[str, Any]] = {}
            for frente, themes_sql in FRENTE_THEMES.items():
                cur.execute(f"""
                    SELECT strftime('%Y-W%W', cycle_date) as semana,
                           ROUND(AVG(CAST(score AS REAL)), 2) as val
                    FROM raw_items
                    WHERE theme IN {themes_sql}
                      AND cycle_date >= date('now', '-75 days')
                      AND score IS NOT NULL
                    GROUP BY semana
                    ORDER BY semana
                """)
                for row in cur.fetchall():
                    sem = row["semana"]
                    if sem not in weeks:
                        weeks[sem] = {"semana": sem}
                    weeks[sem][frente] = row["val"] or 0.0
            result["pressure_weeks"] = sorted(weeks.values(), key=lambda r: r["semana"])[-10:]
        except Exception:
            pass

        # ── tech_signals ────────────────────────────────────────────────────
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
            tech_signals: Dict[str, int] = {}
            tech_scores: Dict[str, float] = {}
            for row in cur.fetchall():
                tech_signals[row["keyword"]] = row["n"]
                tech_scores[row["keyword"]] = row["score"] or 0.0
            result["tech_signals"] = tech_signals
            result["tech_scores"] = tech_scores
        except Exception:
            pass

        # ── memories ────────────────────────────────────────────────────────
        try:
            cur.execute("""
                SELECT id, title, thesis, themes, supporting_facts, strength
                FROM strategic_memory
                WHERE status='active'
                ORDER BY strength DESC
            """)
            memories = []
            for row in cur.fetchall():
                themes_raw = row["themes"] or "[]"
                try:
                    themes_list = json.loads(themes_raw) if isinstance(themes_raw, str) else themes_raw
                except Exception:
                    themes_list = []
                fronts = _themes_to_fronts(themes_list, title=row["title"] or "")
                # thesis → desc para o painel de detalhes do grafo
                thesis = row["thesis"] or ""
                # Usa os temas como contexto analítico (supporting_facts contém IDs numéricos)
                analise = "Temas: " + ", ".join(themes_list[:4]) if themes_list else ""
                memories.append({
                    "id": row["id"], "title": row["title"],
                    "fronts": fronts, "strength": row["strength"] or 0,
                    "desc": thesis[:280], "analise": analise,
                })
            result["memories"] = memories
        except Exception:
            pass

        # ── zettels ─────────────────────────────────────────────────────────
        try:
            cur.execute("""
                SELECT id, title, themes, supports, interpretation, body, strength
                FROM zettel_notes
                WHERE status='active'
            """)
            zettels = []
            for row in cur.fetchall():
                themes_raw = row["themes"] or "[]"
                try:
                    themes_list = json.loads(themes_raw) if isinstance(themes_raw, str) else themes_raw
                except Exception:
                    themes_list = []
                fronts = _themes_to_fronts(themes_list, title=row["title"] or "")
                interp = row["interpretation"] or ""
                body   = row["body"] or ""
                # Primeira frase do body como analise (body pode ser muito longo)
                body_short = body[:300].rsplit(". ", 1)[0] + "." if len(body) > 300 else body
                zettels.append({
                    "id": row["id"], "title": row["title"],
                    "fronts": fronts, "memory": row["supports"],
                    "strength": float(row["strength"] or 5.0),
                    "desc": interp[:280], "analise": body_short,
                })
            result["zettels"] = zettels
        except Exception:
            pass

        # ── entities ────────────────────────────────────────────────────────
        try:
            cur.execute("""
                SELECT id, name, entity_type, importance_score
                FROM entities
                ORDER BY importance_score DESC
                LIMIT 10
            """)
            entities = []
            for row in cur.fetchall():
                fronts = _entity_to_fronts(row["name"] or "", row["entity_type"] or "")
                etype  = row["entity_type"] or ""
                entities.append({
                    "id": row["id"], "label": row["name"],
                    "fronts": fronts, "importance": row["importance_score"] or 5.0,
                    "desc": f"Tipo: {etype}" if etype else "",
                })
            result["entities"] = entities
        except Exception:
            pass

        # ── cockpit totals ───────────────────────────────────────────────────
        try:
            cur.execute("SELECT COUNT(*) FROM raw_items")
            result["total_acumulado"] = cur.fetchone()[0]
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(DISTINCT source) FROM raw_items")
            result["fontes_acumuladas"] = cur.fetchone()[0]
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
            cur.execute("""SELECT date(cycle_date) as dia, COUNT(*) as total,
                    SUM(CASE WHEN CAST(score AS REAL) >= 8 THEN 1 ELSE 0 END) as criticos
                FROM raw_items WHERE cycle_date >= date('now','-15 days')
                  AND cycle_date IS NOT NULL
                GROUP BY dia ORDER BY dia""")
            result["daily_counts"] = [(r[0], r[1], r[2] or 0) for r in cur.fetchall()]
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(*) FROM canonical_facts")
            result["fatos_acumulados"] = cur.fetchone()[0]
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(*) FROM strategic_memory WHERE status='active'")
            result["memorias_ativas"] = cur.fetchone()[0]
        except Exception:
            pass
        try:
            cur.execute("SELECT COUNT(*) FROM entities")
            result["entidades"] = cur.fetchone()[0]
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
    except Exception:
        pass

    return result


# ─── Índice de itens (sinais de origem com link) ─────────────────────────────

def _build_items_index(data: Dict[str, Any]):
    """Retorna (by_idx, by_title) para lookup de itens pelo vetor."""
    itens = data.get("itens") or []
    by_idx: Dict[int, Dict] = {i: it for i, it in enumerate(itens)}
    by_title: Dict[str, Dict] = {}
    for it in itens:
        t = (it.get("titulo_pt") or it.get("titulo") or "").strip()
        if t:
            by_title[t.lower()] = it
    return by_idx, by_title


def _render_fontes_block(items_list: List[Dict[str, Any]]) -> str:
    """Renderiza bloco colapsável com títulos linkados das fontes."""
    if not items_list:
        return ""
    links = []
    for it in items_list:
        titulo = (it.get("titulo_pt") or it.get("titulo") or "").strip()
        url    = (it.get("link") or "").strip()
        fonte  = (it.get("fonte") or "").strip()
        if not titulo:
            continue
        label = f"{titulo}" + (f" — {fonte}" if fonte else "")
        if url:
            links.append(f'<div class="rte5-signal"><a href="{h(url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline;text-decoration-color:rgba(255,255,255,.2)">{h(label)}</a></div>')
        else:
            links.append(f'<div class="rte5-signal">{h(label)}</div>')
    if not links:
        return ""
    body = f'<div class="rte5-signal-list">{"".join(links)}</div>'
    return details_block(f"FONTES ({len(links)})", body)


# ─── Readers Phase 6.5 (v7) ──────────────────────────────────────────────────

def get_hero(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("hero") or {}

def get_impacto_xtech(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("impacto_xtech") or {}

def get_graph_anchors(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("graph_anchors") or {}

def get_fatos_duros(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = data.get("evidencias_ciclo") or data.get("fatos_duros") or []
    return raw if isinstance(raw, list) else []

def get_cenarios_xtech(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = data.get("cenarios") or {}
    return raw if isinstance(raw, dict) else {}

def get_convergencia_v7(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = data.get("convergencia") or []
    return raw if isinstance(raw, list) else []

def get_cta(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("cta") or {}


def _themes_to_fronts(themes: List[str], title: str = "") -> List[str]:
    """Mapeia temas (+ título) para frentes xTechs — keyword matching abrangente."""
    # Analisa o conjunto completo de texto: temas + título
    corpus = " ".join(themes + [title]).lower()
    fronts = set()

    _ENERGY = [
        "energia", "energét", "energeti", "elétric", "eletric", "solar", "bess",
        "transmiss", "eólica", "eolica", "geração", "geracao", "hidro", "renovável",
        "renovavel", "armazenamento", "despacho", "curtailment", "pld", "aneel",
        "ccee", "lrcap", "leilão", "leilao", "ons ", "moderniz", "rede elétr",
        "rede eletr", "newave", "setor elétr", "setor eletr", "mercado livre",
        "capacidade energét", "capacidade energeti", "smart grid",
    ]
    _CLEAN = [
        "hidrogênio", "hidrogenio", "h2v", "verde", "sustentab", "descarboniz",
        "clean", "carbon", "biometano", "saf", "lítio", "litio", "mobilidade elétrica",
        "mobilidade eletrica", "v2x", "transição energética", "transicao energetica",
        "meio ambiente", "emissões", "emissoes",
    ]
    _AGRI = [
        "agroneg", "agro", "agric", "rural", "embrapa", "safra", "soja", "milho",
        "pecuária", "pecuaria", "irrigação", "irrigacao", "colheita", "lavoura",
        "campo", "produtor rural", "crédito rural", "credito rural", "seguro rural",
        "proagro", "fesr",
    ]
    _DEEP = [
        "inteligência artificial", "inteligencia artificial", "ia ", " ia,", "data center",
        "digital", "5g", "robótica", "robotica", "deeptech", "deep tech", "cibersegur",
        "regulação digital", "regulacao digital", "plataforma", "semicondutor",
        "computaç", "computac", "tecnologia da informação", "iot", "satélit", "satelit",
    ]
    _FINTECH = [
        "financ", "rating", "spread", "fintech", "pix", "open finance", "tokeniz",
        "câmbio", "cambio", "capital de giro", "crédito alternativo", "credito alternativo",
        "banco central", "bcb", "cvm", "custo de capital", "tributaç", "tributac",
        "esgotamento fiscal", "sustentabilidade fiscal", "fiscal agro",
    ]

    if any(k in corpus for k in _ENERGY):
        fronts.add("EnergyTech")
    if any(k in corpus for k in _CLEAN):
        fronts.add("CleanTech")
    if any(k in corpus for k in _AGRI):
        fronts.add("AgriTech")
    if any(k in corpus for k in _DEEP):
        fronts.add("DeepTech")
    if any(k in corpus for k in _FINTECH):
        fronts.add("FinTech")

    # Fallback conservador: usa EnergyTech se ainda não mapeou nada
    # (maioria dos dados do pipeline é no setor energético)
    return list(fronts) if fronts else ["EnergyTech"]


def _entity_to_fronts(name: str, entity_type: str = "") -> List[str]:
    """Mapeia entidades para frentes por nome/tipo."""
    nl = (name + " " + entity_type).lower()
    fronts = set()
    if any(k in nl for k in ["ons", "aneel", "ccee", "epe", "mme", "cmse", "pld", "bess",
                               "solar", "eólica", "transmiss", "energi", "petrobras",
                               "petro", "geração", "geracao"]):
        fronts.add("EnergyTech")
    if any(k in nl for k in ["bess", "hidrogênio", "hidrogenio", "carbono", "clean", "verde"]):
        fronts.add("CleanTech")
    if any(k in nl for k in ["bndes", "banco", "bcb", "cvm", "financ", "pagament",
                               "fintech", "open finance", "crédito", "credito"]):
        fronts.add("FinTech")
    if any(k in nl for k in ["embrapa", "agro", "rural", "agric"]):
        fronts.add("AgriTech")
    if any(k in nl for k in ["ia ", "digital", "tech", "data", "5g", "anatel", "mdic"]):
        fronts.add("DeepTech")
    return list(fronts) if fronts else ["EnergyTech"]


# ─── CSS ──────────────────────────────────────────────────────────────────────

def _css() -> str:
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');

.rte5, .rte5 * {{ box-sizing:border-box; }}
.rte5 {{
  --bg:{C_BG}; --bg2:{C_BG2}; --panel:{C_PANEL}; --panel2:{C_PANEL2};
  --line:{C_LINE}; --line2:{C_LINE2}; --text:{C_TEXT}; --muted:{C_MUTED}; --weak:{C_WEAK};
  --terra:{C_TERRA}; --terra2:{C_TERRA_LIGHT}; --danger:{C_DANGER}; --amber:{C_AMBER}; --green:{C_GREEN}; --tech:{C_TECH};
  --accent-blue:{C_TERRA}; --accent-blue-hover:{C_TERRA_LIGHT};
  max-width:1440px; margin:0 auto; padding:0 20px 56px; color:var(--text); background:var(--bg);
  font-family:inherit; line-height:1.65; letter-spacing:.005em;
}}
.rte5 a {{ color:inherit; }}
.rte5-header {{ display:flex; justify-content:space-between; align-items:center; gap:18px; flex-wrap:wrap; padding:16px 0 20px; border-bottom:1px solid var(--line); margin-bottom:22px; }}
.rte5-brand {{ display:flex; align-items:center; gap:12px; }}
.rte5-logo {{ width:38px; height:38px; border-radius:10px; border:1px solid rgba(91,140,255,.35); background:rgba(91,140,255,.14); display:flex; align-items:center; justify-content:center; color:var(--terra2); font-size:18px; }}
.rte5-brand-title {{ font-size:.98rem; font-weight:700; letter-spacing:-.02em; margin:0; }}
.rte5-brand-sub {{ font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--muted); margin-top:2px; }}
.rte5-cycle {{ text-align:right; font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--muted); }}
.rte5-cycle strong {{ color:var(--terra2); font-weight:600; }}
.rte5-section {{ margin:0 0 30px; }}
.rte5-section-head {{ display:flex; justify-content:space-between; align-items:flex-end; gap:12px; margin:0 0 12px; }}
.rte5-title {{ font-size:1.04rem; font-weight:800; color:var(--text); letter-spacing:-.02em; margin:0; display:flex; align-items:center; gap:9px; }}
.rte5-title:before {{ content:''; width:22px; height:1px; background:var(--terra2); display:inline-block; }}
.rte5-note {{ font-family:'IBM Plex Mono',monospace; font-size:.67rem; color:var(--weak); }}
.rte5-kicker {{ font-family:'IBM Plex Mono',monospace; font-size:.66rem; text-transform:uppercase; letter-spacing:.13em; color:var(--terra2); }}

.rte5-situation {{ position:relative; overflow:hidden; border:1px solid rgba(91,140,255,.24); border-radius:20px; background:radial-gradient(760px 420px at 86% 20%, rgba(91,140,255,.18), transparent 58%), linear-gradient(135deg, rgba(29,36,48,.97), rgba(15,17,21,.99)); padding:28px; display:grid; grid-template-columns:1.25fr .95fr; gap:22px; box-shadow:0 24px 70px rgba(0,0,0,.28); margin-bottom:30px; }}
.rte5-situation:before {{ content:''; position:absolute; inset:0; pointer-events:none; background-image:linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px); background-size:44px 44px; mask-image:linear-gradient(to right, rgba(0,0,0,.55), transparent 82%); }}
.rte5-sit-left, .rte5-sit-right {{ position:relative; z-index:1; }}
.rte5-eyebrow {{ font-family:'IBM Plex Mono',monospace; color:var(--terra2); font-size:.66rem; letter-spacing:.13em; text-transform:uppercase; margin-bottom:12px; }}
.rte5-headline {{ font-family:inherit !important; font-size:clamp(34px,3.4vw,52px); line-height:1.06; font-weight:650; letter-spacing:-0.045em; margin:0 0 24px; max-width:18ch; }}
.rte5-subtitle {{ font-size:1.0rem; color:var(--muted); line-height:1.65; max-width:820px; margin:0 0 14px; }}
.rte5-thesis-points {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:18px; }}
.rte5-thesis-point {{ border:1px solid var(--line); background:rgba(255,255,255,.035); border-radius:12px; padding:12px; min-height:112px; }}
.rte5-point-num {{ font-family:'IBM Plex Mono',monospace; color:var(--terra2); font-size:.72rem; margin-bottom:6px; }}
.rte5-point-text {{ font-size:.79rem; color:var(--muted); line-height:1.5; }}
.rte5-metrics {{ display:grid; grid-template-columns:repeat(2,1fr); gap:10px; margin-bottom:12px; }}
.rte5-metric {{ border:1px solid var(--line); background:rgba(15,23,34,.64); border-radius:14px; padding:13px; }}
.rte5-metric-val {{ font-family:'IBM Plex Mono',monospace; font-weight:800; font-size:1.45rem; color:var(--terra2); line-height:1; }}
.rte5-metric-label {{ font-size:.72rem; color:var(--muted); margin-top:5px; }}
.rte5-ips-wrap {{ position:relative; display:inline-block; }}
.rte5-ips-icon {{ cursor:pointer; color:var(--terra2); font-size:.7rem; vertical-align:super; margin-left:2px; user-select:none; }}
.rte5-ips-tip {{ display:none; position:absolute; bottom:calc(100% + 8px); left:50%; transform:translateX(-50%);
  width:240px; background:#0f1722; border:1px solid var(--line); border-radius:10px;
  padding:10px 13px; font-size:.74rem; color:var(--muted); line-height:1.55;
  box-shadow:0 4px 20px rgba(0,0,0,.5); z-index:999; pointer-events:none; }}
.rte5-ips-tip::after {{ content:''; position:absolute; top:100%; left:50%; transform:translateX(-50%);
  border:6px solid transparent; border-top-color:#0f1722; }}
.rte5-ips-wrap:hover .rte5-ips-tip,
.rte5-ips-wrap.active .rte5-ips-tip {{ display:block; }}
.rte5-priority-card {{ border:1px solid rgba(217,108,108,.34); background:rgba(217,108,108,.07); border-radius:16px; padding:16px; }}
.rte5-priority-label {{ font-family:'IBM Plex Mono',monospace; color:var(--danger); font-size:.66rem; text-transform:uppercase; letter-spacing:.12em; margin-bottom:8px; }}
.rte5-priority-name {{ font-size:.95rem; font-weight:800; letter-spacing:-.02em; line-height:1.32; margin-bottom:10px; }}
.rte5-priority-decision {{ color:var(--text); font-size:.82rem; line-height:1.58; margin-top:10px; border-left:2px solid var(--danger); padding-left:10px; }}
.rte5-decision-list {{ display:flex; flex-direction:column; gap:10px; margin-top:12px; }}
.rte5-decision-mini {{ border:1px solid var(--line); background:rgba(15,23,34,.48); border-left:3px solid var(--terra2); border-radius:12px; padding:10px; }}
.rte5-decision-mini-head {{ display:flex; gap:8px; align-items:center; justify-content:space-between; font-family:'IBM Plex Mono',monospace; font-size:.64rem; color:var(--terra2); text-transform:uppercase; letter-spacing:.08em; margin-bottom:5px; }}
.rte5-decision-mini-text {{ font-size:.78rem; color:var(--text); line-height:1.55; }}
.rte5-chip-row {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }}
.rte5-chip {{ --chip:var(--terra2); display:inline-flex; align-items:center; gap:6px; border:1px solid rgba(91,140,255,.45); background:rgba(91,140,255,.11); color:var(--chip); border-radius:999px; padding:3px 8px; font-family:'IBM Plex Mono',monospace; font-size:.64rem; line-height:1.3; }}

.rte5-grid-4 {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
.rte5-grid-3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.rte5-grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
.rte5-card {{ border:1px solid var(--line); background:var(--panel); border-radius:14px; padding:16px; }}
.rte5-card.soft {{ background:rgba(255,255,255,.028); }}
.rte5-card-title {{ font-size:.92rem; font-weight:800; letter-spacing:-.018em; margin:0 0 8px; }}
.rte5-card-body {{ font-size:.82rem; color:var(--muted); line-height:1.6; }}
.rte5-mini-label {{ font-family:'IBM Plex Mono',monospace; color:var(--weak); font-size:.63rem; text-transform:uppercase; letter-spacing:.10em; }}
.rte5-text-full {{ font-size:.82rem; color:var(--text); line-height:1.68; margin:0; }}
.rte5-details {{ margin-top:8px; border-top:1px solid var(--line); padding-top:8px; }}
.rte5-details summary {{ cursor:pointer; list-style:none; font-family:'IBM Plex Mono',monospace; font-size:.66rem; color:var(--terra2); text-transform:uppercase; letter-spacing:.08em; display:flex; justify-content:space-between; align-items:center; gap:12px; }}
.rte5-details summary::-webkit-details-marker {{ display:none; }}
.rte5-details[open] summary span {{ transform:rotate(45deg); }}
.rte5-details-body {{ margin-top:8px; }}

.rte5-pressure-card {{ border-left:3px solid var(--c); }}
.rte5-pressure-score {{ font-family:'IBM Plex Mono',monospace; font-size:2rem; font-weight:800; color:var(--c); line-height:1; }}
.rte5-pressure-score span {{ font-size:.84rem; color:var(--muted); }}
.rte5-pressure-name {{ font-size:.82rem; font-weight:800; margin:7px 0 4px; }}
.rte5-pressure-meta {{ font-family:'IBM Plex Mono',monospace; font-size:.66rem; color:var(--muted); }}
.rte5-pressure-events {{ margin-top:10px; border-top:1px solid var(--line); padding-top:9px; display:flex; flex-direction:column; gap:7px; }}
.rte5-event {{ display:flex; gap:7px; font-size:.74rem; line-height:1.42; color:var(--muted); }}
.rte5-event:before {{ content:'•'; color:var(--c); flex-shrink:0; }}

.rte5-mesa {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.rte5-horizon {{ border:1px solid var(--line); border-radius:14px; overflow:hidden; background:var(--panel); }}
.rte5-horizon-head {{ padding:10px 12px; font-family:'IBM Plex Mono',monospace; font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; color:var(--c); background:rgba(255,255,255,.035); border-bottom:1px solid var(--line); }}
.rte5-horizon-body {{ padding:12px; display:flex; flex-direction:column; gap:10px; }}
.rte5-action {{ background:rgba(15,23,34,.55); border:1px solid var(--line); border-left:3px solid var(--c); border-radius:10px; padding:11px; }}
.rte5-action-vetor {{ font-family:'IBM Plex Mono',monospace; color:var(--c); font-size:.62rem; text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px; }}
.rte5-action-text {{ font-size:.80rem; color:var(--text); line-height:1.6; }}
.rte5-action-risk {{ font-size:.76rem; color:var(--muted); margin-top:8px; border-top:1px solid var(--line); padding-top:8px; line-height:1.5; }}
.rte5-empty {{ font-family:'IBM Plex Mono',monospace; color:var(--weak); font-size:.74rem; padding:12px; text-align:center; }}

.rte5-map-wrap {{ display:grid; grid-template-columns:minmax(0,1fr) 280px; gap:16px; align-items:start; }}
.rte5-chart-card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:16px; overflow-x:auto; }}
.rte5-chart-card canvas {{ width:100% !important; min-height:400px; max-height:460px; }}
.rte5-map-side {{ display:flex; flex-direction:column; gap:10px; }}
.rte5-quad-stat {{ border:1px solid var(--line); background:rgba(255,255,255,.028); border-left:3px solid var(--c); border-radius:12px; padding:12px; }}
.rte5-quad-num {{ font-family:'IBM Plex Mono',monospace; color:var(--c); font-size:1.4rem; font-weight:800; line-height:1; }}
.rte5-quad-name {{ font-size:.78rem; color:var(--text); font-weight:800; margin-top:5px; }}
.rte5-vector-cards {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin-top:14px; }}
.rte5-vector-card {{ border:1px solid var(--line); background:var(--panel); border-left:3px solid var(--c); border-radius:14px; padding:14px; }}
.rte5-vector-name {{ font-size:.92rem; font-weight:850; line-height:1.32; letter-spacing:-.02em; margin-bottom:8px; }}
.rte5-vector-meta {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:9px; }}
.rte5-vector-desc {{ font-size:.79rem; color:var(--muted); line-height:1.58; margin-bottom:10px; }}
.rte5-vector-decision {{ border-left:2px solid var(--green); padding-left:10px; font-size:.80rem; color:var(--text); line-height:1.55; margin-top:9px; }}
.rte5-vector-risk {{ border-left:2px solid var(--amber); padding-left:10px; font-size:.78rem; color:var(--muted); line-height:1.52; margin-top:9px; }}

.rte5-capital-panel {{ display:grid; grid-template-columns:1.2fr .8fr; gap:16px; }}
.rte5-bars {{ display:flex; flex-direction:column; gap:12px; }}
.rte5-bar-row {{ display:grid; grid-template-columns:150px 1fr 62px; gap:10px; align-items:center; }}
.rte5-bar-label {{ font-size:.79rem; color:var(--text); font-weight:700; }}
.rte5-bar-bg {{ height:8px; background:rgba(255,255,255,.06); border-radius:999px; overflow:hidden; }}
.rte5-bar-fill {{ height:100%; background:linear-gradient(90deg,var(--terra),var(--terra2)); border-radius:999px; width:calc(var(--w) * 100%); }}
.rte5-bar-level {{ font-family:'IBM Plex Mono',monospace; color:var(--muted); font-size:.66rem; text-align:right; }}
.rte5-facts {{ display:grid; grid-template-columns:1fr; gap:8px; }}
.rte5-fact {{ border:1px solid var(--line); background:rgba(255,255,255,.028); border-radius:10px; padding:10px; }}
.rte5-fact-value {{ font-family:'IBM Plex Mono',monospace; color:var(--terra2); font-size:.95rem; font-weight:800; }}
.rte5-fact-context {{ font-size:.73rem; color:var(--muted); line-height:1.42; margin-top:3px; }}

.rte5-cluster {{ display:flex; flex-direction:column; }}
.rte5-cluster-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap:10px; }}
.rte5-cluster-name {{ font-size:.96rem; font-weight:850; letter-spacing:-.02em; line-height:1.34; margin-bottom:8px; }}
.rte5-cluster-tese {{ font-size:.81rem; color:var(--muted); line-height:1.62; }}
.rte5-signal-list {{ margin-top:10px; display:flex; flex-direction:column; gap:7px; }}
.rte5-signal {{ display:flex; gap:7px; align-items:flex-start; font-size:.82rem; color:var(--muted); line-height:1.55; }}
.rte5-signal:before {{ content:'•'; color:var(--terra2); flex-shrink:0; }}

.rte5-scenario {{ border:1px solid var(--line); background:var(--panel); border-left:3px solid var(--c); border-radius:14px; padding:16px; }}
.rte5-scenario-top {{ display:flex; gap:12px; align-items:center; margin-bottom:10px; }}
.rte5-scenario-num {{ width:34px; height:34px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-family:'IBM Plex Mono',monospace; color:var(--c); border:1px solid rgba(255,255,255,.10); background:rgba(255,255,255,.035); font-weight:800; flex-shrink:0; }}
.rte5-scenario-name {{ font-size:.97rem; font-weight:850; line-height:1.28; }}
.rte5-scenario-meta {{ font-family:'IBM Plex Mono',monospace; font-size:.66rem; color:var(--muted); margin-top:3px; }}
.rte5-scenario-text {{ font-size:.82rem; color:var(--muted); line-height:1.64; }}
.rte5-scenario-action {{ margin-top:12px; border:1px solid rgba(47,168,124,.22); border-left:3px solid var(--green); background:rgba(47,168,124,.07); border-radius:10px; padding:11px; font-size:.80rem; color:var(--text); line-height:1.56; }}

.rte5-briefing {{ border:1px solid var(--line); background:var(--panel); border-left:3px solid var(--terra2); border-radius:16px; padding:22px; width:100%; max-width:100%; }}
.rte5-brief-title {{ font-family:inherit !important; font-size:1.45rem; line-height:1.24; letter-spacing:-.025em; margin:0 0 6px; width:100%; max-width:100% !important; font-weight:800; }}
.rte5-brief-sub {{ font-family:'IBM Plex Mono',monospace; color:var(--terra2); font-size:.75rem; margin-bottom:16px; width:100%; max-width:100% !important; }}
.rte5-brief-opening {{ font-size:.94rem; color:var(--text); line-height:1.78; margin:0 0 20px; width:100%; max-width:100% !important; display:block; }}
.rte5-brief-segment {{ border-top:1px solid var(--line); padding-top:16px; margin-top:16px; width:100%; max-width:100%; }}
.rte5-brief-horizon {{ display:inline-flex; font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--amber); border:1px solid rgba(217,164,65,.28); background:rgba(217,164,65,.08); border-radius:999px; padding:3px 9px; margin-bottom:10px; }}
.rte5-brief-p {{ font-size:.90rem; color:var(--text); line-height:1.82; margin:0 0 12px; width:100%; max-width:100% !important; display:block; }}
.rte5-briefing p {{ width:100% !important; max-width:100% !important; }}
.rte5-decision-note {{ background:rgba(217,164,65,.08); border:1px solid rgba(217,164,65,.22); border-left:3px solid var(--amber); color:#F2D48A; border-radius:9px; padding:10px 12px; font-size:.82rem; line-height:1.55; margin:8px 0 0; width:100%; max-width:100% !important; box-sizing:border-box; }}
.rte5-cross {{ margin-top:18px; background:rgba(91,140,255,.08); border:1px solid rgba(91,140,255,.22); border-left:3px solid var(--terra2); border-radius:11px; padding:13px; font-size:.86rem; color:var(--text); line-height:1.65; width:100%; max-width:100% !important; box-sizing:border-box; }}

.rte5-footer {{ border-top:1px solid var(--line); margin-top:34px; padding-top:14px; display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px; color:var(--weak); font-family:'IBM Plex Mono',monospace; font-size:.66rem; }}

@media(max-width:1080px) {{
  .rte5-situation, .rte5-map-wrap, .rte5-capital-panel {{ grid-template-columns:1fr; }}
  .rte5-thesis-points {{ grid-template-columns:1fr; }}
}}
@media(max-width:900px) {{
  .rte5-grid-4 {{ grid-template-columns:repeat(2,1fr); }}
  .rte5-grid-3, .rte5-grid-2, .rte5-mesa, .rte5-vector-cards {{ grid-template-columns:1fr; }}
  .rte5-cycle {{ text-align:left; }}
}}
@media(max-width:560px) {{
  .rte5 {{ padding:0 12px 40px; }}
  .rte5-situation {{ padding:18px; }}
  .rte5-grid-4 {{ grid-template-columns:1fr; }}
  .rte5-headline {{ font-family:inherit !important; font-size:clamp(42px,6vw,64px); line-height:1.02; font-weight:650; letter-spacing:-0.055em; margin:0 0 28px; max-width:13ch; }}
  .rte5-metrics {{ grid-template-columns:1fr 1fr; }}
  .rte5-bar-row {{ grid-template-columns:1fr; gap:5px; }}
  .rte5-bar-level {{ text-align:left; }}
}}

/* ── v7 Hero 2 cards ──────────────────────────────────────────────────────── */
.rte5-hero {{ border:1px solid rgba(91,140,255,.20); border-radius:22px; background:radial-gradient(900px 500px at 72% 15%,rgba(91,140,255,.11),transparent 52%),linear-gradient(140deg,rgba(22,30,44,.99),rgba(12,17,27,.99)); padding:28px; margin-bottom:30px; box-shadow:0 24px 72px rgba(0,0,0,.32); overflow:hidden; position:relative; }}
.rte5-hero::before {{ content:''; position:absolute; inset:0; background-image:linear-gradient(rgba(255,255,255,.016) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.016) 1px,transparent 1px); background-size:52px 52px; mask-image:linear-gradient(to right,rgba(0,0,0,.35),transparent 70%); pointer-events:none; }}
.rte5-hero-eyebrow {{ font-family:'IBM Plex Mono',monospace; color:var(--terra2); font-size:.64rem; letter-spacing:.14em; text-transform:uppercase; display:flex; align-items:center; gap:10px; margin-bottom:12px; position:relative; z-index:1; }}
.rte5-hero-eyebrow::before {{ content:''; width:16px; height:1px; background:var(--terra2); display:inline-block; }}
.rte5-hero-grid {{ display:grid; grid-template-columns:1.35fr 0.65fr; gap:20px; position:relative; z-index:1; align-items:stretch; }}
.rte5-hero-card {{ border:1px solid rgba(255,255,255,.07); border-radius:16px; background:rgba(255,255,255,.022); padding:20px; display:flex; flex-direction:column; gap:12px; }}
.rte5-hero-card.left {{ background:rgba(91,140,255,.04); border-color:rgba(91,140,255,.16); }}
.rte5-hero-card.right {{ background:rgba(255,255,255,.018); border-color:rgba(255,255,255,.06); }}
.rte5-hero-xtech {{ display:inline-flex; align-items:center; gap:7px; border:1px solid rgba(91,140,255,.32); background:rgba(91,140,255,.09); border-radius:999px; padding:3px 12px; font-family:'IBM Plex Mono',monospace; font-size:.65rem; color:var(--terra2); width:fit-content; }}
.rte5-hero-manchete {{ font-family:inherit !important; font-size:clamp(18px,1.9vw,28px); line-height:1.18; font-weight:750; letter-spacing:-.035em; margin:0; }}
.rte5-hero-texto {{ font-size:.83rem; color:var(--muted); line-height:1.76; flex:1; }}
.rte5-hero-right-title {{ font-family:'IBM Plex Mono',monospace; font-size:.62rem; text-transform:uppercase; letter-spacing:.11em; color:var(--weak); margin-bottom:4px; }}
.rte5-hero-chart {{ width:100%; flex:1; min-height:160px; }}
.rte5-hero-metrics {{ display:flex; flex-direction:column; gap:6px; border-top:1px solid rgba(255,255,255,.06); padding-top:10px; margin-top:auto; }}
.rte5-hero-metric-row {{ display:flex; justify-content:space-between; align-items:center; }}
.rte5-hero-metric-label {{ font-size:.72rem; color:var(--weak); }}
.rte5-hero-metric-val {{ font-family:'IBM Plex Mono',monospace; font-size:.80rem; font-weight:700; color:var(--text); }}
/* ── v7 extras ──────────────────────────────────────────────────────────── */
.rte5-grid-2plus3 {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:16px; }}
.rte5-cenario-card {{ background:#151F2E; border-radius:12px; padding:16px; }}
.rte5-cenario-titulo {{ font-size:.92rem; font-weight:700; color:#E6EDF3; margin-bottom:4px; line-height:1.4; }}
.rte5-cen-head:hover {{ opacity:.9; }}

/* ── v7 Cockpit ──────────────────────────────────────────────────────────── */
.rte5-ck-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
.rte5-ck-num {{ border:1px solid rgba(255,255,255,.06); background:rgba(255,255,255,.03); border-radius:10px; padding:10px 12px; }}
.rte5-ck-val {{ font-family:'IBM Plex Mono',monospace; font-size:1.18rem; font-weight:800; color:#79A3FF; line-height:1; margin-bottom:3px; }}
.rte5-ck-lbl {{ font-size:.66rem; color:#718096; line-height:1.3; }}

/* ── v7 Impacto xTech ───────────────────────────────────────────────────────── */
.rte5-impacto-grid {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }}
.rte5-impacto-card {{ border:1px solid var(--line); background:var(--panel); border-left:3px solid var(--c); border-radius:14px; padding:14px; }}
.rte5-impacto-xtech {{ font-family:'IBM Plex Mono',monospace; font-size:.60rem; text-transform:uppercase; letter-spacing:.10em; color:var(--c); margin-bottom:8px; }}
.rte5-impacto-dir {{ font-size:1.3rem; font-weight:800; line-height:1; margin-bottom:6px; }}
.rte5-impacto-texto {{ font-size:.76rem; color:var(--muted); line-height:1.58; }}
.rte5-impacto-urg {{ font-family:'IBM Plex Mono',monospace; font-size:.62rem; margin-top:9px; padding-top:8px; border-top:1px solid var(--line); color:var(--weak); }}

/* ── v7 Graph anchor text ───────────────────────────────────────────────────── */
.rte5-graph-anchor {{ font-size:.83rem; color:var(--muted); line-height:1.65; border-left:3px solid var(--terra2); padding:10px 14px; background:rgba(91,140,255,.05); border-radius:0 10px 10px 0; margin-bottom:14px; }}

/* ── v7 Score badge ─────────────────────────────────────────────────────────── */
.rte5-score-badge {{ display:inline-flex; align-items:center; gap:5px; border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.055); border-radius:999px; padding:2px 9px; font-family:'IBM Plex Mono',monospace; font-size:.63rem; color:var(--muted); }}

/* ── v7 Fatos Duros ─────────────────────────────────────────────────────────── */
.rte5-fatos-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.rte5-fato-card {{ border:1px solid var(--line); background:var(--panel); border-radius:14px; padding:14px 16px; }}
.rte5-fato-valor {{ font-family:'IBM Plex Mono',monospace; font-size:1.45rem; font-weight:800; color:var(--terra2); line-height:1; margin-bottom:6px; }}
.rte5-fato-contexto {{ font-size:.79rem; color:var(--muted); line-height:1.55; }}
.rte5-fato-xtech {{ margin-top:10px; }}

/* ── v8 Lente de Decisão ───────────────────────────────────────────────────── */
.rte5-lente-card {{ border-left:3px solid var(--c); }}
.rte5-lente-perfil {{ font-family:'IBM Plex Mono',monospace; font-size:.66rem; text-transform:uppercase; letter-spacing:.12em; color:var(--c); margin-bottom:12px; }}
.rte5-lente-label {{ display:block; font-family:'IBM Plex Mono',monospace; font-size:.60rem; color:var(--weak); text-transform:uppercase; letter-spacing:.09em; margin-bottom:3px; }}
.rte5-lente-sinal, .rte5-lente-decisao, .rte5-lente-risco {{ font-size:.80rem; color:var(--text); line-height:1.55; margin-bottom:10px; padding-left:8px; border-left:2px solid rgba(255,255,255,.07); }}
.rte5-lente-risco {{ color:var(--muted); }}

/* ── v7 Cenários xTech tabs ─────────────────────────────────────────────────── */
.rte5-tabs {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }}
.rte5-tab-btn {{ font-family:'IBM Plex Mono',monospace; font-size:.68rem; padding:5px 12px; border-radius:999px; border:1px solid rgba(255,255,255,.10); background:transparent; color:var(--muted); cursor:pointer; transition:all .15s; }}
.rte5-tab-btn.active {{ border-color:var(--terra2); background:rgba(91,140,255,.12); color:var(--terra2); font-weight:700; }}
.rte5-tab-panel {{ display:none; }}
.rte5-tab-panel.active {{ display:block; }}
.rte5-cenario-card {{ border:1px solid var(--line); background:var(--panel); border-left:3px solid var(--terra2); border-radius:14px; padding:18px; }}
.rte5-cenario-titulo {{ font-size:.96rem; font-weight:800; letter-spacing:-.018em; line-height:1.32; margin-bottom:6px; }}
.rte5-cenario-horizonte {{ font-family:'IBM Plex Mono',monospace; font-size:.64rem; color:var(--amber); display:inline-flex; border:1px solid rgba(217,164,65,.28); background:rgba(217,164,65,.07); border-radius:999px; padding:2px 9px; margin-bottom:12px; }}
.rte5-cenario-narrativa {{ font-size:.83rem; color:var(--muted); line-height:1.68; }}
.rte5-cenario-mem {{ margin-top:12px; font-size:.77rem; color:var(--weak); border-top:1px solid var(--line); padding-top:10px; font-style:italic; }}

/* ── v7 Convergência ────────────────────────────────────────────────────────── */
.rte5-conv-card {{ border:1px solid var(--line); background:var(--panel); border-radius:14px; padding:14px; display:flex; flex-direction:column; gap:8px; }}
.rte5-conv-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
.rte5-conv-titulo {{ font-size:.93rem; font-weight:800; letter-spacing:-.018em; line-height:1.3; }}
.rte5-conv-nivel {{ font-family:'IBM Plex Mono',monospace; font-size:.60rem; text-transform:uppercase; letter-spacing:.10em; }}
.rte5-conv-narrativa {{ font-size:.79rem; color:var(--muted); line-height:1.60; }}
.rte5-conv-sinais {{ font-size:.73rem; color:var(--muted); border-top:1px solid var(--line); padding-top:8px; }}

/* ── v7 CTA ─────────────────────────────────────────────────────────────────── */
.rte5-cta-section {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.rte5-cta-card {{ border:1px solid var(--line); border-radius:18px; padding:22px; display:flex; flex-direction:column; gap:14px; }}
.rte5-cta-card.empresa {{ background:rgba(47,168,124,.06); border-color:rgba(47,168,124,.28); }}
.rte5-cta-card.live {{ background:rgba(91,140,255,.06); border-color:rgba(91,140,255,.28); }}
.rte5-cta-kicker {{ font-family:'IBM Plex Mono',monospace; font-size:.64rem; text-transform:uppercase; letter-spacing:.13em; }}
.rte5-cta-card.empresa .rte5-cta-kicker {{ color:var(--green); }}
.rte5-cta-card.live .rte5-cta-kicker {{ color:var(--terra2); }}
.rte5-cta-headline {{ font-size:1.05rem; font-weight:800; letter-spacing:-.02em; line-height:1.32; }}
.rte5-cta-desc {{ font-size:.83rem; color:var(--muted); line-height:1.65; }}
.rte5-cta-btn {{ display:inline-flex; align-items:center; gap:7px; border-radius:999px; padding:10px 22px; font-family:'IBM Plex Mono',monospace; font-size:.76rem; font-weight:700; text-decoration:none; cursor:pointer; border:none; }}
.rte5-cta-card.empresa .rte5-cta-btn {{ background:rgba(47,168,124,.18); color:var(--green); border:1px solid rgba(47,168,124,.40); }}
.rte5-cta-card.live .rte5-cta-btn {{ background:rgba(91,140,255,.18); color:var(--terra2); border:1px solid rgba(91,140,255,.40); }}
.rte5-cta-data {{ font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--weak); }}

@media(max-width:1080px) {{
  .rte5-hero, .rte5-cta-section {{ grid-template-columns:1fr; }}
  .rte5-impacto-grid {{ grid-template-columns:repeat(3,1fr); }}
}}
@media(max-width:900px) {{
  .rte5-impacto-grid {{ grid-template-columns:repeat(2,1fr); }}
  .rte5-fatos-grid {{ grid-template-columns:1fr 1fr; }}
}}
@media(max-width:560px) {{
  .rte5-impacto-grid, .rte5-fatos-grid {{ grid-template-columns:1fr; }}
  .rte5-hero-pills {{ flex-direction:column; }}
}}

/* ── Cabeçalhos de Horizonte (v9) ── */
.radar-horizonte-header {{
  display:flex; flex-wrap:wrap; align-items:baseline; gap:10px 20px;
  margin:40px 0 18px;
  padding:14px 20px 14px 22px;
  border-left:3px solid {C_TERRA};
  background:linear-gradient(90deg, rgba(91,140,255,.10) 0%, rgba(91,140,255,.03) 60%, transparent 100%);
  border-radius:0 12px 12px 0;
}}
.horizonte-label {{
  font-family:'IBM Plex Mono',monospace;
  font-size:.78rem; font-weight:700;
  text-transform:uppercase; letter-spacing:.12em;
  color:{C_TERRA_LIGHT};
  flex-shrink:0;
}}
.horizonte-audiencia {{
  font-family:'IBM Plex Mono',monospace;
  font-size:.68rem; color:{C_MUTED};
  flex-shrink:0;
}}
.horizonte-pergunta {{
  font-size:.82rem; font-style:italic;
  color:{C_TEXT}; opacity:.75;
  flex-grow:1;
}}
@media(max-width:700px) {{
  .radar-horizonte-header {{ flex-direction:column; gap:5px; }}
}}
</style>
"""


# ─── Seções existentes (v5) ───────────────────────────────────────────────────

def render_header(data: Dict[str, Any]) -> str:
    dash = get_dashboard(data)
    ciclo = dash.get("ciclo") or data.get("ciclo_id") or datetime.now(BRASILIA).strftime("%Y-%m-%d")
    versao = dash.get("versao") or data.get("versao") or "v6"
    fontes = dash.get("fontes_monitoradas", "—")
    paises = dash.get("paises_cobertos", "—")
    total = dash.get("total_sinais") or data.get("total_itens") or "—"
    return f"""
<div class="rte5-header">
  <div class="rte5-brand">
    <div class="rte5-logo">⌁</div>
    <div>
      <div class="rte5-brand-title">Radar xTechs</div>
      <div class="rte5-brand-sub">Painel de Inteligência das 5 Frentes Tecnológicas · efagundes.com</div>
    </div>
  </div>
  <div class="rte5-cycle"><strong>Ciclo {h(ciclo)}</strong> · {h(versao)}<br>{h(total)} sinais · {h(fontes)} fontes monitoradas · {h(paises)} países</div>
</div>"""


def render_situation_room(data: Dict[str, Any]) -> str:
    dash = get_dashboard(data)
    briefing = get_briefing(data)
    vetores = sort_vetores_for_priority(get_vetores(data))
    thesis = dash.get("executive_thesis") or {}
    headline = thesis.get("frase_central") or briefing.get("titulo") or "Energia, IA e capital entraram na mesma equação estratégica."
    subtitle = briefing.get("subtitulo") or first_sentence(briefing.get("frase_de_abertura", "")) or "Sinais globais traduzidos em decisões para conselhos, CEOs e investidores no Brasil."
    lede = clean_text(briefing.get("frase_de_abertura") or dash.get("briefing_executivo") or "")
    mudancas = thesis.get("mudancas_estruturais") or []
    decisions = thesis.get("decisoes_prioritarias") or []

    for v in vetores:
        if len(decisions) >= 3:
            break
        acao = v.get("decisao_recomendada")
        if acao:
            decisions.append({"acao": acao, "horizonte": f"{v.get('janela_decisoria_dias', '')}d"})
    decisions = decisions[:3]

    total = dash.get("total_sinais") or data.get("total_itens") or "—"
    fontes = dash.get("fontes_monitoradas", "—")
    paises = dash.get("paises_cobertos", "—")
    ips = dash.get("score_ips_medio", "—")
    cycle = dash.get("ciclo") or data.get("ciclo_id") or ""

    point_html = "".join(
        f'<div class="rte5-thesis-point"><div class="rte5-point-num">0{i+1}</div><div class="rte5-point-text">{h(m)}</div></div>'
        for i, m in enumerate(mudancas[:3])
    )
    if not point_html and lede:
        point_html = f'<div class="rte5-thesis-point"><div class="rte5-point-num">01</div><div class="rte5-point-text">{h(lede)}</div></div>'

    vetores_dom = dash.get("vetores_dominantes") or [v.get("nome") for v in vetores[:3]]
    chips = "".join(chip(v, C_TERRA_LIGHT) for v in vetores_dom[:4] if v)

    priority = vetores[0] if vetores else {}
    p_score = float(priority.get("pressao_estrategica") or 0)
    p_level, p_color = pressure_level(p_score)
    p_chips = "".join([
        chip(priority.get("quadrante_executivo", "Prioridade"), p_color),
        chip(f"Pressão {p_score:.1f}/10" if p_score else "Pressão —", p_color),
        chip(priority.get("janela_decisoria_categoria", "Janela —"), C_AMBER),
        chip(f"Custo {priority.get('custo_espera','—')}", C_DANGER),
    ]) if priority else ""

    dec_html = "".join(
        f"""
        <div class="rte5-decision-mini">
          <div class="rte5-decision-mini-head"><span>Decisão {i+1}</span><span>{h(d.get('horizonte','—'))}</span></div>
          <div class="rte5-decision-mini-text">{h(d.get('acao',''))}</div>
        </div>
        """
        for i, d in enumerate(decisions)
    )

    return f"""
<section class="rte5-situation" id="sala-situacao">
  <div class="rte5-sit-left">
    <div class="rte5-eyebrow">Sala de Situação Executiva · ciclo {h(cycle)}</div>
    <h1 class="rte5-headline">{h(headline)}</h1>
    <p class="rte5-subtitle">{h(subtitle)}</p>
    <p class="rte5-subtitle" style="font-size:.89rem;max-width:900px">{h(lede)}</p>
    <div class="rte5-chip-row">{chips}</div>
    <div class="rte5-thesis-points">{point_html}</div>
  </div>
  <div class="rte5-sit-right">
    <div class="rte5-metrics">
      <div class="rte5-metric"><div class="rte5-metric-val">{h(total)}</div><div class="rte5-metric-label">sinais monitorados</div></div>
      <div class="rte5-metric"><div class="rte5-metric-val">{h(fontes)}</div><div class="rte5-metric-label">fontes ativas</div></div>
      <div class="rte5-metric"><div class="rte5-metric-val">{h(paises)}</div><div class="rte5-metric-label">países cobertos</div></div>
      <div class="rte5-metric"><div class="rte5-metric-val">{h(ips)}</div><div class="rte5-metric-label"><span class="rte5-ips-wrap">IPS médio<span class="rte5-ips-icon" onclick="this.closest('.rte5-ips-wrap').classList.toggle('active')">ⓘ</span><span class="rte5-ips-tip">IPS médio considera o universo total de sinais monitorados. Os vetores exibidos são apenas os priorizados — por isso seus IPS individuais são mais altos que a média geral.</span></span></div></div>
    </div>
    <div class="rte5-priority-card" style="--p:{p_color}">
      <div class="rte5-priority-label">Vetor prioritário · {h(p_level)}</div>
      <div class="rte5-priority-name">{h(priority.get('nome') or 'Prioridade em consolidação')}</div>
      <div class="rte5-chip-row">{p_chips}</div>
    </div>
    <div class="rte5-decision-list">{dec_html}</div>
  </div>
</section>"""


def _render_sparkline_ips(pressure_weeks: list) -> str:
    """Sparkline de IPS por frente xTech — eixo temporal compartilhado, todas as 5 frentes."""
    FRONTES = [
        ("EnergyTech", "#2FA87C"),
        ("CleanTech",  "#5DCAA5"),
        ("DeepTech",   "#5B8CFF"),
        ("AgriTech",   "#7AB648"),
        ("FinTech",    "#D9A441"),
    ]
    W, H = 120, 26  # largura e altura da área de desenho

    if not pressure_weeks:
        return ""

    # Índice temporal global — todas as semanas em ordem
    semanas = [w.get("semana", "") for w in pressure_weeks]
    n = len(semanas)

    def x_pos(i: int) -> float:
        return round(i * W / max(n - 1, 1), 1)

    def spark_svg(indexed_vals: list, color: str) -> str:
        """indexed_vals: lista de (i, valor) já filtrada para pontos com dado."""
        # linha da grade Y (IPS máx referência = 10)
        lo, hi = 0.0, 10.0
        rng = hi - lo

        def y_pos(v: float) -> float:
            return round(H - 2 - (v - lo) / rng * (H - 4), 1)

        parts = [
            f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'style="display:block;overflow:visible">'
            # linha de grade em IPS=5
            f'<line x1="0" y1="{y_pos(5)}" x2="{W}" y2="{y_pos(5)}" '
            f'stroke="rgba(255,255,255,.06)" stroke-width="1"/>'
        ]
        if len(indexed_vals) >= 2:
            pts = " ".join(f"{x_pos(i)},{y_pos(v)}" for i, v in indexed_vals)
            parts.append(
                f'<polyline points="{pts}" fill="none" stroke="{color}" '
                f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" '
                f'stroke-dasharray="none"/>'
            )
        # ponto no último valor disponível (pode ser único)
        if indexed_vals:
            li, lv = indexed_vals[-1]
            parts.append(f'<circle cx="{x_pos(li)}" cy="{y_pos(lv)}" r="2.4" fill="{color}"/>')
        parts.append('</svg>')
        return "".join(parts)

    def axis_svg() -> str:
        """Eixo X com semana inicial e final."""
        first = semanas[0].replace("2026-", "") if semanas else ""
        last  = semanas[-1].replace("2026-", "") if semanas else ""
        return (
            f'<div style="display:flex;justify-content:space-between;'
            f'font-family:\'IBM Plex Mono\',monospace;font-size:.52rem;color:#4A5568;'
            f'margin-top:2px;padding-left:77px;padding-right:38px">'
            f'<span>{first}</span><span>{last}</span>'
            f'</div>'
        )

    rows = []
    for frente, color in FRONTES:
        # pares (índice_global, valor) para pontos existentes
        indexed = [(i, w[frente]) for i, w in enumerate(pressure_weeks) if frente in w and w[frente] is not None]
        # score atual = último ponto com dado
        current = indexed[-1][1] if indexed else None
        # tendência: último vs penúltimo ponto com dado
        if len(indexed) >= 2:
            delta = indexed[-1][1] - indexed[-2][1]
        else:
            delta = 0.0
        arrow = "↗" if delta > 0.15 else ("↘" if delta < -0.15 else "→")
        arrow_color = "#2FA87C" if delta > 0.15 else ("#D96C6C" if delta < -0.15 else "#718096")
        score_str = f"{current:.1f}" if current is not None else "—"
        svg = spark_svg(indexed, color)
        n_pts = len(indexed)
        pts_hint = f'title="{n_pts}/{n} semanas com dados"' if n_pts < n else ""
        rows.append(
            f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:8px">'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;font-weight:700;'
            f'color:{color};width:70px;flex-shrink:0" {pts_hint}>{frente}</span>'
            f'<span style="flex:1;min-width:0">{svg}</span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.68rem;font-weight:800;'
            f'color:{color};width:24px;text-align:right;flex-shrink:0">{score_str}</span>'
            f'<span style="font-size:.62rem;color:{arrow_color};width:12px;flex-shrink:0">{arrow}</span>'
            f'</div>'
        )

    ips_tooltip = (
        "IPS — Índice de Pressão Estratégica (0–10): média ponderada de impacto no Brasil (25%), "
        "score de relevância dos sinais (25%), exposição setorial (20%), volume de sinais verificados (15%), "
        "confiança analítica (10%) e novidade tecnológica (5%). "
        "Faixa: 8.0–10.0 = Mobilizar Agora | 6.5–7.9 = Capturar Vantagem | 5.0–6.4 = Monitorar Vetores | < 5.0 = Ruído Operacional."
    )
    header = (
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px">'
        f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.58rem;text-transform:uppercase;'
        f'letter-spacing:.10em;color:#718096">'
        f'<abbr title="{ips_tooltip}" style="text-decoration:underline dotted;cursor:help;color:inherit">'
        f'IPS</abbr> por frente · {n} semanas</span>'
        f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.56rem;color:#4A5568">0–10</span>'
        f'</div>'
    )
    return (
        f'<div style="border-top:1px solid rgba(255,255,255,.06);padding-top:12px;margin-top:2px">'
        f'{header}'
        f'{"".join(rows)}'
        f'{axis_svg()}'
        f'</div>'
    )


def render_situation_room_v8(data: Dict[str, Any]) -> str:
    """Sala de Situação Executiva — versão v8.
    Esquerda: headline + badges dos vetores dominantes + sinais_relacionados links.
    Direita: 4 métricas + impacto por frente xTech (vertical).
    """
    dash     = get_dashboard(data)
    briefing = get_briefing(data)
    vetores  = sort_vetores_for_priority(get_vetores(data))
    imp      = get_impacto_xtech(data)
    thesis   = dash.get("executive_thesis") or {}
    hero     = data.get("hero") or {}

    kicker = hero.get("kicker") or ""
    headline = (
        hero.get("manchete")
        or thesis.get("frase_central")
        or briefing.get("titulo")
        or "Energia, IA e capital entraram na mesma equação estratégica."
    )
    subtitle = (
        hero.get("deck")
        or briefing.get("subtitulo")
        or first_sentence(briefing.get("frase_de_abertura", ""))
        or "Sinais globais traduzidos em decisões para conselhos, CEOs e investidores no Brasil."
    )
    lede  = clean_text(briefing.get("frase_de_abertura") or dash.get("briefing_executivo") or "")
    cycle = dash.get("ciclo") or data.get("ciclo_id") or ""
    total  = dash.get("total_sinais") or data.get("total_itens") or "—"
    fontes = dash.get("fontes_monitoradas", "—")
    paises = dash.get("paises_cobertos", "—")
    ips    = dash.get("score_ips_medio", "—")

    # Índice de itens por título (para resolver URLs dos sinais_relacionados)
    _, by_title = _build_items_index(data)

    # Badges dos vetores dominantes + sinais_relacionados abaixo de cada um
    vetores_dom_names = dash.get("vetores_dominantes") or [v.get("nome") for v in vetores[:3] if v.get("nome")]
    vetor_by_name = {}
    for v in vetores:
        key = (v.get("nome") or "").lower()
        if key:
            vetor_by_name[key] = v

    badges_html = ""
    for nome in vetores_dom_names[:3]:
        if not nome:
            continue
        badge = chip(nome, C_TERRA_LIGHT)
        # Tenta casar com vetor para buscar sinais_relacionados
        vetor = vetor_by_name.get(nome.lower()) or next(
            (v for v in vetores if nome.lower() in (v.get("nome") or "").lower()), None
        )
        links_html = ""
        if vetor:
            sinais_rel = vetor.get("sinais_relacionados") or []
            # Fallback: keyword matching quando sinais_relacionados ainda não foi populado
            if not sinais_rel:
                all_items = data.get("itens") or []
                _STOP = {"para", "como", "mais", "isso", "esse", "esta", "este", "pelo", "pela",
                         "com", "que", "uma", "dos", "das", "nos", "nas", "por", "sem", "são"}
                corpus = " ".join([
                    vetor.get("nome") or "",
                    vetor.get("descricao_executiva") or "",
                    vetor.get("mecanismo_causal") or "",
                ]).lower()
                tokens = [w for w in re.split(r"\W+", corpus) if len(w) > 4 and w not in _STOP]
                sinais_rel = [
                    item.get("titulo_pt") or item.get("titulo") or ""
                    for item in all_items
                    if any(tok in (item.get("titulo_pt") or item.get("titulo") or "").lower() for tok in tokens)
                ][:5]
            parts = []
            for titulo in sinais_rel[:5]:
                item = by_title.get(titulo.lower().strip()) or {}
                url  = item.get("link") or item.get("url") or ""
                titulo_safe = h(titulo)
                if url:
                    parts.append(
                        f'<a href="{h(url)}" target="_blank" rel="noopener" '
                        f'style="display:block;font-size:.82rem;color:#718096;line-height:1.55;'
                        f'text-decoration:none;margin-top:2px;padding-left:6px;'
                        f'border-left:2px solid rgba(255,255,255,.08)">'
                        f'{titulo_safe}</a>'
                    )
                else:
                    parts.append(
                        f'<div style="font-size:.82rem;color:#718096;line-height:1.55;margin-top:2px;'
                        f'padding-left:6px;border-left:2px solid rgba(255,255,255,.08)">'
                        f'{titulo_safe}</div>'
                    )
            if parts:
                links_inner = "".join(parts)
                links_html = (
                    f'<details style="margin:5px 0 14px 0">'
                    f'<summary style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;'
                    f'text-transform:uppercase;letter-spacing:.09em;color:#718096;cursor:pointer;'
                    f'list-style:none;display:inline-flex;align-items:center;gap:5px;user-select:none">'
                    f'<span style="font-size:.55rem">▶</span> Fontes ({len(parts)})'
                    f'</summary>'
                    f'<div style="margin-top:5px">{links_inner}</div>'
                    f'</details>'
                )
        badges_html += badge + links_html

    # Impacto por frente xTech — lista vertical
    impacto_rows = ""
    if imp:
        for xt in ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]:
            d = imp.get(xt) or {}
            if not d:
                continue
            c        = _XTECH_COLORS_V7.get(xt, C_MUTED)
            direcao  = str(d.get("direcao") or "neutra").lower()
            icon     = _DIR_ICONS.get(direcao, "◆")
            dir_col  = C_GREEN if "alta" in direcao and "neg" not in direcao else (C_DANGER if "neg" in direcao else C_AMBER)
            impacto_rows += (
                f'<div style="padding:7px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
                f'<div style="display:flex;align-items:center;gap:5px;margin-bottom:2px">'
                f'<span style="font-size:.64rem;font-weight:700;color:{c}">{h(xt)}</span>'
                f'<span style="font-size:.68rem;color:{dir_col}">{icon}</span>'
                f'</div>'
                f'<div style="font-size:.74rem;color:#A8B3C2;line-height:1.45">{h(d.get("impacto") or "")}</div>'
                f'</div>'
            )

    # Sparkline IPS por frente — substitui SVG decorativo
    hist_data_local = data.get("hist_data") or {}
    pressure_weeks  = hist_data_local.get("pressure_weeks") or []
    _sparkline_block = _render_sparkline_ips(pressure_weeks)

    impacto_block = (
        f'{_sparkline_block}'
        f'<div style="border-top:1px solid rgba(255,255,255,.06);padding-top:10px;margin-top:14px">'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.58rem;text-transform:uppercase;'
        f'letter-spacing:.10em;color:#718096;margin-bottom:6px">Impacto por frente xTech</div>'
        f'{impacto_rows}'
        f'</div>'
    ) if impacto_rows else _sparkline_block

    # Aviso de corte de dados — alerta âmbar quando ciclo > 1 dia antes de hoje
    from datetime import date as _date
    _hoje = _date.today()
    _ciclo_str = str(cycle or "")[:10]
    _cut_color = "#718096"
    _cut_icon  = "·"
    _cut_alert = ""
    try:
        _ciclo_dt = _date.fromisoformat(_ciclo_str)
        _dias_atraso = (_hoje - _ciclo_dt).days
        if _dias_atraso >= 1:
            _cut_color = "#D4A017"
            _cut_icon  = "⚠"
            _cut_alert = (
                f'<div style="margin:12px 0 0;padding:9px 13px;border-radius:8px;'
                f'background:rgba(212,160,23,.10);border:1px solid rgba(212,160,23,.30);'
                f'font-size:.78rem;color:#D4A017;line-height:1.55">'
                f'<strong>Corte de dados: {_ciclo_str}.</strong> '
                f'Eventos ocorridos após esta data não estão incorporados a esta análise. '
                f'Verifique fontes primárias para fatos de alto impacto recentes.'
                f'</div>'
            )
    except (ValueError, TypeError):
        pass
    _ciclo_label = (
        f' <span style="color:{_cut_color};font-family:\'IBM Plex Mono\',monospace;font-size:.68rem;">'
        f'{_cut_icon} dados até {h(_ciclo_str)}</span>'
        if _ciclo_str else ""
    )

    _kicker_eyebrow = h(kicker) if kicker else f"Sala de Situação Executiva"
    return f"""
<section class="rte5-situation" id="sala-situacao">
  <div class="rte5-sit-left">
    <div class="rte5-eyebrow">{_kicker_eyebrow}{_ciclo_label}</div>
    <h1 class="rte5-headline">{h(headline)}</h1>
    <p class="rte5-subtitle" style="font-style:italic">{h(subtitle)}</p>
    <p class="rte5-subtitle" style="font-size:.89rem;max-width:900px">{h(lede)}</p>
    {_cut_alert}
    <div style="margin-top:14px">{badges_html}</div>
  </div>
  <div class="rte5-sit-right">
    <div class="rte5-metrics">
      <div class="rte5-metric"><div class="rte5-metric-val">{h(total)}</div><div class="rte5-metric-label">sinais monitorados</div></div>
      <div class="rte5-metric"><div class="rte5-metric-val">{h(fontes)}</div><div class="rte5-metric-label">fontes ativas</div></div>
      <div class="rte5-metric"><div class="rte5-metric-val">{h(paises)}</div><div class="rte5-metric-label">países cobertos</div></div>
      <div class="rte5-metric"><div class="rte5-metric-val">{h(ips)}</div><div class="rte5-metric-label"><span class="rte5-ips-wrap">IPS médio<span class="rte5-ips-icon" onclick="this.closest('.rte5-ips-wrap').classList.toggle('active')">ⓘ</span><span class="rte5-ips-tip">IPS médio considera o universo total de sinais monitorados. Os vetores exibidos são apenas os priorizados — por isso seus IPS individuais são mais altos que a média geral.</span></span></div></div>
    </div>
    {impacto_block}
  </div>
</section>"""


def render_pressure_indices(data: Dict[str, Any]) -> str:
    dash = get_dashboard(data)
    idx = dash.get("indices_pressao") or {}
    mapping = [
        ("risco_regulatorio", "Risco Regulatório", C_DANGER),
        ("oportunidade_mercado", "Oportunidade de Mercado", C_GREEN),
        ("choque_geopolitico", "Choque Geopolítico", C_AMBER),
        ("sinal_tecnologico", "Sinal Tecnológico", C_TECH),
    ]
    cards = []
    for key, label, color in mapping:
        entry = idx.get(key) or {}
        urgentes = urgent_items_by_type(data, label, limit=8)
        first_events = urgentes[:3]
        first_html = "".join(
            f'<div class="rte5-event">{h((it.get("titulo_pt") or (it.get("analise") or {}).get("titulo_pt") or it.get("titulo") or ""))}</div>'
            for it in first_events
        )
        more_html = "".join(
            f'<div class="rte5-event">{h((it.get("titulo_pt") or (it.get("analise") or {}).get("titulo_pt") or it.get("titulo") or ""))}</div>'
            for it in urgentes[3:]
        )
        expand = details_block("Expandir eventos urgentes", more_html) if more_html else ""
        if not first_html:
            first_html = '<div class="rte5-event">Sem eventos urgentes classificados neste ciclo.</div>'
        cards.append(f"""
<div class="rte5-card rte5-pressure-card" style="--c:{color}">
  <div class="rte5-pressure-score">{h(entry.get('score',0))}<span>/100</span></div>
  <div class="rte5-pressure-name">{h(label)}</div>
  <div class="rte5-pressure-meta">{h(entry.get('sinais',0))} sinais · {h(entry.get('tendencia',0))} urgentes</div>
  <div class="rte5-pressure-events">{first_html}</div>
  {expand}
</div>""")
    return f"""
<section class="rte5-section" id="pressao-executiva">
  <div class="rte5-section-head"><h2 class="rte5-title">Indicadores de Pressão Executiva</h2></div>
  <div class="rte5-grid-4">{''.join(cards)}</div>
</section>"""


def render_action_console(data: Dict[str, Any]) -> str:
    mesa = get_dashboard(data).get("mesa_decisao") or {}
    columns = [("30_dias", "30 dias", C_DANGER), ("90_dias", "90 dias", C_AMBER), ("180_dias", "6 meses", C_GREEN)]
    html_cols = []
    for key, label, color in columns:
        items = mesa.get(key) or []
        if not items:
            body = '<div class="rte5-empty">Sem ações registradas neste horizonte.</div>'
        else:
            body_parts = []
            for it in items:
                body_parts.append(f"""
<div class="rte5-action" style="--c:{color}">
  <div class="rte5-action-vetor">{h(it.get('vetor_nome','Vetor estratégico'))}</div>
  <div class="rte5-action-text">{h(it.get('acao',''))}</div>
  <div class="rte5-action-risk"><strong>Consequência da inação:</strong> {h(it.get('detalhe',''))}</div>
</div>""")
            body = "".join(body_parts)
        html_cols.append(f"""
<div class="rte5-horizon" style="--c:{color}">
  <div class="rte5-horizon-head">{h(label)}</div>
  <div class="rte5-horizon-body">{body}</div>
</div>""")
    return f"""
<section class="rte5-section" id="console-cxo">
  <div class="rte5-section-head"><h2 class="rte5-title">Console de Ações CxO</h2></div>
  <div class="rte5-mesa">{''.join(html_cols)}</div>
</section>"""


def render_map(data: Dict[str, Any]) -> str:
    _anchors_map = get_graph_anchors(data)
    periodo_coberto = h(
        _anchors_map.get("periodo_coberto")
        or data.get("ciclo_id")
        or data.get("dashboard", {}).get("ciclo")
        or "Ciclo atual"
    )
    vetores = [v for v in sort_vetores_for_priority(get_vetores(data)) if float(v.get("pressao_estrategica") or 0) >= 3.0]
    grouped = defaultdict(list)
    for v in vetores:
        grouped[v.get("quadrante_executivo", "Monitorar Vetores")].append(v)

    side = "".join(
        f"""
        <div class="rte5-quad-stat" style="--c:{color}">
          <div class="rte5-quad-num">{len(grouped.get(name, []))}</div>
          <div class="rte5-quad-name">{h(name)}</div>
        </div>
        """
        for name, color in [("Mobilizar Agora", C_DANGER), ("Capturar Vantagem", C_GREEN), ("Monitorar Vetores", C_TECH), ("Ruído Operacional", C_WEAK)]
    )

    datasets_by_type = defaultdict(list)
    for idx, v in enumerate(vetores):
        x = janela_to_x(v) + ((idx % 3) - 1) * 0.018
        x = max(0.03, min(0.97, x))
        y = float(v.get("pressao_estrategica") or 0)
        r = max(7, min(22, 6 + int(v.get("n_sinais") or 1) * 1.5))
        datasets_by_type[v.get("tipo") or "Misto"].append({
            "x": round(x, 3), "y": round(y, 2), "r": r,
            "nome": v.get("nome", "Vetor"),
            "quadrante": v.get("quadrante_executivo", "—"),
            "janela": v.get("janela_decisoria_categoria", "—"),
            "custo": v.get("custo_espera", "—"),
            "sinais": v.get("n_sinais", 1),
        })
    datasets = []
    for tipo, pts in datasets_by_type.items():
        col = type_color(tipo)
        datasets.append({"label": tipo, "data": pts, "backgroundColor": col + "99", "borderColor": col, "borderWidth": 1.5, "hitRadius": 10})
    datasets_js = json.dumps(datasets, ensure_ascii=False)

    _, by_title = _build_items_index(data)
    all_items   = data.get("itens") or []
    cards = []
    for v in vetores:
        color = QUAD_COLORS.get(v.get("quadrante_executivo"), C_MUTED)
        meta = "".join([
            chip(v.get("quadrante_executivo", "—"), color),
            chip(f"Pressão {float(v.get('pressao_estrategica') or 0):.1f}/10", score_color(float(v.get("pressao_estrategica") or 0))),
            chip(v.get("janela_decisoria_categoria", "—"), C_AMBER),
            chip(f"Custo {v.get('custo_espera','—')}", C_DANGER),
            chip(f"{v.get('n_sinais',1)} sinais", C_TERRA_LIGHT),
        ])
        # Prioridade 1: sinais_relacionados (títulos explícitos, igual à convergência)
        sinais_rel = v.get("sinais_relacionados") or []
        if sinais_rel:
            fontes_items = []
            for t in sinais_rel:
                item = by_title.get(t.lower().strip())
                fontes_items.append(item if item else {"titulo_pt": t, "link": "", "fonte": ""})
        else:
            # Fallback: keyword matching entre título do vetor + descrição e títulos dos itens
            corpus = " ".join([
                v.get("nome") or "",
                v.get("descricao_executiva") or "",
                v.get("mecanismo_causal") or "",
            ]).lower()
            # Extrair tokens significativos (>4 chars, sem stopwords)
            _STOP = {"para", "como", "mais", "isso", "esse", "esta", "este", "pelo", "pela",
                     "com", "que", "uma", "dos", "das", "nos", "nas", "por", "sem", "são"}
            tokens = [w for w in re.split(r"\W+", corpus) if len(w) > 4 and w not in _STOP]
            matched = []
            for item in all_items:
                t_lower = (item.get("titulo_pt") or item.get("titulo") or "").lower()
                if any(tok in t_lower for tok in tokens):
                    matched.append(item)
            fontes_items = matched[:8]
        fontes_block = _render_fontes_block(fontes_items)
        cards.append(f"""
<div class="rte5-vector-card" style="--c:{color}">
  <div class="rte5-vector-name">{h(v.get('nome',''))}</div>
  <div class="rte5-vector-meta">{meta}</div>
  <div class="rte5-vector-desc">{h(v.get('descricao_executiva',''))}</div>
  <div class="rte5-vector-decision"><strong>Decisão recomendada:</strong> {h(v.get('decisao_recomendada',''))}</div>
  <div class="rte5-vector-risk"><strong>Consequência da inação:</strong> {h(v.get('consequencia_inacao',''))}</div>
  {fontes_block}
</div>""")

    return f"""
<section class="rte5-section" id="mapa-pressao">
  <div class="rte5-section-head"><h2 class="rte5-title">Mapa de Pressão Estratégica × Janela de Decisão</h2></div>
  <div class="rte5-map-wrap">
    <div class="rte5-chart-card"><canvas id="rte5-vetores-canvas"></canvas></div>
    <div class="rte5-map-side">{side}</div>
  </div>
  <div class="rte5-section-head" style="margin-top:18px"><h3 class="rte5-title">Vetores Prioritários do Mapa</h3></div>
  <div class="rte5-vector-cards">{''.join(cards)}</div>
  <script>
  (function() {{
    function loadChart(cb) {{
      if (window.Chart) {{ cb(); return; }}
      var s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/chart.js';
      s.onload = cb;
      document.head.appendChild(s);
    }}
    loadChart(function() {{
      var ctx = document.getElementById('rte5-vetores-canvas');
      if (!ctx || !window.Chart) return;
      var quadrantPlugin = {{
        id:'rte5Quadrants',
        beforeDatasetsDraw:function(chart) {{
          var ca = chart.chartArea, sx = chart.scales.x, sy = chart.scales.y, c = chart.ctx;
          var midX = sx.getPixelForValue(0.5), midY = sy.getPixelForValue(5);
          c.save();
          c.fillStyle='rgba(91,140,255,.055)'; c.fillRect(ca.left, midY, midX-ca.left, ca.bottom-midY);
          c.fillStyle='rgba(111,106,99,.05)'; c.fillRect(midX, midY, ca.right-midX, ca.bottom-midY);
          c.fillStyle='rgba(47,168,124,.075)'; c.fillRect(ca.left, ca.top, midX-ca.left, midY-ca.top);
          c.fillStyle='rgba(217,108,108,.08)'; c.fillRect(midX, ca.top, ca.right-midX, midY-ca.top);
          c.font='11px IBM Plex Mono, monospace'; c.fillStyle='rgba(168,179,194,.72)';
          c.fillText('Monitorar Vetores', ca.left+8, ca.bottom-10);
          c.fillText('Ruído Operacional', midX+8, ca.bottom-10);
          c.fillText('Capturar Vantagem', ca.left+8, ca.top+18);
          c.fillText('Mobilizar Agora', midX+8, ca.top+18);
          c.restore();
        }}
      }};
      new Chart(ctx, {{
        type:'bubble',
        data: {{ datasets: {datasets_js} }},
        options: {{
          responsive:true,
          maintainAspectRatio:false,
          plugins: {{
            legend: {{ labels: {{ color:'{C_MUTED}', font:{{ family:'IBM Plex Mono', size:11 }} }} }},
            tooltip: {{
              displayColors:false,
              callbacks: {{
                title:function(items) {{ return items[0] ? items[0].raw.nome : ''; }},
                label:function(context) {{
                  var d=context.raw;
                  return ['Quadrante: '+d.quadrante, 'Pressão: '+d.y.toFixed(1)+'/10 · Janela: '+d.janela, 'Custo de espera: '+d.custo+' · '+d.sinais+' sinais'];
                }}
              }}
            }}
          }},
          scales: {{
            x: {{ min:0, max:1, title:{{ display:true, text:'Janela de Decisão — direita = mais urgente', color:'{C_MUTED}' }}, ticks:{{ color:'{C_MUTED}', callback:function(v){{ return v>=.80?'Imediata':v>=.55?'Curta':v>=.28?'Média':v>=.09?'Longa':''; }} }}, grid:{{ color:'rgba(255,255,255,.06)' }} }},
            y: {{ min:0, max:10, title:{{ display:true, text:'Pressão Estratégica — 0 a 10', color:'{C_MUTED}' }}, ticks:{{ color:'{C_MUTED}' }}, grid:{{ color:'rgba(255,255,255,.06)' }} }}
          }}
        }},
        plugins:[quadrantPlugin]
      }});
    }});
  }})();
  </script>
  <div style="font-size:.70rem;color:#718096;font-family:'IBM Plex Mono',monospace;padding:4px 0 0 2px">Período coberto: {periodo_coberto}</div>
</section>"""


def render_capital_panel(data: Dict[str, Any]) -> str:
    vetores = get_vetores(data)
    rows = build_capital_panel(vetores)
    bars = "".join(
        f"""
        <div class="rte5-bar-row">
          <div class="rte5-bar-label">{h(MAT_LABELS[k])}</div>
          <div class="rte5-bar-bg"><div class="rte5-bar-fill" style="--w:{val:.3f}"></div></div>
          <div class="rte5-bar-level">{h(level)}</div>
        </div>
        """
        for k, val, level in rows
    )
    fatos = get_fatos(data)[:5]
    facts_html = "".join(
        f'<div class="rte5-fact"><div class="rte5-fact-value">{h(f.get("valor_literal",""))}</div><div class="rte5-fact-context">{h(f.get("contexto",""))}</div></div>'
        for f in fatos
    )
    if not facts_html:
        facts_html = '<div class="rte5-fact"><div class="rte5-fact-value">—</div><div class="rte5-fact-context">Sem fatos canônicos monetários neste ciclo.</div></div>'
    return f"""
<section class="rte5-section" id="capital">
  <div class="rte5-section-head"><h2 class="rte5-title">Painel de Reprecificação de Capital</h2></div>
  <div class="rte5-capital-panel">
    <div class="rte5-card"><div class="rte5-bars">{bars}</div></div>
    <div class="rte5-card"><div class="rte5-mini-label" style="margin-bottom:10px">Fatos quantitativos do ciclo</div><div class="rte5-facts">{facts_html}</div></div>
  </div>
</section>"""


def render_convergence(data: Dict[str, Any]) -> str:
    clusters = get_clusters(data)
    cards = []
    for c in clusters:
        sinais = c.get("titulos_noticias") or []
        signals_html = "".join(f'<div class="rte5-signal">{h(t)}</div>' for t in sinais)
        signals_block = details_block("Listar sinais relacionados", f'<div class="rte5-signal-list">{signals_html}</div>') if signals_html else ""
        cards.append(f"""
<div class="rte5-card rte5-cluster">
  <div class="rte5-cluster-head">
    <div class="rte5-mini-label">Convergência {h(c.get('convergencia','—'))}</div>
    {chip(f"{c.get('n_sinais', len(sinais))} sinais", C_TERRA_LIGHT)}
  </div>
  <div class="rte5-cluster-name">{h(c.get('nome','Cluster'))}</div>
  <div class="rte5-cluster-tese">{h(c.get('tese',''))}</div>
  {signals_block}
</div>""")
    return f"""
<section class="rte5-section" id="convergencia">
  <div class="rte5-section-head"><h2 class="rte5-title">Motor de Convergência Estratégica</h2></div>
  <div class="rte5-grid-3">{''.join(cards)}</div>
</section>"""


def scenario_color(tipo: str) -> str:
    return {"Risco": C_DANGER, "Oportunidade": C_GREEN, "Misto": C_AMBER}.get(tipo, C_TECH)


def render_scenarios(data: Dict[str, Any]) -> str:
    cenarios = get_cenarios(data)
    cards = []
    for i, c in enumerate(cenarios, 1):
        name = c.get("titulo_cenario") or c.get("nome") or f"Cenário {i}"
        tipo = c.get("tipo", "Misto")
        color = scenario_color(tipo)
        prob = c.get("probabilidade", "—")
        impacto = c.get("impacto", "—")
        desc = c.get("descricao_expandida") or c.get("narrativa_macro") or c.get("narrativa") or ""
        diretriz = c.get("diretriz_acao_brasil") or ""
        action = f'<div class="rte5-scenario-action"><strong>Diretriz de ação Brasil:</strong> {h(diretriz)}</div>' if diretriz else ""
        cards.append(f"""
<div class="rte5-scenario" style="--c:{color}">
  <div class="rte5-scenario-top">
    <div class="rte5-scenario-num">{h(c.get('numero', i))}</div>
    <div>
      <div class="rte5-scenario-name">{h(name)}</div>
      <div class="rte5-scenario-meta">Probabilidade: {h(prob)}% · Impacto: {h(impacto)} · Tipo: {h(tipo)}</div>
    </div>
  </div>
  <div class="rte5-scenario-text">{h(desc)}</div>
  {action}
</div>""")
    return f"""
<section class="rte5-section" id="cenarios">
  <div class="rte5-section-head"><h2 class="rte5-title">Cenários Prospectivos</h2></div>
  <div class="rte5-grid-3">{''.join(cards)}</div>
</section>"""


def render_briefing(data: Dict[str, Any]) -> str:
    briefing = get_briefing(data)
    dash = get_dashboard(data)
    title = briefing.get("titulo") or "Briefing Executivo"
    sub = briefing.get("subtitulo") or "Leitura executiva do ciclo"
    opening = briefing.get("frase_de_abertura") or dash.get("briefing_executivo") or ""
    paras = briefing.get("paragrafos") or []
    segments = []
    for p in paras:
        decision = p.get("decisao_implicada") or ""
        note = f'<div class="rte5-decision-note"><strong>Decisão implicada:</strong> {h(decision)}</div>' if decision else ""
        segments.append(f"""
<div class="rte5-brief-segment">
  <div class="rte5-brief-horizon">Horizonte: {h(p.get('horizonte_decisao','—'))}</div>
  <p class="rte5-brief-p">{h(p.get('texto',''))}</p>
  {note}
</div>""")
    cross = briefing.get("implicacao_cruzada") or ""
    cross_html = f'<div class="rte5-cross"><strong>Implicação cruzada:</strong> {h(cross)}</div>' if cross else ""
    return f"""
<section class="rte5-section" id="narrativa-executiva">
  <div class="rte5-section-head"><h2 class="rte5-title">Narrativa Executiva Dinâmica</h2></div>
  <div class="rte5-briefing">
    <h2 class="rte5-brief-title">{h(title)}</h2>
    <div class="rte5-brief-sub">{h(sub)}</div>
    <p class="rte5-brief-opening">{h(opening)}</p>
    {''.join(segments)}
    {cross_html}
  </div>
</section>"""


def render_footer(data: Dict[str, Any]) -> str:
    dash = get_dashboard(data)
    ciclo = dash.get("ciclo") or data.get("ciclo_id") or "—"
    gen = data.get("gerado_em") or datetime.now(BRASILIA).isoformat()
    return f"""
<div class="rte5-footer">
  <span>Efagundes Intelligence Engine · Radar xTechs</span>
  <span>Ciclo {h(ciclo)} · Gerado em {h(gen)}</span>
</div>"""


# ─── Novas seções v6 ──────────────────────────────────────────────────────────

def render_curva_convergencia(hist_data: Dict[str, Any]) -> str:
    """Curva de Maturidade da Convergência Tecnológica — modelo proprietário xTechs."""
    tech_signals = hist_data.get("tech_signals") or {}

    # Usa hype_cycle_live se gerado pelo hype_cycle_updater.py (dados dinâmicos)
    # Caso contrário, fallback para HYPE_TECHS estático com n/score do banco
    hype_cycle_live = hist_data.get("hype_cycle_live") or []
    if hype_cycle_live:
        techs_js = [
            {
                "id":     t["id"],
                "frente": t["frente"],
                "color":  t["color"],
                "x":      t["cx"],
                "n":      t["n"],
                "score":  t["score"],
                "trend":  t["trend"],
                "signal": t["signal"],
                "phase":  t.get("phase", ""),
                "above":  t.get("above", True),
                "updated": t.get("updated", False),
            }
            for t in hype_cycle_live
        ]
    else:
        techs_js = []
        for i, t in enumerate(HYPE_TECHS):
            n     = tech_signals.get(t["id"], t["default_n"])
            score = (hist_data.get("tech_scores") or {}).get(t["id"], t["score_default"])
            techs_js.append({
                "id":    t["id"],
                "frente": t["frente"],
                "color":  t["color"],
                "x":      t["cx"],
                "n":      n,
                "score":  round(score, 2),
                "trend":  t["trend"],
                "signal": t["signal"],
                "phase":  t.get("phase", ""),
                "above":  i % 2 == 0,
                "updated": False,
            })

    cycle_date = hist_data.get("cycle_date", "")
    techs_json = json.dumps(techs_js, ensure_ascii=False)

    # Curva de maturação em ondas (proprietária)
    curve_pts_json = json.dumps([
        [0.00,0.08],[0.08,0.18],[0.16,0.34],[0.24,0.58],[0.32,0.78],
        [0.40,0.88],[0.48,0.56],[0.56,0.34],[0.64,0.42],[0.72,0.58],
        [0.80,0.72],[0.88,0.76],[0.96,0.62],[1.00,0.52]
    ])
    stages_json = json.dumps([
        {"x":0.00,"label":"Sinal Emergente",       "sub":"sinais fracos"},
        {"x":0.14,"label":"Narrativa Exponencial",  "sub":"tese ganha mercado"},
        {"x":0.30,"label":"Pico de Especulação",    "sub":"capital antecipa ROI"},
        {"x":0.45,"label":"Fricção Operacional",    "sub":"CAPEX · regulação · legado"},
        {"x":0.62,"label":"Escala Econômica",       "sub":"casos repetíveis"},
        {"x":0.78,"label":"Infra. Crítica",  "sub":"dependência sistêmica"},
        {"x":0.91,"label":"Commodity",       "sub":"eficiência e custo"},
    ])

    intro_html = (
        f'<div style="margin-bottom:14px;padding:13px 16px;background:{C_BG2};'
        f'border-left:3px solid {C_TERRA_LIGHT};border-radius:0 10px 10px 0;'
        f'font-size:.83rem;color:{C_MUTED};line-height:1.7;">'
        f'<strong style="color:{C_TEXT};">Leitura executiva:</strong> '
        f'esta curva acompanha a passagem de tecnologias de sinais fracos para infraestrutura '
        f'econômica crítica e, por fim, commoditização. O posicionamento combina volume acumulado '
        f'de sinais, score médio, tendência semanal, fricção regulatória, disponibilidade de capital, '
        f'maturidade operacional e evidência de adoção econômica. '
        f'<strong style="color:{C_TEXT};">Nota:</strong> '
        f'maturidade de infraestrutura de mercado (posição na curva) é independente de pressão estratégica do ciclo (IPS). '
        f'Uma frente como FinTech pode liderar em maturidade operacional e ter IPS baixo — porque a tecnologia já é '
        f'infraestrutura consolidada, não emergência. EnergyTech pode ter IPS alto por pressão regulatória e de mercado '
        f'mesmo com tecnologias em estágios intermediários da curva.'
        f'</div>'
    )

    # JS usando string concat — sem conflito de {{ }} com f-string
    js = (
        "<script>\n(function(){\n"
        "  const TECHS = " + techs_json + ";\n"
        "  const CURVE_PTS = " + curve_pts_json + ";\n"
        "  const STAGES = " + stages_json + ";\n"
        r"""
  const W=1060, H=520, margin={top:58,right:34,bottom:96,left:46};
  const iw=W-margin.left-margin.right, ih=H-margin.top-margin.bottom;

  const fills=[
    'rgba(47,168,124,.07)','rgba(91,140,255,.09)','rgba(217,164,65,.09)',
    'rgba(217,108,108,.08)','rgba(93,202,165,.07)','rgba(121,163,255,.07)','rgba(113,128,150,.07)'
  ];

  window._efLoadD3 = window._efLoadD3 || function(cb) {
    if (window.d3) { cb(); return; }
    window._efD3Queue = window._efD3Queue || [];
    window._efD3Queue.push(cb);
    if (window._efD3Loading) return;
    window._efD3Loading = true;
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js";
    s.onload = function() {
      window._efD3Loading = false;
      (window._efD3Queue||[]).forEach(f => { try{f();}catch(e){} });
      window._efD3Queue = [];
    };
    document.head.appendChild(s);
  };

  window._efLoadD3(function() {
    const svg = d3.select("#rte5-ctc-svg");
    const g   = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    const detail = document.getElementById("rte5-ctc-detail");

    const x = d3.scaleLinear().domain([0,1]).range([0,iw]);
    const y = d3.scaleLinear().domain([0,1]).range([ih,0]);

    function curveY(cx) {
      for (let i=0; i<CURVE_PTS.length-1; i++) {
        const a=CURVE_PTS[i], b=CURVE_PTS[i+1];
        if (cx>=a[0]&&cx<=b[0]) { const t=(cx-a[0])/(b[0]-a[0]); return a[1]+t*(b[1]-a[1]); }
      }
      return CURVE_PTS[CURVE_PTS.length-1][1];
    }

    // Faixas de estágio
    STAGES.forEach((s,i) => {
      const x0=x(s.x), x1=i+1<STAGES.length?x(STAGES[i+1].x):x(1);
      g.append("rect").attr("x",x0).attr("y",0).attr("width",x1-x0).attr("height",ih).attr("fill",fills[i]);
      g.append("line").attr("x1",x0).attr("x2",x0).attr("y1",0).attr("y2",ih).attr("stroke","rgba(255,255,255,.07)");
      g.append("text").attr("x",x0+7).attr("y",ih+30)
        .attr("fill","#A8B3C2").attr("font-size","10px").attr("font-family","IBM Plex Mono,monospace")
        .attr("font-weight","600").text(s.label);
      g.append("text").attr("x",x0+7).attr("y",ih+46)
        .attr("fill","#718096").attr("font-size","8px").attr("font-family","IBM Plex Mono,monospace")
        .text(s.sub);
    });

    // Linha de base
    g.append("line").attr("x1",0).attr("x2",iw).attr("y1",ih).attr("y2",ih).attr("stroke","rgba(255,255,255,.13)");

    // Eixos
    g.append("text").attr("x",iw/2).attr("y",ih+75).attr("text-anchor","middle")
      .attr("fill","#718096").attr("font-size","10px").attr("font-family","IBM Plex Mono,monospace")
      .text("Maturidade de mercado e integração infraestrutural");
    g.append("text").attr("transform","rotate(-90)").attr("x",-ih/2).attr("y",-30).attr("text-anchor","middle")
      .attr("fill","#718096").attr("font-size","10px").attr("font-family","IBM Plex Mono,monospace")
      .text("Valor sistêmico e pressão estratégica");

    // Curva principal + halo
    const line = d3.line().x(d=>x(d[0])).y(d=>y(d[1])).curve(d3.curveCatmullRom.alpha(.55));
    g.append("path").datum(CURVE_PTS).attr("d",line)
      .attr("fill","none").attr("stroke","rgba(121,163,255,.18)").attr("stroke-width",10).attr("opacity",.6)
      .style("filter","drop-shadow(0 0 9px rgba(121,163,255,.28))");
    g.append("path").datum(CURVE_PTS).attr("d",line)
      .attr("fill","none").attr("stroke","#79A3FF").attr("stroke-width",3).attr("opacity",.82);

    // Tecnologias
    TECHS.forEach(t => {
      t.px = x(t.x);
      t.py = y(curveY(t.x));
      t.r  = Math.min(8.5, 3.4 + Math.log10(t.n+1)*2.55);
    });

    const item = g.selectAll("g.ctc-tech").data(TECHS).enter().append("g").attr("class","ctc-tech");

    item.append("line")
      .attr("x1",d=>d.px).attr("x2",d=>d.px)
      .attr("y1",d=>d.py).attr("y2",d=>d.above?d.py-25:d.py+29)
      .attr("stroke",d=>d.color).attr("stroke-width",1).attr("opacity",.45);

    item.append("circle")
      .attr("cx",d=>d.px).attr("cy",d=>d.py).attr("r",d=>d.r)
      .attr("fill",d=>d.color).attr("fill-opacity",.9)
      .attr("stroke","#0F1722").attr("stroke-width",1.8)
      .style("cursor","pointer")
      .style("filter","drop-shadow(0 0 8px rgba(91,140,255,.18))")
      .on("mouseover", show).on("click", show);

    item.append("text")
      .attr("x",d=>d.px).attr("y",d=>d.above?d.py-31:d.py+43)
      .attr("fill",d=>d.color).attr("text-anchor","middle")
      .attr("font-size","9px").attr("font-weight","600")
      .attr("font-family","IBM Plex Mono,monospace")
      .style("cursor","pointer")
      .text(d=>d.id+" "+({"↗":"+","↘":"-","→":"="}[d.trend]||d.trend))
      .on("mouseover", show).on("click", show);

    function show(ev, d) {
      if (!detail) return;
      detail.innerHTML =
        `<strong style="color:${d.color};font-size:.92rem;">${d.id}</strong>`
        + ` &nbsp;<span style="color:#718096;font-family:'IBM Plex Mono',monospace;">${d.trend} · ${d.frente}</span>`
        + `<br><span style="color:#718096;font-family:'IBM Plex Mono',monospace;font-size:.75rem;">`
        + `Fase: ${d.phase} · ${d.n} sinais · score ${d.score}</span>`
        + `<br>${d.signal}`;
    }
  });
})();
</script>"""
    )

    return (
        f'<section class="rte5-section" id="curva-convergencia">\n'
        f'  <div class="rte5-section-head">\n'
        f'    <h2 class="rte5-title">Curva de Maturidade da Convergência Tecnológica — xTechs</h2>\n'
        f'    <span class="rte5-note">'
        + (f'{cycle_date} · ' if cycle_date else '')
        + f'21 tecnologias · 7 estágios · valor sistêmico</span>\n'
        f'  </div>\n'
        f'  <div class="rte5-card" style="padding:16px 20px 18px;">\n'
        + intro_html
        + f'    <div style="border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,.06);">\n'
        f'      <svg id="rte5-ctc-svg" viewBox="0 0 1060 520" preserveAspectRatio="xMidYMid meet"\n'
        f'           style="display:block;width:100%;height:auto;background:#0F1722;"></svg>\n'
        f'    </div>\n'
        f'    <div style="font-size:.70rem;color:{C_WEAK};text-align:right;margin-top:5px;'
        f'font-family:\'IBM Plex Mono\',monospace;">passe o mouse ou clique em uma tecnologia para ver o racional analítico</div>\n'
        f'    <div id="rte5-ctc-detail" style="margin-top:10px;padding:14px 16px;background:{C_BG2};'
        f'border-radius:12px;font-size:.84rem;color:{C_MUTED};line-height:1.65;min-height:96px;'
        f'border:1px solid rgba(255,255,255,.06);">\n'
        f'      <span style="color:{C_WEAK};font-family:\'IBM Plex Mono\',monospace;font-size:.75rem;">'
        f'▸ Selecione uma tecnologia para ver o sinal analítico, a fase e a implicação estratégica.</span>\n'
        f'    </div>\n'
        f'  </div>\n'
        + js
        + '\n</section>'
    )


def _graph_layout(nodes: list, links: list) -> dict:
    """Calcula posições dos nós em Python — sem D3, sem JS assíncrono."""
    import math
    W, H = 960, 600
    cx, cy = 480, 295

    hub_order = ["EnergyTech", "DeepTech", "FinTech", "CleanTech", "AgriTech"]
    HUB_R = 195
    pos: dict = {}
    for i, name in enumerate(hub_order):
        ang = -math.pi / 2 + (2 * math.pi * i / 5)
        pos[f"frente_{name}"] = (cx + HUB_R * math.cos(ang), cy + HUB_R * math.sin(ang))

    hub_ids = set(pos.keys())
    node_hubs: dict = {}
    for lk in links:
        s, t = lk["source"], lk["target"]
        if t in hub_ids and s not in hub_ids:
            node_hubs.setdefault(s, []).append(t)
        elif s in hub_ids and t not in hub_ids:
            node_hubs.setdefault(t, []).append(s)

    # Agrupa subnós por hub para distribuição em arco
    hub_buckets: dict = {}
    for nd in nodes:
        nid = nd["id"]
        if nid in hub_ids:
            continue
        hubs = node_hubs.get(nid, [])
        hub = hubs[0] if hubs else None
        if hub:
            hub_buckets.setdefault(hub, []).append(nid)

    # Distribui subnós em arco apontando para fora do centro
    for hub, nids in hub_buckets.items():
        hx, hy = pos[hub]
        base_ang = math.atan2(hy - cy, hx - cx)
        spread_deg = min(200, 38 * len(nids))
        spread = spread_deg * math.pi / 180
        total = max(len(nids), 1)
        for i, nid in enumerate(nids):
            nd = next(n for n in nodes if n["id"] == nid)
            step = spread / total if total > 1 else 0
            ang = base_ang - spread / 2 + step / 2 + i * step
            nd_type = nd.get("type", "zettel")
            sub_r = 95 if nd_type == "memory" else 78 if nd_type == "entity" else 110
            px = max(nd["r"] + 55, min(W - nd["r"] - 55, hx + sub_r * math.cos(ang)))
            py = max(nd["r"] + 24, min(H - nd["r"] - 24, hy + sub_r * math.sin(ang)))
            pos[nid] = (px, py)

    # nós sem hub: distribuir ao redor do canvas
    for nd in nodes:
        nid = nd["id"]
        if nid not in pos:
            idx = len(pos)
            ang = (2 * math.pi * idx / 12)
            pos[nid] = (cx + 240 * math.cos(ang), cy + 240 * math.sin(ang))

    return pos


def _short_label(title: str, max_len: int = 52) -> str:
    """Extrai label curto para o grafo: divide em separadores semânticos antes de truncar."""
    # Tenta separadores em ordem de preferência — retorna antes do separador
    for sep in [" — ", " – ", ": "]:
        if sep in title:
            part = title.split(sep)[0].strip()
            if len(part) <= max_len:
                return part
    # Divide em " & "
    if " & " in title:
        part = title.split(" & ")[0].strip()
        if len(part) <= max_len:
            return part
    # Sem separador: retorna completo se couber, senão corta na última palavra
    if len(title) <= max_len:
        return title
    truncated = title[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len - 14:
        return truncated[:last_space] + "…"
    return truncated + "…"


def render_intel_graph(hist_data: Dict[str, Any]) -> str:
    """Grafo D3 dinâmico — zoom, drag, física. WordPress-safe via shared loader e viewBox fixo."""
    memories = hist_data.get("memories") or FALLBACK_MEMORIES
    zettels   = hist_data.get("zettels")  or FALLBACK_ZETTELS
    entities  = hist_data.get("entities") or FALLBACK_ENTITIES

    # Detalhes analíticos dos hubs (5 frentes)
    hub_details: dict = {
        "EnergyTech": {
            "sinais": 820, "fatos": 38,
            "desc": "Frente mais densa do banco — 820+ sinais acumulados. Cobre geração, transmissão, BESS, mercado livre e regulação setorial.",
            "analise": "EnergyTech concentra os temas de maior impacto no ciclo atual: BESS em decolagem regulatória (ANEEL autorizou primeira unidade), Data Centers como novo vetor de demanda elétrica (201 sinais, score 4.48 em queda pós-pico), insegurança jurídica no mercado livre (LRCap homologado apesar de liminar estadual) e modernização do ONS com ferramentas eletroenergéticas. O sub-tema mais crítico é a coexistência entre segurança regulatória federal e contestação judicial local — risco que se converteu de abstrato para concreto com a suspensão cautelar no caso Electra.",
            "acao": "Mapear exposição de contratos CCEE a liminares ativas; estruturar SPE para leilão BESS 2026-27; incluir prêmio de risco jurídico nos modelos de CAPEX.",
        },
        "CleanTech": {
            "sinais": 310, "fatos": 14,
            "desc": "Transição energética limpa — H₂ verde, lítio, mobilidade elétrica e descarbonização industrial.",
            "analise": "Score caiu de 8.75 (W18) para 5.75 (W23) — a tese do H₂ verde continua válida mas a janela de política industrial está se fechando. MME realizou intercâmbio técnico sobre lítio (abr/2026) mas portaria com roadmap H₂ verde ainda pendente. Custo LFP caiu 40% desde 2022, tornando BESS competitivo mesmo sem subsídio. Mobilidade elétrica pressiona por infraestrutura de carga — integração com smart grid (V2G) é o diferencial competitivo emergente.",
            "acao": "Pressionar por portaria MME com cronograma H₂ verde antes de Q1 2027; articular linha BNDES específica; modelar V2G para frotas.",
        },
        "AgriTech": {
            "sinais": 185, "fatos": 9,
            "desc": "Agronegócio digital — precision farming, crédito rural open finance, sensores IoT e monitoramento climático.",
            "analise": "SELIC elevada comprime crédito rural convencional, criando oportunidade para fintechs de crédito agrícola baseadas em Open Finance. Embrapa Carbon+ abre nova vertente de monetização de carbono no agro. Satélites LEO (Starlink) desbloqueiam IoT em regiões remotas sem cobertura 5G. Risco climático crescente aumenta demanda por modelos preditivos de produtividade — janela para SaaS agro premium.",
            "acao": "Mapear editais de parceria Embrapa; avaliar Embrapa Carbon+ para créditos de carbono; piloto de crédito rural via Open Finance Phase 4.",
        },
        "DeepTech": {
            "sinais": 420, "fatos": 18,
            "desc": "Tecnologias profundas — IA aplicada, 5G industrial, computação de borda, cibersegurança e regulação digital.",
            "analise": "IA em infraestrutura crítica é o sub-tema mais denso (201 sinais). Score de DCs caiu 47% em 7 semanas por pressão tributária e tarifária — estrutural, não pontual. 5G industrial avança em corredores logísticos (Anatel liberou faixas em 2025). PL de IA em fase final no Congresso — aprovação esperada 2026-27. Cibersegurança em infraestrutura crítica volta à agenda após incidentes internacionais. Antecipação regulatória é o diferencial competitivo.",
            "acao": "Engajar processo legislativo do PL IA; preparar framework de governança de IA; piloto 5G industrial em corredor logístico prioritário.",
        },
        "FinTech": {
            "sinais": 255, "fatos": 11,
            "desc": "Finanças digitais — Open Finance, tokenização de ativos energéticos, crédito alternativo e infraestrutura de pagamentos.",
            "analise": "Pix atingiu 9 bilhões de transações/mês — infraestrutura de pagamentos madura. Open Finance Phase 4 (dados de investimentos) desbloqueará casos de uso de wealth management e crédito rural personalizado. CVM avaliando sandbox para tokens de utilidade energética e RECs tokenizados — aprovação esperada 2026-27. SELIC acima de 13% cria spread bancário elevado que favorece fintechs de crédito alternativo. BCB mantém postura pró-inovação.",
            "acao": "Acompanhar Open Finance Phase 4; preparar estrutura jurídica para tokens de utilidade energética; monitorar sandbox CVM.",
        },
    }

    # ── Construir lista de nós e links ──────────────────────────────────────
    nodes: list = []
    links: list = []
    node_details: dict = {}  # id → enriched dict for JS

    for frente, color in FRENTE_COLORS.items():
        nid = f"frente_{frente}"
        hd = hub_details.get(frente, {})
        nodes.append({"id": nid, "label": frente, "type": "frente",
                      "color": color, "r": 26, "fronts": [frente]})
        node_details[nid] = {
            "label": frente, "type": "frente", "color": color,
            "sinais": hd.get("sinais", 0), "fatos": hd.get("fatos", 0),
            "desc": hd.get("desc", ""), "analise": hd.get("analise", ""),
            "acao": hd.get("acao", ""),
        }

    for m in memories[:14]:
        nid = f"mem_{m['id']}"
        fronts = m.get("fronts") or []
        label = m["title"][:70]
        strength = float(m.get("strength") or 0.5)
        nodes.append({"id": nid, "label": label, "type": "memory",
                      "color": C_PURPLE, "r": 18, "fronts": fronts,
                      "slabel": m.get("slabel"), "strength": strength})
        node_details[nid] = {
            "label": label, "type": "memory", "color": C_PURPLE,
            "forca": strength, "sinais": m.get("sinais", 0),
            "fatos": m.get("fatos", 0), "desc": m.get("desc", ""),
            "analise": m.get("analise", ""), "acao": m.get("acao", ""),
        }
        for fr in fronts:
            if fr in FRENTE_COLORS:
                links.append({"source": nid, "target": f"frente_{fr}", "weak": False, "weight": strength})

    for z in zettels[:14]:
        nid = f"zet_{z['id']}"
        fronts = z.get("fronts") or []
        label = z["title"][:70]
        strength = float(z.get("strength") or 0.5)
        nodes.append({"id": nid, "label": label, "type": "zettel",
                      "color": C_TECH, "r": 12, "fronts": fronts,
                      "slabel": z.get("slabel"), "strength": strength})
        node_details[nid] = {
            "label": label, "type": "zettel", "color": C_TECH,
            "forca": strength, "sinais": z.get("sinais", 0),
            "desc": z.get("desc", ""), "analise": z.get("analise", ""),
            "acao": z.get("acao", ""),
        }
        for fr in fronts:
            if fr in FRENTE_COLORS:
                links.append({"source": nid, "target": f"frente_{fr}", "weak": False, "weight": strength})
        if z.get("memory"):
            mem_ref = z["memory"]
            if isinstance(mem_ref, int) or (isinstance(mem_ref, str) and len(mem_ref) < 60 and " " not in str(mem_ref)):
                links.append({"source": nid, "target": f"mem_{mem_ref}", "weak": True, "weight": strength * 0.6})

    for e in entities[:10]:
        nid = f"ent_{e['id']}"
        fronts = e.get("fronts") or []
        label = e["label"]
        importance = float(e.get("importance") or 0.5)
        nodes.append({"id": nid, "label": label, "type": "entity",
                      "color": C_DANGER, "r": 14, "fronts": fronts,
                      "strength": importance})
        node_details[nid] = {
            "label": label, "type": "entity", "color": C_DANGER,
            "forca": importance, "sinais": e.get("sinais", 0),
            "fatos": e.get("fatos", 0), "desc": e.get("desc", ""),
            "analise": e.get("analise", ""), "acao": e.get("acao", ""),
        }
        for fr in fronts:
            if fr in FRENTE_COLORS:
                links.append({"source": nid, "target": f"frente_{fr}", "weak": False, "weight": importance})

    # Cross-hub links via multi-front memories
    hub_pair_done: set = set()
    for nd in nodes:
        if nd["type"] == "memory" and len(nd.get("fronts") or []) >= 2:
            fronts_list = nd["fronts"]
            for i, fa in enumerate(fronts_list):
                for fb in fronts_list[i+1:]:
                    if fa in FRENTE_COLORS and fb in FRENTE_COLORS:
                        key = tuple(sorted([fa, fb]))
                        if key not in hub_pair_done:
                            hub_pair_done.add(key)
                            links.append({"source": f"frente_{fa}", "target": f"frente_{fb}", "weak": False})

    # ── Filtrar nós órfãos (sem nenhum link) — exceto hubs de frente ───────────
    linked_ids: set = set()
    for lk in links:
        linked_ids.add(lk["source"])
        linked_ids.add(lk["target"])
    nodes = [nd for nd in nodes if nd["type"] == "frente" or nd["id"] in linked_ids]
    # Remove links que referenciam nós que não existem mais
    node_id_set = {nd["id"] for nd in nodes}
    links = [lk for lk in links if lk["source"] in node_id_set and lk["target"] in node_id_set]

    # ── Serializar para D3 ───────────────────────────────────────────────────
    d3_nodes = [
        {"id": nd["id"], "label": nd["label"],
         "slabel": nd["label"] if nd["type"] == "frente"
                   else nd.get("slabel") or _short_label(nd["label"]),
         "type": nd["type"], "color": nd["color"], "r": nd["r"],
         "fronts": nd.get("fronts") or []}
        for nd in nodes
    ]
    d3_links = [
        {"source": lk["source"], "target": lk["target"],
         "weak": lk.get("weak", False),
         "weight": round(float(lk.get("weight") or 0.5), 3)}
        for lk in links
    ]
    nodes_json     = json.dumps(d3_nodes, ensure_ascii=False)
    links_json     = json.dumps(d3_links, ensure_ascii=False)
    node_data_json = json.dumps(node_details, ensure_ascii=False)

    W, H = 960, 460
    bg_color   = C_BG
    bg2_color  = C_BG2
    muted_color = C_MUTED
    weak_color  = C_WEAK
    terra_color = C_TERRA_LIGHT

    # Filtros HTML (Python f-string pequena)
    filter_btns = (
        f'<button class="rte5-filter-btn rte5-filter-active" '
        f'style="border-color:#79A3FF55;background:#79A3FF18;color:#AFC4FF;" '
        f'onclick="rte5GrFilter(null,this)">Todas as frentes</button>'
    )
    for fr, c in FRENTE_COLORS.items():
        filter_btns += (
            f'<button class="rte5-filter-btn" '
            f'style="border-color:{c}55;background:{c}18;color:{c};" '
            f'onclick="rte5GrFilter(\'{fr}\',this)">{fr}</button>'
        )

    legend_html = " &nbsp;".join(
        f'<span style="color:{c};font-size:.95rem;">●</span>'
        f'<span style="font-size:.71rem;color:{C_MUTED};">{lbl}</span>'
        for lbl, c in [("Frente xTech","#2FA87C"),("Memória",C_PURPLE),("Zettel",C_TECH),("Entidade",C_DANGER)]
    )

    # JS separado para não ter conflito de {{ }} com f-string
    js = (
        "<script>\n"
        "(function() {\n"
        "  const detail = " + node_data_json + ";\n"
        "  const nodesRaw = " + nodes_json + ";\n"
        "  const linksRaw = " + links_json + ";\n"
        r"""
  const W = 960, H = 460;
  const nodeById = Object.fromEntries(nodesRaw.map(n => [n.id, n]));
  const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function breathingForce() {
    if (prefersReducedMotion) return;
    const t = performance.now() * 0.001;
    nodesRaw.forEach((d, i) => {
      if (d.fx != null || d.fy != null) return;
      const amp = d.type === "frente" ? 0.018 : 0.030;
      d.vx += Math.sin(t * 0.75 + i * 1.91) * amp;
      d.vy += Math.cos(t * 0.62 + i * 1.37) * amp;
    });
  }

  // Posição inicial — hubs em pentágono, sub-nós ao redor do hub pai
  const hubOrder = ["EnergyTech","DeepTech","FinTech","CleanTech","AgriTech"];
  const hubR = 205;
  hubOrder.forEach((fr, i) => {
    const n = nodeById["frente_" + fr];
    const ang = -Math.PI/2 + 2*Math.PI*i/5;
    if (n) { n.x = W/2 + hubR * Math.cos(ang); n.y = H/2 + hubR * Math.sin(ang); }
  });
  nodesRaw.forEach(n => {
    if (n.type === "frente") return;
    const hub = nodeById["frente_" + (n.fronts[0] || "EnergyTech")];
    const a = Math.random() * Math.PI * 2;
    const dist = 75 + Math.random() * 75;
    n.x = (hub?.x || W/2) + dist * Math.cos(a);
    n.y = (hub?.y || H/2) + dist * Math.sin(a);
  });

  window._efLoadD3 = window._efLoadD3 || function(cb) {
    if (window.d3) { cb(); return; }
    window._efD3Queue = window._efD3Queue || [];
    window._efD3Queue.push(cb);
    if (window._efD3Loading) return;
    window._efD3Loading = true;
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js";
    s.onload = function() {
      window._efD3Loading = false;
      (window._efD3Queue || []).forEach(f => { try { f(); } catch(e) {} });
      window._efD3Queue = [];
    };
    document.head.appendChild(s);
  };

  window._efLoadD3(function() {
    const svg  = d3.select("#rte5-gr-svg");
    const root = d3.select("#rte5-gr-g");

    const zoom = d3.zoom().scaleExtent([0.25, 5])
      .on("zoom", ev => { root.attr("transform", ev.transform); });
    svg.call(zoom).on("dblclick.zoom", null);
    svg.on("dblclick", () => svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity));

    const links = linksRaw.map(l => ({ ...l }));

    const link = root.append("g").selectAll("line")
      .data(links).enter().append("line")
      .attr("class", "rte5-gr-link")
      .attr("data-fronts", d => linkFronts(d).join(","))
      .attr("data-weight", d => (d.weight||0.5).toFixed(2))
      .attr("stroke-width", d => {
        if (isHubLink(d)) return 1.1;
        const w = d.weight || 0.5;
        return Math.max(0.7, Math.min(4.2, w * 4.5));
      })
      .attr("stroke-dasharray", d => isHubLink(d) ? "5,5" : (d.weak ? "3,4" : null))
      .attr("stroke", d => {
        const s = nodeById[typeof d.source==="object" ? d.source.id : d.source];
        const t = nodeById[typeof d.target==="object" ? d.target.id : d.target];
        if (isHubLink({source:s,target:t})) return "rgba(255,255,255,.18)";
        return s?.type === "frente" ? s.color : t?.color;
      })
      .attr("opacity", d => {
        if (isHubLink(d)) return 0.18;
        const w = d.weight || 0.5;
        return Math.max(0.12, Math.min(0.65, 0.12 + w * 0.55));
      });

    const node = root.append("g").selectAll("g")
      .data(nodesRaw).enter().append("g")
      .attr("class", "rte5-gr-node")
      .attr("data-fronts", d => d.fronts.join(","))
      .style("cursor", "pointer")
      .on("click", (ev, d) => window.rte5GrInfo(d.id))
      .on("mouseenter", (ev, d) => highlightNeighborhood(d, true))
      .on("mouseleave", (ev, d) => highlightNeighborhood(d, false))
      .call(d3.drag()
        .on("start", dragStarted)
        .on("drag",  dragged)
        .on("end",   dragEnded)
      );

    node.append("circle")
      .attr("r",            d => d.r)
      .attr("fill",         d => d.color)
      .attr("fill-opacity", d => d.type === "frente" ? .92 : .84)
      .attr("stroke",       d => d.type === "frente" ? d.color : "#0F1722")
      .attr("stroke-width", d => d.type === "frente" ? 2.8 : 1.8)
      .style("filter",      d => d.type === "frente" ? `drop-shadow(0 0 10px ${d.color}66)` : "none");

    node.filter(d => d.type === "frente").append("text")
      .attr("text-anchor", "middle").attr("dominant-baseline", "middle")
      .attr("font-size", "10.5").attr("font-weight", "800")
      .attr("font-family", "IBM Plex Mono,monospace")
      .attr("fill", "#E6EDF3").attr("pointer-events", "none")
      .text(d => d.label);

    const sub = node.filter(d => d.type !== "frente");
    sub.append("text").attr("class", "rte5-gr-label")
      .attr("font-size", "8.5").attr("font-weight", "600")
      .attr("font-family", "Inter,sans-serif")
      .attr("fill", "#E6EDF3").attr("fill-opacity", .92)
      .attr("pointer-events", "none")
      .each(function(d) {
        const lbl = d.slabel || d.label;
        const words = lbl.split(" ");
        const el = d3.select(this);
        if (lbl.length <= 16 || words.length <= 2) {
          el.text(lbl);
        } else if (lbl.length <= 32 || words.length <= 4) {
          // 2 linhas
          const mid = Math.ceil(words.length / 2);
          el.append("tspan").attr("x", 0).attr("dy", "-0.65em").text(words.slice(0, mid).join(" "));
          el.append("tspan").attr("x", 0).attr("dy", "1.20em").text(words.slice(mid).join(" "));
        } else {
          // 3 linhas para labels mais longos
          const t1 = Math.ceil(words.length / 3);
          const t2 = Math.ceil(2 * words.length / 3);
          el.append("tspan").attr("x", 0).attr("dy", "-1.15em").text(words.slice(0, t1).join(" "));
          el.append("tspan").attr("x", 0).attr("dy", "1.20em").text(words.slice(t1, t2).join(" "));
          el.append("tspan").attr("x", 0).attr("dy", "1.20em").text(words.slice(t2).join(" "));
        }
      });

    // Grau de cada nó (nº de links) — usado para escalar repulsão de hubs sobrecarregados
    const nodeDegree = {};
    links.forEach(l => {
      const tid = typeof l.target === "object" ? l.target.id : l.target;
      nodeDegree[tid] = (nodeDegree[tid] || 0) + 1;
    });

    const simulation = d3.forceSimulation(nodesRaw)
      .force("link", d3.forceLink(links).id(d => d.id)
        .distance(d => isHubLink(d) ? 340 : 120)
        .strength(d => isHubLink(d) ? .03 : .42))
      .force("charge", d3.forceManyBody()
        .strength(d => {
          if (d.type !== "frente") return -280;
          // Escala repulsão pelo grau: hubs com muitos links recebem carga maior
          const deg = nodeDegree[d.id] || 1;
          return -600 - deg * 28;
        })
        .distanceMax(500))
      .force("center", d3.forceCenter(W/2, H/2))
      .force("x", d3.forceX(W/2).strength(.03))
      .force("y", d3.forceY(H/2).strength(.03))
      .force("collision", d3.forceCollide().radius(d => d.type === "frente" ? d.r + 38 : d.r + 18).strength(1.0))
      .force("breathing", breathingForce)
      .velocityDecay(.28)
      .alphaDecay(.003)
      .alphaTarget(prefersReducedMotion ? 0 : .035);

    simulation.on("tick", () => {
      link
        .attr("x1", d => d.source.x).attr("y1", d => d.source.y)
        .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("transform", d => `translate(${d.x},${d.y})`);
      sub.each(function(d) {
        const hub = nodeById["frente_" + (d.fronts[0] || "EnergyTech")];
        const dx = d.x - (hub?.x || W/2), dy = d.y - (hub?.y || H/2);
        const dist = Math.max(Math.sqrt(dx*dx+dy*dy), 1);
        const ux = dx/dist, uy = dy/dist;
        const lx = ux*(d.r+13), ly = uy*(d.r+13);
        const anchor = Math.abs(ux)<.22 ? "middle" : (ux>0 ? "start" : "end");
        const lel = d3.select(this).select(".rte5-gr-label");
        lel.attr("x",lx).attr("y",ly+4).attr("text-anchor",anchor);
        lel.selectAll("tspan").attr("x",lx);
      });
    });

    function isHubLink(d) {
      const s = typeof d.source==="object" ? d.source : nodeById[d.source];
      const t = typeof d.target==="object" ? d.target : nodeById[d.target];
      return s?.type==="frente" && t?.type==="frente";
    }
    function linkFronts(d) {
      const s = typeof d.source==="object" ? d.source : nodeById[d.source];
      const t = typeof d.target==="object" ? d.target : nodeById[d.target];
      return Array.from(new Set([...(s?.fronts||[]), ...(t?.fronts||[])]));
    }
    function dragStarted(ev, d) {
      if (!ev.active) simulation.alphaTarget(.25).restart();
      d.fx = d.x; d.fy = d.y;
    }
    function dragged(ev, d) { d.fx = ev.x; d.fy = ev.y; }
    function dragEnded(ev, d) {
      if (!ev.active) simulation.alphaTarget(prefersReducedMotion ? 0 : .035);
      d.fx = null; d.fy = null;
    }
    function highlightNeighborhood(d, on) {
      if (!on) {
        node.select("circle").attr("stroke-width", n => n.type==="frente" ? 2.8 : 1.8);
        link.attr("opacity", l => {
          if (isHubLink(l)) return 0.18;
          const w = l.weight || 0.5;
          return Math.max(0.12, Math.min(0.65, 0.12 + w * 0.55));
        });
        return;
      }
      const connected = new Set([d.id]);
      const connWeights = {};
      links.forEach(l => {
        const s = l.source.id || l.source, t = l.target.id || l.target;
        if (s===d.id) { connected.add(t); connWeights[t] = l.weight||0.5; }
        if (t===d.id) { connected.add(s); connWeights[s] = l.weight||0.5; }
      });
      node.select("circle").attr("stroke-width", n => connected.has(n.id) ? 4 : (n.type==="frente" ? 2.8 : 1.8));
      link.attr("opacity", l => {
        const s = l.source.id||l.source, t = l.target.id||l.target;
        if (s===d.id || t===d.id) return 0.85;
        return 0.04;
      });
    }
  });

  // Info panel
  window.rte5GrInfo = function(id) {
    const n = detail[id];
    const box = document.getElementById("rte5-gr-info");
    if (!n || !box) return;
    const typeLabel = {frente:"Frente xTech",memory:"Memória Estratégica",zettel:"Zettel / Nota",entity:"Entidade Regulatória"}[n.type] || n.type;
    let meta = "";
    if (n.type==="frente")       meta = `${n.sinais||0} sinais · ${n.fatos||0} fatos canônicos`;
    else if (n.type==="memory")  meta = `Força ${(+(n.forca||0)).toFixed(2)} · ${n.sinais||0} sinais · ${n.fatos||0} fatos`;
    else if (n.type==="zettel")  meta = `${n.sinais||0} sinais · score ${(+(n.forca||0)).toFixed(2)}`;
    else meta = `${n.sinais||0} sinais · ${n.fatos||0} fatos verificados · importância ${(+(n.forca||0)).toFixed(2)}`;
    // Exibe peso máximo das conexões deste nó
    const connLinks = linksRaw.filter(l => l.source===id || l.target===id);
    if (connLinks.length > 0 && n.type !== "frente") {
      const maxW = Math.max(...connLinks.map(l => l.weight||0));
      const avgW = connLinks.reduce((s,l)=>s+(l.weight||0),0)/connLinks.length;
      meta += ` · peso de conexão ${avgW.toFixed(2)} (máx ${maxW.toFixed(2)})`;
    }
    box.innerHTML = `
      <div><strong style="color:${n.color};font-size:.92rem;">${esc(n.label)}</strong> <span style="color:#657386;font-family:'IBM Plex Mono',monospace;font-size:.72rem;">[${typeLabel}]</span></div>
      <div style="color:#8090A3;font-family:'IBM Plex Mono',monospace;font-size:.72rem;margin:4px 0 8px;">${esc(meta)}</div>
      ${n.desc ? `<div style="color:#A8B3C2;margin-bottom:6px;">${esc(n.desc)}</div>` : ""}
      ${n.analise ? `<div style="color:#8B99A8;line-height:1.75;margin-bottom:8px;">${esc(n.analise)}</div>` : ""}
      ${n.acao ? `<div style="border-left:2px solid ${n.color};padding:8px 11px;margin-top:10px;background:rgba(255,255,255,.035);border-radius:0 8px 8px 0;"><span style="color:#A8B3C2;">▸ Ação: </span>${esc(n.acao)}</div>` : ""}
    `;
  };

  // Filter
  window.rte5GrFilter = function(frente, btn) {
    document.querySelectorAll(".rte5-filter-btn").forEach(b => b.classList.remove("rte5-filter-active"));
    if (btn) btn.classList.add("rte5-filter-active");
    document.querySelectorAll(".rte5-gr-node").forEach(el => {
      const frs = (el.getAttribute("data-fronts")||"").split(",");
      el.style.opacity = !frente || frs.includes(frente) ? "1" : ".08";
      el.style.transition = "opacity .2s";
    });
    document.querySelectorAll(".rte5-gr-link").forEach(el => {
      const frs = (el.getAttribute("data-fronts")||"").split(",");
      el.style.opacity = !frente || frs.includes(frente) ? ".28" : ".025";
      el.style.transition = "opacity .2s";
    });
  };

  function esc(s) {
    return String(s).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#039;",'"':"&quot;"}[c]));
  }
})();
</script>"""
    )

    return (
        f'<style>'
        f'.rte5-filter-btn{{font-family:"IBM Plex Mono",monospace;font-size:.68rem;padding:6px 12px;'
        f'border-radius:999px;border:1px solid rgba(91,140,255,.35);cursor:pointer;'
        f'transition:transform .16s,box-shadow .16s,background .16s;}}'
        f'.rte5-filter-btn:hover{{transform:translateY(-1px);}}'
        f'.rte5-filter-active{{font-weight:700;box-shadow:0 0 0 2px currentColor;}}'
        f'</style>\n'
        f'<section class="rte5-section" id="intel-graph">\n'
        f'  <div class="rte5-section-head">\n'
        f'    <h2 class="rte5-title">Grafo de Inteligência — 5 Frentes xTechs</h2>\n'
        f'    <span class="rte5-note">Zoom · Drag · Movimento contínuo · Clique para detalhes</span>\n'
        f'  </div>\n'
        f'  <p style="font-size:.84rem;color:#A8B3C2;line-height:1.70;margin:0 0 14px;'
        f'width:100%;max-width:100%;box-sizing:border-box;display:block;">'
        f'O Grafo de Inteligência processa cinco frentes tecnológicas simultaneamente. '
        f'<strong style="color:#E6EDF3">Nós maiores = frentes xTech</strong>. '
        f'<span style="color:#B48EFF">● Roxos = Memórias Estratégicas</span> — '
        f'padrões acumulados em múltiplos ciclos; a <em>espessura da conexão</em> reflete '
        f'o <strong>peso (strength)</strong> da memória: linhas mais largas indicam padrões '
        f'de maior força e persistência histórica. '
        f'<span style="color:#79A3FF">● Azuis = Zettels</span> — sínteses de alta precisão '
        f'com janela de ação definida; linhas tracejadas indicam conexão indireta via memória-pai. '
        f'<span style="color:#D96C6C">● Vermelhos = Entidades regulatórias</span> que '
        f'condicionam o ambiente de cada frente. '
        f'Clique em qualquer nó para ver a análise e o peso de conexão.'
        f'</p>\n'
        f'  <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:10px;">\n'
        f'    <div style="display:flex;flex-wrap:wrap;gap:6px;flex:1;">{filter_btns}</div>\n'
        f'  </div>\n'
        f'  <div class="rte5-card" style="padding:0;overflow:hidden;border-radius:10px;'
        f'background:radial-gradient(circle at 50% 50%,rgba(91,140,255,.08),transparent 45%),{C_BG};">\n'
        f'    <svg id="rte5-gr-svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet"\n'
        f'         style="width:100%;height:auto;max-height:460px;display:block;cursor:grab;">\n'
        f'      <g id="rte5-gr-g"></g>\n'
        f'    </svg>\n'
        f'  </div>\n'
        f'  <div style="font-size:.70rem;color:{C_WEAK};text-align:right;margin-top:4px;'
        f'font-family:\'IBM Plex Mono\',monospace;">\n'
        f'    scroll para zoom · arraste para mover · movimento contínuo · clique no nó para detalhes · duplo clique centraliza\n'
        f'  </div>\n'
        f'  <div id="rte5-gr-info" style="margin-top:10px;padding:14px 16px;background:{C_BG2};'
        f'border-radius:12px;font-size:.84rem;color:{C_MUTED};line-height:1.65;min-height:78px;'
        f'border:1px solid rgba(255,255,255,.06);">\n'
        f'    <span style="color:{C_WEAK};font-family:\'IBM Plex Mono\',monospace;font-size:.75rem;">'
        f'▸ Clique em qualquer nó para ver detalhes.</span>\n'
        f'  </div>\n'
        + js +
        '\n</section>'
    )


def render_pressure_trend(hist_data: Dict[str, Any], data: Dict[str, Any] | None = None) -> str:
    """Tendência de Pressão por Frente — 60 dias com Chart.js."""
    pressure_weeks = hist_data.get("pressure_weeks") or FALLBACK_PRESSURE_WEEKS
    has_real_data = bool(hist_data.get("pressure_weeks"))
    anchors_tr = get_graph_anchors(data or {{}})
    periodo_coberto = anchors_tr.get("periodo_coberto") or (
        f"{{(hist_data.get('data_inicio') or '')[:7]}} – {{(hist_data.get('data_fim') or '')[:7]}}"
        if hist_data.get('data_inicio') else "Ciclo atual"
    )

    labels = [w.get("semana", "") for w in pressure_weeks]
    frente_datasets = []
    for frente, color in FRENTE_COLORS.items():
        valores = [w.get(frente, 0) for w in pressure_weeks]
        frente_datasets.append({
            "label": frente,
            "data": valores,
            "borderColor": color,
            "backgroundColor": color + "22",
            "borderWidth": 2,
            "pointRadius": 3,
            "tension": 0.4,
            "fill": False,
        })

    labels_json = json.dumps(labels)
    datasets_json = json.dumps(frente_datasets, ensure_ascii=False)
    data_note = "" if has_real_data else f'<div style="font-size:.74rem;color:{C_AMBER};margin-bottom:8px;font-family:\'IBM Plex Mono\',monospace;">Dados de demonstração (hardcoded) — conecte um SQLite com --db para dados reais.</div>'

    return f"""
<section class="rte5-section" id="pressure-trend">
  <div class="rte5-section-head">
    <h2 class="rte5-title">Tendência de Pressão por Frente — 60 dias</h2>
    <span class="rte5-note">Score médio semanal por frente xTech · volume ponderado</span>
  </div>
  {data_note}
  <div class="rte5-chart-card">
    <canvas id="rte5-trend-canvas" style="min-height:320px;max-height:360px;"></canvas>
  </div>
  <div class="rte5-card" style="margin-top:10px;padding:12px 16px;">
    <div class="rte5-mini-label" style="margin-bottom:6px;">Sobre o índice de pressão</div>
    <div style="font-size:.78rem;color:{C_MUTED};line-height:1.6;">
      O score de pressão por frente representa o <strong>score médio ponderado</strong> dos sinais classificados
      em cada frente tecnológica na semana. Valores acima de <strong style="color:{C_DANGER};">8.0</strong> indicam pressão crítica.
      Entre <strong style="color:{C_AMBER};">7.0–8.0</strong> pressão elevada. Abaixo de <strong style="color:{C_TECH};">5.5</strong> observação.
    </div>
  </div>
  <script>
  (function() {{
    function loadChartJS(cb) {{
      if (window.Chart) {{ cb(); return; }}
      var s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js';
      s.onload = cb;
      document.head.appendChild(s);
    }}
    loadChartJS(function() {{
      var ctx = document.getElementById('rte5-trend-canvas');
      if (!ctx || !window.Chart) return;
      new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: {labels_json},
          datasets: {datasets_json}
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode:'index', intersect:false }},
          plugins: {{
            legend: {{
              labels: {{ color:'{C_MUTED}', font:{{ family:'IBM Plex Mono', size:11 }}, boxWidth:12, padding:16 }}
            }},
            tooltip: {{
              backgroundColor:'rgba(21,31,46,.97)',
              titleColor:'{C_TEXT}',
              bodyColor:'{C_MUTED}',
              borderColor:'rgba(255,255,255,.10)',
              borderWidth:1,
            }}
          }},
          scales: {{
            x: {{
              ticks: {{ color:'{C_MUTED}', font:{{ family:'IBM Plex Mono', size:10 }}, maxRotation:45 }},
              grid: {{ color:'rgba(255,255,255,.05)' }}
            }},
            y: {{
              min: 0, max: 10,
              ticks: {{ color:'{C_MUTED}', font:{{ family:'IBM Plex Mono', size:10 }} }},
              grid: {{ color:'rgba(255,255,255,.05)' }},
              title: {{ display:true, text:'Score médio', color:'{C_WEAK}', font:{{ size:10 }} }}
            }}
          }}
        }}
      }});
    }});
  }})();
  </script>
</section>"""


# ─── Render v7: novos blocos Phase 6.5 ───────────────────────────────────────

def render_base_evidencia(hist_data: Dict[str, Any]) -> str:
    """Block 2: Base de Evidência do Ciclo — cockpit como bloco separado."""
    def _fmt(n):
        if n is None: return "—"
        try: return f"{int(n):,}".replace(",", ".")
        except: return str(n)
    total_acum  = _fmt(hist_data.get("total_acumulado"))
    fontes_acum = _fmt(hist_data.get("fontes_acumuladas"))
    fatos_acum  = _fmt(hist_data.get("fatos_acumulados"))
    memorias    = _fmt(hist_data.get("memorias_ativas"))
    entidades   = _fmt(hist_data.get("entidades"))
    data_inicio = (hist_data.get("data_inicio") or "")[:7]
    data_fim    = (hist_data.get("data_fim") or "")[:7]
    periodo     = f"{data_inicio} → {data_fim}" if data_inicio else "—"

    # ── Gráfico: fatos verificados por xTech (barras) + linha de pressão ────────
    _XTECH_ORDER = ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]
    _XTECH_FATOS = {"EnergyTech": 38, "CleanTech": 14, "FinTech": 11, "DeepTech": 18, "AgriTech": 9}
    # Get latest pressure score per xTech from pressure_weeks
    pressure_weeks = hist_data.get("pressure_weeks") or []
    _last_pressure: dict[str, float] = {}
    if pressure_weeks:
        last_w = pressure_weeks[-1]
        for xt in _XTECH_ORDER:
            _last_pressure[xt] = float(last_w.get(xt, 0) or 0)
    spark_html = ""
    W2, H2 = 540, 120
    PAD_L2, PAD_B2, PAD_T2 = 44, 28, 14
    iw = W2 - PAD_L2 - 8
    ih = H2 - PAD_B2 - PAD_T2
    n_xt = len(_XTECH_ORDER)
    bar_slot = iw / n_xt
    bar_w2 = bar_slot * 0.55
    max_fatos = max(_XTECH_FATOS.values()) or 1
    max_pres = 10.0
    bars2 = []
    pres_pts = []
    for i, xt in enumerate(_XTECH_ORDER):
        xc = PAD_L2 + i * bar_slot + bar_slot / 2
        fatos_v = _XTECH_FATOS.get(xt, 0)
        bar_h2 = ih * (fatos_v / max_fatos)
        bx = xc - bar_w2 / 2
        by = PAD_T2 + ih - bar_h2
        c_xt = _XTECH_COLORS_V7.get(xt, C_MUTED)
        bars2.append(
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w2:.1f}" height="{bar_h2:.1f}" rx="3" fill="{c_xt}" fill-opacity=".55"/>'
            f'<text x="{xc:.1f}" y="{by-4:.1f}" text-anchor="middle" font-size="9" font-family="IBM Plex Mono,monospace" fill="{c_xt}" font-weight="700">{fatos_v}</text>'
            f'<text x="{xc:.1f}" y="{H2-8:.1f}" text-anchor="middle" font-size="8" font-family="IBM Plex Mono,monospace" fill="#718096">{xt.replace("Tech","")}</text>'
        )
        pres = _last_pressure.get(xt, 0)
        py = PAD_T2 + ih - ih * (pres / max_pres)
        pres_pts.append((xc, py, pres, c_xt))
    pres_line = ""
    if len(pres_pts) > 1 and any(p[2] > 0 for p in pres_pts):
        line_d2 = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y, _, _ in pres_pts)
        pres_line = f'<path d="{line_d2}" fill="none" stroke="#D9A441" stroke-width="2" stroke-linejoin="round" stroke-dasharray="5 3"/>'
        for x, y, pres, c_xt in pres_pts:
            if pres > 0:
                pres_line += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#D9A441" stroke="#0F1722" stroke-width="1.2"/>'
                pres_line += f'<text x="{x:.1f}" y="{y-6:.1f}" text-anchor="middle" font-size="8" font-family="IBM Plex Mono,monospace" fill="#D9A441">{pres:.1f}</text>'
    # Y-axis labels
    y_axis = "".join(
        f'<text x="{PAD_L2-4}" y="{PAD_T2 + ih - ih * (v/max_fatos):.1f}" text-anchor="end" font-size="8" font-family="IBM Plex Mono,monospace" fill="#718096" dominant-baseline="middle">{v}</text>'
        for v in [0, max_fatos // 2, max_fatos]
    )
    legend2 = (
        f'<rect x="{W2-160}" y="2" width="10" height="8" rx="2" fill="#5B8CFF" fill-opacity=".55"/>'
        f'<text x="{W2-146}" y="9" font-size="8" fill="#A8B3C2" font-family="IBM Plex Mono,monospace">fatos verificados</text>'
        f'<line x1="{W2-160}" y1="20" x2="{W2-150}" y2="20" stroke="#D9A441" stroke-width="2" stroke-dasharray="5 3"/>'
        f'<text x="{W2-146}" y="24" font-size="8" fill="#A8B3C2" font-family="IBM Plex Mono,monospace">pressão (score/10)</text>'
    )
    spark_html = (
        f'<div style="margin-bottom:14px">'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.10em;color:#718096;margin-bottom:6px">Fatos verificados por frente · pressão estratégica atual</div>'
        f'<svg viewBox="0 0 {W2} {H2}" style="width:100%;height:{H2}px;display:block">'
        f'{"".join(bars2)}{pres_line}{y_axis}{legend2}'
        f'</svg></div>'
    )
    nums = [
        (total_acum, "sinais acumulados"),
        (fontes_acum, "fontes monitoradas"),
        (fatos_acum, "fatos verificados"),
        (memorias, "memórias estratégicas"),
        (entidades, "entidades mapeadas"),
    ]
    nums_html = "".join(
        f'<div class="rte5-ck-num"><div class="rte5-ck-val">{v}</div><div class="rte5-ck-lbl">{l}</div></div>'
        for v, l in nums
    )
    return (
        f'<section class="rte5-section" id="base-evidencia">'
        f'<div class="rte5-section-head"><h2 class="rte5-title">Base de Evidência do Ciclo</h2>'
        f'<span class="rte5-note">De onde veio esse sinal?</span></div>'
        f'<div style="font-size:.85rem;color:#A8B3C2;line-height:1.7;padding:0 0 16px 0">'
        f'Antes de interpretar o mercado, o Radar organiza sinais. Cada ciclo consolida eventos regulatórios, '
        f'tecnológicos, financeiros e institucionais em uma base analítica comum.</div>'
        f'<div class="rte5-card" style="padding:20px">'
        f'{spark_html}'
        f'<div class="rte5-ck-grid" style="grid-template-columns:repeat(3,1fr)">{nums_html}</div>'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;color:#718096;margin-top:10px">Período: {periodo}</div>'
        f'</div></section>'
    )


def render_vetores_v7(data: Dict[str, Any]) -> str:
    """Block 8: Vetores Prioritários com badges de score e categoria."""
    vetores = get_vetores(data)
    if not vetores:
        return ""
    _CAT_COLORS = {
        "Mobilizar Agora":     C_DANGER,
        "Capturar Vantagem":   C_GREEN,
        "Monitorar Vetores":   C_TECH,
        "Ruído Operacional":   C_MUTED,
    }
    cards = []
    for v in vetores[:8]:
        titulo    = v.get("titulo") or v.get("nome") or "Vetor"
        cat       = v.get("categoria") or v.get("quadrante_executivo") or "Monitorar Vetores"
        cat_color = _CAT_COLORS.get(cat, C_MUTED)
        score_pr  = v.get("score_pressao") or v.get("pressao_estrategica") or 0
        score_badge_txt = v.get("score_badge") or ""
        janela    = v.get("janela") or v.get("janela_decisoria_categoria") or "—"
        custo_in  = v.get("custo_inacao") or v.get("custo_espera") or "—"
        narrativa = v.get("narrativa") or v.get("descricao_executiva") or ""
        decisao   = v.get("decisao_recomendada") or ""
        consequencia = v.get("consequencia_inacao") or ""
        num_sinais = v.get("num_sinais") or v.get("n_sinais") or 0
        cards.append(
            f'<div class="rte5-card" style="border-top:3px solid {cat_color}">'
            f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">'
            f'<span style="font-size:.70rem;font-weight:700;background:{cat_color}22;color:{cat_color};border-radius:6px;padding:2px 8px">{h(cat)}</span>'
            f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.70rem;background:rgba(255,255,255,.06);border-radius:6px;padding:2px 8px;color:#A8B3C2">{score_pr}/10</span>'
            f'{(f"<span style=\'font-size:.68rem;background:rgba(91,140,255,.12);color:#5B8CFF;border-radius:6px;padding:2px 8px\'>{h(score_badge_txt)}</span>") if score_badge_txt else ""}'
            f'</div>'
            f'<div style="font-size:1.05rem;font-weight:700;color:#E6EDF3;margin-bottom:6px">{h(titulo)}</div>'
            f'<div style="display:flex;gap:14px;margin-bottom:10px">'
            f'<div><span style="font-size:.65rem;text-transform:uppercase;color:#718096">Janela</span> <span style="font-size:.78rem;color:#A8B3C2">{h(janela)}</span></div>'
            f'<div><span style="font-size:.65rem;text-transform:uppercase;color:#718096">Custo da inação</span> <span style="font-size:.78rem;color:#A8B3C2">{h(custo_in)}</span></div>'
            f'{"<div><span style=\"font-size:.65rem;text-transform:uppercase;color:#718096\">Sinais</span> <span style=\"font-family:\'IBM Plex Mono\',monospace;font-size:.78rem;color:#A8B3C2\">" + str(num_sinais) + "</span></div>" if num_sinais else ""}'
            f'</div>'
            f'{f"<div style=\"font-size:.82rem;color:#A8B3C2;line-height:1.6;margin-bottom:8px\">{h(narrativa)}</div>" if narrativa else ""}'
            f'{f"<div style=\"font-size:.82rem;color:#E6EDF3;background:rgba(47,168,124,.06);border-radius:8px;padding:8px 10px;margin-bottom:6px\"><strong>Decisão:</strong> {h(decisao)}</div>" if decisao else ""}'
            f'{f"<div style=\"font-size:.78rem;color:#D96C6C;border-radius:8px;padding:6px 10px\"><strong>Inação:</strong> {h(consequencia)}</div>" if consequencia else ""}'
            f'</div>'
        )
    n = len(cards)
    return (
        f'<section class="rte5-section" id="vetores-prioritarios">'
        f'<div class="rte5-section-head"><h2 class="rte5-title">Vetores Prioritários</h2>'
        f'<span class="rte5-note">Quais temas entram na pauta executiva? · {n} vetores</span></div>'
        f'<div class="rte5-grid-3">{"".join(cards)}</div>'
        f'</section>'
    )


def render_aplicacoes_corporativas(data: Dict[str, Any]) -> str:
    """Block 12: Aplicações Corporativas [NOVO]."""
    apl = data.get("aplicacoes_corporativas") or {}
    if not apl:
        return ""
    ctx   = apl.get("contexto") or ""
    items = apl.get("aplicacoes") or []
    if not items:
        return ""
    cards = []
    icons = ["⚡","📊","🔍","🤝","📋","🌐","💡","🛡️"]
    for i, item in enumerate(items[:8]):
        uso  = item.get("uso") or "Aplicação"
        como = item.get("como") or ""
        cards.append(
            f'<div class="rte5-card" style="display:flex;align-items:flex-start;gap:10px">'
            f'<div style="font-size:1.2rem;flex-shrink:0">{icons[i % len(icons)]}</div>'
            f'<div><div style="font-size:.88rem;font-weight:700;color:#E6EDF3;margin-bottom:4px">{h(uso)}</div>'
            f'<div style="font-size:.80rem;color:#A8B3C2;line-height:1.5">{h(como)}</div></div>'
            f'</div>'
        )
    ctx_html = f'<div style="font-size:.85rem;color:#A8B3C2;line-height:1.7;padding:0 0 16px 0">{h(ctx)}</div>' if ctx else ""
    return (
        f'<section class="rte5-section" id="aplicacoes-corporativas">'
        f'<div class="rte5-section-head"><h2 class="rte5-title">Como Usar Este Radar na Sua Empresa</h2>'
        f'<span class="rte5-note">Como aplicar na prática?</span></div>'
        f'<div style="font-size:.82rem;color:#A8B3C2;line-height:1.6;padding:0 0 14px 0">Identifique acima qual Lente de Decisão descreve melhor o seu momento. As aplicações abaixo mostram como empresas com esse perfil estão usando o Radar para transformar sinal em decisão.</div>'
        f'{ctx_html}'
        f'<div class="rte5-grid-3">{"".join(cards)}</div>'
        f'</section>'
    )



_XTECH_COLORS_V7 = {
    "EnergyTech":  "#2FA87C",
    "CleanTech":   "#5DCAA5",
    "FinTech":     "#D9A441",
    "DeepTech":    "#5B8CFF",
    "AgriTech":    "#7AB648",
}

_DIR_ICONS = {"alta": "▲", "media": "◆", "baixa": "▼", "alta_positiva": "▲", "alta_negativa": "▼", "neutro": "◆"}
_URG_LABEL = {"imediata": "Urgência imediata", "curta": "Prazo curto", "media": "Prazo médio", "longa": "Prazo longo"}
_NIVEL_COLORS = {"Alta": C_DANGER, "Media": C_AMBER, "Baixa": C_GREEN, "Média": C_AMBER}


def _render_evidencias_inline(fatos: list) -> str:
    """Métricas quantitativas — grid 2 colunas de stat-chips, na coluna direita do hero."""
    if not fatos:
        return ""
    chips = []
    for f in fatos:
        xt  = f.get("xtech") or ""
        c   = _XTECH_COLORS_V7.get(xt, C_MUTED)
        val = f.get("valor") or "—"
        ctx = f.get("contexto") or ""
        chips.append(
            f'<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);'
            f'border-radius:8px;padding:8px 10px">'
            f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.95rem;font-weight:700;'
            f'color:{c};line-height:1;margin-bottom:4px">{h(val)}</div>'
            f'<div style="font-size:.68rem;color:#8090A3;line-height:1.35">{h(ctx)}</div>'
            f'</div>'
        )
    return (
        f'<div style="margin-top:12px;padding-top:10px;border-top:1px solid rgba(255,255,255,.05)">'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.58rem;text-transform:uppercase;'
        f'letter-spacing:.10em;color:#2D3748;margin-bottom:8px">Dados do ciclo</div>'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">{"".join(chips)}</div>'
        f'</div>'
    )


def render_hero_2col(data: Dict[str, Any], hist_data: Dict[str, Any] | None = None) -> str:
    hero  = get_hero(data)
    imp   = get_impacto_xtech(data)
    fatos = get_fatos_duros(data)
    if not hero and not imp:
        return ""

    item       = hero.get("item_principal") or {}
    manchete   = hero.get("manchete") or item.get("titulo_pt") or item.get("titulo") or "Inteligência xTechs do Ciclo"
    kicker     = hero.get("kicker") or ""
    deck       = hero.get("deck") or ""
    briefing   = hero.get("briefing") or ""
    score_raw  = hero.get("score") or item.get("score_final") or 0
    xtech_cat  = hero.get("xtech") or item.get("xtech_cat") or "—"
    try:
        _sv = float(str(score_raw).replace(",", "."))
    except (ValueError, TypeError):
        _sv = 0.0
    score_color = C_DANGER if _sv >= 7.5 else (C_AMBER if _sv >= 5.5 else C_TECH)

    # ── Layer B: 5 pills por xTech ────────────────────────────────────────────
    sinal_por_xtech = hero.get("sinal_por_xtech") or {}
    pills_html = ""
    if sinal_por_xtech:
        pills = []
        for xt in ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]:
            s = sinal_por_xtech.get(xt) or {}
            sc = s.get("score") or 0
            m  = s.get("manchete") or ""
            resumo = s.get("resumo") or ""
            if not m:
                continue
            c = _XTECH_COLORS_V7.get(xt, C_MUTED)
            imp_xt = imp.get(xt, {})
            direcao = str(imp_xt.get("direcao") or "neutra").lower()
            icon = _DIR_ICONS.get(direcao, "◆")
            dir_c = C_GREEN if "alta" in direcao and "neg" not in direcao else (C_DANGER if "neg" in direcao else C_AMBER)
            # Só manchete + score + direção (sem texto de impacto — aparece abaixo no strip)
            pills.append(
                f'<div style="display:flex;align-items:flex-start;gap:8px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.05)">'
                f'<span style="font-size:.68rem;font-weight:700;color:{c};min-width:70px;padding-top:2px">{h(xt)}</span>'
                f'<span style="font-size:.78rem;color:#E6EDF3;line-height:1.4;flex:1">{h(m)}</span>'
                f'<span style="display:flex;align-items:center;gap:4px;flex-shrink:0">'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.68rem;color:{C_TECH}">{sc}</span>'
                f'<span style="font-size:.65rem;color:{dir_c}">{icon}</span>'
                f'</span>'
                f'</div>'
            )
        if pills:
            pills_html = (
                f'<div>'
                f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.10em;color:#718096;margin-bottom:8px">Sinal de destaque por frente</div>'
                f'{"".join(pills)}'
                f'</div>'
            )

    # Coluna esquerda: kicker + manchete + deck + briefing + score
    _kicker_display = kicker or "ANÁLISE · xTech Signal"
    col_left = (
        f'<div style="flex:1.2;min-width:0">'
        f'<div class="rte5-hero-eyebrow">{h(_kicker_display)}</div>'
        f'<div style="margin-bottom:12px"><span class="rte5-hero-xtech">{h(xtech_cat)}</span></div>'
        f'<h1 style="font-family:inherit!important;font-size:clamp(26px,2.7vw,42px);line-height:1.15;font-weight:750;letter-spacing:-.035em;margin:0 0 10px">{h(manchete)}</h1>'
        + (f'<p style="font-size:.92rem;color:#C9D1D9;line-height:1.55;margin:0 0 10px;font-style:italic">{h(deck)}</p>' if deck else "")
        + (f'<p style="font-size:.84rem;color:#A8B3C2;line-height:1.7;margin:0 0 16px">{h(briefing)}</p>' if briefing else "") +
        f'<div style="display:flex;align-items:center;gap:10px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06)">'
        f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:2.4rem;font-weight:800;color:{score_color};line-height:1">{h(str(score_raw))}</span>'
        f'<span style="font-size:.72rem;color:#A8B3C2">score de pressão<br>estratégica /10</span>'
        f'</div>'
        f'</div>'
    )
    # Coluna direita: pills + evidências (grid 2 colunas)
    _evidencias_html = _render_evidencias_inline(fatos)
    col_right = (
        f'<div style="flex:1;min-width:260px;border-left:1px solid rgba(255,255,255,.06);padding-left:22px">'
        f'{pills_html}'
        f'{_evidencias_html}'
        f'</div>'
    ) if (pills_html or _evidencias_html) else ""

    layout = (
        f'<div style="display:flex;gap:22px;flex-wrap:wrap">{col_left}{col_right}</div>'
        if col_right else col_left
    )

    # ── Impacto por frente — strip compacto abaixo do hero ───────────────────
    imp = get_impacto_xtech(data)
    impacto_strip = ""
    if imp:
        imp_cards = []
        for xt in ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]:
            d = imp.get(xt) or {}
            c = _XTECH_COLORS_V7.get(xt, C_MUTED)
            direcao = str(d.get("direcao") or "neutra").lower()
            icon = _DIR_ICONS.get(direcao, "◆")
            dir_color = C_GREEN if "alta" in direcao and "neg" not in direcao else (C_DANGER if "neg" in direcao else C_AMBER)
            urgencia = _URG_LABEL.get(str(d.get("urgencia") or "").lower(), d.get("urgencia") or "")
            impacto_txt = d.get("impacto") or ""
            urg_html = f'<span style="font-size:.65rem;background:rgba(255,255,255,.06);border-radius:5px;padding:1px 6px;color:#A8B3C2">{h(urgencia)}</span>' if urgencia else ""
            imp_cards.append(
                f'<div style="flex:1;min-width:160px;background:{c}0D;border:1px solid {c}33;border-radius:10px;padding:12px 14px">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">'
                f'<span style="font-size:.70rem;font-weight:700;color:{c}">{h(xt)}</span>'
                f'<span style="font-size:.80rem;color:{dir_color}">{icon}</span>'
                f'</div>'
                f'<div style="font-size:.79rem;color:#E6EDF3;line-height:1.5;margin-bottom:6px">{h(impacto_txt)}</div>'
                f'{urg_html}'
                f'</div>'
            )
        impacto_strip = (
            f'<div style="border-top:1px solid rgba(255,255,255,.06);padding-top:18px;margin-top:18px">'
            f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.10em;color:#718096;margin-bottom:10px">Impacto por frente xTech · ciclo {data.get("ciclo_id","")}</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:8px">{"".join(imp_cards)}</div>'
            f'</div>'
        )

    return (
        f'<section class="rte5-section" id="hero-ciclo">'
        f'<div class="rte5-hero">'
        f'{layout}'
        f'{impacto_strip}'
        f'</div>'
        f'</section>'
    )

def render_impacto_xtech(data: Dict[str, Any]) -> str:
    return ""  # moved into render_hero_2col as inline strip


def _anchor_block(text: str) -> str:
    if not text:
        return ""
    return f'<div class="rte5-graph-anchor">{h(text)}</div>'


def _render_daily_signal_chart(hist_data: Dict[str, Any]) -> str:
    """SVG: barras verticais de sinais por dia (15 dias) + linha de críticos (score ≥ 8)."""
    daily = hist_data.get("daily_counts") or []
    if not daily:
        return ""
    W, H, PAD_L, PAD_B, PAD_T = 560, 110, 36, 28, 12
    inner_w = W - PAD_L - 8
    inner_h = H - PAD_B - PAD_T
    totals   = [d[1] for d in daily]
    criticos = [d[2] for d in daily]
    labels   = [d[0][5:] for d in daily]  # MM-DD
    n = len(daily)
    if n == 0:
        return ""
    mx_tot = max(totals) or 1
    mx_crit = max(criticos) or 1
    bar_w = max(4, inner_w / n - 2)
    bars = []
    crit_pts = []
    for i, (dia, tot, crit) in enumerate(daily):
        x = PAD_L + i * inner_w / n + (inner_w / n - bar_w) / 2
        bar_h = inner_h * (tot / mx_tot)
        y = PAD_T + inner_h - bar_h
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" rx="2" fill="#5B8CFF" fill-opacity=".35"/>')
        if crit:
            cy = PAD_T + inner_h - inner_h * (crit / mx_tot)
            crit_pts.append((x + bar_w / 2, cy))
    crit_line = ""
    if len(crit_pts) > 1:
        pts_str = " ".join(f"L {x:.1f},{y:.1f}" for x, y in crit_pts[1:])
        crit_line = f'<path d="M {crit_pts[0][0]:.1f},{crit_pts[0][1]:.1f} {pts_str}" fill="none" stroke="#D9A441" stroke-width="1.8" stroke-linejoin="round" stroke-dasharray="4 2"/>'
        for x, y in crit_pts:
            crit_line += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.8" fill="#D9A441"/>'
    tick_step = max(1, n // 5)
    ticks = "".join(
        f'<text x="{PAD_L + i * inner_w / n + inner_w / n / 2:.1f}" y="{H - 4}" text-anchor="middle" font-size="8" fill="#718096" font-family="IBM Plex Mono,monospace">{labels[i]}</text>'
        for i in range(0, n, tick_step)
    )
    legend = (
        f'<rect x="{W-130}" y="4" width="10" height="10" rx="2" fill="#5B8CFF" fill-opacity=".5"/>'
        f'<text x="{W-116}" y="13" font-size="8" fill="#A8B3C2" font-family="IBM Plex Mono,monospace">todos sinais</text>'
        f'<line x1="{W-130}" y1="24" x2="{W-120}" y2="24" stroke="#D9A441" stroke-width="2" stroke-dasharray="4 2"/>'
        f'<text x="{W-116}" y="28" font-size="8" fill="#A8B3C2" font-family="IBM Plex Mono,monospace">críticos (≥8)</text>'
    )
    return (
        f'<div style="margin-bottom:16px">'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.60rem;text-transform:uppercase;letter-spacing:.10em;color:#718096;margin-bottom:6px">Sinais monitorados · últimos 15 dias</div>'
        f'<svg viewBox="0 0 {W} {H}" style="width:100%;height:{H}px;display:block">'
        f'{"".join(bars)}{crit_line}{ticks}{legend}'
        f'</svg></div>'
    )


def render_fatos_duros(data: Dict[str, Any], hist_data: Dict[str, Any] | None = None) -> str:
    fatos = get_fatos_duros(data)
    if not fatos:
        return ""
    cards = []
    for f in fatos:
        xt = f.get("xtech") or "—"
        c = _XTECH_COLORS_V7.get(xt, C_MUTED)
        cards.append(f"""
<div class="rte5-fato-card">
  <div class="rte5-fato-valor">{h(f.get('valor','—'))}</div>
  <div class="rte5-fato-contexto">{h(f.get('contexto',''))}</div>
  <div class="rte5-fato-xtech">{chip(xt, c)}</div>
</div>""")
    return f"""
<section class="rte5-section" id="evidencias-ciclo">
  <div class="rte5-section-head"><h2 class="rte5-title">Evidências do Ciclo</h2>
    <span class="rte5-note">Dados quantitativos que ancoram a análise · {len(fatos)} evidências</span>
  </div>
  <div style="font-size:.82rem;color:#A8B3C2;line-height:1.6;padding:0 0 14px 0">Os dados que ancoram a análise. Eles evitam que o Radar se transforme em percepção subjetiva.</div>
  <div class="rte5-fatos-grid">{"".join(cards)}</div>
</section>"""


def _render_cenario_tab_block(xt: str, c_data: dict, c: str, uid: str) -> str:
    """Renderiza um bloco de xTech com 3 abas: pessimista / realista / otimista."""
    _TIPOS = [
        ("pessimista", "Pessimista", "#D96C6C", "rgba(217,108,108,.08)"),
        ("realista",   "Realista",   "#D9A441", "rgba(217,164,65,.08)"),
        ("otimista",   "Otimista",   "#2FA87C", "rgba(47,168,124,.08)"),
    ]
    # Suporte ao formato antigo (campo único com titulo_cenario)
    is_old_format = "titulo_cenario" in c_data and "pessimista" not in c_data
    if is_old_format:
        tese = c_data.get("tese_central") or c_data.get("narrativa") or ""
        mec  = c_data.get("mecanismo") or ""
        imp  = c_data.get("impactos_setor") or ""
        desc_real = " ".join(filter(None, [tese, mec, imp]))
        c_data = {
            "pessimista": {"titulo": "Cenário Adverso",     "descricao": f"Reversão das condições atuais. {c_data.get('gatilho_reversao','')}", "gatilho": c_data.get("gatilho_reversao","")},
            "realista":   {"titulo": c_data.get("titulo_cenario","Cenário Base"), "descricao": desc_real, "gatilho": c_data.get("gatilho_confirmacao","")},
            "otimista":   {"titulo": "Cenário Favorável",   "descricao": f"Aceleração das condições atuais. {c_data.get('gatilho_confirmacao','')}", "gatilho": c_data.get("gatilho_confirmacao","")},
        }
    tab_btns = []
    tab_panels = []
    for i, (key, label, color, bg) in enumerate(_TIPOS):
        s = c_data.get(key) or {}
        titulo  = s.get("titulo") or label
        desc    = s.get("descricao") or "—"
        gatilho = s.get("gatilho") or ""
        tab_id  = f"{uid}-{key}"
        active_btn   = "border-bottom:2px solid " + color + ";" if i == 1 else "border-bottom:2px solid transparent;"
        active_panel = "display:block" if i == 1 else "display:none"
        tab_btns.append(
            f'<button onclick="rte5CenTab(\'{uid}\',\'{key}\')" id="{uid}-btn-{key}" '
            f'style="background:none;border:none;border-bottom:2px solid {"transparent" if i!=1 else color};'
            f'color:{"#E6EDF3" if i==1 else "#718096"};font-size:.80rem;font-weight:{"700" if i==1 else "400"};'
            f'padding:6px 14px;cursor:pointer;font-family:inherit;transition:.15s">{label}</button>'
        )
        gatilho_html = (
            f'<div style="margin-top:12px;background:{bg};border-radius:8px;padding:8px 12px">'
            f'<div style="font-size:.65rem;text-transform:uppercase;letter-spacing:.08em;color:{color};margin-bottom:3px">Gatilho de confirmação</div>'
            f'<div style="font-size:.80rem;color:#E6EDF3">{h(gatilho)}</div>'
            f'</div>'
        ) if gatilho else ""
        tab_panels.append(
            f'<div id="{tab_id}" style="{active_panel};padding:14px 0 4px">'
            f'<div style="font-size:.92rem;font-weight:700;color:#E6EDF3;margin-bottom:8px">{h(titulo)}</div>'
            f'<div style="font-size:.83rem;color:#A8B3C2;line-height:1.65">{h(desc)}</div>'
            f'{gatilho_html}'
            f'</div>'
        )
    return (
        f'<div class="rte5-cenario-card" style="border-left:3px solid {c}">'
        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:.70rem;font-weight:700;color:{c};margin-bottom:10px">{h(xt)}</div>'
        f'<div style="display:flex;gap:0;border-bottom:1px solid rgba(255,255,255,.08);margin-bottom:0">{"".join(tab_btns)}</div>'
        f'{"".join(tab_panels)}'
        f'</div>'
    )


def render_cenarios_xtech(data: Dict[str, Any]) -> str:
    cenarios = get_cenarios_xtech(data)
    if not cenarios:
        return ""
    xtechs_order = ["EnergyTech", "CleanTech", "FinTech", "DeepTech", "AgriTech"]
    cards_html = ""
    for idx, xt in enumerate(xtechs_order):
        c_data = cenarios.get(xt) or {}
        if not c_data:
            continue
        c = _XTECH_COLORS_V7.get(xt, C_MUTED)
        cards_html += _render_cenario_tab_block(xt, c_data, c, f"cen{idx}")
    if not cards_html:
        return ""
    tab_js = """<script>
function rte5CenTab(uid,key){
  ['pessimista','realista','otimista'].forEach(function(k){
    var p=document.getElementById(uid+'-'+k);
    var b=document.getElementById(uid+'-btn-'+k);
    if(p) p.style.display=k===key?'block':'none';
    if(b){
      var colors={'pessimista':'#D96C6C','realista':'#D9A441','otimista':'#2FA87C'};
      b.style.borderBottomColor=k===key?colors[k]:'transparent';
      b.style.color=k===key?'#E6EDF3':'#718096';
      b.style.fontWeight=k===key?'700':'400';
    }
  });
}
</script>"""
    return (
        f'<section class="rte5-section" id="cenarios-xtech">'
        f'<div class="rte5-section-head"><h2 class="rte5-title">Cenários por Frente xTech</h2>'
        f'<span class="rte5-note">O que pode acontecer nos próximos 6–12 meses?</span></div>'
        f'<div style="font-size:.82rem;color:#A8B3C2;line-height:1.6;padding:0 0 16px 0">Cenário não é previsão. É hipótese estruturada sob incerteza — construída a partir de sinais acumulados e memória histórica do Radar.</div>'
        f'<div class="rte5-grid-3">{cards_html}</div>'
        f'{tab_js}'
        f'</section>'
    )
def render_convergencia_v7(data: Dict[str, Any]) -> str:
    conv = get_convergencia_v7(data)
    if not conv:
        return render_convergence(data)
    _, by_title = _build_items_index(data)
    cards = []
    for c_item in conv:
        titulo = c_item.get("titulo") or "Convergência"
        nivel = c_item.get("nivel") or "—"
        nivel_color = _NIVEL_COLORS.get(nivel, C_MUTED)
        num_sinais = c_item.get("num_sinais") or 0
        xtechs_env = c_item.get("xtech_envolvidas") or []
        narrativa = c_item.get("narrativa") or ""
        sinais_rel = c_item.get("sinais_relacionados") or []
        xt_chips = "".join(chip(xt, _XTECH_COLORS_V7.get(xt, C_MUTED)) for xt in xtechs_env)
        n_display = len(sinais_rel) if sinais_rel else num_sinais
        # Resolve URLs cruzando título com índice de itens
        fontes_items = []
        for titulo_sinal in sinais_rel:
            item = by_title.get(titulo_sinal.lower().strip())
            if item:
                fontes_items.append(item)
            else:
                fontes_items.append({"titulo_pt": titulo_sinal, "link": "", "fonte": ""})
        fontes_block = _render_fontes_block(fontes_items)
        cards.append(f"""
<div class="rte5-conv-card">
  <div class="rte5-conv-head">
    <div class="rte5-conv-nivel" style="color:{nivel_color}">{h(nivel)}</div>
    {chip(f"{n_display} sinais", nivel_color)}
  </div>
  <div class="rte5-conv-titulo">{h(titulo)}</div>
  <div class="rte5-chip-row">{xt_chips}</div>
  <div class="rte5-conv-narrativa">{h(narrativa)}</div>
  {fontes_block}
</div>""")
    return f"""
<section class="rte5-section" id="convergencia">
  <div class="rte5-section-head"><h2 class="rte5-title">Motor de Convergência Estratégica</h2></div>
  <div class="rte5-grid-3">{''.join(cards)}</div>
</section>"""


_LENTE_PROFILES = [
    {"perfil": "Gestor de Risco / CRO",                "cor": "#D96C6C"},
    {"perfil": "Empreendedor / Fundador",              "cor": "#79A3FF"},
    {"perfil": "Especialista Técnico / Engenheiro",    "cor": "#2FA87C"},
    {"perfil": "Investidor / Alocador",                "cor": "#D9A441"},
    {"perfil": "Head de Compliance / ESG / Jurídico",  "cor": "#5B8CFF"},
    {"perfil": "Conselheiro / Executivo",              "cor": "#A78BFA"},
]

_LENTE_SYSTEM_PROMPT = """Você receberá os dados de um ciclo do Radar xTech.
Gere o conteúdo para os 6 cards da seção "Lente de Decisão".
Para cada perfil abaixo, produza exatamente três campos:
  - sinal: uma frase sobre o sinal mais relevante do ciclo para este perfil
  - decisao: uma frase sobre a ação que não pode esperar (semanas, não trimestres)
  - risco: uma frase sobre o risco que provavelmente ainda não está no modelo deste perfil
Perfis:
1. Gestor de Risco / CRO
2. Empreendedor / Fundador
3. Especialista Técnico / Engenheiro
4. Investidor / Alocador
5. Head de Compliance / ESG / Jurídico
6. Conselheiro / Executivo
Regras:
- Cada frase: máximo 25 palavras
- Sem jargão vazio — cada frase deve conter uma afirmação específica e verificável
- Não repetir o mesmo sinal em perfis diferentes
- Retornar JSON puro, sem markdown, sem explicação
Formato de saída:
{"lente_decisao": [{"perfil": "Gestor de Risco / CRO", "sinal": "...", "decisao": "...", "risco": "..."}, ...]}"""


def _build_lente_context(data: Dict[str, Any]) -> str:
    dash = get_dashboard(data)
    vetores = get_vetores(data)[:4]
    convergencias = (data.get("convergencias") or data.get("convergencia") or {})
    if isinstance(convergencias, list):
        convergencias = {"itens": convergencias}
    tese = dash.get("executive_thesis") or {}
    ctx: Dict[str, Any] = {
        "ciclo": dash.get("ciclo") or data.get("ciclo_id"),
        "total_sinais": dash.get("total_sinais"),
        "executive_thesis": tese.get("frase_central") or "",
        "mudancas_estruturais": (tese.get("mudancas_estruturais") or [])[:3],
        "vetores_top": [
            {k: v.get(k) for k in ("titulo", "nome", "quadrante_executivo", "decisao_recomendada", "custo_espera")}
            for v in vetores
        ],
        "convergencias_resumo": str(convergencias)[:600],
    }
    return json.dumps(ctx, ensure_ascii=False)


def generate_lente_decisao(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Chama LLM para gerar os 5 cards da Lente de Decisão. Retorna lista ou fallback vazio."""
    try:
        from llm_client import call_llm
        ctx_str = _build_lente_context(data)
        result = call_llm(
            system=_LENTE_SYSTEM_PROMPT,
            user=f"Dados do ciclo:\n{ctx_str}",
            json_schema=None,
            schema_name=None,
        )
        if isinstance(result, dict) and "lente_decisao" in result:
            return result["lente_decisao"]
        if isinstance(result, str):
            parsed = json.loads(result)
            return parsed.get("lente_decisao", [])
    except Exception as exc:
        print(f"[lente_decisao] geração via LLM falhou ({exc}); usando fallback vazio.")
    return []


def render_lente_decisao(data: Dict[str, Any]) -> str:
    """Bloco 4 — Lente de Decisão: 5 cards por perfil de decisor."""
    cards_data = data.get("lente_decisao") or []
    cards_html = []
    for i, profile in enumerate(_LENTE_PROFILES):
        cor = profile["cor"]
        perfil_label = profile["perfil"]
        content = cards_data[i] if i < len(cards_data) else {}
        sinal   = h(content.get("sinal", "—"))
        decisao = h(content.get("decisao", "—"))
        risco   = h(content.get("risco", "—"))
        cards_html.append(
            f'<div class="rte5-card rte5-lente-card" style="--c:{cor}">'
            f'<div class="rte5-lente-perfil">{h(perfil_label)}</div>'
            f'<div class="rte5-lente-sinal"><span class="rte5-lente-label">Sinal deste ciclo</span>{sinal}</div>'
            f'<div class="rte5-lente-decisao"><span class="rte5-lente-label">Decisão que não pode esperar</span>{decisao}</div>'
            f'<div class="rte5-lente-risco"><span class="rte5-lente-label">Risco fora do seu modelo</span>{risco}</div>'
            f'</div>'
        )
    return (
        f'<section class="rte5-section" id="lente-decisao">'
        f'<div class="rte5-section-head">'
        f'<h2 class="rte5-title">Lente de Decisão</h2>'
        f'<span class="rte5-note">Para quem este ciclo fala — e o que não pode esperar</span>'
        f'</div>'
        f'<div class="rte5-grid-3">{"".join(cards_html)}</div>'
        f'</section>'
    )


def render_cta(data: Dict[str, Any]) -> str:
    return """
<section class="rte5-section" id="cta">
  <div style="background:linear-gradient(135deg,rgba(47,168,124,.07),rgba(91,140,255,.05));border:1px solid rgba(47,168,124,.22);border-radius:20px;padding:40px 36px;max-width:620px;margin:0 auto;text-align:center">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:.64rem;text-transform:uppercase;letter-spacing:.14em;color:#2FA87C;margin-bottom:14px">efagundes.com · Think Tank</div>
    <div style="font-size:1.18rem;font-weight:700;color:#E6EDF3;line-height:1.38;margin-bottom:12px">Quer aplicar esta análise nas decisões da sua empresa?</div>
    <div style="font-size:.86rem;color:#A8B3C2;line-height:1.7;margin-bottom:28px">O Radar xTech é atualizado a cada ciclo de inteligência.<br>Entre em contato para transformar sinal em decisão.</div>
    <a href="https://efagundes.com/contato/"
       style="display:inline-block;background:#2FA87C;color:#fff;font-weight:700;font-size:.92rem;padding:13px 32px;border-radius:10px;text-decoration:none;letter-spacing:.02em">
      Entrar em contato →
    </a>
  </div>
</section>"""


# ─── Cabeçalho de Horizonte (v9) ─────────────────────────────────────────────

def render_horizonte_header(n: int, nome: str, audiencia: str, pergunta: str) -> str:
    """Renderiza o cabeçalho divisor de horizonte conforme REQ-06 / xtech-system-spec-v1 seção 4.6.2."""
    return (
        f'<div class="radar-horizonte-header">'
        f'<span class="horizonte-label">Horizonte {h(str(n))} · {h(nome)}</span>'
        f'<span class="horizonte-audiencia">Para: {h(audiencia)}</span>'
        f'<span class="horizonte-pergunta">&#8220;{h(pergunta)}&#8221;</span>'
        f'</div>'
    )


# ─── Parágrafos-resposta dos horizontes (v10) ────────────────────────────────

def _render_horizonte_resposta_h2(data: Dict[str, Any]) -> str:
    """Parágrafo executivo que responde 'Qual é a tendência de 6–18 meses? Onde estão os riscos?'
    Fontes: sala_situacao_com_acao (SCR) e convergencia."""
    scr = (data.get("v31_horizonte1") or {}).get("sala_situacao_com_acao") or {}
    complicacao  = clean_text(scr.get("complicacao") or "")
    acoes        = scr.get("acao_recomendada") or []
    urgencia     = scr.get("urgencia") or ""

    # Fallback: usar convergencia
    conv_list = (data.get("convergencias") or data.get("convergencia") or {})
    if isinstance(conv_list, dict):
        conv_list = conv_list.get("convergencias") or []
    conv_text = ""
    if conv_list and isinstance(conv_list, list) and len(conv_list) > 0:
        first = conv_list[0]
        conv_text = clean_text(
            first.get("tese") or first.get("descricao") or first.get("convergencia") or ""
        )

    body = complicacao or conv_text
    if not body:
        return ""

    urgencia_badge = ""
    if urgencia:
        urg_color = {"alta": "#D96C6C", "media": "#D4A017", "baixa": "#2FA87C"}.get(urgencia.lower(), C_MUTED)
        urgencia_badge = (
            f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:.65rem;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:.10em;'
            f'color:{urg_color};margin-left:10px">● urgência {urgencia}</span>'
        )

    acoes_html = ""
    if acoes:
        items_html = "".join(
            f'<li style="margin-bottom:4px">{h(a)}</li>'
            for a in (acoes[:3] if isinstance(acoes, list) else [str(acoes)])
        )
        acoes_html = (
            f'<ul style="margin:10px 0 0 16px;font-size:.82rem;color:#8B99A8;line-height:1.65">'
            f'{items_html}</ul>'
        )

    return (
        f'<div style="width:100%;margin:0 0 22px;padding:16px 20px;'
        f'background:rgba(91,140,255,.06);border-left:3px solid #5B8CFF;'
        f'border-radius:0 10px 10px 0">'
        f'<div style="font-size:.84rem;color:#A8B3C2;line-height:1.70">'
        f'{h(body)}{urgencia_badge}'
        f'</div>'
        f'{acoes_html}'
        f'</div>'
    )


def _render_horizonte_resposta_h3(data: Dict[str, Any]) -> str:
    """Parágrafo executivo que responde 'Qual oportunidade posso capturar? Quais competências preciso?'
    Fontes: aplicacoes_corporativas e sala_situacao_com_acao.acao_recomendada."""
    apl = data.get("aplicacoes_corporativas") or {}
    desc_geral = clean_text(apl.get("descricao_geral") or apl.get("descricao") or "")

    scr = (data.get("v31_horizonte1") or {}).get("sala_situacao_com_acao") or {}
    acoes = scr.get("acao_recomendada") or []

    body = desc_geral
    if not body and acoes:
        body = acoes[0] if isinstance(acoes, list) else str(acoes)
    if not body:
        return ""

    competencias = []
    for item in (apl.get("aplicacoes") or [])[:3]:
        comp = item.get("competencias_necessarias") or item.get("competencias") or []
        if isinstance(comp, list):
            competencias.extend(comp[:2])
        elif isinstance(comp, str):
            competencias.append(comp)
    comp_html = ""
    if competencias:
        badges = "".join(
            f'<span style="display:inline-block;padding:3px 9px;margin:3px 4px 0 0;'
            f'border-radius:999px;font-size:.70rem;font-family:\'IBM Plex Mono\',monospace;'
            f'background:rgba(47,168,124,.12);border:1px solid rgba(47,168,124,.28);color:#2FA87C">'
            f'{h(c)}</span>'
            for c in dict.fromkeys(competencias)  # deduplica mantendo ordem
        )
        comp_html = f'<div style="margin-top:10px">{badges}</div>'

    return (
        f'<div style="width:100%;margin:0 0 22px;padding:16px 20px;'
        f'background:rgba(47,168,124,.06);border-left:3px solid #2FA87C;'
        f'border-radius:0 10px 10px 0">'
        f'<div style="font-size:.84rem;color:#A8B3C2;line-height:1.70">{h(body)}</div>'
        f'{comp_html}'
        f'</div>'
    )


# ─── Render principal ─────────────────────────────────────────────────────────

def render_html(data: Dict[str, Any], hist_data: Dict[str, Any] | None = None) -> str:
    # hist_data vem embutido no intel_output.json (gerado pelo analyzer 6.5.11).
    # O parâmetro opcional mantém compatibilidade com chamadas legadas.
    hist_data = hist_data or data.get("hist_data") or {}
    anchors = get_graph_anchors(data)

    def _with_anchor(section_html: str, anchor_key: str) -> str:
        anchor = anchors.get(anchor_key) or ""
        if not anchor:
            return section_html
        return section_html.replace(
            '<div class="rte5-section-head">',
            f'{_anchor_block(anchor)}\n<div class="rte5-section-head">',
            1,
        )

    # v10 — estrutura por três horizontes de uso (REQ-06 / xtech-system-spec-v1 seção 4.6)
    graph_html = _with_anchor(render_intel_graph(hist_data),        "xtech_graph")
    curva_html = _with_anchor(render_curva_convergencia(hist_data), "maturity_curve")
    map_html   = _with_anchor(render_map(data),                     "pressure_map")

    h1 = render_horizonte_header(
        1,
        "Operação e monitoramento",
        "analistas, gestores, consultores",
        "O que mudou hoje? O que devo monitorar?",
    )
    h2 = render_horizonte_header(
        2,
        "Planejamento estratégico",
        "diretores, C-level, comitês de estratégia",
        "Qual é a tendência de 6–18 meses? Onde estão os riscos?",
    )
    h3 = render_horizonte_header(
        3,
        "Desenvolvimento de projetos",
        "empreendedores, consultores, equipes de consultoria",
        "Qual oportunidade posso capturar? Quais competências preciso?",
    )

    # v10: parágrafos-resposta executivos para H2 e H3
    h2_resposta = _render_horizonte_resposta_h2(data)
    h3_resposta = _render_horizonte_resposta_h3(data)

    return (
        f'<div class="rte5">'
        f'{_css()}'
        f'{render_header(data)}'

        # ── HORIZONTE 1: Operação e monitoramento ─────────────────────────────
        # v10: cabeçalho H1 ANTES da Sala de Situação (lógica do horizonte)
        f'{h1}'
        # H1·B0 — Sala de Situação Executiva
        f'{render_situation_room_v8(data)}'
        # H1·B1 — Lente de Decisão
        f'{render_lente_decisao(data)}'
        # H1·B2 — Vetores Prioritários (Mapa de Pressão Estratégica × Janela de Decisão)
        f'{map_html}'
        # v10: base_evidencia removida (redundante com Sala de Situação)

        # ── HORIZONTE 2: Planejamento estratégico ─────────────────────────────
        f'{h2}'
        # v10: parágrafo-resposta — tendência + riscos (SCR / convergência)
        f'{h2_resposta}'
        # H2·B4 — Grafo de Inteligência (intro embutido dentro da section do grafo)
        f'{graph_html}'
        # H2·B5 — Motor de Convergência Estratégica
        f'{render_convergencia_v7(data)}'
        # H2·B6 — Cenários por Frente xTech
        f'{render_cenarios_xtech(data)}'

        # ── HORIZONTE 3: Desenvolvimento de projetos ──────────────────────────
        f'{h3}'
        # v10: parágrafo-resposta — oportunidades + competências (aplicações corporativas)
        f'{h3_resposta}'
        # H3·B7 — Curva de Maturidade xTech
        f'{curva_html}'
        # H3·B8 — Análise Reversa de Competências — versão pública
        f'{render_aplicacoes_corporativas(data)}'
        # H3·B9 — CTA · efagundes.com · think tank
        f'{render_cta(data)}'

        f'{render_footer(data)}'
        f'</div>'
    )
# ─── main ─────────────────────────────────────────────────────────────────────

def _avaliar_freshness(data: Dict[str, Any]) -> Dict[str, Any]:
    """Detecta quais componentes principais usaram fallback estático vs dado vivo.

    Retorna dict com fallback_count, fallback_components e radar_quality
    ('ok' | 'degraded'). Threshold: >= 3 fallbacks = degraded.
    """
    hist = data.get("hist_data") or {}
    fallback: list[str] = []

    # Hype Cycle: live se hype_cycle_live presente com ao menos 1 narrativa atualizada
    hype_live = hist.get("hype_cycle_live") or []
    if not hype_live:
        fallback.append("hype_cycle (sem hype_cycle_live)")
    elif not any(t.get("updated") for t in hype_live):
        fallback.append("hype_cycle (nenhuma narrativa atualizada neste ciclo)")

    # Pressão por Frente: live se pressure_weeks do banco (não FALLBACK_PRESSURE_WEEKS)
    if not hist.get("pressure_weeks"):
        fallback.append("pressure_weeks")

    # Grafo Intel: live se memories e entities do banco
    if not hist.get("memories"):
        fallback.append("intel_graph_memories")
    if not hist.get("entities"):
        fallback.append("intel_graph_entities")

    # Enriquecimento: live se enrichment_audit do analyzer indica qualidade ok
    enrichment = data.get("enrichment_audit") or {}
    if enrichment.get("radar_quality") == "degraded":
        fallback.append(
            f"enrichment ({enrichment.get('fallback_count', '?')} blocos em fallback)"
        )
    elif not enrichment:
        # analyzer antigo sem enrichment_audit — não penaliza, só informa
        pass

    return {
        "fallback_count":      len(fallback),
        "fallback_components": fallback,
        "radar_quality":       "degraded" if len(fallback) >= 3 else "ok",
        "hype_cycle_live":     bool(hype_live),
        "pressure_weeks_live": bool(hist.get("pressure_weeks")),
        "memories_live":       bool(hist.get("memories")),
    }


def _fmt_mkt_valor(valor: Any, unidade: str) -> str:
    """Formata valor numérico de mercado."""
    try:
        v = float(valor)
        if unidade in ("%", "% a.a."):
            return f"{v:.2f}%"
        if v > 10_000:
            return f"{v:,.0f}"
        if v > 10:
            return f"{v:.2f}"
        return f"{v:.4f}"
    except (TypeError, ValueError):
        return str(valor)


def _mkt_delta_html(var30: Any, d_color_pos: str, d_color_neg: str, mono: bool = False) -> str:
    """Gera span de variação 30d."""
    if var30 is None:
        return ""
    try:
        d = float(var30)
        color = d_color_pos if d >= 0 else d_color_neg
        arrow = "▲" if d >= 0 else "▼"
        family = "font-family:'IBM Plex Mono','Courier New',monospace;" if mono else ""
        return f'<span style="{family}font-size:11px;color:{color};">{arrow} {abs(d):.1f}% 30d</span>'
    except (TypeError, ValueError):
        return ""


# ─── CSS compartilhado dos heroes ────────────────────────────────────────────

_HERO_FONT_IMPORT = "@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&display=swap');"


def _hero_ef_style(uid: str) -> str:
    """CSS com media queries para o hero efagundes.com."""
    return f"""<style>
{_HERO_FONT_IMPORT}
.{uid} {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: {C_BG};
  border-radius: 14px;
  padding: clamp(20px, 4vw, 36px);
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  box-sizing: border-box;
  color: {C_TEXT};
  line-height: 1.5;
}}
.{uid} .rxt-eyebrow {{
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 11px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: {C_MUTED};
  margin: 0;
}}
.{uid} .rxt-eyebrow--accent {{ color: inherit; }}
.{uid} .rxt-headline {{
  font-size: clamp(28px, 4.5vw, 52px);
  font-weight: 800;
  letter-spacing: -0.04em;
  line-height: 1.04;
  color: #ffffff;
  margin: 6px 0 8px;
}}
.{uid} .rxt-desc {{
  font-size: clamp(13px, 1.6vw, 16px);
  color: {C_MUTED};
  line-height: 1.6;
  margin: 0 0 12px;
  max-width: 72ch;
}}
.{uid} .rxt-mono-val {{
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: clamp(18px, 2.8vw, 28px);
  font-weight: 700;
  line-height: 1;
  color: #ffffff;
  margin: 0 0 2px;
}}
.{uid} .rxt-sep {{
  border: none;
  border-top: 1px solid rgba(255,255,255,.07);
  margin: 16px 0;
}}
.{uid} .rxt-mkt-grid {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}}
.{uid} .rxt-mkt-card {{
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 10px;
  padding: 13px 15px;
}}
.{uid} .rxt-score-badge {{
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 12px;
  font-weight: 700;
  border-radius: 5px;
  padding: 3px 9px;
  background: rgba(255,255,255,.06);
  flex-shrink: 0;
  min-width: 34px;
  text-align: center;
  line-height: 1.6;
}}
.{uid} .rxt-sinal-titulo {{
  font-size: clamp(13px, 1.5vw, 15px);
  color: {C_TEXT};
  margin: 0 0 2px;
  line-height: 1.4;
}}
.{uid} .rxt-cta {{
  display: inline-block;
  font-size: 14px;
  font-weight: 700;
  padding: 12px 24px;
  border-radius: 8px;
  text-decoration: none;
  letter-spacing: .04em;
  white-space: nowrap;
}}
@media (min-width: 760px) {{
  .{uid} .rxt-mkt-grid {{ grid-template-columns: repeat(3, 1fr); }}
}}
@media (max-width: 759px) {{
  .{uid} .rxt-mkt-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
@media (max-width: 480px) {{
  .{uid} {{ padding: 16px; border-radius: 10px; }}
  .{uid} .rxt-headline {{ letter-spacing: -0.03em; }}
  .{uid} .rxt-cta {{ display: block; width: 100%; box-sizing: border-box; text-align: center; }}
  .{uid} .rxt-mkt-grid {{ grid-template-columns: 1fr 1fr; gap: 8px; }}
}}
</style>"""


def _hero_nm_style(uid: str) -> str:
    """CSS com media queries para o hero nMentors."""
    return f"""<style>
{_HERO_FONT_IMPORT}
.{uid} {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: {C_BG};
  border-radius: 14px;
  padding: clamp(20px, 4vw, 36px);
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  box-sizing: border-box;
  color: {C_TEXT};
  line-height: 1.5;
}}
.{uid} .rxt-eyebrow {{
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 11px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: {C_MUTED};
  margin: 0;
}}
.{uid} .rxt-headline {{
  font-size: clamp(24px, 4vw, 44px);
  font-weight: 800;
  letter-spacing: -0.035em;
  line-height: 1.07;
  color: #ffffff;
  margin: 4px 0 0;
}}
.{uid} .rxt-pergunta {{
  font-size: clamp(15px, 2.2vw, 21px);
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.5;
  color: #ffffff;
  font-style: italic;
  margin: 0;
}}
.{uid} .rxt-sep {{
  border: none;
  border-top: 1px solid rgba(255,255,255,.07);
  margin: 16px 0;
}}
.{uid} .rxt-mkt-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}}
.{uid} .rxt-mkt-card {{
  background: rgba(255,255,255,.05);
  border: 1px solid rgba(255,255,255,.08);
  border-radius: 10px;
  padding: 12px 14px;
}}
.{uid} .rxt-mono-val {{
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: clamp(15px, 2.2vw, 20px);
  font-weight: 700;
  line-height: 1.1;
  color: #ffffff;
  margin: 2px 0;
}}
.{uid} .rxt-cta {{
  flex: 1;
  display: block;
  text-align: center;
  font-size: 14px;
  font-weight: 600;
  padding: 12px 20px;
  border-radius: 8px;
  text-decoration: none;
  min-width: 0;
}}
@media (max-width: 640px) {{
  .{uid} {{ padding: 18px; border-radius: 10px; }}
  .{uid} .rxt-mkt-grid {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
  .{uid} .rxt-headline {{ letter-spacing: -0.025em; }}
}}
@media (max-width: 400px) {{
  .{uid} {{ padding: 14px; }}
  .{uid} .rxt-mkt-grid {{ grid-template-columns: 1fr 1fr; }}
  .{uid} .rxt-cta-row {{ flex-direction: column; }}
  .{uid} .rxt-cta {{ text-align: center; }}
}}
</style>"""


def render_hero(data: Dict[str, Any], ciclo: str, radar_url: str = "https://efagundes.com/radar-xtechs") -> str:
    """Hero efagundes.com — cópia do primeiro bloco do Radar (Sala de Situação Executiva)."""
    ciclo_s = h(ciclo)
    return (
        f"<!-- hero-radar-xtechs-{ciclo_s} | efagundes.com · think tank -->"
        + '<div class="rte5">'
        + _css()
        + render_situation_room_v8(data)
        + "</div>"
        + f"<!-- /hero-radar-xtechs-{ciclo_s} -->"
    )



def render_hero_nmentors(
    data: Dict[str, Any],
    ciclo: str,
    radar_url: str = "https://efagundes.com/radar-xtechs",
    nmentors_url: str = "https://nmentors.com.br",
) -> str:
    """Hero nMentors.com.br — 'Briefing do Mentor'.

    Tipografia fluid: headline tight 22–36px, pergunta estratégica em destaque
    (15–19px itálico), IBM Plex Mono para eyebrows e valores de mercado.
    CSS grid 4→2 colunas para mercado. Responsivo 520px e 380px.

    Usa strategic_briefing quando disponível. Sem ele, gera hero funcional sem
    a pergunta estratégica.
    """
    vetores = sort_vetores_for_priority(get_vetores(data))
    top_vetor = vetores[0] if vetores else {}

    tech_nome      = h(top_vetor.get("xtech") or top_vetor.get("frente") or "xTech")
    tech_frente    = top_vetor.get("frente", "")
    tech_color     = FRENTE_COLORS.get(tech_frente, C_TECH)
    tech_quadrante = h(top_vetor.get("quadrante_executivo") or "")
    pressao_color  = C_DANGER if float(top_vetor.get("pressao_estrategica") or 0) >= 8 else C_AMBER

    sb = data.get("strategic_briefing") or {}
    pergunta     = h(sb.get("pergunta_estrategica") or "")
    ctx_pergunta = h(sb.get("contexto_pergunta") or "")
    sinais_atencao: list = sb.get("sinais_atencao") or []
    market_data: Dict[str, Any] = sb.get("market_data") or {}

    hero_data   = data.get("hero") or {}
    hero_kicker = hero_data.get("kicker") or ""
    hero_manchete = hero_data.get("manchete") or ""
    hero_deck   = hero_data.get("deck") or ""

    if not sinais_atencao:
        itens = sorted(get_itens(data), key=lambda x: float(x.get("score_final") or 0), reverse=True)
        sinais_atencao = [
            {"titulo": h(it.get("titulo") or it.get("title") or ""),
             "contexto": h(it.get("resumo") or ""),
             "tipo": "oportunidade"}
            for it in itens[:3]
        ]

    tipo_icons  = {"oportunidade": "▲", "risco": "▼", "ruptura": "◆"}
    tipo_colors = {"oportunidade": C_GREEN, "risco": C_DANGER, "ruptura": C_AMBER}

    sinais_html = ""
    for sinal in sinais_atencao[:3]:
        tipo  = (sinal.get("tipo") or "oportunidade").lower()
        icon  = tipo_icons.get(tipo, "●")
        color = tipo_colors.get(tipo, C_TECH)
        sinais_html += f"""
    <div style="display:flex;gap:12px;align-items:flex-start;padding:10px 12px;background:rgba(255,255,255,.04);border-radius:9px;border:1px solid rgba(255,255,255,.07);">
      <span style="font-family:'IBM Plex Mono','Courier New',monospace;font-size:13px;color:{color};flex-shrink:0;margin-top:2px;">{icon}</span>
      <div>
        <p style="margin:0 0 3px;font-size:clamp(12px,2vw,14px);font-weight:600;color:{C_TEXT};line-height:1.35;">{h(sinal.get('titulo', ''))}</p>
        <p style="margin:0;font-size:12px;color:{C_MUTED};line-height:1.55;">{h(sinal.get('contexto', ''))}</p>
      </div>
    </div>"""

    # Market grid (4 colunas → 2 no mobile)
    mkt_chaves = [
        ("Selic Real", "Selic Real"),
        ("USD/BRL",    "USD/BRL"),
        ("Ibovespa",   "Ibovespa"),
        ("Brent",      "Brent"),
    ]
    mkt_cards_html = ""
    n_mkt = 0
    for chave, label in mkt_chaves:
        if chave not in market_data or n_mkt >= 4:
            continue
        s   = market_data[chave]
        val = s.get("valor")
        if val is None:
            continue
        vfmt = _fmt_mkt_valor(val, s.get("unidade", ""))
        d30  = _mkt_delta_html(s.get("variacao_30d_pct"), C_GREEN, C_DANGER, mono=True)
        mkt_cards_html += f"""
      <div class="rxt-mkt-card">
        <p class="rxt-eyebrow" style="margin-bottom:4px;">{h(label)}</p>
        <p class="rxt-mono-val">{h(vfmt)}</p>
        {d30}
      </div>"""
        n_mkt += 1

    mkt_section = ""
    if mkt_cards_html:
        mkt_section = f"""
  <hr class="rxt-sep">
  <div class="rxt-mkt-grid">{mkt_cards_html}
  </div>"""

    # Semana ISO
    try:
        from datetime import date as _date
        semana = f"Semana {_date.fromisoformat(ciclo[:10]).isocalendar().week} · {ciclo[:4]}"
    except Exception:
        semana = ciclo

    # Pré-computa seções com aspas aninhadas (evita f-string dentro de f-string)
    quad_badge = (
        f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:10px;'
        f'background:{pressao_color};color:#fff;border-radius:3px;padding:2px 6px;">'
        f'{tech_quadrante}</span>'
    ) if tech_quadrante else ""

    ctx_html = (
        f'<p style="margin:8px 0 0;font-size:12px;color:{C_MUTED};">{ctx_pergunta}</p>'
    ) if ctx_pergunta else ""

    pergunta_section = (
        f'<div style="background:{C_BG2};border-radius:10px;padding:16px 18px;'
        f'margin-bottom:16px;border-left:3px solid {C_TECH};">'
        f'<p class="rxt-eyebrow" style="color:{C_TECH};margin-bottom:8px;">'
        f'Pergunta estratégica desta semana</p>'
        f'<p class="rxt-pergunta">&#8220;{pergunta}&#8221;</p>'
        f'{ctx_html}'
        f'</div>'
    ) if pergunta else ""

    uid = "rxt-nm"

    return f"""<!-- hero-nmentors-{h(ciclo)} | nMentors.com.br · briefing do mentor -->
{_hero_nm_style(uid)}
<div class="{uid}">

  <!-- Header -->
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
    <div>
      <p class="rxt-eyebrow">nMentors · Briefing do Mentor{(" · " + h(hero_kicker)) if hero_kicker else ""}</p>
      <h2 class="rxt-headline">{h(hero_manchete) if hero_manchete else "O que monitorar<br>esta semana"}</h2>
      {(f'<p style="margin:6px 0 0;font-size:.85rem;color:#A8B3C2;line-height:1.55;font-style:italic">{h(hero_deck)}</p>') if hero_deck else ""}
    </div>
    <span class="rxt-eyebrow" style="white-space:nowrap;">{h(semana)}</span>
  </div>

  <!-- xTech badge -->
  <div style="display:inline-flex;align-items:center;gap:8px;background:{C_BG2};border-radius:8px;padding:8px 14px;margin-bottom:16px;border-left:3px solid {tech_color};">
    <span class="rxt-eyebrow" style="color:{tech_color};">xTech</span>
    <span style="font-size:15px;font-weight:700;color:#fff;letter-spacing:-.02em;">{tech_nome}</span>
    {quad_badge}
  </div>

  <!-- Pergunta estratégica -->
  {pergunta_section}

  <!-- Sinais de atenção -->
  <p class="rxt-eyebrow" style="margin-bottom:10px;">Sinais de atenção para suas sessões</p>
  <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:0;">{sinais_html}
  </div>
{mkt_section}
  <!-- CTAs -->
  <hr class="rxt-sep">
  <div class="rxt-cta-row" style="display:flex;gap:10px;flex-wrap:wrap;">
    <a class="rxt-cta" href="{radar_url}" target="_blank" rel="noopener"
       style="background:{C_TECH};color:#fff;">
      Ver análise completa no Radar →
    </a>
    <a class="rxt-cta" href="{nmentors_url}" target="_blank" rel="noopener"
       style="background:rgba(255,255,255,.07);color:{C_TEXT};border:1px solid rgba(255,255,255,.12);">
      Usar na sessão de mentoria
    </a>
  </div>

</div>
<!-- /hero-nmentors-{h(ciclo)} -->"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Gerar Radar xTechs v11 em HTML para WordPress")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Arquivo intel_output.json")
    parser.add_argument("--output", default=None, help="Arquivo HTML de saída")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Diretório de saída")
    args = parser.parse_args()

    data = load_json(args.input)
    dash = get_dashboard(data)
    ciclo = dash.get("ciclo") or data.get("ciclo_id") or datetime.now(BRASILIA).strftime("%Y-%m-%d")

    hist_data_local = data.get("hist_data") or {}
    n_mem   = len(hist_data_local.get("memories", []))
    n_zt    = len(hist_data_local.get("zettels",  []))
    n_lente = len(data.get("lente_decisao") or [])
    n_hype_live = len(hist_data_local.get("hype_cycle_live") or [])
    print(f"intel_output.json: hist_data={bool(hist_data_local)} "
          f"({n_mem} memórias, {n_zt} zettels), lente_decisao={n_lente} cards, "
          f"hype_cycle_live={n_hype_live} techs")

    # ── Detector de freshness ────────────────────────────────────────────────
    _freshness = _avaliar_freshness(data)
    if _freshness["radar_quality"] == "degraded":
        print(
            f"  ⚠ QUALIDADE DEGRADADA: {_freshness['fallback_count']} componentes "
            f"usando fallback estático: {', '.join(_freshness['fallback_components'])}"
        )
    else:
        print(f"  ✓ Qualidade do Radar: {_freshness['radar_quality']} "
              f"({_freshness['fallback_count']} fallbacks)")

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"radar-xtechs-{ciclo}.html"

    # Gera Lente de Decisão via LLM se vazia ou com conteúdo placeholder
    _lente_raw = data.get("lente_decisao") or []
    _lente_vazia = not _lente_raw or all(
        c.get("sinal", "—") in ("—", "", None) for c in _lente_raw
    )
    if _lente_vazia:
        print("[lente_decisao] gerando cards via LLM...")
        data["lente_decisao"] = generate_lente_decisao(data)
        n_lente = len(data["lente_decisao"])
        print(f"[lente_decisao] {n_lente} cards gerados")

    html_out = render_html(data)

    # Âncora semântica (opcional)
    try:
        from ancora_semantica_v1 import render_ancora
        ancora_html = render_ancora("efagundes", ciclo)
        ancora_path = output_path.parent / f"ancora-efagundes-{ciclo}.html"
        ancora_path.write_text(ancora_html, encoding="utf-8")
        print(f"Âncora: {ancora_path}")
    except Exception:
        pass

    output_path.write_text(html_out, encoding="utf-8")
    print(f"OK: {output_path} gerado com sucesso")
    print(f"Ciclo: {ciclo}")
    print("Versão: v11 (Radar xTechs — hero block para homepage)")

    # ── Hero block — efagundes.com ───────────────────────────────────────────
    hero_html = render_hero(data, ciclo)
    hero_path = output_path.parent / f"hero-{ciclo}.html"
    hero_path.write_text(hero_html, encoding="utf-8")
    print(f"Hero efagundes.com: {hero_path} gerado com sucesso")

    # ── Hero block — nMentors.com.br (gerar_hero_nmentors.py) ───────────────
    try:
        import importlib.util, sys
        _mod_path = Path(__file__).parent / "gerar_hero_nmentors.py"
        _spec = importlib.util.spec_from_file_location("gerar_hero_nmentors", _mod_path)
        _mod  = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        hero_nm_path = _mod.gerar_hero_nmentors(
            intel_path=args.input,
            output_dir=output_path.parent,
        )
        print(f"Hero nMentors: {hero_nm_path} gerado com sucesso")
    except Exception as _e:
        print(f"⚠ Hero nMentors (novo) falhou ({_e}) — usando fallback legado")
        hero_nm_html = render_hero_nmentors(data, ciclo)
        hero_nm_path = output_path.parent / f"hero-nmentors-{ciclo}.html"
        hero_nm_path.write_text(hero_nm_html, encoding="utf-8")
        print(f"Hero nMentors: {hero_nm_path} (fallback legado)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
