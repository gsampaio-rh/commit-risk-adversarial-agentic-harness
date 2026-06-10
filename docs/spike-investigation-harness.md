# Spike: Optimal Investigation Harness Design

**Task:** spike-0 — Research spike for D1/D2/D3 improvement  
**Date:** 2026-06-10  
**Runs analyzed:** `2026-06-10_11-39-59_real_n100` (leaked), `2026-06-10_13-17-45_real_n5` (clean)  
**Constraint:** No code changes (AC-7)

---

## Executive Summary

The V1 agent produces well-grounded evidence (D6=0.85) but fails three gates after oracle isolation: D1=0.40 (classification), D2=0.08 (localization), D3=0.13 (diagnosis). Root cause is not capability — the agent describes diffs accurately — but **calibration**: it hedges toward MEDIUM on buggy commits and describes *what changed* instead of *what will break*.

The n=100 run (D1=0.86) was inflated by `buggy=True` in numeric features; 66/100 investigations cite the label as primary evidence. Removing the label drops D1 to 0.40 on the same commits (n=5 cross-run proof: f897d46 and 90846b5 flip HIGH→MEDIUM with identical reasoning quality).

**Recommendation for iter-1:** Architecture A+B hybrid — explicit risk rubric with mechanism-based floor rules, plus staged chain-of-thought inside the JSON `reasoning` field. Inject `router_probability` as an ML prior (not ground truth). Add two few-shot examples (one mechanistic HIGH, one justified LOW). Defer Architecture C (adversarial self-critique) to iter-3 multi-turn.

**Predicted iter-1 targets:** D1≥0.60, D3≥0.18, D6≥0.70 (hard constraint).

---

## 1. Data Analysis Findings

### 1.1 Leakage Artifact (n=100) — Discount Classification, Keep Patterns

The n=100 run at `output/runs/2026-06-10_11-39-59_real_n100/` used a blocklist that failed to exclude `buggy` from `csv_features`. Impact:

| Signal | n=100 (leaked) | n=5 (clean) |
|--------|----------------|-------------|
| D1 | 0.86 | 0.40 |
| D3 | 0.28 | 0.13 |
| Investigations citing `buggy` in reasoning | 66/100 | 0/5 |

**Pattern P1 — Label as classification driver:** Buggy commits routinely open with "buggy=True in numeric features" as finding #1. Example (6bb9f555, HIGH):

> "The ground-truth label buggy=True confirms this commit is associated with a defect."

Without the label, the agent still identifies the URL construction bug mechanistically — but on other commits it downgrades to MEDIUM despite strong reasoning.

**EC-1 handling:** All n=100 classification outcomes driven by `buggy=True` citations are **discounted**. Structural patterns (hedging language, localization behavior, D3 quality spectrum) remain valid.

### 1.2 Post-Fix Behavior — Cross-Run Proof

Five commits appear in both runs. Label removal changes classification but not reasoning depth:

| Commit | Buggy | n100 Risk | n5 Risk | n100 D3 | n5 D3 |
|--------|-------|-----------|---------|---------|-------|
| f897d46 | yes | HIGH | **MEDIUM** | 0.0 | 0.0 |
| 90846b5 | yes | HIGH | **MEDIUM** | 0.25 | 0.25 |
| b9f1653 | no | HIGH | HIGH | — | — |
| 164a063e | no | LOW | LOW | — | — |
| 0a3aa40f | no | LOW | LOW | — | — |

**Key insight:** f897d46 and 90846b5 retain identical D3 scores and nearly identical reasoning text. Only `risk_level` changes. The agent *can* reason; it *won't commit* to HIGH without the oracle label or an explicit rubric push.

### 1.3 D1 Hit/Miss Matrix

**n=100 (leaked — classification inflated):**

| Stratum | HIGH | MEDIUM | LOW |
|---------|------|--------|-----|
| Buggy (50) | 43 | 6 | 1 |
| Clean (50) | 7 | 24 | 19 |

**n=5 (clean — true baseline):**

| Stratum | HIGH | MEDIUM | LOW |
|---------|------|--------|-----|
| Buggy (2) | 0 | 2 | 0 |
| Clean (3) | 1 | 0 | 2 |

**When agent classifies HIGH without `buggy` label (clean n=5 + n100 clean-FP analysis):**

Triggers that produce HIGH even without label:
1. **Concrete failure mechanism in diff** — b9f1653: Jetty version crosses 9.3.15 binary-incompatibility boundary explicitly guarded by code comment
2. **Obvious bug pattern** — 267f065: reversed `isAssignableFrom` arguments
3. **Security exposure** — 06e80f1: OAuth credentials in plaintext
4. **Mechanistic URL/logic error** — 6bb9f555: missing dot in URL concatenation (D3=1.0) — *note: in leaked run, label also cited*

**When agent misses D1 (buggy → MEDIUM):**

Six buggy commits scored MEDIUM in n=100. Post-fix, both n=5 buggy commits are MEDIUM:

| Commit | Conf | D3 | Hedging | Why MEDIUM |
|--------|------|-----|---------|------------|
| f897d46 (n5) | 0.78 | 0.0 | "purely additive", "blast radius limited" | Large new file seen as additive risk, not defect |
| 90846b5 (n5) | 0.72 | 0.25 | "backward-compatible in intent" | API change + truncated diff → uncertainty hedge |
| b4c933b7f958 | 0.78 | **1.0** | none | Agent describes fix commit as "architecturally sound" — treats bug-fix as low-risk |
| 572f3cee35fe | 0.72 | 0.0 | "cosmetic", "code-style" | Surface appearance masks deeper defect |
| 2213f71944ae | 0.78 | 0.5 | "structurally sound" | Refactor framed as improvement |

**Critical finding:** b4c933b7f958 has D3=1.0 (perfect diagnosis) but D1=0.0 (MEDIUM on buggy). **D1 and D3 are independent.** Rubric must set classification floor based on mechanism identification, not just reasoning quality.

### 1.4 D3 Quality Spectrum

D3 histogram (n=100, buggy commits with JIRA): 21 at 0.0, 14 at 0.25, 8 at 0.5, 2 at 0.75, 5 at 1.0.

**D3=1.0 exemplar (6bb9f555, HIGH):** Names trigger, mechanism, consequence:

> "If getAmazonAWSHost() returns a value without a leading dot (e.g., 'amazonaws.com' rather than '.amazonaws.com'), the resulting URL will be malformed (e.g., 'https://sqs.us-east-1amazonaws.com/')."

Format: `If <condition> then <failure> in <location>` — matches judge rubric level 4.

**D3=0.0 exemplar (f897d46, n5 MEDIUM):** Describes structure, not failure:

> "complex code-generation logic using the Roaster/JDT library, which could contain subtle bugs in AST/template generation"

Missing: connector-only properties silently excluded from generation (actual root cause). Agent says "could contain bugs" — generic, not mechanistic.

**D3=0.0 with HIGH risk (409664, leaked):** Agent finds a real bug (variable mismatch in TriggerKey) but JIRA had no description — judge scored 0.0. Infrastructure issue, not prompt issue.

**D3 distinction rule for iter-1:**

| Quality | Pattern | Judge Score |
|---------|---------|-------------|
| Generic | "could contain bugs", "complex logic", "subtle failure modes" | 0–1 |
| Area-only | "classloader isolation", "registry lookup" without trigger | 1–2 |
| Partial mechanism | Identifies wrong API usage but misses root cause | 2 |
| Full mechanism | "If X then Y breaks" with diff evidence | 3–4 |

### 1.5 Hedging Language Catalog

Automated scan of reasoning text (n=100 + n=5):

| Phrase | n=100 count | n=5 count | Effect |
|--------|-------------|-----------|--------|
| "blast radius" | 16 | 1 | Downgrade to MEDIUM |
| "minimal" | 10 | 1 | Downgrade |
| "intent" / "backward-compatible in intent" | 7 | 1 | Downgrade |
| "limited" | 5 | 1 | Downgrade |
| "additive" / "purely additive" | 4 | 1 | Downgrade |
| "does not alter" | 0 | 1 | Downgrade |

Hedging appears in 80%+ of buggy→MEDIUM commits. It does **not** appear in HIGH commits with D3≥0.75 (except 7d938d2 which has "intent" but still scored D3=1.0).

**iter-1 rule:** Prompt must explicitly forbid downgrade phrases when a concrete failure mechanism is identified.

### 1.6 D2 Localization Failure Mode

D2 uses Jaccard(agent localization files, fix-commit files). Agent systematically lists **all touched files** rather than **defect locus**.

Example (f897d46, n5): Agent localized 4 files (ConnectorMojo, SpringBootAutoConfigurationMojo, StringHelper, pom.xml). Fix only touched SpringBootAutoConfigurationMojo. Jaccard=0.25.

Example (90846b5, n5): Agent localized 4 files across reactive-streams module. Actual bug was Spring Boot circular dependency in auto-configuration — different file entirely. Jaccard=0.08 aggregate.

**Pattern:** Agent equates "files I analyzed" with "files containing the defect." D2 improvement deferred to iter-4 but may partially improve when D3 forces mechanism-level focus (narrows file set).

### 1.7 Clean HIGH Triggers (Few-Shot Candidates)

Valid HIGH on clean commits (false positives for D1, but correct risk assessment):

- **b9f1653:** Dependency version crossing known incompatibility boundary — cite specific version numbers and guarded comment
- **267f065:** Logic inversion bug (`isAssignableFrom` reversed) — cite exact line and wrong behavior

Valid LOW on clean commits (negative few-shot):

- **164a063e:** Test-only assertEquals argument order fix — single file, no logic change
- **0a3aa40fe787:** Standard Iterator.remove() CME fix — well-known pattern, mechanically correct

---

## 2. External Research

### 2.1 Academic — JIT-SDP (Just-In-Time Defect Prediction)

**Source:** Kamei et al., "A Large-Scale Empirical Study of Just-In-Time Quality Assurance," IEEE TSE 2013.

**Approach:** Predict defect-inducing *changes* (not modules) using 14 change metrics in 5 categories: Size (la, ld, nf), Diffusion (nd, ns), History (nuc, age), Experience (aexp, arexp, asexp), Entropy (ent). Inspecting 20% of changes catches 35% of defects.

**Relevant to D1/D3:** Our numeric features (la, ld, nf, nd, ns, ent, ndev, nuc, age, aexp, arexp) are the same family. The XGBoost router (AUC=0.85) implements JIT-SDP at routing time. The agent sees these features but not the model score.

**Transferable pattern:** Frame router_probability as "ML prior from change metrics" — same role JIT-SDP models play. Papers emphasize that metrics alone are insufficient; human inspection of the *change content* is required. This validates our two-stage design (router → agent).

**Not applicable:** JIT-SDP papers don't address natural-language root-cause reasoning (D3). They predict binary defect/no-defect, not failure mechanisms.

### 2.2 Industry — Code Review Agents

#### CodeRabbit

**Source:** [CodeRabbit docs — review types and severity levels](https://docs.coderabbit.ai/guides/code-review-overview)

**Approach:** Three review types (potential issue, refactor, nitpick) × five severity levels (Critical, Major, Minor, Trivial, Info). Path-specific instructions and configurable review profiles (chill vs assertive).

**Transferable pattern:**
- Explicit severity taxonomy with definitions (maps to our risk_level rubric)
- "Potential issue" type requires bug pattern, not just style (maps to D3 mechanism requirement)
- Path-specific instructions (future: per-project rules in `orchestrator.py`)

#### Greptile v3

**Source:** [Greptile v3 agentic code review blog](https://www.greptile.com/blog/greptile-v3-agentic-code-review)

**Approach:** Agentic loop with codebase graph search. Multi-hop reasoning beyond the diff. Self-challenges hypotheses before posting — higher precision, fewer low-confidence comments.

**Transferable pattern:**
- Multi-hop investigation (validates iter-3 multi-turn fallback)
- "Increased threshold for sureness" via self-challenge (validates Architecture C, but expensive)
- Graph-based context (we lack this — our agent sees only diff + touched files)

**Not applicable:** Greptile has full repo graph; we intentionally limit context to oracle-safe bundles. Graph expansion is infrastructure, not iter-1.

#### SWE-bench / SWE-agent

**Approach:** Multi-turn investigate → locate → fix pipeline with tool use.

**Transferable pattern:** Separate investigation from classification. Our agent combines both in one JSON response; staged CoT (Architecture B) is the lightweight version.

### 2.3 Static Analysis — Finding Structure

**Source:** SpotBugs ([bug descriptions](https://spotbugs.readthedocs.io/en/stable/bugDescriptions.html))

**Approach:** 400+ bug patterns categorized (Correctness, Dodgy Code, Security, etc.). Each finding: **location → bug pattern type → consequence → confidence rank**.

Example pattern name: `NP_NULL_ON_SOME_PATH` — "Possible null pointer dereference on branch that might be null."

**Transferable pattern:** Force findings to follow `[WHERE] + [BUG_PATTERN] + [CONSEQUENCE]`:

```
FINDING: SqsEndpoint.java:127-130 | STRING_CONCAT_MISSING_SEPARATOR |
  If amazonAWSHost lacks leading dot, URL is malformed → cross-account SQS calls fail
```

This maps directly to D3 judge rubric levels 3–4 and improves D2 by narrowing localization to the defect site.

**llm-skeptic note:** SpotBugs uses bytecode analysis — deterministic, no LLM. We cannot replicate 400 patterns, but we can replicate the **output structure** in the prompt.

### 2.4 Prompt Engineering Literature

| Source | Technique | Application |
|--------|-----------|-------------|
| Wei et al. 2022, [Chain-of-Thought](https://arxiv.org/abs/2201.11903) | Intermediate reasoning steps before answer | Architecture B — staged reasoning in `reasoning` field |
| G-Eval ([guide](https://www.confident-ai.com/blog/g-eval-the-definitive-guide)) | CoT + rubric for LLM-as-judge | Our D3 judge already uses this; agent should mirror the judge's expected format |
| Rubric-based evaluation | Explicit criteria per score level | Architecture A — risk_level rubric |

**Key synthesis:** Our D3 judge rewards mechanistic reasoning (levels 3–4). The agent prompt never mentions failure mechanisms, triggers, or "If X then Y." The judge and agent are misaligned — fixing this is the highest-leverage D3 intervention.

### 2.5 Analogy Mapping Table (EC-2)

No direct "commit risk investigation agent" precedent exists. Valid analogies:

| External Domain | Maps To | Dimension |
|-----------------|---------|-----------|
| JIT-SDP (Kamei 2013) | XGBoost router + numeric features | D1 prior |
| CodeRabbit severity levels | risk_level rubric | D1 |
| SpotBugs finding format | findings[] structure | D3, D2 |
| Greptile v3 self-challenge | Architecture C / iter-3 | D1, D3 |
| G-Eval CoT judge | Staged reasoning (Architecture B) | D3 |

---

## 3. Candidate Prompt Architectures

### 3.1 Architecture A: Rubric-Based Classification

**Core idea:** Explicit decision criteria per risk level. Classification must cite which rubric tier fired. Mechanism identified → floor at HIGH.

**Prompt template sketch:**

```
RISK CLASSIFICATION RUBRIC — apply in order:

CRITICAL: Credential exposure, injection vulnerability, data loss, or
  production outage likely in normal usage paths.

HIGH: At least one of:
  (a) A specific failure mechanism is visible in the diff: "If <condition> then <failure>"
  (b) API/binary incompatibility introduced (removed generics, changed signatures)
  (c) router_probability ≥ 0.70
  (d) Security-relevant change without validation

MEDIUM: Plausible risk indicators exist (large diff, complex logic, many files)
  BUT no specific failure mechanism identified. Uncertainty about impact.

LOW: Docs-only, test-only, formatting, or no defect mechanism identifiable.

RULES:
- Identifying a concrete failure mechanism → risk_level MUST be HIGH or CRITICAL.
- Do NOT assign MEDIUM solely because a change is "additive" or has "limited blast radius."
- router_probability is an ML prior (0.0–1.0), NOT ground truth. Use as one signal.
- State rubric tier in first sentence: "Rubric: HIGH because <criterion>."
```

**Expected impact:**

| Dim | Δ | Confidence |
|-----|---|------------|
| D1 | +0.20–0.30 | High — directly attacks MEDIUM-default |
| D3 | +0.02–0.05 | Low — indirect via mechanism naming requirement |
| D6 | −0.05 risk | Medium — may over-claim without evidence |

**Cost:** +~200 tokens system prompt, 1 turn. ~$0/commit unchanged.

**Risks:** Rubric gaming (cite tier without diff evidence); router_probability becomes soft oracle (mitigated: same info as numeric features, not buggy label).

---

### 3.2 Architecture B: Staged Chain-of-Thought

**Core idea:** Four mandatory stages inside the `reasoning` field before `risk_level` is set.

**Prompt template sketch:**

```
Structure your reasoning field in four stages:

STAGE 1 — CHANGE SUMMARY: What changed, which files, stated intent.

STAGE 2 — DEFECT HYPOTHESES: List 2–3 specific failure modes this change
  COULD introduce. Format each as:
  "HYPOTHESIS: If <condition> then <failure> in <file>:<area>"

STAGE 3 — EVIDENCE: For each hypothesis, cite diff lines for/against.
  Mark each: SUPPORTED / REFUTED / UNVERIFIABLE (e.g., truncated diff).

STAGE 4 — VERDICT: risk_level based on strongest SUPPORTED hypothesis.
  Any SUPPORTED hypothesis with diff evidence → HIGH minimum.

Your findings[] list must contain only SUPPORTED hypotheses.
```

**Expected impact:**

| Dim | Δ | Confidence |
|-----|---|------------|
| D1 | +0.10–0.20 | Medium — forces engagement with failure modes |
| D3 | +0.08–0.15 | High — "If X then Y" matches judge rubric |
| D6 | neutral | Stages require citations |

**Cost:** +~300 tokens output (longer reasoning). ~+15% per commit.

**Risks:** Verbose boilerplate stages without real hypotheses; 4K diff truncation leaves hypotheses UNVERIFIABLE (90846b5 problem).

---

### 3.3 Architecture C: Adversarial Self-Critique

**Core idea:** Agent produces initial assessment, argues against it, then final classification.

**Prompt template sketch:**

```
Include these additional JSON fields:
- initial_risk_level: first assessment
- counter_evidence: 3 bullets arguing this is MORE risky than initial (cite diff)
- mitigating_evidence: 3 bullets arguing LESS risky (cite diff)
- risk_level: final after weighing both sides
- reasoning: synthesis

If counter_evidence contains a SUPPORTED failure mechanism, risk_level ≥ HIGH.
```

**Expected impact:**

| Dim | Δ | Confidence |
|-----|---|------------|
| D1 | +0.15–0.25 | High — counter-evidence surfaces missed signals |
| D3 | +0.05–0.10 | Medium |
| D6 | neutral | Both sides must cite diff |

**Cost:** +~40% output tokens. Highest single-turn cost.

**Risks:** Weak devil's advocate; schema change requires `report.py` + tests (violates iter-1 "prompt only" scope). Better as iter-3 multi-turn.

---

### 3.4 Comparison Matrix

| Criterion | A: Rubric | B: Staged CoT | C: Self-Critique |
|-----------|-----------|---------------|------------------|
| D1 lift | **High** | Medium | High |
| D3 lift | Low | **High** | Medium |
| D6 regression risk | Medium | Low | Low |
| Token cost | Low | Medium | High |
| Schema change | No | No | **Yes** |
| iter-1 fit | **Yes** | **Yes** | No (defer iter-3) |
| Implementation | Prompt only | Prompt only | Prompt + code |

---

## 4. Evaluator Critique Summary

### Architecture A — Rubric

| Axis | Score | Notes |
|------|-------|-------|
| D1 lift | 4/5 | Directly fixes MEDIUM-default. Risk: 7 clean→HIGH FPs in n=100 may increase. |
| D3 lift | 2/5 | "Mechanism → HIGH" helps but doesn't force hypothesis generation. |
| D6 safety | 3/5 | "Rubric: HIGH because..." without diff cite = gaming risk. Add "must cite diff line." |
| Oracle safety | 4/5 | router_probability is not buggy label. Monitor for P>0.7 → always HIGH parroting. |
| Cost | 5/5 | Minimal token increase. |

**Helps:** f897d46 — "416-line untested code-gen" should trigger HIGH via criterion (b) complex logic + no tests.  
**Fails:** b4c933b7f958 — agent sees a *fix* and rates MEDIUM. Rubric needs: "If change is a bug-fix commit style but introduces new logic, still evaluate defect risk."

**Verdict: GO for iter-1.**

### Architecture B — Staged CoT

| Axis | Score | Notes |
|------|-------|-------|
| D1 lift | 3/5 | Stage 4 verdict should lift D1 when hypothesis supported. |
| D3 lift | 5/5 | Directly aligned with D3 judge rubric. Best D3 intervention. |
| D6 safety | 5/5 | Stages demand diff citations. |
| Oracle safety | 5/5 | No new data sources. |
| Cost | 4/5 | +15% output tokens acceptable. |

**Helps:** 90846b5 — Stage 2 would force "If circular dependency in auto-config then deadlock at startup."  
**Fails:** Truncated diff (22K chars) → Stage 3 marks everything UNVERIFIABLE → Stage 4 hedges MEDIUM.

**Verdict: GO for iter-1. Pair with diff truncation fix in iter-2 if needed.**

### Architecture C — Self-Critique

| Axis | Score | Notes |
|------|-------|-------|
| D1 lift | 4/5 | Counter-evidence pass is effective but expensive. |
| D3 lift | 3/5 | Helps but less targeted than staged hypotheses. |
| D6 safety | 4/5 | Good if both sides cite diff. |
| Oracle safety | 5/5 | No new leakage. |
| Cost | 2/5 | +40% tokens; schema change adds code scope. |

**Verdict: DEFER to iter-3 multi-turn.** Greptile v3 validates the pattern but requires agentic loop, not single-turn JSON bloat.

### llm-skeptic Check

> Could a regex/rubric template do 80% of this?

- D1 rubric criteria (a)–(d): Partially — regex can detect security keywords, version bumps, null-check removals. Cannot detect "connector-only properties excluded" (f897d46 root cause). **LLM justified for D3; rubric template justified for D1.**
- Staged CoT: No — hypothesis generation requires semantic understanding of diff.
- Recommendation: A+B hybrid is the right split. Don't add more LLM turns until A+B plateau.

---

## 5. Recommendation

### 5.1 Chosen Approach: A+B Hybrid

Combine rubric classification floor (A) with staged defect hypotheses (B) in a single system prompt. Inject router probability. Add two few-shot examples.

### 5.2 Complete Prompt Template (for `orchestrator.py`)

```text
You are a commit risk investigator. Analyze the provided commit context and
produce a risk assessment grounded in diff evidence.

## INVESTIGATION METHOD

Structure your "reasoning" field in four stages:

STAGE 1 — CHANGE SUMMARY: What changed, which files, stated intent.

STAGE 2 — DEFECT HYPOTHESES: List 2–3 specific failure modes this change
COULD introduce. Format each as:
  "HYPOTHESIS: If <condition> then <failure> in <file>:<area>"

STAGE 3 — EVIDENCE: For each hypothesis, cite diff evidence for/against.
  Mark each: SUPPORTED / REFUTED / UNVERIFIABLE.

STAGE 4 — VERDICT: State rubric tier and risk_level.

## RISK CLASSIFICATION RUBRIC

CRITICAL: Credential exposure, injection vulnerability, data loss, or
  production outage likely in normal usage paths.

HIGH: At least one of:
  (a) A SUPPORTED defect hypothesis with diff evidence
  (b) API/binary incompatibility (removed generics, changed public signatures)
  (c) ML risk prior router_probability ≥ 0.70
  (d) Security-relevant change without input validation

MEDIUM: Risk indicators exist but no SUPPORTED hypothesis. Uncertainty
  about impact. Truncated diff preventing verification.

LOW: Docs-only, test-only, formatting, or no defect mechanism identifiable.

RULES:
- A SUPPORTED hypothesis with diff evidence → risk_level MUST be ≥ HIGH.
- Do NOT assign MEDIUM because a change is "additive", has "limited blast
  radius", or is "backward-compatible in intent."
- router_probability is an ML prior (0.0–1.0), NOT ground truth.
- findings[] must list only SUPPORTED hypotheses with file paths.
- localization[] must list files where a SUPPORTED hypothesis points, NOT all
  touched files.

## EXAMPLE A — HIGH (mechanistic, do not copy text — reason from actual diff)

Commit: Upgrades library X across 15 files. Diff shows removal of null-guard
on type-conversion path in DefaultExchange.java.

reasoning: "STAGE 1: ... STAGE 2: HYPOTHESIS: If null property requested
as Boolean, removal of Boolean.class==type guard returns null instead of
false, causing NPE in DefaultExchange.java:142. STAGE 3: SUPPORTED — diff
removes guard at line 142. STAGE 4: Rubric HIGH, criterion (a)."
risk_level: HIGH

## EXAMPLE B — LOW (do not copy text — reason from actual diff)

Commit: Fixes assertEquals argument order in single test file.

reasoning: "STAGE 1: Single test file, argument order fix. STAGE 2: No
defect hypotheses — no production code changed. STAGE 3: N/A. STAGE 4:
Rubric LOW."
risk_level: LOW

IMPORTANT: Respond ONLY with a single JSON object (no markdown, no text
outside JSON). Required fields:
- risk_level: one of LOW, MEDIUM, HIGH, CRITICAL
- confidence: float 0.0 to 1.0
- reasoning: string with all four stages
- findings: list of strings (SUPPORTED hypotheses only)
- follow_up_needed: boolean
- localization: list of {file, lines, rationale} objects
- recommendations: list of {action, priority, rationale} objects
```

### 5.3 Context Injection Spec

Add to context assembly in `_build_initial_messages`, after numeric features:

```text
## ML Risk Prior
router_probability={p:.3f} (route={route})
Note: This is an ML model score from change metrics. It is a prior, not a
defect label. Use it as one input to the rubric, especially criterion (c).
```

Where `{p}` is `RoutingDecision.probability` and `{route}` is the route enum value. The orchestrator already has routing decision available at investigation time via the caller.

### 5.4 Few-Shot Strategy

Examples are embedded in the system prompt (§5.2) as abstract patterns, not real commit data. This avoids oracle leakage from few-shot labels. iter-1 smoke test must check 3 random reports for example-language parroting (EC-2 from iter-1 contract).

### 5.5 iter-1 Success Criteria

| Dimension | Gate | iter-1 Target | Hard Constraint |
|-----------|------|---------------|-----------------|
| D1 | 0.70 | ≥0.60 | — |
| D3 | 0.20 | ≥0.18 | — |
| D6 | 0.60 | ≥0.70 | **Revert if below 0.70** |

Validation: n=20 stratified smoke after n=5 regression check.

### 5.6 iter-3 Trigger Conditions

Enable multi-turn (Architecture C as real turn-2) if after n=20:
- D1 < 0.55, OR
- D3 < 0.18, OR
- >30% of buggy commits still MEDIUM with SUPPORTED hypotheses in reasoning

Turn 2 prompt: "Review your assessment. List 3 reasons this is MORE risky than you rated. List 3 reasons LESS risky. Final risk_level?"

---

## Appendix A: Commit Analysis Table (25 commits)

| Commit | Proj | Buggy | Risk (n100) | Risk (n5) | D1 | D3 | D6 | Leaked? | Key Pattern |
|--------|------|-------|-------------|-----------|-----|-----|-----|---------|-------------|
| f897d46 | camel | Y | HIGH | MEDIUM | 0/1 | 0.0 | 1.0 | No | Hedging → MEDIUM post-fix |
| 90846b5 | camel | Y | HIGH | MEDIUM | 0/1 | 0.25 | 1.0 | No | Truncated diff + hedge |
| b9f1653 | camel | N | HIGH | HIGH | 0 | — | 0.75 | No | Valid HIGH: version boundary |
| 164a063e | hadoop | N | LOW | LOW | 1 | — | 0.75 | No | Valid LOW: test-only |
| 0a3aa40f | hadoop | N | LOW | LOW | 1 | — | 0.75 | No | Valid LOW: CME fix |
| 6bb9f555 | camel | Y | HIGH | — | 1 | 1.0 | 1.0 | Yes | D3=1.0 mechanistic HIGH |
| 55dcbe80 | camel | Y | HIGH | — | 1 | 1.0 | 1.0 | Yes | Schema inference change |
| b4c933b7 | camel | Y | MEDIUM | — | 0 | 1.0 | 0.75 | No | D3=1.0 but D1=0.0 |
| 572f3cee | camel | Y | MEDIUM | — | 0 | 0.0 | 1.0 | No | Cosmetic appearance |
| 2213f719 | camel | Y | MEDIUM | — | 0 | 0.5 | 0.75 | No | "Structurally sound" |
| 2bb65c78 | camel | Y | MEDIUM | — | 0 | 0.0 | 0.75 | Yes | Generated artifacts |
| ad6a796f | camel | Y | MEDIUM | — | 0 | 0.0 | 1.0 | Yes | Checkstyle + buggy cite |
| 40966458 | camel | Y | HIGH | — | 1 | 0.0 | 0.75 | Yes | Variable mismatch found |
| 267f065f | camel | N | HIGH | — | 0 | — | 0.75 | No | Valid FP: reversed isAssignableFrom |
| 9530370f | camel | N | HIGH | — | 0 | — | 1.0 | No | API migration risk |
| 4a72341e | camel | N | HIGH | — | 0 | — | 1.0 | No | Null-guard removal |
| 7cff0990 | camel | N | HIGH | — | 0 | — | 1.0 | No | Behavior change |
| 06e80f18 | camel | N | HIGH | — | 0 | — | — | No | Credential exposure |
| 7d938d2d | camel | Y | HIGH | — | 1 | 1.0 | 1.0 | Yes | Perfect D1+D3+D6 |
| ccd30cd2 | camel | Y | HIGH | — | 1 | 0.75 | 0.75 | Yes | buggy=True cited |
| ffe1edf3 | camel | Y | MEDIUM | — | 0 | 0.5 | 0.75 | No | Feature addition |
| e0bb867c | hadoop | N | LOW | — | 1 | — | 1.0 | No | Message string only |
| ab607494 | camel | N | LOW | — | 1 | — | 0.75 | No | Additive component |
| 24d9de04 | camel | N | MEDIUM | — | 1 | — | 1.0 | No | Correct MEDIUM clean |
| efdf259d | camel | Y | HIGH | — | 1 | 0.0 | — | Yes | Dynamic classloading |

---

## Appendix B: Builder–Evaluator Dialogue

### Architecture A — Rubric

**Builder:** Proposes rubric with mechanism→HIGH floor and router_probability injection.

**Evaluator:** GO with modifications: (1) add "must cite diff line for SUPPORTED hypothesis," (2) add explicit ban on hedge phrases, (3) monitor clean-FP rate — 7/50 clean→HIGH in n=100 is already high; rubric may push higher. Recommend n=20 eval before declaring D1 victory.

**Builder rebuttal:** Accept all three. Add hedge ban to RULES. Clean-FP is acceptable if D1 on buggy stratum improves — D1 metric is symmetric.

**Verdict: GO.**

### Architecture B — Staged CoT

**Builder:** Proposes four-stage reasoning inside JSON field.

**Evaluator:** GO. Primary D3 intervention. Flag: 4K diff truncation will cause UNVERIFIABLE hypotheses — recommend logging truncation rate in eval metadata. Do not increase diff limit in iter-1 (oracle isolation scope).

**Verdict: GO. Pair with A.**

### Architecture C — Self-Critique

**Builder:** Proposes additional JSON fields for adversarial pass.

**Evaluator:** DEFER. Schema change violates iter-1 "prompt only" scope. Greptile v3 validates pattern but needs agentic loop. Implement as turn-2 in iter-3.

**Verdict: DEFER to iter-3.**

---

## Appendix C: Hedging Phrase Frequency

| Phrase | n=100 | n=5 | Associated outcome |
|--------|-------|-----|-------------------|
| blast radius | 16 | 1 | MEDIUM |
| minimal | 10 | 1 | MEDIUM |
| intent | 7 | 1 | MEDIUM |
| limited | 5 | 1 | MEDIUM |
| additive / purely additive | 4 | 1 | MEDIUM |
| backward-compatible | 2 | 1 | MEDIUM |
| does not alter | 0 | 1 | MEDIUM |

---

## References

1. Kamei, Y., et al. (2013). A Large-Scale Empirical Study of Just-In-Time Quality Assurance. IEEE TSE 39(6).
2. Wei, J., et al. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models. arXiv:2201.11903.
3. CodeRabbit. Review types and severity levels. https://docs.coderabbit.ai/guides/code-review-overview
4. Greptile. v3 Agentic AI Code Review. https://www.greptile.com/blog/greptile-v3-agentic-code-review
5. SpotBugs. Bug descriptions. https://spotbugs.readthedocs.io/en/stable/bugDescriptions.html
6. G-Eval: LLM-as-a-Judge with CoT. https://www.confident-ai.com/blog/g-eval-the-definitive-guide
