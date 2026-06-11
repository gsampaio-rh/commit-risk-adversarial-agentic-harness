# V2 n=50 Failure Forensics

## Executive Summary

| Dimension | Score | V2 Target | Gap |
|-----------|-------|-----------|-----|
| D1 | 0.700 | 0.80 | -0.100 |
| D2 | 0.190 | 0.25 | -0.060 |
| D3 | 0.230 | 0.35 | -0.120 |
| D4 | 0.894 | 0.75 | +0.144 |
| D5 | 0.413 | 0.40 | +0.013 |
| D6 | 0.770 | 0.70 | +0.070 |

- Clean FP rate: **32%** (8/25 clean commits)
- Buggy recall proxy: delivery gate **72%**
- Hard panel: **18** commits (11 D3=0 + 7 D3=0.25)

## D3 Failure Taxonomy

### D3=0 breakdown
- **wrong-mechanism** (9): dd6f9ba60fb4, 2213f71944ae, 55dcbe801e76, ea21180b1315, a8407f8aa67e, 2bb65c78633e, eba3689dd12a, ad6a796f4c61, 8780086dbe23
- **missing-context** (2): 572f3cee35fe, 520f7eb892f5
- **judge-infra** (0): none

### D3=0.25 partial panel

- **right-area-wrong-mechanism** (7): f897d46870ba, 90846b586c51, 294c169f6d66, fbf0ffad627b, fe57a498f6a5, ccd30cd252bc, 7d938d2db472

## D1 FP Analysis

| Prefix | Supported | Cap | Archetype | D6 |
|--------|-----------|-----|-----------|-----|
| 95b7f1d29a5e | 5 | False | False | 0.75 |
| e411dd666604 | 4 | False | False | 0.75 |
| e135c0b20794 | 3 | False | False | 0.75 |
| 59830ca772df | 6 | False | False | 0.75 |
| 78f3c6b36a03 | 7 | False | True | 0.75 |
| bb8a6eea52cb | 4 | False | False | 0.75 |
| b5bdecd350ec | 5 | False | False | 0.75 |
| f548bfffbdcd | 4 | False | False | 0.75 |

Pattern: single-SUPPORTED→HIGH rule on defensive/refactor/UI commits.

## D2 Gap

- Mean Jaccard (buggy): **0.381**
- Localization dilution count: **8**
- Fix-chain subset D2: **0.381**

## Priority Matrix

| Rank | Dimension | Intervention | Expected Δ | Task |
|------|-----------|--------------|------------|------|
| 1 | D3 | contrastive hypothesis + primary mechanism selection | +0.08 to +0.12 on D3 | v2-d3-contrastive-hypothesis |
| 2 | D1 | risk_policy: ≥2 SUPPORTED OR defect_signal for HIGH on clean archetypes | +0.08 to +0.10 on D1 | v2-fp-risk-tightening |
| 3 | D2 | SUPPORTED-only localization + defect-signal file ranking | +0.05 to +0.08 on D2 | v2-d2-localization-precision |
| 4 | D3 | EXP-BUNDLE-EXPAND (test adjacency + blame) on failure subset | +0.04 to +0.08 on D3 hard commits | EXP-BUNDLE-EXPAND |
| 5 | D1 | stricter SUPPORTED verification in evidence_tagger | +0.03 to +0.05 on D1 | v2-evidence-strict-supported |

## Hard-Commit Panel

### Tier 1 — D3=0

- 2213f71944ae
- 2bb65c78633e
- 520f7eb892f5
- 55dcbe801e76
- 572f3cee35fe
- 8780086dbe23
- a8407f8aa67e
- ad6a796f4c61
- dd6f9ba60fb4
- ea21180b1315
- eba3689dd12a

### Tier 2 — D3=0.25

- 294c169f6d66
- 7d938d2db472
- 90846b586c51
- ccd30cd252bc
- f897d46870ba
- fbf0ffad627b
- fe57a498f6a5
