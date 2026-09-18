"""Command-line and GitHub Action entry points."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

from . import __version__
from .planner import analyze, validate_policy


def _constant(value):
    raise ValueError("JSON must not contain nonfinite numeric values.")


def _json(text):
    try:
        return json.loads(text, parse_constant=_constant)
    except RecursionError:
        raise ValueError("JSON nesting exceeds the supported depth.") from None


def _read(path, limit=20_000_000):
    with Path(path).open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input file exceeds the supported size limit.")
    return data.decode("utf-8-sig")


def _policy(path):
    return validate_policy(tomllib.loads(_read(path, 100_000))) if path else None


def _safe(text):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(text))


def _markdown(text):
    return html.escape(_safe(text), quote=True).replace("|", "&#124;").replace("`", "&#96;")


def render(report, format="text"):
    if format == "json":
        return json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
    if format == "html":
        from .reports import render_html, render_queue
        if "reports" in report:
            if "budget" in report:
                return render_queue(report)
            from .reports import render_collection
            return render_collection(report["reports"])
        return render_html(report)
    if "reports" in report:
        text = "\n".join(render(item, format) for item in report["reports"])
        if "budget" in report:
            text += "\nQueue budget: " + (_markdown if format == "markdown" else _safe)(report["budget"]) + "\n"
        return text
    escape = _markdown if format == "markdown" else _safe
    heading = "## ReviewBudget" if format == "markdown" else "ReviewBudget"
    identity = (f"Local diff: base {(report.get('base_sha') or 'unknown')[:10]} / head {(report.get('head_sha') or 'unknown')[:10]}"
                if report.get("source") == "local_git" else f"{report['repository']} #{report['number']}")
    lines = [heading, "", escape(identity),
             f"Verification: Tier {report['tier']} - {report['tier_name']}",
             f"Structural risk: {report['risk']['level']} ({report['risk']['score']}/100)",
             f"Evidence debt: {report['evidence_debt']['level']} ({report['evidence_debt']['score']}/100)",
             f"Review burden score: {report['review_cost_score']}/100 (uncalibrated)", "",
             "Recommended checks:"]
    lines += [f"- {escape(check.replace('_', ' '))}" for check in report["plan"]["required"]]
    if report.get("checks"):
        lines += ["", "Repository checks:"]
        for check in report["checks"]:
            cost = check.get("estimated_minutes")
            cost = "unpriced" if cost is None else f"{cost:g} estimated min"
            lines.append(f"- {escape(check['id'])}: {check['status']} ({cost})" + (" [mandatory]" if check["mandatory"] else ""))
        budget = report["budget"]
        lines += ["", f"Budget: {budget['status']}; selected known cost: {budget['selected_known_minutes']:g} estimated min."]
        if budget.get("shortfall_minutes"):
            lines.append(f"Mandatory work exceeds the budget by {budget['shortfall_minutes']:g} estimated min. It remains selected.")
        if budget.get("unmapped_capabilities"):
            lines.append("Unmapped capabilities: " + ", ".join(escape(item) for item in budget["unmapped_capabilities"]))
    lines += ["", "Reasons:"]
    for driver in report["drivers"]:
        lines.append(f"- {escape(driver['message'])}")
        if driver["paths"]:
            lines.append("  Paths: " + ", ".join(escape(p) for p in driver["paths"][:10]))
            if len(driver["paths"]) > 10:
                lines.append(f"  ({len(driver['paths']) - 10} more paths)")
    if report["missing_evidence"]:
        lines += ["", "Missing evidence (description evidence is self-reported):"]
        lines += [f"- {escape(item.replace('_', ' '))}" for item in report["missing_evidence"]]
    if report["uncertainties"]:
        lines += ["", "Incomplete inputs:"] + [f"- {escape(item.replace('_', ' '))}" for item in report["uncertainties"]]
    lines += ["", "Advisory only. Keep all existing required checks and branch protection.",
              "Scores do not estimate defect probability, review minutes, or measured savings.", ""]
    return "\n".join(lines)


def _write(text, path):
    if not path:
        sys.stdout.write(text)
        return
    destination = Path(path)
    # Do not destroy a previous report if serialization or writing fails.
    descriptor, temporary = tempfile.mkstemp(prefix=".reviewbudget-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _parser():
    parser = argparse.ArgumentParser(prog="reviewbudget", description="Allocate pull request verification effort.")
    parser.add_argument("--version", action="version", version=f"reviewbudget {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    analysis = commands.add_parser("analyze", help="Plan verification for a snapshot or a GitHub PR URL.")
    analysis.add_argument("target", nargs="?", help="https://github.com/OWNER/REPO/pull/NUMBER")
    analysis.add_argument("--input", help="Offline pull request snapshot JSON.")
    analysis.add_argument("--policy", help="Explicit TOML repository policy.")
    analysis.add_argument("--repo", help="Local Git repository (defaults to . with --base).")
    analysis.add_argument("--base", help="Compare committed changes since the merge base with this ref.")
    analysis.add_argument("--head", default="HEAD")
    analysis.add_argument("--title", default="")
    analysis.add_argument("--body-file", help="Local description with testing and rollback evidence.")
    analysis.add_argument("--budget-minutes", type=float)
    _auth_argument(analysis)
    _output_arguments(analysis)
    init = commands.add_parser("init", help="Create a starter policy without overwriting files.")
    init.add_argument("directory", nargs="?", default=".")
    init.add_argument("--preset", choices=("python", "node"), default="python")
    demo = commands.add_parser("demo", help="Run bundled examples offline; HTML includes all scenarios.")
    demo.add_argument("--scenario", choices=("docs", "tested-code", "auth", "migration-dependency", "incomplete"))
    _output_arguments(demo)
    queue = commands.add_parser("allocate", help="Allocate one budget across saved JSON reports.")
    queue.add_argument("--input", action="append", required=True)
    queue.add_argument("--budget-minutes", type=float, required=True)
    _output_arguments(queue, "json")
    report = commands.add_parser("report", help="Render saved JSON reports as standalone HTML.")
    report.add_argument("input")
    _output_arguments(report, "html")
    capture = commands.add_parser("capture", help="Save a GitHub PR snapshot with review-time provenance.")
    capture.add_argument("target")
    capture.add_argument("--output", required=True)
    _auth_argument(capture)
    collect = commands.add_parser("collect", help="Collect final-state closed PRs for exploratory evaluation.")
    collect.add_argument("repository", help="OWNER/REPO")
    collect.add_argument("--limit", type=int, default=100)
    collect.add_argument("--output", required=True, help="Local JSONL output; may contain private repository data.")
    collect.add_argument("--resume", action="store_true", help="Continue the same checkpointed PR cohort.")
    _auth_argument(collect)
    evaluate = commands.add_parser("evaluate", help="Compare a JSONL history with a change-size baseline.")
    evaluate.add_argument("--input", action="append", required=True)
    evaluate.add_argument("--snapshots", help="Directory of original capture JSON files to join with outcomes.")
    evaluate.add_argument("--holdout-fraction", type=float, default=0.3)
    evaluate.add_argument("--output", help="Write the JSON evaluation report instead of stdout.")
    return parser


def _output_arguments(parser, default="text"):
    parser.add_argument("--format", choices=("text", "json", "markdown", "html"), default=default)
    parser.add_argument("--output", help="Write a report instead of stdout.")


def _auth_argument(parser):
    parser.add_argument("--auth", choices=("env", "gh"), default="env", help="Use GITHUB_TOKEN/GH_TOKEN or the existing GitHub CLI login.")


def _client(auth):
    from .github import GitHubClient
    if auth == "env":
        return GitHubClient()
    executable = _gh_executable()
    try:
        result = subprocess.run([str(executable), "auth", "token", "--hostname", "github.com"],
                                cwd=executable.parent, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Cannot read the GitHub CLI login. Run gh auth login or use --auth env.") from None
    if result.returncode or not result.stdout.strip():
        raise ValueError("No GitHub CLI token available. Run gh auth login or use --auth env.")
    return GitHubClient(token=result.stdout.strip())


def _gh_executable():
    # Windows searches the working directory before PATH. A PR checkout must
    # not supply the executable used to access the user's existing credentials.
    current = Path.cwd().resolve()
    checkout = next((path for path in (current, *current.parents) if (path / ".git").exists()), current)

    def excluded(path):
        return (path == current or current in path.parents or path in current.parents
                or path == checkout or checkout in path.parents)

    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        directory = Path(entry.strip('"'))
        if not entry or not directory.is_absolute():
            continue
        directory = directory.resolve()
        if excluded(directory):
            continue
        candidate = directory / ("gh.exe" if os.name == "nt" else "gh")
        if candidate.is_file() and os.access(candidate, os.X_OK):
            candidate = candidate.resolve()
            if not excluded(candidate.parent):
                return candidate
    raise ValueError("GitHub CLI must be installed on an absolute PATH outside the current checkout; use --auth env if unavailable.")


def _reports(value):
    if isinstance(value, dict) and "reports" in value:
        value = value["reports"]
    values = value if isinstance(value, list) else [value]
    if not values or any(not isinstance(item, dict) or "tier" not in item or "checks" not in item for item in values):
        raise ValueError("Expected a ReviewBudget report or a nonempty list of reports.")
    return values


def _join_snapshots(records, directory):
    snapshots = {}
    for path in sorted(Path(directory).glob("*.json")):
        from .planner import validate_snapshot
        value = _json(_read(path))
        validate_snapshot(value)
        key = (value["repository"].casefold(), value["number"])
        if key in snapshots:
            raise ValueError("Multiple original snapshots for the same PR; select one observation per PR.")
        snapshots[key] = value
    if not snapshots:
        raise ValueError("No snapshot JSON files found.")
    joined = []
    for record in records:
        final = record.get("snapshot") if isinstance(record, dict) else None
        if (not isinstance(final, dict) or not isinstance(final.get("repository"), str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", final["repository"])
                or type(final.get("number")) is not int or final["number"] <= 0):
            raise ValueError("History records must include a snapshot with a valid repository and positive PR number before joining observations.")
        key = (final["repository"].casefold(), final["number"])
        if key in snapshots:
            joined.append({**record, "snapshot": snapshots[key],
                           "feature_timing": snapshots[key].get("feature_timing")})
    if not joined:
        raise ValueError("No captured snapshots match the supplied outcomes.")
    return joined


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and (argv[0].startswith(("https://", "github.com/")) or re.fullmatch(r"[\w.-]+/[\w.-]+#\d+", argv[0])):
        argv.insert(0, "analyze")
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            local = bool(args.repo or args.base)
            if sum(map(bool, (args.target, args.input, local))) != 1:
                raise ValueError("Provide exactly one PR URL, --input snapshot.json, or --base REF for local Git.")
            policy = _policy(args.policy)
            if args.budget_minutes is not None:
                policy = {**(policy or {}), "budget": {"limit_minutes": args.budget_minutes}}
            if local:
                from .local import capture_local
                if not args.base:
                    raise ValueError("Local analysis requires --base REF.")
                value = capture_local(args.repo or ".", args.base, args.head, title=args.title,
                                      body=_read(args.body_file, 100_000) if args.body_file else "")
            elif args.input:
                value = _json(_read(args.input))
            else:
                from .github import parse_pr_url
                repository, number = parse_pr_url(args.target)
                value = _client(args.auth).fetch_pr(repository, number)
            report = analyze(value, policy)
            _write(render(report, args.format), args.output)
        elif args.command == "init":
            from .onboarding import initialize
            print(f"Created {initialize(args.directory, preset=args.preset)}")
        elif args.command == "demo":
            from .demos import names, load_scenario, load_policy
            reports = [analyze(load_scenario(name), load_policy()) for name in
                       (names() if args.format == "html" and not args.scenario else [args.scenario or "auth"])]
            if args.format == "html" and len(reports) > 1:
                from .reports import render_demo
                text = render_demo(reports)
            else:
                text = render(reports[0], args.format)
            _write(text, args.output)
        elif args.command == "allocate":
            from .budget import allocate
            reports = [report for path in args.input for report in _reports(_json(_read(path)))]
            _write(render(allocate(reports, args.budget_minutes), args.format), args.output)
        elif args.command == "report":
            value = _json(_read(args.input))
            reports = _reports(value)
            # Reuse the saved-report consistency boundary without replacing
            # the original decisions with this validation-only allocation.
            from .budget import allocate
            allocate(reports, 0)
            if isinstance(value, list):
                value = {"reports": reports}
            _write(render(value, args.format), args.output)
        elif args.command == "capture":
            from .github import parse_pr_url
            repository, number = parse_pr_url(args.target)
            if Path(args.output).exists():
                raise ValueError("Snapshot already exists; choose a new output path to preserve the original observation.")
            value = _client(args.auth).capture_pr(repository, number)
            text = json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
            with Path(args.output).open("x", encoding="utf-8") as handle:
                handle.write(text)
            print(f"Captured {repository}#{number}: {value['feature_timing']}.", file=sys.stderr)
        elif args.command == "collect":
            from .history import collect_to_file
            status = collect_to_file(_client(args.auth), args.repository, args.limit, args.output,
                                     resume=args.resume, progress=lambda done, total: print(f"Collected {done}/{total}", file=sys.stderr) if done % 10 == 0 or done == total else None)
            print(f"Collected {status['completed']} final-state snapshots. These are exploratory, not pre-review observations.", file=sys.stderr)
        else:
            from .evaluation import evaluate
            records = [_json(line) for path in args.input for line in _read(path, 200_000_000).splitlines() if line.strip()]
            if args.snapshots:
                records = _join_snapshots(records, args.snapshots)
            report = evaluate(records, holdout_fraction=args.holdout_fraction)
            _write(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n", args.output)
        return 0
    except (ValueError, OSError, UnicodeError) as error:
        print(f"reviewbudget: {_safe(error)}", file=sys.stderr)
        return 2


def _action_outputs(report=None):
    budget = report.get("budget", {}) if report else {}
    values = {
        "tier": report["tier"] if report else 3,
        "full-ci-required": report["full_ci_required"] if report else True,
        "human-review-required": report["human_review_required"] if report else True,
        "security-review-required": report["security_review_required"] if report else True,
        "input-complete": report["input_complete"] if report else False,
        "review-cost-score": report["review_cost_score"] if report else 100,
        "allow-skip-required-checks": False,
        "selected-checks": json.dumps([check["id"] for check in report.get("checks", []) if check["selected"]]) if report else "[]",
        "coverage-complete": bool(report and report.get("checks") and not budget.get("unmapped_capabilities") and report["input_complete"]),
        "budget-status": budget.get("status", "unavailable"),
        "estimated-minutes": budget.get("estimated_minutes"),
        "budget-shortfall": budget.get("shortfall_minutes"),
    }
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            for key, value in values.items():
                rendered = str(value).lower() if isinstance(value, bool) else ("" if value is None else str(value))
                handle.write(f"{key}={rendered}\n")


def run_action(client=None):
    try:
        if os.environ.get("GITHUB_EVENT_NAME") not in {"pull_request", "pull_request_target"}:
            raise ValueError("The Action requires a pull_request or pull_request_target event.")
        event = _json(_read(os.environ["GITHUB_EVENT_PATH"]))
        pr = event.get("pull_request")
        if not isinstance(pr, dict) or type(pr.get("number")) is not int:
            raise ValueError("Event does not contain a pull request number.")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if client is None:
            from .github import GitHubClient
            client = GitHubClient(token=os.environ.get("REVIEWBUDGET_TOKEN") or None)
        value = client.capture_pr(repository, pr["number"]) if hasattr(client, "capture_pr") else client.fetch_pr(repository, pr["number"])
        if (value.get("head_sha") != pr.get("head", {}).get("sha")
                or value.get("base_sha") != pr.get("base", {}).get("sha")):
            raise ValueError("Pull request changed after the event; rerun against its current commits.")
        report = analyze(value, _policy(os.environ.get("REVIEWBUDGET_POLICY")))
        artifact_dir = os.environ.get("REVIEWBUDGET_ARTIFACT_DIR")
        if artifact_dir:
            destination = Path(artifact_dir)
            destination.mkdir(parents=True, exist_ok=True)
            _write(render(report, "json"), destination / "report.json")
            _write(render(report, "html"), destination / "report.html")
            _write(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n", destination / "snapshot.json")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as handle:
                handle.write(render(report, "markdown"))
        _action_outputs(report)
        print(render(report))
        return 0
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        _action_outputs()
        print(f"reviewbudget: {_safe(error)}", file=sys.stderr)
        return 2
