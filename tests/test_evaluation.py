"""Behavior checks for retrospective review-burden screening."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import unittest
from unittest.mock import patch

from reviewbudget.evaluation import evaluate


def history(count=360, *, feature_timing="pre_review", synthetic=False):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    records = []
    for index in range(count):
        created = start + timedelta(days=index)
        costly = index % 5 == 4
        timestamp = lambda value: value.isoformat().replace("+00:00", "Z")
        records.append({
            "snapshot": {
                "repository": "example/repo" + str(index % 3),
                "number": index + 1,
                "title": "Change " + str(index + 1),
                "body": "",
                "created_at": timestamp(created),
                "updated_at": timestamp(created),
                "captured_at": timestamp(created + timedelta(minutes=1)),
                "head_sha": "a" * 40,
                "base_sha": "b" * 40,
                "changed_files": 1,
                "files_complete": True,
                "files": [{"filename": "src/main.py", "status": "modified",
                           "additions": 10, "deletions": 1, "patch": "@@ -1 +1 @@\n-old\n+new"}],
                "feature_timing": feature_timing,
                "synthetic": synthetic,
            },
            "outcome": {
                "review_count": 8 if costly else 1,
                "changes_requested": 2 if costly else 0,
                "review_comments": 8 if costly else 0,
                "first_review_at": timestamp(created + timedelta(hours=1)),
                "created_at": timestamp(created),
                "closed_at": timestamp(created + timedelta(hours=3)),
                "merged_at": timestamp(created + timedelta(hours=3)),
            },
            "feature_timing": feature_timing,
            "synthetic": synthetic,
        })
    return records


def predictive(snapshot):
    costly = snapshot["number"] % 5 == 0
    return {"review_cost_score": 90 if costly else 10, "tier": 3 if costly else 1, "input_complete": True}


class EvaluationTests(unittest.TestCase):
    def evaluate(self, records, fraction=0.3, scorer=predictive):
        with patch("reviewbudget.evaluation._score_snapshot", side_effect=scorer):
            return evaluate(records, fraction)

    def test_eligible_data_reports_research_gates_but_no_product_claim(self):
        result = self.evaluate(history())
        self.assertEqual(result["verdict"], "research_gates_met")
        self.assertEqual(result["evidence_status"], "eligible_retrospective")
        self.assertFalse(result["product_claims_supported"])
        self.assertEqual(result["routing"]["high_burden_recall"], 1)
        self.assertGreaterEqual(result["routing"]["cheap_share"], 0.7)
        self.assertGreater(result["comparison"]["relative_precision_lift"], 0.15)

    def test_final_state_cannot_be_upgraded_by_record_level_label(self):
        records = history(feature_timing="final_state")
        for record in records:
            record["feature_timing"] = "pre_review"
        result = self.evaluate(records)
        self.assertEqual(result["verdict"], "unvalidated")
        self.assertIn("feature_timing_not_pre_review", result["limitations"])
        self.assertEqual(result["evidence_status"], "exploratory")

    def test_synthetic_and_small_samples_never_support_verdict(self):
        result = self.evaluate(history(40, synthetic=True))
        self.assertEqual(result["verdict"], "unvalidated")
        self.assertIn("synthetic_or_unverified_origin", result["limitations"])
        self.assertIn("fewer_than_300_evaluated_records", result["limitations"])

    def test_missing_first_review_or_late_capture_is_exploratory(self):
        records = history()
        del records[0]["outcome"]["first_review_at"]
        records[1]["snapshot"]["captured_at"] = records[1]["outcome"]["closed_at"]
        result = self.evaluate(records)
        self.assertIn("first_review_time_missing", result["limitations"])
        self.assertIn("snapshot_captured_after_first_review", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_training_outcomes_that_cross_holdout_start_are_excluded(self):
        records = history()
        records[0]["outcome"]["closed_at"] = records[-1]["outcome"]["closed_at"]
        records[0]["outcome"]["merged_at"] = records[-1]["outcome"]["merged_at"]
        result = self.evaluate(records)
        self.assertEqual(result["split"]["excluded_overlapping_outcomes"], 1)
        self.assertEqual(result["split"]["train_count"], 251)
        self.assertEqual(result["split"]["holdout_count"], 108)

    def test_holdout_outcomes_do_not_change_training_thresholds(self):
        records = history()
        first = self.evaluate(records)
        for record in records[252:]:
            record["outcome"]["review_comments"] = 1000
        second = self.evaluate(records)
        self.assertEqual(first["training"], second["training"])
        self.assertEqual(second["verdict"], "unvalidated")
        self.assertIn("holdout_has_fewer_than_10_negative_examples", second["limitations"])

    def test_duplicate_identity_removes_every_copy(self):
        records = history(40)
        records.append(deepcopy(records[0]))
        result = self.evaluate(records)
        self.assertEqual(result["data"]["duplicate_identities"], 1)
        self.assertEqual(result["data"]["excluded_duplicate_records"], 2)
        self.assertEqual(result["data"]["usable_records"], 39)
        self.assertIn("duplicate_records_excluded", result["limitations"])

    def test_nonfinite_and_malformed_records_are_reported_without_crashing(self):
        records = history(40)
        records[0]["outcome"]["review_count"] = math.nan
        records[1]["snapshot"]["files"][0]["additions"] = -1
        records[2]["snapshot"]["created_at"] = "yesterday"
        records.extend([None, {}])
        result = self.evaluate(records)
        self.assertEqual(result["data"]["excluded_malformed_records"], 5)
        self.assertEqual(result["data"]["usable_records"], 37)
        self.assertEqual(result["verdict"], "unvalidated")
        self.assertIn("malformed_records_excluded", result["limitations"])

    def test_zero_burden_has_no_positives_and_undefined_recall(self):
        records = history(40)
        for record in records:
            record["outcome"].update(review_count=0, changes_requested=0, review_comments=0)
        result = self.evaluate(records)
        self.assertIsNone(result["routing"]["high_burden_recall"])
        self.assertIsNone(result["comparison"]["relative_precision_lift"])
        self.assertFalse(result["gates"]["high_burden_recall"]["passed"])

    def test_tied_scores_use_fractional_expected_precision(self):
        result = self.evaluate(history(40), scorer=lambda _: {"review_cost_score": 10, "tier": 1})
        top = result["planner"]["top20"]
        expected = result["holdout"]["positive_count"] / result["split"]["holdout_count"]
        self.assertAlmostEqual(top["precision"], expected)
        self.assertEqual(top["boundary_tie_count"], result["split"]["holdout_count"])
        self.assertEqual(result["comparison"]["relative_precision_lift"], 0)

    def test_routing_gate_uses_actual_tier_not_learned_score_cutoff(self):
        result = self.evaluate(history(), scorer=lambda row: {**predictive(row), "tier": 0})
        self.assertEqual(result["planner"]["trained_cutoff"]["recall"], 1)
        self.assertEqual(result["routing"]["high_burden_recall"], 0)
        self.assertEqual(result["verdict"], "research_gates_not_met")

    def test_incomplete_snapshot_blocks_evidence(self):
        records = history()
        records[0]["snapshot"]["files_complete"] = False
        result = self.evaluate(records)
        self.assertIn("historical_diff_incomplete", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_planner_input_incomplete_blocks_evidence_even_with_all_files(self):
        result = self.evaluate(history(), scorer=lambda row: {**predictive(row), "input_complete": False})
        self.assertEqual(result["data"]["usable_records"], 360)
        self.assertIn("planner_input_incomplete", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_changes_requested_cannot_exceed_reviews(self):
        records = history()
        records[0]["outcome"]["changes_requested"] = 2
        result = self.evaluate(records)
        self.assertEqual(result["data"]["malformed_reasons"]["changes_requested_exceeds_review_count"], 1)
        self.assertIn("malformed_records_excluded", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_filtered_large_dataset_remains_exploratory(self):
        records = history()
        records.append(deepcopy(records[0]))
        records.append(None)
        result = self.evaluate(records)
        self.assertGreaterEqual(result["split"]["train_count"] + result["split"]["holdout_count"], 300)
        self.assertIn("malformed_records_excluded", result["limitations"])
        self.assertIn("duplicate_records_excluded", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_equal_creation_times_do_not_cross_split(self):
        records = history(40)
        for record in records:
            record["snapshot"]["created_at"] = records[0]["snapshot"]["created_at"]
            record["outcome"]["created_at"] = records[0]["snapshot"]["created_at"]
        result = self.evaluate(records)
        self.assertEqual(result["split"]["train_count"], 0)
        self.assertEqual(result["split"]["holdout_count"], 40)
        self.assertEqual(result["verdict"], "unvalidated")
        self.assertIsNone(result["planner"]["top20"]["precision"])
        self.assertEqual(result["holdout"]["unlabeled_count"], 40)
        self.assertEqual(result["holdout"]["negative_count"], 0)

    def test_future_snapshot_metadata_blocks_evidence(self):
        records = history()
        records[0]["snapshot"]["updated_at"] = records[0]["outcome"]["closed_at"]
        result = self.evaluate(records)
        self.assertIn("snapshot_metadata_after_capture", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_zero_baseline_precision_does_not_produce_infinite_lift(self):
        records = history()
        for record in records:
            record["snapshot"]["files"][0]["additions"] = 0 if record["snapshot"]["number"] % 5 == 0 else 100
        result = self.evaluate(records)
        self.assertEqual(result["baseline"]["top20"]["precision"], 0)
        self.assertIsNone(result["comparison"]["relative_precision_lift"])
        self.assertFalse(result["gates"]["relative_precision_lift"]["passed"])

    def test_unknown_origin_blocks_evidence(self):
        records = history()
        del records[0]["synthetic"]
        del records[0]["snapshot"]["synthetic"]
        result = self.evaluate(records)
        self.assertEqual(result["verdict"], "unvalidated")
        self.assertIn("synthetic_or_unverified_origin", result["limitations"])

    def test_single_repository_does_not_meet_research_coverage(self):
        records = history()
        for record in records:
            record["snapshot"]["repository"] = "example/one-repo"
        result = self.evaluate(records)
        self.assertIn("fewer_than_3_repositories_in_both_partitions", result["limitations"])
        self.assertEqual(result["verdict"], "unvalidated")

    def test_invalid_planner_output_is_excluded(self):
        result = self.evaluate(history(40), scorer=lambda _: {"review_cost_score": math.inf, "tier": 2})
        self.assertEqual(result["data"]["excluded_malformed_records"], 40)
        self.assertEqual(result["data"]["usable_records"], 0)

    def test_invalid_fraction_is_rejected(self):
        for fraction in (0, 1, -0.1, math.nan, math.inf, True, "0.3", 10**1000):
            with self.subTest(fraction=fraction), self.assertRaises(ValueError):
                self.evaluate([], fraction)

    def test_empty_input_is_explicitly_unvalidated(self):
        result = self.evaluate([])
        self.assertEqual(result["verdict"], "unvalidated")
        self.assertEqual(result["data"]["input_records"], 0)

    def test_input_is_not_mutated(self):
        records = history(40)
        original = deepcopy(records)
        self.evaluate(records)
        self.assertEqual(records, original)


if __name__ == "__main__":
    unittest.main()
