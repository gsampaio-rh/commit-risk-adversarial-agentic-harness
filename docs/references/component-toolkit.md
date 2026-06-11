# Component Toolkit

Building blocks for long-running agentic systems. The core principle: **pick the lightest component that reliably solves the problem.**

---

## Component Hierarchy

From cheapest/fastest to most expensive/slowest:

```
Script  →  Schema/Parser  →  Embedding  →  LLM (fast)  →  LLM (reasoning)  →  Agent Session
 free        free             low           low            medium              high
 ms          ms               ms            1-5s           10-60s              minutes
```

**Rule**: Start at the left. Move right only when the cheaper option demonstrably fails.

---

## Components

### Script

Deterministic code — regex, heuristics, rule engines, parsers, template engines.

| Property | Value |
|----------|-------|
| Cost | Free |
| Latency | Milliseconds |
| Reliability | 100% deterministic |
| When to use | Pattern matching, text splitting, threshold checks, formatting, keyword classification, severity assignment |

**Examples in practice:**
- Split contract by `ARTICLE` headings (regex)
- Extract definitions: `"Term" means X` (regex with smart quotes)
- Detect cross-references: `Section 3.2`, `Article IV` (regex)
- Classify severity by score thresholds (rule engine)
- Generate risk assessment from flag counts (template)

### Schema / Parser

Pydantic models, JSON schema constraints, structured output modes, validation layers.

| Property | Value |
|----------|-------|
| Cost | Free |
| Latency | Milliseconds |
| Reliability | Deterministic validation |
| When to use | Enforcing output shape, preventing parse failures, validating stage outputs, rejecting garbage |

**Key patterns:**
- Define expected output as Pydantic model BEFORE writing the stage
- Use `field_validator` to reject placeholders and enforce minimums
- Parse LLM output through schema immediately — fail fast
- Retry LLM call with validation error message as feedback

### Embedding

Vector representation of text for semantic operations.

| Property | Value |
|----------|-------|
| Cost | Low (~$0.0001/1K tokens) |
| Latency | 50-200ms |
| Reliability | Deterministic per model |
| When to use | Semantic similarity, clause retrieval, clustering, cross-contract comparison |

**Models:**
- `text-embedding-3-small` — general purpose, cheap
- `text-embedding-3-large` — higher quality
- Voyage Law — domain-specific for legal text

### LLM Call (Fast)

Single call to a small/fast model (Haiku, GPT-4o-mini, DeepSeek).

| Property | Value |
|----------|-------|
| Cost | Low (~$0.25-1/M tokens) |
| Latency | 1-5 seconds |
| Reliability | Non-deterministic; needs validation |
| When to use | Classification, labeling, simple extraction where script can't handle ambiguity |

**When NOT to use:**
- If regex/keywords work 80%+ of the time → use script
- If the task requires legal judgment → use reasoning model

### LLM Call (Reasoning)

Single call to a strong model (Sonnet, Opus, GPT-4o).

| Property | Value |
|----------|-------|
| Cost | Medium (~$3-15/M tokens) |
| Latency | 10-60 seconds |
| Reliability | Non-deterministic; needs validation |
| When to use | Legal reasoning, nuanced comparison, holistic assessment, language drafting |

**Justify every use:** Can a cheaper component solve this? If not, why not?

### LLM Batched

Multiple items grouped into one prompt, one call.

| Property | Value |
|----------|-------|
| Cost | Medium (one call, larger prompt) |
| Latency | 30-120 seconds |
| Reliability | Risk of cross-contamination between items |
| When to use | Processing N items that share context (clauses by category, rules by topic) |

**Trade-off:** Cheaper than N separate calls, but quality may degrade if prompt is too long or items interact.

### LLM Fan-Out

Parallel calls, one per item, capped concurrency.

| Property | Value |
|----------|-------|
| Cost | High (N × single call cost) |
| Latency | Bounded by slowest call (with parallelism) |
| Reliability | Independent failures; partial results possible |
| When to use | Independent tasks per item (extract per article, suggest per deviation) |

**Note:** Cursor SDK currently requires sequential fan-out (one agent session at a time).

### RAG (Retrieval-Augmented Generation)

Retrieve relevant context from a vector store before LLM call.

| Property | Value |
|----------|-------|
| Cost | Embedding cost + LLM cost |
| Latency | Retrieval (ms) + LLM (seconds) |
| Reliability | Depends on retrieval quality |
| When to use | When LLM needs precedent, prior contracts, or context beyond prompt window |

### Agent Session

Multi-step LLM with tool use, planning, self-correction.

| Property | Value |
|----------|-------|
| Cost | High (multiple LLM calls + tool executions) |
| Latency | Minutes |
| Reliability | Non-deterministic; can loop or diverge |
| When to use | Complex tasks requiring exploration, iteration, or tool interaction |

**Warning:** Agent sessions are the MOST expensive and unpredictable component. Use only when the task genuinely requires multi-step reasoning with tools. Most "agent" tasks can be decomposed into a pipeline of simpler calls.

### Adversarial Evaluator

Separate agent/context that tests output against criteria.

| Property | Value |
|----------|-------|
| Cost | Medium (one LLM call or scripted checks) |
| Latency | 30-120 seconds |
| Reliability | As good as the contract definition |
| When to use | Verification — never let the builder judge its own work |

---

## Decision Framework

```
Can a regex/rule handle it?
  YES → Script
  NO ↓

Is it just enforcing structure on known data?
  YES → Schema/Parser
  NO ↓

Does it need semantic similarity (not generation)?
  YES → Embedding
  NO ↓

Is it simple classification/labeling?
  YES → LLM (fast model)
  NO ↓

Does it require judgment, reasoning, or generation?
  YES → LLM (reasoning model)
  NO ↓

Does it need multiple items processed?
  Independent? → Fan-out or Batched
  Dependent? → Prompt Chaining
  NO ↓

Does it need multi-step tool use?
  YES → Agent Session (last resort)
```

---

## Cost Reference (approximate, 2025 pricing)

| Component | Cost per 1M input tokens | Cost per 1M output tokens |
|-----------|--------------------------|---------------------------|
| Haiku 3.5 | $0.80 | $4.00 |
| Sonnet 4 | $3.00 | $15.00 |
| Opus 4 | $15.00 | $75.00 |
| GPT-4o | $2.50 | $10.00 |
| GPT-4o-mini | $0.15 | $0.60 |
| text-embedding-3-small | $0.02 | — |

**Rule of thumb:** A 500K-char M&A contract ≈ 125K tokens. Processing all of it through Sonnet ≈ $0.38 input + output cost per call. Fan-out of 21 articles ≈ $8 total. Scripts cost $0.
