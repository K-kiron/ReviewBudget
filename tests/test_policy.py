import copy
import json
import math
from pathlib import Path
import tomllib
import unittest

from reviewbudget.planner import analyze, validate_policy


class PolicyTests(unittest.TestCase):
    def test_shipped_budget_policy_covers_security_work_without_hiding_shortfall(self):
        root = Path(__file__).resolve().parents[1]
        policy = tomllib.loads((root / "examples" / "budget-policy.toml").read_text(encoding="utf-8"))
        snapshot = json.loads((root / "examples" / "auth-change.json").read_text(encoding="utf-8"))
        report = analyze(snapshot, policy)
        self.assertEqual(report["tier"], 3)
        self.assertEqual(report["budget"]["unmapped_capabilities"], [])
        self.assertEqual(report["budget"]["estimated_minutes"], 98)
        self.assertEqual(report["budget"]["shortfall_minutes"], 68)
        self.assertTrue(all(c["selected"] and c["mandatory"] for c in report["checks"]))

    def test_nested_typos_and_invalid_costs_cannot_silently_change_a_budget(self):
        valid = {"id": "lint", "name": "Ruff lint", "covers": ["format_and_lint"]}
        policies = [
            {"checks": [{**valid, "minutes": 3}]},
            {"budget": {"minutes": 3}},
            {"checks": [{**valid, "covers": ["unit_test"]}]},
            {"checks": [{**valid, "paths": []}]},
            {"checks": [{**valid, "required": 1}]},
            {"checks": [{**valid, "priority": True}]},
            {"checks": [{**valid, "priority": -1}]},
            {"checks": [{**valid, "id": "../lint"}]},
            {"checks": [{**valid, "name": "lint\nnext"}]},
            {"checks": [valid, valid]},
        ]
        for cost in (True, "2", -1, math.inf, -math.inf, math.nan, 10 ** 1000):
            policies += [{"checks": [{**valid, "estimated_minutes": cost}]}, {"budget": {"limit_minutes": cost}}]
        for policy in policies:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_policy(policy)

    def test_legacy_settings_remain_supported_and_normalization_is_idempotent(self):
        legacy = {"thresholds": [15, 40, 70], "sensitive_paths": ["src/payments/*"]}
        value = validate_policy(legacy)
        self.assertEqual(value["thresholds"], legacy["thresholds"])
        self.assertEqual(value["sensitive_paths"], legacy["sensitive_paths"])
        self.assertEqual(value["checks"], [])
        self.assertEqual(value, validate_policy(value))

    def test_policy_normalization_is_order_independent_and_does_not_mutate(self):
        value = {"checks": [
            {"id": "z", "name": "Review", "covers": ["human_review", "light_review"], "paths": ["src/*", "lib/*"]},
            {"id": "a", "name": "Lint", "covers": ["format_and_lint"]},
        ]}
        before = copy.deepcopy(value)
        reversed_policy = copy.deepcopy(value)
        reversed_policy["checks"].reverse()
        reversed_policy["checks"][1]["paths"].reverse()
        reversed_policy["checks"][1]["covers"].reverse()
        self.assertEqual(validate_policy(value), validate_policy(reversed_policy))
        self.assertEqual(value, before)

    def test_named_check_policy_is_normalized_without_inventing_a_price(self):
        result = validate_policy({"checks": [{
            "id": "lint", "name": "Ruff lint", "covers": ["format_and_lint"],
        }], "budget": {"limit_minutes": 10}})
        self.assertEqual(result["checks"][0], {
            "id": "lint", "name": "Ruff lint", "covers": ["format_and_lint"],
            "paths": ["*"], "estimated_minutes": None, "required": False,
            "priority": 0,
        })
        self.assertEqual(result["budget"]["limit_minutes"], 10)


if __name__ == "__main__":
    unittest.main()
