import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from reviewbudget.local import capture_local
from reviewbudget.planner import analyze, validate_snapshot


class LocalCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repository with spaces"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Local test")
        self.git("config", "user.email", "local@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.write("README.md", "Original documentation.\n")
        self.base = self.commit("Initial fixture")

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        result = subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return result.stdout.decode("utf-8").strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_committed_diff_from_outside_repository_preserves_metadata(self):
        self.write("docs/old name é.md", "Alpha\nBeta\nGamma\n")
        self.write("removed.py", "old\n")
        base = self.commit("Add originals")
        self.git("mv", "docs/old name é.md", "docs/new name é.md")
        self.write("docs/new name é.md", "Alpha\nBeta\nGamma\nDelta\n")
        (self.repo / "removed.py").unlink()
        self.write("new file.py", "first\nsecond\n")
        self.write("image.bin", b"\x00\xff\x01")
        head = self.commit("Change fixtures")
        self.write("untracked.py", "Excluded\n")
        self.write("new file.py", "Uncommitted change\n")
        current = Path.cwd()
        try:
            os.chdir(self.temporary.name)
            result = capture_local(self.repo, base, title="Local comparison", body="Scope details")
        finally:
            os.chdir(current)
        validate_snapshot(result)
        self.assertEqual(result["head_sha"], head)
        self.assertEqual(result["base_sha"], base)
        self.assertEqual(result["merge_base_sha"], base)
        self.assertEqual(result["source"], "local_git")
        self.assertEqual(result["feature_timing"], "local")
        self.assertFalse(result["synthetic"])
        self.assertTrue(result["provenance"]["number_is_placeholder"])
        self.assertEqual(result["changed_files"], 4)
        files = {item["filename"]: item for item in result["files"]}
        renamed = files["docs/new name é.md"]
        self.assertEqual(renamed["status"], "renamed")
        self.assertEqual(renamed["previous_filename"], "docs/old name é.md")
        self.assertEqual((renamed["additions"], renamed["deletions"]), (1, 0))
        self.assertIn("+Delta", renamed["patch"])
        self.assertEqual(files["removed.py"]["deletions"], 1)
        self.assertEqual(files["removed.py"]["status"], "removed")
        self.assertEqual(files["new file.py"]["additions"], 2)
        self.assertIn("+second", files["new file.py"]["patch"])
        self.assertNotIn("Uncommitted", files["new file.py"]["patch"])
        self.assertTrue(files["image.bin"]["binary"])
        self.assertIsNone(files["image.bin"]["patch"])
        self.assertIn("binary_patch", result["capture_uncertainties"])
        self.assertIn("missing_patch", analyze(result)["uncertainties"])

    def test_uses_merge_base_while_recording_requested_base(self):
        self.git("checkout", "-qb", "feature")
        self.write("feature.py", "feature\n")
        head = self.commit("Feature work")
        self.git("checkout", "-qb", "advanced-base", self.base)
        self.write("base-only.py", "base work\n")
        advanced_base = self.commit("Independent base work")
        result = capture_local(self.repo, "advanced-base", "feature")
        self.assertEqual(result["base_sha"], advanced_base)
        self.assertEqual(result["head_sha"], head)
        self.assertEqual(result["merge_base_sha"], self.base)
        self.assertEqual([f["filename"] for f in result["files"]], ["feature.py"])
        self.assertEqual(self.git("branch", "--show-current"), "advanced-base")
        self.assertEqual(result, capture_local(self.repo, "advanced-base", "feature"))

    def test_git_helpers_are_not_executed(self):
        marker = Path(self.temporary.name) / "helper-executed"
        helper = Path(self.temporary.name) / "helper.py"
        helper.write_text("from pathlib import Path\n" +
                          f"Path({str(marker)!r}).write_text('executed')\n" +
                          "print('converted content')\n", encoding="utf-8")
        command = f'"{Path(sys.executable).as_posix()}" "{helper.as_posix()}"'
        self.write(".gitattributes", "*.py diff=hostile filter=hostile\n")
        self.write("code.py", "original\n")
        base = self.commit("Original helper fixture")
        self.write("code.py", "changed\n")
        self.commit("Updated helper fixture")
        self.git("config", "diff.external", command)
        self.git("config", "diff.hostile.textconv", command)
        self.git("config", "filter.hostile.clean", command)
        self.git("config", "filter.hostile.smudge", command)
        self.git("config", "core.fsmonitor", command)
        # Positive controls establish that both configured diff helpers are usable.
        self.git("diff", "--ext-diff", base, "HEAD", "--", "code.py")
        self.assertTrue(marker.exists())
        marker.unlink()
        self.git("diff", "--no-ext-diff", "--textconv", base, "HEAD", "--", "code.py")
        self.assertTrue(marker.exists())
        marker.unlink()
        with patch.dict(os.environ, {"GIT_EXTERNAL_DIFF": command,
                                    "GIT_CONFIG_COUNT": "1",
                                    "GIT_CONFIG_KEY_0": "diff.external",
                                    "GIT_CONFIG_VALUE_0": command}):
            result = capture_local(self.repo, base)
        self.assertFalse(marker.exists())
        self.assertIn("+changed", result["files"][0]["patch"])
        self.assertEqual(result["files"][0]["additions"], 1)

    def test_file_and_patch_limits_are_conservative(self):
        for index in range(3):
            self.write(f"{index}.py", "x" * 200 + "\n")
        self.commit("Three files")
        with patch("reviewbudget.local.MAX_FILES", 2):
            result = capture_local(self.repo, self.base)
        self.assertEqual(result["changed_files"], 3)
        self.assertEqual(len(result["files"]), 2)
        self.assertFalse(result["files_complete"])
        self.assertIn("file_limit", result["capture_uncertainties"])
        with patch("reviewbudget.local.MAX_PATCH_BYTES", 100):
            result = capture_local(self.repo, self.base)
        self.assertTrue(all(item["patch"] is None for item in result["files"]))
        self.assertTrue(result["files_complete"])
        self.assertIn("patch_size_limit", result["capture_uncertainties"])
        self.assertFalse(analyze(result)["input_complete"])
        with patch("reviewbudget.local.MAX_TOTAL_PATCH_BYTES", 100):
            result = capture_local(self.repo, self.base)
        self.assertTrue(all(item["patch"] is None for item in result["files"]))
        self.assertIn("patch_output_limit", result["capture_uncertainties"])

    def test_origin_credentials_and_local_paths_are_not_captured(self):
        self.git("remote", "add", "origin", "https://private-user:secret-token@github.com/example/project.git")
        result = capture_local(self.repo, self.base)
        self.assertEqual(result["repository"], "example/project")
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertNotIn("private-user", json.dumps(result))
        self.assertNotIn(str(self.repo), json.dumps(result))
        self.git("remote", "set-url", "origin", "git@github.com:example/project.git")
        self.assertEqual(capture_local(self.repo, self.base)["repository"], "example/project")
        self.git("remote", "set-url", "origin", "https://github.com.evil.invalid/example/project")
        self.assertEqual(capture_local(self.repo, self.base)["repository"], "local/project")

    def test_rejects_bad_refs_and_non_repository_with_safe_errors(self):
        for ref in ("--help", "", "missing-secret-ref", "HEAD\x00", 123):
            with self.subTest(ref=ref), self.assertRaises(ValueError) as error:
                capture_local(self.repo, ref)
            self.assertNotIn("missing-secret-ref", str(error.exception))
        with self.assertRaises(ValueError):
            capture_local(self.temporary.name, "HEAD")

    def test_does_not_load_repository_policy(self):
        self.write(".reviewbudget.toml", "invalid untrusted policy")
        self.write("README.md", "Updated documentation.\n")
        self.commit("Documentation change with unrelated policy")
        result = capture_local(self.repo, self.base)
        validate_snapshot(result)
        self.assertNotIn("policy", result)

    def test_repository_git_executable_cannot_shadow_installed_git(self):
        shadow_name = "git.exe" if os.name == "nt" else "git"
        self.write(shadow_name, b"not a Git executable\n")
        (self.repo / shadow_name).chmod(0o755)
        self.write("README.md", "A committed documentation change.\n")
        self.commit("Shadowing fixture")
        current = Path.cwd()
        try:
            os.chdir(self.repo)
            with patch.dict(os.environ, {"PATH": str(self.repo) + os.pathsep + "." + os.pathsep + os.environ["PATH"]}):
                result = capture_local(self.repo, self.base)
        finally:
            os.chdir(current)
        self.assertEqual(result["changed_files"], 2)


if __name__ == "__main__":
    unittest.main()
