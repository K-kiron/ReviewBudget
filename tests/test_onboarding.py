from pathlib import Path
import tempfile
import tomllib
import unittest

from reviewbudget.onboarding import initialize, policy_text


class OnboardingTests(unittest.TestCase):
    def test_presets_create_explicit_policy_with_illustrative_estimates(self):
        for preset, command in (("python", "python -m unittest"), ("node", "npm test")):
            with self.subTest(preset=preset), tempfile.TemporaryDirectory() as directory:
                path = initialize(directory, preset)
                self.assertEqual(path, Path(directory) / ".reviewbudget.toml")
                text = path.read_text(encoding="utf-8")
                policy = tomllib.loads(text)
                self.assertIn("illustrative", text)
                self.assertIn(command, text)
                self.assertEqual(policy["budget"]["limit_minutes"], 45)
                self.assertEqual(len(policy["checks"]), 8)
                self.assertTrue(policy["checks"][0]["required"])
                self.assertIn("--policy .reviewbudget.toml", text)

    def test_existing_policy_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".reviewbudget.toml"
            path.write_text("existing project policy", encoding="utf-8")
            with self.assertRaises(ValueError):
                initialize(directory)
            self.assertEqual(path.read_text(encoding="utf-8"), "existing project policy")

    def test_invalid_preset_or_directory_does_not_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                initialize(directory, "../python")
            self.assertEqual(list(Path(directory).iterdir()), [])
            with self.assertRaises(ValueError):
                initialize(Path(directory) / "missing")
            with self.assertRaises(ValueError):
                policy_text("ruby")


if __name__ == "__main__":
    unittest.main()
