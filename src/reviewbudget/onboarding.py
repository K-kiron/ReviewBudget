"""Create an explicit, editable verification policy from a packaged preset."""

from importlib.resources import files
from pathlib import Path

PRESETS = ("python", "node")


def policy_text(preset: str = "python") -> str:
    if preset not in PRESETS:
        raise ValueError("Choose a supported policy preset: python or node.")
    return files("reviewbudget").joinpath("data", f"{preset}.toml").read_text(encoding="utf-8")


def initialize(directory: str | Path, preset: str = "python") -> Path:
    """Write .reviewbudget.toml once; existing files and symlinks are preserved."""
    text = policy_text(preset)
    destination = Path(directory) / ".reviewbudget.toml"
    if not destination.parent.is_dir():
        raise ValueError("The policy destination must be an existing directory.")
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except FileExistsError:
        raise ValueError("A .reviewbudget.toml already exists; edit it explicitly to preserve project settings.") from None
    return destination
