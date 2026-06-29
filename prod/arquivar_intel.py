"""
arquivar_intel.py — Efagundes Intelligence Engine
Sistema de arquivamento histórico do intel_output.json.

Estrutura gerada:
  arquivo/
    2026/
      04/
        2026-04-04_10-45.json       ← cópia com timestamp
    indices/
      semanal_2026-W14.json         ← índice da semana 14
      mensal_2026-04.json           ← índice de abril
      trimestral_2026-Q2.json       ← índice do 2º trimestre
      semestral_2026-S1.json        ← índice do 1º semestre
      anual_2026.json               ← índice do ano
    resumos/
      semanal_2026-W14.json         ← resumo executivo semanal
      mensal_2026-04.json           ← resumo executivo mensal

Uso:
  python arquivar_intel.py                    ← arquiva intel_output.json atual
  python arquivar_intel.py --resumo semanal   ← gera resumo executivo da semana
  python arquivar_intel.py --resumo mensal    ← gera resumo executivo do mês
"""

import json, os, shutil, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

INPUT_FILE  = "intel_output.json"
ARQUIVO_DIR = Path("arquivo")
BRASILIA    = timezone(timedelta(hours=-3))

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def agora_br():
    return datetime.now(BRASILIA)

def semana_iso(dt):
    return f"{dt.year}-W{dt.isocalendar()[1]:02d}"

def trimestre(dt):
    return f"{dt.year}-Q{(dt.month-1)//3+1}"

def semestre(dt):
    return f"{dt.year}-S{1 if dt.month <= 6 else 2}"

def caminho_diario(dt):
    return ARQUIVO_DIR / str(dt.year) / f"{dt.month:02d}"

def nome_arquivo(dt):
    return f"{dt.strftime('%Y-%m-%d_%H-%M')}.json"


# ─────────────────────────────────────────────
# ARQUIVAMENTO
# ─────────────────────────────────────────────

def arquivar():
    if not os.path.exists(INPUT_FILE):
        print(f"✗ {INPUT_FILE} não encontrado.")
        return None

    with open(INPUT_FILE, encoding="utf-8") as f:
        data = json.load(f)

    dt = agora_br()

    # Cria pasta do dia
    pasta = caminho_diario(dt)
    pasta.mkdir(parents=True, exist_ok=True)

    # Salva cópia com timestamp
    destino = pasta / nome_arquivo(dt)
    shutil.copy(INPUT_FILE, destino)

    print(f"✓ Arquivado: {destino}")

    # Atualiza índices
    _atualizar_indices(data, dt, str(destino))

    return str(destino)


def _atualizar_indices(data, dt, caminho_arquivo):
    """Atualiza todos os índices de período com referência ao novo arquivo."""
    indices_dir = ARQUIVO_DIR / "indices"
    indices_dir.mkdir(parents=True, exist_ok=True)

    periodos = {
        f"semanal_{semana_iso(dt)}":      "semanal",
        f"mensal_{dt.strftime('%Y-%m')}": "mensal",
        f"trimestral_{trimestre(dt)}":    "trimestral",
        f"semestral_{semestre(dt)}":      "semestral",
        f"anual_{dt.year}":               "anual",
    }

    # Extrai resumo executivo do arquivo atual
    sintese    = data.get("sintese", {})
    top5       = sintese.get("top5_movimentos", [])
    frase      = sintese.get("frase_do_dia", "")
    total      = data.get("total_itens", 0)
    gerado_em  = data.get("gerado_em", "")

    entrada = {
        "arquivo":    caminho_arquivo,
        "gerado_em":  gerado_em,
        "total_itens": total,
        "frase_do_dia": frase,
        "top5": [
            {
                "titulo":   m.get("titulo", ""),
                "urgencia": m.get("urgencia", ""),
                "tipo":     m.get("tipo", ""),
                "impacto":  m.get("impacto", ""),
            }
            for m in top5[:5]
        ],
        "temas": list({
            item.get("tema_original", "")
            for item in data.get("itens", [])
            if item.get("analise", {}).get("score_final", 0) >= 7
        }),
    }

    for nome_idx, periodo in periodos.items():
        idx_path = indices_dir / f"{nome_idx}.json"

        if idx_path.exists():
            with open(idx_path, encoding="utf-8") as f:
                idx = json.load(f)
        else:
            idx = {
                "periodo":  periodo,
                "chave":    nome_idx,
                "criado_em": dt.isoformat(),
                "entradas": []
            }

        # Evita duplicata pelo mesmo arquivo
        caminhos = [e["arquivo"] for e in idx["entradas"]]
        if caminho_arquivo not in caminhos:
            idx["entradas"].append(entrada)
            idx["atualizado_em"] = dt.isoformat()
            idx["total_execucoes"] = len(idx["entradas"])

            with open(idx_path, "w", encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False, indent=2)

            print(f"  → Índice atualizado: {idx_path.name} ({len(idx['entradas'])} entradas)")


# ─────────────────────────────────────────────
# GERADOR DE RESUMO EXECUTIVO DE PERÍODO
# ─────────────────────────────────────────────

def gerar_resumo(periodo="semanal"):
    """
    Lê o índice do período e consolida os dados em um resumo executivo
    pronto para alimentar um RAG ou gerar um briefing periódico.
    """
    try:
        from anthropic import Anthropic
        client = Anthropic()
    except Exception as e:
        print(f"✗ Erro ao inicializar Anthropic: {e}")
        return

    dt = agora_br()

    chaves = {
        "semanal":    f"semanal_{semana_iso(dt)}",
        "mensal":     f"mensal_{dt.strftime('%Y-%m')}",
        "trimestral": f"trimestral_{trimestre(dt)}",
        "semestral":  f"semestral_{semestre(dt)}",
        "anual":      f"anual_{dt.year}",
    }

    chave = chaves.get(periodo)
    if not chave:
        print(f"✗ Período inválido: {periodo}. Use: semanal, mensal, trimestral, semestral, anual")
        return

    idx_path = ARQUIVO_DIR / "indices" / f"{chave}.json"
    if not idx_path.exists():
        print(f"✗ Índice não encontrado: {idx_path}")
        print(f"  Execute 'python arquivar_intel.py' pelo menos uma vez antes de gerar resumos.")
        return

    with open(idx_path, encoding="utf-8") as f:
        idx = json.load(f)

    entradas = idx.get("entradas", [])
    if not entradas:
        print("✗ Nenhuma entrada no índice.")
        return

    print(f"\n  Gerando resumo {periodo} com {len(entradas)} execuções do pipeline...\n")

    # Consolida dados para o prompt
    consolidado = {
        "periodo":    periodo,
        "chave":      chave,
        "execucoes":  len(entradas),
        "frases_do_dia": [e["frase_do_dia"] for e in entradas if e.get("frase_do_dia")],
        "todos_top5": [
            {"data": e["gerado_em"][:10], "movimento": m}
            for e in entradas
            for m in e.get("top5", [])
        ],
        "temas_recorrentes": _contar_temas(entradas),
    }

    prompt = f"""Você é um analista estratégico sênior. Responda em português do Brasil.

Com base nas análises de mercado do período {chave}, produza um RESUMO EXECUTIVO {periodo.upper()} seguindo o padrão McKinsey:

Dados do período:
- Execuções analisadas: {consolidado['execucoes']}
- Movimentos identificados: {len(consolidado['todos_top5'])}
- Temas mais recorrentes: {json.dumps(consolidado['temas_recorrentes'], ensure_ascii=False)}

Top movimentos do período:
{json.dumps(consolidado['todos_top5'][:30], ensure_ascii=False, indent=2)}

Frases de análise registradas:
{json.dumps(consolidado['frases_do_dia'][:10], ensure_ascii=False, indent=2)}

Produza APENAS um JSON válido:
{{
  "titulo": "Título executivo do período — padrão McKinsey com dado quantificado",
  "periodo": "{chave}",
  "gerado_em": "{dt.isoformat()}",
  "sumario_executivo": "3-4 parágrafos sobre os principais movimentos do período, tom FT/Economist",
  "principais_riscos": [
    {{"risco": "...", "magnitude": "...", "prazo": "...", "setor": "..."}}
  ],
  "principais_oportunidades": [
    {{"oportunidade": "...", "magnitude": "...", "janela": "...", "setor": "..."}}
  ],
  "temas_emergentes": ["tema1", "tema2", "tema3"],
  "acoes_recomendadas": [
    {{"acao": "...", "prioridade": "Alta|Média|Baixa", "responsavel": "Board|C-Level|Operacional"}}
  ],
  "indicadores_monitorar": ["indicador1", "indicador2"],
  "frase_executiva": "síntese do período em 1 frase — padrão editorial FT"
}}"""

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    texto = resp.content[0].text.strip()
    if "```" in texto:
        texto = texto.split("```")[1]
        if texto.startswith("json"):
            texto = texto[4:]
    texto = texto.strip()

    resumo = json.loads(texto)

    # Salva resumo
    resumos_dir = ARQUIVO_DIR / "resumos"
    resumos_dir.mkdir(parents=True, exist_ok=True)
    resumo_path = resumos_dir / f"{chave}.json"

    with open(resumo_path, "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2)

    print(f"✓ Resumo {periodo} gerado: {resumo_path}")
    print(f"\n  Título: {resumo.get('titulo','')}")
    print(f"\n  {resumo.get('sumario_executivo','')[:300]}...")
    print(f"\n  Frase executiva: \"{resumo.get('frase_executiva','')}\"")

    return resumo


def _contar_temas(entradas):
    contagem = {}
    for e in entradas:
        for tema in e.get("temas", []):
            contagem[tema] = contagem.get(tema, 0) + 1
    return dict(sorted(contagem.items(), key=lambda x: x[1], reverse=True))


# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────

def listar_arquivo():
    """Lista todos os arquivos armazenados com estatísticas."""
    if not ARQUIVO_DIR.exists():
        print("Nenhum arquivo ainda. Execute: python arquivar_intel.py")
        return

    print(f"\n{'─'*60}")
    print(f"  ARQUIVO INTELLIGENCE — {agora_br().strftime('%d/%m/%Y')}")
    print(f"{'─'*60}\n")

    total_arquivos = 0
    for ano_dir in sorted(ARQUIVO_DIR.glob("????/")):
        for mes_dir in sorted(ano_dir.glob("??/")):
            arquivos = sorted(mes_dir.glob("*.json"))
            if arquivos:
                mes_nome = mes_dir.name
                ano_nome = ano_dir.name
                print(f"  {ano_nome}/{mes_nome} — {len(arquivos)} execuções")
                for a in arquivos[-3:]:  # mostra últimas 3
                    print(f"    · {a.name}")
                if len(arquivos) > 3:
                    print(f"    ... e mais {len(arquivos)-3} arquivos")
                total_arquivos += len(arquivos)

    print(f"\n  Total: {total_arquivos} execuções arquivadas")

    # Índices disponíveis
    indices_dir = ARQUIVO_DIR / "indices"
    if indices_dir.exists():
        indices = list(indices_dir.glob("*.json"))
        print(f"\n  Índices: {len(indices)} períodos indexados")

    # Resumos disponíveis
    resumos_dir = ARQUIVO_DIR / "resumos"
    if resumos_dir.exists():
        resumos = list(resumos_dir.glob("*.json"))
        if resumos:
            print(f"  Resumos: {len(resumos)} resumos executivos gerados")
            for r in resumos:
                print(f"    · {r.name}")

    print()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--listar" in args:
        listar_arquivo()

    elif "--resumo" in args:
        idx = args.index("--resumo")
        periodo = args[idx+1] if idx+1 < len(args) else "semanal"
        # Arquiva primeiro se houver arquivo novo
        if os.path.exists(INPUT_FILE):
            arquivar()
        gerar_resumo(periodo)

    else:
        # Comportamento padrão: arquiva
        arquivar()
        print("\n  Comandos disponíveis:")
        print("  python arquivar_intel.py --listar")
        print("  python arquivar_intel.py --resumo semanal")
        print("  python arquivar_intel.py --resumo mensal")
        print("  python arquivar_intel.py --resumo trimestral")
        print("  python arquivar_intel.py --resumo anual\n")


if __name__ == "__main__":
    main()
