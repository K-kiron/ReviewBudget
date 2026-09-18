# Snapshot and history formats

`reviewbudget analyze --input` accepts one JSON object. Required fields:

| Field | Meaning |
| --- | --- |
| `repository` | `OWNER/REPO` |
| `number` | Positive integer pull request number |
| `changed_files` | Total changed files declared by the source |
| `files_complete` | Boolean; false when retrieval is incomplete |
| `files` | Observed file entries, at most 3,000 |

Each file contains a relative POSIX `filename`, a GitHub file `status`, and nonnegative integer `additions` and `deletions`. A `renamed` file also requires `previous_filename`. `patch` is optional text; missing or empty patches are unknown and raise the minimum verification tier. Duplicate paths, traversal, absolute paths, and control characters are rejected.

`title` and `body` are optional text. `head_sha` and `base_sha` are hexadecimal commit identifiers; omitting them produces a conservative incomplete-input report. They bind recommendations to a revision, not the latest state of a moving branch. The report's `schema_version` is currently 1. See the JSON files in `examples/` for complete offline inputs.

`reviewbudget evaluate --input` accepts one history record per JSONL line:

```json
{
  "snapshot": {
    "repository": "example/service",
    "number": 42,
    "title": "Clarify usage",
    "body": "## Summary\nClarify the installation steps.",
    "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-01-01T10:00:00Z",
    "captured_at": "2026-01-01T10:01:00Z",
    "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "base_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "feature_timing": "pre_review",
    "synthetic": true,
    "changed_files": 1,
    "files_complete": true,
    "files": [{"filename": "README.md", "status": "modified", "additions": 1, "deletions": 1, "patch": "@@ -1 +1 @@\n-old\n+new"}]
  },
  "outcome": {
    "review_count": 2,
    "changes_requested": 1,
    "review_comments": 3,
    "created_at": "2026-01-01T10:00:00Z",
    "first_review_at": "2026-01-01T11:00:00Z",
    "closed_at": "2026-01-02T10:00:00Z",
    "merged_at": "2026-01-02T10:00:00Z"
  },
  "feature_timing": "pre_review",
  "synthetic": true
}
```

This example is synthetic even though its timing is consistent. All timestamps must include a timezone. `merged_at` may be null for closed, unmerged PRs. `review_count` counts submitted review events, `changes_requested` counts events currently in that state, and `review_comments` counts inline review comments. Dismissed review states cannot recover all original review decisions.

The collector always sets `feature_timing` to `final_state`. Renaming that label does not reconstruct an earlier diff or description. For eligible pre-review data, independently preserve the original snapshot and commit identities when captured, record capture/update/first-review timestamps, and audit the source. The evaluator can check supplied provenance consistency; it cannot authenticate it.

Malformed records and all copies of duplicate PR identities are excluded and reported. Their exclusion makes the evaluation exploratory. Training PR outcomes must finish before the chronological holdout boundary. Zero-positive partitions and zero baseline precision produce undefined metrics where appropriate; they cannot pass the associated research gates.
