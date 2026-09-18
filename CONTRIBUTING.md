# Contributing

Useful contributions include reproducible capture failures, missing path categories, clearer explanations, better policy examples, and prospective evaluations with honest provenance. Open an issue for a functional change before implementation. Keep setup-only changes small.

Develop from `dev` on a focused branch. Submit pull requests to `dev`; maintainers handle release integration separately. Use an isolated worktree when other work is in progress. Do not commit local histories, private snapshots, credentials, temporary plans, or generated build output.

## Development

Python 3.11+ and Git are required. Runtime code uses the standard library.

```bash
python -m venv .venv
# Activate the environment for your shell.
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pip install build twine
python -m build
python -m twine check dist/*
python scripts/build_preview.py preview
```

Tests use temporary repositories and fake network transports; they do not need credentials or make live API requests. The Git helper safety test deliberately executes a harmless marker helper as a positive control, then proves capture does not execute it. A sandbox that blocks Git's shell helper may prevent that positive control; run the test in an environment that permits its temporary local process.

CI builds and installs the wheel before testing on Linux, macOS, and Windows with Python 3.11 and 3.13. Packaged demos, policies, CSS, and JavaScript must work outside a checkout. For renderer changes, verify scenario switching, local import errors, narrow layouts, keyboard access, and the restrictive content-security policy in a browser.

## Review criteria

- Preserve mandatory work, conservative incomplete-input handling, and existing required checks.
- Unknown costs stay unknown. Do not turn declared estimates into measured-benefit claims.
- Inputs remain data. Never execute commands, hooks, filters, or code from a contribution.
- Keep report output deterministic and update its fingerprint after meaningful mutation.
- Cover meaningful behavior and failure modes; avoid tests that merely repeat implementation.
- Describe the user-visible behavior and verification in the PR. Reference the issue. GitHub closing keywords do not close issues on merges to a non-default branch, so use an explicit issue follow-up for `dev` delivery.

For bug reports, follow the diagnostic information in [usage](docs/usage.md). Report security issues using [SECURITY.md](SECURITY.md), without publishing secrets or exploitable private data.
