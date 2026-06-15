# Agent Patterns

Patterns for building long-running agentic systems. Organized from simple (single-agent, single-turn) to complex (multi-agent, multi-hour).

---

## Cognition Patterns (how one agent thinks per turn)

| Pattern | Mechanism | Use when | Watch out for |
|---------|-----------|----------|---------------|
| **Chain-of-Thought** | Reasoning tokens before answer, no tools | Interpretation, classification, policy choice — fully contained in context | Reasoning drift; no grounding in live data |
| **ReAct** | Interleaved think → act → observe loop | Multi-step tasks with tools/APIs where path is unknown | Action loops; context bloat from tool dumps |
| **Reflection** | Generate → critique → revise (same agent) | Medium-stakes drafts where latency is constrained | Shallow self-critique; sycophantic "fixes" — not a substitute for adversarial eval |
| **Tool Use** | Schema-bound calls to external capabilities (MCP, function calling) | Anything requiring fresh or authoritative data | Wrong tool selection; huge payloads in context |
| **Planning** | Explicit multi-step plan, then execute (or replan) | Long horizons, many dependent steps, human checkpoints | Plans that ignore execution feedback; over-planning simple tasks |

**Selection rule**: Task needs live data? → Tool Use. Steps predictable? → Planning. Steps unknown? → ReAct. No tools needed? → CoT. Quality gate before ship? → Adversarial evaluator (not Reflection).

---

## Orchestration Patterns (how to sequence work)

| Pattern | Mechanism | Use when | Watch out for |
|---------|-----------|----------|---------------|
| **Prompt Chaining** | Fixed sequence: output of call N feeds input of call N+1 | Tasks with known structure (extract → transform → validate) | Too rigid for exploratory work; breaks if step order depends on runtime data |
| **Routing** | Classify intent → dispatch to the right handler | Distinct task classes with specialized handlers | Misroute = confidently wrong answer; classifier must be cheap and accurate |
| **Parallelization** | Independent calls fire simultaneously, results merge | Independent subtasks with a merge step | Hidden dependencies produce inconsistent outputs; cost = sum of branches |
| **Sequential with Checkpoints** | Stages run in order, each saves output to disk | Pipelines where later stages depend on earlier ones, and recovery matters | Long total latency; cannot exploit independence between stages |
| **Map-Reduce** | Fan-out (one call per item) → merge results | Processing N independent items (articles, clauses, files) | Cost scales linearly; merge step can be complex |
| **Gate/Verify** | Stage output passes validation before next stage starts | When downstream quality depends on upstream correctness | Over-strict gates block progress; under-strict gates propagate errors |

### When to use what

```
Known fixed sequence?     → Prompt Chaining
Items are independent?    → Parallelization / Map-Reduce
Need recovery/resume?     → Sequential with Checkpoints
Quality-critical output?  → Gate/Verify before next stage
Multiple task types?      → Routing
```

---

## Multi-Agent Patterns (how agents collaborate)

| Pattern | Mechanism | Use when | Watch out for |
|---------|-----------|----------|---------------|
| **Orchestrator-Workers** | Central agent delegates subtasks to specialized workers | Complex tasks decomposable into independent subtasks | Orchestrator bottleneck; workers diverge without shared context |
| **Evaluator-Optimizer** | Generator produces, evaluator critiques, loop until pass | Quality-critical output requiring iterative refinement | Infinite loops; evaluator too lenient or too strict |
| **Adversarial Verification** | Builder implements, separate evaluator tests against contract | High-stakes work where self-evaluation is unreliable | Overhead of maintaining contracts; evaluator must have different context than builder |
| **Debate** | Two agents argue opposing positions, judge decides | Decisions with genuine trade-offs requiring explored alternatives | Expensive (3 agents); can devolve into rhetoric over substance |
| **Hierarchical** | Managers → team leads → workers, each with narrower scope | Very large tasks requiring coordination at multiple levels | Communication overhead; managers out of touch with implementation reality |

---

## Foundations Patterns (harness & long-running)

Patterns originally referenced from a FOUNDATIONS.md document (no longer maintained). Each entry below describes a pattern relevant to harness and long-running agent design.

### Three-Agent Architecture (Planner / Generator / Evaluator)

Three specialized agents with hard context separation: Planner expands the spec, Generator builds in sprints, Evaluator tests live output adversarially without seeing Generator reasoning. Related table entries: **Evaluator-Optimizer**, **Adversarial Verification** (Multi-Agent Patterns above).

| Use when | Watch out for |
|----------|---------------|
| Multi-hour autonomous builds where self-evaluation is unreliable | Contract and evaluator overhead; three contexts to maintain |

### The Ralph Loop

Autonomous loop: agent runs repeatedly until PRD tasks complete. Each iteration uses a fresh context; state persists via git, progress files, and on-disk task lists.

| Use when | Watch out for |
|----------|---------------|
| Batch work with binary "done" (tests, tags, completion promises) | Runaway loops without stop hooks; noisier than single-pass pipelines |

### Sprint Contracts

Generator and Evaluator negotiate binary, testable acceptance criteria in a file **before** implementation. Same mechanism as harness `contract.json` in this repo.

| Use when | Watch out for |
|----------|---------------|
| Any work where quality matters and self-assessment is insufficient | Weak criteria produce weak verification; negotiate before building |

### File-Based Inter-Agent Communication

Agents read/write shared files instead of in-context messages. Implements crash-survivable, inspectable state. Related: **Disk Checkpoints**, **Breadcrumb Trail** (State & Recovery below).

| Use when | Watch out for |
|----------|---------------|
| Multi-agent or multi-session systems; default for durable state | File schema drift; concurrent writes without locking |

### Context Resets vs Compaction

Clear the context window and start a fresh agent with a structured handoff artifact instead of summarizing old context (compaction).

| Use when | Watch out for |
|----------|---------------|
| Model degrades as context grows; long-horizon work | Handoff artifacts must be complete or critical detail is lost |

### Adversarial Verification

Builder implements; separate evaluator in a fresh context tests actual output against a contract — no access to builder reasoning.

| Use when | Watch out for |
|----------|---------------|
| Non-trivial, high-stakes work (always in this repo's harness) | Evaluator maintenance; criteria must be binary and testable |

### Brain / Hands / Session Decoupling

Separate reasoning (brain), tool execution (hands), and session lifecycle. Each layer is independently replaceable as models and runtimes evolve.

| Use when | Watch out for |
|----------|---------------|
| Production systems swapping models, tools, or environments | Integration surface area; version skew between layers |

---

## Long-Running Specific Patterns

These patterns address challenges unique to tasks that run for minutes to hours:

### State & Recovery

| Pattern | Description |
|---------|-------------|
| **Disk Checkpoints** | Save each stage's output as JSON. Resume from any stage on failure. |
| **Breadcrumb Trail** | Append-only log of decisions and progress. Enables post-hoc debugging. |
| **Graceful Degradation** | Failed stages produce coverage gaps, not crashes. Pipeline continues with reduced scope. |
| **Partial Results** | Even if the pipeline fails midway, whatever completed is still useful. |

### Observability

| Pattern | Description |
|---------|-------------|
| **Per-Call Metrics** | Every LLM call logs: prompt size, response size, latency, cost estimate, model, status. |
| **Stage Timing** | Start/end timestamps per stage. Identifies bottlenecks. |
| **Coverage Gaps** | Explicit records of what the system couldn't analyze and why. |
| **Agent Thought Logging** | Stream and log intermediate reasoning for evaluation. |

### Cost Management

| Pattern | Description |
|---------|-------------|
| **Minimum Capable Component** | Use the cheapest tool that reliably solves the problem. Script > Schema > Fast LLM > Reasoning LLM. |
| **Batched Processing** | Group items into single prompts to reduce per-call overhead. |
| **Progressive Enhancement** | Start with fast/cheap, escalate to expensive only when quality is insufficient. |
| **Budget Gates** | Set cost limits per stage or per run. Abort or degrade gracefully if exceeded. |

### Human-in-the-Loop

| Pattern | Description |
|---------|-------------|
| **Approval Checkpoints** | Pause at key decisions and wait for human confirmation before proceeding. |
| **Confidence Thresholds** | Auto-proceed when confidence is high, escalate to human when low. |
| **Progress Visibility** | Real-time status updates so humans know what's happening during long runs. |
| **Abort/Resume** | Human can stop a run at any point and resume later from the last checkpoint. |

---

## Anti-Patterns

| Anti-Pattern | Why it fails | Alternative |
|--------------|-------------|-------------|
| **LLM for everything** | Slow, expensive, non-deterministic for tasks that don't require reasoning | Use scripts/heuristics first |
| **Self-evaluation** | Agents are poor judges of their own output | Adversarial evaluator |
| **Monolithic prompts** | One giant prompt for the entire task | Break into stages with validated intermediate outputs |
| **No observability** | Can't debug, can't evaluate, can't improve | Log everything from day one |
| **Optimistic parsing** | Assume LLM output is always valid JSON/schema | Validate with Pydantic, retry with error feedback |
| **Fire and forget** | Launch long run with no monitoring | Checkpoints, progress logs, abort capability |
