# Portable HTML reports

The HTML report is a self-contained view of a ReviewBudget analysis. Its CSS,
JavaScript, and report data are embedded in one file. It needs no server, account,
remote assets, or network connection. The Python interfaces are
`reviewbudget.reports.render_html(report)` and
`reviewbudget.reports.render_demo(reports)`; both return the complete document as
a string. Pass analysis output, rather than raw pull request snapshots.

The viewer displays the planner's check decisions. It does not contain a second
risk engine, change verification tiers, execute checks, or recalculate an
allocation. The demo chooser compares planner-produced examples and explicitly
labels all built-in scenarios as synthetic.

## Reading the allocation

- **Selected minutes** use the planner's complete selected-cost estimate. Unknown
  costs or unmapped required capabilities keep the total unpriced. A known
  partial sum is identified separately.
- **Full baseline** is the sum of all configured checks, including checks that
  do not apply to the change's paths. Both amounts are declared estimates, not
  measurements of runtime or demonstrated savings. Adding check minutes does
  not predict elapsed time when checks run concurrently.
- **Required checks** follow the planner's verification floor and explicit
  repository policy. A smaller budget does not remove required checks.
- **Deferred checks** are optional under the model. All existing branch
  protection and repository requirements still apply.
- **Evidence present** means the planner detected the evidence in its inputs.
  Description evidence is self-reported, and changed tests do not demonstrate
  coverage or passing results.

Expand a check for its decision reasons, covered capabilities, and matched paths.
Use the check filters to inspect selected, deferred, or inapplicable work. The
source section preserves commit identities, policy fingerprint, provenance,
input completeness, and the planner's limitations.

## Opening a local report

Use **Open a JSON report**, or drop one analysis report onto the import panel.
The file must use schema version 1 and be no larger than 2 MiB. Collection sizes,
nesting, numeric values, and the rendered field shapes are validated before
replacing the displayed report. A validation error leaves the previous report
visible. Raw PR snapshots and queue allocation reports are not supported by this
viewer.

Imported reports are marked as unverified. Content and provenance come from the
file; the viewer does not authenticate a report or verify its claims against a
repository. Imported data remains in the tab's memory and is not uploaded or
saved to persistent browser storage. Share HTML and JSON reports carefully:
repository names, file paths, commit identities, and policy details can be private.

## Security and accessibility

Report values enter the page through text nodes. Embedded JSON escapes HTML
script delimiters, and HTML titles and fallback content are escaped separately.
A hash-based Content Security Policy allows only the exact embedded script and
stylesheet, disables network connections, and blocks injected inline scripts.

Controls support keyboard navigation with visible focus indicators. The layout
adapts to narrow screens, honors reduced-motion preferences, and includes a
minimal text fallback when JavaScript is disabled. Interactive details and
imports require a modern browser with JavaScript enabled.
