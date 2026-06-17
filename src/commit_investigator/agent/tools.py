"""Tool registry: defines tools available to the attribution agent.

Tools wrap GitContextProvider methods for LLM function-calling dispatch.
Scoped tools restrict examination to CandidateSet SHAs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from commit_investigator.infra.git_context import GitContextProvider
from commit_investigator.models.candidates import CandidateSet


@dataclass
class ToolDefinition:
    """A tool that the agent can invoke during investigation."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]


class ToolRegistry:
    """Registry of tools available to the investigative agent.

    Tools are dispatched by name. Each tool returns a string result
    that gets appended to the conversation context.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def execute(self, name: str, **kwargs: Any) -> str:
        """Execute a tool by name. Returns result string or error message."""
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"
        try:
            return tool.handler(**kwargs)
        except Exception as e:
            return f"Error executing {name}: {e}"

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools.keys())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Export tools in OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]


def _truncate(text: str, max_chars: int = 8000) -> str:
    """Truncate long tool output to keep context manageable."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... (truncated, {len(text)} chars total)"


def _build_sha_validator(candidate_set: CandidateSet) -> Callable[[str], str | None]:
    """Return a function that validates a SHA against the CandidateSet.

    Returns None if valid, or an error message string if rejected.
    Uses 12-char prefix matching (same as git short-SHA convention).
    """
    allowed_shas = {c.commit_id for c in candidate_set.commits}
    allowed_prefixes = {sha[:12] for sha in allowed_shas}

    def validate(commit_id: str) -> str | None:
        short = commit_id[:12]
        if short in allowed_prefixes:
            return None
        if any(sha.startswith(commit_id) or commit_id.startswith(sha[:8]) for sha in allowed_shas):
            return None
        return (
            f"Error: commit {commit_id[:12]} is not in the CandidateSet "
            f"({len(allowed_shas)} commits). Use only SHAs from the candidate list."
        )

    return validate


def build_scoped_tools(
    git: GitContextProvider, candidate_set: CandidateSet
) -> ToolRegistry:
    """Build examination-only tools scoped to a CandidateSet (V4.1).

    Only registers examination tools (diff, message, blame, file_at_commit).
    Tools that take commit_id reject SHAs not in the CandidateSet.
    No search tools — retrieval is done by the input pipeline.
    """
    registry = ToolRegistry()
    validate_sha = _build_sha_validator(candidate_set)

    def _scoped_diff(commit_id: str) -> str:
        err = validate_sha(commit_id)
        if err:
            return err
        return _truncate(git.get_diff(commit_id) or f"No diff found for {commit_id}")

    def _scoped_message(commit_id: str) -> str:
        err = validate_sha(commit_id)
        if err:
            return err
        return git.get_commit_message(commit_id) or f"No message for {commit_id}"

    def _scoped_file_at_commit(commit_id: str, path: str) -> str:
        err = validate_sha(commit_id)
        if err:
            return err
        return _truncate(
            git.get_file_at_commit(commit_id, path) or f"File {path} not found at {commit_id}"
        )

    registry.register(ToolDefinition(
        name="get_commit_diff",
        description="Get the unified diff (patch) for a candidate commit. Shows exactly what code changed.",
        parameters={
            "type": "object",
            "properties": {
                "commit_id": {"type": "string", "description": "Commit SHA from the candidate list"},
            },
            "required": ["commit_id"],
        },
        handler=_scoped_diff,
    ))

    registry.register(ToolDefinition(
        name="get_commit_message",
        description="Get the full commit message for a candidate commit.",
        parameters={
            "type": "object",
            "properties": {
                "commit_id": {"type": "string", "description": "Commit SHA from the candidate list"},
            },
            "required": ["commit_id"],
        },
        handler=_scoped_message,
    ))

    registry.register(ToolDefinition(
        name="get_blame",
        description="Get git blame for a file, showing which commit last modified each line.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to blame"},
                "line_start": {"type": "integer", "description": "Start line number", "default": 1},
                "line_end": {"type": "integer", "description": "End line number (omit for whole file)"},
            },
            "required": ["path"],
        },
        handler=lambda path, line_start=1, line_end=None: _truncate(
            git.get_blame(path, line_start=line_start, line_end=line_end) or f"No blame for {path}"
        ),
    ))

    registry.register(ToolDefinition(
        name="get_file_at_commit",
        description="Get file contents at a specific candidate commit.",
        parameters={
            "type": "object",
            "properties": {
                "commit_id": {"type": "string", "description": "Commit SHA from the candidate list"},
                "path": {"type": "string", "description": "File path"},
            },
            "required": ["commit_id", "path"],
        },
        handler=_scoped_file_at_commit,
    ))

    return registry
