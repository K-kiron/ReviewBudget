# Repository checks and verification budgets

A check policy connects the planner's recommended capabilities to the work your repository actually performs. Each check has a stable ID, a display name, applicable paths, and an optional cost estimate. ReviewBudget returns decisions and explanations; it does not execute checks or change branch protection.

Start with the complete [example policy](../examples/budget-policy.toml):

```bash
reviewbudget analyze --input examples/auth-change.json --policy examples/budget-policy.toml --format json
```

The example costs are illustrative estimates. Replace its check names, capability mappings, and estimates with your repository's verification work before using its numbers for planning. Include checks required by branch protection and other repository rules with `required = true`; ReviewBudget does not discover those settings.

## Policy schema

Policies use TOML. Only `thresholds`, `sensitive_paths`, `checks`, and `budget` are accepted at the top level. Unknown keys, including nested keys, are errors.

```toml
thresholds = [20, 45, 70]
sensitive_paths = ["src/payments/*"]

[budget]
limit_minutes = 30

[[checks]]
id = "unit"
name = "Unit tests: python -m unittest discover -s tests"
covers = ["unit_tests"]
paths = ["src/*", "tests/*", "pyproject.toml"]
estimated_minutes = 6
required = false
priority = 80
```

| Field | Meaning and validation |
| --- | --- |
| `thresholds` | Three strictly increasing integers in `1..100`; defaults to `[20, 45, 70]`. Existing safety floors still apply. |
| `sensitive_paths` | Additional security-sensitive path patterns; defaults to `[]`. At most 100 patterns. |
| `checks` | At most 500 check tables. Defaults to `[]`; no repository checks or prices are invented. |
| `budget.limit_minutes` | Optional finite, nonnegative number. Omitting it selects mandatory work and leaves optional upgrades deferred. |
| `checks.id` | Required, unique stable ID: 1–80 lowercase letters, digits, dots, underscores, or hyphens, beginning with a letter or digit. |
| `checks.name` | Required display text, up to 200 characters without control characters. A command in a name is display text only. |
| `checks.covers` | Required list of recognized capability IDs below. May be empty for a repository-specific check. Multiple capabilities may map to one check and its cost is counted once. |
| `checks.paths` | Nonempty list of at most 100 path patterns, default `['*']`. Both the new path and previous path of a rename are considered. |
| `checks.estimated_minutes` | Optional finite, nonnegative number. Omit when unknown. Zero must be declared explicitly; missing costs are never treated as free work. |
| `checks.required` | Boolean, default `false`. `true` retains the check unconditionally, including when its paths do not match or the budget is insufficient. |
| `checks.priority` | Integer in `0..1000000`, default `0`. Larger values prioritize optional upgrades. It cannot remove mandatory work. |

Patterns use case-insensitive Python `fnmatch` against the whole relative POSIX path. A `*` may match `/`; a pattern such as `src/**` does not imply Git-style glob semantics. Patterns may not contain traversal, backslashes, drive prefixes, empty path segments, or control characters. Each pattern is at most 512 characters. Existing policies containing only `thresholds` and `sensitive_paths` continue to work.

The normalized Python/JSON representation uses `null`/`None` for omitted estimates and an omitted budget limit. TOML has no null value: omit the key. Numeric booleans, negative costs, NaN, infinity, duplicate IDs, and unknown capability IDs are rejected. Cost totals that cannot be represented as finite numbers are also rejected.

## Capabilities and safety floors

| Capability ID | Recommended by the built-in tiers |
| --- | --- |
| `format_and_lint` | Every tier |
| `unit_tests` | Tier 1 and above |
| `light_review` | Tier 1 |
| `comprehensive_review` | Tiers 2 and 3 |
| `integration_tests` | Tiers 2 and 3 |
| `e2e_tests` | Tier 3 |
| `security_review` | Tier 3 |
| `human_review` | Tier 3; available as an optional upgrade below Tier 3 |

A check is mandatory if it is explicitly required, if the input is incomplete, or if it applies to the changed paths and covers a recommended capability. Every applicable check covering a recommended capability is retained. If two separately configured checks cover the same recommended capability, both are retained and both costs count. Put multiple capability IDs in a single check when they describe one piece of work.

Every recommended capability must be covered by a selected check or appear in `budget.unmapped_capabilities`. Path restrictions can therefore expose a coverage gap. A security check restricted to `src/auth/*`, for example, does not cover a security recommendation for a workflow change. Broaden its paths or add a relevant check; the planner never substitutes a weaker tier to conceal the gap.

Incomplete file lists, missing patches or commit identities, and empty diffs select **every configured check**, regardless of its path filters. This preserves conservative behavior when the observed paths cannot establish which checks apply. Unmapped capabilities remain visible even in this case.

Budget limits never lower the verification tier, drop mandatory checks, or authorize skipping existing required checks. The report always keeps `allow_skip_required_checks = false`. Configuration mappings express the maintainer's intended coverage; they do not prove that a check actually tests a capability or that it has passed.

## Allocation rules

Mandatory work is selected first. Its known costs count against the budget even when they exceed it. The report exposes the shortfall and keeps the work selected. With missing mandatory estimates or unmapped capabilities, the report cannot establish the remaining budget, so optional upgrades stay deferred.

When an explicit budget remains, applicable optional checks are considered by descending priority, ascending declared cost, then stable check ID. The allocator admits each priced check that fits and proceeds to the next candidate when one is too expensive. Unknown-cost optional checks stay deferred. This is a deterministic ranking policy, not a claim of mathematically optimal benefit or measured risk reduction.

A queue shares one budget across analyzed pull requests:

```python
from reviewbudget.budget import allocate
from reviewbudget.planner import analyze

reports = [analyze(snapshot, policy) for snapshot in snapshots]
allocation = allocate(reports, budget_minutes=90)
```

Queue allocation preserves every report's mandatory work, then ranks optional upgrades by descending check priority, descending `review_cost_score`, ascending cost, repository/PR identity, and check ID. It recomputes optional selections against the queue limit, replacing any earlier per-report budget selection. A check is counted separately for each PR; no shared execution, caching, or parallel runtime is assumed. Duplicate repository/PR identities are errors. Reports are copied, not mutated, and output ordering is deterministic.

`allocation.reports` contains the updated decisions. Its per-report budgets have `scope = 'queue_share'` and `queue_limit_minutes`; their `limit_minutes` is null because the budget belongs to the queue. Use `allocation.budget` to assess the aggregate fit or shortfall. Queue check IDs and capability gaps are qualified as `OWNER/REPO#NUMBER/ID`.

## Reading the report

Each entry in `checks` contains the normalized policy fields, `matched_paths`, `applicable`, `mandatory`, `selected`, a `status` (`selected`, `deferred`, or `not_applicable`), and human-readable `reasons`. Explicitly required checks may have no matched paths. `mandatory` includes built-in safety decisions, whereas `required` records the user's policy flag.

| Budget field | Interpretation |
| --- | --- |
| `limit_minutes` | Declared limit, or null when none applies to this report. |
| `mandatory_known_minutes` | Sum of known mandatory costs; a lower bound when estimates are missing. |
| `selected_known_minutes` | Sum of known selected costs; also a lower bound when coverage or estimates are incomplete. |
| `optional_selected_known_minutes` | Known cost of selected optional upgrades. |
| `estimated_minutes` | Total selected estimate, or null if selected work is unpriced, capabilities are unmapped, or checks are unconfigured. |
| `shortfall_minutes` | Nonnegative excess of known selected cost over the limit; null without a limit. With unknown work, this is only a lower bound. |
| `remaining_minutes` | Nonnegative remaining budget; null without a limit or when selected cost is unknown. |
| `unpriced_checks` | Selected check IDs that need estimates. |
| `unpriced_configured_checks` | All check IDs with missing estimates, including deferred and path-inapplicable checks. |
| `unmapped_capabilities` | Recommended capabilities without selected coverage. |
| `within_budget` | True only for a complete selected estimate that fits; false for a known shortfall; otherwise null. |
| `full_baseline_minutes` | Sum of **all configured checks**, including path-inapplicable checks. Null if any configured cost is unknown or a report has no configured checks. |
| `baseline_known_minutes` | Known portion of that full configured baseline. |
| `modeled_savings_minutes` | Full configured baseline minus selected estimate; null if either total is unknown. |

`budget.status` is `not_configured`, `over_budget`, `unpriced`, `no_limit`, or `within_budget`. A legacy policy without named checks reports `not_configured`; it still returns the original heuristic plan. No configuration failure or cheaper recommendation is inferred from that status.

The full configured baseline is an explicit hypothetical comparison, **not a measurement of the repository's previous CI**. Differences between it and the selected estimate are modeled minutes, not observed time or monetary savings. Estimates may mix human effort with job runtimes and are added sequentially; they do not predict elapsed wall-clock duration. The report records `estimate_basis` and `baseline_basis` alongside these figures.

The existing structural risk, evidence debt, tier, and recommended capability fields are retained. Reports also include title, safe file metadata, change counts, provenance labels, and fingerprints without copying raw patches or descriptions. `policy_hash` hashes the normalized policy, and `report_fingerprint` hashes the report's content excluding the fingerprint itself. These identify reproducible outputs; they do not authenticate supplied provenance or establish that any check ran.
