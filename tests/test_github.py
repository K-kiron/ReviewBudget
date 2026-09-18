"""Behavior checks for the read-only GitHub input adapter."""

import json
import unittest
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from reviewbudget.github import GitHubClient, GitHubError, parse_pr_url


def pr_data(number=12, changed_files=1):
    return {
        "number": number, "title": "Improve checks", "body": None,
        "created_at": "2026-08-01T10:00:00Z", "updated_at": "2026-08-02T10:00:00Z",
        "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40},
        "changed_files": changed_files, "state": "closed", "review_comments": 2,
        "closed_at": "2026-08-02T10:00:00Z", "merged_at": None,
    }


def file_data(name="src/new.py"):
    return {"filename": name, "status": "renamed", "additions": 3, "deletions": 1,
            "previous_filename": "src/old.py", "patch": "@@ -1 +1,3 @@\n-old\n+new"}


def page_response(data, next_url):
    return 200, {"Link": f'<{next_url}>; rel="next"'}, json.dumps(data).encode()


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        data = self.responses.pop(0)
        if isinstance(data, tuple):
            return data
        return 200, {}, json.dumps(data).encode("utf-8")


class ParsePullRequestTests(unittest.TestCase):
    def test_accepts_pr_urls_and_rejects_ambiguous_or_unsafe_inputs(self):
        for value in (
            "https://github.com/octocat/Hello-World/pull/12",
            "github.com/octocat/Hello-World/pull/12",
            "octocat/Hello-World#12",
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_pr_url(value), ("octocat/Hello-World", 12))
        for value in (
            "http://github.com/o/r/pull/1", "https://example.com/o/r/pull/1",
            "https://secret@github.com/o/r/pull/1", "https://github.com:443/o/r/pull/1",
            "https://github.com/o/r/pull/1/files", "https://github.com/o/r/pull/1?q=2",
            "https://github.com/o/r/pull/1#discussion", "https://github.com/o/r/pull/0",
            "https://github.com/o/r/pull/1/", "o/../r#1", "o/r#-1", "o/r#true",
            "\nhttps://github.com/o/r/pull/1", "https://github.com/o/r/pull/1\n",
        ):
            with self.subTest(value=value), self.assertRaises(GitHubError):
                parse_pr_url(value)


class FetchPullRequestTests(unittest.TestCase):
    def test_missing_body_and_null_or_omitted_patch_remain_unknown(self):
        details = {key: value for key, value in pr_data().items() if key != "body"}
        for include_patch in (True, False):
            with self.subTest(include_patch=include_patch):
                file = {key: value for key, value in file_data().items() if key != "patch"}
                if include_patch:
                    file["patch"] = None
                client = GitHubClient(token="")
                client._transport = ScriptedTransport([details, [file], details])
                snapshot = client.fetch_pr("octocat/Hello-World", 12)
                self.assertEqual(snapshot["body"], "")
                self.assertIsNone(snapshot["files"][0].get("patch"))

    def test_marks_truncated_files_incomplete_and_rejects_duplicate_or_excess_files(self):
        client = GitHubClient(token="")
        client._transport = ScriptedTransport([pr_data(changed_files=2), [file_data()], pr_data(changed_files=2)])
        self.assertFalse(client.fetch_pr("octocat/Hello-World", 12)["files_complete"])
        for count, files in ((2, [file_data(), file_data()]), (0, [file_data()])):
            with self.subTest(count=count):
                client._transport = ScriptedTransport([pr_data(changed_files=count), files, pr_data(changed_files=count)])
                with self.assertRaises(GitHubError):
                    client.fetch_pr("octocat/Hello-World", 12)
        responses = [pr_data(changed_files=3001)]
        for page in range(1, 31):
            files = [file_data(f"src/{page}-{item}.py") for item in range(100)]
            responses.append(page_response(files, f"https://api.github.com/repos/octocat/Hello-World/pulls/12/files?per_page=100&page={page + 1}"))
        responses.append(pr_data(changed_files=3001))
        transport = ScriptedTransport(responses)
        client._transport = transport
        snapshot = client.fetch_pr("octocat/Hello-World", 12)
        self.assertEqual(len(snapshot["files"]), 3000)
        self.assertEqual(snapshot["changed_files"], 3001)
        self.assertFalse(snapshot["files_complete"])
        self.assertEqual(len(transport.requests), 32)

    def test_rejects_changes_during_capture(self):
        for key, value in (("head", {"sha": "c" * 40}), ("base", {"sha": "c" * 40}),
                           ("updated_at", "2026-08-03T10:00:00Z"), ("changed_files", 2)):
            with self.subTest(key=key):
                changed = {**pr_data(), key: value}
                client = GitHubClient(token="")
                client._transport = ScriptedTransport([pr_data(), [file_data()], changed])
                with self.assertRaisesRegex(GitHubError, "changed during capture"):
                    client.fetch_pr("octocat/Hello-World", 12)

    def test_rejects_malformed_prs_and_files_without_exposing_payloads(self):
        invalid_prs = [[], {}, {**pr_data(), "number": 999},
                        {**pr_data(), "changed_files": True},
                        {**pr_data(), "changed_files": -1},
                        {**pr_data(), "title": {"secret": "sensitive"}},
                        {**pr_data(), "body": ["sensitive"]},
                        {**pr_data(), "created_at": "yesterday"},
                        {**pr_data(), "updated_at": "2026-08-02"},
                        {**pr_data(), "head": {"sha": "bad"}}]
        client = GitHubClient(token="")
        for data in invalid_prs:
            with self.subTest(data=data):
                client._transport = ScriptedTransport([data, [file_data()], pr_data()])
                with self.assertRaises(GitHubError) as error:
                    client.fetch_pr("octocat/Hello-World", 12)
                self.assertNotIn("sensitive", str(error.exception))
        invalid_files = [None, {}, {**file_data(), "additions": True},
                         {**file_data(), "deletions": -1},
                         {**file_data(), "filename": ""},
                         {**file_data(), "patch": {"secret": "sensitive"}},
                         {**file_data(), "previous_filename": None},
                         {**file_data(), "status": "unknown"}]
        invalid_files += [file_data(path) for path in ("../secret", "/absolute", "src\\file.py", "src//file.py", "C:file.py", "src/file\n.py")]
        invalid_files.append({key: value for key, value in file_data().items() if key != "previous_filename"})
        for data in invalid_files:
            with self.subTest(data=data):
                client._transport = ScriptedTransport([pr_data(), [data], pr_data()])
                with self.assertRaises(GitHubError):
                    client.fetch_pr("octocat/Hello-World", 12)

    def test_paginates_files_without_following_foreign_links(self):
        page2 = "https://api.github.com/repos/octocat/Hello-World/pulls/12/files?per_page=100&page=2"
        first_files = [file_data(f"src/{number}.py") for number in range(100)]
        transport = ScriptedTransport([
            pr_data(changed_files=101), page_response(first_files, page2),
            [file_data("src/last.py")], pr_data(changed_files=101),
        ])
        client = GitHubClient(token="")
        client._transport = transport
        snapshot = client.fetch_pr("octocat/Hello-World", 12)
        self.assertEqual(len(snapshot["files"]), 101)
        self.assertTrue(snapshot["files_complete"])
        self.assertEqual(transport.requests[2].full_url, page2)
        for url in (page2.replace("api.github.com", "evil.example"),
                    page2.replace("https:", "http:"),
                    page2.replace("page=2", "page=1"),
                    "https://[invalid",
                    page2.replace("/files?", "/reviews?")):
            with self.subTest(url=url):
                transport = ScriptedTransport([pr_data(changed_files=101), page_response(first_files, url)])
                client._transport = transport
                with self.assertRaises(GitHubError):
                    client.fetch_pr("octocat/Hello-World", 12)
                self.assertEqual(len(transport.requests), 2)

    def test_fetches_read_only_consistent_snapshot_and_preserves_rename(self):
        transport = ScriptedTransport([pr_data(), [file_data()], pr_data()])
        client = GitHubClient(token="test-secret")
        client._transport = transport
        snapshot = client.fetch_pr("octocat/Hello-World", 12)
        self.assertEqual(snapshot["repository"], "octocat/Hello-World")
        self.assertEqual(snapshot["body"], "")
        self.assertEqual(snapshot["files"], [file_data()])
        self.assertEqual(snapshot["head_sha"], "a" * 40)
        self.assertEqual(snapshot["base_sha"], "b" * 40)
        self.assertTrue(snapshot["files_complete"])
        self.assertEqual(snapshot["feature_timing"], "final_state")
        self.assertFalse(snapshot["synthetic"])
        self.assertTrue(snapshot["captured_at"].endswith("Z"))
        self.assertEqual([request.full_url for request in transport.requests], [
            "https://api.github.com/repos/octocat/Hello-World/pulls/12",
            "https://api.github.com/repos/octocat/Hello-World/pulls/12/files?per_page=100&page=1",
            "https://api.github.com/repos/octocat/Hello-World/pulls/12",
        ])
        for request in transport.requests:
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(headers["authorization"], "Bearer test-secret")
            self.assertEqual(headers["x-github-api-version"], "2022-11-28")


class CollectHistoryTests(unittest.TestCase):
    def test_rejects_invalid_candidates_outcomes_reviews_and_changed_review_counts(self):
        client = GitHubClient(token="")
        for candidates in ([{}], [{"number": 12, "state": "open"}],
                           [{"number": 12, "state": "closed"}] * 2):
            with self.subTest(candidates=candidates):
                client._transport = ScriptedTransport([candidates])
                with self.assertRaises(GitHubError):
                    client.collect_history("octocat/Hello-World")
        for review in ({}, {"id": 1, "state": "UNKNOWN"}, {"id": True, "state": "APPROVED"},
                       {"id": 1, "state": "APPROVED", "submitted_at": "yesterday"}):
            with self.subTest(review=review):
                client._transport = ScriptedTransport([[{"number": 12, "state": "closed"}],
                                                      pr_data(), [file_data()], pr_data(), [review]])
                with self.assertRaises(GitHubError):
                    client.collect_history("octocat/Hello-World")
        for final in ({**pr_data(), "review_comments": 3}, {**pr_data(), "state": "open"}):
            with self.subTest(final=final):
                client._transport = ScriptedTransport([[{"number": 12, "state": "closed"}],
                                                      pr_data(), [file_data()], pr_data(), [], final])
                with self.assertRaises(GitHubError):
                    client.collect_history("octocat/Hello-World")

    def test_unknown_review_submission_time_is_not_filled_in(self):
        client = GitHubClient(token="")
        client._transport = ScriptedTransport([[{"number": 12, "state": "closed"}],
            pr_data(), [file_data()], pr_data(), [{"id": 1, "state": "APPROVED"}], pr_data()])
        outcome = client.collect_history("octocat/Hello-World")[0]["outcome"]
        self.assertEqual(outcome["review_count"], 1)
        self.assertIsNone(outcome["first_review_at"])

    def test_collects_closed_prs_and_all_submitted_reviews_with_final_state_provenance(self):
        history_page2 = "https://api.github.com/repos/octocat/Hello-World/pulls?state=closed&sort=updated&direction=desc&per_page=100&page=2"
        reviews_page2 = "https://api.github.com/repos/octocat/Hello-World/pulls/12/reviews?per_page=100&page=2"
        reviews = [{"id": 1, "state": "PENDING"}, {"id": 2, "state": "CHANGES_REQUESTED", "submitted_at": "2026-08-01T11:00:00Z"}]
        responses = [page_response([{"number": 12, "state": "closed"}], history_page2),
                     [{"number": 13, "state": "closed"}],
                     pr_data(), [file_data()], pr_data(), page_response(reviews, reviews_page2),
                     [{"id": 3, "state": "APPROVED", "submitted_at": "2026-08-02T09:00:00Z"},
                      {"id": 4, "state": "DISMISSED", "submitted_at": "2026-08-02T09:30:00Z"}], pr_data(),
                     pr_data(13), [file_data()], pr_data(13), [], pr_data(13)]
        client = GitHubClient(token="")
        transport = ScriptedTransport(responses)
        client._transport = transport
        records = client.collect_history("octocat/Hello-World", limit=2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["outcome"], {
            "review_count": 3, "changes_requested": 1, "review_comments": 2,
            "created_at": "2026-08-01T10:00:00Z", "closed_at": "2026-08-02T10:00:00Z",
            "merged_at": None, "first_review_at": "2026-08-01T11:00:00Z",
        })
        self.assertEqual(records[1]["outcome"]["review_count"], 0)
        for record in records:
            self.assertEqual(record["feature_timing"], "final_state")
            self.assertFalse(record["synthetic"])
            self.assertEqual(record["snapshot"]["feature_timing"], "final_state")
        self.assertEqual(transport.responses, [])


class TransportSafetyTests(unittest.TestCase):
    def test_invalid_inputs_fail_before_network_access(self):
        client = GitHubClient(token="")
        transport = ScriptedTransport([])
        client._transport = transport
        for number in (True, 0, -1, "12", 1.0, None):
            with self.subTest(number=number), self.assertRaises(GitHubError):
                client.fetch_pr("octocat/Hello-World", number)
        for limit in (True, 0, -1, "12", 1.0, None, 1001):
            with self.subTest(limit=limit), self.assertRaises(GitHubError):
                client.collect_history("octocat/Hello-World", limit)
        for repository in ("../secret", "a/b/c", "a/r?query", "a/r#1"):
            with self.subTest(repository=repository), self.assertRaises(GitHubError):
                client.fetch_pr(repository, 12)
        self.assertEqual(transport.requests, [])

    def test_rejects_invalid_json_and_unexpected_page_shapes(self):
        client = GitHubClient(token="")
        for body in (b'{"secret":', b'\xff', b'x' * (16 * 1024 * 1024 + 1),
                     b'[' * 1500 + b'0' + b']' * 1500):
            with self.subTest(size=len(body)):
                client._transport = ScriptedTransport([(200, {}, body)])
                with self.assertRaises(GitHubError):
                    client.fetch_pr("octocat/Hello-World", 12)
        for page in ({"message": "sensitive"}, [file_data()] * 101):
            with self.subTest(page_type=type(page).__name__):
                client._transport = ScriptedTransport([pr_data(), page])
                with self.assertRaises(GitHubError):
                    client.fetch_pr("octocat/Hello-World", 12)

    def test_tokens_use_only_explicit_or_documented_environment_values(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "github-token", "GH_TOKEN": "gh-token"}, clear=True):
            for explicit, expected in ((None, "github-token"), ("explicit-token", "explicit-token"), ("", None)):
                with self.subTest(explicit=explicit):
                    client = GitHubClient(token=explicit)
                    transport = ScriptedTransport([pr_data(), [file_data()], pr_data()])
                    client._transport = transport
                    client.fetch_pr("octocat/Hello-World", 12)
                    self.assertEqual(transport.requests[0].get_header("Authorization"),
                                     "Bearer " + expected if expected else None)
        with patch.dict("os.environ", {"GH_TOKEN": "gh-only"}, clear=True):
            client = GitHubClient()
            transport = ScriptedTransport([pr_data(), [file_data()], pr_data()])
            client._transport = transport
            client.fetch_pr("octocat/Hello-World", 12)
            self.assertEqual(transport.requests[0].get_header("Authorization"), "Bearer gh-only")
        for token in ("secret\ninjected", "secret\rinjected", "not a token", "é", 12, "x" * 4097):
            with self.subTest(token_type=type(token).__name__), self.assertRaises(GitHubError):
                GitHubClient(token=token)

    def test_reports_http_errors_without_exposing_server_body_or_token(self):
        for status, headers, message in (
            (301, {"Location": "https://evil.example/"}, "redirect"),
            (401, {}, "authentication"), (403, {}, "denied"),
            (403, {"X-RateLimit-Remaining": "0"}, "rate limit"),
            (404, {}, "not found"), (429, {}, "rate limit"), (503, {}, "503"),
        ):
            with self.subTest(status=status, headers=headers):
                transport = ScriptedTransport([(status, headers, b'{"message":"server-sensitive test-secret"}')])
                client = GitHubClient(token="test-secret")
                client._transport = transport
                with self.assertRaises(GitHubError) as error:
                    client.fetch_pr("octocat/Hello-World", 12)
                self.assertIn(message, str(error.exception).lower())
                self.assertNotIn("server-sensitive", str(error.exception))
                self.assertNotIn("test-secret", str(error.exception))
                self.assertEqual(len(transport.requests), 1)

    def test_real_transport_sets_timeout_and_disables_redirects(self):
        with patch("reviewbudget.github.build_opener") as build:
            build.return_value.open.side_effect = HTTPError(
                "https://api.github.com/", 302, "server-sensitive", {"Location": "https://evil.example/"}, None,
            )
            client = GitHubClient(token="test-secret")
            with self.assertRaises(GitHubError):
                client.fetch_pr("octocat/Hello-World", 12)
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 30)
            handler = build.call_args.args[0]
            self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://evil.example/"))
            build.return_value.open.side_effect = URLError("server-sensitive test-secret")
            with self.assertRaises(GitHubError) as error:
                client.fetch_pr("octocat/Hello-World", 12)
            self.assertNotIn("server-sensitive", str(error.exception))
            self.assertNotIn("test-secret", str(error.exception))
            build.return_value.open.side_effect = IncompleteRead(b"server-sensitive test-secret")
            with self.assertRaises(GitHubError) as error:
                client.fetch_pr("octocat/Hello-World", 12)
            self.assertNotIn("server-sensitive", str(error.exception))


if __name__ == "__main__":
    unittest.main()
