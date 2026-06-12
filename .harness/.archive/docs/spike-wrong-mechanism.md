# Spike: Wrong-Mechanism Root Cause — Research & Hypotheses

**Status**: Draft · Jun 11, 2026  
**Problem**: D3=0.230 at V1 n=50 — 91% of D3=0 failures are `wrong-mechanism`  
**Question**: Why does the agent cite the right files but identify the wrong failure mechanism? What can be done?

---

## 1. Problem Definition — Agnostic

This is **not** hallucination (D6=0.77 confirms the agent cites real files from the real diff).  
This is **not** wrong classification (the risk level is often correct).  
This is specifically:

> The agent derives the wrong causal chain between the code change and the failure.

The agent correctly identifies:
- That the commit is risky (D1 ✓)
- Which files were changed (D6 ✓)

But it identifies:
- The wrong mechanism for *why* those changes introduce a bug (D3 ✗)

**Confirmed examples from n=50 hard panel:**

| Commit | Changed Area | Agent Says | Actual Mechanism |
|--------|-------------|-----------|-----------------|
| `55dcbe` | HTTP routing in Camel | URL-as-path handling | NPE when Host-header is null |
| `dd6f9b` | Type refactor | Minus-sign parsing regression | Implicit narrowing cast drops precision |
| `ea2118` | Lifecycle ordering | RoutesCollector absent | Spring Boot auto-config initialization order |
| `2213f7` | Guard clause removal | Missing validation | Null dereference via removed null-check |

The agent has sufficient context (D6 grounding ≥ 0.70 on all these commits). The problem is in the *reasoning*, not the *retrieval*.

---

## 2. Literature Review

### 2.1 Abstraction Bias / Familiar Pattern Attack

**Source**: *Trust Me, I Know This Function* — NDSS 2026 (Bernstein et al., arxiv 2508.17361)

LLMs skip local line-level reasoning when they recognize a familiar code pattern, instead retrieving a memorized high-level template. The model effectively says "I know this function" and applies the defect template most commonly associated with that pattern in training data — rather than analyzing the specific lines that changed.

**Direct applicability**: Apache Camel HTTP routing code matches patterns (Spring MVC, JAX-RS, servlet filters) that Claude has seen thousands of times. The model applies the "most common HTTP routing bug" template (URL-as-path, encoding, redirect) rather than analyzing the specific guard clause removal in the diff.

**Critical finding**: This bias **persists even when the model is explicitly warned about it in the system prompt** and is **universal across OpenAI/Anthropic/Google model families**. Prompt-level mitigations alone are insufficient.

### 2.2 Anchoring Bias / Failure to Update Belief

**Source**: *Stalled, Biased, and Confused* — arxiv 2601.22208 (cloud RCA study)

Named `RF-13` (Anchoring Bias) in the reasoning failure taxonomy: "Fixates prematurely on one hypothesis and neglects exploration of alternatives." The study shows this is the **clearest negative predictor of correctness**, associated with ≥15% drop and making correct predictions 45% less likely.

The model generates its first plausible hypothesis and never seriously considers that a different mechanism might be more specific to the actual lines changed.

### 2.3 Self-Consistent Error

**Source**: *Too Consistent to Detect* — EMNLP 2025 (Tan et al., ACL 2025.emnlp-main.238)

Wrong-mechanism diagnoses may be **systematic rather than stochastic** — the model produces the same wrong mechanism every run, regardless of temperature. Two critical findings:

1. Self-consistent errors **do not decrease as model scale increases** (unlike stochastic errors which reduce with larger models).
2. **Self-consistency voting (majority of N samples) falls below random guessing** for detecting these errors. If the model always says "URL-path" for commit X, sampling 5 times gives 5-0 vote for "URL-path" — the wrong answer wins.

**Direct implication**: Running the same model multiple times and voting will NOT fix wrong-mechanism errors. Cross-model verification is required.

### 2.4 Completion Bias / Systematic Overconfidence

**Source**: Claude Code field report — anthropics/claude-code issue #61932; issue #60226

The model optimizes for *appearing to have answered* over *actually having reasoned*. It produces a specific, confident mechanism even when the context is insufficient to derive the correct one — because admitting "I see risky changes but I cannot determine the specific mechanism" is a task failure, and the model's training reinforces task completion.

This is the "context anxiety" intuition: the model feels compelled to name a mechanism. It chooses the most plausible-sounding one given the pattern, not the most evidenced one given the specific lines.

**Named behavior**: `recognition-without-arrest` — the model can articulate that it should consider multiple mechanisms, and then immediately name only one without actually considering alternatives.

### 2.5 Contrastive Evidence and Multi-Stage Filtering

**Source**: LLM4FL (LLM4FL, openreview z91EvZbSI1); CrashFixer (arxiv 2504.20412)

LLM4FL shows that **method ordering in the initial list affects fault localization accuracy by up to 22-36%**. CrashFixer shows that grounding hypotheses in execution traces (rather than static diffs) reduces hallucinated mechanisms.

Key technique from LLM4FL: **propose fix → re-rank suspicious methods based on the proposed fix**. The model that reasons about what a fix would look like naturally constrains the mechanism space to what is actually fixable.

### 2.6 Iterative Refinement with Outcome Verification

**Source**: REFLECT — arxiv 2606.09071

REFLECT closes the loop: (1) diagnose a step, (2) inject a targeted correction, (3) replay the agent, (4) if the outcome flips → hypothesis verified; if not → re-localize. The contrastive evidence from the flip is used to sharpen the attribution.

Applied to our setting: generate mechanism H, construct a minimal test that would fail if H is true, check if the test passes on the fix commit. The fix commit is ground truth we already have.

---

## 3. Topology Options (Agent Architecture Alternatives)

Read these before the hypotheses — each hypothesis below references which topology it works best in. Topologies are architectural choices; hypotheses are reasoning interventions. They compose.

Reference: `docs/references/patterns.md`

---

### T1 — Debate Pattern

**Topology**: Two agents argue opposing positions about the mechanism; a third (judge) picks.

```
diff + context
     ↓
[Agent A] → "mechanism: Host-header NPE"
[Agent B] → "mechanism: URL-path encoding"
     ↓
[Judge] → evaluates both arguments against the diff → picks winner
```

**Why applicable**: Wrong-mechanism is a decision with genuine trade-offs requiring explored alternatives — exactly the Debate pattern's target. The model commits to the first plausible mechanism (anchoring bias). Debate structurally forces two distinct positions to coexist, making it impossible for anchoring to suppress the alternative.

**Connection to literature**: Maps to H2 (cross-model verification) but more structured — instead of "do you agree?", Agent B must argue the strongest alternative case.

**Cost**: 3 LLM calls per commit (A + B + judge). ~$0.009/commit. ~3x current.

**Watch out for**: Both agents may generate plausible-sounding but equally wrong mechanisms if they share the same training biases. Requires either H4 (evidence forcing on both agents) or a different model family for Agent B (see H2b).

**Verdict**: High potential. Directly attacks anchoring bias at the architecture level.

---

### T2 — Map-Reduce / Mechanism Specialization

**Topology**: Fan-out to N specialized agents, each forced to analyze the diff through one lens; reduce to the most evidenced mechanism.

```
diff + context
     ↓ (parallel)
[Null-Safety Specialist]        → "could this introduce a NPE? where?"
[Concurrency Specialist]        → "could this introduce a race condition? where?"
[Type-Safety Specialist]        → "could this introduce a type error? where?"
[Lifecycle/Ordering Specialist] → "could this break initialization order? where?"
     ↓
[Reducer] → picks the one with the strongest evidence_quote ⊆ diff (+/- lines)
```

**Why applicable**: Abstraction bias causes the model to apply the *most common* pattern for the general code area. If anchored on "HTTP routing = URL bug", it never seriously considers "HTTP routing = null-header bug." Specialized agents with a forced lens make the null-safety analysis *mandatory*, not optional.

**Cost**: N=4 specialist calls + 1 reducer = 5 LLM calls. ~$0.015/commit. ~5x current.

**Cost optimization**: Replace Sonnet with a small code-specific model (Qwen2.5-Coder-7B) for the specialist lanes — each specialist only needs focused local code analysis, not framework-level reasoning. ~$0.0003/call vs $0.003/call → ~10x reduction in specialist layer cost.

**Watch out for**: The reducer must pick based on evidence quality, not specialist confidence. Needs a deterministic reducer (script, not LLM) using our `evidence_tagger` output.

**Verdict**: High structural elegance. The reducer can be deterministic (most SUPPORTED quotes with +/- lines wins). Works well with H4. Specialist count can be tuned to 2-3 for the most common mechanism categories in the hard panel.

---

### T3 — Evaluator-Optimizer on Mechanism (Tight Loop)

**Topology**: HypothesisEngine proposes mechanism → MechanismEvaluator critiques it → loop until grounded or max rounds.

```
diff + context
     ↓
[HypothesisEngine] → proposed_mechanism + evidence_quote
     ↓
[MechanismEvaluator] → "the quote you cited is context, not the changed line;
                         propose a mechanism that cites a +/- line"
     ↓
[HypothesisEngine] → revised_mechanism + new evidence_quote
     ↓
[MechanismEvaluator] → PASS / challenge again (max 2 rounds)
```

**Why applicable**: The evaluator doesn't need to know the correct mechanism — it only checks that the proposed mechanism is grounded in a changed line (H4 criterion). This is cheap and deterministic.

**Important distinction from iter-3f multi-turn**: iter-3f added a second turn with more context. T3 adds a second turn with **mechanism critique** — a fundamentally different forcing function.

**Cost**: 1-2 extra LLM calls per commit on the challenge path only. ~$0.003-0.006 additional.

**Watch out for**: Evaluator must use max_rounds=2 and a deterministic criterion (script checks +/- line presence), not an LLM "is this good?" check — the latter risks infinite challenge loops.

**Verdict**: Highest ROI of the topology options. Minimal extra cost, directly forces mechanism re-grounding. Fits naturally into existing `quality_gate` infrastructure.

---

### T4 — Parallel Hypothesis Diversity

**Topology**: Run the HypothesisEngine N times in parallel with explicit diversity constraints.

```
diff + context
     ↓ (parallel, 3 calls)
[Run 1] → mechanism A  (no constraint)
[Run 2] → mechanism B  ("not about A")
[Run 3] → mechanism C  ("not about A or B")
     ↓
[Script Reducer] → pick the one with strongest +/- evidence
```

**Why applicable**: The wrong mechanism is often self-consistent — the model produces the same wrong answer every run. This explicitly forces the alternatives the model would never naturally consider.

**Cost**: 3 LLM calls in parallel. Same latency as 1 call. ~$0.009/commit.

**Watch out for**: Run 2 and 3 are conditioned on Run 1's mechanism — introduces sequential dependency. Mitigation: pre-defined diversity templates rather than dynamic conditioning.

**Verdict**: Interesting for self-consistent errors specifically. Lower cost than T1, but prompt-level diversity forcing may not be sufficient per FPA paper.

---

### Topologies to Skip and Why

| Pattern | Why it doesn't apply |
|---------|---------------------|
| **Ralph Loop** | Wrong-mechanism is a single-turn problem. Looping the same analysis produces the same wrong mechanism. |
| **Hierarchical** | Overkill. The problem is in one stage (mechanism identification), not coordination across many levels. |
| **Routing (mechanism-level)** | Would require knowing the mechanism category before analysis — circular. |
| **Prompt Chaining (fixed)** | Already our architecture. The problem is *within* a stage, not between stages. |
| **Approval Checkpoints** | Human-in-the-loop for mechanism identification defeats the purpose of automation. |

---

### Topology Ranking

| Rank | Topology | Rationale |
|------|----------|-----------|
| 1 | **T3 (Evaluator-Optimizer)** | Best ROI. Deterministic criterion, minimal cost, fits existing `quality_gate`. |
| 2 | **T1 (Debate)** | Best structural attack on anchoring. Forces two positions. Moderate cost (3x). |
| 3 | **T2 (Map-Reduce)** | Highest structural elegance, highest cost. Reserved for irreducible tier-1 after T3 fails. |
| 4 | **T4 (Parallel Diversity)** | Useful for self-consistent errors. Lower cost than T1, riskier (prompt-level diversity). |

---

## 4. Hypothesis Space

Each hypothesis below specifies which topology it operates in and lists variants where topology changes the behavior meaningfully.

---

### H1 — Symptom-First Reasoning (reverse causality)

**Mechanism**: Restructure the hypothesis generation prompt to start from user-observable behavior rather than code structure.

Current flow: "look at this diff → what could go wrong?"  
Proposed flow: "what user-visible failure could result from this change?" → "which specific lines enable that failure path?"

**Why this works**: Abstraction bias is triggered by pattern recognition of code structure. Starting from observable behavior breaks the pattern-matching shortcut — the model has to imagine a runtime scenario specific to the changed lines, not recognize a familiar code shape.

**Literature support**: CrashFixer (hypothesis from execution traces), FuseFL (step-by-step reasoning with test outcomes)

**Cost**: Zero. Prompt restructure only. No additional LLM calls.

**Risk**: May increase verbosity without improving precision if the model still anchors on the symptom space ("this could cause a 500" is as generic as "this could cause an NPE").

**Topology variants**:

| Variant | Topology | Description |
|---------|----------|-------------|
| H1-flat | Single agent (baseline) | Symptom-first prompt in one HypothesisEngine call. Zero cost. |
| H1-T3 | T3 (Evaluator-Optimizer) | Symptom-first prompt + MechanismEvaluator forces the symptom to be grounded in a specific +/- line before passing. Best pairing — H1 generates the symptom anchor; T3 verifies it is actually evidenced. |
| H1-T1 | T1 (Debate) | Agent A uses symptom-first; Agent B uses code-first. Judge picks. Forces explicit comparison between the two reasoning directions. |

**Recommended pairing**: H1-T3 as Layer 1. The symptom prompt reduces abstraction bias; T3 catches the cases where the symptom anchor is still not grounded in the actual changed lines.

---

### H2 — Cross-Model Mechanism Verification

**Mechanism**: After the primary model proposes a mechanism, a second *different* model checks it. If the second model disagrees, flag for re-analysis or escalate.

**Why this works**: Self-consistent errors are model-specific and rarely overlap across different LLM families (EMNLP 2025). If Claude Sonnet always says "URL-path" for commit X, a different model will not have the same systematic bias — making the disagreement detectable.

**Literature support**: Cross-model probe (EMNLP 2025), consortium voting (arxiv 2510.19507)

**Cost**: 1 additional LLM call per commit in the "uncertain mechanism" path. ~$0.003-0.005 per commit.

**Risk**: Disagreement signal is useful only if the second model is genuinely independent (different architecture/training, not just a different version of Sonnet).

**Implementation note**: The verifier does NOT need to produce a better mechanism — it only needs to detect "this mechanism looks wrong." That's a much easier task than root cause analysis.

**Topology variants**:

| Variant | Topology | Description |
|---------|----------|-------------|
| H2a | Single verifier | General-purpose model (GPT-4o, Gemini) as verifier. Works, but risk of shared abstraction biases in common framework code. |
| H2b | Single verifier (code-specific) | DeepSeek-Coder or Qwen2.5-Coder as verifier. Code models trained on 87%+ code data have structurally different abstraction biases from Claude Sonnet (general RLHF). They do not anchor on the same familiar code patterns. Role is disagreement detection only — not better investigation. Cost: same as H2a. |
| H2c | T1 (Debate) | H2b as Agent B in a Debate topology. Instead of "do you agree?", Agent B argues the strongest alternative case. More structured than H2a/H2b. Cost: full T1 (3 LLM calls). |

**Recommended variant**: H2b for maximum architectural independence at H2a's cost. H2c if Debate topology is adopted.

---

### H3 — Historical Bug RAG (dataset-grounded domain knowledge)

**Mechanism**: Embed historical buggy commits from the ApacheJIT dataset. At investigation time, retrieve the N most semantically similar commits and inject their confirmed defect category as context.

Prompt injection: *"Historically, commits with this pattern in Apache Camel have caused: [defect_category list]."*

**Why this works**: Replaces generic training-time templates with domain-specific patterns (bugs that actually occurred in Apache Camel / Hadoop). The model's prior shifts from general to project-specific.

**Literature support**: Linux kernel mailing list paper (arxiv 2505.19489), FlexFL (arxiv 2411.10714)

**Cost**: One-time embedding of corpus. Query per investigation: ~$0.001. Infrastructure: vector DB or cosine search over embeddings.

#### Critical constraint — temporal correctness

This is **not** "RAG over everything in the dataset." Requires strict temporal scoping to avoid oracle contamination:

```
RAG corpus = { (buggy_commit, defect_category) }
             where buggy_commit.date < T_investigated
             AND   fix_commit.date   < T_investigated   ← closed pairs only
```

| Scope | What's included | Risk |
|-------|----------------|------|
| **Unrestricted** | All 28K pairs including future | INVALID — fix commit leaks mechanism directly |
| **Commits up to T** | Buggy commits before T | Partial pairs included (bug known, fix unknown — no mechanism label) |
| **Closed pairs up to T** | Both buggy + fix commits before T | Only valid option — mechanism confirmed without future info |

**Why fix commits must be excluded entirely**: Fix commit messages routinely say "Fix NPE when Host-header is null" — injecting that into context leaks the exact mechanism, not just a binary flag.

#### What is the "defect category" source?

| Source | Valid? | Reason |
|--------|--------|--------|
| Fix commit diff | No | Oracle — the answer itself |
| Fix commit message | No | Describes the mechanism explicitly |
| JIRA issue description | No | Often describes the user-visible bug = mechanism |
| Buggy commit diff + message | Yes | What the agent already sees |
| Heuristic category from buggy diff (null-check removal → `null-reference`) | Yes | Derived without fix |
| JIT-SDP model prediction (H8) over buggy diff | Yes | Parameterized over past patterns, no future info |

**Risk**: Retrieval quality. Similar-looking diffs may have different mechanisms (a null-check removal could be defensive cleanup or a genuine defect). RAG could introduce new wrong-mechanism anchoring. **Must be tested on the hard panel (n=18) before any n=50 integration.**

**Relationship to H8**: H3 is retrieval-based (finds similar examples). H8 is parameterized (compresses patterns into learned weights). H8 solves the temporal correctness problem more cleanly — the training cutoff is explicit in fine-tuning, not a retrieval filter. Both should be explored; H8 may supersede H3 if it generalizes well.

**Topology variants**:

| Variant | Topology | Description |
|---------|----------|-------------|
| H3a | Single agent | Retrieved defect categories injected into HypothesisEngine context. Baseline. |
| H3b | T2 (Map-Reduce) | Retrieved categories define the specialist lanes. Instead of hardcoded "null-safety, concurrency, type-safety, lifecycle", the specialists are dynamically chosen from the top-K retrieved categories. Adapts specialist set to what actually occurs in this codebase. |

---

### H4 — Line-Level Evidence Forcing (changed-line anchoring)

**Mechanism**: Require the model to cite the specific **added (+) or removed (-)** diff line that enables the failure path for each mechanism. SPECULATIVE is the only valid tier for a mechanism without a supporting changed line.

Current state: `evidence_tagger` verifies that `evidence_quote ⊆ diff` at token level. This permits quoting unchanged context lines.

Proposed: Require that at least one `evidence_quote` contains a `+` or `-` prefixed line — an actual change, not just surrounding context.

**Why this works**: Abstraction bias causes the model to reason from the shape of the code rather than from what changed. Forcing a +/- line citation requires identifying the specific changed line that introduces the risk — making abstract pattern-matching insufficient.

**Literature support**: FPA defense (force local reasoning over template retrieval), SBFL (spectrum-based fault localization grounds reasoning in covered/changed lines)

**Cost**: Zero additional LLM calls. Schema + evidence_tagger logic change.

**Risk**: May over-restrict. Some mechanisms are legitimately grounded in unchanged lines that interact with the changed line. Mitigation: require +/- citation for PRIMARY mechanism only; secondary mechanisms can use context quotes.

**Role in topologies — H4 is a cross-cutting criterion, not a standalone topology**:

| Topology | H4's role |
|----------|-----------|
| T1 (Debate) | Scoring criterion for the judge: mechanism with more +/- line evidence wins. Applied to both Agent A and Agent B. |
| T2 (Map-Reduce) | Reducer selection criterion: specialist whose evidence_quote contains a +/- line beats one quoting context. Deterministic, no LLM needed. |
| T3 (Evaluator-Optimizer) | The evaluation criterion the MechanismEvaluator checks. If no +/- line cited → challenge; if +/- line cited → PASS. Deterministic. |
| T4 (Diversity) | Reducer selection criterion, same as T2. |

H4 alone (no topology change) is the cheapest intervention. H4 inside T3 is the recommended pairing — T3 forces a re-run when H4 fails, rather than just flagging.

---

### H5 — Reasoning Model on Hard Panel

**Mechanism**: For commits that remain D3=0 after H1+H4, escalate to an extended-thinking or reasoning model (Claude extended thinking, o3).

**Why this works**: The anchoring bias study (arxiv 2505.15392) shows reasoning models achieve 22-35% anchoring rate vs 45-61% for standard chat models. The explicit chain-of-thought dilutes the anchoring effect because the model writes out the reasoning steps and has opportunity to self-correct.

**Literature support**: Anchoring bias mitigation study (EMNLP), CrashFixer self-reflection

**Cost**: 5-10x per commit. Applied only to the 11 tier-1 hard panel commits = ~$0.15-0.30 total for validation.

**Risk**: Extended thinking may still be anchored on the same pattern — just more verbosely. The FPA paper shows abstraction bias persists through explicit warnings; extended thinking may not break it either.

**Topology variants**:

| Variant | Topology | Description |
|---------|----------|-------------|
| H5a | Single agent (residual escalation) | Reasoning model runs on commits that failed H1+H4+T3. Clean separation: cheap path first, expensive path only for irreducibles. |
| H5b | T2 (Map-Reduce, final tier) | Reasoning model replaces Sonnet in the T2 reducer role on tier-1 commits. Instead of a script reducer, the reasoning model evaluates specialist outputs with full chain-of-thought. Higher cost, potentially better at breaking ties between specialists. |

**Recommended variant**: H5a. Use as last resort after all cheaper options are exhausted, not as a first-line escalation.

---

### H6 — Fix-Conditioned Reverse Analysis

**Mechanism**: Show the fix diff (unlabeled) to a second LLM pass and ask "what problem does this patch solve?" Compare the reverse-engineered mechanism to the forward-analysis mechanism from the investigator.

**Why this works**: Fix diffs are often more semantically specific than bug diffs. A fix that adds a null-check `if (host != null)` is unambiguous about the mechanism in a way the bug diff (removal of the null-check) might not be.

**Cost**: 1 additional LLM call per commit. Can be run offline (post-hoc analysis, not production path).

**Limitation**: Requires fix commit from ground truth — only available in eval/training context, not production. This is a training/eval insight mechanism, not a production feature.

**Use case**: Run on the hard panel to understand which mechanisms are "derivable from the fix" vs "fundamentally ambiguous." Informs which commits are theoretically solvable vs require different context entirely.

**Topology**: Single agent, eval-only. No topology variants — this is not a production path.

---

### H7 — Multi-Sample Voting

**Explicitly NOT recommended.**

The EMNLP 2025 self-consistent error paper shows that majority voting over multiple samples falls below random guessing for systematic errors. Wrong-mechanism diagnoses observed in our data are likely self-consistent (same model, same wrong answer every run). Sampling N=5 provides no lift and wastes 5x the cost.

---

### H8 — JIT-SDP Model as Defect Signature Anchor

**Mechanism**: Fine-tune a compact encoder model (CodeT5+, UniXCoder) on the ApacheJIT dataset with a temporal cutoff. At investigation time, run the diff through the model to produce a `defect_signature` — a predicted defect category distribution — before the LLM hypothesis generation.

```
diff → [CodeT5+ fine-tuned on ApacheJIT, cutoff T] → "defect_signature: {null-reference: 0.73, lifecycle: 0.18, type: 0.09}"
                                                              ↓ injected into HypothesisEngine prompt
                                      "Prior analysis suggests null-reference pattern (0.73 confidence)"
```

**Why this works**: Compresses knowledge from 28K labeled commit-bug pairs into model weights. Unlike RAG (H3), there is no retrieval step that could surface contaminated results — the temporal cutoff is baked into training. Unlike a general LLM, the model has seen the actual distribution of defects in this specific codebase.

**Why it's different from the XGBoost router**: The router predicts buggy/clean (D1). This model predicts *defect mechanism category* (D3). Different output, different training signal.

**Temporal correctness**: Fine-tune with only pairs where `fix_commit.date < T_train`. At inference time, zero access to future commits. Clean by construction.

**Cost**: One-time fine-tune of a ~125M parameter model on 28K examples (~$5-10 GPU hours). Inference: <10ms per commit (CPU). Zero marginal LLM cost.

**Risk**: Defect category taxonomy must be defined before fine-tuning. Too coarse (5 categories) → low discriminative power. Too fine-grained → data sparsity per category.

**Literature support**: CodeFlowLM (arxiv 2512.00231) demonstrates encoder models fine-tuned on JIT-SDP tasks in exactly this setting.

**Topology variants**:

| Variant | Topology | Description |
|---------|----------|-------------|
| H8a | Single agent (prior injection) | defect_signature injected as a soft prior into the HypothesisEngine prompt. Cheapest — zero additional LLM calls, only the encoder inference. |
| H8b | T2 (Map-Reduce) | defect_signature defines which specialist lanes to activate. Only the top-2 predicted categories get specialist agents, reducing T2 from 4-5 calls to 2. H8 as a T2 router. |
| H8c | T1 (Debate) | defect_signature anchors Agent A's initial position. Agent B is given the diversity constraint "argue against the JIT-SDP prior." Forces the debate to be between the data-driven mechanism and the model's first-instinct mechanism. |

**Recommended starting point**: H8a. Lowest cost, direct improvement to the prior. Evaluate before committing to T2 or T1 integration.

---

## 5. Interaction Effects and Dependencies

These hypotheses are not independent:

- **H1 + H4 are synergistic**: Symptom-first reasoning generates a symptom anchor; line-level forcing then requires the specific changed line that enables that symptom. Together they close the abstraction-bias loop from both ends.

- **T3 wraps H4**: T3 is a topology that applies H4 as the evaluation criterion. They are complementary — H4 defines *what* to check; T3 defines *when* to check and how to force a revision cycle.

- **T1 requires H4 on both agents**: Without H4, both debate agents may generate plausible-but-ungrounded mechanisms. H4 applies as the judge's scoring criterion for evidence quality. H2b (code model verifier) strengthens T1 by ensuring Agent B has independent biases.

- **T2 is the expensive version of H1**: H1 asks one agent to consider multiple mechanisms sequentially; T2 runs specialized agents in parallel. T2 only if Layer 1 (H1+H4+T3) is exhausted.

- **H3 conflicts with H1 if poorly implemented**: A bad retrieval result could anchor the model on the wrong historical mechanism. H3 needs H4 as a guard — the retrieved mechanism must be grounded in the actual diff lines.

- **H8 and H3 are alternative paths to the same prior**: H8 compresses patterns into weights (cleaner temporally, higher setup cost). H3 retrieves similar examples (no setup, higher contamination risk). H8b (H8 as T2 router) combines both: H8 defines which specialists to activate, reducing the retrieval surface area.

- **H2 is a safety net**: If H1+H4+T3 still produce wrong mechanisms, H2 detects the disagreement signal and can trigger T1 (Debate) or H5 escalation.

- **H6 is evaluation infrastructure, not production**: Run on the hard panel to understand theoretical ceiling and diagnose which commits are solvable before investing in Layers 3-4.

---

## 6. Proposed Experiment Sequence

Incremental layers — each step validated on the 18-commit hard panel (11 tier-1 + 7 tier-2) before running n=50. Ordered by ROI: cheapest structural changes first, expensive topology changes last.

```
v2-spike-wrong-mechanism  ← this doc
    ↓
Layer 1 — Zero extra LLM calls (prompt + schema only)
    v2-d3-h1h4-t3         H1-T3 (symptom-first) + H4 (line-level forcing) + T3 (mechanism evaluator loop)
                          Hard panel n=18, ~$0.04
    ↓ if D3 < 0.30 on hard panel
Layer 2 — Domain knowledge injection (~0 marginal LLM cost)
    v2-d3-h8a             H8a: JIT-SDP model prior injection (encoder inference only)
                          Hard panel n=18, ~$0.04 + one-time fine-tune
    v2-d3-h3a-bug-rag     H3a: Historical bug RAG if H8a insufficient or not yet trained
                          Hard panel n=18, ~$0.06
    ↓ if D3 < 0.30 on hard panel
Layer 3 — Topology change (2-3x cost)
    v2-d3-t1-h2b-debate   T1 Debate with H2b (code model as Agent B): 2 agents argue mechanism, judge picks
                          Hard panel n=18, ~$0.12
    ↓ if D3 < 0.30 on tier-1 (n=11) only
Layer 4 — Escalation (5-10x cost, hard subset only)
    v2-d3-t2-h8b          T2 Map-Reduce with H8b (H8 as specialist router): 2-3 specialists + script reducer
                          Hard panel tier-1 only (n=11), ~$0.08
    v2-d3-h5a-reasoning   H5a: Reasoning model on truly irreducible commits (n≤5), ~$0.15
    ↓
v2-d3-n20-validate        Full n=20 iteration gate with best combination from above
    ↓
v2-n50-delivery           Delivery gate
```

**H6** (fix-conditioned reverse analysis) runs in parallel as eval infrastructure — offline, no production LLM cost. Informs which commits are theoretically solvable before investing in Layers 3-4.

---

## 7. Open Questions

1. **Are the wrong-mechanism commits self-consistent?** Run the same 11 tier-1 commits twice with different seeds. If the mechanism is identical both times, confirms self-consistent error → H2 is mandatory.

2. **Does H3 help or hurt?** The risk of RAG poisoning (wrong historical match → anchored on wrong mechanism) needs a controlled experiment. Test on hard panel n=18 first, before any n=50 integration.

3. **Can extended thinking break FPA?** The FPA paper shows robustness even under explicit warning. Does extended thinking (which forces explicit reasoning chains) break the pattern shortcut or just elaborate on it more verbosely?

4. **Is the mechanism ceiling at Sonnet?** Some commits may require architectural knowledge that Sonnet simply doesn't have about Apache Camel internals. H5 (stronger model) and H8 (codebase-specific prior) address this from different angles.

5. **Does T3 (Evaluator-Optimizer) converge or loop?** If the mechanism evaluator challenges the model and the model produces the same wrong mechanism again (more elaborately), max_rounds=2 breaks the loop — but we lose the commit. Need to measure: does the re-challenge produce a better mechanism or just a more verbose version of the same wrong one?

6. **Does T1 (Debate) require model diversity to avoid correlated biases?** Two instances of Sonnet will have correlated biases — Agent B may not genuinely argue an alternative. H2b (code model as Agent B) directly addresses this. Needs empirical comparison: T1-Sonnet-Sonnet vs T1-Sonnet-H2b.

7. **Temporal correctness in H3**: The RAG corpus must use only closed pairs where `fix_commit.date < T`. Retrieval must explicitly filter out: (a) the fix commit of the current bug, (b) any JIRA descriptions associated with the fix, (c) partial pairs where fix is in the future.

8. **What defect taxonomy for H8?** The JIT-SDP model needs a label schema. Too coarse (5 categories) → low discriminative power. Too fine-grained (50 categories) → data sparsity per category on 28K pairs. Taxonomy design is a prerequisite for H8 fine-tuning.

---

## 8. References

| Source | Year | Key Claim | Relevance |
|--------|------|-----------|-----------|
| Bernstein et al. — *Trust Me, I Know This Function* | NDSS 2026 | Abstraction bias: models skip local reasoning on familiar patterns; persists through explicit warning | Core diagnosis mechanism |
| Tan et al. — *Too Consistent to Detect* | EMNLP 2025 | Self-consistent errors don't decrease with scale; voting falls below random; cross-model probe works | Explains why voting won't help; motivates H2 |
| Cloud RCA study — *Stalled, Biased, and Confused* | arxiv 2601 | RF-13 anchoring = 45% lower correct prediction rate | Quantifies cost of anchoring in RCA tasks |
| anchoring bias study — *Understanding the Anchoring Effect* | arxiv 2505 | Reasoning models: 22-35% anchoring vs 45-61% standard; dilution via contextual signals | Motivates H5, partial evidence for H1 |
| REFLECT — *Intervention-Supported Error Attribution* | arxiv 2606 | Inject fix, replay, verify outcome flip = contrastive evidence | Motivates H6 |
| Linux kernel fault diagnosis | arxiv 2505 | Mail-augmented hypothesis (RAG on bug history) improves localization | Motivates H3 |
| LLM4FL | openreview z91EvZbSI1 | Method ordering affects FL accuracy 22-36%; propose fix → re-rank | Motivates H4 + fix-conditioned reasoning |
| Claude Code field report | anthropics/claude-code #61932 | Completion bias, recognition-without-arrest, systematic overconfidence | Explains why context anxiety is real and named |
| CodeFlowLM — *Incremental JIT Defect Prediction* | arxiv 2512.00231 | CodeT5+/UniXCoder fine-tuned on JIT-SDP; false positives from conservative bias + lack of context | Direct precedent for H8 |
| Qwen2.5-Coder Technical Report | arxiv 2409.12186 | Sonnet/GPT-4o still outperform code models on general benchmarks; code models excel at local syntax | Justifies H2b/T2-specialist role (verifier, not investigator) |
| Cloud RCA — *Why Do AI Agents Systematically Fail* | arxiv 2602.09937 | Gemini 2.5 Pro: 12.5% perfect RCA; problem is hard across all model families | Contextualizes ceiling; model swap alone won't solve it |
