# References

Key resources on long-running agents, agentic systems, harness design, and related patterns.

---

## Core — Anthropic Engineering Blog

The canonical source for harness patterns. These posts document Anthropic's evolution from single-session agents to multi-hour autonomous systems.

| Title | Date | Link | Key Takeaways |
|-------|------|------|---------------|
| Building Effective Agents | Dec 2024 | [anthropic.com](https://www.anthropic.com/research/building-effective-agents) | Agent patterns taxonomy: prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer. Start simple, add complexity only when needed. |
| Effective Harnesses for Long-Running Agents | Nov 2025 | [anthropic.com](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Two-agent architecture (initializer + coding agent). File-based state. Git as memory. Progress files as handoff artifacts. The foundation. |
| Harness Design for Long-Running Application Development | Mar 2026 | [anthropic.com](https://www.anthropic.com/engineering/harness-design-long-running-apps) | Three-agent GAN-inspired architecture: Planner → Generator → Evaluator. Sprint contracts. Adversarial QA with Playwright. $200/6hr runs producing complete full-stack apps. The state of the art. |
| Scaling Managed Agents: Decoupling the Brain from the Hands | Apr 2026 | [anthropic.com](https://www.anthropic.com/engineering/managed-agents) | Meta-harness design. Brain/hands/session decoupling. Harnesses evolve with models — build interfaces that outlast implementations. |

---

## Talks & Videos

| Title | Speaker(s) | Link | Key Takeaways |
|-------|-----------|------|---------------|
| **Build Agents That Run for Hours** | Ash Prabaker & Andrew Wilson (Anthropic) | [YouTube](https://www.youtube.com/watch?v=mR-WAvEPRwE) | Full workshop on the GAN-inspired harness. History from Sonnet 3.7 (1hr) to Opus 4.6 (12hr+). Sprint contracts = negotiation between generator & evaluator. "Self-evaluation is a trap." Evaluator catches bugs by USING the app (Playwright), not reading code. 27 contract criteria for one app. Read traces, not just metrics. |
| Building Effective Agents | Anthropic | [YouTube](https://www.youtube.com/watch?v=T-D1OfcDW1M) | Agents vs workflows. Component taxonomy. When to use which pattern. |
| Claude SDK: 24-Hour Coding Agent | Cole Medin | [YouTube](https://www.youtube.com/watch?v=BGouphNN5hg) | Practical walkthrough of the Anthropic harness running 24hrs. Linear integration for task management. |
| The Ralph Wiggum Loop from 1st Principles | Geoffrey Huntley | [YouTube](https://www.youtube.com/watch?v=4Nna09dG_c0) | Original creator explaining the philosophy: "deterministically bad in an undeterministic world." |
| Ship Working Code While You Sleep | Matt Pocock | [YouTube](https://www.youtube.com/watch?v=_IK18goX4X8) | Practical Ralph loop implementation for shipping overnight. |

---

## Articles & Blog Posts

| Title | Author | Link | Key Takeaways |
|-------|--------|------|---------------|
| Long-Running Agents | Addy Osmani | [addyosmani.com](https://addyosmani.com/blog/long-running-agents/) | 5 production patterns: checkpoint-and-resume, delegated approval, memory-layered context, ambient processing, fleet orchestration. Google Cloud's perspective. |
| The Production Gap: 5 Patterns for Long-Running AI Agents | Addy Osmani & Shubham Saboo | [turingpost.com](https://www.turingpost.com/p/the-production-gap-5-patterns-for-building-long-running-ai-agents) | Deeper dive on patterns. A2A and MCP interoperability. "If it's minutes, you don't need long-running agents. If it's hours or days, these patterns are where you start." |
| The Agent Stack Bet | Addy Osmani | [oreilly.com](https://www.oreilly.com/radar/the-agent-stack-bet/) | "A mission that survives a quarter is the bar enterprises actually need." Persistence with guardrails. |
| Long Running Agent Engineering | Nicolas Bustamante | [nicolasbustamante.com](https://nicolasbustamante.com/blog/long-running-agent-engineering) | Synthesis of Anthropic + Cursor + OpenAI approaches. "The best long-running agent harnesses feel weirdly old-fashioned: Git. Markdown. Shell scripts. JSON checklists." |
| Stop Calling It an Agent. Anthropic Calls It a Harness. | Towards AI | [towardsai.net](https://pub.towardsai.net/stop-calling-it-an-agent-anthropic-calls-it-a-harness-4774d5056e7b) | 7 patterns extracted from Anthropic posts: Three-agent harness, file-based comms, context resets, sprint contracts, brain/hands/session, hooks, initializer+coding agent. |
| Generator-Evaluator Harness: Long-Running AI Apps | AI Heroes | [ai-heroes.co](https://www.ai-heroes.co/en-us/blog/long-running-agent-harness-claude-agent-sdk-2026) | Detailed breakdown of Prithvi Rajasekaran's harness. GAN analogy explained. |
| Agent Harness Design Patterns | Zylos Research | [zylos.ai](https://zylos.ai/research/2026-03-31-agent-harness-design-patterns) | Infrastructure layer for production agents. Structured docs, memory, evaluator separation. |
| GAN-Inspired Multi-Agent Harnesses (paper) | Jung-Hua Liu | [Medium](https://medium.com/@gwrx2005/gan-inspired-multi-agent-harnesses-for-long-running-autonomous-software-engineering-architecture-37a8c2d59b6b) | Academic framing. Generalised Agentic Development Cycle (GADC) framework extending three-agent pattern to full SDLC. |
| Ralph Wiggum AI Agents: The Coding Loop of 2026 | Leanware | [leanware.co](https://www.leanware.co/insights/ralph-wiggum-ai-coding) | History and mechanics of the Ralph loop. Stop hooks, completion promises, fresh context per iteration. |

---

## Code & Repos

| Repo | What | Link |
|------|------|------|
| anthropics/cwc-long-running-agents | Official harness primitives as hooks + subagent | [GitHub](https://github.com/anthropics/cwc-long-running-agents) |
| anthropics/claude-plugins-official (ralph-loop) | Official Ralph Loop plugin for Claude Code | [GitHub](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/ralph-loop) |
| coleam00/adversarial-dev | GAN-inspired harness with Claude SDK + Codex SDK | [GitHub](https://github.com/coleam00/adversarial-dev) |
| coreyepstein/ralph-methodology | Comprehensive Ralph methodology guide | [GitHub](https://github.com/coreyepstein/ralph-methodology) |
| mikeyobrien/ralph-orchestrator | Ralph orchestrator with multi-agent support | [GitHub](https://github.com/mikeyobrien/ralph-orchestrator) |

---

## Papers

| Title | Link | Relevance |
|-------|------|-----------|
| ReAct: Synergizing Reasoning and Acting | [arxiv](https://arxiv.org/abs/2210.03629) | Foundation for tool-using agents |
| Chain-of-Thought Prompting | [arxiv](https://arxiv.org/abs/2201.11903) | Reasoning patterns used in agentic loops |
| Voyager: An Open-Ended Embodied Agent | [arxiv](https://arxiv.org/abs/2305.16291) | Long-horizon task completion with skill library |
| The Landscape of Emerging AI Agent Architectures | [arxiv](https://arxiv.org/abs/2404.11584) | Survey of multi-agent architectures |
| Practices for Governing Agentic AI Systems | [OpenAI](https://cdn.openai.com/papers/practices-for-governing-agentic-ai-systems.pdf) | Safety and governance for autonomous agents |

---

## Frameworks & Tools

| Tool | What | When to use |
|------|------|-------------|
| [Cursor SDK](https://docs.cursor.com/sdk) | TypeScript/Python SDK for programmatic agent sessions | LLM calls with tool access in pipelines |
| [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents) | Anthropic's agent runtime with compaction, tools, permissions | Long-running coding, sub-agents, custom harnesses |
| [LangGraph](https://langchain-ai.github.io/langgraph/) | State machine orchestration for agents | Complex state flows, cycles, human-in-the-loop |
| [Temporal](https://temporal.io/) | Durable execution engine | Long-running workflows with retry/recovery |
| [Playwright MCP](https://github.com/anthropics/claude-code-playwright-mcp) | Browser automation for agent testing | Evaluator agents that USE the app, not just read code |

---

## Datasets (Legal Domain)

| Resource | Focus |
|----------|-------|
| [MAUD Dataset](https://zenodo.org/records/6617392) | 39K+ M&A contract annotations — used for our test fixtures |
| [Contract Understanding Atticus Dataset (CUAD)](https://www.atticusprojectai.org/cuad) | 510 contracts, 13K+ annotations across 41 clause types |
| [Legalbench](https://hazyresearch.stanford.edu/legalbench/) | Benchmark for legal reasoning tasks |

---

## Key Principles (distilled from the above)

1. **Self-evaluation is a trap** — Tuning a standalone critic to be harsh is tractable; tuning a builder to be self-critical is not. Always use an adversarial evaluator.
2. **The harness co-evolves with the model** — What was necessary for Sonnet 4.5 (context resets, sprint decomposition) may be unnecessary for Opus 4.6. Re-evaluate after each model release.
3. **File system > context window** — For state that survives sessions: git, JSON, progress files. Not in-memory, not compressed summaries.
4. **Contracts before code** — Generator and evaluator negotiate what "done" means BEFORE building starts. Vague criteria → vague critiques → the generator shrugs.
5. **Read the traces** — "The primary debugging loop was reading what the agent actually did, finding where its judgment diverged from ours, and then tuning the prompt for that." — Ash Prabaker
6. **The frontier doesn't shrink, it moves** — As models improve, harness complexity can be reduced in some areas, but new capabilities open new harness possibilities.

---

## Adding References

When adding a new reference, include:
1. Title and link
2. One-line description of relevance to long-running agents
3. Category (talk, paper, article, framework, repo)
