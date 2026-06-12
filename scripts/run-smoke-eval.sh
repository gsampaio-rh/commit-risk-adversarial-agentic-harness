#!/usr/bin/env bash
# Smoke eval: n=5 stratified gate — quick sanity check before full n=20/n=50 runs.
# Requires CURSOR_API_KEY or OPENAI_API_KEY (optionally in repo-root .env).
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${CURSOR_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then
  echo "ERROR: Set CURSOR_API_KEY or OPENAI_API_KEY (or create .env at repo root)."
  exit 1
fi

echo "=== smoke n=5 stratified (D6>=0.70 gate) ==="
python -m commit_investigator.runners.run_eval --max-evals 5

echo "=== smoke individual commits: f897d46, 90846b5, b4c933b7 ==="
python -m commit_investigator.runners.run_eval --commit-ids f897d46 90846b5 b4c933b7

echo "=== smoke n=20 stratified validation (D1>=0.50, D3>=0.18) ==="
python -m commit_investigator.runners.run_eval --max-evals 20

echo "Done. Compare eval-report.json dimension_averages to baseline D1=0.40 D3=0.13 D6=0.85."
