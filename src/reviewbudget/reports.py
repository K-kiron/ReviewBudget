"""Portable, offline HTML views of planner output; no second allocation engine."""

from __future__ import annotations

import base64
import hashlib
import html
from importlib.resources import files
import json


def _digest(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode("ascii")


def _identity(report: dict) -> str:
    provenance = report.get("provenance") or {}
    source = provenance.get("source") if isinstance(provenance, dict) else None
    if (source or report.get("source")) == "local_git":
        base = str(report.get("base_sha") or "unknown")[:8]
        head = str(report.get("head_sha") or "unknown")[:8]
        return f"{report['repository']} / local diff · base {base} / head {head}"
    return f"{report['repository']} / PR #{report['number']}"


def _document(reports: list[dict], *, demo: bool, collection: bool = False,
              queue: dict | None = None) -> str:
    if not isinstance(reports, list) or not reports or len(reports) > 30:
        raise ValueError("HTML reports require between 1 and 30 analysis reports.")
    for report in reports:
        if (not isinstance(report, dict) or report.get("schema_version") != 1
                or not isinstance(report.get("repository"), str)
                or type(report.get("number")) is not int
                or type(report.get("tier")) is not int
                or report["tier"] not in range(4)):
            raise ValueError("Expected a schema_version 1 ReviewBudget analysis report.")
    payload = {"demo": demo, "reports": reports}
    if collection:
        payload["collection"] = True
    if queue is not None:
        payload["queue"] = {key: value for key, value in queue.items() if key != "reports"}
    data = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Embedded report data exceeds the 10 MiB limit.")
    # HTML's script tokenizer recognizes closing tags even in JSON strings.
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    assets = files("reviewbudget").joinpath("assets")
    css = assets.joinpath("report.css").read_text(encoding="utf-8")
    script = assets.joinpath("report.js").read_text(encoding="utf-8")
    csp = ("default-src 'none'; base-uri 'none'; form-action 'none'; "
           "connect-src 'none'; object-src 'none'; "
           f"style-src 'sha256-{_digest(css)}'; "
           f"script-src 'sha256-{_digest(script)}' 'sha256-{_digest(data)}'")
    first = reports[0]
    title = html.escape(str(first.get("title") or _identity(first)))
    identity = html.escape(_identity(first))
    fallback = html.escape(", ".join(str(item).replace("_", " ") for item in first.get("plan", {}).get("required", [])))
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}">
<meta name="color-scheme" content="light">
<meta name="description" content="A local, explainable verification budget for a pull request. Inspect the checks, costs, and evidence behind each decision.">
<title>ReviewBudget — {title}</title>
<style>{css}</style>
</head>
<body>
<a class="skip-link" href="#report">Skip to report</a>
<header class="masthead">
  <a class="wordmark" href="#top" aria-label="ReviewBudget home"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>ReviewBudget<span class="version">/ 01</span></a>
  <div class="masthead-right"><span class="local-label"><span aria-hidden="true">●</span> Local by design</span><a class="text-link" href="https://github.com/K-kiron/ReviewBudget" rel="noreferrer">Source <span aria-hidden="true">↗</span></a></div>
</header>
<main id="top">
  <section class="intro" aria-labelledby="intro-title">
    <div><p class="eyebrow">THE VERIFICATION BUDGET</p><h1 id="intro-title">Spend review effort<br>where it matters.</h1><p class="intro-copy">Turn a pull request into an explicit check plan.<br>See what is selected, what is deferred, and why.</p></div>
    <div class="import-card" id="drop-zone"><p class="eyebrow">BRING YOUR OWN CHANGE</p><label class="button" for="report-file">Open a JSON report <span aria-hidden="true">↗</span></label><input id="report-file" type="file" accept=".json,application/json" aria-describedby="import-help import-status"><p id="import-help">Or drop one here. Up to 2 MiB.<br>Read in this browser. Never uploaded.</p><p id="import-status" role="status" aria-live="polite"></p><details class="import-instructions"><summary>How to generate a report</summary><code>reviewbudget analyze --input snapshot.json --policy policy.toml --format json --output report.json</code><p>Open an analysis report, not a raw PR snapshot or queue report.</p></details></div>
  </section>
  <section id="queue-summary" class="queue-summary" aria-label="Shared queue budget" hidden></section>
  <section id="scenario-section" class="scenario-section" aria-label="Report selection" hidden><div class="section-line"><span class="eyebrow" id="selector-title">INSPECT A CHANGE</span><span class="quiet" id="selector-description"></span></div><div id="scenario-tabs" class="scenario-tabs" role="group" aria-label="Choose a report"></div></section>
  <p id="notice" class="notice"></p>
  <article id="report" tabindex="-1" aria-label="Verification budget report"></article>
  <noscript><section class="noscript"><h2>{title}</h2><p>{identity} · Verification tier {first['tier']}</p><p>Required capabilities: {fallback}.</p><p>Enable JavaScript to inspect allocation details, switch reports, or open a local report. All code is embedded in this file and works offline.</p></section></noscript>
  <details class="method"><summary>What this report can tell you</summary><div><p>ReviewBudget allocates verification effort using explicit rules and declared check costs. Risk and evidence scores are uncalibrated heuristics. They are not defect probabilities, measured runtimes, or proof that a change is safe.</p><p>A full baseline means all configured checks, including checks whose paths do not apply to this change. Cost differences are modeled allocation differences, not demonstrated savings. Estimates add check minutes; parallel execution and reviewer availability can change elapsed time.</p><p>Evidence in a description is self-reported. A changed test does not establish coverage or a passing result. Recommendations never override branch protection or authorize skipping required checks. This viewer displays planner output; it does not run checks or recalculate risk.</p></div></details>
</main>
<footer><span>ReviewBudget <span class="quiet">/ explainable verification planning</span></span><span>One file. No account. No network requests.</span></footer>
<script type="application/json" id="report-data">{data}</script>
<script>{script}</script>
</body>
</html>
'''


def render_html(report: dict) -> str:
    """Render one analysis report as a self-contained HTML document."""
    return _document([report], demo=False)


def render_demo(reports: list[dict]) -> str:
    """Render planner-produced synthetic examples with a scenario chooser."""
    return _document(reports, demo=True)


def render_collection(reports: list[dict]) -> str:
    """Render analysis reports with neutral selection and original provenance."""
    return _document(reports, demo=False, collection=True)


def render_queue(allocation: dict) -> str:
    """Render a queue allocation, preserving aggregate and per-report budgets."""
    if (not isinstance(allocation, dict) or allocation.get("schema_version") != 1
            or not isinstance(allocation.get("budget"), dict)):
        raise ValueError("Expected a schema_version 1 ReviewBudget queue allocation with a budget.")
    return _document(allocation.get("reports"), demo=False, collection=True, queue=allocation)
