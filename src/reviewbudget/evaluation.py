"""Chronological, retrospective screening of a review-burden proxy.

The label is review_count + 2 * changes_requested + review_comments, strictly
above the training 80th percentile. It measures observed review activity, not
defects, correctness, reviewer effort, money saved, or causal benefit. Thresholds
are fitted on training records only; this module never changes routing policy.

All repositories share one creation-time boundary. Training outcomes must end
before it. This avoids training on a future outcome from another repository,
which independent per-repository splits could permit. Per-repository held-out
results are descriptive and are not separate fitted models.
"""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import math


TARGET_RECALL = 0.85
MINIMUM_RECORDS = 300
MINIMUM_REPOSITORIES = 3
MINIMUM_CLASS_COUNT = 10


def _score_snapshot(snapshot):
    from reviewbudget.planner import analyze

    return analyze(snapshot)


def _integer(value, field, *, maximum=2**53 - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("invalid_" + field)
    return value


def _timestamp(value, field, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid_" + field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_" + field) from None
    if parsed.tzinfo is None:
        raise ValueError("timezone_missing_" + field)
    return parsed.astimezone(timezone.utc)


def _identity(record):
    if not isinstance(record, dict) or not isinstance(record.get("snapshot"), dict):
        raise ValueError("missing_snapshot")
    snapshot = record["snapshot"]
    repository = snapshot.get("repository")
    if not isinstance(repository, str) or len(repository.strip().split("/")) != 2:
        raise ValueError("invalid_repository")
    repository = repository.strip().casefold()
    if not all(repository.split("/")):
        raise ValueError("invalid_repository")
    number = _integer(snapshot.get("number"), "number")
    if not number:
        raise ValueError("invalid_number")
    return repository, number


def _prepare(record, identity):
    snapshot = record["snapshot"]
    outcome = record.get("outcome")
    if not isinstance(outcome, dict):
        raise ValueError("missing_outcome")
    created = _timestamp(snapshot.get("created_at"), "created_at")
    outcome_created = _timestamp(outcome.get("created_at"), "outcome_created_at", optional=True)
    if outcome_created is not None and outcome_created != created:
        raise ValueError("inconsistent_created_at")
    closed = _timestamp(outcome.get("closed_at"), "closed_at", optional=True)
    merged = _timestamp(outcome.get("merged_at"), "merged_at", optional=True)
    ended = max((value for value in (closed, merged) if value is not None), default=None)
    if ended is None or any(value < created for value in (closed, merged) if value is not None):
        raise ValueError("outcome_not_completed_or_invalid")
    captured = _timestamp(snapshot.get("captured_at"), "captured_at", optional=True)
    updated = _timestamp(snapshot.get("updated_at"), "updated_at", optional=True)
    first_review = _timestamp(outcome.get("first_review_at"), "first_review_at", optional=True)
    if captured is not None and captured < created:
        raise ValueError("capture_before_creation")
    if first_review is not None and not created <= first_review <= ended:
        raise ValueError("invalid_first_review_order")
    counts = {name: _integer(outcome.get(name), name)
              for name in ("review_count", "changes_requested", "review_comments")}
    if counts["changes_requested"] > counts["review_count"]:
        raise ValueError("changes_requested_exceeds_review_count")
    files = snapshot.get("files")
    if not isinstance(files, list):
        raise ValueError("invalid_files")
    lines = 0
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("filename"), str) or not file["filename"]:
            raise ValueError("invalid_file")
        lines += _integer(file.get("additions"), "additions")
        lines += _integer(file.get("deletions"), "deletions")
    changed_files = _integer(snapshot.get("changed_files"), "changed_files")
    limitations = []
    if snapshot.get("feature_timing") != "pre_review" or record.get("feature_timing", "pre_review") != "pre_review":
        limitations.append("feature_timing_not_pre_review")
    if (snapshot.get("synthetic", record.get("synthetic")) is not False
            or record.get("synthetic", snapshot.get("synthetic")) is not False):
        limitations.append("synthetic_or_unverified_origin")
    if captured is None:
        limitations.append("snapshot_capture_time_missing")
    if updated is None:
        limitations.append("snapshot_update_time_missing")
    if captured is not None and updated is not None and updated > captured:
        limitations.append("snapshot_metadata_after_capture")
    if first_review is None:
        limitations.append("first_review_time_missing")
    if captured is not None and first_review is not None and captured > first_review:
        limitations.append("snapshot_captured_after_first_review")
    if snapshot.get("files_complete") is not True or changed_files != len(files):
        limitations.append("historical_diff_incomplete")
    if not all(isinstance(snapshot.get(key), str) and snapshot[key].strip()
               for key in ("head_sha", "base_sha")):
        limitations.append("historical_revision_missing")
    scored = _score_snapshot(deepcopy(snapshot))
    if not isinstance(scored, dict):
        raise ValueError("invalid_planner_result")
    if scored.get("input_complete") is not True:
        limitations.append("planner_input_incomplete")
    score = _integer(scored.get("review_cost_score"), "review_cost_score", maximum=100)
    tier = _integer(scored.get("tier"), "tier", maximum=3)
    return {
        "repository": identity[0], "number": identity[1], "created": created,
        "ended": ended, "burden": counts["review_count"] + 2 * counts["changes_requested"] + counts["review_comments"],
        "score": score, "baseline": lines + changed_files, "tier": tier,
        "limitations": limitations,
    }


def _percentile80(values):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * 0.8
    lower = math.floor(position)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower])


def _cutoff(rows, score_key):
    positive_scores = sorted((row[score_key] for row in rows if row["positive"]), reverse=True)
    if not positive_scores:
        return None
    return positive_scores[math.ceil(TARGET_RECALL * len(positive_scores)) - 1]


def _selection(rows, selected):
    positives = sum(row["positive"] is True for row in rows)
    selected_positives = sum(row["positive"] is True for row in selected)
    labels_available = all(row["positive"] is not None for row in rows)
    return {
        "selected_count": len(selected),
        "selected_share": len(selected) / len(rows) if rows else None,
        "precision": selected_positives / len(selected) if selected and labels_available else None,
        "recall": selected_positives / positives if positives else None,
    }


def _top20(rows, score_key):
    """Expected top-k precision under uniform selection within a boundary tie."""
    if not rows:
        return {"selected_count": 0, "precision": None, "recall": None,
                "boundary_tie_count": 0, "boundary_tie_weight": None,
                "tie_handling": "fractional_expectation"}
    count = math.ceil(len(rows) * 0.2)
    boundary = sorted((row[score_key] for row in rows), reverse=True)[count - 1]
    above = [row for row in rows if row[score_key] > boundary]
    tied = [row for row in rows if row[score_key] == boundary]
    weight = (count - len(above)) / len(tied)
    selected_positives = sum(row["positive"] is True for row in above) + weight * sum(row["positive"] is True for row in tied)
    positives = sum(row["positive"] is True for row in rows)
    labels_available = all(row["positive"] is not None for row in rows)
    return {
        "selected_count": count, "precision": selected_positives / count if labels_available else None,
        "recall": selected_positives / positives if positives else None,
        "boundary_tie_count": len(tied), "boundary_tie_weight": weight,
        "tie_handling": "fractional_expectation",
    }


def _model_metrics(rows, score_key, cutoff):
    return {
        "top20": _top20(rows, score_key),
        "trained_cutoff": _selection(rows, [row for row in rows if cutoff is not None and row[score_key] >= cutoff]),
    }


def _routing(rows):
    selected = _selection(rows, [row for row in rows if row["tier"] >= 2])
    return {
        "high_burden_recall": selected["recall"],
        "cheap_share": sum(row["tier"] <= 1 for row in rows) / len(rows) if rows else None,
        "expensive_review_share": selected["selected_share"],
        "definition": "Tier 0/1 cheap; Tier 2/3 selected for deeper review; observed planner tiers only",
    }


def evaluate(records: list[dict], holdout_fraction: float = 0.3) -> dict:
    """Return descriptive metrics and conservative retrospective research gates.

    Malformed rows and all copies of duplicated PR identities are excluded and
    counted. Naive timestamps, nonfinite counts, open outcomes, and invalid
    scorer outputs are malformed. Unknown provenance remains exploratory.

    Eligibility requires 300 evaluated PRs, three repositories represented in
    both partitions, ten positive and negative examples per partition, and
    complete non-synthetic pre-review snapshots captured no later than the
    first review. Caller-supplied provenance is checked for consistency; this
    function cannot independently authenticate collection claims. Exclusions
    caused by malformed or duplicate records also keep results exploratory,
    because these filters can bias the sample.
    """
    if not isinstance(records, list):
        raise TypeError("records must be a list")
    if (type(holdout_fraction) not in (int, float)
            or not 0 < holdout_fraction < 1 or not math.isfinite(holdout_fraction)):
        raise ValueError("holdout_fraction must be finite and strictly between 0 and 1")

    errors = Counter()
    identified = []
    for record in records:
        try:
            identified.append((_identity(record), record))
        except ValueError as error:
            errors[str(error)] += 1
    identity_counts = Counter(identity for identity, _ in identified)
    duplicate_identities = {identity for identity, count in identity_counts.items() if count > 1}
    rows = []
    for identity, record in identified:
        if identity in duplicate_identities:
            continue
        try:
            rows.append(_prepare(record, identity))
        except (ValueError, TypeError, KeyError) as error:
            errors[str(error) if isinstance(error, ValueError) else "malformed_record"] += 1
    rows.sort(key=lambda row: (row["created"], row["repository"], row["number"]))
    desired_holdout = max(1, math.ceil(len(rows) * holdout_fraction)) if rows else 0
    boundary = rows[len(rows) - desired_holdout]["created"] if rows else None
    train_candidates = [row for row in rows if row["created"] < boundary] if boundary else []
    train = [row for row in train_candidates if row["ended"] < boundary]
    holdout = [row for row in rows if row["created"] >= boundary] if boundary else []
    overlap_count = len(train_candidates) - len(train)
    burden_threshold = _percentile80([row["burden"] for row in train])
    for row in train + holdout:
        row["positive"] = row["burden"] > burden_threshold if burden_threshold is not None else None
    planner_cutoff = _cutoff(train, "score")
    baseline_cutoff = _cutoff(train, "baseline")
    planner = _model_metrics(holdout, "score", planner_cutoff)
    baseline = _model_metrics(holdout, "baseline", baseline_cutoff)
    routing = _routing(holdout)
    baseline_precision = baseline["top20"]["precision"]
    planner_precision = planner["top20"]["precision"]
    relative_lift = ((planner_precision - baseline_precision) / baseline_precision
                     if baseline_precision is not None and baseline_precision > 0 else None)
    common_repositories = {row["repository"] for row in train} & {row["repository"] for row in holdout}
    limitations = {reason for row in train + holdout for reason in row["limitations"]}
    if errors:
        limitations.add("malformed_records_excluded")
    if duplicate_identities:
        limitations.add("duplicate_records_excluded")
    if len(train) + len(holdout) < MINIMUM_RECORDS:
        limitations.add("fewer_than_300_evaluated_records")
    if len(common_repositories) < MINIMUM_REPOSITORIES:
        limitations.add("fewer_than_3_repositories_in_both_partitions")
    for name, partition in (("train", train), ("holdout", holdout)):
        positive_count = sum(row["positive"] is True for row in partition)
        if positive_count < MINIMUM_CLASS_COUNT:
            limitations.add(name + "_has_fewer_than_10_positive_examples")
        if sum(row["positive"] is False for row in partition) < MINIMUM_CLASS_COUNT:
            limitations.add(name + "_has_fewer_than_10_negative_examples")
    if burden_threshold is None:
        limitations.add("no_training_label_threshold")
    gates = {}
    for name, value, target in (
        ("high_burden_recall", routing["high_burden_recall"], TARGET_RECALL),
        ("cheap_share", routing["cheap_share"], 0.30),
        ("relative_precision_lift", relative_lift, 0.15),
    ):
        gates[name] = {"observed": value, "minimum": target, "passed": value is not None and value >= target}
    eligible = not limitations
    verdict = ("research_gates_met" if all(gate["passed"] for gate in gates.values())
               else "research_gates_not_met") if eligible else "unvalidated"
    per_repository = {}
    for repository in sorted({row["repository"] for row in train + holdout}):
        repository_holdout = [row for row in holdout if row["repository"] == repository]
        per_repository[repository] = {
            "train_count": sum(row["repository"] == repository for row in train),
            "holdout_count": len(repository_holdout),
            "positive_count": sum(row["positive"] is True for row in repository_holdout),
            "unlabeled_count": sum(row["positive"] is None for row in repository_holdout),
            "planner_top20": _top20(repository_holdout, "score"),
            "baseline_top20": _top20(repository_holdout, "baseline"),
            "routing": _routing(repository_holdout),
        }
    return {
        "schema_version": 1,
        "verdict": verdict,
        "evidence_status": "eligible_retrospective" if eligible else "exploratory",
        "product_claims_supported": False,
        "label": {
            "name": "observed_review_burden_proxy",
            "formula": "review_count + 2 * changes_requested + review_comments",
            "positive_rule": "strictly greater than training 80th percentile; boundary ties excluded",
            "interpretation": "Observed review activity only; not defects, correctness, time, or cost",
        },
        "data": {
            "input_records": len(records), "usable_records": len(rows),
            "excluded_malformed_records": sum(errors.values()),
            "malformed_reasons": dict(sorted(errors.items())),
            "duplicate_identities": len(duplicate_identities),
            "excluded_duplicate_records": sum(identity_counts[identity] for identity in duplicate_identities),
        },
        "split": {
            "strategy": "global_pr_created_at_chronological; equal timestamps stay together",
            "requested_holdout_fraction": holdout_fraction,
            "holdout_start": boundary.isoformat() if boundary else None,
            "train_count": len(train), "holdout_count": len(holdout),
            "excluded_overlapping_outcomes": overlap_count,
            "training_outcome_rule": "closed_at and merged_at, when present, must both precede holdout_start",
            "repositories_in_both_partitions": sorted(common_repositories),
        },
        "training": {
            "burden_threshold": burden_threshold, "target_recall": TARGET_RECALL,
            "planner_score_cutoff": planner_cutoff, "baseline_score_cutoff": baseline_cutoff,
            "positive_count": sum(row["positive"] is True for row in train),
            "negative_count": sum(row["positive"] is False for row in train),
        },
        "holdout": {
            "positive_count": sum(row["positive"] is True for row in holdout),
            "negative_count": sum(row["positive"] is False for row in holdout),
            "unlabeled_count": sum(row["positive"] is None for row in holdout),
        },
        "planner": planner,
        "baseline": {"definition": "sum(additions + deletions) + changed_files", **baseline},
        "routing": routing,
        "comparison": {
            "relative_precision_lift": relative_lift,
            "lift_definition": "(planner precision@top20 - baseline precision@top20) / baseline precision@top20",
            "zero_baseline_rule": "relative lift is undefined and cannot pass its gate",
        },
        "gates": gates,
        "limitations": sorted(limitations),
        "per_repository": per_repository,
        "interpretation": [
            "Research screening gates only; passing does not establish statistical significance or generalization.",
            "No observed or simulated cost savings are claimed; Tier 0/1 share is a routing count only.",
            "The policy is fixed; only label and diagnostic ranking cutoffs are fitted on training data.",
            "Final-state snapshots, unknown provenance, and synthetic examples are exploratory only.",
            "Provenance is caller supplied and must be audited against historical revisions before relying on results.",
        ],
    }
