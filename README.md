# ReviewBudget

Allocate verification effort across pull requests.

ReviewBudget turns changed files and contribution evidence into an explainable verification plan. Use it to identify changes that need integration tests, security review, or missing supporting evidence before spending more review time.

The first release is **advisory**. It does not execute contribution code, approve changes, modify branch protection, invoke review services, or skip existing required checks. Scores are transparent heuristics awaiting validation against historical data.

```text
ReviewBudget

example/service #42
Verification: Tier 3 — intensive
Structural risk: high
Evidence debt: high

Recommended checks:
- format and lint
- unit tests
- comprehensive review
- integration tests
- e2e tests
- security review
- human review
```

## Try it locally

Python 3.11 or later is required. There are no runtime package dependencies.

```bash
git clone https://github.com/K-kiron/ReviewBudget.git
cd ReviewBudget
git switch dev
# Before the initial release is merged, check out its pull request branch.
python -m venv .venv
```

Activate the environment (`.venv\Scripts\Activate.ps1` on Windows or `source .venv/bin/activate` on Linux/macOS), then:

```bash
python -m pip install .
reviewbudget analyze --input examples/documentation.json
reviewbudget analyze --input examples/auth-change.json --format markdown
reviewbudget analyze --input examples/auth-change.json --format json --output reviewbudget-report.json
```

The examples are synthetic. They demonstrate behavior, not measured effectiveness. The package is not yet published to a package registry.

Analyze a live pull request:

```bash
reviewbudget analyze https://github.com/OWNER/REPO/pull/123
# Equivalent shorthand:
reviewbudget OWNER/REPO#123
```

Set `GITHUB_TOKEN` or `GH_TOKEN` for private repositories or higher API limits. Only repository pull-request read access is needed. Public requests work without a token, subject to GitHub's unauthenticated rate limit. Tokens are never command-line arguments or report fields.

## What the plan means

| Tier | Recommended verification |
| --- | --- |
| 0 · deterministic | Format and lint checks for complete, low-risk documentation changes |
| 1 · light | Deterministic checks, unit tests, and light review |
| 2 · comprehensive | Deterministic checks, unit tests, comprehensive review, and integration tests |
| 3 · intensive | Tier 2 plus end-to-end tests, security review, and human review |

Three separate outputs explain the recommendation:

- **Structural risk** reflects change size and paths associated with security, workflows, dependencies, public interfaces, schema changes, or configuration.
- **Evidence debt** lists missing scope, verification, test changes, reproduction, dependency rationale, migration, and rollout evidence when relevant.
- **Review burden score** combines those signals for ranking. It is not a defect probability, review-time estimate, or currency amount.

Security and workflow changes always receive Tier 3. Dependency, interface, migration, and configuration changes receive at least Tier 2. Incomplete file lists, missing patches, missing commit identities, and empty diffs receive at least Tier 2. Renames retain the sensitivity of both paths. Removing or shrinking tests also sets a Tier 2 floor.

Descriptions are self-reported evidence. A changed test file does not prove coverage or a successful run. Path matching identifies potential interface changes; it does not parse language semantics. English Markdown headings such as `Summary`, `Testing`, `Reproduction`, `Migration`, and `Rollout` are currently recognized. Comments, unchecked checkboxes, and common placeholders do not count. Non-English or unusual descriptions may need manual assessment.

The initial structural score starts at 10 for non-documentation changes, adds a logarithmic size contribution capped at 30, then adds category weights: security/workflow 55 each, migration 35, public interface/dependencies 25 each, configuration 20. Test reduction adds 15 and incomplete input adds 35. Scores cap at 100. Each missing evidence item contributes 15 debt points; 40% of debt is added to the structural score for ranking. Complete documentation changes start at zero. These are provisional policy choices, not learned estimates.

## Repository policy

Policy is loaded only when explicitly specified:

```bash
reviewbudget analyze --input examples/auth-change.json --policy examples/policy.toml
```

```toml
thresholds = [20, 45, 70]
sensitive_paths = ["src/payments/*", "lib/permissions.py"]
```

Patterns use case-insensitive Python `fnmatch` against the whole POSIX path; `*` can match `/`. Custom paths add sensitivity. Thresholds change score routing, but cannot remove the built-in safety floors. Unknown policy keys fail validation. The JSON report records a normalized policy hash alongside the analyzed base and head commits.

## GitHub Action

Pin the Action to a reviewed full commit SHA. Replace `REVIEWED_COMMIT_SHA` below after selecting the version to use. No repository checkout is needed with the default policy.

```yaml
name: Verification plan
on:
  pull_request:
permissions:
  contents: read
  pull-requests: read
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: K-kiron/ReviewBudget@REVIEWED_COMMIT_SHA
        id: budget
        with:
          token: ${{ github.token }}
```

The Action writes a job summary and scalar outputs: `tier`, `full-ci-required`, `human-review-required`, `security-review-required`, `input-complete`, `review-cost-score`, and `allow-skip-required-checks` (always `false`). Boolean outputs are the strings `true` and `false`. The Action supplies Python 3.11 and imports its package in isolated mode from its own trusted directory.

Keep existing CI enabled while evaluating recommendations. If input retrieval or validation fails, the step fails and emits conservative Tier 3 outputs. A stale event also fails; rerun on the current PR commits. An incomplete but valid snapshot still produces a conservative report.

For an explicit `policy` input, provide a trusted TOML file. If using `pull_request_target`, keep the workflow and any checkout on a trusted base revision. Never check out or execute the contribution's head in a privileged job. The [GitHub secure-use reference](https://docs.github.com/en/actions/reference/security/secure-use) explains that trust boundary.

## Test the hypothesis

Collect closed pull requests into a local JSONL file:

```bash
reviewbudget collect OWNER/REPO --limit 100 --output history.local.jsonl
reviewbudget evaluate --input history.local.jsonl --output reviewbudget-report.json
# Run the evaluator offline with explicitly synthetic data:
reviewbudget evaluate --input examples/history.jsonl
```

Collection uses read-only metadata, files, and review endpoints. It captures current **final-state** data from recently updated closed PRs, never an invented opening-time snapshot. Files and reviews are paginated; the [GitHub files endpoint](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests-files) has a 3,000-file cap, which is preserved as incomplete input. Each PR needs multiple API requests; the collector stops on API errors instead of writing a partial dataset. Keep collected private repository data local.

Evaluation uses `review_count + 2 × changes_requested + review_comments` as an observed activity proxy, with labels above the training 80th percentile. Submitted review events are not unique reviewers or proven review rounds. All repositories share a chronological creation-time split; training outcomes that end after the holdout boundary are excluded. Label and diagnostic ranking thresholds come only from training data.

The report compares the planner against `changed lines + changed files`, including expected precision at the top 20% with fractional tie handling, actual Tier 2/3 recall, and actual Tier 0/1 share. The research targets are:

- At least 85% recall of high-burden PRs in deeper verification tiers.
- At least 30% of PRs assigned to Tier 0/1.
- At least 15% relative precision lift over the change-size baseline.

These are project investment thresholds. They are not industry benchmarks. Synthetic, final-state, temporally unverified, incomplete, small, or malformed datasets remain exploratory. Eligibility requires at least 300 evaluated records across three repositories represented in both partitions, enough positive and negative examples, and consistent non-synthetic snapshots captured before review. See [the data format](docs/data-format.md) for provenance fields. Even an eligible passing result does not establish significance, defect prevention, or financial savings.

Repository-specific learned routing, semantic claim-to-diff verification, prospective evaluation, and cost simulation are not implemented in this initial release. Repository policy is configurable; routing has not been calibrated from historical outcomes. The next decision is whether verified pre-review data supports the hypothesis beyond the size baseline.

## Development

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pip install build
python -m build
```

CI builds and installs the wheel, runs behavior tests, and checks the offline examples on Windows and Linux with Python 3.11 and 3.13. Runtime code uses only the standard library. See [SECURITY.md](SECURITY.md) for the input and execution boundaries.

MIT licensed.
