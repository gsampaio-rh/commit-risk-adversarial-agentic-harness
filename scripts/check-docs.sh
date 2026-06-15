#!/usr/bin/env bash
# Verification script for Documentation Overhaul Phase 1.
# Checks all 6 acceptance criteria with machine-verifiable assertions.
# Exit 0 = all pass, exit 1 = at least one failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0

check() {
    local ac="$1" desc="$2"
    shift 2
    if "$@" >/dev/null 2>&1; then
        echo "  PASS  $ac: $desc"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  $ac: $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Documentation Phase 1 — Acceptance Criteria ==="
echo ""

# AC-1: project_domain.mdc exists, alwaysApply: true, <= 60 lines
check "AC-1a" "project_domain.mdc exists" test -f .cursor/rules/project_domain.mdc
check "AC-1b" "alwaysApply: true" rg -q "alwaysApply: true" .cursor/rules/project_domain.mdc
LINE_COUNT=$(wc -l < .cursor/rules/project_domain.mdc | tr -d ' ')
check "AC-1c" "line count <= 60 (actual: $LINE_COUNT)" test "$LINE_COUNT" -le 60

# AC-2: temporal-model.md exists with required sections, JIRA marked eval-only
check "AC-2a" "temporal-model.md exists" test -f docs/temporal-model.md
check "AC-2b" "has Investigation-time visibility section" rg -q "Investigation-Time Visibility|Investigation-time visibility" docs/temporal-model.md
check "AC-2c" "has Eval-only oracle section" rg -q "Eval-Only Oracle|Eval-only oracle" docs/temporal-model.md
check "AC-2d" "has Worked example section" rg -q "Worked Example|Worked example" docs/temporal-model.md
check "AC-2e" "JIRA described as eval-only" rg -qi "eval.only.*jira|jira.*eval.only" docs/temporal-model.md

# AC-3: glossary.md defines 8 minimum terms
check "AC-3a" "glossary.md exists" test -f docs/glossary.md
for term in "buggy" "fix_hash" "SUPPORTED" "oracle isolation" "wrong-mechanism" "judge_oracle" "historical defect context" "bug_hash"; do
    check "AC-3b" "glossary defines '$term'" rg -q "$term" docs/glossary.md
done

# AC-4: AGENTS.md exists with required links
check "AC-4a" "AGENTS.md exists" test -f AGENTS.md
check "AC-4b" "links to temporal-model.md" rg -q "temporal-model.md" AGENTS.md
check "AC-4c" "links to glossary.md" rg -q "glossary.md" AGENTS.md
check "AC-4d" "links to architecture.md" rg -q "architecture.md" AGENTS.md
check "AC-4e" "links to state.json" rg -q "state.json" AGENTS.md

# AC-5: broken links fixed
check "AC-5a" "no broken prompt-engineering-ceiling link in evaluation.md" \
    bash -c '! rg -q "\(prompt-engineering-ceiling.md\)" docs/evaluation.md'
check "AC-5b" "no broken FOUNDATIONS.md link in patterns.md" \
    bash -c '! rg -q "FOUNDATIONS\.md\)" docs/references/patterns.md'

# AC-6: citation corrected
check "AC-6a" "no McIntosh 2021 in datasets.md" \
    bash -c '! rg -q "McIntosh.*2021" docs/datasets.md'
check "AC-6b" "Keshavarz or Zenodo 5907847 in datasets.md" \
    rg -q "Keshavarz|5907847" docs/datasets.md

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
echo "All acceptance criteria verified."
