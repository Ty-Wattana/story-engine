"""Shared helpers — do not add business logic here."""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt file from the repo's prompts/ directory. Returns empty string if missing."""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""
