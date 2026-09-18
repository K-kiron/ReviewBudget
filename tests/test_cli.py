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

    def test_markdown_escapes_imported_evidence_and_queue_notes(self):
        report = analyze(snapshot())
        report["missing_evidence"] = ["<img onerror=x>"]
        text = render({"reports": [report], "budget": {"note": "<img onerror=x>"}}, "markdown")
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)

    def test_incomplete_local_report_handles_unknown_commits(self):
        report = analyze(snapshot(source="local_git", base_sha=None, head_sha=None))
        self.assertIn("base unknown / head unknown", render(report))

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

    def test_action_exports_selected_checks_and_original_capture(self):
        class Client:
            def capture_pr(self, repository, number):
                return snapshot(feature_timing="pre_review", synthetic=False, source="github_rest",
                                body="Private description kept only in the snapshot")
        with tempfile.TemporaryDirectory() as directory:
            env = self.action_environment(directory)
            policy = Path(directory, "policy.toml")
            from reviewbudget.onboarding import initialize
            preset = initialize(directory)
            policy.write_text(preset.read_text(), encoding="utf-8")
            artifacts = Path(directory, "artifacts")
            env.update(REVIEWBUDGET_POLICY=str(policy), REVIEWBUDGET_ARTIFACT_DIR=str(artifacts))
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_action(Client()), 0)
            outputs = dict(line.split("=", 1) for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines())
            self.assertIn("lint", json.loads(outputs["selected-checks"]))
            self.assertEqual(outputs["coverage-complete"], "true")
            report = json.loads((artifacts / "report.json").read_text())
            captured = json.loads((artifacts / "snapshot.json").read_text())
            self.assertEqual(report["feature_timing"], "pre_review")
            self.assertIn("Private description", captured["body"])
            self.assertNotIn("Private description", (artifacts / "report.html").read_text(encoding="utf-8"))
            self.assertNotIn("Private description", (artifacts / "report.json").read_text())

    def test_action_failure_emits_no_optional_skip_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.action_environment(directory)
            with patch.dict(os.environ, env), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_action(object()), 2)
            outputs = Path(env["GITHUB_OUTPUT"]).read_text()
            self.assertIn("selected-checks=[]", outputs)
            self.assertIn("coverage-complete=false", outputs)
            self.assertIn("allow-skip-required-checks=false", outputs)


class PublicWorkflowTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_installed_demo_requires_no_token_or_checkout(self):
        code, output, errors = self.invoke(["demo", "--scenario", "auth", "--format", "json"])
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(report["tier"], 3)
        self.assertTrue(report["synthetic"])
        self.assertTrue(report["checks"])

    def test_capture_preserves_existing_snapshot_before_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "capture.json")
            path.write_text("original observation", encoding="utf-8")
            with patch("reviewbudget.cli._client") as client:
                self.assertEqual(self.invoke(["capture", "example/parser#1", "--output", str(path)])[0], 2)
                client.assert_not_called()
            self.assertEqual(path.read_text(), "original observation")

    def test_render_rejects_malformed_reports_without_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory, "report.json"), Path(directory, "output.html")
            source.write_text('{"tier": 0, "checks": []}')
            destination.write_text("previous report")
            for format in ("html", "text", "markdown", "json"):
                code, _, errors = self.invoke(["report", str(source), "--format", format, "--output", str(destination)])
                self.assertEqual(code, 2)
                self.assertNotIn("Traceback", errors)
                self.assertEqual(destination.read_text(), "previous report")

    def test_queue_html_preserves_global_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "report.json")
            self.assertEqual(self.invoke(["demo", "--scenario", "auth", "--format", "json", "--output", str(source)])[0], 0)
            code, html, errors = self.invoke(["allocate", "--input", str(source), "--budget-minutes", "1", "--format", "html"])
            self.assertEqual(code, 0, errors)
            self.assertIn('"limit_minutes": 1.0', html)
            self.assertIn('"shortfall_minutes": 106.0', html)
            self.assertIn('id="queue-summary"', html)

    def test_init_generates_policy_but_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke(["init", directory, "--preset", "python"])[0], 0)
            path = Path(directory, ".reviewbudget.toml")
            contents = path.read_text()
            self.assertIn("[[checks]]", contents)
            self.assertEqual(self.invoke(["init", directory])[0], 2)
            self.assertEqual(path.read_text(), contents)

    def test_demo_html_runs_offline_and_contains_all_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "demo.html")
            code, _, errors = self.invoke(["demo", "--format", "html", "--output", str(output)])
            self.assertEqual(code, 0, errors)
            document = output.read_text(encoding="utf-8")
            self.assertIn("ReviewBudget", document)
            self.assertNotIn('<script src="http', document)
            self.assertGreater(len(document), 5000)

    def test_allocate_combines_report_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "report.json")
            code, _, errors = self.invoke(["demo", "--scenario", "auth", "--format", "json", "--output", str(source)])
            self.assertEqual(code, 0, errors)
            code, output, errors = self.invoke(["allocate", "--input", str(source), "--budget-minutes", "1"])
            self.assertEqual(code, 0, errors)
            result = json.loads(output)
            self.assertGreater(result["budget"]["shortfall_minutes"], 0)
            self.assertEqual(result["reports"][0]["tier"], 3)

    def test_evaluate_accepts_multiple_histories(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory, "a.jsonl"), Path(directory, "b.jsonl")
            a.write_text("")
            b.write_text("")
            code, output, errors = self.invoke(["evaluate", "--input", str(a), "--input", str(b)])
            self.assertEqual(code, 0, errors)
            self.assertEqual(json.loads(output)["data"]["input_records"], 0)

    def test_report_accepts_lists_without_inventing_a_queue_budget(self):
        reports = [analyze(snapshot(number=number)) for number in (1, 2)]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "reports.json")
            source.write_text(json.dumps(reports), encoding="utf-8")
            for format in ("text", "markdown", "json"):
                with self.subTest(format=format):
                    code, output, errors = self.invoke(["report", str(source), "--format", format])
                    self.assertEqual(code, 0, errors)
                    if format == "json":
                        self.assertEqual(json.loads(output), {"reports": reports})
                    else:
                        self.assertIn("example/parser #1", output)
                        self.assertIn("example/parser #2", output)
                        self.assertNotIn("Queue budget:", output)

    def test_snapshot_join_rejects_malformed_records_without_traceback(self):
        malformed = [None, {}, {"snapshot": []},
                     {"snapshot": {"repository": "example/parser", "number": []}},
                     {"snapshot": {"repository": [], "number": 1}},
                     {"snapshot": {"repository": "example/parser", "number": True}}]
        with tempfile.TemporaryDirectory() as directory:
            captures = Path(directory, "captures")
            captures.mkdir()
            Path(captures, "snapshot.json").write_text(json.dumps(snapshot()), encoding="utf-8")
            history = Path(directory, "history.jsonl")
            output_path = Path(directory, "evaluation.json")
            output_path.write_text("previous evaluation", encoding="utf-8")
            for record in malformed:
                with self.subTest(record=record):
                    history.write_text(json.dumps(record) + "\n", encoding="utf-8")
                    code, output, errors = self.invoke(["evaluate", "--input", str(history),
                                                       "--snapshots", str(captures), "--output", str(output_path)])
                    self.assertEqual(code, 2)
                    self.assertEqual(output, "")
                    self.assertNotIn("Traceback", errors)
                    self.assertEqual(output_path.read_text(encoding="utf-8"), "previous evaluation")

    def test_snapshot_join_preserves_original_and_outcome_provenance(self):
        from reviewbudget.cli import _join_snapshots
        captured = snapshot(feature_timing="pre_review", synthetic=True)
        final = snapshot(feature_timing="final_state", synthetic=False, head_sha="c" * 40)
        record = {"snapshot": final, "feature_timing": "final_state", "synthetic": False,
                  "outcome": {"review_count": 2, "changes_requested": 1, "review_comments": 3}}
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "original.json").write_text(json.dumps(captured), encoding="utf-8")
            joined = _join_snapshots([record], directory)
        self.assertEqual(joined[0]["snapshot"], captured)
        self.assertEqual(joined[0]["outcome"], record["outcome"])
        self.assertTrue(joined[0]["snapshot"]["synthetic"])
        self.assertFalse(joined[0]["synthetic"])
        self.assertEqual(joined[0]["feature_timing"], "pre_review")
        self.assertEqual(record["snapshot"]["head_sha"], "c" * 40)

    def test_gh_auth_uses_absolute_installation_outside_the_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo, installed = root / "checkout", root / "installed"
            cwd, sibling = repo / "src", repo / "bin"
            for path in (repo / ".git", cwd, sibling, installed, cwd / "tools"):
                path.mkdir(parents=True, exist_ok=True)
            executable = "gh.exe" if os.name == "nt" else "gh"
            for path in (root, repo, cwd, sibling, cwd / "tools", installed):
                binary = path / executable
                binary.write_text("test executable placeholder", encoding="utf-8")
                binary.chmod(0o755)
            search = os.pathsep.join(map(str, (cwd, root, repo, sibling, cwd / "tools", ".", installed)))
            result = subprocess.CompletedProcess([], 0, "test-private-token\n", "")
            with contextlib.chdir(cwd), patch.dict(os.environ, {"PATH": search}), \
                    patch("reviewbudget.cli.subprocess.run", return_value=result) as run, \
                    patch("reviewbudget.github.GitHubClient") as client:
                client.return_value.fetch_pr.return_value = snapshot()
                code, output, errors = self.invoke(["analyze", "example/parser#1", "--auth", "gh", "--format", "json"])
            self.assertEqual(code, 0, errors)
            self.assertEqual(run.call_args.args[0], [str(installed / executable), "auth", "token", "--hostname", "github.com"])
            self.assertEqual(Path(run.call_args.kwargs["cwd"]), installed)
            client.assert_called_once_with(token="test-private-token")
            self.assertNotIn("test-private-token", output + errors)

    def test_gh_auth_refuses_checkout_only_executable_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            executable = repo / ("gh.exe" if os.name == "nt" else "gh")
            executable.write_text("untrusted executable", encoding="utf-8")
            executable.chmod(0o755)
            with contextlib.chdir(repo), patch.dict(os.environ, {"PATH": str(repo) + os.pathsep + "."}), \
                    patch("reviewbudget.cli.subprocess.run") as run:
                code, output, errors = self.invoke(["analyze", "example/parser#1", "--auth", "gh"])
            self.assertEqual(code, 2)
            self.assertEqual(output, "")
            self.assertNotIn("Traceback", errors)
            run.assert_not_called()

    def test_github_cli_authentication_requires_explicit_opt_in(self):
        with patch("reviewbudget.cli.subprocess.run") as run, \
                patch("reviewbudget.github.GitHubClient") as client:
            client.return_value.fetch_pr.return_value = snapshot()
            code, output, errors = self.invoke(["analyze", "example/parser#1", "--format", "json"])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["repository"], "example/parser")
        client.assert_called_once_with()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
