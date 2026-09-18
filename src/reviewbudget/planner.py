"""Deterministic heuristics, with non-negotiable floors for incomplete inputs."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import math
import re
from pathlib import PurePosixPath

DEFAULT_POLICY = {"thresholds": [20, 45, 70], "sensitive_paths": []}
TIERS = ("deterministic", "light", "comprehensive", "intensive")
DEPENDENCIES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
    "pnpm-lock.yaml", "pyproject.toml", "poetry.lock", "uv.lock", "pipfile",
    "pipfile.lock", "cargo.toml", "cargo.lock", "go.mod", "go.sum", "gemfile",
    "gemfile.lock", "composer.json", "composer.lock", "pom.xml", "build.gradle",
    "build.gradle.kts", "packages.lock.json", "directory.packages.props",
}


def validate_policy(policy: dict | None = None) -> dict:
    if policy is None:
        policy = {}
    if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
        raise ValueError("Policy accepts only thresholds and sensitive_paths.")
    result = {**DEFAULT_POLICY, **policy}
    thresholds = result["thresholds"]
    if (not isinstance(thresholds, list) or len(thresholds) != 3
            or any(type(v) is not int or not 1 <= v <= 100 for v in thresholds)
            or not thresholds[0] < thresholds[1] < thresholds[2]):
        raise ValueError("thresholds must be three strictly increasing integers in 1..100.")
    patterns = result["sensitive_paths"]
    if (not isinstance(patterns, list) or len(patterns) > 100
            or any(not isinstance(p, str) or not p or len(p) > 512
                   or "\\" in p or p.startswith("/") or ".." in p.split("/")
                   or any(ord(c) < 32 for c in p) for p in patterns)):
        raise ValueError("sensitive_paths must be a list of relative POSIX glob patterns.")
    return {"thresholds": list(thresholds), "sensitive_paths": sorted(set(patterns))}


def _path(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > 4096
            or value.startswith("/") or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("File names must be nonempty relative POSIX paths without traversal.")
    return value


def validate_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be a JSON object.")
    if not isinstance(snapshot.get("repository"), str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", snapshot["repository"]
    ):
        raise ValueError("Snapshot repository must be OWNER/REPO.")
    if type(snapshot.get("number")) is not int or snapshot["number"] <= 0:
        raise ValueError("Snapshot number must be a positive integer.")
    for key in ("title", "body"):
        if snapshot.get(key) is not None and not isinstance(snapshot[key], str):
            raise ValueError(f"Snapshot {key} must be text or null.")
        if len(snapshot.get(key) or "") > 1_000_000:
            raise ValueError(f"Snapshot {key} exceeds the input limit.")
    for key in ("head_sha", "base_sha"):
        if snapshot.get(key) is not None and not re.fullmatch(r"[0-9a-fA-F]{40,64}", str(snapshot[key])):
            raise ValueError(f"Snapshot {key} must be a commit SHA.")
    if type(snapshot.get("files_complete")) is not bool:
        raise ValueError("Snapshot files_complete must be a boolean.")
    if type(snapshot.get("changed_files")) is not int or snapshot["changed_files"] < 0:
        raise ValueError("Snapshot changed_files must be a nonnegative integer.")
    files = snapshot.get("files")
    if not isinstance(files, list) or len(files) > 3000:
        raise ValueError("Snapshot files must be a list with at most 3000 entries.")
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Each changed file must be an object.")
        path = _path(item.get("filename"))
        if path in seen:
            raise ValueError("Snapshot contains duplicate file paths.")
        seen.add(path)
        if item.get("previous_filename") is not None:
            _path(item["previous_filename"])
        if not isinstance(item.get("status"), str) or item["status"] not in {"added", "modified", "removed", "renamed", "copied", "changed", "unchanged"}:
            raise ValueError("Unknown changed-file status.")
        if item["status"] == "renamed" and not item.get("previous_filename"):
            raise ValueError("Renamed files require previous_filename.")
        for key in ("additions", "deletions"):
            if type(item.get(key)) is not int or not 0 <= item[key] <= 1_000_000_000:
                raise ValueError(f"File {key} must be a nonnegative integer.")
        if item.get("patch") is not None and not isinstance(item["patch"], str):
            raise ValueError("File patch must be text or null.")


def _is_test(path: str) -> bool:
    p = PurePosixPath(path.lower())
    return (bool(set(p.parts[:-1]) & {"test", "tests", "__tests__", "spec", "specs"})
            or p.name.startswith("test_") or bool(re.search(r"(?:_test|[.]test|[.]spec)[.]", p.name)))


def _is_doc(path: str) -> bool:
    p = PurePosixPath(path.lower())
    return (p.suffix in {".md", ".rst", ".txt"}
            or p.name in {"license", "copying", "authors", "notice"})


def _categories(path: str, policy: dict) -> set[str]:
    path = path.lower()
    p = PurePosixPath(path)
    parts = set(re.split(r"[/_.-]+", path))
    result = set()
    if parts & {"auth", "authentication", "authorization", "oauth", "oauth2", "permissions", "permission", "security", "crypto", "jwt", "sessions", "session", "secrets"}:
        result.add("security")
    if any(fnmatch.fnmatchcase(path, pattern.lower()) for pattern in policy["sensitive_paths"]):
        result.add("security")
    if path.startswith(".github/workflows/") or p.name in {"action.yml", "action.yaml"}:
        result.add("workflow")
    if p.name in DEPENDENCIES or re.fullmatch(r"requirements(?:[-.][\w-]+)?[.]txt", p.name) or p.suffix in {".csproj", ".fsproj"}:
        result.add("dependencies")
    if parts & {"migration", "migrations", "schema", "schemas"}:
        result.add("migration")
    if parts & {"api", "public", "include"} or p.suffix in {".proto", ".graphql", ".graphqls"}:
        result.add("public_api")
    if (parts & {"config", "configuration", "terraform", "kubernetes", "helm"}
            or p.name.startswith(("dockerfile", "docker-compose", ".env"))
            or p.suffix in {".tf", ".tfvars"}):
        result.add("configuration")
    return result


def _clean_body(body: str) -> str:
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return re.sub(r"(?m)^\s*[-*]\s*\[ \].*$", "", body)


def _sections(body: str) -> dict[str, str]:
    sections = {}
    heading = "summary"
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            heading = match[1].lower()
        else:
            sections[heading] = sections.get(heading, "") + "\n" + line
    return sections


def _evidence_section(sections: dict, names: str) -> bool:
    for heading, text in sections.items():
        if not re.search(names, heading):
            continue
        meaningful = re.sub(r"[`#*>_\[\]-]", " ", text).strip()
        if len(meaningful) < 12:
            continue
        if re.search(r"\b(todo|tbd|not (?:yet )?(?:run|tested|provided|applicable)|no tests|untested|none|n/a)\b", meaningful, re.I):
            continue
        if re.fullmatch(r"(?is)(?:describe|add|insert|provide)\s+(?:the|your|a)\s+.*(?:here|below)[.!]?", meaningful):
            continue
        return True
    return False


def analyze(snapshot: dict, policy: dict | None = None) -> dict:
    validate_snapshot(snapshot)
    policy = validate_policy(policy)
    files = snapshot["files"]
    uncertainties = []
    if not snapshot["files_complete"] or len(files) != snapshot["changed_files"]:
        uncertainties.append("incomplete_files")
    if not files:
        uncertainties.append("empty_diff")
    if any(not item.get("patch") for item in files):
        uncertainties.append("missing_patch")
    if not snapshot.get("head_sha") or not snapshot.get("base_sha"):
        uncertainties.append("missing_commit_identity")
    groups: dict[str, list[str]] = {}
    for item in files:
        for path in (item["filename"], item.get("previous_filename")):
            if path:
                for kind in _categories(path, policy):
                    groups.setdefault(kind, []).append(path)
    groups = {key: sorted(set(paths)) for key, paths in sorted(groups.items())}
    docs_only = bool(files) and all(_is_doc(f["filename"]) and (
        not f.get("previous_filename") or _is_doc(f["previous_filename"])
    ) for f in files) and not groups
    code_changed = any(not _is_doc(f["filename"]) and not _is_test(f["filename"]) for f in files)
    test_changes = any(_is_test(f["filename"]) and f["additions"] > 0 and f["status"] != "removed" for f in files)
    tests_removed = any(
        (_is_test(f["filename"]) and (f["status"] == "removed" or f["deletions"] > f["additions"]))
        or (f["status"] == "renamed" and _is_test(f["previous_filename"]) and not _is_test(f["filename"]))
        for f in files
    )
    body = _clean_body(snapshot.get("body") or "")
    sections = _sections(body)
    evidence = {
        "scope": _evidence_section(sections, r"summary|scope|description|what|changes"),
        "verification": _evidence_section(sections, r"test|verif|validat"),
        "test_changes": test_changes,
        "linked_issue": bool(re.search(r"(?:\b(?:fix(?:es)?|clos(?:e[sd]?)|resolv(?:e[sd]?))\s+(?:[\w.-]+/[\w.-]+)?#\d+|https://github[.]com/[\w.-]+/[\w.-]+/issues/\d+)", body, re.I)),
        "reproduction": _evidence_section(sections, r"repro|before|bug"),
        "migration": _evidence_section(sections, r"migration|compatib|breaking"),
        "dependency_rationale": _evidence_section(sections, r"dependenc|rationale|why"),
        "rollout": _evidence_section(sections, r"rollout|rollback|deploy"),
    }
    required_evidence = ["scope"]
    if not docs_only:
        required_evidence.append("verification")
    if code_changed:
        required_evidence.append("test_changes")
    if re.search(r"\b(fix|fixes|bug|regression)\b", snapshot.get("title") or "", re.I):
        required_evidence.append("reproduction")
    if "dependencies" in groups:
        required_evidence.append("dependency_rationale")
    if "migration" in groups or "public_api" in groups:
        required_evidence.append("migration")
    if set(groups) & {"security", "migration", "workflow", "configuration"}:
        required_evidence.append("rollout")
    missing = [name for name in required_evidence if not evidence[name]]
    debt = min(100, len(missing) * 15)
    loc = sum(f["additions"] + f["deletions"] for f in files)
    size_score = min(30, int(math.log2(1 + loc) * 2 + math.log2(1 + len(files)) * 3))
    risk = 0 if docs_only else 10 + size_score
    weights = {"security": 55, "workflow": 55, "migration": 35,
               "public_api": 25, "dependencies": 25, "configuration": 20}
    risk += sum(weights[k] for k in groups)
    if tests_removed:
        risk += 15
    if uncertainties:
        risk += 35
    risk = min(100, risk)
    review_score = min(100, risk + int(debt * 0.4))
    tier = sum(review_score >= threshold for threshold in policy["thresholds"])
    if not docs_only:
        tier = max(1, tier)
    if uncertainties or tests_removed or set(groups) & {"dependencies", "public_api", "migration", "configuration"}:
        tier = max(2, tier)
    if set(groups) & {"security", "workflow"}:
        tier = 3
    required = ["format_and_lint"]
    if tier >= 1:
        required += ["unit_tests", "light_review" if tier == 1 else "comprehensive_review"]
    if tier >= 2:
        required += ["integration_tests"]
    if tier >= 3:
        required += ["e2e_tests", "security_review", "human_review"]
    explanations = {
        "security": "A security-sensitive path changed.",
        "workflow": "Workflow or action execution configuration changed.",
        "migration": "A schema or migration path changed.",
        "public_api": "A potential public interface path changed; compatibility is unverified.",
        "dependencies": "A dependency manifest or lockfile changed.",
        "configuration": "Runtime or infrastructure configuration changed.",
    }
    drivers = [{"signal": kind, "message": explanations[kind], "paths": paths} for kind, paths in groups.items()]
    if tests_removed:
        drivers.append({"signal": "tests_reduced", "message": "Tests were removed or have a net line reduction; assess lost coverage.", "paths": []})
    drivers.append({"signal": "change_size", "message": f"{loc} changed lines across {len(files)} observed files.", "paths": []})
    if docs_only:
        drivers.append({"signal": "documentation_only", "message": "All observed paths are documentation with no sensitive path match.", "paths": []})
    return {
        "schema_version": 1,
        "repository": snapshot["repository"], "number": snapshot["number"],
        "head_sha": snapshot.get("head_sha"), "base_sha": snapshot.get("base_sha"),
        "policy_hash": hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest(),
        "mode": "advisory", "tier": tier, "tier_name": TIERS[tier],
        "risk": {"score": risk, "level": _level(risk), "signals": list(groups)},
        "evidence_debt": {"score": debt, "level": _level(debt)},
        "review_cost_score": review_score,
        "evidence": evidence, "missing_evidence": missing,
        "drivers": drivers, "uncertainties": uncertainties,
        "input_complete": not uncertainties,
        "full_ci_required": tier >= 2,
        "human_review_required": tier == 3, "security_review_required": tier == 3,
        "allow_skip_required_checks": False,
        "plan": {"required": required, "optional": ["human_review"] if tier < 3 else [],
                 "next_steps": [f"Provide {name.replace('_', ' ')} evidence." for name in missing]},
        "limitations": [
            "Scores are uncalibrated heuristics, not probabilities or estimates of review time.",
            "Description evidence is self-reported; changed tests do not establish coverage or passing results.",
            "Path matching identifies potential surfaces, not semantic API changes or vulnerabilities.",
            "Recommendations never override branch protection or authorize skipping required checks.",
        ],
    }


def _level(score: int) -> str:
    return "high" if score >= 60 else "medium" if score >= 30 else "low"
