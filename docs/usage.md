# CLI and troubleshooting

Install Python 3.11+ and the package, then run `reviewbudget --help`. Every subcommand has `--help`. Python modules work too: `python -m reviewbudget demo`.

| Command | Input | Output |
| --- | --- | --- |
| `init [directory] --preset python` | Existing directory | New `.reviewbudget.toml`; never overwrites |
| `demo --format html --output demo.html` | Bundled synthetic examples | Standalone interactive walkthrough |
| `analyze --base main --policy .reviewbudget.toml` | Local committed Git diff | Text plan; also JSON, Markdown, HTML |
| `analyze OWNER/REPO#123 --auth gh` | GitHub PR metadata and files | Current-state plan |
| `analyze --input snapshot.json` | Saved snapshot | Offline plan |
| `report report.json --output report.html` | Saved analysis or queue report | Offline HTML viewer |
| `allocate --input a.json --input b.json --budget-minutes 60` | Saved reports | Shared budget JSON; `--format html` for interactive output |
| `capture OWNER/REPO#123 --output original.json` | Current PR plus submitted-review check | Original snapshot; never overwrites |
| `collect OWNER/REPO --output history.local.jsonl` | Recently updated closed PRs | Final-state JSONL plus resumable checkpoint |
| `evaluate --input history.local.jsonl` | One or more histories | Chronological evaluation JSON |

Use `--output` to avoid redirect/encoding differences between shells. Exit code 0 means the requested operation succeeded, not that the change is safe. Exit code 2 means invalid input, retrieval, or output failure. Budget overruns remain successful reports with explicit shortfalls; the allocator cannot make required work disappear.

`allocate` and `report` require complete current-version reports with matching fingerprints. Regenerate older or manually edited reports with `analyze`. Fingerprints verify internal consistency, not origin or authorship.

## Local changes

`--base` is required. `--repo /path/to/repository` selects another repository; otherwise the current directory is used. `--head` defaults to `HEAD`. Both refs resolve to commits. The actual comparison is `merge-base(base, head)..head`, matching a branch's contribution. Requested base, head, and merge-base identities are recorded.

Uncommitted files are excluded. Commit intended changes on your feature branch before analysis. Add `--title "Fix empty-input handling"` and `--body-file description.md` to provide contribution evidence. No description means evidence may be missing; the planner will report that instead of inventing it.

Git must be available on an absolute PATH outside the analyzed repository. Custom Git hooks, textconv, external diff helpers, filters, and filesystem monitors are disabled for capture. Unsupported filenames, unavailable refs, or unrelated histories fail clearly. Binary or oversized patches and truncated input retain conservative verification floors. Local captures currently support up to 3,000 files, 100 KB per patch, and 2 MB of patch data.

## Authentication and privacy

Network commands default to `GITHUB_TOKEN` or `GH_TOKEN`, then anonymous GitHub access. `--auth gh` explicitly uses an existing `gh auth login` session. Install GitHub CLI on an absolute PATH outside the working checkout. Tokens never appear in reports or error output.

Public GitHub.com is the supported API host. Enterprise hosts and arbitrary API URLs are not supported. Redirects are refused: supply the repository's current name. A private repository returning 404 may mean the token lacks access. API errors stop collection; wait for rate limits or restore connectivity, then use `--resume` with the **same repository, limit, and output**.

History has an adjacent `.checkpoint` directory containing the original PR-number cohort and one file per completed PR. `status.json` says whether collection finished and provides a dataset hash. Interrupted JSONL contains completed records only. Do not treat it as the entire planned cohort. A `.lock` file prevents overlapping collectors; remove it only after verifying the original collection process has ended. A malformed checkpoint fails without replacing a prior dataset.

Raw snapshots can contain private descriptions, paths, and patches. Store them outside your tracked repository. HTML/JSON reports omit descriptions and patches but retain repository names, titles, file paths, and commits. Review reports before sharing. Browser import is local-only and accepts analysis reports up to 2 MiB; larger reports can be rendered by the CLI within its documented limits.

## Unexpected plans

- **No named checks:** pass an explicit `--policy`; policies are never silently loaded from a contribution.
- **Unmapped capabilities:** add checks whose `covers` values represent the missing activities. A path mismatch can make an otherwise mapped check inapplicable.
- **Unpriced budget:** supply estimates if known. Missing prices never count as zero and block discretionary spending when mandatory costs are unknown.
- **Tier stays high after adding evidence:** security, workflow, test-reduction, and incomplete-input floors cannot be removed by a description.
- **Unusual evidence headings:** recognition currently uses English Markdown headings such as Summary, Testing, Reproduction, Migration, and Rollout. Placeholders and unchecked template boxes do not count.
- **Import rejected:** use the JSON output of `analyze` or `demo`, not a raw snapshot. Imported reports are untrusted display data; fingerprints detect changes, not authorship.
- **Stale Action event:** rerun against the current PR commits. A failure emits conservative outputs; required jobs must remain enabled.

For a bug, include the package version, command, operating system, sanitized policy, and a minimal synthetic input. Never attach tokens or private repository data to a public issue.
