"""Command-line and GitHub Action entry points."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
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
    escape = _markdown if format == "markdown" else _safe
    heading = "## ReviewBudget" if format == "markdown" else "ReviewBudget"
    lines = [heading, "", f"{escape(report['repository'])} #{report['number']}",
             f"Verification: Tier {report['tier']} - {report['tier_name']}",
             f"Structural risk: {report['risk']['level']} ({report['risk']['score']}/100)",
             f"Evidence debt: {report['evidence_debt']['level']} ({report['evidence_debt']['score']}/100)",
             f"Review burden score: {report['review_cost_score']}/100 (uncalibrated)", "",
             "Recommended checks:"]
    lines += [f"- {check.replace('_', ' ')}" for check in report["plan"]["required"]]
    lines += ["", "Reasons:"]
    for driver in report["drivers"]:
        lines.append(f"- {escape(driver['message'])}")
        if driver["paths"]:
            lines.append("  Paths: " + ", ".join(escape(p) for p in driver["paths"][:10]))
            if len(driver["paths"]) > 10:
                lines.append(f"  ({len(driver['paths']) - 10} more paths)")
    if report["missing_evidence"]:
        lines += ["", "Missing evidence (description evidence is self-reported):"]
        lines += [f"- {item.replace('_', ' ')}" for item in report["missing_evidence"]]
    if report["uncertainties"]:
        lines += ["", "Incomplete inputs:"] + [f"- {item.replace('_', ' ')}" for item in report["uncertainties"]]
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
    analysis.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    analysis.add_argument("--output", help="Write a report instead of stdout.")
    collect = commands.add_parser("collect", help="Collect final-state closed PRs for exploratory evaluation.")
    collect.add_argument("repository", help="OWNER/REPO")
    collect.add_argument("--limit", type=int, default=100)
    collect.add_argument("--output", required=True, help="Local JSONL output; may contain private repository data.")
    evaluate = commands.add_parser("evaluate", help="Compare a JSONL history with a change-size baseline.")
    evaluate.add_argument("--input", required=True)
    evaluate.add_argument("--holdout-fraction", type=float, default=0.3)
    evaluate.add_argument("--output", help="Write the JSON evaluation report instead of stdout.")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and (argv[0].startswith(("https://", "github.com/")) or re.fullmatch(r"[\w.-]+/[\w.-]+#\d+", argv[0])):
        argv.insert(0, "analyze")
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            if bool(args.target) == bool(args.input):
                raise ValueError("Provide exactly one PR URL or --input snapshot.json.")
            policy = _policy(args.policy)
            if args.input:
                value = _json(_read(args.input))
            else:
                from .github import GitHubClient, parse_pr_url
                repository, number = parse_pr_url(args.target)
                value = GitHubClient().fetch_pr(repository, number)
            report = analyze(value, policy)
            _write(render(report, args.format), args.output)
        elif args.command == "collect":
            from .github import GitHubClient
            records = GitHubClient().collect_history(args.repository, args.limit)
            text = "".join(json.dumps(r, ensure_ascii=True, allow_nan=False) + "\n" for r in records)
            _write(text, args.output)
            print(f"Collected {len(records)} final-state snapshots. These are exploratory, not pre-review observations.", file=sys.stderr)
        else:
            from .evaluation import evaluate
            records = [_json(line) for line in _read(args.input, 200_000_000).splitlines() if line.strip()]
            report = evaluate(records, holdout_fraction=args.holdout_fraction)
            _write(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n", args.output)
        return 0
    except (ValueError, OSError) as error:
        print(f"reviewbudget: {_safe(error)}", file=sys.stderr)
        return 2


def _action_outputs(report=None):
    values = {
        "tier": report["tier"] if report else 3,
        "full-ci-required": report["full_ci_required"] if report else True,
        "human-review-required": report["human_review_required"] if report else True,
        "security-review-required": report["security_review_required"] if report else True,
        "input-complete": report["input_complete"] if report else False,
        "review-cost-score": report["review_cost_score"] if report else 100,
        "allow-skip-required-checks": False,
    }
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={str(value).lower()}\n")


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
        value = client.fetch_pr(repository, pr["number"])
        if (value.get("head_sha") != pr.get("head", {}).get("sha")
                or value.get("base_sha") != pr.get("base", {}).get("sha")):
            raise ValueError("Pull request changed after the event; rerun against its current commits.")
        report = analyze(value, _policy(os.environ.get("REVIEWBUDGET_POLICY")))
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
