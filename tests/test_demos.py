import os
from pathlib import Path
import tempfile
import unittest

from reviewbudget.demos import load_policy, load_scenario, names
from reviewbudget.planner import analyze, validate_snapshot


class DemoTests(unittest.TestCase):
    def test_all_packaged_demos_are_synthetic_and_meaningful(self):
        self.assertEqual(names(), ["docs", "tested-code", "auth", "migration-dependency", "incomplete"])
        for name in names():
            with self.subTest(name=name):
                snapshot = load_scenario(name)
                validate_snapshot(snapshot)
                self.assertTrue(snapshot["synthetic"])
                self.assertEqual(snapshot["source"], "synthetic_demo")
                self.assertIn("Synthetic", snapshot["title"])
                self.assertTrue(analyze(snapshot)["evidence"]["scope"])
                for item in snapshot["files"]:
                    if item["patch"] is not None:
                        lines = item["patch"].splitlines()
                        self.assertEqual(item["additions"], sum(line.startswith("+") for line in lines))
                        self.assertEqual(item["deletions"], sum(line.startswith("-") for line in lines))

    def test_demonstrates_floor_and_evidence_differences(self):
        self.assertEqual(analyze(load_scenario("docs"))["tier"], 0)
        tested = analyze(load_scenario("tested-code"))
        self.assertEqual(tested["tier"], 1)
        self.assertEqual(tested["missing_evidence"], [])
        self.assertEqual(analyze(load_scenario("auth"))["tier"], 3)
        migration = analyze(load_scenario("migration-dependency"))
        self.assertGreaterEqual(migration["tier"], 2)
        self.assertIn("migration", migration["risk"]["signals"])
        self.assertIn("dependencies", migration["risk"]["signals"])
        incomplete = analyze(load_scenario("incomplete"))
        self.assertGreaterEqual(incomplete["tier"], 2)
        self.assertFalse(incomplete["input_complete"])
        self.assertIn("incomplete_files", incomplete["uncertainties"])
        self.assertIn("missing_patch", incomplete["uncertainties"])

    def test_resources_load_outside_checkout_and_are_independent(self):
        current = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                scenario = load_scenario("docs")
                policy = load_policy()
                self.assertTrue(policy["checks"])
                self.assertEqual(load_policy("node")["budget"]["limit_minutes"], 45)
            finally:
                os.chdir(current)
        scenario["files"].clear()
        policy["checks"].clear()
        self.assertTrue(load_scenario("docs")["files"])
        self.assertTrue(load_policy()["checks"])

    def test_names_do_not_become_resource_paths(self):
        for name in ("../docs", "docs.json", "missing"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_scenario(name)
        with self.assertRaises(ValueError):
            load_policy("../python")


if __name__ == "__main__":
    unittest.main()
