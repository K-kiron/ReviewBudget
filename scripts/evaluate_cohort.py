"""Recollect the published public PR identities and evaluate them locally.

GitHub current state changes; the cohort is fixed, not the API responses.
Raw snapshots stay in the caller-selected output directory.
"""

import argparse
import json
from pathlib import Path

from reviewbudget.cli import _client
from reviewbudget.evaluation import evaluate
from reviewbudget.history import collect_to_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="docs/evaluation/cohort.json")
    parser.add_argument("--output", required=True, help="Local directory outside the tracked repository.")
    parser.add_argument("--auth", choices=("env", "gh"), default="env")
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    destination = Path(args.output)
    destination.mkdir(parents=True, exist_ok=True)
    client = _client(args.auth)
    records = []
    for cohort in manifest["repositories"]:
        path = destination / (cohort["repository"].replace("/", "--") + ".jsonl")
        checkpoint = Path(str(path) + ".checkpoint")
        if not checkpoint.exists():
            if path.exists():
                raise ValueError("Refusing to overwrite an output without its checkpoint.")
            checkpoint.mkdir()
            value = {"schema_version": 1, "repository": cohort["repository"],
                     "limit": len(cohort["numbers"]), "numbers": cohort["numbers"],
                     "selection": "fixed published cohort identities", "created_at": manifest["collected_on"]}
            (checkpoint / "manifest.json").write_text(json.dumps(value) + "\n", encoding="utf-8")
        else:
            existing = json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("numbers") != cohort["numbers"]:
                raise ValueError("Existing checkpoint uses different PR identities.")
        status = collect_to_file(client, cohort["repository"], len(cohort["numbers"]), path, resume=True)
        print(f"{cohort['repository']}: {status['completed']} records; {status['dataset_sha256']}")
        records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    report = evaluate(records)
    (destination / "evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Evaluation: {report['evidence_status']}; {report['verdict']}")


if __name__ == "__main__":
    main()
