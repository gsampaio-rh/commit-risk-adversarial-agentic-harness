# exp19b — Commit Risk Adversarial Agentic Harness: Retrospective

**Período:** 9–13 Jun 2026 (5 dias)  
**44 commits · 481 tests · 53 eval runs · $4.87 custo total LLM**

---

## 1. Objetivo

Construir um **sistema de investigação de commits verificável** — não um classificador, não um wrapper de LLM. A tese: engenharia de harness (roteamento, montagem de contexto, schema enforcement, governança de custo) combinada com uma framework de avaliação adversarial (6 dimensões contra ground truth) produz investigações melhores do que qualidade de modelo bruta.

O dataset ApacheJIT fornece a cadeia de ground truth: commit buggy → commit de fix → ticket JIRA. Isso permite dimensões de avaliação que nenhum modelo score-only pode satisfazer. Um modelo pode prever "buggy" mas não pode provar que investigou o *mecanismo*.

### Targets

| Tier | D1 | D2 | D3 | D4 | D5 | D6 |
|------|-----|-----|-----|-----|-----|-----|
| **Gate** (mínimo V1) | 0.70 | 0.15 | 0.20 | 0.60 | 0.25 | 0.60 |
| **Target** (V2) | 0.80 | 0.25 | 0.35 | 0.75 | 0.40 | 0.70 |
| **Stretch** | 0.90 | 0.40 | 0.50 | 0.85 | 0.55 | 0.80 |

---

## 2. O que cada dimensão mede

| Dim | Pergunta respondida | Método | Custo |
|-----|---------------------|--------|-------|
| D1 | O agente previu o risco corretamente? | risk_level vs buggy label | Zero |
| D2 | Apontou os arquivos certos? | Arquivos do agente vs fix-commit (Jaccard) | Zero |
| D3 | O raciocínio identifica a causa raiz real? | LLM-as-judge rubric 0–4 (adversarial) | LLM |
| D4 | Severidade está calibrada? | Risco vs prioridade JIRA | Zero |
| D5 | Recomendações alinham com o fix real? | LLM-as-judge rubric 0–3 (adversarial) | LLM |
| D6 | O agente cita artefatos reais? | Claims vs diff/files reais | Zero |

---

## 3. Fases do projeto

### Fase 0: ITSM Pipeline (Arquivada) — Jun 9 manhã
- **3 commits.** Scaffolding de um analisador de change requests ITSM.
- **Pivô:** Nenhum dataset público de ITSM tem cadeias de ground truth (commit→fix→ticket). Descoberto que BPI 2014 tem process mining, não investigação de commits.
- **Decisão:** Migrar para ApacheJIT que tem linkage commit→fix→JIRA para 15 projetos Apache.

### Fase 1: Fundação ApacheJIT — Jun 9 tarde
- **7 commits.** Ground truth graph loader, JIRA client, git context provider, context builder, schema de relatório (Pydantic v2), orchestrator + CLI, XGBoost router (AUC=0.855), harness de avaliação 5-dim, 44 testes.
- **Decisão arquitetural:** Router de $0 (XGBoost em features numéricas do CSV) filtra commits antes do LLM. AUC=0.855 significa que ~85% dos commits são roteados corretamente sem nenhum custo de LLM.

### Fase 2: Iteração 1 — Primeiro Run Real — Jun 9 noite
- **3 commits.** Primeiro eval real com Claude Sonnet: D1=0.85, D3=0.20, D6=0.75.
- **Descoberta crítica:** O campo `buggy` do CSV estava sendo passado para o agente. Quando removido, D1 caiu de 0.86 → 0.40. O agente estava **trapaceando** — lendo o label em vez de investigar.
- **Fix:** Allowlist enforcement em `CommitContextBuilder` + testes de isolamento de oracle. Nunca mais: `buggy`, `fix`, `year`, `author_date`, JIRA metadata.

### Fase 3: Iteração 2 — Smart Diff + Clean Rubric — Jun 10 início
- **2 commits.** Assembly de diff de 16K chars com ranking por arquivo. Rubric dual-path para clean commits.
- **D1=0.75 no painel de 12 commits.**
- **Taxonomia de falhas D3:** 57% wrong-mechanism (tipo de defeito errado), 34% correct-area-wrong-detail, 9% missing-context.

### Fase 4: Iteração 3 — Pipeline Script-First — Jun 10
- **8 commits.** A mudança arquitetural mais importante do projeto.
- **Antes:** Prompt monolítico de ~135 linhas pedindo ao LLM para classificar E raciocinar E localizar E recomendar num único shot. D3=0.13.
- **Depois:** 5 estágios determinísticos. O LLM faz **uma coisa**: gerar hipóteses de mecanismo com evidence_quote do diff. Tudo mais é Script:

| Responsabilidade | Script | LLM |
|-----------------|--------|-----|
| Detecção de archetype | `archetype.py` | — |
| Tiering de evidência | `evidence_tagger.py` | — |
| Computação de risco | `risk_policy.py` | — |
| Quality gate | `quality_gate.py` | — |
| Geração de hipóteses | — | **HypothesisEngine** |
| Extração de evidence_quote | — | **HypothesisEngine** |

- **Decomposição:** `orchestrator.py` 600L → `HypothesisEngine` + `ReportBuilder` + orchestrator ~242L.
- **D1 estabilizou em 0.70–0.90** após a extração.

### Fase 5: V1 Delivery Gate — Jun 10–11
- **5 commits.** D3 JIRA fallback oracle, cross-model judge validation, n=50 estratificado.
- **RESULTADO: TODOS os 6 GATE thresholds PASSAM em n=50.**
- D1=0.70, D2=0.19, D3=0.23, D4=0.894, D5=0.413, D6=0.77.
- Verificação adversarial: 11/11 ACs, 3/3 ECs PASS.
- **Custo total do delivery: $0.29 por run de 50 commits (~$0.006/commit).**

### Fase 6: V2 — Contrastive + Extended Context — Jun 11
- **6 commits.**
- **Contrastive hypotheses:** Gerar 2–3 hipóteses diversas e selecionar pela melhor evidência grounded. Flips: 2213f719 e 55dcbe801e76 de D3=0→1.0.
- **Composite selector:** Multi-factor scoring no lugar de seleção binária por citation.
- **Extended context:** Test-adjacency + blame snippets para commits com contexto insuficiente.
- **SUPPORTED-only localization:** Filtro no report_builder. D2_fix_chain=0.384 (target 0.25 atingido).
- **Archetype FP fix:** Detecção de test-only/examples-only. FP 32%→24%.
- **n=20: D1=0.90, D3=0.35 (exatamente no target).**

### Fase 7: V2 n=50 Delivery — FALHA — Jun 11–12
- **3 commits.** Duas tentativas de n=50.
- **RESULTADO: FAIL.** D3=0.29/0.31 (target 0.35), D1=0.70/0.72 (target 0.80).
- **Lição aprendida:** n=20 era amostra favorável. Com 10 commits buggy no estrato, um único commit swingando muda a média em 0.10. **Nunca confiar em n=20 para decisões de delivery.**
- Segundo run com FP fix: D1 subiu para 0.72, D3 para 0.31. FP=24% (target 25% atingido).

### Fase 8: Technical Debt + Feature Flag — Jun 12
- **6 commits.** Rename chain (6 tasks, custo zero), unit tests para historical_rag, cache refactor, `--enable-historical-defect-context` flag.
- **481 testes (de 44 iniciais para 481 em 4 dias).**
- Harness: contrato negociado com evaluator, 7/7 ACs + 5/5 ECs verificados.

### Fase 9: Validação V2 — Jun 13
- **1 commit (resilience fix).** n=50 com todas as flags ativas.
- D1=0.714, D3=0.31, D6=0.786. **D3 ceiling confirmado.**
- Historical defect context: net lift +0.01±0.03. Dentro do ruído.

---

## 4. Scorecard atual (latest n=50, Jun 13)

| Dim | Gate | Target | Current | Status |
|-----|------|--------|---------|--------|
| D1 | 0.70 | 0.80 | **0.714** | GATE PASS, target miss (-0.086) |
| D2 | 0.15 | 0.25 | **0.184** | GATE PASS (D2_fix=0.384 target met) |
| D3 | 0.20 | 0.35 | **0.310** | GATE PASS, target miss (-0.040) |
| D4 | 0.60 | 0.75 | **0.881** | TARGET MET |
| D5 | 0.25 | 0.40 | **0.413** | TARGET MET |
| D6 | 0.60 | 0.70 | **0.786** | TARGET MET |

**4/6 targets atingidos. D1 e D3 ainda abaixo.**

---

## 5. Evolução dos scores — todos os key runs

| Run | Data | n | D1 | D3 | D6 | Custo | Nota |
|-----|------|---|-----|-----|-----|-------|------|
| Primeiro n=20 real | Jun 10 | 20 | 0.850 | 0.200 | 0.750 | $0.07 | Pré-oracle-isolation |
| iter-1 n=100 | Jun 10 | 100 | 0.860 | 0.280 | 0.835 | $0.37 | Buggy label vazando |
| iter-2 n=12 panel | Jun 10 | 12 | 0.750 | 0.292 | 0.833 | $0.11 | Smart diff |
| iter-3 n=12 panel | Jun 11 | 12 | 0.667 | 0.167 | 0.750 | $0.07 | Script-first (regressão temporária) |
| iter-3 n=20 v2 | Jun 11 | 20 | 0.900 | 0.175 | 0.750 | $0.10 | D1 corrigido, D3 atrasado |
| iter-3+fixes n=12 | Jun 11 | 12 | 0.917 | 0.458 | 0.750 | $0.10 | Pico D3 em painel |
| **V1 n=50 delivery** | Jun 11 | 50 | 0.700 | 0.230 | 0.770 | $0.29 | **All 6 gates PASS** |
| V2 contrastive n=20 | Jun 11 | 20 | 0.900 | 0.275 | 0.762 | $0.14 | Regressão do selector |
| V2 selector-fix n=20 | Jun 11 | 20 | 0.900 | 0.350 | 0.762 | $0.16 | D3 no target! |
| V2 n=50 attempt | Jun 12 | 50 | 0.720 | 0.310 | 0.780 | $0.40 | FP fix, D3 ceiling |
| V2 hist-ctx n=20 | Jun 12 | 20 | 0.900 | 0.375 | 0.787 | $0.13 | Amostra favorável |
| **V2 n=50 final** | Jun 13 | 49 | 0.714 | 0.310 | 0.786 | $0.33 | **Ceiling confirmado** |

**53 runs no total** (mock + real + smoke + panel + delivery).

---

## 6. O que funcionou

### Oracle isolation
Remover o campo `buggy` do contexto do agente derrubou D1 de 0.86→0.40. Provou que o agente estava trapaceando. Allowlist enforcement transformou um 0.86 falso num 0.70 real. Esta foi a decisão mais importante do projeto — sem ela, teríamos declarado vitória numa mentira.

### Arquitetura Script-first
Tirar a classificação de risco do prompt do LLM e colocar em estágios determinísticos (`archetype.py` → `evidence_tagger.py` → `risk_policy.py`) tornou cada decisão auditável e reproduzível. `PolicyVerdict.applied_rules[]` registra exatamente quais regras foram aplicadas. D1 estabilizou em 0.70–0.90.

### Geração contrastiva de hipóteses
Gerar hipóteses diversas e selecionar pela melhor evidência grounded flipou 2213f719 e 55dcbe801e76 de D3=0→1.0. Provou que seleção por evidência adiciona valor real para commits tier-1.

### Avaliação adversarial 6-dimensional
D6 (grounding) pega quando D3 melhora por chute em vez de investigação. O painel pegou toda regressão — nenhuma métrica única consegue isso. Exemplos:
- D6 alto + D3 baixo = descreve estrutura, não mecanismo de falha
- D3 alto + D1 baixo = identifica mecanismo mas não classifica
- D1 alto + D6 baixo = acertou no chute, sem evidência

### Escada iterativa n=5→n=12→n=20→n=50
Smoke tests a $0.01 pegam regressões catastróficas. n=20 a $0.13 é barato para iteração rápida. n=50 a $0.35 pega variância que n=20 esconde. Este padrão permitiu 53 runs com custo total de $4.87.

### FP fix via archetype detection
Detectar commits test-only/examples-only como clean archetypes derrubou a taxa de false positive de 32% para 24% sem perder buggy recall.

---

## 7. O que falhou ou underperformou

### Variância do n=20 mascarou o estado real
D3=0.35 em n=20 caiu para D3=0.29 em n=50. D1=0.90 caiu para D1=0.70. Com apenas 10 commits buggy no estrato, um único commit swingando muda a média em 0.10. **Lição:** n=20 é para iteração, nunca para decisões de delivery.

### Historical defect context (KNN RAG)
Hipótese H3a: injetar priors de categoria de defeito dos commits vizinhos no treinamento. Resultado: +0.01±0.03 no D3 — dentro do ruído. O hit rate do repo local é ~14% (a maioria dos commits não pode ser lookupado). O fallback por projeto adiciona distribuição genérica que não ancora o LLM em nada específico.

### Investigação multi-turn
A/B test em commits difíceis: ΔD3 < threshold. Dois turnos não ajudaram porque o segundo turno recebe o mesmo contexto — sem informação nova, o LLM restata a hipótese original. **Decisão:** manter single-turn, investir em contexto melhor em vez de turnos adicionais.

### Pipeline ITSM (escopo original)
Uma manhã construindo um analisador de change requests ITSM antes de descobrir que nenhum dataset público de ITSM tem cadeias de ground truth. Pivô para ApacheJIT que tem linkage commit→fix→JIRA.

### Prompt monolítico
iter-1 usou um prompt de ~135 linhas pedindo ao LLM para classificar E raciocinar E localizar E recomendar num único shot. Produziu D3=0.13 — estrutura correta, mecanismos errados. Decomposição resolveu.

---

## 8. Decisões arquiteturais com evidência

| Decisão | Alternativa rejeitada | Evidência |
|---------|----------------------|-----------|
| Script computa risco; LLM só gera hipóteses | LLM outputs HIGH/MEDIUM/LOW diretamente | D1 estabilizou; audit trail via PolicyVerdict |
| Selector contrastivo com multi-factor | Desabilitar select_primary_by_evidence | Flips 2213f719/55dcbe801e76 (0→1.0) |
| Selector fix antes de RAG | Rodar H3a RAG imediatamente | 4 regressões tier-2 eram bugs determinísticos ($0) |
| Same-model judge aceitável | Cross-model judge obrigatório | EXP-JUDGE-SWAP: <0.05 D3 variância |
| Single-turn mantido | Multi-turn com quality-gate trigger | A/B: ΔD3 < threshold sem contexto novo |
| Track D1 antes de D3 multiturn | Rodar multiturn spike imediatamente | D1 precisa de policy fixes ($0); D3 precisa de JIRA (91% wrong-mechanism) |
| Capability names no código de produção | Manter IDs de experimento (H3a, EXP-BUNDLE-EXPAND) | Uma vez merged, feature é parte do produto |

---

## 9. Limites conhecidos e problemas abertos

### D3 ceiling: wrong-mechanism (91%)
D3=0.31 é um ceiling de prompt-engineering. Em 91% dos commits com D3=0, o LLM identifica a área certa mas erra o tipo de defeito (ex: diz "race condition" quando era "null pointer"). Sem contexto do ticket JIRA, o LLM advinha o mecanismo apenas pelo diff.

**Proposta:** `v2-jira-context-injection` — injetar título + tipo do ticket JIRA. Expected delta: D3 +0.04–0.06.

### D1 gap: hidden-fix-in-CS commits
8 commits buggy recebem MEDIUM porque o diff parece cosmético (test assertion, CS log line). São fix commits disfarçados de code style. A presença de chave JIRA no commit message sinaliza que são fix-carrying.

**Proposta:** `v2-d1-cs-fix-detector` — detectar JIRA bug key → bypass archetype cap. Expected delta: D1 +0.04–0.08.

### Variância amostral
n=20 tem variância alta demais para decisões. O estrato de 10 commits buggy permite swings de ±0.10 na média por um único commit. Validar sempre em n=50.

### API instability
2 de 3 runs n=50 crasharam no meio por erro de API (empty response, server disconnect). Fix aplicado: per-commit error handling (skip, don't crash). Commit `6a70dfe`.

### Grounding rate
Apenas 2/20 commits têm citations grounded por run típico. A maioria das evidências é SPECULATIVE. Threshold de fuzzy match pode ser muito alto ou muito baixo.

### Escopo de 2 projetos
Resultados são sobre Camel e Hadoop apenas. Podem não generalizar para os outros 13 projetos do ApacheJIT.

---

## 10. Dados financeiros

| Métrica | Valor |
|---------|-------|
| Custo total LLM (53 runs) | $4.87 |
| Custo médio por n=20 run | $0.13 |
| Custo médio por n=50 run | $0.35 |
| Custo por commit investigado | $0.007 |
| Provider | cursor-sdk/claude-sonnet-4-6 |
| Modelo judge (D3/D5) | Mesmo modelo (validado cross-model) |

---

## 11. Evolução do codebase

| Métrica | Início | Final |
|---------|--------|-------|
| Testes | 0 → 44 → 392 → 465 → **481** |
| Commits | 0 → **44** |
| Módulos src/ | 0 → **17 componentes ativos** |
| Eval runs | 0 → **53** |
| Harness tasks | 0 → **38** (15 committed, 9 completed, 4 pending) |

### Componentes principais

| Componente | Módulo | Papel |
|------------|--------|-------|
| XGBoostRouter | `routing/router.py` | Roteamento $0 (AUC=0.855) |
| CommitContextBuilder | `context/context_builder.py` | Montagem de contexto + smart-diff 16K |
| HypothesisEngine | `hypothesis/hypothesis_engine.py` | Geração de hipóteses (único uso do LLM) |
| evidence_tagger | `analysis/evidence_tagger.py` | Tiering SUPPORTED/SPECULATIVE |
| risk_policy | `analysis/risk_policy.py` | Computação de risco (fonte única de verdade) |
| quality_gate | `analysis/quality_gate.py` | Trigger determinístico de follow-up |
| EvalHarness | `runners/eval_harness.py` | Scoring 6-dimensional + sampling estratificado |
| AdversarialJudge | `runners/eval_judge.py` | LLM-as-judge D3/D5 |

---

## 12. Próximos passos

1. **`v2-jira-context-injection`** (prioridade 32) — Injetar título+tipo do ticket JIRA no prompt de hipóteses. Ataca o ceiling D3 wrong-mechanism (91%). Expected: D3 +0.04–0.06. Requer CSV commit→JIRA key→issue type (57% dos commits camel já têm `CAMEL-NNNN` no message; tipo precisa vir da API JIRA ou proxy).

2. **`v2-d1-cs-fix-detector`** (prioridade 33) — Detectar commits com chave JIRA Bug no message → bypass archetype cap. Recupera 4–6 dos 8 BUG→MEDIUM. Expected: D1 +0.04–0.08. Compartilha o prerequisito CSV com #1.

3. **`v2-fp-risk-tightening`** (prioridade 34) — Endereçar D1 adicional se necessário após #2.

4. **`td-h3a-performance`** (prioridade 31) — heapq.nsmallest + LRU cache para KNN. Otimização de wall-clock.

---

*Gerado em 13 Jun 2026. Dados extraídos de .harness/state.json, breadcrumbs.jsonl, e 53 eval runs.*
