# NKE Lite — Relatório de Implementação

**Data:** 2026-06-28  
**Versão:** NKE Lite 0.1  
**Status:** Implementado — aguardando validação com dados reais

---

## Arquivos alterados

| Arquivo | Tipo de alteração |
|---|---|
| `knowledge_core/reasoning_model.yaml` | **Criado** — modelo de raciocínio NKE Lite |
| `prod/.env` | **Adicionado** — `NKE_LITE_ENABLED=false` (padrão desativado) |
| `prod/analyzer_v33_agent.py` | **Modificado** — constantes, função `gerar_nke_lite`, chamada no orquestrador |

**Arquivos NÃO alterados (confirmado):**
- `analyzer_v30.py` — intocado
- `run_pipeline_v2.py` — intocado
- `gerar_radar_xtechs_v11.py` — intocado
- `ingest_to_sqlite.py` — intocado
- schema SQLite — intocado
- templates HTML — intocados

---

## Prompts alterados

Nenhum prompt existente foi modificado.

O NKE Lite opera como **bloco 6.5.15**, executado ao final da Fase 6.5, após a auditoria de enriquecimento (6.5.12). Usa chamada independente ao Sonnet com `_NKE_SYSTEM` exclusivo.

A instrução NKE Lite (`_NKE_LITE_INSTRUCTION`) está definida como constante mas **não é injetada** nos prompts das fases 6.5.1–6.5.14 nem na 6.6. Fica disponível para injeção futura controlada.

---

## Namespace de saída

Quando `NKE_LITE_ENABLED=true`, o seguinte bloco é adicionado ao `intel_output.json`:

```json
{
  "nke_lite": {
    "strategic_theme": "string",
    "inferred_pattern": "string",
    "business_impact": "string",
    "decision_window": "string",
    "decision_pressure": "baixa | média | alta | crítica",
    "confidence_score": 0.0,
    "gerado_em": "ISO timestamp",
    "enabled_by": "NKE_LITE_ENABLED=true"
  }
}
```

Quando `NKE_LITE_ENABLED=false`, o bloco não é gerado. O `intel_output.json` permanece idêntico ao modo atual.

---

## Fallback

Dois níveis de proteção implementados:

1. **Fallback interno em `gerar_nke_lite`**: qualquer exceção retorna `{...NKE_EMPTY, "fallback": True, "erro": "..."}` sem propagar.
2. **Fallback externo no orquestrador (6.5.15)**: `try/except` adicional captura qualquer falha inesperada fora da função. O pipeline continua normalmente em ambos os casos.

Se o NKE Lite falhar, o log exibe aviso e o pipeline segue para a Fase 6.6 sem interrupção.

---

## Resultado dos testes

> ⚠️ Os testes A e B abaixo são **pendentes de execução com dados reais**.  
> A validação sintática passou: `ast.parse()` sem erros.

### Teste A — `NKE_LITE_ENABLED=false` (modo atual)

```bash
NKE_LITE_ENABLED=false python run_pipeline_v2.py
```

Comportamento esperado:
- `intel_output.json` gerado normalmente — sem campo `nke_lite`
- HTML gerado normalmente — sem alterações estruturais
- SQLite atualizado normalmente
- Log exibe: `· [6.5.15] NKE Lite desativado (NKE_LITE_ENABLED=false) — pulando.`

### Teste B — `NKE_LITE_ENABLED=true` (modo NKE Lite)

```bash
NKE_LITE_ENABLED=true python run_pipeline_v2.py
```

Comportamento esperado:
- `intel_output.json` gerado com campo `nke_lite` preenchido
- HTML gerado normalmente — template não lê `nke_lite`, sem impacto visual
- SQLite atualizado normalmente — `ingest_to_sqlite.py` ignora campos desconhecidos
- Log exibe: `[OK] NKE Lite — tema: '...' | pressão: ... | confiança: ...`
- Se falhar: log exibe aviso e pipeline continua

---

## Diferenças esperadas no `intel_output.json`

**Modo atual (`NKE_LITE_ENABLED=false`):** sem diferença — arquivo idêntico ao ciclo anterior.

**Modo NKE Lite (`NKE_LITE_ENABLED=true`):** um bloco adicional no nível raiz:
```json
"nke_lite": {
  "strategic_theme": "...",
  "inferred_pattern": "...",
  "business_impact": "...",
  "decision_window": "...",
  "decision_pressure": "alta",
  "confidence_score": 0.7,
  "gerado_em": "2026-06-28T...",
  "enabled_by": "NKE_LITE_ENABLED=true"
}
```

Nenhum campo existente é alterado ou removido.

---

## Riscos remanescentes

| Risco | Probabilidade | Mitigação |
|---|---|---|
| `ingest_to_sqlite.py` rejeitar campo `nke_lite` | Baixa — SQLite ignora campos não mapeados no schema | Protegido por fallback; campo não altera schema |
| Custo de tokens aumentado | Baixo — 1 chamada adicional Sonnet (~512 tokens) por ciclo | Monitorar custo no primeiro ciclo com flag ativo |
| LLM retornar JSON malformado | Baixa — `_reparar_json` + `try/except` cobrem este caso | Fallback vazio explícito |
| Injeção futura nos prompts 6.5.1–6.5.14 sem avaliação | Médio (risco futuro) | Requer nova aprovação — `_NKE_LITE_INSTRUCTION` disponível mas não conectada |

---

## Confirmação de fallback

O fallback foi implementado em dois níveis independentes e é explícito no código.  
Se qualquer etapa do NKE Lite falhar, o pipeline roda no modo atual sem interrupção.  
Confirmação definitiva requer execução com `NKE_LITE_ENABLED=true` e dados reais.

---

## Recomendação

**Não ativar `NKE_LITE_ENABLED=true` como padrão neste ciclo.**

Recomendação para o próximo passo:
1. Executar **Teste A** (`NKE_LITE_ENABLED=false`) e confirmar que o ciclo atual não foi impactado.
2. Executar **Teste B** (`NKE_LITE_ENABLED=true`) em um ciclo com dados reais e inspecionar o campo `nke_lite` gerado.
3. Se `intel_output.json` e HTML gerados corretamente, e campo `nke_lite` com conteúdo analítico relevante → ativar como padrão no ciclo seguinte.

A mudança está pronta para o próximo ciclo oficial assim que os testes passarem.
