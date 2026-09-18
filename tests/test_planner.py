import copy
import unittest

from reviewbudget.planner import analyze, validate_policy


def snapshot(files=None, **overrides):
    files = files if files is not None else [
        {"filename": "src/parser.py", "status": "modified", "additions": 8,
         "deletions": 2, "patch": "@@ -1 +1 @@\n-old\n+new"},
        {"filename": "tests/test_parser.py", "status": "modified", "additions": 8,
         "deletions": 0, "patch": "@@ -1 +1 @@\n+test"},
    ]
    result = {
        "repository": "example/parser", "number": 1, "title": "Handle empty input",
        "body": "## Summary\nHandle empty input without raising a parsing error.\n"
                "## Testing\n`python -m unittest` passed locally with the empty input case.\n",
        "head_sha": "a" * 40, "base_sha": "b" * 40,
        "files": files, "changed_files": len(files), "files_complete": True,
    }
    result.update(overrides)
    return result


def file(path, **overrides):
    value = {"filename": path, "status": "modified", "additions": 2,
             "deletions": 1, "patch": "@@ -1 +1 @@\n-a\n+b"}
    value.update(overrides)
    return value


class PlannerTests(unittest.TestCase):
    def test_small_tested_change_gets_light_verification(self):
        result = analyze(snapshot())
        self.assertEqual(result["tier"], 1)
        self.assertIn("unit_tests", result["plan"]["required"])
        self.assertEqual(result["mode"], "advisory")
        self.assertFalse(result["allow_skip_required_checks"])

    def test_documentation_only_gets_deterministic_checks(self):
        result = analyze(snapshot([file("docs/usage.md")]))
        self.assertEqual(result["tier"], 0)
        self.assertFalse(result["full_ci_required"])

    def test_security_cannot_be_downgraded_by_good_evidence(self):
        result = analyze(snapshot([file("src/auth/login.py"), file("tests/test_login.py")]))
        self.assertEqual(result["tier"], 3)
        self.assertIn("security_review", result["plan"]["required"])
        self.assertIn("human_review", result["plan"]["required"])

    def test_workflow_change_requires_highest_tier(self):
        self.assertEqual(analyze(snapshot([file(".github/workflows/build.yml")]))["tier"], 3)

    def test_renamed_sensitive_file_still_requires_review(self):
        result = analyze(snapshot([file("docs/old.md", status="renamed", previous_filename="src/auth.py")]))
        self.assertEqual(result["tier"], 3)

    def test_metadata_completeness_cannot_be_forged(self):
        result = analyze(snapshot(changed_files=20, files_complete=True))
        self.assertGreaterEqual(result["tier"], 2)
        self.assertFalse(result["input_complete"])

    def test_missing_patch_is_unknown_not_zero_risk(self):
        result = analyze(snapshot([file("README.md", patch=None)]))
        self.assertGreaterEqual(result["tier"], 2)
        self.assertIn("missing_patch", result["uncertainties"])

    def test_empty_diff_does_not_qualify_for_tier_zero(self):
        self.assertGreaterEqual(analyze(snapshot([]))["tier"], 2)

    def test_deleted_tests_do_not_pay_test_evidence_debt(self):
        result = analyze(snapshot([file("src/parser.py"), file("tests/test_parser.py", status="removed", additions=0)]))
        self.assertIn("test_changes", result["missing_evidence"])
        self.assertGreaterEqual(result["tier"], 2)

    def test_renaming_tests_out_of_discovery_preserves_removal_floor(self):
        result = analyze(snapshot([file("docs/old.md", status="renamed", previous_filename="tests/test_parser.py")]))
        self.assertGreaterEqual(result["tier"], 2)
        self.assertIn("tests_reduced", [driver["signal"] for driver in result["drivers"]])

    def test_non_string_file_status_is_validation_error(self):
        for status in ([], {}, 1):
            with self.subTest(status=status), self.assertRaises(ValueError):
                analyze(snapshot([file("x.py", status=status)]))

    def test_unticked_template_and_comments_are_not_evidence(self):
        body = "<!-- fixes #42 -->\n## Summary\nTODO\n## Testing\n- [ ] Tests passed\n## Reproduction\nN/A"
        result = analyze(snapshot(body=body, title="Fix regression"))
        self.assertIn("scope", result["missing_evidence"])
        self.assertIn("verification", result["missing_evidence"])
        self.assertIn("reproduction", result["missing_evidence"])
        self.assertFalse(result["evidence"]["linked_issue"])

    def test_negated_testing_statement_is_not_evidence(self):
        result = analyze(snapshot(body="## Summary\nRepair a parser edge case.\n## Testing\nTests were not run because the suite is expensive."))
        self.assertIn("verification", result["missing_evidence"])

    def test_dependencies_need_rationale_and_full_ci(self):
        result = analyze(snapshot([file("package.json")]))
        self.assertGreaterEqual(result["tier"], 2)
        self.assertIn("dependency_rationale", result["missing_evidence"])

    def test_migration_needs_rollout_and_migration_evidence(self):
        result = analyze(snapshot([file("db/migrations/003.sql")]))
        self.assertIn("migration", result["missing_evidence"])
        self.assertIn("rollout", result["missing_evidence"])

    def test_custom_patterns_raise_risk(self):
        result = analyze(snapshot([file("lib/money.py")]), {"sensitive_paths": ["lib/money.py"]})
        self.assertEqual(result["tier"], 3)

    def test_policy_cannot_remove_builtin_safety_floor(self):
        result = analyze(snapshot([file("src/auth.py")]), {"sensitive_paths": [], "thresholds": [80, 90, 100]})
        self.assertEqual(result["tier"], 3)

    def test_invalid_policy_is_not_silently_ignored(self):
        for policy in [{"typo": 1}, {"thresholds": [30, 20, 80]}, {"sensitive_paths": "*"}, {"thresholds": [False, 30, 80]}]:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_policy(policy)

    def test_invalid_snapshot_rejected(self):
        for changed in [snapshot([file("../auth.py")]), snapshot([file("x", additions=-1)]), snapshot(files_complete="yes"), snapshot(number=True)]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                analyze(changed)

    def test_duplicate_paths_rejected(self):
        with self.assertRaises(ValueError):
            analyze(snapshot([file("x.py"), file("x.py")]))

    def test_analysis_is_deterministic_and_does_not_mutate_input(self):
        value = snapshot()
        before = copy.deepcopy(value)
        self.assertEqual(analyze(value), analyze(value))
        self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
