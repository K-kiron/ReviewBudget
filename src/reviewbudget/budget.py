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


def _texts(value: object, label: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of text values.")


def _validate_metadata(report: dict, tier: int, files: list[dict]) -> None:
    """Validate retained report fields before passing them to report consumers."""
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1 or report.get("mode") != "advisory":
        raise ValueError("Allocation requires a supported advisory analysis report.")
    if not isinstance(report.get("policy_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", report["policy_hash"]):
        raise ValueError("Report policy_hash must be a normalized policy fingerprint.")
    if report.get("tier_name") != ("deterministic", "light", "comprehensive", "intensive")[tier]:
        raise ValueError("Report tier_name does not match its tier.")
    for key, expected in {"allow_skip_required_checks": False, "full_ci_required": tier >= 2,
                          "human_review_required": tier == 3, "security_review_required": tier == 3}.items():
        if report.get(key) is not expected:
            raise ValueError(f"Report {key} is inconsistent with its advisory tier.")
    for key in ("risk", "evidence_debt"):
        value = report.get(key)
        if (not isinstance(value, dict) or type(value.get("score")) is not int
                or not 0 <= value["score"] <= 100 or value.get("level") not in ("low", "medium", "high")):
            raise ValueError(f"Report {key} requires a score in 0..100 and a recognized level.")
    risk = report.get("review_cost_score")
    if type(risk) is not int or not 0 <= risk <= 100:
        raise ValueError("Report review_cost_score must be an integer in 0..100.")
    for key in ("uncertainties", "missing_evidence", "recommendations", "limitations"):
        _texts(report.get(key), f"Report {key}")
    _texts(report["risk"].get("signals"), "Report risk.signals")
    known_signals = {"security", "workflow", "migration", "public_api", "dependencies", "configuration"}
    signals = set(report["risk"]["signals"])
    if signals - known_signals or (signals & {"security", "workflow"} and tier != 3):
        raise ValueError("Report risk signals are unknown or inconsistent with its safety tier.")
    if report["input_complete"] != (not report["uncertainties"]):
        raise ValueError("Report input_complete contradicts its uncertainties.")
    if report["input_complete"] and (not files or not report.get("base_sha") or not report.get("head_sha")):
        raise ValueError("Complete reports require files and both commit identities.")
    if tier < 2 and (report["uncertainties"] or signals & {"migration", "public_api", "dependencies", "configuration"}):
        raise ValueError("Report tier is below its recorded safety floor.")
    for key in ("optional", "next_steps"):
        _texts(report["plan"].get(key), f"Report plan.{key}")
    if any(capability not in CAPABILITIES for capability in report["plan"]["optional"]):
        raise ValueError("Report plan.optional contains unknown capabilities.")
    if not isinstance(report.get("evidence"), dict) or any(type(value) is not bool for value in report["evidence"].values()):
        raise ValueError("Report evidence must map names to booleans.")
    if not isinstance(report.get("provenance"), dict):
        raise ValueError("Report provenance must be an object of supplied origin labels.")
    for key in ("source", "synthetic", "feature_timing", "captured_at"):
        if report["provenance"].get(key) != report.get(key):
            raise ValueError("Report provenance labels contradict their top-level values.")
    drivers = report.get("drivers")
    if not isinstance(drivers, list):
        raise ValueError("Report drivers must be a list.")
    for driver in drivers:
        if not isinstance(driver, dict) or not all(isinstance(driver.get(key), str) for key in ("signal", "message")):
            raise ValueError("Report drivers require a signal and message.")
        _texts(driver.get("paths"), "Report driver paths")
        if driver["signal"] == "tests_reduced" and tier < 2:
            raise ValueError("Report tier is below the recorded test-reduction safety floor.")
    stats = report.get("stats")
    expected_stats = {"files": len(files), "additions": sum(f["additions"] for f in files),
                      "deletions": sum(f["deletions"] for f in files)}
    expected_stats["lines"] = expected_stats["additions"] + expected_stats["deletions"]
    if not isinstance(stats, dict) or any(type(stats.get(key)) is not int or stats[key] != value for key, value in expected_stats.items()):
        raise ValueError("Report stats must match its saved changed files.")
    for item in files:
        if item.get("classification") not in ("source", "test", "documentation"):
            raise ValueError("Report file classification is unrecognized.")
        _texts(item.get("signals"), "Report file signals")
    budget = report.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("Report budget must be an accounting object.")
    nullable_costs = {"limit_minutes", "estimated_minutes", "shortfall_minutes", "remaining_minutes",
                      "full_baseline_minutes", "modeled_savings_minutes"}
    required_costs = {"mandatory_known_minutes", "selected_known_minutes", "optional_selected_known_minutes", "baseline_known_minutes"}
    for key in nullable_costs | required_costs:
        if key not in budget:
            raise ValueError(f"Report budget is missing {key}.")
        if budget[key] is not None or key in required_costs:
            minutes(budget[key], f"Report budget.{key}")
    if budget.get("status") not in ("not_configured", "over_budget", "unpriced", "no_limit", "within_budget"):
        raise ValueError("Report budget status is unrecognized.")
    if not all(isinstance(budget.get(key), str) for key in ("estimate_basis", "baseline_basis")):
        raise ValueError("Report budget requires explicit estimate and baseline descriptions.")
    if budget.get("within_budget") is not None and type(budget["within_budget"]) is not bool:
        raise ValueError("Report budget.within_budget must be a boolean or null.")
    for key in ("unpriced_checks", "unpriced_configured_checks", "unmapped_capabilities"):
        _texts(budget.get(key), f"Report budget.{key}")


def allocate(reports: list[dict], budget_minutes: float) -> dict:
    """Allocate a queue budget without mutating reports or weakening required work.

    Optional checks use priority, report risk, cost, and stable identity ordering.
    Checks are recommendations; this function never executes repository commands.
    """
    # Deferred import reuses the snapshot boundary without a module import cycle.
    from .planner import _provenance, validate_snapshot

    limit = minutes(budget_minutes, "budget_minutes")
    if not isinstance(reports, list) or not reports:
        raise ValueError("Allocation requires a nonempty list of analysis reports.")
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError("Each saved analysis report must be a JSON object.")
        saved_hash = report.get("report_fingerprint")
        if not isinstance(saved_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", saved_hash):
            raise ValueError("Saved reports require their original report_fingerprint; regenerate missing or obsolete reports.")
        try:
            actual_hash = fingerprint(report)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("Report content must be finite JSON-compatible data within nesting limits.") from None
        if saved_hash != actual_hash:
            raise ValueError("Saved report fingerprint does not match its content; regenerate the analysis report.")
    try:
        results = copy.deepcopy(reports)
    except RecursionError:
        raise ValueError("Report content exceeds nesting limits.") from None
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
        tier = report.get("tier")
        if type(tier) is not int or not 0 <= tier <= 3:
            raise ValueError("Report tier must be an integer in 0..3.")
        expected = {"format_and_lint"}
        if tier >= 1:
            expected.update({"unit_tests", "light_review" if tier == 1 else "comprehensive_review"})
        if tier >= 2:
            expected.add("integration_tests")
        if tier >= 3:
            expected.update({"e2e_tests", "security_review", "human_review"})
        if not expected.issubset(required):
            raise ValueError("Report plan.required omits capabilities required by its tier.")
        files = report.get("files")
        if not isinstance(files, list):
            raise ValueError("Report files must contain the saved changed-file metadata.")
        validate_snapshot({**report, "files_complete": report["input_complete"], "changed_files": len(files)})
        if report.get("provenance") != _provenance(report):
            raise ValueError("Report provenance contains unsupported fields or inconsistent origin labels.")
        _validate_metadata(report, tier, files)
        risk = minutes(report.get("review_cost_score"), "report.review_cost_score")
        if any(not isinstance(check, dict) for check in report["checks"]):
            raise ValueError("Report checks must be check-decision objects.")
        validated = validate_policy({"checks": [{k: v for k, v in check.items() if k in CHECK_KEYS} for check in report["checks"]]})
        previous_mandatory = {}
        for check in report["checks"]:
            if not CHECK_KEYS.issubset(check):
                raise ValueError("Report checks require all normalized policy fields, including required and estimated_minutes.")
            if any(type(check.get(key)) is not bool for key in ("applicable", "mandatory", "selected")):
                raise ValueError("Report checks require boolean applicable, mandatory and selected decisions.")
            if check.get("status") not in ("selected", "deferred", "not_applicable"):
                raise ValueError("Report check status is unrecognized.")
            _texts(check.get("matched_paths"), "Report check matched_paths")
            _texts(check.get("reasons"), "Report check reasons")
            previous_mandatory[check["id"]] = check["mandatory"]
        report["checks"], _ = plan_checks(files, required, report["input_complete"], validated)
        for check in report["checks"]:
            if previous_mandatory[check["id"]] and not check["mandatory"]:
                check.update(mandatory=True, selected=True, status="selected")
                check["reasons"] = ["Previously mandatory saved work is retained conservatively."]
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
            "budget": budget, "recommendations": recommendations(budget),
            "limitations": ["Supplied reports and provenance are self-declared. Fingerprints check content consistency but do not authenticate origin, repository policy, capability coverage or check execution."]}


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
