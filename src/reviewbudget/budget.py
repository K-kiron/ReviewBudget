"""Advisory selection and accounting for declared repository check estimates."""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import math
import re
from decimal import Decimal, localcontext

from .policy import CAPABILITIES, CHECK_KEYS, minutes, validate_policy

ESTIMATE_BASIS = "Declared configuration estimates; not measured execution times or wall-clock duration."
BASELINE_BASIS = "All configured checks, including path-inapplicable checks; declared estimates."


def _sum(values) -> float:
    # Policy estimates are decimal values, so 0.1 + 0.2 must fit a 0.3 budget.
    with localcontext() as context:
        context.prec = 50
        result = float(sum((Decimal(str(value)) for value in values), Decimal(0)))
    if not math.isfinite(result):
        raise ValueError("Total declared check estimates must remain finite.")
    return result


def _account(checks: list[dict], gaps: list[str], limit: float | None, *, configured: bool = True) -> dict:
    selected = [c for c in checks if c["selected"]]
    known = _sum(c["estimated_minutes"] for c in selected if c["estimated_minutes"] is not None)
    mandatory = _sum(c["estimated_minutes"] for c in checks if c["mandatory"] and c["estimated_minutes"] is not None)
    baseline_known = _sum(c["estimated_minutes"] for c in checks if c["estimated_minutes"] is not None)
    unpriced = [c["id"] for c in selected if c["estimated_minutes"] is None]
    all_unpriced = [c["id"] for c in checks if c["estimated_minutes"] is None]
    total = None if unpriced or gaps or not configured else known
    baseline = None if all_unpriced or not configured else baseline_known
    shortfall = None if limit is None else max(0.0, _sum([known, -limit]))
    within = None if limit is None or total is None else known <= limit
    if shortfall:
        within = False
    status = ("not_configured" if not configured else "over_budget" if shortfall
              else "unpriced" if total is None else "no_limit" if limit is None else "within_budget")
    return {
        "limit_minutes": limit, "mandatory_known_minutes": mandatory,
        "selected_known_minutes": known,
        "optional_selected_known_minutes": _sum(c["estimated_minutes"] for c in selected if not c["mandatory"] and c["estimated_minutes"] is not None),
        "estimated_minutes": total,
        "shortfall_minutes": shortfall,
        "remaining_minutes": None if limit is None or total is None else max(0.0, _sum([limit, -known])),
        "unpriced_checks": unpriced, "unpriced_configured_checks": all_unpriced,
        "unmapped_capabilities": gaps, "within_budget": within, "status": status,
        "full_baseline_minutes": baseline, "baseline_known_minutes": baseline_known,
        "modeled_savings_minutes": None if baseline is None or total is None else max(0.0, _sum([baseline, -total])),
        "estimate_basis": ESTIMATE_BASIS, "baseline_basis": BASELINE_BASIS,
    }


def _select_optional(candidates: list[dict], budget: dict) -> None:
    spent = budget["selected_known_minutes"]
    limit = budget["limit_minutes"]
    for check in candidates:
        estimate = check["estimated_minutes"]
        if limit is None:
            reason = "Optional upgrade deferred because no explicit budget is configured."
        elif budget["estimated_minutes"] is None:
            reason = "Optional upgrade deferred until mandatory costs and capability gaps are resolved."
        elif estimate is None:
            reason = "Optional upgrade has no declared cost; price it before allocating budget."
        elif _sum([spent, estimate]) > limit:
            reason = "Optional upgrade does not fit the remaining declared budget."
        else:
            spent = _sum([spent, estimate])
            check["selected"] = True
            check["status"] = "selected"
            reason = "Optional upgrade fits the remaining declared budget in priority order."
        check["reasons"] = [reason]


def fingerprint(report: dict) -> str:
    """Hash the reproducible report content, excluding its own fingerprint."""
    content = {key: value for key, value in report.items() if key != "report_fingerprint"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, allow_nan=False).encode()).hexdigest()


def recommendations(budget: dict) -> list[str]:
    result = []
    if budget["status"] == "not_configured":
        result.append("Configure named repository checks and declared estimates to create an actionable budget.")
    if budget["unmapped_capabilities"]:
        result.append("Map recommended capabilities to applicable checks: " + ", ".join(budget["unmapped_capabilities"]) + ".")
    if budget["unpriced_checks"]:
        result.append("Declare estimates for retained checks: " + ", ".join(budget["unpriced_checks"]) + ".")
    if budget["shortfall_minutes"]:
        result.append(f"Increase the budget by at least {budget['shortfall_minutes']:g} estimated minutes or schedule the retained work later; mandatory checks remain selected.")
    if budget["limit_minutes"] is None and budget.get("scope") != "queue_share":
        result.append("Set a budget limit to consider optional upgrades.")
    return result


def allocate(reports: list[dict], budget_minutes: float) -> dict:
    """Allocate a queue budget without mutating reports or weakening required work.

    Optional checks use priority, report risk, cost, and stable identity ordering.
    Checks are recommendations; this function never executes repository commands.
    """
    limit = minutes(budget_minutes, "budget_minutes")
    if not isinstance(reports, list) or not reports:
        raise ValueError("Allocation requires a nonempty list of analysis reports.")
    results = copy.deepcopy(reports)
    seen = set()
    candidates = []
    gaps_by_report = {}
    for report in results:
        if (not isinstance(report, dict) or not isinstance(report.get("repository"), str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", report["repository"])
                or type(report.get("number")) is not int or report["number"] <= 0
                or type(report.get("input_complete")) is not bool
                or not isinstance(report.get("checks"), list)):
            raise ValueError("Allocation requires analysis reports with repository, number, input_complete and checks.")
        identity = f"{report['repository']}#{report['number']}"
        if identity in seen:
            raise ValueError(f"Duplicate report in allocation: {identity}.")
        seen.add(identity)
        required = report.get("plan", {}).get("required") if isinstance(report.get("plan"), dict) else None
        if not isinstance(required, list) or any(not isinstance(c, str) or c not in CAPABILITIES for c in required):
            raise ValueError("Allocation requires recognized plan.required capabilities.")
        risk = minutes(report.get("review_cost_score"), "report.review_cost_score")
        if any(not isinstance(check, dict) for check in report["checks"]):
            raise ValueError("Report checks must be check-decision objects.")
        validated = validate_policy({"checks": [{k: v for k, v in check.items() if k in CHECK_KEYS} for check in report["checks"]]})
        config = {check["id"]: check for check in validated["checks"]}
        for check in report["checks"]:
            if type(check.get("applicable")) is not bool or type(check.get("mandatory")) is not bool:
                raise ValueError("Report checks require boolean applicable and mandatory decisions.")
            check.update(config[check["id"]])
            check["applicable"] = check["applicable"] or check["required"] or not report["input_complete"]
            check["mandatory"] = check["mandatory"] or check["required"] or not report["input_complete"] or bool(check["applicable"] and set(check["covers"]) & set(required))
            check["selected"] = check["mandatory"]
            check["status"] = "selected" if check["mandatory"] else "deferred" if check["applicable"] else "not_applicable"
            if check["applicable"] and not check["mandatory"]:
                candidates.append((check, risk, identity))
        covered = {c for check in report["checks"] if check["selected"] for c in check["covers"]}
        gaps_by_report[identity] = [c for c in required if c not in covered]
        report["checks"].sort(key=lambda c: c["id"])
    results.sort(key=lambda r: (r["repository"], r["number"]))

    def accounting() -> dict:
        checks = [{**c, "id": f"{r['repository']}#{r['number']}/{c['id']}"} for r in results for c in r["checks"]]
        gaps = [f"{r['repository']}#{r['number']}/{gap}" for r in results for gap in gaps_by_report[f"{r['repository']}#{r['number']}"]]
        return _account(checks, gaps, limit, configured=all(bool(r["checks"]) for r in results))

    candidates.sort(key=lambda item: (-item[0]["priority"], -item[1],
                    item[0]["estimated_minutes"] if item[0]["estimated_minutes"] is not None else math.inf,
                    item[2], item[0]["id"]))
    _select_optional([item[0] for item in candidates], accounting())
    for report in results:
        identity = f"{report['repository']}#{report['number']}"
        report["budget"] = _account(report["checks"], gaps_by_report[identity], None, configured=bool(report["checks"]))
        report["budget"].update({"scope": "queue_share", "queue_limit_minutes": limit})
        report["recommendations"] = recommendations(report["budget"])
        report["report_fingerprint"] = fingerprint(report)
    budget = accounting()
    return {"schema_version": 1, "mode": "advisory", "reports": results,
            "budget": budget, "recommendations": recommendations(budget)}


def plan_checks(files: list[dict], required: list[str], input_complete: bool, policy: dict) -> tuple[list[dict], dict]:
    paths = sorted({path for item in files for path in (item["filename"], item.get("previous_filename")) if path})
    checks = []
    for configured in policy["checks"]:
        matched = [path for path in paths if any(fnmatch.fnmatchcase(path.lower(), p.lower()) for p in configured["paths"])]
        applicable = bool(matched) or configured["required"] or not input_complete
        capabilities = sorted(set(required) & set(configured["covers"]))
        mandatory = configured["required"] or not input_complete or bool(applicable and capabilities)
        reasons = []
        if configured["required"]:
            reasons.append("Explicitly required by repository policy; path filters and budgets cannot remove this check.")
        if not input_complete:
            reasons.append("Input is incomplete; every configured check is retained conservatively.")
        if applicable and capabilities:
            reasons.append("Covers recommended capabilities: " + ", ".join(capabilities) + ".")
        if not applicable:
            reasons.append("No changed or previous path matches this check's path filters.")
        elif not mandatory:
            reasons.append("Applicable optional upgrade; no explicit budget has selected it.")
        checks.append({
            **configured, "matched_paths": matched, "applicable": applicable,
            "mandatory": mandatory, "selected": mandatory,
            "status": "selected" if mandatory else "deferred" if applicable else "not_applicable",
            "reasons": reasons,
        })
    covered = {capability for check in checks if check["selected"] for capability in check["covers"]}
    gaps = [capability for capability in required if capability not in covered]
    limit = policy["budget"]["limit_minutes"]
    candidates = sorted((c for c in checks if c["applicable"] and not c["mandatory"]), key=lambda c: (
        -c["priority"], c["estimated_minutes"] if c["estimated_minutes"] is not None else math.inf, c["id"],
    ))
    _select_optional(candidates, _account(checks, gaps, limit, configured=bool(checks)))
    return checks, _account(checks, gaps, limit, configured=bool(checks))
