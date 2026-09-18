# Security boundaries

ReviewBudget reads metadata and diff text. It never checks out, imports, builds, or executes code from an analyzed contribution. PR descriptions and patches are untrusted data, not commands. The Action imports its own trusted package under Python isolated mode and writes escaped Markdown and fixed scalar outputs.

The network adapter makes GET requests only to `https://api.github.com`, refuses redirects, validates pagination links, pins the REST API version, and bounds request time and response size. Authentication comes from the supplied Action token, `GITHUB_TOKEN`, or `GH_TOKEN`. Use a token restricted to the required repository with pull-request read access. Error messages do not include credentials or raw server response bodies.

Run the Action from a reviewed immutable commit. In privileged workflows, do not execute contribution code or load policy from a contribution checkout. Explicit TOML policy can add sensitive paths and change heuristic thresholds but cannot remove built-in safety floors.

Planning failures produce conservative Action outputs and a failing exit status. Recommendations cannot approve a PR or override required checks. Missing patches and file truncation increase the review tier; they never become proof of low risk.

Treat collected snapshots as repository data. They can contain private descriptions, paths, and patches. Store them locally and review them before sharing. Reports do not include PR bodies or patches, but may include changed paths.

This tool does not detect vulnerabilities or certify code safety. Path heuristics, self-reported evidence, and historical activity proxies each have documented limits.

Local capture uses installed Git without a shell, scrubs inherited Git configuration variables, disables hooks, filesystem monitors, external diff/textconv helpers, and clean/smudge/process filters, and resolves the executable outside the analyzed repository. `--auth gh` is opt-in and uses an installed executable outside the working checkout. Policy contains data only; configured check names never become commands.

Standalone HTML embeds its assets and data under a restrictive content-security policy that blocks network requests. Local imports render as text, not HTML. Reports and fingerprints are not signatures: treat imported identities, capability mappings, and costs as self-declared. Sharing a report may disclose private repository names, titles, paths, or commit identities.

Please report suspected vulnerabilities through the repository's private [security reporting page](https://github.com/K-kiron/ReviewBudget/security/advisories/new) when available. If private reporting is unavailable, open a minimal issue requesting a private contact route without including exploit details or sensitive data.
