# Datasets — ApacheJIT

Primary data source for commit-risk investigation and five-dimension evaluation.

## Active Data Source: ApacheJIT

ApacheJIT (McIntosh et al., MSR 2021) provides labeled commits from 15 Apache projects with numeric change metrics and a replication package linking buggy commits to fixes and JIRA issues.

| Split | Rows | Period |
|-------|------|--------|
| `apachejit_train.csv` | 44,834 | 2003–2016 |
| `apachejit_test_large.csv` | 30,111 | 2017–2019 |
| `apachejit_test_small.csv` | 7,526 | — |

**Download:** run `./scripts/download_apachejit.sh` (defaults to `data/apachejit/`). The script fetches the Zenodo replication zip and extracts the three split CSVs.

**Local path:** `data/apachejit/` (gitignored — download locally)

### CSV Features

Each row includes commit hash, project, buggy label, and numeric features: LA, LD, NF, ND, NS, ENT, NDEV, AGE, NUC, AEXP, AREXP, ASEXP (and related author/commit metrics).

## Ground Truth Chain

The replication package (`apachejit_dataset_replication.zip`) contains linkage files enabling a full oracle chain:

```
bug_hash → fix_hash → issue_key → JIRA metadata
```

| File pattern | Contents |
|--------------|----------|
| `commit_links_{PROJECT}.csv` | Maps **fix_hash** (fixing commit) to **bug_hash** (bug-inducing commit) |
| `{PROJECT}.csv` | Maps commit IDs to **issue_key** (JIRA) |

Example projects: `camel`, `hadoop`, and 13 others. V1 investigation uses **Camel** and **Hadoop** git clones; eval can sample across all projects present in the CSVs.

### Chain Statistics

- ~28,239 buggy commits across 15 projects
- Replication package claim: 100% chain coverage for positives — **must be verified** by feat-2 coverage report before eval claims

### JIRA Metadata (eval-only)

Public Apache JIRA API provides issue summary, description, priority, components, and resolution for eval dimensions D3–D5. Fetched at eval time with disk cache — **never** injected into agent investigation context.

## Local Git Repositories

V1 clones (feat-3):

| Project | Role |
|---------|------|
| Camel | Largest commit volume (~14K commits in dataset) |
| Hadoop | Second-project generalization |

Clones cached under `data/repos/`. Shallow clones are insufficient for file history; plan for ~2–5 GB disk.

## Data Layout

```
data/
├── apachejit/
│   ├── apachejit_train.csv
│   ├── apachejit_test_large.csv
│   ├── apachejit_test_small.csv
│   └── apachejit_dataset_replication.zip
└── repos/                  # feat-3: local git clones (gitignored)
    ├── camel/
    └── hadoop/
```

## Related Documents

- [Experiment context](experiment-context.md) — why ApacheJIT enables five-dimension eval
- [Evaluation](evaluation.md) — how ground truth feeds D1–D5 scoring
