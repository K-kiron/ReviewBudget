"""Read-only GitHub REST input with explicit final-state provenance."""

import json
import os
import re
from datetime import datetime, timezone
from http.client import HTTPException
from itertools import islice
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GitHubError(ValueError):
    """A safe, user-facing GitHub input or API error."""


_REPOSITORY = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+"
_PR_URL = re.compile(rf"(?:https://)?github\.com/({_REPOSITORY})/pull/([1-9][0-9]*)")
_PR_SHORT = re.compile(rf"({_REPOSITORY})#([1-9][0-9]*)")
_FILE_STATUSES = {"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"}


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise GitHubError(f"GitHub API returned an invalid {name}.")
    return value


def _timestamp(value: object, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError
    except (ValueError, OverflowError):
        raise GitHubError(f"GitHub API returned an invalid {name} timestamp.") from None
    return value


def _validate_pr(data: object, number: int) -> dict:
    if not isinstance(data, dict) or _integer(data.get("number"), "PR number", 1) != number:
        raise GitHubError("GitHub API returned an unexpected pull request.")
    if not isinstance(data.get("title"), str) or (data.get("body") is not None and not isinstance(data["body"], str)):
        raise GitHubError("GitHub API returned invalid pull request text.")
    _integer(data.get("changed_files"), "changed file count")
    _timestamp(data.get("created_at"), "creation")
    _timestamp(data.get("updated_at"), "update")
    for key in ("head", "base"):
        ref = data.get(key)
        if not isinstance(ref, dict) or not isinstance(ref.get("sha"), str) or not re.fullmatch(r"[0-9a-fA-F]{40}", ref["sha"]):
            raise GitHubError("GitHub API returned an invalid commit SHA.")
    return data


def _file(data: object) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("filename"), str) or not data["filename"]:
        raise GitHubError("GitHub API returned an invalid file entry.")
    if not isinstance(data.get("status"), str) or data["status"] not in _FILE_STATUSES:
        raise GitHubError("GitHub API returned an invalid file status.")
    result = {key: data[key] for key in ("filename", "status")}
    _file_path(data["filename"])
    for key in ("additions", "deletions"):
        result[key] = _integer(data.get(key), key)
    for key in ("patch", "previous_filename"):
        if key in data:
            if key == "patch" and data[key] is None:
                result[key] = None
                continue
            if not isinstance(data[key], str) or (key == "previous_filename" and not data[key]):
                raise GitHubError("GitHub API returned invalid file details.")
            result[key] = data[key]
    if "previous_filename" in result:
        _file_path(result["previous_filename"])
    if result["status"] == "renamed" and "previous_filename" not in result:
        raise GitHubError("GitHub API omitted the previous name of a renamed file.")
    return result


def _file_path(value: str) -> None:
    if (len(value) > 4096 or value.startswith("/") or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise GitHubError("GitHub API returned an unsupported file path; expected a relative POSIX path.")


def _version(pr: dict) -> tuple:
    return (pr["head"]["sha"], pr["base"]["sha"], pr["updated_at"],
            pr["changed_files"], pr["title"], pr.get("body"))


def parse_pr_url(value: str) -> tuple[str, int]:
    """Parse a strict github.com PR URL or OWNER/REPO#NUMBER reference."""
    match = (_PR_URL.fullmatch(value) or _PR_SHORT.fullmatch(value)) if isinstance(value, str) else None
    if not match or match[1].split("/")[1] in {".", ".."}:
        raise GitHubError("Expected https://github.com/OWNER/REPO/pull/NUMBER or OWNER/REPO#NUMBER.")
    try:
        return match[1], int(match[2])
    except ValueError:
        raise GitHubError("Pull request number is too large.") from None


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    """Fetch GitHub PR metadata and diffs without writing remote state."""

    def __init__(self, token: str | None = None):
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if self._token is not None and (
            not isinstance(self._token, str) or len(self._token) > 4096
            or not re.fullmatch(r"[\x21-\x7e]*", self._token)
        ):
            raise GitHubError("GitHub token must be at most 4096 non-whitespace ASCII characters.")

    def _transport(self, request: Request) -> tuple[int, dict, bytes]:
        """Small replaceable transport boundary; never follow redirects."""
        try:
            with build_opener(_NoRedirects()).open(request, timeout=30) as response:
                return response.status, dict(response.headers), response.read(16 * 1024 * 1024 + 1)
        except HTTPError as error:
            result = error.code, dict(error.headers or {}), b""
            error.close()
            return result
        except (URLError, OSError, ValueError, HTTPException):
            raise GitHubError("Unable to reach the GitHub API; check connectivity and retry.") from None

    def _get_json(self, path: str) -> tuple[object, dict]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ReviewBudget",
        }
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        status, response_headers, body = self._transport(Request("https://api.github.com" + path, headers=headers, method="GET"))
        response_headers = {key.lower(): value for key, value in response_headers.items()}
        if status != 200:
            if 300 <= status < 400:
                raise GitHubError("GitHub API redirect refused; use the current repository name.")
            if status == 429 or (status == 403 and (response_headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response_headers)):
                raise GitHubError("GitHub API rate limit reached; retry later.")
            if status == 401:
                raise GitHubError("GitHub authentication failed; check the supplied token.")
            if status == 403:
                raise GitHubError("GitHub API access denied; check repository read permission or retry later.")
            if status == 404:
                raise GitHubError("GitHub repository or pull request not found, or not accessible to this token.")
            raise GitHubError(f"GitHub API returned HTTP {status}.")
        if len(body) > 16 * 1024 * 1024:
            raise GitHubError("GitHub API response exceeded the size limit.")
        try:
            return json.loads(body), response_headers
        except (ValueError, UnicodeError, RecursionError):
            raise GitHubError("GitHub API returned invalid JSON.") from None

    def fetch_pr(self, repository: str, number: int) -> dict:
        """Capture current PR files; this is not an opening-time snapshot."""
        return self._capture_pr(repository, number)[0]

    def _capture_pr(self, repository: str, number: int) -> tuple[dict, dict]:
        if type(number) is not int or number < 1:
            raise GitHubError("Pull request number must be a positive integer.")
        repository, number = parse_pr_url(f"{repository}#{number}")
        path = f"/repos/{repository}/pulls/{number}"
        before, _ = self._get_json(path)
        before = _validate_pr(before, number)
        files = [_file(data) for data in self._paginate(path + "/files", max_pages=30, truncate=True)]
        after, _ = self._get_json(path)
        after = _validate_pr(after, number)
        if _version(before) != _version(after):
            raise GitHubError("Pull request changed during capture; retry for a consistent snapshot.")
        if len(files) > before["changed_files"] or len({item["filename"] for item in files}) != len(files):
            raise GitHubError("GitHub API returned inconsistent or duplicate file entries.")
        snapshot = {
            "repository": repository, "number": number, "title": before["title"],
            "body": before.get("body") or "", "created_at": before["created_at"],
            "updated_at": before["updated_at"], "head_sha": before["head"]["sha"],
            "base_sha": before["base"]["sha"], "changed_files": before["changed_files"],
            "files_complete": len(files) == before["changed_files"], "files": files,
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feature_timing": "final_state", "synthetic": False,
        }
        return snapshot, after

    def collect_history(self, repository: str, limit: int = 100) -> list[dict]:
        """Capture recently updated closed PRs, explicitly as final-state evidence.

        Reviews are submitted review events, not unique reviewers or review rounds.
        This retrospective collection cannot establish opening-time feature values.
        """
        repository, _ = parse_pr_url(f"{repository}#1")
        _integer(limit, "history limit", 1)
        if limit > 1000:
            raise GitHubError("History limit must be between 1 and 1000.")
        path = f"/repos/{repository}/pulls"
        candidates = list(islice(self._paginate(path, params={
            "state": "closed", "sort": "updated", "direction": "desc",
        }), limit))
        numbers = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("state") != "closed":
                raise GitHubError("GitHub API returned an invalid closed pull request.")
            number = _integer(candidate.get("number"), "PR number", 1)
            if number in numbers:
                raise GitHubError("Pull request listing changed during pagination; retry collection.")
            numbers.append(number)
        records = []
        for number in numbers:
            snapshot, pr = self._capture_pr(repository, number)
            outcome = self._outcome_metadata(pr)
            reviews = list(self._paginate(f"{path}/{number}/reviews"))
            submitted = []
            ids = set()
            for review in reviews:
                if not isinstance(review, dict):
                    raise GitHubError("GitHub API returned an invalid review.")
                review_id = _integer(review.get("id"), "review identifier", 1)
                state = review.get("state")
                if not isinstance(state, str) or state not in {"PENDING", "APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}:
                    raise GitHubError("GitHub API returned an invalid review state.")
                if review_id in ids:
                    raise GitHubError("Review listing changed during pagination; retry collection.")
                ids.add(review_id)
                if state != "PENDING":
                    _timestamp(review.get("submitted_at"), "review submission", nullable=True)
                    submitted.append(review)
            final, _ = self._get_json(f"{path}/{number}")
            final = _validate_pr(final, number)
            if _version(pr) != _version(final) or outcome != self._outcome_metadata(final):
                raise GitHubError("Pull request changed during capture; retry for a consistent snapshot.")
            dates = [review.get("submitted_at") for review in submitted]
            first_review = min(dates, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))) if dates and all(dates) else None
            outcome.update({
                "review_count": len(submitted),
                "changes_requested": sum(review["state"] == "CHANGES_REQUESTED" for review in submitted),
                "first_review_at": first_review,
            })
            records.append({"snapshot": snapshot, "outcome": outcome,
                            "feature_timing": "final_state", "synthetic": False})
        return records

    @staticmethod
    def _outcome_metadata(pr: dict) -> dict:
        if pr.get("state") != "closed":
            raise GitHubError("Pull request is no longer closed; retry collection.")
        return {
            "review_comments": _integer(pr.get("review_comments"), "review comment count"),
            "created_at": pr["created_at"],
            "closed_at": _timestamp(pr.get("closed_at"), "closure"),
            "merged_at": _timestamp(pr.get("merged_at"), "merge", nullable=True),
        }

    def _paginate(self, path: str, *, params: dict | None = None, max_pages: int = 1000, truncate: bool = False):
        """Follow validated pagination only, reconstructing URLs on the fixed host."""
        params = dict(params or {})
        for page in range(1, max_pages + 1):
            query = {**params, "per_page": 100, "page": page}
            data, headers = self._get_json(path + "?" + urlencode(query))
            if not isinstance(data, list) or len(data) > 100:
                raise GitHubError("GitHub API returned an invalid page of results.")
            yield from data
            next_links = re.findall(r'<([^>]+)>\s*;\s*rel="next"', headers.get("link", ""))
            if not next_links:
                return
            if len(next_links) != 1:
                raise GitHubError("GitHub API returned invalid pagination metadata.")
            try:
                next_url = urlsplit(next_links[0])
            except ValueError:
                raise GitHubError("GitHub API returned an invalid pagination link.") from None
            expected_query = {key: [str(value)] for key, value in {**query, "page": page + 1}.items()}
            if (next_url.scheme != "https" or next_url.netloc != "api.github.com"
                    or next_url.path != path or next_url.fragment
                    or parse_qs(next_url.query) != expected_query):
                raise GitHubError("GitHub API returned an unsafe or inconsistent pagination link.")
        if not truncate:
            raise GitHubError("GitHub API pagination exceeded the request limit.")
