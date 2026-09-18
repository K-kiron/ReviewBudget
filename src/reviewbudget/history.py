"""Resumable, cohort-stable collection with a durable checkpoint per PR."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .github import parse_pr_url
from .planner import validate_snapshot


def _dump(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


def _save(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path):
    def reject_constant(value):
        raise ValueError("Nonfinite value in collection checkpoint.")
    with path.open("rb") as handle:
        data = handle.read(20_000_001)
    if len(data) > 20_000_000:
        raise ValueError("Collection checkpoint exceeds the size limit.")
    try:
        return json.loads(data, parse_constant=reject_constant)
    except (RecursionError, UnicodeError):
        raise ValueError("Invalid collection checkpoint.") from None


def _record(record, repository, number):
    if not isinstance(record, dict):
        raise ValueError("Invalid history record.")
    snapshot = record.get("snapshot")
    validate_snapshot(snapshot)
    if snapshot["repository"].casefold() != repository.casefold() or snapshot["number"] != number:
        raise ValueError("Checkpoint record does not match its requested pull request.")
    outcome = record.get("outcome")
    if not isinstance(outcome, dict) or any(
        type(outcome.get(key)) is not int or outcome[key] < 0
        for key in ("review_count", "changes_requested", "review_comments")
    ):
        raise ValueError("Invalid history outcome counts.")
    if outcome["changes_requested"] > outcome["review_count"]:
        raise ValueError("Changes-requested count exceeds submitted reviews.")
    return record


def collect_to_file(client, repository, limit, output, *, resume=False, progress=None):
    """Persist complete PR records; resume never changes the original cohort.

    On request failure, the JSONL output contains completed records only. Its
    adjacent .checkpoint/status.json explicitly records whether the cohort is
    complete. No partial API response is promoted to a completed PR record.
    """
    repository, _ = parse_pr_url(f"{repository}#1")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("History limit must be between 1 and 1000.")
    output = Path(output)
    checkpoint = Path(str(output) + ".checkpoint")
    manifest_path = checkpoint / "manifest.json"
    if output.exists() and not manifest_path.exists():
        raise ValueError("Output exists without a matching checkpoint; choose a new output path.")
    if resume:
        if not manifest_path.exists():
            raise ValueError("No collection checkpoint exists for --resume.")
        manifest = _read(manifest_path)
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("repository") != repository or manifest.get("limit") != limit):
            raise ValueError("Resume requires the original repository and limit.")
    else:
        if checkpoint.exists():
            raise ValueError("Collection checkpoint exists; use --resume or a new output path.")
        numbers = client.closed_numbers(repository, limit)
        manifest = {"schema_version": 1, "repository": repository, "limit": limit,
                    "numbers": numbers, "selection": "closed, updated descending at collection start",
                    "created_at": datetime.now(timezone.utc).isoformat()}
    numbers = manifest.get("numbers")
    if (not isinstance(numbers, list) or len(numbers) > limit
            or any(type(number) is not int or number < 1 for number in numbers)
            or len(set(numbers)) != len(numbers)):
        raise ValueError("Invalid cohort in collection checkpoint.")
    if not resume:
        checkpoint.mkdir()
        _save(manifest_path, _dump(manifest) + "\n")
    lock = checkpoint / ".lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("Collection is locked; verify the previous process ended before removing its .lock file.") from None
    os.close(descriptor)
    records = {}
    try:
        # Validate every cached record before replacing a previous materialized
        # dataset. Corrupt checkpoints must never truncate a valid output.
        for number in numbers:
            cached = checkpoint / f"{number}.json"
            if cached.exists():
                records[number] = _record(_read(cached), repository, number)
        try:
            for index, number in enumerate(numbers, 1):
                if number not in records:
                    record = _record(client.fetch_history_record(repository, number), repository, number)
                    _save(checkpoint / f"{number}.json", _dump(record) + "\n")
                    records[number] = record
                if progress:
                    progress(index, len(numbers))
        finally:
            contents = "".join(_dump(records[number]) + "\n" for number in numbers if number in records)
            _save(output, contents)
            status = {"repository": repository, "planned": len(numbers), "completed": len(records),
                      "complete": len(records) == len(numbers),
                      "dataset_sha256": hashlib.sha256(contents.encode("utf-8")).hexdigest(),
                      "checkpoint": str(checkpoint)}
            _save(checkpoint / "status.json", _dump(status) + "\n")
    finally:
        lock.unlink()
    return status
