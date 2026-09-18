"""Build portable demo assets from the installed planner, without network access."""

import argparse
import hashlib
import json
from pathlib import Path

from reviewbudget import __version__
from reviewbudget.demos import load_policy, load_scenario, names
from reviewbudget.planner import analyze
from reviewbudget.reports import render_demo


def build(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    reports = [analyze(load_scenario(name), load_policy()) for name in names()]
    (destination / "index.html").write_text(render_demo(reports), encoding="utf-8", newline="\n")
    artifacts = [destination / "index.html"]
    for name, report in zip(names(), reports):
        path = destination / f"{name}.json"
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        artifacts.append(path)
    manifest = {"version": __version__, "synthetic": True,
                "description": "Portable worked examples; costs are illustrative declarations, not measured savings.",
                "sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Built {len(reports)} offline scenarios in {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", default="preview")
    build(parser.parse_args().output)
