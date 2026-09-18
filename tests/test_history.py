import json
from pathlib import Path
import tempfile
import unittest

from reviewbudget.history import collect_to_file
from test_planner import snapshot


class Client:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def closed_numbers(self, repository, limit):
        self.calls.append("list")
        return list(range(1, min(limit, 3) + 1))

    def fetch_history_record(self, repository, number):
        self.calls.append(number)
        if number == self.fail:
            raise ValueError("Temporary request failure")
        return {"snapshot": snapshot(repository=repository, number=number),
                "outcome": {"review_count": 1, "changes_requested": 0, "review_comments": 2},
                "feature_timing": "final_state", "synthetic": False}


class HistoryTests(unittest.TestCase):
    def test_failure_keeps_completed_records_and_resume_uses_fixed_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            first = Client(fail=2)
            with self.assertRaisesRegex(ValueError, "Temporary"):
                collect_to_file(first, "example/parser", 3, path)
            self.assertEqual(len(path.read_text().splitlines()), 1)
            second = Client()
            result = collect_to_file(second, "example/parser", 3, path, resume=True)
            self.assertEqual(second.calls, [2, 3])
            self.assertEqual(result["completed"], 3)
            self.assertTrue(result["complete"])
            self.assertEqual([json.loads(line)["snapshot"]["number"] for line in path.read_text().splitlines()], [1, 2, 3])

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text("user data")
            for resume in (True, False):
                with self.assertRaises(ValueError):
                    collect_to_file(Client(), "example/parser", 3, path, resume=resume)
            self.assertEqual(path.read_text(), "user data")

    def test_resume_rejects_changed_query(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            collect_to_file(Client(), "example/parser", 3, path)
            with self.assertRaises(ValueError):
                collect_to_file(Client(), "another/repo", 3, path, resume=True)
            with self.assertRaises(ValueError):
                collect_to_file(Client(), "example/parser", 4, path, resume=True)

    def test_wrong_record_identity_is_not_checkpointed(self):
        class Wrong(Client):
            def fetch_history_record(self, repository, number):
                return super().fetch_history_record("other/repo", number)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            with self.assertRaises(ValueError):
                collect_to_file(Wrong(), "example/parser", 3, path)
            self.assertFalse((Path(str(path) + ".checkpoint") / "1.json").exists())

    def test_concurrent_collection_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            collect_to_file(Client(), "example/parser", 3, path)
            lock = Path(str(path) + ".checkpoint") / ".lock"
            lock.write_text("another collector")
            with self.assertRaises(ValueError):
                collect_to_file(Client(), "example/parser", 3, path, resume=True)
            self.assertEqual(lock.read_text(), "another collector")

    def test_completed_resume_does_not_call_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            collect_to_file(Client(), "example/parser", 3, path)
            client = Client()
            result = collect_to_file(client, "example/parser", 3, path, resume=True)
            self.assertEqual(client.calls, [])
            self.assertTrue(result["complete"])

    def test_corrupt_checkpoint_cannot_truncate_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            collect_to_file(Client(), "example/parser", 3, path)
            previous = path.read_bytes()
            (Path(str(path) + ".checkpoint") / "2.json").write_text("invalid")
            with self.assertRaises(ValueError):
                collect_to_file(Client(), "example/parser", 3, path, resume=True)
            self.assertEqual(path.read_bytes(), previous)

    def test_callback_error_retains_all_existing_completed_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            collect_to_file(Client(), "example/parser", 3, path)
            previous = path.read_bytes()
            def fail(done, total):
                raise ValueError("callback failed")
            with self.assertRaises(ValueError):
                collect_to_file(Client(), "example/parser", 3, path, resume=True, progress=fail)
            self.assertEqual(path.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
