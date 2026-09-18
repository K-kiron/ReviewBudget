"""Strict, dependency-free repository check policy validation."""

from __future__ import annotations

import math
import re

CAPABILITIES = frozenset({
    "format_and_lint", "unit_tests", "light_review", "comprehensive_review",
    "integration_tests", "e2e_tests", "security_review", "human_review",
})
DEFAULT_POLICY = {
    "thresholds": [20, 45, 70], "sensitive_paths": [], "checks": [],
    "budget": {"limit_minutes": None},
}
CHECK_KEYS = {"id", "name", "covers", "paths", "estimated_minutes", "required", "priority"}


def minutes(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite nonnegative number.")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{label} must be a finite nonnegative number.") from None
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite nonnegative number.")
    return result


def _patterns(value: object, label: str, *, allow_empty: bool = True) -> list[str]:
    if (not isinstance(value, list) or len(value) > 100
            or (not allow_empty and not value)
            or any(not isinstance(p, str) or not p or len(p) > 512
                   or "\\" in p or ":" in p or p.startswith("/")
                   or any(part in {"", ".", ".."} for part in p.split("/"))
                   or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in value)):
        raise ValueError(f"{label} must be a list of relative POSIX glob patterns.")
    return sorted(set(value))


def validate_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = {}
    if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
        raise ValueError("Policy accepts only thresholds, sensitive_paths, checks and budget.")
    thresholds = policy.get("thresholds", DEFAULT_POLICY["thresholds"])
    if (not isinstance(thresholds, list) or len(thresholds) != 3
            or any(type(v) is not int or not 1 <= v <= 100 for v in thresholds)
            or not thresholds[0] < thresholds[1] < thresholds[2]):
        raise ValueError("thresholds must be three strictly increasing integers in 1..100.")
    patterns = _patterns(policy.get("sensitive_paths", []), "sensitive_paths")
    configured = policy.get("checks", [])
    if not isinstance(configured, list) or len(configured) > 500:
        raise ValueError("checks must be a list with at most 500 entries.")
    checks = []
    seen = set()
    for check in configured:
        if not isinstance(check, dict) or set(check) - CHECK_KEYS:
            raise ValueError("Each check accepts only id, name, covers, paths, estimated_minutes, required and priority.")
        check_id = check.get("id")
        if not isinstance(check_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", check_id):
            raise ValueError("Check id must be 1..80 lowercase letters, digits, dots, underscores or hyphens, starting with a letter or digit.")
        if check_id in seen:
            raise ValueError(f"Duplicate check id: {check_id}.")
        seen.add(check_id)
        name = check.get("name")
        if (not isinstance(name, str) or not name.strip() or len(name) > 200
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError(f"Check {check_id} name must be nonempty display text of at most 200 characters.")
        covers = check.get("covers")
        if (not isinstance(covers, list) or len(covers) > len(CAPABILITIES)
                or any(not isinstance(c, str) or c not in CAPABILITIES for c in covers)):
            raise ValueError(f"Check {check_id} covers must list recognized capability IDs.")
        required = check.get("required", False)
        if type(required) is not bool:
            raise ValueError(f"Check {check_id} required must be a boolean.")
        priority = check.get("priority", 0)
        if type(priority) is not int or not 0 <= priority <= 1_000_000:
            raise ValueError(f"Check {check_id} priority must be an integer in 0..1000000.")
        estimate = check.get("estimated_minutes")
        checks.append({
            "id": check_id, "name": name.strip(), "covers": sorted(set(covers)),
            "paths": _patterns(check.get("paths", ["*"]), f"Check {check_id} paths", allow_empty=False),
            "estimated_minutes": None if estimate is None else minutes(estimate, f"Check {check_id} estimated_minutes"),
            "required": required, "priority": priority,
        })
    budget = policy.get("budget", {})
    if not isinstance(budget, dict) or set(budget) - {"limit_minutes"}:
        raise ValueError("budget accepts only limit_minutes.")
    limit = budget.get("limit_minutes")
    return {
        "thresholds": list(thresholds), "sensitive_paths": patterns,
        "checks": sorted(checks, key=lambda check: check["id"]),
        "budget": {"limit_minutes": None if limit is None else minutes(limit, "budget.limit_minutes")},
    }
