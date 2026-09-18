import copy
import math
import unittest

from reviewbudget.budget import allocate
from reviewbudget.planner import analyze
from test_planner import file, snapshot


def check(check_id, covers, cost, **options):
    return {"id": check_id, "name": check_id.replace("-", " "), "covers": covers,
            "estimated_minutes": cost, **options}


class QueueBudgetTests(unittest.TestCase):
    def test_mandatory_shortfall_is_retained_across_queue(self):
        policy = {"checks": [check("lint", ["format_and_lint"], 4), check("legal", [], 7, required=True)]}
        reports = [analyze(snapshot([file("README.md")], number=n), policy) for n in (1, 2)]
        result = allocate(reports, 3)
        self.assertEqual(result["budget"]["shortfall_minutes"], 19)
        self.assertFalse(result["budget"]["within_budget"])
        self.assertTrue(all(c["selected"] for r in result["reports"] for c in r["checks"]))
        self.assertEqual([r["tier"] for r in result["reports"]], [r["tier"] for r in reports])
        self.assertTrue(result["recommendations"])

    def test_unpriced_mandatory_work_blocks_optional_spending_and_exact_total(self):
        report = analyze(snapshot([file("README.md")]), {"checks": [
            check("lint", ["format_and_lint"], None), check("review", ["human_review"], 1),
        ]})
        result = allocate([report], 100)
        self.assertIsNone(result["budget"]["estimated_minutes"])
        self.assertIsNone(result["budget"]["remaining_minutes"])
        self.assertIsNone(result["budget"]["within_budget"])
        self.assertEqual(result["budget"]["unpriced_checks"], ["example/parser#1/lint"])
        self.assertEqual([c["id"] for c in result["reports"][0]["checks"] if c["selected"]], ["lint"])

    def test_unpriced_optional_work_is_visible_but_never_treated_as_free(self):
        report = analyze(snapshot([file("README.md")]), {"checks": [
            check("lint", ["format_and_lint"], 1), check("review", ["human_review"], None),
        ]})
        result = allocate([report], 100)
        self.assertEqual(result["budget"]["estimated_minutes"], 1)
        self.assertIsNone(result["budget"]["full_baseline_minutes"])
        self.assertIsNone(result["budget"]["modeled_savings_minutes"])
        self.assertEqual(result["budget"]["unpriced_configured_checks"], ["example/parser#1/review"])
        self.assertFalse(next(c for c in result["reports"][0]["checks"] if c["id"] == "review")["selected"])

    def test_capability_gaps_block_optional_upgrades_even_when_cost_is_known(self):
        report = analyze(snapshot(), {"checks": [check("extra", [], 1)]})
        result = allocate([report], 100)
        self.assertIsNone(result["budget"]["estimated_minutes"])
        self.assertIn("example/parser#1/unit_tests", result["budget"]["unmapped_capabilities"])
        self.assertFalse(result["reports"][0]["checks"][0]["selected"])

    def test_priority_then_cost_then_identity_break_optional_ties(self):
        report = analyze(snapshot([file("README.md")]), {"checks": [
            check("lint", ["format_and_lint"], 1),
            check("first", [], 3, priority=5), check("cheap-a", [], 1, priority=4),
            check("cheap-b", [], 1, priority=4), check("expensive", [], 2, priority=4),
        ]})
        result = allocate([report], 5)
        self.assertEqual({c["id"] for c in result["reports"][0]["checks"] if c["selected"]}, {"lint", "first", "cheap-a"})

    def test_invalid_budget_and_duplicate_reports_are_rejected(self):
        report = analyze(snapshot([file("README.md")]))
        for value in (-1, True, "5", math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                allocate([report], value)
        for reports in ([], [report, report], [{}]):
            with self.subTest(reports=reports), self.assertRaises(ValueError):
                allocate(reports, 10)

    def test_overflowing_declared_totals_are_rejected(self):
        with self.assertRaises(ValueError):
            analyze(snapshot([file("README.md")]), {"checks": [
                check("a", ["format_and_lint"], 1e308), check("b", [], 1e308, required=True),
            ]})

    def test_queue_replaces_stale_per_report_budget_recommendations(self):
        report = analyze(snapshot([file("README.md")]), {"checks": [
            check("lint", ["format_and_lint"], 5),
        ], "budget": {"limit_minutes": 1}})
        self.assertEqual(report["budget"]["shortfall_minutes"], 4)
        result = allocate([report], 8)
        self.assertTrue(result["budget"]["within_budget"])
        self.assertEqual(result["reports"][0]["recommendations"], [])

    def test_fractional_estimates_fit_exact_declared_budget(self):
        report = analyze(snapshot([file("README.md")]), {"checks": [
            check("lint", ["format_and_lint"], 0.1),
            check("review", ["human_review"], 0.2),
        ]})
        result = allocate([report], 0.3)
        self.assertTrue(all(c["selected"] for c in result["reports"][0]["checks"]))
        self.assertEqual(result["budget"]["estimated_minutes"], 0.3)
        self.assertTrue(result["budget"]["within_budget"])

    def test_queue_preserves_mandatory_work_then_upgrades_higher_risk_first(self):
        policy = {"checks": [check("lint", ["format_and_lint"], 1),
                             check("maintainer", ["human_review"], 3, priority=10)]}
        low = analyze(snapshot([file("README.md")], number=1), policy)
        high = analyze(snapshot([file("README.md")], number=2, body=""), policy)
        before = copy.deepcopy([low, high])
        result = allocate([low, high], 5)
        self.assertEqual(result["budget"]["mandatory_known_minutes"], 2)
        self.assertEqual(result["budget"]["estimated_minutes"], 5)
        selected = {(r["number"], c["id"]) for r in result["reports"] for c in r["checks"] if c["selected"]}
        self.assertEqual(selected, {(1, "lint"), (2, "lint"), (2, "maintainer")})
        self.assertEqual([low, high], before)
        self.assertEqual(result, allocate([high, low], 5))


if __name__ == "__main__":
    unittest.main()
