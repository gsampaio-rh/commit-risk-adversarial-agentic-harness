"""CLI entry point for commit investigation.

Usage: python -m commit_investigator.investigate --commit <id> --project <name>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from commit_investigator.context_builder import AuthorStatsIndex, CommitContextBuilder
from commit_investigator.git_context import GitContextProvider, GitRepoNotFoundError
from commit_investigator.llm import get_provider
from commit_investigator.orchestrator import AgentOrchestrator


def find_csv_row(commit_id: str, csv_path: Path) -> dict[str, str] | None:
    """Look up a commit's row in an ApacheJIT CSV file."""
    if not csv_path.exists():
        return None
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("commit_id", "").strip() == commit_id:
                return dict(row)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Investigate a commit for risk assessment"
    )
    parser.add_argument("--commit", required=True, help="Commit hash to investigate")
    parser.add_argument("--project", required=True, help="Project name (e.g., camel, hadoop)")
    parser.add_argument("--repos-dir", default="data/repos", help="Path to cloned repos")
    parser.add_argument("--train-csv", default="data/apachejit/apachejit_train.csv", help="Train CSV for author stats")
    parser.add_argument("--data-csv", default=None, help="CSV to look up commit features")
    parser.add_argument("--max-turns", type=int, default=3, help="Max investigation turns")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    parser.add_argument("--checkpoint-dir", default=None, help="Directory for turn checkpoints")

    args = parser.parse_args()

    try:
        git_provider = GitContextProvider.for_project(args.project, args.repos_dir)
    except GitRepoNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Run: ./scripts/clone_apache_repos.sh {args.repos_dir}", file=sys.stderr)
        sys.exit(1)

    csv_row = None
    if args.data_csv:
        csv_row = find_csv_row(args.commit, Path(args.data_csv))

    author_stats = None
    train_path = Path(args.train_csv)
    if train_path.exists():
        print("Loading author stats from train CSV...", file=sys.stderr)
        author_stats = AuthorStatsIndex.from_train_csv(train_path)

    context_builder = CommitContextBuilder(git_provider, author_stats)
    context = context_builder.build(args.commit, args.project, csv_row)

    llm = get_provider(prefer_real=True)
    print(f"Using LLM provider: {llm.model_name}", file=sys.stderr)

    orchestrator = AgentOrchestrator(
        llm_provider=llm,
        max_turns=args.max_turns,
        checkpoint_dir=args.checkpoint_dir,
    )

    report = orchestrator.investigate(
        commit_id=args.commit,
        project=args.project,
        csv_row=csv_row,
        git_provider=git_provider,
        context=context,
    )

    output_json = report.model_dump_json(indent=2)

    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
