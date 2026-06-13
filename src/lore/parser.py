"""Lore parser — loads markdown lore into structured knowledge."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from rich.console import Console


@dataclass
class LoreFact:
    """Represents a single fact about the world."""
    category: str
    fact: str
    raw_content: str = ""
    severity: str = "warning"

    @classmethod
    def from_dict(cls, data: dict) -> "LoreFact":
        return cls(
            category=data["category"],
            fact=data["fact"],
            raw_content=data.get("raw_content", ""),
            severity=data.get("severity", "warning"),
        )


@dataclass
class LoreConstraint:
    """A constraint that user input must satisfy."""
    name: str
    description: str
    forbidden_values: List[str] = field(default_factory=list)
    required_elements: List[str] = field(default_factory=list)
    severity: str = "error"


@dataclass
class LoreDatabase:
    """Manages lore facts and constraints extracted from lore files."""
    lore_summary: str = ""
    facts: List[LoreFact] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)

    def add_fact(self, fact: LoreFact) -> None:
        self.facts.append(fact)
        self.categories.append(fact.category)


@dataclass
class LoreConflict:
    """A conflict between user input and established lore."""
    fact: LoreFact
    conflict_type: str
    conflict_message: str
    severity: str = "error"
    suggestion: Optional[str] = None


class LoreParser:
    """Parses markdown lore files into a structured database for LLM context."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.db = LoreDatabase()

    def parse_markdown(self, file_path: str | Path) -> "LoreParser":
        """Parse a markdown lore file and build context for LLM."""
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            self._parse_content(content)
        except Exception as e:
            self.console.print(f"[red]Error reading lore file: {e}[/red]")
        return self

    def _parse_content(self, content: str) -> None:
        """Parse lore content — simplified to just store full content for LLM."""
        self.db.lore_summary = content
        self.db.categories = [
            "Setting", "Factions", "Magic", "Technology", "World History",
            "Character Creation", "Tone and Style",
        ]
        self.db.add_fact(LoreFact(
            category="Lore Summary",
            fact=content[:5000],
            raw_content=content,
            severity="info",
        ))

    def get_context(self, user_input: str) -> dict:
        """Build context dictionary for LLM validation query."""
        return {
            "lore_summary": self.db.lore_summary,
            "user_input": user_input,
            "categories": self.db.categories,
        }
