"""
gerar_hero_nmentors.py — Bloco 2 dinâmico nMentors.com.br
Spec: SPEC-hero-nmentors.md v2.0 (aprovada 24/06/2026)
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import html as _html_mod

import anthropic

# ── Constantes ────────────────────────────────────────────────────────────────

MODEL      = "claude-sonnet-4-6"
MAX_TOKENS = 500
TIMEOUT    = 30

FRENTES_ATIVAS = ["EnergyTech", "CleanTech", "DeepTech", "AgriTech"]

CORES_FRENTE: dict[str, str] = {
    "EnergyTech": "#2FA87C",
    "CleanTech":  "#38D6B4",
    "DeepTech":   "#4F83F0",
    "AgriTech":   "#7AB648",
}

URGENCIA_BADGE: dict[str, dict] = {
    "imediata":    {"label": "Agir agora",       "bg": "rgba(217,108,108,.12)", "cor": "#D96C6C"},
    "médio_prazo": {"label": "Pr&oacute;ximos meses", "bg": "rgba(204,154,58,.12)",  "cor": "#CC9A3A"},
    "medio_prazo": {"label": "Pr&oacute;ximos meses", "bg": "rgba(204,154,58,.12)",  "cor": "#CC9A3A"},
    "monitorar":   {"label": "Monitorar",         "bg": "rgba(86,104,128,.12)",   "cor": "#718096"},
}

URGENCIA_ORDEM = {"imediata": 0, "médio_prazo": 1, "medio_prazo": 1, "monitorar": 2}

FALLBACK_CARDS: dict[str, dict] = {
    "EnergyTech": {
        "como_atuamos": "Integramos engenharia de sistemas BESS com modelagem preditiva e gest&atilde;o de projetos P&amp;D junto &agrave; ANEEL, conectando vis&atilde;o estrat&eacute;gica a entregas de campo.",
        "servicos": [
            "Modelagem de despacho e arbitragem para sistemas h&iacute;bridos BESS",
            "Estrutura&ccedil;&atilde;o e execu&ccedil;&atilde;o de P&amp;D regulado ANEEL &mdash; armazenamento",
            "Diagn&oacute;stico de portf&oacute;lio com mapeamento de gaps para integra&ccedil;&atilde;o h&iacute;brida",
        ],
        "cta": "Fale com a nMentors",
    },
    "CleanTech": {
        "como_atuamos": "Apoiamos empresas CleanTech a estruturar P&amp;D regulado e integrar IA em processos de biomassa e refino verde, posicionando clientes antes do mercado se consolidar.",
        "servicos": [
            "Propostas P&amp;D ANEEL &mdash; combust&iacute;veis sustent&aacute;veis e processos de biomassa",
            "Modelagem preditiva com IA para otimiza&ccedil;&atilde;o de rendimento em refino verde",
            "PMO Ag&ecirc;ntico para pipeline de projetos CleanTech",
        ],
        "cta": "Fale com a nMentors",
    },
    "DeepTech": {
        "como_atuamos": "Desenvolvemos solu&ccedil;&otilde;es de gest&atilde;o inteligente de redes e despacho otimizado para ambientes com armazenamento distribu&iacute;do, da modelagem preditiva &agrave; homologa&ccedil;&atilde;o regulat&oacute;ria.",
        "servicos": [
            "G&ecirc;meos digitais para infraestrutura energ&eacute;tica com BESS integrado",
            "Controle preditivo para redes com fontes infl&eacute;x&iacute;veis e armazenamento distribu&iacute;do",
            "P&amp;D regulado ANEEL &mdash; gest&atilde;o inteligente de redes",
        ],
        "cta": "Fale com a nMentors",
    },
    "AgriTech": {
        "como_atuamos": "A press&atilde;o por mat&eacute;rias-primas sustent&aacute;veis cria oportunidade para AgriTechs com log&iacute;stica de biomassa e rastreabilidade de insumos &mdash; a nMentors estrutura e executa esses projetos.",
        "servicos": [
            "PMO para projetos agro-energia e bioeconomia",
            "Rastreabilidade de insumos sustent&aacute;veis com IoT",
            "Capacita&ccedil;&atilde;o t&eacute;cnica em bioenergia e cadeia de biomassa",
        ],
        "cta": "Fale com a nMentors",
    },
}

SYSTEM_PROMPT = """Você gera conteúdo comercial conciso para a nMentors Engenharia, \
consultoria AI-first especializada em projetos de infraestrutura, tecnologia e sustentabilidade.
Capacidades da nMentors:
- Inteligência e Diagnóstico: mapeamento de riscos antes do CAPEX
- Engenharia e Arquitetura: especificação técnica agnóstica de fornecedor
- IA Aplicada: RAG corporativo, agentes autônomos, automação de operação
- PMO e Implantação: gestão com agentes de IA, evidência auditável
- Capacitação e Transferência: metodologia Feynman, avaliação por IA
Clientes-alvo: PMEs de energia, datacenters, EPCs, comercializadoras.
REGRAS ABSOLUTAS:
- Tom executivo, direto, orientado a resultado de negócio
- NUNCA mencione FinTech como área de atuação da nMentors
- NUNCA faça promessas de prazo, custo ou resultado específico
- NUNCA use "AI" — use sempre "IA" ou "inteligência artificial"
- NUNCA mencione "Radar xTech" ou "efagundes.com" nos cards
- Serviços devem ser específicos e acionáveis, não genéricos
- Responda APENAS com JSON válido, sem markdown, sem explicações"""

PROMPT_CARD = """O Radar identificou este impacto para {frente}:
Sinal de referência: {sinal_referencia}
Impacto analisado: {impacto}
Urgência: {urgencia}
Direção: {direcao}

Gere um JSON com exatamente estas chaves:
{{
  "como_atuamos": "2 frases descrevendo como a nMentors atua neste contexto. Foque no resultado de negócio para o cliente.",
  "servicos": ["serviço acionável 1", "serviço acionável 2", "serviço acionável 3"],
  "cta": "Fale com a nMentors"
}}
Formato dos serviços — específico e técnico. Exemplos:
"Modelagem de despacho e arbitragem para sistemas híbridos BESS"
"Estruturação e execução de P&D regulado ANEEL — armazenamento eletroquímico"
"PMO Agêntico para pipeline de projetos CleanTech com rastreabilidade regulatória"
"Gêmeos digitais para infraestrutura energética com BESS integrado"
"Rastreabilidade de insumos sustentáveis com IoT"
"""

# ── SVG icons ─────────────────────────────────────────────────────────────────

SVG_ALERTA = (
    '<svg class="nmb2-sinal-icon" viewBox="0 0 14 14" fill="none">'
    '<path d="M7 1.5L12.5 11H1.5L7 1.5Z" stroke="#D96C6C" stroke-width="1.2" stroke-linejoin="round"/>'
    '<line x1="7" y1="5.5" x2="7" y2="8.5" stroke="#D96C6C" stroke-width="1.2" stroke-linecap="round"/>'
    '<circle cx="7" cy="10" r=".7" fill="#D96C6C"/>'
    '</svg>'
)

SVG_INTERROGACAO = (
    '<svg width="18" height="18" viewBox="0 0 18 18" fill="none">'
    '<circle cx="9" cy="9" r="7.5" stroke="#CC9A3A" stroke-width="1.3" stroke-opacity=".6"/>'
    '<path d="M7 7C7 5.9 7.9 5 9 5C10.1 5 11 5.9 11 7C11 8 10 8.5 9 9.5" stroke="#CC9A3A" stroke-width="1.3" stroke-linecap="round"/>'
    '<circle cx="9" cy="12.5" r=".8" fill="#CC9A3A"/>'
    '</svg>'
)

SVG_DOT_RED = (
    '<svg width="8" height="8" viewBox="0 0 8 8" fill="none">'
    '<circle cx="4" cy="4" r="4" fill="#D96C6C"/>'
    '</svg>'
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _e(text: Any) -> str:
    """HTML-escape e converte para entidades HTML compatíveis com WordPress."""
    return _html_mod.escape(str(text or ""), quote=True)


def _client(api_key: str | None) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        timeout=TIMEOUT,
    )


def _parse_json(raw: str) -> dict:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


# ── LLM — Chamada A ───────────────────────────────────────────────────────────

def _gerar_card_xtech(client: anthropic.Anthropic, frente: str, dados: dict) -> dict:
    prompt = PROMPT_CARD.format(
        frente=frente,
        sinal_referencia=dados.get("sinal_referencia", ""),
        impacto=dados.get("impacto", ""),
        urgencia=dados.get("urgencia", ""),
        direcao=dados.get("direcao", ""),
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.content[0].text)
        # Escape HTML nas strings geradas pelo LLM
        result["como_atuamos"] = _e(result.get("como_atuamos", ""))
        result["servicos"] = [_e(s) for s in (result.get("servicos") or [])]
        result["cta"] = _e(result.get("cta", "Fale com a nMentors"))
        return result
    except Exception as exc:
        print(f"[nm-hero] ⚠ LLM {frente} falhou: {exc} — fallback")
        return FALLBACK_CARDS.get(frente, FALLBACK_CARDS["EnergyTech"])


# ── CSS ───────────────────────────────────────────────────────────────────────

def _css() -> str:
    return """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=IBM+Plex+Mono:wght@400;600;700&display=swap');

.nmb2, .nmb2 * { box-sizing: border-box; margin: 0; padding: 0; }
.nmb2 {
  font-family: 'Inter', sans-serif;
  background: #0B1418;
  border-radius: 16px;
  color: #E8EEF4;
  line-height: 1.6;
  overflow: hidden;
}
.nmb2-header {
  display: flex; align-items: center; gap: 12px;
  flex-wrap: wrap; padding: 22px 32px;
  border-bottom: 1px solid rgba(255,255,255,.07);
  background: rgba(255,255,255,.015);
}
.nmb2-header-left { display: flex; flex-direction: column; gap: 3px; }
.nmb2-eyebrow {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px; letter-spacing: .14em;
  text-transform: uppercase; color: #566880;
}
.nmb2-ciclo { font-size: 14px; font-weight: 700; color: #E8EEF4; }
.nmb2-badge {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px; font-weight: 700;
  border-radius: 4px; padding: 3px 10px;
  display: inline-flex; align-items: center; gap: 5px;
}
.nmb2-tese-wrap {
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 0;
}
.nmb2-tese-left {
  padding: 28px 32px;
  border-right: 1px solid rgba(255,255,255,.07);
}
.nmb2-kicker {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px; font-weight: 700;
  letter-spacing: .16em; text-transform: uppercase;
  color: #38D6B4; margin-bottom: 12px; display: block;
}
.nmb2-manchete {
  font-family: 'Inter', sans-serif !important;
  font-size: clamp(20px, 2.6vw, 30px);
  font-weight: 800; letter-spacing: -.035em;
  line-height: 1.1; color: #E8EEF4;
  margin-bottom: 14px;
}
.nmb2-insight {
  font-size: 15px; color: #96A8BC;
  line-height: 1.7; margin-bottom: 20px;
}
.nmb2-insight strong { color: #C8D6E8; font-weight: 600; }
.nmb2-sinais-label {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px; font-weight: 700;
  letter-spacing: .13em; text-transform: uppercase;
  color: #D96C6C; margin-bottom: 10px; display: block;
}
.nmb2-sinais { display: flex; flex-direction: column; gap: 8px; }
.nmb2-sinal {
  display: flex; align-items: flex-start; gap: 9px;
  font-size: 14px; color: #96A8BC; line-height: 1.5;
}
.nmb2-sinal-icon { width: 14px; height: 14px; flex-shrink: 0; margin-top: 1px; }
.nmb2-tese-right {
  padding: 28px 28px;
  background: rgba(204,154,58,.04);
  display: flex; flex-direction: column;
  justify-content: center; gap: 0;
}
.nmb2-pergunta-kicker {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase;
  color: #CC9A3A; margin-bottom: 14px; display: block;
}
.nmb2-pergunta-icon {
  width: 36px; height: 36px;
  background: rgba(204,154,58,.12);
  border: 1px solid rgba(204,154,58,.25);
  border-radius: 9px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 16px;
}
.nmb2-pergunta {
  font-size: clamp(15px, 1.9vw, 18px);
  font-weight: 700; color: #E8EEF4;
  font-style: italic; line-height: 1.5;
  margin-bottom: 16px;
  padding-left: 14px;
  border-left: 3px solid #CC9A3A;
}
.nmb2-pergunta-ctx { font-size: 13.5px; color: #718096; line-height: 1.6; }
.nmb2-pergunta-ctx strong { color: #96A8BC; font-weight: 600; }
.nmb2-cards-section {
  padding: 28px 32px;
  border-top: 1px solid rgba(255,255,255,.07);
}
.nmb2-sec-title {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px; font-weight: 700;
  letter-spacing: .14em; text-transform: uppercase;
  color: #566880; margin-bottom: 18px;
  display: flex; align-items: center; gap: 10px;
}
.nmb2-sec-title::before {
  content: ''; display: block;
  width: 18px; height: 1px; background: #38D6B4;
}
.nmb2-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0,1fr));
  gap: 14px;
}
.nmb2-card {
  background: #0C1B24;
  border: 1px solid rgba(255,255,255,.07);
  border-radius: 13px; padding: 20px;
  display: flex; flex-direction: column; gap: 14px;
  position: relative; overflow: hidden;
}
.nmb2-card::before {
  content: ''; position: absolute;
  top: 0; left: 0; right: 0; height: 3px;
  background: var(--c);
}
.nmb2-card.nm-monit { opacity: .6; }
.nmb2-card-hd {
  display: flex; align-items: center;
  justify-content: space-between; gap: 8px;
}
.nmb2-frente {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; font-weight: 700;
  letter-spacing: .05em; color: var(--c);
}
.nmb2-urg {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 9px; font-weight: 700;
  border-radius: 4px; padding: 3px 8px;
  background: var(--urg-bg); color: var(--urg-c);
}
.nmb2-como { font-size: 14.5px; color: #96A8BC; line-height: 1.65; flex: 1; }
.nmb2-divider { height: 1px; background: rgba(255,255,255,.06); }
.nmb2-servicos { list-style: none; display: flex; flex-direction: column; gap: 7px; }
.nmb2-srv {
  font-size: 13.5px; color: #C8D6E8;
  display: flex; align-items: flex-start;
  gap: 8px; line-height: 1.45;
}
.nmb2-srv-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--c); flex-shrink: 0; margin-top: 5px; opacity: .7;
}
.nmb2-cta {
  font-size: 13px; font-weight: 700;
  text-decoration: none; color: var(--c) !important;
  display: inline-flex; align-items: center; gap: 5px;
  margin-top: 2px;
}
.nmb2-cta:hover { opacity: .8; }
.nmb2-cta-final {
  margin: 0 32px 28px;
  background: rgba(56,214,180,.05);
  border: 1px solid rgba(56,214,180,.18);
  border-radius: 12px; padding: 22px 26px;
  display: flex; align-items: center;
  justify-content: space-between;
  gap: 16px; flex-wrap: wrap;
}
.nmb2-cta-text { font-size: 15px; font-weight: 600; color: #E8EEF4; }
.nmb2-btns { display: flex; gap: 10px; flex-wrap: wrap; }
.nmb2-btn {
  font-family: 'Inter', sans-serif;
  font-size: 13px; font-weight: 700;
  padding: 11px 22px; border-radius: 8px;
  text-decoration: none; display: inline-block;
  white-space: nowrap; border: none; cursor: pointer;
}
@media (max-width: 800px) {
  .nmb2-tese-wrap { grid-template-columns: 1fr; }
  .nmb2-tese-left { border-right: none; border-bottom: 1px solid rgba(255,255,255,.07); }
  .nmb2-header, .nmb2-tese-left, .nmb2-tese-right, .nmb2-cards-section { padding-left: 20px; padding-right: 20px; }
  .nmb2-cta-final { margin: 0 20px 24px; }
}
@media (max-width: 560px) {
  .nmb2-cards { grid-template-columns: 1fr; }
  .nmb2-cta-final { flex-direction: column; align-items: flex-start; }
}
</style>"""


# ── Renderização ──────────────────────────────────────────────────────────────

def _badge_header(impacto_xtech: dict) -> str:
    frentes_alta = [
        f for f in FRENTES_ATIVAS
        if (impacto_xtech.get(f) or {}).get("urgencia") in ("imediata", "médio_prazo")
        and (impacto_xtech.get(f) or {}).get("direcao") in ("positivo", "ambíguo")
    ]
    frentes_alta.sort(key=lambda f: URGENCIA_ORDEM.get(
        (impacto_xtech.get(f) or {}).get("urgencia", "monitorar"), 2
    ))

    if frentes_alta:
        badge_text = " &middot; ".join(frentes_alta[:2]) + " em alta"
        return (
            f'<span class="nmb2-badge" style="background:rgba(217,108,108,.12);color:#D96C6C;margin-left:auto;">'
            f'{SVG_DOT_RED} {badge_text}'
            f'</span>'
        )
    return (
        '<span class="nmb2-badge" style="background:rgba(86,104,128,.12);color:#718096;margin-left:auto;">'
        'Ciclo de monitoramento'
        '</span>'
    )


def _render_sinais(sinais: list) -> str:
    items = ""
    for s in sinais[:3]:
        titulo = _e((s.get("titulo") or s) if isinstance(s, dict) else str(s))
        items += f'<div class="nmb2-sinal">{SVG_ALERTA}<span>{titulo}</span></div>\n'
    return items


def _render_card(frente: str, urgencia: str, card: dict) -> str:
    cor      = CORES_FRENTE.get(frente, "#38D6B4")
    urg_key  = urgencia.lower().replace(" ", "_").replace("-", "_")
    badge    = URGENCIA_BADGE.get(urg_key, URGENCIA_BADGE["monitorar"])
    monit_cls = " nm-monit" if urg_key == "monitorar" else ""

    servicos_html = "".join(
        f'<li class="nmb2-srv"><span class="nmb2-srv-dot"></span>{s}</li>'
        for s in (card.get("servicos") or [])[:3]
    )
    cta_label = card.get("cta") or "Fale com a nMentors"

    return (
        f'<div class="nmb2-card{monit_cls}" data-frente="{frente}" '
        f'style="--c:{cor};--urg-bg:{badge["bg"]};--urg-c:{badge["cor"]};">\n'
        f'  <div class="nmb2-card-hd">\n'
        f'    <span class="nmb2-frente">{frente}</span>\n'
        f'    <span class="nmb2-urg">{badge["label"]}</span>\n'
        f'  </div>\n'
        f'  <p class="nmb2-como">{card.get("como_atuamos", "")}</p>\n'
        f'  <div class="nmb2-divider"></div>\n'
        f'  <ul class="nmb2-servicos">{servicos_html}</ul>\n'
        f'  <a class="nmb2-cta" href="https://nmentors.com.br/contato/" target="_blank" rel="noopener">'
        f'{cta_label} &rarr;</a>\n'
        f'</div>\n'
    )


def _render_html(data: dict, cards: dict[str, dict]) -> str:
    ciclo_raw = data.get("ciclo_id") or "—"
    try:
        p = ciclo_raw.split("-")
        ciclo_display = f"{p[2]}-{p[1]}-{p[0]}"
    except Exception:
        ciclo_display = ciclo_raw

    impacto_xtech = data.get("impacto_xtech") or {}
    sb            = data.get("strategic_briefing") or {}
    hero          = data.get("hero") or {}

    manchete  = _e(hero.get("manchete") or "")
    insight   = _e(sb.get("insight_executivo") or sb.get("correlacao_mercado") or "")
    pergunta  = _e(sb.get("pergunta_estrategica") or "")
    ctx_perg  = _e(sb.get("contexto_pergunta") or "")
    sinais    = sb.get("sinais_atencao") or []

    # Header
    header = (
        f'<div class="nmb2-header">\n'
        f'  <div class="nmb2-header-left">\n'
        f'    <span class="nmb2-eyebrow">nMentors Engenharia &middot; Briefing do Mentor</span>\n'
        f'    <span class="nmb2-ciclo">Ciclo {_e(ciclo_display)}</span>\n'
        f'  </div>\n'
        f'  {_badge_header(impacto_xtech)}\n'
        f'</div>\n'
    )

    # Tese
    tese = (
        f'<div class="nmb2-tese-wrap">\n'
        f'  <div class="nmb2-tese-left">\n'
        f'    <span class="nmb2-kicker">Tese do ciclo</span>\n'
        f'    <h2 class="nmb2-manchete">{manchete}</h2>\n'
        f'    <p class="nmb2-insight">{insight}</p>\n'
        f'    <span class="nmb2-sinais-label">Sinais de aten&ccedil;&atilde;o neste ciclo</span>\n'
        f'    <div class="nmb2-sinais">\n{_render_sinais(sinais)}    </div>\n'
        f'  </div>\n'
        f'  <div class="nmb2-tese-right">\n'
        f'    <span class="nmb2-pergunta-kicker">A pergunta que seu projeto precisa responder</span>\n'
        f'    <div class="nmb2-pergunta-icon">{SVG_INTERROGACAO}</div>\n'
        f'    <p class="nmb2-pergunta">&ldquo;{pergunta}&rdquo;</p>\n'
        f'    {f"<p class=\"nmb2-pergunta-ctx\">{ctx_perg}</p>" if ctx_perg else ""}\n'
        f'  </div>\n'
        f'</div>\n'
    )

    # Cards
    cards_html = "".join(
        _render_card(frente, (impacto_xtech.get(frente) or {}).get("urgencia", "monitorar"), cards.get(frente, FALLBACK_CARDS.get(frente, {})))
        for frente in FRENTES_ATIVAS
    )
    cards_section = (
        f'<div class="nmb2-cards-section">\n'
        f'  <p class="nmb2-sec-title">Como a nMentors atua neste ciclo</p>\n'
        f'  <div class="nmb2-cards">\n{cards_html}  </div>\n'
        f'</div>\n'
    )

    # CTA final
    cta_final = (
        f'<div class="nmb2-cta-final">\n'
        f'  <p class="nmb2-cta-text">Quer entender como este ciclo impacta seu projeto?</p>\n'
        f'  <div class="nmb2-btns">\n'
        f'    <a class="nmb2-btn" href="https://nmentors.com.br/contato/" target="_blank" rel="noopener"\n'
        f'       style="background:#38D6B4;color:#0B1418 !important;">Agendar sess&atilde;o diagn&oacute;stica &rarr;</a>\n'
        f'    <a class="nmb2-btn" href="https://efagundes.com/radar-xtech/" target="_blank" rel="noopener"\n'
        f'       style="background:rgba(255,255,255,.07);color:#E8EEF4 !important;border:1px solid rgba(255,255,255,.12);">Ver o Radar completo &rarr;</a>\n'
        f'  </div>\n'
        f'</div>\n'
    )

    return (
        f"<!-- hero-nmentors-{_e(ciclo_raw)} | nMentors Engenharia -->\n"
        + _css()
        + f'\n<div class="nmb2">\n\n'
        + f'<!-- ── HEADER ── -->\n' + header
        + f'\n<!-- ── TESE + PERGUNTA ── -->\n' + tese
        + f'\n<!-- ── CARDS xTECH ── -->\n' + cards_section
        + f'\n<!-- ── CTA FINAL ── -->\n' + cta_final
        + f'\n</div>\n'
        + f'<!-- /hero-nmentors-{_e(ciclo_raw)} -->'
    )


# ── Função principal ──────────────────────────────────────────────────────────

def gerar_hero_nmentors(
    intel_path: str | Path,
    output_dir: str | Path,
    api_key: str | None = None,
    dry_run: bool = False,
) -> Path:
    """
    Lê intel_output.json, gera cards xTech via LLM (Chamada A),
    renderiza HTML e salva em output_dir/hero-nmentors-{ciclo_id}.html.
    Retorna o Path do arquivo gerado.
    """
    intel_path = Path(intel_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[nm-hero] Lendo intel_output.json...")
    with open(intel_path, encoding="utf-8") as f:
        data: dict = json.load(f)

    ciclo         = data.get("ciclo_id") or "sem-ciclo"
    impacto_xtech = data.get("impacto_xtech") or {}
    cards: dict[str, dict] = {}

    if dry_run:
        print("[nm-hero] Modo dry_run — usando fallbacks")
        cards = dict(FALLBACK_CARDS)
    else:
        client = _client(api_key)
        print("[nm-hero] Disparando chamadas LLM paralelas (max_workers=4)...")
        futures: dict = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for frente in FRENTES_ATIVAS:
                dados = impacto_xtech.get(frente) or {}
                fut = pool.submit(_gerar_card_xtech, client, frente, dados)
                futures[fut] = frente
            for fut in as_completed(futures):
                frente = futures[fut]
                cards[frente] = fut.result()
                print(f"[nm-hero] ✓ card {frente}")

    html = _render_html(data, cards)
    output_path = output_dir / f"hero-nmentors-{ciclo}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"[nm-hero] ✓ Salvo: {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    p = argparse.ArgumentParser()
    p.add_argument("--intel", default="intel_output.json")
    p.add_argument("--output-dir", default="../outputs/radar")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    gerar_hero_nmentors(
        intel_path=args.intel,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )
