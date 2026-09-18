import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from reviewbudget.cli import main, render, run_action
from reviewbudget.planner import analyze
from test_planner import snapshot


class CliTests(unittest.TestCase):
    def test_offline_json_command(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["analyze", "--input", str(source), "--format", "json"]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["tier"], 1)
            self.assertEqual(report["mode"], "advisory")

    def test_invalid_input_is_failure_without_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text('{"files": []}', encoding="utf-8")
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = main(["analyze", "--input", str(source), "--format", "json"])
            self.assertEqual(code, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertNotIn("Traceback", errors.getvalue())

    def test_markdown_escapes_untrusted_paths(self):
        value = snapshot()
        value["files"][0]["filename"] = "src/auth/<img onerror=x>.py"
        text = render(analyze(value), "markdown")
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)

    def test_policy_file_changes_sensitive_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            source, policy, output = [Path(directory) / name for name in ("in.json", "policy.toml", "out.json")]
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            policy.write_text('sensitive_paths = ["src/parser.py"]\n', encoding="utf-8")
            self.assertEqual(main(["analyze", "--input", str(source), "--policy", str(policy), "--format", "json", "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text())["tier"], 3)

    def test_json_with_nonfinite_numbers_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text('{"bad": NaN}', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["analyze", "--input", str(source)]), 2)

    def test_nested_json_is_a_clean_input_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text("[" * 1500 + "0" + "]" * 1500, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["analyze", "--input", str(source)]), 2)

    def test_nested_action_event_still_emits_conservative_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.action_environment(directory)
            Path(env["GITHUB_EVENT_PATH"]).write_text("[" * 1500 + "0" + "]" * 1500)
            with patch.dict(os.environ, env), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_action(object()), 2)
            self.assertIn("tier=3", Path(env["GITHUB_OUTPUT"]).read_text())

    def action_environment(self, directory):
        event = Path(directory) / "event.json"
        event.write_text(json.dumps({"number": 1, "pull_request": {"number": 1,
            "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}}), encoding="utf-8")
        return {"GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "example/parser",
                "GITHUB_EVENT_NAME": "pull_request", "GITHUB_OUTPUT": str(Path(directory) / "outputs"),
                "GITHUB_STEP_SUMMARY": str(Path(directory) / "summary"), "REVIEWBUDGET_POLICY": ""}

    def test_action_outputs_are_scalar_and_do_not_execute_body(self):
        class Client:
            def fetch_pr(self, repository, number):
                return snapshot(body="$(touch ATTACK)\n## Testing\necho 'test passed' from a description")
        with tempfile.TemporaryDirectory() as directory:
            env = self.action_environment(directory)
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_action(Client()), 0)
            outputs = Path(env["GITHUB_OUTPUT"]).read_text()
            self.assertIn("allow-skip-required-checks=false", outputs)
            self.assertNotIn("ATTACK", outputs)
            self.assertFalse((Path(directory) / "ATTACK").exists())

    def test_stale_event_fails_closed(self):
        class Client:
            def fetch_pr(self, repository, number):
                return snapshot(head_sha="c" * 40)
        with tempfile.TemporaryDirectory() as directory:
            env = self.action_environment(directory)
            with patch.dict(os.environ, env), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_action(Client()), 2)
            self.assertIn("full-ci-required=true", Path(env["GITHUB_OUTPUT"]).read_text())
            self.assertIn("input-complete=false", Path(env["GITHUB_OUTPUT"]).read_text())

    def test_api_failure_cannot_emit_a_cheap_tier(self):
        class Client:
            def fetch_pr(self, repository, number):
                raise ValueError("Request failed")
        with tempfile.TemporaryDirectory() as directory:
            env = self.action_environment(directory)
            with patch.dict(os.environ, env), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_action(Client()), 2)
            self.assertIn("tier=3", Path(env["GITHUB_OUTPUT"]).read_text())

    def test_action_isolated_mode_does_not_import_working_directory_code(self):
        import reviewbudget
        action = Path(reviewbudget.__file__).with_name("action.py")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "json.py").write_text("raise RuntimeError('WORKSPACE_IMPORT_EXECUTED')")
            env = {**os.environ, **self.action_environment(directory), "GITHUB_EVENT_NAME": "push"}
            result = subprocess.run([sys.executable, "-I", str(action)], cwd=directory,
                                    env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("WORKSPACE_IMPORT_EXECUTED", result.stderr)
            self.assertIn("tier=3", Path(env["GITHUB_OUTPUT"]).read_text())


if __name__ == "__main__":
    unittest.main()
