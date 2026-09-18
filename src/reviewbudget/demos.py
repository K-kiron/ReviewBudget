"""Portable synthetic examples and illustrative policy estimates."""

from importlib.resources import files
import json
import tomllib

from .onboarding import policy_text
from .planner import validate_snapshot

_SCENARIOS = ("docs", "tested-code", "auth", "migration-dependency", "incomplete")


def names() -> list[str]:
    return list(_SCENARIOS)


def load_scenario(name: str) -> dict:
    if name not in _SCENARIOS:
        raise ValueError("Choose a demo scenario: " + ", ".join(_SCENARIOS) + ".")
    snapshot = json.loads(files("reviewbudget").joinpath("data", f"{name}.json").read_text(encoding="utf-8"))
    validate_snapshot(snapshot)
    return snapshot


def load_policy(name: str = "python") -> dict:
    """Load the same illustrative policy used by init, without writing a file."""
    return tomllib.loads(policy_text(name))
