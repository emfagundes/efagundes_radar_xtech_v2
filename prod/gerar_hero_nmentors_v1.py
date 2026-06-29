"""
gerar_hero_nmentors.py — Hero dinâmico do ciclo Radar xTech para nMentors.com.br
Spec: SPEC-hero-nmentors.md v1.0
"""
from __future__ import annotations

import json
import os
import html as _html
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import anthropic

# ── Constantes ────────────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 600
LLM_TIMEOUT = 30  # segundos (via timeout no client)

FRENTES_ATIVAS = ["EnergyTech", "CleanTech", "DeepTech", "AgriTech"]  # FinTech excluída

CORES_FRENTE: dict[str, str] = {
    "EnergyTech": "#2FA87C",
    "CleanTech":  "#38D6B4",
    "DeepTech":   "#4F83F0",
    "AgriTech":   "#7AB648",
}

URGENCIA_BADGE: dict[str, dict[str, str]] = {
    "imediata":    {"label": "Agir agora",       "cor": "#D96C6C"},
    "médio_prazo": {"label": "Próximos meses",   "cor": "#CC9A3A"},
    "medio_prazo": {"label": "Próximos meses",   "cor": "#CC9A3A"},
    "monitorar":   {"label": "Monitorar",        "cor": "#566880"},
}

FALLBACK_SERVICOS: dict[str, list[str]] = {
    "EnergyTech": ["PMO de projetos de energia", "Engenharia BESS + integração", "P&D regulado ANEEL"],
    "CleanTech":  ["Consultoria em descarbonização", "PMO de projetos CleanTech", "Capacitação técnica"],
    "DeepTech":   ["IA aplicada a sistemas de energia", "Automação e SCADA", "Modelagem preditiva"],
    "AgriTech":   ["PMO agro-energia", "Rastreabilidade com IoT", "Capacitação técnica"],
}

TIPO_SERVICO_LABELS: dict[str, str] = {
    "pmo_agetico":      "PMO Agêntico",
    "ciberseguranca_ot":"Engenharia+IA",
    "ia_iot_pred":      "Engenharia+IA",
    "p_d_aneel":        "P&D ANEEL",
    "capacitacao":      "Capacitação",
}

SYSTEM_PROMPT = """Você gera conteúdo comercial conciso para a nMentors Engenharia, \
consultoria AI-first especializada em energia e infraestrutura.
Capacidades da nMentors:
- PMO Agêntico: gestão de projetos com agentes de IA integrados a ERP/CRM
- P&D regulado ANEEL: propostas, execução e homologação com universidades
- Engenharia + IA Aplicada: automação, SCADA, BESS, modelagem preditiva, IoT
- Capacitação técnica: programas sob demanda do campo ao C-level
Clientes-alvo: PMEs de energia, datacenters, EPCs, comercializadoras de energia.
REGRAS:
- Tom executivo, direto, sem jargão técnico excessivo
- NÃO mencione FinTech como área de atuação
- NÃO faça promessas de prazo, custo ou resultado específico
- NÃO use "AI" — use "IA" ou "inteligência artificial"
- Máximo 2 frases em como_atuamos
- Responda APENAS com JSON válido, sem markdown, sem explicações"""


def _h(text: Any) -> str:
    """HTML-escape."""
    return _html.escape(str(text or ""), quote=True)


def _get_client(api_key: str | None = None) -> anthropic.Anthropic:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=key, timeout=LLM_TIMEOUT)


# ── Chamada A — Cards por frente xTech ───────────────────────────────────────

def _llm_card_frente(client: anthropic.Anthropic, frente: str, impacto: dict) -> dict:
    user = f"""O Radar xTech identificou este impacto para {frente}:
Sinal de referência: {impacto.get('sinal_referencia', '')}
Impacto analisado: {impacto.get('impacto', '')}
Urgência: {impacto.get('urgencia', '')}
Direção: {impacto.get('direcao', '')}

Gere um JSON com exatamente estas chaves:
{{
  "como_atuamos": "2 frases sobre como a nMentors ajuda neste contexto",
  "servicos": ["serviço 1", "serviço 2", "serviço 3"],
  "cta": "1 frase de chamada à ação (máx 12 palavras)"
}}
Os serviços devem ser específicos e acionáveis."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[nm-hero] ⚠ LLM card_frente {frente} falhou: {e} — usando fallback")
        return {
            "como_atuamos": str(impacto.get("impacto", ""))[:200],
            "servicos": FALLBACK_SERVICOS.get(frente, ["Consultoria técnica", "PMO", "Capacitação"]),
            "cta": "Converse com nosso time sobre este cenário →",
        }


# ── Chamada B — Cards de oportunidade comercial ───────────────────────────────

def _llm_card_oportunidade(client: anthropic.Anthropic, topic: dict) -> dict:
    caps = ", ".join(topic.get("matched_capabilities") or [])
    cases = ", ".join(topic.get("recommended_cases") or [])
    user = f"""O Radar xTech identificou esta oportunidade comercial prioritária para a nMentors:
Vetor: {topic.get('vector_name', '')}
Pressão estratégica: {topic.get('pressao_estrategica', 0)}/10
Janela decisória: {topic.get('janela_decisoria_categoria', '')}
Decisão recomendada pelo Radar: {topic.get('vector_decisao_recomendada', '')}
Capacidades nMentors mapeadas: {caps}
Cases recomendados: {cases}
Formato: {topic.get('content_format', '')}

Gere um JSON com exatamente estas chaves:
{{
  "titulo": "título executivo do projeto (máx 8 palavras)",
  "descricao": "2 frases sobre o que a nMentors entrega neste contexto",
  "tipo_servico": "PMO Agêntico" | "P&D ANEEL" | "Engenharia+IA" | "Capacitação",
  "case_referencia": "nome do case mais relevante (1 string)"
}}"""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[nm-hero] ⚠ LLM card_oportunidade {topic.get('vector_name')} falhou: {e} — usando fallback")
        caps_list = topic.get("matched_capabilities") or []
        tipo = TIPO_SERVICO_LABELS.get(caps_list[0], "Engenharia+IA") if caps_list else "Engenharia+IA"
        return {
            "titulo": topic.get("vector_name", "Oportunidade estratégica")[:60],
            "descricao": str(topic.get("vector_decisao_recomendada", ""))[:200],
            "tipo_servico": tipo,
            "case_referencia": (topic.get("recommended_cases") or ["—"])[0],
        }


# ── HTML ──────────────────────────────────────────────────────────────────────

def _css() -> str:
    return """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;600;700&display=swap');

.nm-hp-wrap * { box-sizing: border-box; }
.nm-hp-wrap {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0B1418;
  border-radius: 16px;
  padding: clamp(20px, 4vw, 36px);
  color: #E8EEF4;
  line-height: 1.6;
  max-width: 1100px;
  margin: 0 auto;
}
.nm-hp-eyebrow {
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 11px;
  letter-spacing: .13em;
  text-transform: uppercase;
  color: #96A8BC;
  margin: 0;
}
.nm-hp-badge {
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 10px;
  font-weight: 700;
  border-radius: 4px;
  padding: 3px 8px;
  display: inline-block;
}
.nm-hp-header {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 22px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(255,255,255,0.07);
}
.nm-hp-tese-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
  margin-bottom: 28px;
}
.nm-hp-manchete {
  font-size: clamp(22px, 3vw, 34px);
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.1;
  color: #E8EEF4;
  margin: 6px 0 10px;
}
.nm-hp-insight {
  font-size: clamp(13px, 1.6vw, 15px);
  color: #96A8BC;
  line-height: 1.65;
  margin: 0;
}
.nm-hp-pergunta-card {
  background: #0C1B24;
  border: 1px solid rgba(204,154,58,0.35);
  border-left: 3px solid #CC9A3A;
  border-radius: 12px;
  padding: 18px 20px;
}
.nm-hp-pergunta {
  font-size: clamp(14px, 2vw, 18px);
  font-weight: 600;
  color: #E8EEF4;
  font-style: italic;
  line-height: 1.5;
  margin: 8px 0 0;
}
.nm-hp-section-title {
  font-size: 13px;
  font-weight: 700;
  color: #E8EEF4;
  text-transform: uppercase;
  letter-spacing: .08em;
  margin: 0 0 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.nm-hp-section-title::before {
  content: '';
  display: inline-block;
  width: 18px;
  height: 1px;
  background: #38D6B4;
}
.nm-hp-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}
.nm-hp-card {
  background: #0C1B24;
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 12px;
  padding: 16px;
}
.nm-hp-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}
.nm-hp-frente-label {
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .06em;
}
.nm-hp-como {
  font-size: 13px;
  color: #96A8BC;
  line-height: 1.6;
  margin: 0 0 10px;
}
.nm-hp-servicos {
  list-style: none;
  padding: 0;
  margin: 0 0 12px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.nm-hp-servico-item {
  font-size: 12px;
  color: #E8EEF4;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.nm-hp-servico-item::before {
  content: '→';
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  flex-shrink: 0;
  margin-top: 1px;
}
.nm-hp-cta-link {
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
  display: inline-block;
  margin-top: 4px;
}
.nm-hp-oport-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
  margin-bottom: 28px;
}
.nm-hp-oport-card {
  background: #0C1B24;
  border: 1px solid rgba(79,131,240,0.25);
  border-radius: 12px;
  padding: 18px;
}
.nm-hp-oport-tipo {
  font-family: 'IBM Plex Mono', 'Courier New', monospace;
  font-size: 10px;
  font-weight: 700;
  color: #4F83F0;
  text-transform: uppercase;
  letter-spacing: .1em;
  margin: 0 0 8px;
}
.nm-hp-oport-titulo {
  font-size: 16px;
  font-weight: 700;
  color: #E8EEF4;
  letter-spacing: -0.02em;
  line-height: 1.3;
  margin: 0 0 10px;
}
.nm-hp-oport-desc {
  font-size: 13px;
  color: #96A8BC;
  line-height: 1.6;
  margin: 0 0 12px;
}
.nm-hp-oport-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.nm-hp-oport-meta-item {
  font-size: 11px;
  color: #566880;
  display: flex;
  gap: 4px;
  align-items: center;
}
.nm-hp-oport-meta-label {
  color: #96A8BC;
  font-weight: 600;
}
.nm-hp-cta-final {
  background: #0C1B24;
  border: 1px solid rgba(56,214,180,0.2);
  border-radius: 12px;
  padding: 22px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}
.nm-hp-cta-final-text {
  font-size: 16px;
  font-weight: 600;
  color: #E8EEF4;
  max-width: 500px;
}
.nm-hp-cta-buttons {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.nm-hp-btn {
  font-size: 14px;
  font-weight: 700;
  padding: 11px 22px;
  border-radius: 8px;
  text-decoration: none;
  display: inline-block;
  white-space: nowrap;
}
.nm-hp-sep {
  border: none;
  border-top: 1px solid rgba(255,255,255,0.07);
  margin: 22px 0;
}
@media (max-width: 680px) {
  .nm-hp-tese-grid { grid-template-columns: 1fr; }
  .nm-hp-cta-final { flex-direction: column; align-items: flex-start; }
  .nm-hp-cards-grid { grid-template-columns: 1fr; }
  .nm-hp-oport-grid { grid-template-columns: 1fr; }
}
</style>"""


def _render_card_frente(frente: str, impacto: dict, conteudo: dict) -> str:
    cor = CORES_FRENTE.get(frente, "#38D6B4")
    urgencia_key = (impacto.get("urgencia") or "monitorar").lower().replace(" ", "_").replace("-", "_")
    badge_info = URGENCIA_BADGE.get(urgencia_key, URGENCIA_BADGE["monitorar"])
    opacity = "0.6" if urgencia_key == "monitorar" else "1"

    servicos_html = "".join(
        f'<li class="nm-hp-servico-item" style="color:#E8EEF4;">{_h(s)}</li>'
        for s in (conteudo.get("servicos") or [])[:3]
    )
    cta = _h(conteudo.get("cta") or "Converse com nosso time →")

    return f"""<div class="nm-hp-card" data-frente="{_h(frente)}" style="border-top:3px solid {cor};opacity:{opacity};">
  <div class="nm-hp-card-header">
    <span class="nm-hp-frente-label" style="color:{cor};">{_h(frente)}</span>
    <span class="nm-hp-badge" style="background:{badge_info['cor']}22;color:{badge_info['cor']};">{_h(badge_info['label'])}</span>
  </div>
  <p class="nm-hp-como">{_h(conteudo.get('como_atuamos') or '')}</p>
  <ul class="nm-hp-servicos">{servicos_html}</ul>
  <a class="nm-hp-cta-link" style="color:{cor};" href="https://nmentors.com.br/contato" target="_blank" rel="noopener">{cta}</a>
</div>"""


def _render_card_oportunidade(topic: dict, conteudo: dict) -> str:
    janela = _h(topic.get("janela_decisoria_categoria") or "—")
    case = _h(conteudo.get("case_referencia") or "—")
    tipo = _h(conteudo.get("tipo_servico") or "Engenharia+IA")
    pressao = float(topic.get("pressao_estrategica") or 0)

    return f"""<div class="nm-hp-oport-card">
  <p class="nm-hp-oport-tipo">{tipo}</p>
  <h3 class="nm-hp-oport-titulo">{_h(conteudo.get('titulo') or topic.get('vector_name') or '')}</h3>
  <p class="nm-hp-oport-desc">{_h(conteudo.get('descricao') or '')}</p>
  <div class="nm-hp-oport-meta">
    <span class="nm-hp-oport-meta-item"><span class="nm-hp-oport-meta-label">Case:</span> {case}</span>
    <span class="nm-hp-oport-meta-item"><span class="nm-hp-oport-meta-label">Janela:</span> {janela}</span>
    <span class="nm-hp-oport-meta-item"><span class="nm-hp-oport-meta-label">Pressão:</span> {pressao:.1f}/10</span>
  </div>
</div>"""


def _render_html(data: dict, cards_frente: dict[str, dict], cards_oport: list[dict]) -> str:
    ciclo = data.get("ciclo_id") or "—"
    dashboard = data.get("dashboard") or {}
    total_sinais = dashboard.get("total_sinais") or 0
    score_ips = float(dashboard.get("score_ips_medio") or 0)

    sb = data.get("strategic_briefing") or {}
    pergunta = _h(sb.get("pergunta_estrategica") or "")
    contexto_pergunta = _h(sb.get("contexto_pergunta") or "")
    insight = _h(sb.get("insight_executivo") or "")

    hero = data.get("hero") or {}
    manchete = _h(hero.get("manchete") or "")

    # Cabeçalho
    # Formatar ciclo_id para exibição: 2026-06-24 → 24-06-2026
    try:
        partes = ciclo.split("-")
        ciclo_display = f"{partes[2]}-{partes[1]}-{partes[0]}"
    except Exception:
        ciclo_display = ciclo

    header = f"""<div class="nm-hp-header">
  <div>
    <p class="nm-hp-eyebrow">nMentors Engenharia · Briefing do Mentor</p>
    <p style="font-size:15px;font-weight:700;color:#E8EEF4;margin:4px 0 0;">Ciclo {_h(ciclo_display)}</p>
  </div>
  <span class="nm-hp-badge" style="background:rgba(56,214,180,0.1);color:#38D6B4;margin-left:auto;">{total_sinais} sinais</span>
  <span class="nm-hp-badge" style="background:rgba(79,131,240,0.1);color:#4F83F0;">IPS {score_ips:.1f}</span>
</div>"""

    # Tese + Pergunta estratégica
    tese_grid = f"""<div class="nm-hp-tese-grid">
  <div>
    <p class="nm-hp-eyebrow" style="margin-bottom:6px;">Tese do ciclo</p>
    <h2 class="nm-hp-manchete">{manchete}</h2>
    <p class="nm-hp-insight">{insight}</p>
  </div>
  <div class="nm-hp-pergunta-card">
    <p class="nm-hp-eyebrow" style="color:#CC9A3A;margin-bottom:6px;">Pergunta estratégica desta semana</p>
    <p class="nm-hp-pergunta">&#8220;{pergunta}&#8221;</p>
    {(f'<p style="margin:8px 0 0;font-size:12px;color:#96A8BC;">{contexto_pergunta}</p>') if contexto_pergunta else ''}
  </div>
</div>"""

    # Cards de frente
    impacto_xtech = data.get("impacto_xtech") or {}
    cards_frente_html = ""
    for frente in FRENTES_ATIVAS:
        impacto = impacto_xtech.get(frente) or {}
        conteudo = cards_frente.get(frente) or {
            "como_atuamos": str(impacto.get("impacto") or "")[:200],
            "servicos": FALLBACK_SERVICOS.get(frente, []),
            "cta": "Converse com nosso time →",
        }
        cards_frente_html += _render_card_frente(frente, impacto, conteudo)

    frentes_section = f"""<hr class="nm-hp-sep">
<p class="nm-hp-section-title">Como a nMentors atua neste ciclo</p>
<div class="nm-hp-cards-grid">{cards_frente_html}</div>"""

    # Cards de oportunidade
    oport_html = ""
    commercial = data.get("commercial_nmentors_opportunities") or {}
    topics = commercial.get("selected_topics") or []
    if topics and cards_oport:
        oport_items = ""
        for topic, conteudo in zip(topics[:2], cards_oport[:2]):
            oport_items += _render_card_oportunidade(topic, conteudo)
        oport_html = f"""<hr class="nm-hp-sep">
<p class="nm-hp-section-title">Oportunidades identificadas neste ciclo</p>
<div class="nm-hp-oport-grid">{oport_items}</div>"""

    # CTA final
    cta_final = """<hr class="nm-hp-sep">
<div class="nm-hp-cta-final">
  <p class="nm-hp-cta-final-text">Quer entender como este ciclo impacta seu projeto?</p>
  <div class="nm-hp-cta-buttons">
    <a class="nm-hp-btn" href="https://nmentors.com.br/contato" target="_blank" rel="noopener"
       style="background:#38D6B4;color:#0B1418;">Agendar sessão diagnóstica →</a>
    <a class="nm-hp-btn" href="https://efagundes.com/radar-xtechs" target="_blank" rel="noopener"
       style="background:rgba(255,255,255,0.07);color:#E8EEF4;border:1px solid rgba(255,255,255,0.12);">Ver o Radar completo →</a>
  </div>
</div>"""

    return (
        f"<!-- hero-nmentors-{_h(ciclo)} | nMentors Engenharia -->\n"
        + _css()
        + f'\n<div class="nm-hp-wrap">\n'
        + header
        + tese_grid
        + frentes_section
        + oport_html
        + cta_final
        + "\n</div>"
        + f"\n<!-- /hero-nmentors-{_h(ciclo)} -->"
    )


# ── Função principal ──────────────────────────────────────────────────────────

def gerar_hero_nmentors(
    intel_path: str | Path,
    output_dir: str | Path,
    api_key: str | None = None,
    dry_run: bool = False,
) -> Path:
    """
    Lê intel_output.json, chama o LLM para gerar conteúdo dos cards,
    renderiza o HTML e salva em output_dir/hero-nmentors-{ciclo_id}.html.
    Retorna o Path do arquivo gerado.
    """
    intel_path = Path(intel_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[nm-hero] Lendo intel_output.json...")
    with open(intel_path, encoding="utf-8") as f:
        data: dict = json.load(f)

    ciclo = data.get("ciclo_id") or "sem-ciclo"
    impacto_xtech = data.get("impacto_xtech") or {}
    commercial = data.get("commercial_nmentors_opportunities") or {}
    topics = commercial.get("selected_topics") or []

    # Frentes elegíveis para Chamada A (urgencia != monitorar, excluir FinTech)
    frentes_a = [
        f for f in FRENTES_ATIVAS
        if (impacto_xtech.get(f) or {}).get("urgencia", "monitorar").lower() != "monitorar"
    ]
    # Se todas são monitorar, incluir todas para não deixar cards vazios
    if not frentes_a:
        frentes_a = FRENTES_ATIVAS

    cards_frente: dict[str, dict] = {}
    cards_oport: list[dict] = []

    if dry_run:
        print("[nm-hero] Modo dry_run — usando fallbacks para todos os cards")
        for frente in FRENTES_ATIVAS:
            impacto = impacto_xtech.get(frente) or {}
            cards_frente[frente] = {
                "como_atuamos": str(impacto.get("impacto") or "")[:200],
                "servicos": FALLBACK_SERVICOS.get(frente, []),
                "cta": "Converse com nosso time →",
            }
        for topic in topics[:2]:
            caps = topic.get("matched_capabilities") or []
            tipo = TIPO_SERVICO_LABELS.get(caps[0], "Engenharia+IA") if caps else "Engenharia+IA"
            cards_oport.append({
                "titulo": topic.get("vector_name", "")[:60],
                "descricao": str(topic.get("vector_decisao_recomendada") or "")[:200],
                "tipo_servico": tipo,
                "case_referencia": (topic.get("recommended_cases") or ["—"])[0],
            })
    else:
        client = _get_client(api_key)
        futures: dict = {}

        print(f"[nm-hero] Disparando chamadas LLM (max_workers=4)...")
        with ThreadPoolExecutor(max_workers=4) as pool:
            # Chamada A
            for frente in frentes_a:
                impacto = impacto_xtech.get(frente) or {}
                fut = pool.submit(_llm_card_frente, client, frente, impacto)
                futures[fut] = ("frente", frente)

            # Chamada B
            for i, topic in enumerate(topics[:2]):
                fut = pool.submit(_llm_card_oportunidade, client, topic)
                futures[fut] = ("oport", i)

            for fut in as_completed(futures):
                kind, key = futures[fut]
                result = fut.result()  # já tem fallback interno, nunca levanta
                if kind == "frente":
                    cards_frente[key] = result
                    print(f"[nm-hero] ✓ card frente {key}")
                else:
                    # Garante ordem
                    while len(cards_oport) <= key:
                        cards_oport.append({})
                    cards_oport[key] = result
                    print(f"[nm-hero] ✓ card oportunidade {key}")

        # Frentes que não tiveram chamada A (urgencia==monitorar) → fallback
        for frente in FRENTES_ATIVAS:
            if frente not in cards_frente:
                impacto = impacto_xtech.get(frente) or {}
                cards_frente[frente] = {
                    "como_atuamos": str(impacto.get("impacto") or "")[:200],
                    "servicos": FALLBACK_SERVICOS.get(frente, []),
                    "cta": "Converse com nosso time →",
                }

    html = _render_html(data, cards_frente, cards_oport)

    output_path = output_dir / f"hero-nmentors-{ciclo}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"[nm-hero] ✓ Salvo: {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")
    return output_path


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

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
