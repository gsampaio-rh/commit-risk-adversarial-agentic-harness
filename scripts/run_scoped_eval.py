#!/usr/bin/env python3
"""V4.1 Scoped Tools eval — run scoped investigation on eval cases.

Uses V4 retrieval + V3-style scoped tools (restricted to CandidateSet).
Measures Hit@5, MRR, Retrieval Recall vs V3 baselines.

Usage:
    source .env && python scripts/run_scoped_eval.py
    python scripts/run_scoped_eval.py --resume
    python scripts/run_scoped_eval.py --cases 3   # quick test on first 3 cases
"""

import json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

from commit_investigator.harness.scoped_runner import run_scoped_investigation
from commit_investigator.infra.llm import get_provider

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "results" / "v3-subagent-eval-v2" / "manifest.json"
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
RESULTS_DIR = PROJECT_ROOT / "results" / "v4-checkpoints"
RESULTS_PATH = RESULTS_DIR / "scoped-eval.json"
CHECKPOINT_PATH = RESULTS_DIR / "scoped-eval-checkpoint.json"


def load_jira(key: str) -> dict | None:
    path = JIRA_CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {"title": raw["fields"]["summary"], "description": raw["fields"].get("description") or ""}


def hit_at_k(suspects: list[dict], gt: str, k: int = 5) -> bool:
    for s in suspects[:k]:
        cid = s.get("commit_id", "")
        if cid and (gt.startswith(cid) or cid.startswith(gt[:8])):
            return True
    return False


def mrr(suspects: list[dict], gt: str) -> float:
    for i, s in enumerate(suspects):
        cid = s.get("commit_id", "")
        if cid and (gt.startswith(cid) or cid.startswith(gt[:8])):
            return 1.0 / (i + 1)
    return 0.0


def load_checkpoint() -> dict | None:
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    return None


def save_checkpoint(results: list[dict], done: set[str]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "done": sorted(done), "results": results,
    }, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    llm = get_provider(prefer_real=True)
    print(f"LLM provider: {llm.model_name}")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases = manifest["cases"]

    max_cases = int(sys.argv[sys.argv.index("--cases") + 1]) if "--cases" in sys.argv else len(cases)
    cases = cases[:max_cases]

    ckpt = load_checkpoint()
    done: set[str] = set()
    results: list[dict] = []
    if ckpt and "--resume" in sys.argv:
        done = set(ckpt["done"])
        results = ckpt["results"]
        print(f"Resuming: {len(done)} done")

    hits = sum(1 for r in results if r.get("hit"))
    mrr_sum = sum(r.get("mrr", 0) for r in results)
    recalls = sum(1 for r in results if r.get("retrieval_recall"))
    start_time = time.time()

    for i, case in enumerate(cases):
        key = case["issue_key"]
        if key in done:
            continue

        repo = Path(case["repo_path"])
        if not (repo / ".git").exists():
            print(f"  [{i+1}/{len(cases)}] SKIP {key}: repo not cloned")
            results.append({"issue_key": key, "status": "skipped"})
            done.add(key)
            save_checkpoint(results, done)
            continue

        jira = load_jira(key)
        if not jira:
            print(f"  [{i+1}/{len(cases)}] SKIP {key}: no JIRA cache")
            results.append({"issue_key": key, "status": "skipped"})
            done.add(key)
            save_checkpoint(results, done)
            continue

        t0 = time.time()
        try:
            res = run_scoped_investigation(
                title=jira["title"], description=jira["description"],
                project=case["project"], issue_key=key,
                repo_path=repo, temporal_bound=case["temporal_bound"],
                ground_truth_sha=case["bug_hash"], llm=llm,
            )
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(cases)}] ERR  {key}: {e} ({elapsed:.0f}s)")
            results.append({"issue_key": key, "status": "error", "error": str(e)})
            done.add(key)
            save_checkpoint(results, done)
            continue

        elapsed = time.time() - t0
        if res.error:
            print(f"  [{i+1}/{len(cases)}] ERR  {key}: {res.error} ({elapsed:.0f}s)")
            results.append({"issue_key": key, "status": "error", "error": res.error})
            done.add(key)
            save_checkpoint(results, done)
            continue

        h = hit_at_k(res.suspects, case["bug_hash"])
        m = mrr(res.suspects, case["bug_hash"])
        if h:
            hits += 1
        mrr_sum += m
        if res.retrieval_recall:
            recalls += 1

        tag = "HIT" if h else "MISS"
        tool_calls = len(res.trace.examination_turns) if res.trace else 0
        print(
            f"  [{i+1}/{len(cases)}] {tag} {key}: "
            f"{len(res.suspects)} suspects, recall={'Y' if res.retrieval_recall else 'N'}, "
            f"mrr={m:.2f}, {elapsed:.1f}s, {tool_calls} calls"
        )
        results.append({
            "issue_key": key, "status": "hit" if h else "miss",
            "hit": h, "mrr": m, "suspects": len(res.suspects),
            "retrieval_recall": res.retrieval_recall,
            "elapsed_s": round(elapsed, 1), "tool_calls": tool_calls,
        })
        done.add(key)
        save_checkpoint(results, done)

    total_time = time.time() - start_time
    tested = sum(1 for r in results if r.get("status") in ("hit", "miss"))
    h5 = hits / tested if tested else 0
    avg_mrr = mrr_sum / tested if tested else 0
    rr = recalls / tested if tested else 0

    print(f"\n{'='*60}")
    print(f"V4.1 Scoped Tools Eval (n={tested})")
    print(f"  Hit@5:            {h5:.3f} ({hits}/{tested})")
    print(f"  MRR:              {avg_mrr:.3f}")
    print(f"  Retrieval Recall: {rr:.3f} ({recalls}/{tested})")
    print(f"  Time:             {total_time:.1f}s ({total_time/max(tested,1):.1f}s/case)")
    print(f"  V3 baseline:      Hit@5=0.500, MRR=0.304")
    print(f"  Gate:             Hit@5>=0.20")
    print(f"  Gate:             {'PASS' if h5 >= 0.20 else 'FAIL'}")
    print(f"{'='*60}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps({
        "checkpoint": "v4.1-scoped-eval",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": llm.model_name, "n_tested": tested,
        "hit_at_5": round(h5, 4), "mrr": round(avg_mrr, 4),
        "retrieval_recall": round(rr, 4),
        "cases": results,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults: {RESULTS_PATH}")

    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()


if __name__ == "__main__":
    main()
