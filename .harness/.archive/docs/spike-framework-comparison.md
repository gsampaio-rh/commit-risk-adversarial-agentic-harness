# Spike: Framework Comparison — LangGraph vs Minimal Custom Loop

Post-V1 comparison of agent framework options for V2.

## Context

V1 uses a **minimal custom loop** (100-line `AgentOrchestrator` in `orchestrator.py`) with:
- Explicit turn counting and budget tracking
- Direct tool dispatch via `ToolRegistry`
- JSON-based checkpoint persistence
- Pluggable `LLMProvider` interface

This spike evaluates whether LangGraph (or similar) would improve V2.

## Comparison Criteria

| Criterion | Custom Loop (V1) | LangGraph |
|-----------|-----------------|-----------|
| **Lines of code** | ~180 (orchestrator.py) | ~120 (graph definition) + framework deps |
| **Checkpoint fidelity** | Manual JSON per turn | Built-in state persistence (SQLite/Postgres) |
| **Tool dispatch** | ToolRegistry.execute() | Native tool binding with schema validation |
| **Debuggability** | Print + checkpoint files | LangSmith tracing, graph visualization |
| **Conditional branching** | if/else in loop body | Declarative edges with conditions |
| **Multi-agent** | Not supported natively | Supervisor/worker patterns built-in |
| **Streaming** | Not supported | Native streaming with callbacks |
| **Dependencies** | httpx (for LLM) | langchain-core, langgraph, langsmith |

## Analysis

### Where Custom Loop Wins

1. **Zero framework coupling**: The orchestrator has no dependency beyond httpx. Swapping LLM providers, changing tool interfaces, or modifying loop logic requires editing one file.
2. **Explicit control flow**: Budget gates, turn limits, and follow-up triggers are plain Python conditions. No framework magic to debug.
3. **Minimal dependencies**: Critical for reproducibility. LangGraph brings langchain-core (fast-moving API), which has broken backward compat multiple times.
4. **Sizing**: 180 lines is well within "comprehensible in one sitting" territory.

### Where LangGraph Wins

1. **Checkpoint durability**: LangGraph's built-in checkpointer supports resumable workflows, multi-session persistence, and time-travel debugging. Our JSON checkpoints are single-session only.
2. **Observability**: LangSmith integration gives trace visualization, cost tracking, and latency profiling without custom code.
3. **Multi-agent patterns**: If V2 needs a supervisor routing between specialist sub-agents (e.g., security analyst + performance analyst), LangGraph provides this natively.
4. **Streaming**: Real-time token streaming to users — irrelevant for batch eval but useful for interactive mode.

### Where Neither Wins Clearly

- **Tool dispatch**: Both handle it well. LangGraph's schema validation is slightly more rigorous but our ToolRegistry already exports OpenAI-format schemas.
- **Testing**: Custom loop is easier to unit test (no framework mocks needed). LangGraph has test utilities but they add complexity.

## Recommendation

**Stay with custom loop for V2.** Reasons:

1. **Problem fit**: Our agent is simple (≤3 turns, 4 tools, single-step reasoning). LangGraph's value appears at higher complexity (multi-agent, long-running workflows, human-in-the-loop).
2. **Dependency risk**: LangGraph's rapid release cadence introduces upgrade friction. Our custom loop has zero framework deps to manage.
3. **Harness thesis**: The experiment claims harness engineering > framework choice. Using a minimal loop and still achieving good results strengthens this claim.
4. **Cost**: Adding LangGraph adds ~500KB of dependencies and a learning curve for no measurable capability gain at V1/V2 scale.

### When to Reconsider

- If V3 needs multi-agent collaboration (security + performance sub-agents)
- If interactive/streaming mode becomes a requirement
- If we need durable multi-session workflows (resume investigations across days)
- If observability becomes a bottleneck (LangSmith is genuinely good)

## Spike Outcome

**Decision: Defer LangGraph to V3+. Continue with custom loop.**

No code was written for this spike — the comparison is based on architecture analysis and documentation review. The 180-line custom loop adequately serves the bounded investigation pattern.
