#!/usr/bin/env python3
"""P9: Full V4 eval — run_v4_investigation on n=20 eval cases.

Runs the complete V4 pipeline (input pipeline + harness + trace) using the
Cursor SDK as LLM provider. Measures Hit@5, MRR, D6 vs V3 baselines.

Features:
  - Checkpoint/resume: saves progress after each case, resumes on restart
  - Per-call timeout: prevents indefinite SDK hangs
  - Structured context injection: synthesizes evidence before attribution

Usage:
    source .env && python scripts/run_v4_eval.py
    python scripts/run_v4_eval.py --resume   # resume from checkpoint
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

from commit_investigator.harness.llm_protocol import LLMResponse as HarnessLLMResponse
from commit_investigator.harness.v4_runner import run_v4_investigation

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "results" / "v3-subagent-eval-v2" / "manifest.json"
JIRA_CACHE_DIR = PROJECT_ROOT / "data" / "jira_cache"
RESULTS_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "full-eval-n20.json"
CHECKPOINT_PATH = PROJECT_ROOT / "results" / "v4-checkpoints" / "eval-checkpoint.json"

SEND_TIMEOUT = 300
CASE_TIMEOUT = 900


class CursorMultiTurnAdapter:
    """Uses Agent.create() + agent.send() for multi-turn investigation.

    One agent session per investigation — context accumulates across
    planning, examination, and attribution turns. Uses thread-based
    timeout since SIGALRM doesn't interrupt the SDK's IPC blocking.
    """

    def __init__(self) -> None:
        self._total_tokens = 0
        self._total_cost = 0.0
        self._agent = None
        self._call_count = 0
        self._consecutive_errors = 0

    def _ensure_agent(self):
        if self._agent is None:
            from cursor_sdk import Agent, LocalAgentOptions
            self._agent = Agent.create(
                model="claude-sonnet-4-6",
                api_key=os.environ["CURSOR_API_KEY"],
                local=LocalAgentOptions(cwd=str(PROJECT_ROOT)),
            )
            self._agent.__enter__()
            self._consecutive_errors = 0

    def _recycle_agent(self):
        """Kill and recreate agent after errors (bridge may be stale)."""
        self.close()
        self._ensure_agent()

    def generate(self, prompt: str, **kwargs) -> HarnessLLMResponse:
        self._ensure_agent()
        self._call_count += 1

        import concurrent.futures
        response_text = ""

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._send_prompt, prompt)
        try:
            response_text = future.result(timeout=SEND_TIMEOUT)
            self._consecutive_errors = 0
        except concurrent.futures.TimeoutError:
            response_text = f"Error: agent.send() exceeded {SEND_TIMEOUT}s timeout"
            self._consecutive_errors += 1
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            self._recycle_agent()
        except Exception as e:
            response_text = f"Error: {e}"
            self._consecutive_errors += 1
            if self._consecutive_errors >= 2:
                self._recycle_agent()
        else:
            executor.shutdown(wait=False)

        word_count = len(prompt.split()) + len(response_text.split())
        token_estimate = int(word_count * 1.3)
        cost_estimate = token_estimate * 0.000003

        self._total_tokens += token_estimate
        self._total_cost += cost_estimate

        return HarnessLLMResponse(
            content=response_text,
            tool_calls=[],
            tokens_used=token_estimate,
            cost=cost_estimate,
            model="cursor-sdk/claude-sonnet-4-6",
        )

    def _send_prompt(self, prompt: str) -> str:
        """Execute agent.send() in a thread (allows timeout via Future)."""
        run = self._agent.send(prompt)
        return run.text()

    def close(self):
        if self._agent is not None:
            try:
                self._agent.__exit__(None, None, None)
            except Exception:
                pass
            self._agent = None

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def call_count(self) -> int:
        return self._call_count


def load_jira_text(issue_key: str) -> dict | None:
    path = JIRA_CACHE_DIR / f"{issue_key}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "title": raw["fields"]["summary"],
        "description": raw["fields"].get("description") or "",
    }


def compute_hit_at_k(suspects: list[dict], ground_truth: str, k: int = 5) -> bool:
    for suspect in suspects[:k]:
        commit_id = suspect.get("commit_id", "")
        if commit_id and ground_truth.startswith(commit_id):
            return True
        if commit_id and commit_id.startswith(ground_truth[:8]):
            return True
    return False


def compute_mrr(suspects: list[dict], ground_truth: str) -> float:
    for i, suspect in enumerate(suspects):
        commit_id = suspect.get("commit_id", "")
        if commit_id and (ground_truth.startswith(commit_id) or commit_id.startswith(ground_truth[:8])):
            return 1.0 / (i + 1)
    return 0.0


def load_checkpoint() -> dict | None:
    """Load checkpoint from disk if it exists."""
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    return None


def save_checkpoint(results: list[dict], completed_keys: set[str]) -> None:
    """Save progress checkpoint after each case."""
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "completed_keys": sorted(completed_keys),
        "results": results,
    }
    CHECKPOINT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if not os.environ.get("CURSOR_API_KEY"):
        print("ERROR: CURSOR_API_KEY not set. Source .env first.")
        sys.exit(1)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases = manifest["cases"]

    checkpoint = load_checkpoint()
    completed_keys: set[str] = set()
    results_per_case: list[dict] = []

    if checkpoint and "--resume" in sys.argv:
        completed_keys = set(checkpoint["completed_keys"])
        results_per_case = checkpoint["results"]
        print(f"Resuming from checkpoint: {len(completed_keys)} cases already done")
    elif checkpoint and "--resume" not in sys.argv:
        print(f"Checkpoint exists ({len(checkpoint['completed_keys'])} cases). "
              f"Use --resume to continue or delete {CHECKPOINT_PATH} to restart.")

    print(f"Running V4 eval on {len(cases)} cases with Cursor SDK...")

    hits_at_5 = sum(1 for r in results_per_case if r.get("hit_at_5"))
    mrr_sum = sum(r.get("mrr", 0.0) for r in results_per_case)
    retrieval_hits = sum(1 for r in results_per_case if r.get("retrieval_recall"))
    skipped = sum(1 for r in results_per_case if r.get("status") == "skipped")
    errors = sum(1 for r in results_per_case if r.get("status") == "error")
    start_time = time.time()

    for i, case in enumerate(cases):
        issue_key = case["issue_key"]
        project = case["project"]
        repo_path = Path(case["repo_path"])
        temporal_bound = case["temporal_bound"]
        bug_hash = case["bug_hash"]

        if issue_key in completed_keys:
            continue

        if not (repo_path / ".git").exists():
            print(f"  [{i+1}/{len(cases)}] SKIP {issue_key}: repo not cloned")
            skipped += 1
            results_per_case.append({"issue_key": issue_key, "status": "skipped"})
            completed_keys.add(issue_key)
            save_checkpoint(results_per_case, completed_keys)
            continue

        jira = load_jira_text(issue_key)
        if jira is None:
            print(f"  [{i+1}/{len(cases)}] SKIP {issue_key}: JIRA cache missing")
            skipped += 1
            results_per_case.append({"issue_key": issue_key, "status": "skipped"})
            completed_keys.add(issue_key)
            save_checkpoint(results_per_case, completed_keys)
            continue

        llm = CursorMultiTurnAdapter()
        case_start = time.time()

        try:
            result = run_v4_investigation(
                title=jira["title"],
                description=jira["description"],
                project=project,
                issue_key=issue_key,
                repo_path=repo_path,
                temporal_bound=temporal_bound,
                ground_truth_sha=bug_hash,
                llm=llm,
                traces_dir=PROJECT_ROOT / "results" / "traces",
            )
        except Exception as e:
            case_time = time.time() - case_start
            print(f"  [{i+1}/{len(cases)}] ERR  {issue_key}: {e} ({case_time:.0f}s)")
            errors += 1
            results_per_case.append({
                "issue_key": issue_key, "status": "error",
                "error": str(e), "elapsed_s": round(case_time, 1),
            })
            completed_keys.add(issue_key)
            save_checkpoint(results_per_case, completed_keys)
            llm.close()
            continue
        finally:
            llm.close()

        case_time = time.time() - case_start

        if result.error:
            print(f"  [{i+1}/{len(cases)}] ERR  {issue_key}: {result.error} ({case_time:.0f}s)")
            errors += 1
            results_per_case.append({
                "issue_key": issue_key, "status": "error",
                "error": result.error, "elapsed_s": round(case_time, 1),
            })
            completed_keys.add(issue_key)
            save_checkpoint(results_per_case, completed_keys)
            continue

        hit = compute_hit_at_k(result.suspects, bug_hash, k=5)
        mrr = compute_mrr(result.suspects, bug_hash)

        if hit:
            hits_at_5 += 1
        mrr_sum += mrr
        if result.retrieval_recall:
            retrieval_hits += 1

        status = "HIT" if hit else "MISS"
        print(
            f"  [{i+1}/{len(cases)}] {status} {issue_key}: "
            f"{len(result.suspects)} suspects, "
            f"retrieval={'Y' if result.retrieval_recall else 'N'}, "
            f"mrr={mrr:.2f}, "
            f"{case_time:.1f}s, "
            f"{llm.call_count} calls, "
            f"${llm.total_cost:.4f}"
        )

        results_per_case.append({
            "issue_key": issue_key,
            "project": project,
            "status": "hit" if hit else "miss",
            "hit_at_5": hit,
            "mrr": mrr,
            "suspect_count": len(result.suspects),
            "retrieval_recall": result.retrieval_recall,
            "degraded": result.outcome.degraded if result.outcome else False,
            "degraded_reason": result.outcome.degraded_reason if result.outcome else None,
            "elapsed_s": round(case_time, 1),
            "tokens": llm.total_tokens,
            "cost": round(llm.total_cost, 4),
        })
        completed_keys.add(issue_key)
        save_checkpoint(results_per_case, completed_keys)

    elapsed = time.time() - start_time
    tested = len(cases) - skipped
    hit_at_5_rate = hits_at_5 / tested if tested > 0 else 0.0
    mrr_rate = mrr_sum / tested if tested > 0 else 0.0
    retrieval_recall = retrieval_hits / tested if tested > 0 else 0.0

    print(f"\n{'='*60}")
    print(f"V4 Full Eval Results (n={tested})")
    print(f"  Hit@5:            {hit_at_5_rate:.3f} ({hits_at_5}/{tested})")
    print(f"  MRR:              {mrr_rate:.3f}")
    print(f"  Retrieval Recall: {retrieval_recall:.3f} ({retrieval_hits}/{tested})")
    print(f"  Errors:           {errors}")
    print(f"  Time:             {elapsed:.1f}s ({elapsed/max(tested,1):.1f}s/case)")
    print(f"")
    print(f"  V3 baseline:      Hit@5=0.500, MRR=0.304")
    print(f"  Gate:             Hit@5>=0.40, MRR>=0.20")
    print(f"  Hit@5 gate:       {'PASS' if hit_at_5_rate >= 0.40 else 'FAIL'}")
    print(f"  MRR gate:         {'PASS' if mrr_rate >= 0.20 else 'FAIL'}")
    print(f"{'='*60}")

    output = {
        "checkpoint": "v4-full-eval-n20",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": "cursor-sdk/claude-sonnet-4-6",
        "n_total": len(cases),
        "n_tested": tested,
        "n_skipped": skipped,
        "n_errors": errors,
        "hit_at_5": round(hit_at_5_rate, 4),
        "hit_at_5_count": hits_at_5,
        "mrr": round(mrr_rate, 4),
        "retrieval_recall_100": round(retrieval_recall, 4),
        "retrieval_recall_hits": retrieval_hits,
        "elapsed_seconds": round(elapsed, 1),
        "gate": {"hit_at_5": 0.40, "mrr": 0.20},
        "gate_passed": hit_at_5_rate >= 0.40 and mrr_rate >= 0.20,
        "v3_baseline": {"hit_at_5": 0.50, "mrr": 0.304},
        "cases": results_per_case,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults written to {RESULTS_PATH}")

    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        print("Checkpoint cleared (eval complete).")


if __name__ == "__main__":
    main()
