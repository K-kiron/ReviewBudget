"""Read committed Git changes without checking out or executing repository code."""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from .planner import validate_snapshot

MAX_FILES = 3000
MAX_PATCH_BYTES = 100_000
MAX_TOTAL_PATCH_BYTES = 2_000_000
_MAX_METADATA_BYTES = 32_000_000
_STATUSES = {"A": "added", "D": "removed", "M": "modified", "R": "renamed",
             "C": "copied", "T": "changed"}


def _git_executable(repo: Path) -> str:
    # Windows executable lookup normally prefers the current directory. Search
    # only absolute PATH entries, excluding the repository and its ancestors.
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        directory = Path(entry.strip('"'))
        if not entry or not directory.is_absolute():
            continue
        directory = directory.resolve()
        if directory == repo or repo in directory.parents or directory in repo.parents:
            continue
        candidate = directory / ("git.exe" if os.name == "nt" else "git")
        if candidate.is_file() and os.access(candidate, os.X_OK):
            candidate = candidate.resolve()
            if repo not in candidate.parents:
                return str(candidate)
    raise ValueError("Git must be installed on an absolute PATH outside the repository.")


def _git(repo: Path, *args: str, limit: int = _MAX_METADATA_BYTES,
         optional: bool = False, configuration: tuple[str, ...] = ()) -> tuple[bytes, bool]:
    # Inherited Git variables can redirect the repository or inject configuration.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_ATTR_NOSYSTEM="1", GIT_NO_REPLACE_OBJECTS="1")
    command = [_git_executable(repo), "--no-pager", "--literal-pathspecs", "-C", str(repo),
               "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
               "-c", f"core.attributesFile={os.devnull}",
               "-c", "diff.submodule=short", *configuration, *args]
    try:
        # A temporary output file keeps a large patch out of process memory.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL,
                                    env=env, timeout=30, check=False)
            if result.returncode:
                if optional and result.returncode == 1:
                    return b"", False
                raise ValueError("Git could not read the requested repository or committed comparison.")
            output.seek(0)
            data = output.read(limit + 1)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Git is unavailable, or the local comparison could not finish within 30 seconds.") from None
    return data[:limit], len(data) > limit


def _disable_filters(repo: Path) -> tuple[str, ...]:
    data, truncated = _git(repo, "config", "--null", "--name-only", "--get-regexp",
                           r"^filter\..*\.(clean|smudge|process|required)$", optional=True)
    if truncated:
        raise ValueError("Git filter configuration exceeds the supported local input limit.")
    settings = []
    for key in data.decode("utf-8").split("\0"):
        if key:
            settings.extend(("-c", key + ("=false" if key.endswith(".required") else "=")))
    return tuple(settings)


def _commit(git, ref: str) -> str:
    if (not isinstance(ref, str) or not ref or len(ref) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in ref)):
        raise ValueError("A nonempty, valid Git commit reference is required.")
    data, truncated = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    sha = data.decode("ascii", errors="replace").strip()
    if truncated or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise ValueError("Git did not resolve the reference to a commit SHA.")
    return sha


def _repository(git) -> str:
    data, truncated = git("config", "--get", "remote.origin.url", limit=8192, optional=True)
    if truncated:
        return "local/project"
    remote = data.decode("utf-8", errors="replace").strip()
    match = re.fullmatch(r"git@github[.]com:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", remote)
    path = match[1] if match else ""
    if not path:
        try:
            parsed = urlsplit(remote)
            if (parsed.scheme in {"https", "ssh"} and parsed.hostname == "github.com"
                    and not parsed.query and not parsed.fragment):
                path = parsed.path.lstrip("/")
        except ValueError:
            pass
    path = path.removesuffix(".git")
    return path if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path) else "local/project"


def _files(status: bytes, numstat: bytes) -> list[dict]:
    try:
        tokens = status.decode("utf-8").split("\0")
        stats = numstat.decode("utf-8").split("\0")
        if tokens[-1] or stats[-1]:
            raise ValueError
        files = []
        index = 0
        while index < len(tokens) - 1:
            marker, filename = tokens[index:index + 2]
            index += 2
            item = {"filename": filename, "status": _STATUSES[marker[0]]}
            if marker[0] in {"R", "C"}:
                item["previous_filename"] = filename
                item["filename"] = tokens[index]
                index += 1
            files.append(item)
        index = 0
        for item in files:
            additions, deletions, filename = stats[index].split("\t", 2)
            index += 1
            if filename == "":
                previous, filename = stats[index:index + 2]
                index += 2
                if previous != item.get("previous_filename"):
                    raise ValueError
            if filename != item["filename"]:
                raise ValueError
            binary = additions == deletions == "-"
            item.update(additions=0 if binary else int(additions),
                        deletions=0 if binary else int(deletions), patch=None)
            if binary:
                item["binary"] = True
        if index != len(stats) - 1:
            raise ValueError
        return files
    except (UnicodeDecodeError, ValueError, KeyError, IndexError):
        raise ValueError("Git returned unsupported paths or inconsistent changed-file metadata.") from None


def capture_local(repo: str | Path, base: str, head: str = "HEAD", title: str = "",
                  body: str = "") -> dict:
    """Capture merge-base..head; unstaged, staged, and untracked work is excluded.

    ``base_sha`` identifies the requested base; ``merge_base_sha`` identifies the
    actual diff baseline. ``number=1`` is a schema compatibility placeholder,
    never a claim that a pull request exists. Binary line counts are unknown and
    represented as zero with ``binary=True`` and a missing patch.
    """
    directory = Path(repo).resolve()
    git = partial(_git, directory, configuration=_disable_filters(directory))
    base_sha, head_sha = _commit(git, base), _commit(git, head)
    merge_output, truncated = git("merge-base", "--all", base_sha, head_sha)
    merge_base = merge_output.decode("ascii", errors="replace").strip()
    if truncated or not re.fullmatch(r"[0-9a-f]{40,64}", merge_base):
        raise ValueError("The committed comparison requires one unambiguous merge base.")
    # Both revisions are verified hashes. These options also defeat repository
    # diff drivers, textconv helpers, pager settings, and submodule diff commands.
    diff = ("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-relative",
            "--ignore-submodules=none", "--src-prefix=a/", "--dst-prefix=b/", "-M")
    status, status_limit = git(*diff, "--name-status", "-z", merge_base, head_sha, "--")
    numstat, stats_limit = git(*diff, "--numstat", "-z", merge_base, head_sha, "--")
    if status_limit or stats_limit:
        raise ValueError("Changed-file metadata exceeds the supported local input limit.")
    files = _files(status, numstat)
    changed_files = len(files)
    files = files[:MAX_FILES]
    uncertainties = []
    if changed_files > MAX_FILES:
        uncertainties.append("file_limit")
    snapshot = {
        "repository": _repository(git), "number": 1,
        "source": "local_git", "synthetic": False, "feature_timing": "local",
        "title": title or "Local committed comparison", "body": body,
        "base_sha": base_sha, "head_sha": head_sha, "merge_base_sha": merge_base,
        "files": files, "changed_files": changed_files,
        "files_complete": changed_files <= MAX_FILES,
        "capture_uncertainties": uncertainties,
        "provenance": {"diff_basis": "merge_base", "working_tree_included": False,
                       "number_is_placeholder": True},
    }
    # Reject unsupported filenames before using textual patch boundaries.
    validate_snapshot(snapshot)
    output, output_limit = git(*diff, "--patch", merge_base, head_sha, "--",
                               limit=MAX_TOTAL_PATCH_BYTES)
    chunks = re.split(rb"(?m)^diff --git ", output)[1:]
    if output_limit:
        uncertainties.append("patch_output_limit")
        # The final chunk may have been cut in the middle of a hunk.
        chunks = chunks[:-1]
    if not output_limit and len(chunks) != changed_files:
        # Unexpected output must never be attached to a different filename.
        uncertainties.append("patch_metadata_mismatch")
        chunks = []
    for index, item in enumerate(files):
        if item.get("binary"):
            uncertainties.append("binary_patch")
            continue
        if index >= len(chunks):
            continue
        chunk = b"diff --git " + chunks[index]
        if len(chunk) > MAX_PATCH_BYTES:
            uncertainties.append("patch_size_limit")
            continue
        try:
            item["patch"] = chunk.decode("utf-8")
        except UnicodeDecodeError:
            uncertainties.append("patch_encoding")
    snapshot["capture_uncertainties"] = sorted(set(uncertainties))
    validate_snapshot(snapshot)
    return snapshot
