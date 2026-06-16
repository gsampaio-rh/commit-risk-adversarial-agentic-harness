#!/usr/bin/env python3
"""Prepare eval case specs for Task Subagent evaluation.

Loads the same n=20, seed=42 eval cases used in all V3 evaluations,
generates EvalCaseSpec objects with the improved V2 prompt, and writes
a JSON manifest for dispatching Task subagents.

Usage:
    python scripts/prepare_subagent_eval.py \
        --zip data/apachejit/apachejit_dataset_replication.zip \
        --repos-dir data/repos \
        --jira-cache data/jira_cache \
        --output results/v3-subagent-eval-v2/manifest.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.context.problem_extractor import ProblemExtractor
from commit_investigator.infra.ground_truth import GroundTruthGraph
from commit_investigator.infra.jira_client import JiraClient
from commit_investigator.pipeline.task_subagent_investigator import EvalCaseSpec
from commit_investigator.runners.run_eval import select_eval_cases


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Task Subagent eval cases")
    parser.add_argument("--zip", required=True)
    parser.add_argument("--repos-dir", required=True)
    parser.add_argument("--jira-cache", default="data/jira_cache")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="results/v3-subagent-eval-v2/manifest.json")
    args = parser.parse_args()

    _load_dotenv()

    gt = GroundTruthGraph.from_replication_zip(Path(args.zip))
    jira = JiraClient(cache_dir=args.jira_cache)
    repos_dir = Path(args.repos_dir).resolve()

    cases = select_eval_cases(gt, jira, n=args.n, seed=args.seed)
    print(f"Selected {len(cases)} eval cases")

    specs: list[EvalCaseSpec] = []
    for i, case in enumerate(cases):
        repo_path = repos_dir / case.project.lower()
        temporal_bound = f"{case.fix_hash}~1"

        spec = EvalCaseSpec(
            idx=i,
            project=case.project,
            issue_key=case.issue_key,
            bug_hash=case.bug_hash,
            fix_hash=case.fix_hash,
            title=case.problem.title if case.problem else "",
            description=case.problem.description[:2000] if case.problem else "",
            temporal_bound=temporal_bound,
            repo_path=str(repo_path),
        )
        specs.append(spec)
        print(f"  [{i:03d}] {case.project}/{case.issue_key} "
              f"bug={case.bug_hash[:12]} repo={repo_path.name}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "eval_run": "task-subagent-v2",
        "prompt_version": "v2",
        "n": len(specs),
        "seed": args.seed,
        "cases": [s.to_dict() for s in specs],
    }
    output_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nManifest written to {output_path}")

    prompts_dir = output_path.parent / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        prompt_file = prompts_dir / f"{spec.idx:03d}_{spec.issue_key}.txt"
        prompt_file.write_text(spec.to_task_prompt())
    print(f"Prompts written to {prompts_dir}/")


if __name__ == "__main__":
    main()
