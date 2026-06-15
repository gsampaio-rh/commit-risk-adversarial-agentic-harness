# Evaluation Framework — Bug Attribution

V3 replaces the V2 six-dimension risk panel with retrieval and attribution metrics centered on **Hit@5**.

## Metrics

| Metric | Type | Question |
|--------|------|----------|
| **Hit@1** | Binary | Is `bug_hash` the top suspect? |
| **Hit@5** | Binary | Is `bug_hash` in top 5? **Primary metric** |
| **MRR** | Continuous | Mean reciprocal rank of `bug_hash` |
| **D3 Attribution** | LLM judge (0-4) | Does mechanism match root cause? |
| **D6 Evidence** | Script (0-4) | Are quotes grounded in diffs? |
| **Retrieval Recall** | Binary | Did agent fetch `bug_hash` during search? |

### Why Hit@5 over Hit@1

SZZ-based `bug_hash` has known noise. Hit@5 tolerates this; Hit@1 is tracked but not gated.

## Acceptance Thresholds (provisional)

| Metric | GATE | TARGET |
|--------|------|--------|
| **Hit@5** | >= 0.30 | >= 0.50 |
| **MRR** | -- | >= 0.35 |
| **D3 Attribution** | >= 0.20 | >= 0.35 |
| **D6 Evidence** | >= 0.60 | >= 0.70 |
| **Retrieval Recall** | >= 0.50 | >= 0.70 |

## Zero-LLM Baselines

| Baseline | Method |
|----------|--------|
| **git-blame-naive** | Blame files at `COMMIT_B~1`, return most recent commit |
| **file-history-recency** | Most recent commit touching JIRA-mentioned files |

## Cost Tracking

Every investigation records: `tokens_in`, `tokens_out`, `tool_calls`, `cost_usd`, `latency_ms`.

Budget limits: 30 tool calls, 100K tokens, $0.50 per investigation.

## Related

- [temporal-model.md](temporal-model.md) — what the agent may see
- [glossary.md](glossary.md) — term definitions
- [agent-loop.md](agent-loop.md) — investigation loop
