# When Prompt Engineering Hits the Wall: A Post-Mortem on LLM-Based Commit Risk Analysis

> Four iterations. Zero improvement. One commit. Here's what we learned.

---

## Background

We've been building **exp19b** — an agentic pipeline that reads a git commit diff and identifies the root cause of a bug. It runs against Apache Camel and Hadoop commits from the [ApacheJIT dataset](https://zenodo.org/record/6967238), and is evaluated across six dimensions. The one that matters most for this story is **D3: root-cause identification fidelity**, scored 0–1 by an LLM-as-judge against the JIRA oracle.

The V1 delivery gate was D3 ≥ 0.20. We passed it. The V2 target is D3 ≥ 0.35. As of the baseline, we're at **0.275** — close, but with a hard cluster of commits that won't move.

One of them, `294c169f6d66`, took four separate prompt engineering interventions before we finally understood what was actually wrong.

---

## The Pipeline

Before getting into the post-mortem, here's what the pipeline does:

```
git diff
    ↓
Router (XGBoost)          — HIGH / CLEAN risk prediction
    ↓
Context Builder           — assembles up to 16K chars of smart-ranked diff
    ↓
Hypothesis Engine (LLM)   — generates N hypotheses (claude-sonnet-4-5)
    ↓
Contrastive Layer         — regenerates alternative hypotheses for diversity
    ↓
Evidence Selector         — promotes the best-cited hypothesis to H1
    ↓
Evidence Tagger           — tags each hypothesis: SUPPORTED / SPECULATIVE / REFUTED
    ↓
Report Builder            — emits localization claims from SUPPORTED only
```

D3 is computed by the judge on the selected H1 mechanism. The judge grades on two axes:

- **Mechanism correctness** — does it describe what actually failed?
- **Grounding** — is it anchored to a specific quote from the diff?

Both are required for D3 = 1.0. This dual requirement turns out to be the entire story.

---

## The Commit: `294c169f6d66`

This is an Apache Camel commit that introduces a concurrency bug in `CacheManagerService`. The change adds:

```java
userCaches.computeIfAbsent(name, key ->
    newUserManagedCacheBuilder(keyType, valueType).build(true)
);
```

The cache is keyed by `name` alone, ignoring `keyType` and `valueType`. A concurrent caller requesting the same cache name with different K/V types receives the existing cache. The unchecked cast `(Cache<K,V>)` succeeds via type erasure, but subsequent `put()` and `get()` calls throw `ClassCastException` inside Ehcache's type-checking layer.

In V1, the agent scored **D3 = 0.25** on this commit. Partial credit — it got the area right but missed the exact mechanism.

Then we introduced the contrastive layer.

---

## What Happened When We Added Contrastive Prompting

Contrastive hypothesis generation is a standard technique: ask the model to generate N alternative hypotheses, then select the one with the strongest evidence. The intuition is that a single-pass generation often misses the mechanism; forcing diversity surfaces better candidates.

For most commits, this worked. D3 rose from 0.23 to 0.275 overall. Two commits that were previously D3 = 0 flipped to 1.0.

For `294c169f6d66`, it dropped from **0.25 to 0.0**.

The judge's rationale: the hypothesis correctly described the ClassCastException path, but could not cite a specific line from the diff where the type-unsafe operation occurred. Without a grounded quote, D3 = 0.

We ran forensics on the full 6-commit panel and found something unexpected.

---

## H-SEL-3: The Contrastive Prompt Replaces, It Doesn't Reorder

Our initial hypothesis was that the evidence selector was promoting the wrong candidate — a contrastive alternative with a weak citation beating a partially-correct H1. We called this **H-SEL-1**.

The forensics report said otherwise.

```json
// v2-d3-selector-forensics.json
{
  "commit_prefix": "294c169f6d66",
  "selector_analysis": {
    "all_candidates_ungrounded": true,
    "selector_reordered": false,
    "failure_mode": "hypothesis_regen",
    "driver": "contrastive_prompt"
  },
  "meta": {
    "selector_vs_prompt_conclusion":
      "All 6 panel commits: all_candidates_ungrounded=true → selector stable-sort.
       Regressions/improvements are contrastive_prompt driven (H-SEL-3), not selector_reorder."
  }
}
```

Across **all six commits** in the panel, every hypothesis candidate had `has_changed_line_citation = false`. The selector was not choosing the wrong candidate from a mixed pool. It was doing a stable sort across N equally ungrounded candidates.

The contrastive prompt wasn't reordering the hypothesis set. It was replacing it.

This is **H-SEL-3**: when the contrastive prompt generates a new set of hypotheses, it explores a different part of the mechanism space. For commits where the diff lacks the call site that would ground any hypothesis, every candidate in both the original and contrastive set ends up ungrounded — and the selector has nothing to work with.

---

## The Four Interventions That Failed

### Intervention 1 — V1 baseline (D3 = 0.25)

**Assumption**: A single-pass "If X then Y at file:area" prompt is enough to surface the failure mechanism.

**The prompt** (`HYPOTHESIS_SYSTEM_PROMPT`):

```
mechanism: "If <specific condition> then <specific failure> at <file>:<area>"
evidence_quote: "exact line(s) from the diff showing this mechanism (empty string if not visible)"
```

No explicit grounding requirement. The model was free to describe mechanisms it inferred without anchoring them to a specific diff line.

**What happened**: The model correctly identified the `CacheManagerService` area and flagged a concurrency risk — but anchored on a race condition between two `computeIfAbsent` calls, not on the type-erasure path. The judge gave partial credit (D3 = 0.25): mechanism plausible but imprecise, evidence absent.

**Why it partially worked**: The diff *was* in the context window. The model could see `computeIfAbsent` being added. It just picked the wrong failure mode from that line.

---

### Intervention 2 — Symptom-first prompt + T3 grounding evaluator (D3 = 0.0)

**Assumption**: Forcing the model to reason "Observable failure first → trace to +/- line" would ground citations. Adding a second challenge turn (T3) would catch any ungrounded outputs and force revision.

**The prompt** (`HYPOTHESIS_SYSTEM_PROMPT_H1H4T3`):

```
Reason symptom-first: imagine the user-visible failure BEFORE naming code structure.
Do not apply familiar framework templates without citing the exact changed line.

mechanism: "Observable: [user-visible failure]. Root change: [+/- line from diff]. Mechanism: [causal chain at file:area]"

## CHANGED-LINE EVIDENCE
For each primary hypothesis (first 3 in the list): if evidence_quote is non-empty,
it MUST contain at least one line starting with + or - (an actual code change from
the diff). Context-only lines (unchanged diff context without + or - prefix) do NOT count.
```

**The T3 evaluator loop** (`_mechanism_evaluator_loop_multi_turn`): after the first pass, if any of the top-3 hypotheses had `has_changed_line_citation = false`, a second turn was injected:

```
Mechanism evidence must cite at least one +/- changed diff line (not context-only).
Revise the following primary hypotheses with evidence_quote containing a + or - line:
- mechanism: "[logic-error] Observable: ClassCastException..."
```

**What happened**: The model produced mechanistically correct output — it now described the ClassCastException path via type erasure precisely. The `mechanism` text embedded `Root change: +userCaches.computeIfAbsent(...)`. But `evidence_quote` remained empty. The T3 challenge prompted a second attempt. Same result. The model was not able to produce a standalone `evidence_quote` with a `+/-` prefix because `computeIfAbsent` was the only changed line visible in the diff — and no line in the diff showed the *type validation* that causes `ClassCastException`. The model correctly described what would happen, but couldn't point at a line that showed it happening.

**Why it failed**: The T3 evaluator can challenge ungrounded outputs, but it cannot conjure evidence that isn't in the diff. The challenge was answered with "there is no +/- line I can cite" — which is correct.

---

### Intervention 3 — Contrastive diversity + composite selector (D3 = 0.0)

**Assumption (contrastive)**: A single-pass generation anchors on the most salient hunk. Forcing the model to produce 3 *categorically different* mechanisms would surface one that cites a different changed line with grounding.

**The prompt** (`HYPOTHESIS_SYSTEM_PROMPT_CONTRASTIVE`):

```
## CONTRASTIVE REQUIREMENT

Your first hypothesis (position 0) MUST be the highest-confidence, most direct
diff evidence mechanism — the failure path most strongly supported by the
changed lines in the diff.

For your second and third hypotheses (H2+), generate DIFFERENT causal categories:
  null-reference | lifecycle-ordering | concurrency | api-contract |
  input-validation | resource-leak | error-handling | logic-error

Begin each mechanism with its category label in brackets:
  "[null-reference] Observable: <failure>. Root change: <+/- line>. Mechanism: <chain>"

No two of the first 3 hypotheses may share the same category label.
```

**Assumption (selector)**: Even if contrastive generation produces a grounded candidate, the original binary sort (`has_changed_line_citation` only) might demote it. Replace with composite scoring.

**The composite selector** (`select_primary_by_evidence`):

```python
sort_key = (
    0 if grounded else 1,        # citation first
    -is_production_file(h.file), # prefer prod files
    original_index,              # stable tie-break
    -len(h.evidence_quote),      # longer quote = stronger
)
```

**What happened**: Contrastive generation produced 3 hypotheses across different categories (`[logic-error]`, `[concurrency]`, `[api-contract]`). All three had `evidence_quote = ""`. No changed line in the diff showed type-validation failure, race entry, or API contract violation — because the test call site and the prior implementation weren't in the diff. The composite selector scored all three identically: `(1, ..., index, 0)`. Stable sort. Same H1 as before.

The forensics confirmed this across all 6 panel commits:

```
"all_candidates_ungrounded": true,
"selector_reordered": false
```

**Why it failed**: The diversity constraint worked — the model explored different mechanism categories. But every category required citing the type-validation call site, which was in `CacheManagerServiceTest.java`, not in the production diff. Three diverse ungrounded hypotheses are still three ungrounded hypotheses. The selector had nothing to sort.

---

### Intervention 4 — H1 anchor in contrastive prompt (D3 = 0.0)

**Assumption**: Contrastive generation is drifting away from the partially-correct V1 H1. If we force H1 into the preamble as the highest-confidence anchor, the contrastive set will orbit around a correct mechanism and at least one variant might cite a grounding line.

**The modification** (contrastive system prompt, preamble injection):

```
You are generating alternative hypotheses. The following is the primary hypothesis
with the highest confidence — preserve its core insight while exploring alternatives:

[logic-error] Observable: ClassCastException at the call site when a second caller
requests the same cache name with different K/V types. Root change:
+userCaches.computeIfAbsent(name, key -> ...newUserManagedCacheBuilder(...).build(true))
stores caches keyed by name alone, ignoring types...
```

**What happened on other commits**: On `55dcbe801e76` (regex capture group), the H1 anchor worked — D3 lifted from 0.0 to 0.75. The anchor prevented the contrastive layer from replacing a partially-correct H1 with off-target mechanisms, and one of the constrained variants landed a cited line.

**What happened on `294c169`**: The H1 text was mechanistically correct but cited no line. Constraining the contrastive set to "orbit around H1" meant three hypotheses all described the ClassCastException path in different ways — and none of them could cite a line from the diff that showed the type check failing, because that line (`cacheManager.getCache()` validating `K.class, V.class`) was only in the test file.

**The key asymmetry**: For `55dcbe`, the mechanism was in the diff but the model wasn't finding it. The anchor redirected attention. For `294c169`, the mechanism was correct but the evidence was *outside* the diff. No amount of attention-steering reaches context that doesn't exist.

**D3 = 0.0. Four interventions. Zero movement.**

This is the prompt engineering ceiling.

---

## What the Ceiling Actually Is

Here's the key realization, stated precisely:

> **The LLM cannot cite evidence it was never shown.**

The diff for `294c169f6d66` shows the `computeIfAbsent` call being added. It does *not* show:

- The test that exercises `cacheManager.getCache(name, K.class, V.class)` after the write
- The prior implementation with type-safe cache retrieval
- The call site where the unchecked cast propagates to user code

The judge requires a quote from the diff. The relevant code wasn't in the diff. No prompt formulation can extract a quote from context that doesn't exist.

This is structurally different from a failure caused by bad prompt design. A better prompt cannot generate evidence that isn't there.

```
Prompt engineering ceiling = f(context quality), not f(prompt quality)
```

The diagnostic signal is `all_candidates_ungrounded = true` across the entire hypothesis set. When you see this, stop iterating prompts and start auditing what's missing from the context window.

---

## What Actually Fixed It: Context Expansion

We added two optional flags to `CommitContextBuilder.build()`:

```python
def build(
    self,
    commit_id: str,
    project: str,
    csv_row: dict | None = None,
    max_diff_chars: int = 16_000,
    include_test_adjacency: bool = False,   # NEW
    include_blame_snippets: bool = False,   # NEW
) -> InvestigationContext:
```

**`include_test_adjacency`**: finds paired `*Test.java` files whose hunks are present in the same commit diff (matched by Java identifier overlap), and appends them as a `## Test Adjacency` block. Cap: 2K chars. Uses only hunks already in the commit diff — no full file injection.

**`include_blame_snippets`**: calls `git blame` on the top-ranked production files changed by the commit, appending author + prior-state context as a `## Git Blame` block. Cap: 1500 chars/file, max 2 files. Reuses existing `GitContextProvider.get_blame_snippet()`.

Both run within a reserved 2500-char budget:

```python
assemble_cap = max(1, max_diff_chars - EXPANSION_RESERVED_CHARS)
assembled = assemble_diff(raw_diff, max_chars=assemble_cap)
# assembled production hunks: guaranteed ≤ 13500 chars
# expansion headroom: 2500 chars
diff = assembled.text + build_expansion_sections(...)
```

The production diff is never truncated further. Expansion appends from the reserved headroom only.

### The Result

For `294c169f6d66`:

- `UserManagedCacheTest.java` was found in the same commit diff
- Its hunks exercised `cacheManager.getCache(name, K.class, V.class)` — the type-validation call site
- Git blame showed the prior type-safe implementation, making the regression vector explicit

**D3: 0.0 → 0.5** in a single run. $0.023 total cost.

The mechanism text was already correct. The agent finally had the evidence to anchor it.

---

## The Numbers

### D3 progression across experiment iterations

| Checkpoint | Panel mean D3 | Note |
|---|---|---|
| V1 n=50 (baseline) | 0.230 | Gate: 0.20 ✓ |
| Pre-contrastive n=20 | 0.235 | Small lift from evidence tagger |
| V2 contrastive n=20 | 0.275 | +0.045 overall; 4 regressions |
| Post selector-fix (panel) | 0.292 | 3/4 regressions recovered |
| Bundle expand (294c169) | 0.5 | Single-commit; context-level fix |

### Per-commit D3 delta (V1 n=50 → V2 n=20)

| Commit | Bug class | V1 | V2 | Δ | Outcome |
|---|---|---|---|---|---|
| `294c169` | cache type-erasure + race | 0.25 | 0.00 | −0.25 | **Ceiling hit** |
| `409664` | trigger null assignment | 0.50 | 0.25 | −0.25 | Partial regression |
| `fbf0ff` | lifecycle ordering | 0.25 | 0.50 | +0.25 | Improved |
| `fe57a498` | format string input-val | 0.25 | 0.75 | +0.50 | Strong improvement |
| `2213f719` | enum whitespace split | 0.00 | 0.00 | 0.00 | Unchanged |
| `55dcbe80` | regex capture group | 0.00 | 0.50 | +0.50 | Contrastive flip |

### Hypothesis failure taxonomy

| Hypothesis | Description | Confirmed? |
|---|---|---|
| H-SEL-1 | Contrastive winner had better citation than H1 | No — all candidates ungrounded |
| H-SEL-2 | Wrong candidate cited irrelevant changed line | No — no citations at all |
| **H-SEL-3** | **Contrastive prompt replaced hypothesis set entirely** | **Yes — 6/6 commits** |
| H-SEL-4 | Evidence tagger bias from ungrounded commits | Symptom, not cause |

---

## Key Takeaways

### 1. Measure grounding rate, not just score

`all_candidates_ungrounded` is the leading indicator. When every candidate in the hypothesis set has zero changed-line citations, stop iterating prompts. The ceiling is structural.

### 2. Context budget is an accuracy dial

We spent significant effort on prompt engineering — composite scoring, H1 anchoring, contrastive constraints — and gained nothing on `294c169`. Adding 2500 chars of context moved it from 0.0 to 0.5 in one shot. Context budget is a quality parameter, not a fixed constraint.

### 3. Contrastive prompting has a grounding precondition

Contrastive diversity works when the original hypothesis set is at least partially grounded. When the diff lacks the call site that would ground any hypothesis, contrastive generation explores a wider wrong-mechanism space and makes things worse, not better. Check grounding rate before enabling contrastive.

### 4. The LLM-as-judge amplifies the grounding requirement

A D3 score of 1.0 requires both correct mechanism *and* a quote from the diff. These are independent failure modes. A model can reach semantic correctness (mechanism right) while failing on citation (no quote). Prompt engineering addresses mechanism quality; only context expansion addresses citation availability.

### 5. Ceiling detection is cheap; intervention is cheap

The `all_candidates_ungrounded` flag is deterministic and free. Running a targeted 3-commit context expansion costs $0.023. The cost of the four failed prompt engineering iterations was higher — both in LLM cost and in engineering time.

---

## What's Next

The context expansion result was `decision: expand_to_hard_panel`. Two of the three target commits (`572f3cee35fe`, `520f7eb892f5`) didn't improve — they need a broader investigation. The next step is a full mini n=20 re-gate with both the selector fix and the context expansion active, targeting D3 ≥ 0.35 and D2 ≥ 0.25.

If that gate passes, we go to n=50 delivery. If D3 is still below 0.35 after the gate, the next lever is RAG over ApacheJIT historical bugs — injecting top-3 defect category labels from 28K closed bug pairs.

But the architectural lesson is already clear: when an LLM pipeline stalls on a specific subset of inputs, the question to ask first is not "what prompt would fix this?" but "what information is absent from the context that would make this answerable?"

---

## References

- **ApacheJIT dataset**: Ni, A. et al. (2022). [Just-In-Time Defect Prediction on JavaScript Projects: A Replication Study](https://dl.acm.org/doi/10.1145/3524842.3528027). MSR 2022.
- **LLM-as-judge calibration**: Zheng, L. et al. (2023). [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685). NeurIPS 2023.
- **Contrastive chain-of-thought**: Chia, P.K. et al. (2023). [Contrastive Chain-of-Thought Prompting](https://arxiv.org/abs/2311.09277).
- **JIT-SDP survey**: Ni, A. et al. (2023). [Just-In-Time Defect Prediction: A Comprehensive Study and Practitioner Survey](https://arxiv.org/abs/2305.02185). IEEE TSE.

---

*exp19b · branch `experiment/v2-d3-contrastive` · commits `0350f28` (selector-fix), `6fbcd22` (bundle-expand), `188229f` (D2 localization)  
Dataset: ApacheJIT · Model: claude-sonnet-4-5 · Judge: claude-sonnet-4-6*
