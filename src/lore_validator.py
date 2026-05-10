import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Set
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


@dataclass
class LoreFact:
    """Represents a single fact about the world that can be validated."""
    category: str
    fact: str
    keywords: List[str] = field(default_factory=list)
    severity: str = "warning"  # error, warning, info

    @classmethod
    def from_dict(cls, data: dict) -> "LoreFact":
        return cls(
            category=data["category"],
            fact=data["fact"],
            keywords=data.get("keywords", []),
            severity=data.get("severity", "warning")
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
class LoreConflict:
    """A conflict between user input and established lore."""
    fact: LoreFact
    conflict: str
    severity: str = "error"


@dataclass
class LoreDatabase:
    """Manages lore facts and constraints extracted from lore files."""
    facts: List[LoreFact] = field(default_factory=list)
    constraints: List[LoreConstraint] = field(default_factory=list)
    categories: Set[str] = field(default_factory=set)

    def add_fact(self, fact: LoreFact):
        self.facts.append(fact)
        self.categories.add(fact.category)

    def add_constraint(self, constraint: LoreConstraint):
        self.constraints.append(constraint)

    def find_related_facts(self, input_text: str) -> List[LoreFact]:
        """Find lore facts that might be relevant to the user's input."""
        input_lower = input_text.lower()
        relevant = []
        for fact in self.facts:
            fact_text = fact.fact.lower()
            for keyword in fact.keywords:
                if keyword.lower() in input_lower or keyword.lower() in fact_text:
                    relevant.append(fact)
                    break
            # Also check if the input text matches the fact content
            if any(kw.lower() in input_lower for kw in fact.keywords):
                relevant.append(fact)
        return relevant


class LoreParser:
    """Parses markdown lore files into a structured database."""

    def __init__(self, console: Console = None):
        self.console = console or Console()
        self.db = LoreDatabase()

    def parse_markdown(self, file_path: str) -> "LoreParser":
        """Parse a markdown lore file and extract facts/constraints."""
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            self._parse_content(content)
        except Exception as e:
            self.console.print(f"[red]Error reading lore file: {e}[/red]")
        return self

    def _parse_content(self, content: str):
        """Parse lore content into structured facts."""
        # Parse setting facts
        setting_patterns = [
            (r"Setting:\s*(.+)", "Setting", ["setting", "world"]),
            (r"Technology:\s*(.+)", "Technology", ["tech level", "magic level"]),
            (r"Magic:\s*(.+)", "Magic System", ["magic", "supernatural"]),
        ]

        for pattern, category, keywords in setting_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                self.db.add_fact(LoreFact(
                    category=category,
                    fact=match.group(1).strip(),
                    keywords=keywords,
                    severity="info"
                ))

        # Parse factions
        faction_pattern = r"Key Factions:\s*(.+?)(?=\n\n|$)"
        match = re.search(faction_pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            factions_text = match.group(1)
            for line in factions_text.split("\n"):
                line = line.strip()
                if line and not line.startswith("-"):
                    continue
                faction = line.strip(" -").strip()
                if faction:
                    self.db.add_fact(LoreFact(
                        category="Factions",
                        fact=f"Available faction: {faction}",
                        keywords=[faction.lower(), "faction", "group", "guild"],
                        severity="info"
                    ))

        # Extract explicit constraints (forbidden items, required elements)
        forbidden_patterns = [
            (r"[^:]+:\s*no\s+\w+", "Forbidden", ["forbidden", "not allowed"]),
            (r"[^:]+:\s*not\s+\w+", "Forbidden", ["forbidden", "impossible"]),
        ]

        for pattern, category, keywords in forbidden_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                forbidden = match.group(1).replace("no ", "").replace("not ", "").strip()
                self.db.add_fact(LoreFact(
                    category=category,
                    fact=f"Cannot use: {forbidden}",
                    keywords=keywords,
                    severity="error"
                ))

        # Parse lore sections with keywords
        self._parse_lore_sections(content)

    def _parse_lore_sections(self, content: str):
        """Parse any explicit lore sections with category-keyword pairs."""
        # Look for sections like "World: Oakhaven", "Setting: Medieval", etc.
        for line in content.split("\n"):
            line = line.strip()
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip().lower()
                value = value.strip()

                if key in ["world", "setting", "technology", "magic", "faction", "race"]:
                    self.db.add_fact(LoreFact(
                        category=key.capitalize(),
                        fact=value,
                        keywords=[key, value.lower()],
                        severity="info"
                    ))


class LoreValidator:
    """Validates user input against the lore database."""

    def __init__(self, lore_parser: LoreParser):
        self.parser = lore_parser
        self.console = console

    def validate_input(self, user_input: str) -> tuple:
        """
        Validate user input against lore.
        Returns: (is_valid, list of conflicts, suggested_revisions)
        """
        conflicts = []
        suggestions = []

        # Find relevant lore facts
        relevant_facts = self.parser.db.find_related_facts(user_input)

        # Check each relevant fact for conflicts
        for fact in relevant_facts:
            # Check for forbidden keywords
            input_lower = user_input.lower()
            forbidden = fact.fact.lower()

            # Extract forbidden values from fact description
            forbidden_matches = re.findall(
                r"(cannot|forbidden|not allowed|impossible)\s+(.+?)(?:\.|$|,\s*and)",
                forbidden,
                re.IGNORECASE
            )

            for _, forbidden_value in forbidden_matches:
                forbidden_value = forbidden_value.lower()
                if forbidden_value in input_lower or any(kw.lower() in input_lower for kw in fact.keywords):
                    conflicts.append(LoreConflict(
                        fact=fact,
                        conflict=f"Input conflicts with lore: {forbidden_value} is not allowed",
                        severity=fact.severity
                    ))

            # Check if input mentions a faction/race that doesn't exist
            if fact.category in ["Factions", "Setting"]:
                mentioned = re.search(r"\b(\w+\s*(\w+)?)\b", input_lower)
                if mentioned:
                    mentioned_entity = mentioned.group(1)
                    if mentioned_entity not in [f.fact for f in self.parser.db.facts if f.category == "Factions"]:
                        conflicts.append(LoreConflict(
                            fact=fact,
                            conflict=f"Unknown {fact.category.lower()}: {mentioned_entity}",
                            severity="warning"
                        ))
                        suggestions.append(f"Available factions: {', '.join([f.fact for f in self.parser.db.facts if f.category == 'Factions'])}")

        return len(conflicts) == 0, conflicts, suggestions

    def negotiate(self, user_input: str, conflicts: List[LoreConflict]) -> Optional[str]:
        """
        Negotiate with user to resolve conflicts.
        Returns the revised input or None if negotiation continues.
        """
        if not conflicts:
            return user_input

        # Build negotiation panel
        negotiation_text = self._build_negotiation_message(conflicts)

        self.console.print(Panel(
            negotiation_text,
            title="Lore Inconsistency Detected",
            border_style="red",
            box=box.DOUBLED
        ))

        # Show available options
        options = self._build_revision_options(conflicts)
        if options:
            self.console.print(options)

        # Wait for user response
        response = self.console.input("\n[yellow]How would you like to proceed?[/yellow] ")
        self.console.print("[gray](Enter 'y' to accept revision, 'n' to try again, 'c' to skip)[/gray]")

        if "y" in response.lower():
            # User accepts the suggested revision
            return self._apply_revision(user_input, conflicts)
        elif "n" in response.lower():
            # User tries to revise themselves
            new_input = self.console.input("\n[blue]Please revise your input to match the lore:[/blue]\n>")
            return self.validate_input(new_input)
        else:
            # Skip the conflict
            return user_input

    def _build_negotiation_message(self, conflicts: List[LoreConflict]) -> str:
        """Build the negotiation message."""
        lines = ["\n", "We detected the following inconsistencies with the established lore:\n"]

        for i, conflict in enumerate(conflicts, 1):
            severity_icon = {
                "error": "[red][!]",
                "warning": "[yellow][!]}",
                "info": "[gray][i]"}
            lines.append(f"{severity_icon.get(conflict.severity)} [?] Conflict {i}: {conflict.conflict}\n")

        lines.append("\n" + "=" * 50 + "\n")
        lines.append("To proceed, please revise your input to be consistent with the lore.\n")

        return "".join(lines)

    def _build_revision_options(self, conflicts: List[LoreConflict]) -> Optional[Table]:
        """Build a table of suggested revisions."""
        if not conflicts:
            return None

        tables = []

        for conflict in conflicts:
            fact = conflict.fact
            category = fact.category
            description = fact.fact

            # Extract suggestions based on conflict type
            if "faction" in description.lower() or "group" in description.lower():
                suggestions = self._get_available_factions()
            elif "magic" in description.lower():
                suggestions = ["Rare and taxing", "Feared by commoners"]
            elif "technology" in description.lower():
                suggestions = ["Medieval era only", "No gunpowder or steam"]

            if suggestions:
                table = Table(
                    title=f"Suggested Revision for: {category}",
                    box=box.SIMPLE,
                    show_header=True,
                    header_style="bold cyan"
                )
                table.add_column("Suggestion", style="cyan")
                for suggestion in suggestions:
                    table.add_row(suggestion)
                tables.append(table)

        return tables[0] if tables else None

    def _get_available_factions(self) -> List[str]:
        """Get list of available factions from lore."""
        return [f.fact for f in self.parser.db.facts if f.category == "Factions"]

    def _apply_revision(self, original_input: str, conflicts: List[LoreConflict]) -> str:
        """Apply suggested revisions to the user's input."""
        revised = original_input

        for conflict in conflicts:
            fact = conflict.fact
            # Extract the forbidden/restricted value
            match = re.search(r"(\w+\s*(\w+)?)", fact.fact)
            if match:
                restricted = match.group(1)
                # Replace in input if it appears as a word
                revised = re.sub(
                    r"\b" + re.escape(restricted.lower()) + r"\b",
                    "appropriate entity",
                    revised,
                    flags=re.IGNORECASE
                )

        return revised


# Convenience function to create a validator from a lore file
def create_validator(lore_file: str = "data/lore_summary.md") -> LoreValidator:
    """Create a lore validator from a markdown file."""
    parser = LoreParser(console)
    parser.parse_markdown(Path(lore_file))
    return LoreValidator(parser)
