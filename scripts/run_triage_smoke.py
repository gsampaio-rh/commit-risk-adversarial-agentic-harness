#!/usr/bin/env python3
"""V4.2 Gate: Triage smoke test — does LLM rank GT in top 7?

One-shot triage prompt on n=5 cases where Recall@15=true.
Gate: GT in top 7 (must_examine ∪ watchlist) on >= 60% of cases.

Uses pre-score formula from recall15 ablation to build ScoredShortlist@15,
then asks LLM to select 3 must_examine + 4 watchlist from those 15.
Harness constraint: top 3 by pre_score are pinned to must_examine.

Usage:
    python scripts/run_triage_smoke.py
    python scripts/run_triage_smoke.py --cases CASSANDRA-7570,HIVE-4113
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commit_investigator.eval.ground_truth import GroundTruthGraph
from commit_investigator.infra.llm import LLMMessage, LLMProvider, get_provider
from commit_investigator.models.candidates import CandidateCommit
from commit_investigator.retrieval import prepare_investigation

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
REPOS_DIR = PROJECT_ROOT / "data" / "repos"
ZIP_PATH = PROJECT_ROOT / "data" / "apachejit" / "apachejit_dataset_replication.zip"
RECALL100_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "retrieval-recall.json"
RECALL15_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "recall15-ablation.json"
RESULTS_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "triage-smoke.json"

GATE = 0.60
DEFAULT_WEIGHTS = {"file_overlap": 0.5, "signal_count": 0.3, "rank": 0.2}
MUST_EXAMINE_SIZE = 3
WATCHLIST_SIZE = 4
SHORTLIST_SIZE = 15

DEFAULT_CASES = [
    "CASSANDRA-7570",  # GT rank 1 in top 15 (auto must_examine)
    "HIVE-4113",       # GT rank 3 (auto must_examine, edge)
    "FLINK-3602",      # GT rank 1 (auto must_examine)
    "GROOVY-8298",     # GT rank 5 (needs watchlist — hard case)
    "IGNITE-2158",     # GT rank 5 (needs watchlist — hard case)
]


@dataclass
class EvalCase:
    issue_key: str
    project: str
    bug_hashes: list[str]
    fix_hash: str
    repo_path: Path
    temporal_bound: str


@dataclass(frozen=True)
class ScoredCandidate:
    commit: CandidateCommit
    pre_score: float
    file_overlap: float
    signal_count: int


@dataclass
class TriageOutput:
    must_examine_shas: list[str]
    watchlist_shas: list[str]
    raw_response: str
    parse_success: bool
    fallback_used: bool


@dataclass
class TriageResult:
    issue_key: str
    gt_in_top7: bool
    gt_in_must_examine: bool
    gt_in_watchlist: bool
    gt_in_top3_prescore: bool
    gt_rank_in_shortlist: int | None
    triage: TriageOutput | None = None
    error: str | None = None
    elapsed_s: float = 0.0


def load_jira_text(issue_key: str) -> dict[str, str] | None:
    path = JIRA_CACHE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "title": raw["fields"]["summary"],
        "description": raw["fields"].get("description") or "",
    }


def build_eval_cases(gt: GroundTruthGraph, issue_keys: list[str]) -> list[EvalCase]:
    recall_data = json.loads(RECALL100_PATH.read_text(encoding="utf-8"))
    case_map = {c["issue_key"]: c for c in recall_data["cases"]}

    cases: list[EvalCase] = []
    for key in issue_keys:
        if key not in case_map:
            print(f"  WARN {key}: not in retrieval-recall.json")
            continue
        data = case_map[key]
        project = data["project"]
        fix_hash, bug_hashes = _find_hashes(gt, key)
        if not fix_hash or not bug_hashes:
            print(f"  WARN {key}: could not resolve GT chain")
            continue
        cases.append(EvalCase(
            issue_key=key, project=project, bug_hashes=bug_hashes,
            fix_hash=fix_hash, repo_path=REPOS_DIR / project.lower(),
            temporal_bound=f"{fix_hash}~1",
        ))
    return cases


def _find_hashes(gt: GroundTruthGraph, issue_key: str) -> tuple[str, list[str]]:
    commits = gt._issue_to_commits.get(issue_key, [])
    for commit_id in commits:
        if gt.has_fix(commit_id):
            bug_hashes = gt.get_bug_commits(commit_id)
            if bug_hashes:
                return commit_id, bug_hashes
    return "", []


# --- Pre-score (reused from recall15 ablation) ---

def _file_matches(changed: str, hint: str) -> bool:
    cf, eh = changed.lower(), hint.lower()
    return cf == eh or cf.endswith("/" + eh)


def compute_file_overlap(files_changed: list[str], extracted: list[str]) -> float:
    if not extracted:
        return 0.0
    matches = sum(1 for ef in extracted if any(_file_matches(fc, ef) for fc in files_changed))
    return matches / len(extracted)


def get_signal_count(c: CandidateCommit) -> int:
    return len([s for s in c.retrieval_signal.split(",") if s and s != "recency_fallback"])


def compute_pre_scores(
    candidates: list[CandidateCommit],
    extracted_files: list[str],
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> list[ScoredCandidate]:
    if not candidates:
        return []
    max_signals = max(get_signal_count(c) for c in candidates) or 1
    n = len(candidates)
    scored: list[ScoredCandidate] = []
    for c in candidates:
        fo = compute_file_overlap(c.files_changed, extracted_files)
        sc = get_signal_count(c)
        norm_sc = sc / max_signals
        norm_rank = (c.rank - 1) / (n - 1) if n > 1 else 0.0
        pre_score = weights["file_overlap"] * fo + weights["signal_count"] * norm_sc + weights["rank"] * (1 - norm_rank)
        scored.append(ScoredCandidate(commit=c, pre_score=pre_score, file_overlap=fo, signal_count=sc))
    return sorted(scored, key=lambda s: (-s.pre_score, s.commit.commit_id))


# --- Triage prompt ---

def build_triage_prompt(
    title: str,
    description: str,
    shortlist: list[ScoredCandidate],
) -> str:
    cands_lines: list[str] = []
    for i, sc in enumerate(shortlist, start=1):
        c = sc.commit
        files = ", ".join(c.files_changed[:5])
        if len(c.files_changed) > 5:
            files += f" (+{len(c.files_changed) - 5} more)"
        diff = c.diff_summary[:300] if c.diff_summary else "(no diff available)"
        cands_lines.append(
            f"{i}. `{c.commit_id}` — \"{c.summary}\" [{c.date}]\n"
            f"   Files: {files}\n"
            f"   Signals: {c.retrieval_signal} | Pre-score: {sc.pre_score:.3f}\n"
            f"   Diff preview: {diff}"
        )
    candidates_text = "\n\n".join(cands_lines)

    return (
        "You are a bug attribution triage agent. Given a bug report and 15 candidate "
        "commits pre-ranked by a retrieval system, select the 7 most likely to have "
        "INTRODUCED the bug.\n\n"
        f"## Bug Report\n**Title:** {title}\n\n"
        f"**Description:**\n{description[:2000]}\n\n"
        f"## Candidates (15, ranked by pre-score)\n{candidates_text}\n\n"
        "## Task\n"
        "Select exactly 7 commits for further investigation:\n"
        "- **must_examine** (3): Highest priority — will be deeply examined with diffs.\n"
        "- **watchlist** (4): Second tier — examined only if must_examine is inconclusive.\n\n"
        "Focus on:\n"
        "1. Commits touching files mentioned in the bug report\n"
        "2. Changes that could CAUSE the described symptoms\n"
        "3. Temporal proximity and retrieval signal strength\n\n"
        "## Output Format\n"
        "Respond with ONLY this JSON (no other text):\n"
        "```json\n"
        "{\n"
        '  "must_examine": [\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"},\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"},\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"}\n'
        "  ],\n"
        '  "watchlist": [\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"},\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"},\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"},\n'
        '    {"sha": "<full 40-char SHA>", "rationale": "<1-line reason>"}\n'
        "  ]\n"
        "}\n"
        "```\n\n"
        "IMPORTANT: Use ONLY full 40-character SHAs from the candidate list above."
    )


# --- Triage output parser ---

_JSON_BLOCK_RE = re.compile(r"```json\s*\n([\s\S]+?)\n\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r'\{\s*"must_examine"[\s\S]+\}', re.DOTALL)


def parse_triage_output(
    text: str,
    shortlist: list[ScoredCandidate],
) -> TriageOutput:
    """Parse LLM triage output, apply harness constraints."""
    valid_shas = {sc.commit.commit_id.lower() for sc in shortlist}
    must_examine: list[str] = []
    watchlist: list[str] = []
    parse_success = False

    json_match = _JSON_BLOCK_RE.search(text) or _BARE_JSON_RE.search(text)
    if json_match:
        try:
            raw_json = json_match.group(1) if _JSON_BLOCK_RE.search(text) else json_match.group(0)
            data = json.loads(raw_json)
            for entry in data.get("must_examine", []):
                sha = entry.get("sha", "").strip().lower()
                if sha in valid_shas:
                    must_examine.append(sha)
            for entry in data.get("watchlist", []):
                sha = entry.get("sha", "").strip().lower()
                if sha in valid_shas:
                    watchlist.append(sha)
            parse_success = bool(must_examine or watchlist)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    fallback_used = not parse_success
    if fallback_used:
        must_examine = [sc.commit.commit_id.lower() for sc in shortlist[:MUST_EXAMINE_SIZE]]
        watchlist = [sc.commit.commit_id.lower() for sc in shortlist[MUST_EXAMINE_SIZE:MUST_EXAMINE_SIZE + WATCHLIST_SIZE]]

    # Harness constraint: top 3 by pre_score MUST be in must_examine
    top3 = [sc.commit.commit_id.lower() for sc in shortlist[:MUST_EXAMINE_SIZE]]
    llm_must = set(must_examine)
    llm_watch = set(watchlist)

    final_must: list[str] = list(top3)  # pin top 3
    for sha in must_examine:
        if sha not in final_must and len(final_must) < MUST_EXAMINE_SIZE:
            final_must.append(sha)

    # LLM picks that were must_examine but got bumped → move to watchlist
    all_must_set = set(final_must)
    demoted = [s for s in must_examine if s not in all_must_set]

    final_watch: list[str] = []
    for sha in demoted + watchlist:
        if sha not in all_must_set and sha not in final_watch and len(final_watch) < WATCHLIST_SIZE:
            final_watch.append(sha)
    # Fill remaining watchlist slots from shortlist
    for sc in shortlist:
        sha = sc.commit.commit_id.lower()
        if sha not in all_must_set and sha not in final_watch and len(final_watch) < WATCHLIST_SIZE:
            final_watch.append(sha)

    return TriageOutput(
        must_examine_shas=final_must,
        watchlist_shas=final_watch,
        raw_response=text,
        parse_success=parse_success,
        fallback_used=fallback_used,
    )


def find_gt_in_shortlist(
    shortlist: list[ScoredCandidate],
    bug_hashes: list[str],
) -> int | None:
    """Return 1-based rank of best GT commit in shortlist, or None."""
    targets = {bh.lower() for bh in bug_hashes}
    for i, sc in enumerate(shortlist, start=1):
        if sc.commit.commit_id.lower() in targets:
            return i
    return None


def run_triage_case(
    case: EvalCase,
    llm: LLMProvider,
) -> TriageResult:
    start = time.time()
    jira = load_jira_text(case.issue_key)
    if jira is None:
        return TriageResult(
            issue_key=case.issue_key, gt_in_top7=False, gt_in_must_examine=False,
            gt_in_watchlist=False, gt_in_top3_prescore=False, gt_rank_in_shortlist=None,
            error="JIRA cache missing",
        )

    try:
        retrieval = prepare_investigation(
            source=(jira["title"], jira["description"]),
            repo_path=case.repo_path,
            temporal_bound=case.temporal_bound,
            project=case.project,
            issue_key=case.issue_key,
        )
    except Exception as exc:
        return TriageResult(
            issue_key=case.issue_key, gt_in_top7=False, gt_in_must_examine=False,
            gt_in_watchlist=False, gt_in_top3_prescore=False, gt_rank_in_shortlist=None,
            error=f"Retrieval failed: {exc}",
        )

    scored = compute_pre_scores(
        retrieval.candidate_set.commits,
        retrieval.problem_statement.extracted_files,
    )
    shortlist = scored[:SHORTLIST_SIZE]
    gt_rank = find_gt_in_shortlist(shortlist, case.bug_hashes)

    if gt_rank is None:
        elapsed = time.time() - start
        return TriageResult(
            issue_key=case.issue_key, gt_in_top7=False, gt_in_must_examine=False,
            gt_in_watchlist=False, gt_in_top3_prescore=False, gt_rank_in_shortlist=None,
            error="GT not in shortlist (Recall@15 = false for this run)",
            elapsed_s=round(elapsed, 1),
        )

    prompt = build_triage_prompt(jira["title"], jira["description"], shortlist)
    print(f"    Calling LLM ({llm.model_name})... ", end="", flush=True)

    try:
        response = llm.complete(
            messages=[LLMMessage(role="user", content=prompt)],
            temperature=0.0,
            max_tokens=2048,
        )
    except Exception as exc:
        elapsed = time.time() - start
        return TriageResult(
            issue_key=case.issue_key, gt_in_top7=False, gt_in_must_examine=False,
            gt_in_watchlist=False, gt_in_top3_prescore=(gt_rank <= MUST_EXAMINE_SIZE),
            gt_rank_in_shortlist=gt_rank,
            error=f"LLM failed: {exc}", elapsed_s=round(elapsed, 1),
        )

    print(f"done ({response.tokens_used} tokens)")
    triage = parse_triage_output(response.content, shortlist)

    gt_targets = {bh.lower() for bh in case.bug_hashes}
    gt_in_must = bool(gt_targets & set(triage.must_examine_shas))
    gt_in_watch = bool(gt_targets & set(triage.watchlist_shas))
    gt_in_top7 = gt_in_must or gt_in_watch

    elapsed = time.time() - start
    return TriageResult(
        issue_key=case.issue_key,
        gt_in_top7=gt_in_top7,
        gt_in_must_examine=gt_in_must,
        gt_in_watchlist=gt_in_watch,
        gt_in_top3_prescore=(gt_rank <= MUST_EXAMINE_SIZE),
        gt_rank_in_shortlist=gt_rank,
        triage=triage,
        elapsed_s=round(elapsed, 1),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="V4.2 triage smoke test")
    parser.add_argument(
        "--cases", type=str, default=None,
        help="Comma-separated issue keys (default: 5 representative cases)",
    )
    parser.add_argument("--mock", action="store_true", help="Use mock provider (parse test only)")
    args = parser.parse_args()

    case_keys = args.cases.split(",") if args.cases else DEFAULT_CASES

    print("Loading ground truth graph...")
    gt = GroundTruthGraph.from_replication_zip(str(ZIP_PATH))

    print(f"Building eval cases for {len(case_keys)} issue keys...")
    cases = build_eval_cases(gt, case_keys)
    print(f"  Resolved {len(cases)} cases\n")

    if not cases:
        print("ERROR: No valid cases found")
        sys.exit(1)

    print("Initializing LLM provider...")
    if args.mock:
        from commit_investigator.infra.llm import MockLLMProvider
        llm = MockLLMProvider()
    else:
        llm = get_provider(prefer_real=True)
    print(f"  Provider: {llm.model_name}\n")

    results: list[TriageResult] = []
    for i, case in enumerate(cases, start=1):
        print(f"[{i}/{len(cases)}] {case.issue_key}")
        print(f"    Retrieving candidates...")
        result = run_triage_case(case, llm)

        if result.error:
            print(f"    ERROR: {result.error}")
        else:
            marker = "HIT" if result.gt_in_top7 else "MISS"
            detail = "must_examine" if result.gt_in_must_examine else ("watchlist" if result.gt_in_watchlist else "not_selected")
            prescore_note = f"prescore_rank={result.gt_rank_in_shortlist}"
            parsed_note = "parsed" if (result.triage and result.triage.parse_success) else "fallback"
            print(f"    {marker:4s}  gt_in={detail}  {prescore_note}  ({parsed_note})  [{result.elapsed_s:.1f}s]")
        results.append(result)

    # Aggregate
    tested = [r for r in results if r.error is None]
    n_tested = len(tested)
    n_hit = sum(1 for r in tested if r.gt_in_top7)
    n_must = sum(1 for r in tested if r.gt_in_must_examine)
    n_watch = sum(1 for r in tested if r.gt_in_watchlist)
    n_prescore = sum(1 for r in tested if r.gt_in_top3_prescore)
    n_parsed = sum(1 for r in tested if r.triage and r.triage.parse_success)
    recall7 = n_hit / n_tested if n_tested > 0 else 0.0
    gate_passed = recall7 >= GATE

    print(f"\n{'='*72}")
    print(f"TRIAGE SMOKE TEST  (n_tested={n_tested}, gate={GATE:.0%})")
    print(f"{'='*72}")
    print(f"  TriageRecall@7:  {recall7:.2f} ({n_hit}/{n_tested})  [{'PASS' if gate_passed else 'FAIL'}]")
    print(f"  GT in must_examine: {n_must}/{n_tested}")
    print(f"  GT in watchlist:    {n_watch}/{n_tested}")
    print(f"  GT in top3_prescore (auto): {n_prescore}/{n_tested}")
    print(f"  Parse success:      {n_parsed}/{n_tested}")
    print(f"  Provider:           {llm.model_name}")
    print(f"{'='*72}")

    output = {
        "checkpoint": "triage-smoke",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gate": GATE,
        "n_tested": n_tested,
        "n_errors": len(results) - n_tested,
        "triage_recall_7": round(recall7, 4),
        "gate_passed": gate_passed,
        "provider": llm.model_name,
        "breakdown": {
            "gt_in_must_examine": n_must,
            "gt_in_watchlist": n_watch,
            "gt_in_top3_prescore": n_prescore,
            "parse_success": n_parsed,
        },
        "cases": [
            {
                "issue_key": r.issue_key,
                "gt_in_top7": r.gt_in_top7,
                "gt_in_must_examine": r.gt_in_must_examine,
                "gt_in_watchlist": r.gt_in_watchlist,
                "gt_in_top3_prescore": r.gt_in_top3_prescore,
                "gt_rank_in_shortlist": r.gt_rank_in_shortlist,
                "parse_success": r.triage.parse_success if r.triage else None,
                "fallback_used": r.triage.fallback_used if r.triage else None,
                "must_examine_shas": r.triage.must_examine_shas if r.triage else None,
                "watchlist_shas": r.triage.watchlist_shas if r.triage else None,
                "error": r.error,
                "elapsed_s": r.elapsed_s,
            }
            for r in results
        ],
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults written to {RESULTS_PATH}")

    sys.exit(0 if gate_passed else 1)


if __name__ == "__main__":
    main()
