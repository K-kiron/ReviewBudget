# GitHub Actions integration

Start in advisory mode. ReviewBudget emits named check IDs and a job summary; it never runs your checks. Keep existing required jobs unconditional. Policy mappings declare capability intent, not actual test coverage.

Pin the Action to a reviewed full commit SHA. With no policy, no checkout is needed and only capability recommendations are emitted. To load repository configuration, check out the trusted base revision separately:

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
    outputs:
      selected: ${{ steps.budget.outputs.selected-checks }}
      complete: ${{ steps.budget.outputs.coverage-complete }}
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          persist-credentials: false
      - uses: K-kiron/ReviewBudget@REVIEWED_COMMIT_SHA
        id: budget
        with:
          policy: .reviewbudget.toml
          artifact-directory: ${{ runner.temp }}/reviewbudget
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: verification-plan
          path: |
            ${{ runner.temp }}/reviewbudget/report.json
            ${{ runner.temp }}/reviewbudget/report.html
```

Create and merge the trusted policy first. The example deliberately uploads only reports. `snapshot.json` also exists in the configured directory and contains raw contribution data; preserve it in appropriate private artifact storage if collecting prospective evidence. It may include private patches and descriptions.

After validating a policy against your repository, an **additional optional** job can use:

```yaml
  optional-integration:
    needs: plan
    if: >-
      always() && !cancelled() &&
      (needs.plan.result != 'success' ||
       needs.plan.outputs.complete != 'true' ||
       contains(fromJSON(needs.plan.outputs.selected || '[]'), 'integration'))
    runs-on: ubuntu-latest
    steps:
      # Keep your existing checkout, dependency setup, and test invocation here.
      - run: echo "Replace with the existing optional integration job steps"
```

This is a condition recipe, not an executable integration suite. The check ID must match your policy. Planning failure or incomplete capability mapping takes the conservative branch. Do not attach this condition to an existing required job or allow it to redefine required branch-protection checks.

## Outputs

| Output | Meaning |
| --- | --- |
| `selected-checks` | JSON array of configured check IDs; use `fromJSON`, not string interpolation into shell code |
| `coverage-complete` | Complete input and no unmapped required capabilities; does not attest test coverage |
| `budget-status` | Accounting state, including overruns, unknown prices, or missing mappings |
| `estimated-minutes` | Sum of declared selected costs, blank if unknown |
| `budget-shortfall` | Known mandatory cost above the limit, blank without a limit |
| `tier` | Verification floor from 0 to 3 |
| `full-ci-required`, `human-review-required`, `security-review-required` | Advisory capability requirements |
| `input-complete` | Required snapshot metadata, files, and patches available |
| `review-cost-score` | Uncalibrated ranking score, not minutes |
| `allow-skip-required-checks` | Always `false` |

Boolean outputs are literal `true` or `false` strings. On errors, the step fails, reports Tier 3, sets completeness false, and emits no selected-check list. Never interpret an empty list from a failed job as permission to skip work.

The Action uses Python isolated mode and reads files from its own trusted directory. It checks the captured head/base against the event. It uses read-only GitHub endpoints and requires only repository pull-request read access. For `pull_request_target`, keep all checkout and execution on trusted code; never run the contribution's head in a privileged job. See GitHub's [secure use reference](https://docs.github.com/en/actions/reference/security/secure-use).
