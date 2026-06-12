"""Tool registry: defines tools available to the investigative agent.

Tools wrap GitContextProvider methods and feature lookup for LLM dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from commit_investigator.context.context_builder import InvestigationContext
from commit_investigator.context.git_context import GitContextProvider


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


def build_default_registry(
    git_provider: GitContextProvider,
    context: InvestigationContext,
) -> ToolRegistry:
    """Build the standard tool registry for commit investigation."""
    registry = ToolRegistry()

    registry.register(ToolDefinition(
        name="get_file_history",
        description="Get the last N commits that modified a specific file",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to check history for"},
                "n": {"type": "integer", "description": "Number of commits to return", "default": 3},
            },
            "required": ["path"],
        },
        handler=lambda path, n=3: _handle_file_history(git_provider, path, n),
    ))

    registry.register(ToolDefinition(
        name="get_file_diff",
        description="Get the diff for a specific file in the commit under investigation",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to get diff for"},
            },
            "required": ["path"],
        },
        handler=lambda path: _handle_file_diff(git_provider, context.commit_id, path),
    ))

    registry.register(ToolDefinition(
        name="get_numeric_features",
        description="Get the numeric commit features (lines added/deleted, entropy, author experience, etc.)",
        parameters={"type": "object", "properties": {}},
        handler=lambda: _handle_numeric_features(context),
    ))

    registry.register(ToolDefinition(
        name="get_author_stats",
        description="Get precomputed statistics for the commit author",
        parameters={"type": "object", "properties": {}},
        handler=lambda: _handle_author_stats(context),
    ))

    return registry


def _handle_file_history(git: GitContextProvider, path: str, n: int = 3) -> str:
    entries = git.get_file_history(path, n=n)
    if not entries:
        return f"No history found for {path}"
    lines = [f"History for {path} (last {n} commits):"]
    for e in entries:
        lines.append(f"  {e.commit_id[:8]} | {e.date[:10]} | {e.author} | {e.message}")
    return "\n".join(lines)


def _handle_file_diff(git: GitContextProvider, commit_id: str, path: str) -> str:
    diff = git.get_diff(commit_id)
    if not diff:
        return f"No diff available for {commit_id}"
    file_sections = []
    in_section = False
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            in_section = path in line
        if in_section:
            file_sections.append(line)
    return "\n".join(file_sections) if file_sections else f"File {path} not in diff"


def _handle_numeric_features(context: InvestigationContext) -> str:
    if not context.csv_features:
        return "No numeric features available"
    lines = ["Numeric features for this commit:"]
    for k, v in sorted(context.csv_features.items()):
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _handle_author_stats(context: InvestigationContext) -> str:
    if not context.author_stats:
        return "No author stats available"
    s = context.author_stats
    return (
        f"Author stats ({s.author}):\n"
        f"  Total commits: {s.total_commits}\n"
        f"  Buggy commits: {s.buggy_commits} ({s.buggy_rate:.1%})\n"
        f"  Avg files changed: {s.avg_files_changed:.1f}\n"
        f"  Avg lines added: {s.avg_lines_added:.1f}\n"
        f"  Avg lines deleted: {s.avg_lines_deleted:.1f}\n"
        f"  Projects: {', '.join(s.projects)}"
    )
