---
id: npe_null_check_removal
scope: general
project: ""
triggers: [NullPointerException, NPE, "null pointer"]
source: trace-derived
trace_ref: GROOVY-8298
---

# NPE from Null-Check Removal

When JIRA mentions NullPointerException or NPE, prioritize CandidateSet commits
whose diffs remove null-check guards in stack-trace file paths. Rank those commits
first in the examination plan. Do not search the full repo — examine pre-retrieved
candidates only.
