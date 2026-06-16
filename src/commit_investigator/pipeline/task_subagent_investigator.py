"""Task Subagent Investigator — runs bug attribution via Cursor IDE subagents.

Uses Cursor IDE's generalPurpose Task subagents with Shell/Read/Grep tools
to investigate git repositories. Each investigation runs as an independent
subagent with full shell access to the repo.

Prompt improvements (v2) based on failure forensics of 13 misses:
  - Enforce minimum 3 suspects (reduce overconfidence)
  - Causal chain guidance (consider ALL commits in a change chain)
  - 2-phase search strategy (broad → narrow for large repos)
  - Stronger evidence demands (causal mechanism required)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from commit_investigator.context.problem_extractor import ProblemStatement
from commit_investigator.pipeline.orchestrator import (
    BugAttributionReport,
    SuspectCommit,
)

logger = logging.getLogger(__name__)


INVESTIGATION_PROMPT_V2 = """\
You are a bug attribution agent. You must find the commit that INTRODUCED
a reported bug in a git repository.

## CRITICAL RULES

1. **Temporal Boundary**: You MUST NOT look at any commit at or after `{temporal_bound}`.
   All git commands must respect this: `git log {temporal_bound} ...`
   Use `git blame {temporal_bound} -- <file>` for blame.

2. **Minimum 3 Suspects**: You MUST always produce at least 3 suspect commits,
   even if you are highly confident in one. The ground truth is noisy (SZZ-based
   labeling), so the "obvious" commit is often wrong. List alternatives from
   nearby commits in the same area of code.

3. **Full 40-char SHAs**: Always use full SHA hashes. Run `git rev-parse <short>`
   to expand short hashes before including them in your output.

## Bug Report

**{title}**

{description}

## Investigation Strategy (2-Phase)

### Phase 1 — Broad Search (use 3-5 different strategies)

Pick at least 3 of these search approaches:

a) **Keyword search in commit messages**:
   `git log {temporal_bound} --oneline --all --grep="<keyword>" -n 15`

b) **Pickaxe search** (find commits that added/removed a string):
   `git log {temporal_bound} --oneline --all -S "<string>" -n 10`

c) **File history** (for files mentioned in the bug report):
   `git log {temporal_bound} --oneline --all -n 30 -- <path>`

d) **Blame** (who last touched the relevant lines):
   `git blame {temporal_bound} -- <file> | head -60`

e) **Directory listing** to find relevant source files:
   `find . -name "*.java" | grep -i "<keyword>" | head -20`
   or `git ls-files | grep -i "<keyword>" | head -20`

### Phase 2 — Deep Examination (trace causal chains)

For each promising commit found in Phase 1:

a) **Read the diff**: `git show <commit> --stat` then `git diff <commit>^..<commit> -- <file>`

b) **Trace the causal chain**: When you find a suspicious change, look at what
   came BEFORE it. The bug might have been introduced by an earlier commit that
   the later one built upon:
   `git log {temporal_bound} --oneline -n 10 -- <file>` to see the chain.

c) **Check parent commits**: If commit X modified a buggy function, check what
   X's parent changed: `git show <parent-commit> -- <file>`

d) **Consider the FULL chain**: For a function with commits A→B→C→D, the bug
   may have been introduced at ANY point (A, B, C, or D). Include multiple
   commits from the chain in your suspects list.

## Causal Chain Guidance

The #1 failure mode is finding the RIGHT code area but picking the WRONG commit
in a chain of changes. To avoid this:

- When you find the file/function where the bug lives, ALWAYS run
  `git log {temporal_bound} --oneline -n 20 -- <relevant-file>` to see ALL
  recent commits touching that file.
- For each commit in the chain, briefly check what it changed.
- Include 2-3 commits from the chain as suspects, not just the most recent one.
- The bug-introducing commit is often NOT the most recent one — it could be the
  one that first added the problematic pattern.

## Output Format

When done investigating, output EXACTLY this JSON block (I will parse it
programmatically):

```json
{{
  "suspects": [
    {{
      "commit_id": "<full 40-char SHA>",
      "confidence": 0.7,
      "mechanism": "This commit changed <X> which caused <Y> leading to the reported bug <Z>",
      "evidence_quotes": ["exact text from the diff showing the problematic change"]
    }},
    {{
      "commit_id": "<full 40-char SHA>",
      "confidence": 0.5,
      "mechanism": "Alternative: this earlier commit first introduced <pattern>",
      "evidence_quotes": ["exact text"]
    }},
    {{
      "commit_id": "<full 40-char SHA>",
      "confidence": 0.3,
      "mechanism": "Alternative: this commit modified <related area>",
      "evidence_quotes": ["exact text"]
    }}
  ],
  "reasoning_summary": "Brief summary of your investigation and why you ranked suspects this way"
}}
```

**Requirements for each suspect:**
- `commit_id`: Full 40-character SHA (use `git rev-parse` to expand)
- `confidence`: 0.0 to 1.0 (must sum to <= 2.0 across all suspects)
- `mechanism`: Must be a causal "If X then Y" statement, not just "this commit touched the file"
- `evidence_quotes`: At least 1 exact quote from the diff you examined

Rank suspects by confidence (highest first). Include 3-5 suspects. NEVER output
fewer than 3 suspects.

## Budget

You have ~25 shell commands. Be efficient but thorough. Spend Phase 1 on
broad search (5-8 commands), Phase 2 on deep examination (10-15 commands),
and save a few for expanding short SHAs with git rev-parse.
"""

_SUSPECTS_JSON_PATTERN = re.compile(
    r"```json\s*\n\s*(\{[\s\S]+?\})\s*\n\s*```",
    re.DOTALL,
)

_SUSPECTS_ARRAY_PATTERN = re.compile(
    r'"suspects"\s*:\s*(\[[\s\S]+?\])',
    re.DOTALL,
)


def build_investigation_prompt(
    problem: ProblemStatement,
    temporal_bound: str,
) -> str:
    """Build the investigation prompt for a given case."""
    return INVESTIGATION_PROMPT_V2.format(
        temporal_bound=temporal_bound,
        title=problem.title,
        description=problem.description,
    )


def parse_subagent_response(text: str) -> tuple[list[SuspectCommit], str]:
    """Parse suspects and reasoning from a subagent's text response."""
    json_match = _SUSPECTS_JSON_PATTERN.search(text)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            suspects_raw = data.get("suspects", [])
            reasoning = data.get("reasoning_summary", "")
            suspects = _raw_to_suspects(suspects_raw)
            return suspects, reasoning
        except (json.JSONDecodeError, KeyError):
            pass

    array_match = _SUSPECTS_ARRAY_PATTERN.search(text)
    if array_match:
        try:
            suspects_raw = json.loads(array_match.group(1))
            suspects = _raw_to_suspects(suspects_raw)
            return suspects, ""
        except json.JSONDecodeError:
            pass

    return [], ""


def _raw_to_suspects(raw: list[dict]) -> list[SuspectCommit]:
    """Convert raw JSON array to SuspectCommit list."""
    suspects = []
    for i, item in enumerate(raw):
        cid = item.get("commit_id", "")
        if not cid or len(cid) < 7:
            continue
        suspects.append(SuspectCommit(
            commit_id=cid,
            rank=i + 1,
            confidence=float(item.get("confidence", 0.0)),
            mechanism=item.get("mechanism", ""),
            evidence_quotes=item.get("evidence_quotes", []),
        ))
    return suspects


def suspects_to_report(
    problem: ProblemStatement,
    suspects: list[SuspectCommit],
    reasoning: str,
    temporal_bound: str,
    elapsed_ms: float = 0.0,
) -> BugAttributionReport:
    """Wrap parsed suspects in a BugAttributionReport for eval scoring."""
    return BugAttributionReport(
        problem_title=problem.title,
        problem_description=problem.description,
        suspects=suspects,
        reasoning_summary=reasoning,
        tool_trace=[],
        metadata={
            "turns_used": 1,
            "tool_calls": 0,
            "tokens_used": 0,
            "total_cost_usd": 0.0,
            "elapsed_ms": elapsed_ms,
            "temporal_bound": temporal_bound,
            "model": "cursor-task-subagent/claude-sonnet",
            "budget_exceeded": False,
            "evidence_scores": [],
            "evidence_scoring_applied": False,
            "post_processing_applied": False,
            "investigator": "task-subagent-v2",
        },
    )


@dataclass
class EvalCaseSpec:
    """Everything needed to dispatch a Task subagent for one eval case."""

    idx: int
    project: str
    issue_key: str
    bug_hash: str
    fix_hash: str
    title: str
    description: str
    temporal_bound: str
    repo_path: str
    prompt: str = ""

    def __post_init__(self) -> None:
        if not self.prompt:
            problem = ProblemStatement(
                title=self.title,
                description=self.description,
                project=self.project,
                issue_key=self.issue_key,
            )
            self.prompt = build_investigation_prompt(problem, self.temporal_bound)

    def to_task_prompt(self) -> str:
        """Build the full prompt for a Cursor Task subagent invocation."""
        return (
            f"You are investigating bug {self.issue_key} in the {self.project} project.\n\n"
            f"The git repository is at: {self.repo_path}\n\n"
            f"cd into the repository first, then follow these instructions:\n\n"
            f"{self.prompt}"
        )

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "project": self.project,
            "issue_key": self.issue_key,
            "bug_hash": self.bug_hash,
            "fix_hash": self.fix_hash,
            "temporal_bound": self.temporal_bound,
            "repo_path": self.repo_path,
        }
