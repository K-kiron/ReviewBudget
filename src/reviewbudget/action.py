"""Run from the trusted Action directory with Python isolated mode."""

from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from reviewbudget.cli import run_action

    raise SystemExit(run_action())
