---
id: npe_null_check_removal
scope: general
project: ""
triggers: [NullPointerException, NPE, "null pointer"]
source: trace-derived
trace_ref: GROOVY-8298
---

# NPE from Null Check Removal

Prioritize CandidateSet commits whose diffs remove null-check guards in
stack-trace file paths. Rank those commits first in the examination plan.
The agent examines pre-retrieved candidates only — no repo-wide search.
