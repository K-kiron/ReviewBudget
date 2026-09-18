# Public-data evaluation: what the current rules establish

**The current rules do not outperform the change-size baseline on aggregate precision in this exploratory cohort.** The public preview is useful as a configurable verification planner and budget accounting tool. It is not validated as a superior predictor of review burden, defects, or time saved.

On 2026-09-18, the collector captured 300 real public PRs: 100 each from [Flask](https://github.com/pallets/flask), [Requests](https://github.com/psf/requests), and [GitHub CLI](https://github.com/cli/cli). Each cohort consists of the most recently updated closed PRs at collection start. Identities were frozen before fetching individual records; interrupted collection resumed the same cohort. Repositories were selected for distinct Python/web/CLI maintenance patterns, not sampled randomly.

[Machine-readable results](evaluation/aggregate.json) · [PR identities and original dataset hashes](evaluation/cohort.json)

## Results

The label is observed activity: `review_count + 2 × changes_requested + review_comments`, strictly above the training 80th percentile (2 in this cohort). These are submitted review events, not unique reviewers, reviewer minutes, or defects. Routing and scoring remained fixed; this sample was not used to tune them.

| Held-out measure | ReviewBudget | Change-size baseline |
| --- | ---: | ---: |
| Precision among top 20% (18 PRs) | 66.7% | 66.7% |
| Recall among top 20% | 52.2% | 52.2% |
| Recall at training-fitted diagnostic cutoff | 82.6% | 82.6% |
| Relative top-20 precision lift | 0% | Reference |

Actual Tier 2/3 routing recalls **69.6%** of the high-burden holdout PRs. **50.0%** of holdout PRs fall in Tier 0/1. The training-fitted score cutoff is only a diagnostic comparison and does not alter actual routing.

The project screening targets are at least 85% high-burden routing recall, at least 30% Tier 0/1 share, and at least 15% relative precision lift. Only the Tier 0/1 share target passes here. These are project research criteria, not industry benchmarks; lower-tier share is not a savings measurement.

## Why this remains exploratory

- The API provides **final-state** snapshots. Description, tests, and diff contents may already reflect review feedback. They cannot stand in for original pre-review features.
- Of 300 inputs, one was excluded because its first submitted review was after the completed outcome. Another six training candidates ended after the common holdout boundary and were excluded to avoid temporal leakage. The final split is 203 training and 90 holdout records, below the 300-evaluated-record eligibility requirement.
- The global holdout starts at `2026-08-03T16:54:57Z`. Creation times define one shared chronological boundary; equal times stay together. Training outcomes must end before that boundary.
- All 23 high-burden holdout examples are from GitHub CLI. Flask and Requests have zero positives under the common threshold, so their recall is undefined. Per-repository differences do not establish cross-repository generalization.
- Recently updated closed PRs are a convenience cohort. It includes varied ages and workflows, and may overrepresent dependency maintenance or review styles specific to these repositories.
- Missing review times and incomplete patches remain limitations. Ties at the top-20 boundary use fractional expected inclusion. No significance test or causal inference is claimed.

The machine-readable verdict is `unvalidated`, evidence is `exploratory`, and `product_claims_supported` is `false`. Publishing the unsuccessful checks is deliberate: the budget feature should stand on transparent behavior rather than unsupported prediction claims.

## Reproduce the procedure

Install the candidate package and run from a checkout:

```bash
python scripts/evaluate_cohort.py --auth gh --output ../reviewbudget-cohort-local
```

This requests the exact published PR identities using read-only GitHub APIs, preserves checkpoints, and writes raw snapshots and aggregate results only to the selected local directory. Use an output outside version control. The original raw dataset hashes identify this run; raw descriptions and patches are not republished in the repository.

GitHub's current state and capture timestamps can change, so recollection is **not a byte-identical archive** and may produce different metrics. To reproduce an exact result, retain the original JSONL and checkpoint manifest locally, then run:

```bash
reviewbudget evaluate --input pallets--flask.jsonl --input psf--requests.jsonl --input cli--cli.jsonl --output evaluation.json
```

## Prospective evaluation

Create a snapshot directory. Call `capture` before review and preserve that file; it refuses to overwrite an existing observation. Later collect completed outcomes and evaluate with `--snapshots` pointing to the originals. The Action can also save a snapshot artifact at planning time. Do not expose private snapshots in public artifacts.

Capture marks an observation `pre_review` only if the PR is open and no submitted review is observed. The evaluator independently checks capture/update/review/outcome ordering. Caller-supplied provenance is not cryptographic attestation. Eligible evidence still requires enough evaluated records, multiple repositories in both partitions, and sufficient positive and negative examples. Even passing those gates would not establish measured savings or defect prevention.

See [data-format.md](data-format.md) for the exact evidence fields and [policy.md](policy.md) for declared-cost accounting, which is separate from this activity-proxy evaluation.
