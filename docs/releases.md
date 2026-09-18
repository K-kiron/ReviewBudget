# Releases and portable demos

Version 0.2.0 is a public-preview candidate. The repository does not currently claim a package-registry release or a hosted demo. CI produces a wheel, source distribution, and standalone demo artifacts. A maintainer decides when to publish them.

## Build the exact candidate

On the intended release commit, with Python 3.11+:

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
python -m venv release-env
# Use release-env/bin/python on Linux/macOS or release-env\Scripts\python.exe on Windows.
release-env/bin/python -m pip install --no-deps dist/reviewbudget-0.2.0-py3-none-any.whl
release-env/bin/python -m unittest discover -s tests -v
release-env/bin/python scripts/build_preview.py preview
```

Run the demo from a directory outside the checkout to verify bundled resources:

```bash
reviewbudget --version
reviewbudget demo --format html --output demo.html
```

The manual **Release candidate** workflow performs installed-wheel verification and uploads `dist/` plus `preview/`. It records the source commit and SHA-256 checksums. It has read-only repository permissions and does not publish a release, tag, package, or site.

## Publish after review

Confirm the exact commit passed CI and the Actions integration test. Review the public evaluation limits and announcement claims. Use the candidate wheel/source distribution from that commit; retain checksums and source identity with the release. Pin downstream Action usage to that reviewed commit.

The generated `preview/index.html` is the complete no-account demo. It has no external assets, telemetry, backend, or network requests. It can be served by any static host or opened as a local file. Deploy that directory only when publication is approved, then verify the final URL and mobile layout. The JSON examples in the same directory are synthetic sample imports.

Update the README installation ref to the immutable published tag or commit, and add the verified demo URL after deployment. Registry installation instructions should be added only after an actual package is available. Do not announce nonexistent download or demo links.

## Versioning

Keep `pyproject.toml` and `reviewbudget.__version__` synchronized. Report schema version 1 remains additive in this preview; allocation accepts internally consistent reports from the current package. Fingerprints support reproducibility and change detection, not authentication. New semantics that invalidate stored data require explicit migration guidance.
