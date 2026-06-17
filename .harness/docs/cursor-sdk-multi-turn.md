# ADR: Cursor SDK Multi-Turn Integration

**Date:** 2026-06-17
**Status:** Active (in evaluation)
**Context:** P9 full V4 eval revealed performance and quality issues with single-shot SDK usage.

## Problem Statement

The V4 investigation harness requires multiple LLM interactions per case:
- Stage 2 (Planning): 1-3 calls (brief generation + validation retries)
- Stage 3 (Examination): 3-10 calls (hypothesis testing, evidence gathering)
- Stage 4 (Attribution): 1 call (suspect ranking)

Initial implementation used `Agent.prompt(mode="plan")` per call — spawning a fresh agent for each interaction. This caused:
1. **~30 min/case latency** (agent bootstrap overhead × calls)
2. **No context accumulation** between turns (each call started fresh)
3. **JSON parsing failures** (plan mode returns natural language, not structured JSON)

## Decision: Multi-Turn via Agent.create() + agent.send()

Replace one-shot `Agent.prompt()` with persistent agent sessions:

```python
agent = Agent.create(model="claude-sonnet-4-6", api_key=key, local=LocalAgentOptions(cwd=repo))
agent.__enter__()

# Planning turn
run = agent.send(planning_prompt)
brief = parse_brief(run.text())

# Examination turns (context accumulates)
for turn in examination_turns:
    run = agent.send(examination_prompt)
    evidence = parse_evidence(run.text())

# Attribution turn (has full investigation context)
run = agent.send(attribution_prompt)
suspects = parse_suspects(run.text())

agent.__exit__(None, None, None)
```

### Results (Partial — 6/20 cases)

| Metric | Agent.prompt() | Agent.create() | Δ |
|--------|---------------|----------------|---|
| Latency/case | ~30 min | ~12 min | **-60%** |
| Calls/case | 6-7 (same) | 6-10 | similar |
| Suspects parsed | 0 (broken) | 3-4 ✓ | fixed |
| Hit@5 | 0/2 (before fix) | 0/6 | both fail |
| Retrieval Recall | 50% | 50% | same |

### Key Observation

The pipeline is **mechanically functional** (suspects parsed, retrieval works) but attribution quality is poor (Hit@5 = 0/6). Ground truth is often IN the candidate set (retrieval=Y) but the LLM doesn't rank it correctly.

## Root Causes for Low Hit@5

### 1. Context Quality Between Turns

The Cursor SDK Agent accumulates context across `send()` calls, but this context is a raw conversation transcript — not structured investigation state. The agent sees:
- Turn 1: A long planning prompt → its own brief response
- Turn 2-N: Examination prompts → its evidence responses
- Turn N+1: Attribution prompt

**Problem:** By attribution time, the earlier examination findings are buried in conversation history. The agent may not recall or synthesize evidence effectively.

**Improvement ideas:**
- **Structured evidence summary:** Before attribution, inject a synthesized evidence summary (not just the raw conversation replay). Build this programmatically from parsed examination outputs.
- **Progressive context pruning:** Keep only the latest investigation state + collected evidence, not the full conversation transcript.
- **Explicit state handoff:** Between stages, send a "context update" message that summarizes what was learned, clearing cognitive overhead.

### 2. Prompt Format Mismatch

The Cursor SDK agent is optimized for coding tasks (editing files, running commands). Our prompts ask it to reason about git commits and produce JSON — a misfit for its tooling.

**Problem:** The agent may try to use its tools (file reads, grep) rather than reason from the provided text. Or it may wrap JSON in markdown blocks rather than returning raw JSON.

**Improvement ideas:**
- **Explicit "no tools" instruction:** Tell the agent to reason purely from provided context.
- **Robust parsing:** Already implemented — extract JSON from markdown code blocks (done).
- **Alternative backend:** Consider using the Cursor SDK's `Agent.prompt(mode="plan")` for individual turns where we don't need tool access, but `Agent.create()` for overall session management. Or use a direct LLM API (OpenAI, Anthropic) instead of the SDK agent abstraction.

### 3. Examination Stage Quality

The examination prompts ask the agent to "examine candidates" and "collect evidence" but don't provide enough structure for what counts as useful evidence for attribution.

**Problem:** The agent may confirm irrelevant details about commits rather than testing hypotheses that would differentiate the buggy commit from similar ones.

**Improvement ideas:**
- **Hypothesis-driven examination:** Each examination turn should target a specific hypothesis with a falsification plan.
- **Diff-level examination:** Provide actual commit diffs (or summaries) in examination prompts, not just commit metadata.
- **Evidence relevance scoring:** After each examination turn, score whether the collected evidence actually helps discriminate between candidates.

### 4. Attribution Reasoning Gap

Even when examination finds evidence about the correct commit, attribution may fail because:
- The confidence scores are estimated from word patterns, not actual reasoning
- The agent produces generic suspects rather than commits that match evidence

**Improvement ideas:**
- **Evidence→Attribution explicit link:** Require each suspect's rationale to cite specific evidence collected.
- **Contrastive attribution:** Ask "why this commit and NOT the other top candidates?"
- **Two-pass attribution:** First generate suspects, then re-score with a focused comparison.

## Resilience Improvements Needed

1. **Per-call timeout (implemented):** 300s SIGALRM per `agent.send()` call prevents indefinite hangs.
2. **Checkpoint/resume:** Script should save progress after each case and resume from where it left off.
3. **Agent lifecycle management:** Kill and recreate agent after timeout errors (bridge may be in bad state).
4. **Rate limiting awareness:** Cursor SDK may have undocumented rate limits that cause delays on sustained use.

## Key Architectural Finding (P9 Result)

**Retrieve-then-reason without diff access cannot match tool-use agents.**

After 9/20 cases with all improvements applied:
- Hit@5 = 0/9 = 0.000 (gate: 0.40, V3 baseline: 0.50)
- Retrieval Recall = 4/9 = 44% (ground truth IS in candidates)
- All cases produce 3-5 suspects (parsing works)
- Average 7 min/case (down from 30 min)

The LLM sees only candidate metadata (summary, files, date, signal) but NOT the actual
commit diffs. Without seeing what code actually changed, it cannot determine which commit
introduced the described bug behavior. It's essentially ranking commits by how "related"
their summary sounds, not by causal reasoning about code changes.

**V3 achieved Hit@5=0.50 because it had full `git show`, `blame`, `grep` tools.** The
LLM could read actual diffs and reason about code semantics.

### Path Forward

The V4 architecture needs ONE of these to compete with V3:

1. **Diff summaries in CandidateSet** — Pre-compute short summaries of each commit's
   actual changes (e.g., "Added null check to CqlPagingRecordReader.initialize()") and
   include them in the candidate metadata. Zero LLM cost if done via `git show --stat`.

2. **Examination tools scoped to CandidateSet** — Give the harness-governed LLM access
   to `git show <sha>` but ONLY for commits in the CandidateSet. This is the ADR §Q6
   "scoped tools" concept.

3. **Hybrid approach** — Use the V3 tool-use agent BUT governed by the V4 harness (state
   machine, completion criteria, budget). This gets V3's quality with V4's structure.

Option 3 is recommended for next iteration.

## Implementation Status (Completed)

1. ✅ **Checkpoint/resume** — saves/loads progress between runs
2. ✅ **Thread-based timeout** — per-call 300s timeout via ThreadPoolExecutor
3. ✅ **Agent recycling** — recreates agent on consecutive errors/timeouts
4. ✅ **Context bridge** — synthesizes evidence summary before attribution
5. ✅ **Improved prompts** — examination, attribution, planning all enhanced
6. ✅ **No-tools notice** — prevents SDK agent from using tools
7. ✅ **Full candidate visibility** — attribution sees all top candidates
8. ✅ **Temporal data** — commit dates in candidate display
9. ✅ **Suspect tracing** — actual suspect commit IDs stored in traces

## Next Steps (Priority Order)

1. **Finish n=20 eval** — document baseline (expected Hit@5≈0)
2. **Add diff summaries to CandidateSet** — `git show --stat` + first 10 lines of diff
3. **Implement scoped examination tools** — `git show` restricted to candidate SHAs
4. **Consider hybrid V3+V4** — V3 tool-use agent governed by V4 harness
5. **Consider direct API** — if SDK agent abstraction continues to hurt, use raw Anthropic API

## Trade-offs

| Approach | Latency | Quality | Complexity |
|----------|---------|---------|------------|
| Agent.prompt() per call | High (30m) | Low (no context) | Low |
| Agent.create() multi-turn | Medium (12m) | Medium (raw context) | Medium |
| Direct API + managed context | Low (5m est.) | High (curated context) | High |
| Hybrid (SDK for search, API for reason) | Medium | Highest | Highest |

The current multi-turn approach is a middle ground. If Hit@5 remains at 0 after prompt improvements, the "Direct API + managed context" approach should be explored — it gives full control over what the LLM sees at each stage.
