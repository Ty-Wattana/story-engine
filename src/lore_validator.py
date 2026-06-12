"""
Lore Validation System with LLM-based semantic validation.

This module provides:
1. LoreParser - Parses markdown lore files into structured knowledge
2. LoreValidator - Uses LLM to validate user input against lore
3. LLM-based conflict detection and intelligent suggestions
"""
import json
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
import re

# Import LLM client for semantic validation
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.llm_client import LLMClient

console = Console()


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
class LoreDatabase:
    """Manages lore facts and constraints extracted from lore files."""
    lore_summary: str = ""
    facts: List[LoreFact] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)

    def add_fact(self, fact: LoreFact):
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


@dataclass
class LLMValidationError:
    """Error returned by LLM validation."""
    is_valid: bool
    conflicts: List[LoreConflict]
    suggestions: List[str]
    llm_response: Optional[str] = None

# Export at module level for testing
__all__ = ["LoreParser", "LoreValidator", "LoreFact", "LoreConflict", "LLMValidationError", "create_validator", "set_llm_client"]

class LoreParser:
    """Parses markdown lore files into a structured database for LLM context."""

    def __init__(self, console: Console = None):
        self.console = console or Console()
        self.db = LoreDatabase()

    def parse_markdown(self, file_path: str) -> "LoreParser":
        """Parse a markdown lore file and build context for LLM."""
        try:
            content = Path(file_path).read_text(encoding="utf-8")
            self._parse_content(content)
        except Exception as e:
            self.console.print(f"[red]Error reading lore file: {e}[/red]")
        return self

    def _parse_content(self, content: str):
        """Parse lore content - simplified to just store full content for LLM."""
        # Store the full content for LLM to reason about
        self.db.lore_summary = content

        # Extract categories from content
        self.db.categories = [
            "Setting", "Factions", "Magic", "Technology", "World History",
            "Character Creation", "Tone and Style"
        ]

        # Add a general lore fact containing the full content
        self.db.add_fact(LoreFact(
            category="Lore Summary",
            fact=content[:5000],  # Truncate for storage
            raw_content=content,
            severity="info"
        ))

    def _parse_setting(self, content: str, categories: set):
        """Parse setting-related lore."""
        patterns = [
            (r"Setting:\s*(.+)", "Setting", ["setting", "world"]),
            (r"Technology:\s*(.+)", "Technology", ["tech level"]),
            (r"Magic:\s*(.+)", "Magic System", ["magic", "supernatural"]),
        ]

        for pattern, category, _ in patterns:
            match = __import__("re").search(pattern, content, __import__("re").IGNORECASE)
            if match:
                raw_content = f"{pattern}: {match.group(1)}"
                self.db.add_fact(LoreFact(
                    category=category,
                    fact=match.group(1).strip(),
                    raw_content=raw_content,
                    severity="info"
                ))
                categories.add(category)

    def _parse_factions(self, content: str, categories: set):
        """Parse faction information."""
        faction_pattern = r"Key Factions:\s*(.+?)(?=\n\n|$)"
        match = __import__("re").search(faction_pattern, content,
                                          __import__("re").IGNORECASE | __import__("re").DOTALL)
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
                        raw_content=f"Key Factions: {factions_text}",
                        severity="info"
                    ))
                    categories.add("Factions")

    def _parse_magic(self, content: str, categories: set):
        """Parse magic system rules."""
        # Look for magic-related sections
        lines = content.split("\n")
        in_magic_section = False
        magic_content = []

        for line in lines:
            line_lower = line.lower()
            if "magic" in line_lower or "supernatural" in line_lower or "spell" in line_lower:
                in_magic_section = True
            if in_magic_section:
                magic_content.append(line)
            if line.strip() and not line.startswith(" ") and ":" in line:
                if "magic" not in line_lower or magic_content:
                    break

        if magic_content:
            self.db.add_fact(LoreFact(
                category="Magic Rules",
                fact="\n".join(magic_content[:5]),  # Take first 5 lines
                raw_content="".join(magic_content),
                severity="warning"
            ))
            categories.add("Magic Rules")

    def _parse_technology(self, content: str, categories: set):
        """Parse technology level."""
        lines = content.split("\n")
        tech_content = []
        in_tech_section = False

        for line in lines:
            line_lower = line.lower()
            if "technology" in line_lower or "tech" in line_lower or "weapon" in line_lower:
                in_tech_section = True
            if in_tech_section:
                tech_content.append(line)
            if line.strip() and not line.startswith(" ") and ":" in line:
                if "technology" not in line_lower or tech_content:
                    break

        if tech_content:
            self.db.add_fact(LoreFact(
                category="Technology Level",
                fact="\n".join(tech_content[:5]),
                raw_content="".join(tech_content),
                severity="warning"
            ))
            categories.add("Technology Level")

    def _parse_forbidden_constraints(self, content: str, categories: set):
        """Parse explicit forbidden constraints."""
        forbidden_patterns = [
            (r"no\s+\w+", "Forbidden", ["forbidden", "not allowed"]),
            (r"not\s+\w+", "Forbidden", ["forbidden", "impossible"]),
        ]

        for pattern, category, _ in forbidden_patterns:
            match = __import__("re").search(pattern, content, __import__("re").IGNORECASE)
            if match:
                forbidden = match.group(1).replace("no ", "").replace("not ", "").strip()
                self.db.add_fact(LoreFact(
                    category=category,
                    fact=f"Cannot use: {forbidden}",
                    raw_content=match.group(0),
                    severity="error"
                ))
                categories.add(category)

    def add_fact(self, fact: LoreFact):
        self.db.facts.append(fact)
        self.db.categories.append(fact.category)

    def get_context(self, user_input: str) -> dict:
        """Build context dictionary for LLM validation query."""
        return {
            "lore_summary": self.db.lore_summary,
            "user_input": user_input,
            "categories": self.db.categories
        }


class LoreValidator:
    """Validates user input against lore using LLM-based semantic analysis."""

    def __init__(self, lore_parser: LoreParser,
                 model_name: str = "qwen3.5:64k",
                 console: Console = None):
        self.parser = lore_parser
        self.console = console or Console()
        self.model_name = model_name
        self.llm_client = LLMClient(model_name=model_name)

    def validate_input(self, user_input: str) -> LLMValidationError:
        """
        Validate user input against lore using LLM.

        Returns: LLMValidationError with validation results
        """
        self.console.print(f"[cyan]Validating input: {user_input}[/cyan]")

        # Build context for LLM
        context = self.parser.get_context(user_input)

        # Create validation prompt
        validation_prompt = self._create_validation_prompt(context)

        # Call LLM for validation
        llm_response = self.llm_client.generate_flavor_text(
            context=json.dumps(context, indent=2),
            instruction=validation_prompt
        )

        # Parse LLM response
        try:
            result = self._parse_llm_response(llm_response)
        except Exception as e:
            self.console.print(f"[yellow]Failed to parse LLM response: {e}[/yellow]")
            # Fallback to basic validation
            return self._fallback_validation(user_input)

        # Convert dict conflicts to LoreConflict objects if needed
        conflicts = result.get("conflicts", [])
        if conflicts and isinstance(conflicts, list):
            # Convert dicts to LoreConflict objects
            converted_conflicts = []
            for c in conflicts:
                if isinstance(c, dict):
                    converted_conflicts.append(self._create_conflict_from_llm(
                        llm_type=c.get("type", "unknown"),
                        message=c.get("message", ""),
                        severity=c.get("severity", "error")
                    ))
                else:
                    converted_conflicts.append(c)
            conflicts = converted_conflicts

        is_valid = result["is_valid"]
        suggestions = result.get("suggestions", [])

        # If there are conflicts, generate concrete revised backstory options via LLM
        if not is_valid and self.llm_client:
            suggestions = self._generate_revision_options(context.get("user_input", ""), conflicts)

        return LLMValidationError(
            is_valid=is_valid,
            conflicts=conflicts,
            suggestions=suggestions,
            llm_response=llm_response
        )

    def _generate_revision_options(self, user_input: str, conflicts: List[LoreConflict]) -> List[str]:
        """Generate exactly 3 concrete revised backstory options that resolve the given lore conflicts."""
        # Try LLM first
        conflict_details = "\n".join(f"- {c.conflict_message}" for c in conflicts)
        revision_prompt = (
            f"Revise this user's backstory so it fits the world lore.\n\n"
            f"LORE CONTEXT:\n{self.parser.db.lore_summary[:4000]}\n\n"
            f"USER INPUT: {user_input!r}\n\n"
            f"CONFLICTS TO FIX:\n{conflict_details}\n\n"
            "Return EXACTLY a JSON array of 3 revised backstory strings.\n"
            'Format: ["revision 1", "revision 2", "revision 3"]\n'
            "No markdown, no code blocks, no explanation."
        )

        try:
            response = self.llm_client.generate_flavor_text(
                context=revision_prompt,
                instruction="Return EXACTLY a JSON array of exactly 3 revised backstory strings. No markdown, no code blocks, no explanation."
            )
            start = response.find('[')
            end = response.rfind(']') + 1
            if start != -1 and end > start:
                import json
                revisions = json.loads(response[start:end])
                valid = [rev for rev in revisions[:3] if isinstance(rev, str)]
                if len(valid) == 3:
                    return valid
        except Exception as e:
            self.console.print(f"[dim]Revision generation debug: {e}[/dim]")

        # Fallback: always generate exactly 3 concrete revised backstories from lore data
        factions = [f for f in ["Iron Circle", "Root-Walkers", "Oakhaven Guard", "Void Monks"] if f.lower() in self.parser.db.lore_summary.lower()]
        if not factions:
            factions = ["Iron Circle", "Root-Walkers"]

        options = []
        for i in range(3):
            faction = factions[i % len(factions)]
            conflict = conflicts[i % len(conflicts)]

            # Combine the user's original story with a lore-appropriate faction replacement
            prefix = user_input.split('.')[0] if '.' in user_input else user_input
            options.append(
                f"{prefix}. Instead of {conflict.fact.category}, you belong to the {faction}."
            )

        return options

    def _create_validation_prompt(self, context: dict) -> str:
        """Create the LLM prompt for validation."""
        return f"""Analyze the following user input against the established lore context:

LORE CONTEXT:
{context.get("lore_summary", "")}

USER INPUT:
"{context.get("user_input", "")}"

INSTRUCTIONS:
1. Analyze if the user input is consistent with the lore context
2. Identify any conflicts (e.g., forbidden technology, non-existent factions, magic misuse)
3. Consider the setting type, technology level, and magic rules
4. Be strict about lore violations

RESPOND ONLY WITH JSON IN THIS EXACT FORMAT:
{{
    "is_valid": true/false,
    "conflicts": [
        {{
            "type": "forbidden_value"|"unknown_faction"|"magic_violation"|"tech_violation"|"setting_violation",
            "message": "Clear explanation of the conflict",
            "severity": "error"|"warning"
        }}
    ],
    "suggestions": [
        "Optional suggestion text 1",
        "Optional suggestion text 2"
    ]
}}

Return is_valid=false if ANY conflict is found. Be thorough in checking against the lore."""

    def _parse_llm_response(self, response: str) -> dict:
        """Parse the LLM's JSON response."""
        import json
        import re
        # Clean up the response and try to parse JSON
        response = response.strip()

        # Try to find JSON in the response - handle various markdown formats
        json_content = None

        # Try to extract JSON from markdown code blocks (```, ```json, etc.)
        markdown_patterns = [
            r'```json\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)```',  # ```json...```
            r'```\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)```',      # ```...```
            r'` `` `json\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)` `` `',  # ` `` `json...` `` `
            r'` `` `\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)` `` `',    # ` `` ...` `` `
        ]

        for pattern in markdown_patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                json_content = match.group(1)
                break

        if not json_content:
            # If no markdown block found, try parsing the response directly
            try:
                json_content = response
            except json.JSONDecodeError:
                # Try to find any JSON object in the response
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                if json_start != -1:
                    json_content = response[json_start:json_end]
                else:
                    self.console.print("[yellow]No JSON found in LLM response[/yellow]")
                    self.console.print(f"[yellow]Response: {response[:200]}[/yellow]")
                    return {
                        "is_valid": True,
                        "conflicts": [],
                        "suggestions": []
                    }

        try:
            return json.loads(json_content)
        except json.JSONDecodeError as e:
            self.console.print(f"[yellow]Failed to parse LLM JSON: {e}[/yellow]")
            self.console.print(f"[yellow]Content: {json_content[:200]}[/yellow]")
            # Attempt to repair common LLM JSON issues and retry
            repaired = self._repair_json(json_content)
            if repaired is not None:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
            # Return defaults if parsing fails
            return {
                "is_valid": True,
                "conflicts": [],
                "suggestions": []
            }

    def _repair_json(self, text: str) -> Optional[str]:
        """Attempt to fix common LLM JSON issues before giving up.

        Known failure modes:
        - Array elements missing {}  ( [type: "x"] instead of [{"type": "x"}] )
        - Unquoted string values    ( severity: error  instead of  severity: "error" )
        - Single quotes used        ( 'key' instead of "key" )
        """
        repaired = text

        # Step 1: Replace single-quoted keys with double quotes
        repaired = re.sub(r"(?<=\s)'([^']+)'\s*:", r'"\1":', repaired)

        # Step 2: Unquote bare values (severity: error → severity: "error")
        # Only when value is not true/false/null and not already quoted.
        repaired = re.sub(
            r':\s+([a-zA-Z_]\w*)\s*([,}\]])',
            r': "\1"\2',
            repaired,
        )

        return repaired

    def _create_conflict_from_llm(self, llm_type: str, message: str, severity: str = "error") -> LoreConflict:
        """Create a LoreConflict from LLM response."""
        return LoreConflict(
            fact=LoreFact(category="General", fact=message),
            conflict_type=llm_type,
            conflict_message=message,
            severity=severity,
            suggestion=""
        )

    def _fallback_validation(self, user_input: str) -> LLMValidationError:
        """Fallback validation using basic rules if LLM fails."""
        conflicts = []
        suggestions = []

        input_lower = user_input.lower()

        # Check for forbidden technology (gunpowder, steam, etc.)
        if any(term in input_lower for term in ["gunpowder", "steam", "steam engine", "rocket", "cannon"]):
            conflicts.append(LoreConflict(
                fact=LoreFact(category="Technology Level", fact="Medieval era only"),
                conflict_type="tech_violation",
                conflict_message="Sci-Fi technology (gunpowder/steam) is not allowed in this medieval setting",
                severity="error",
                suggestion="Use medieval-appropriate technology like wooden weapons or stone castles"
            ))
            suggestions.append("Try a medieval-appropriate action instead")

        # Check for forbidden magic misuse
        if any(term in input_lower for term in ["time travel", "resurrection", "godlike power"]):
            conflicts.append(LoreConflict(
                fact=LoreFact(category="Magic Rules", fact="Magic is rare and dangerous"),
                conflict_type="magic_violation",
                conflict_message="That level of power violates the magic system rules",
                severity="error",
                suggestion="Use standard rare and taxing magic instead"
            ))
            suggestions.append("Choose standard magic effects instead")

        # Check for unknown factions
        if ":" in user_input or "," in user_input:
            # Extract potential faction mentions
            import re
            potential_factions = re.findall(r"\b(\w+\s*(?:knight|elf|dwarf|wizard|ninja|mercenary)?)\b",
                                           input_lower)
            if potential_factions:
                for faction in potential_factions:
                    # Skip common generic terms
                    if faction.lower() not in ["oakhaven", "the void", "wanderer", "knight", "elf", "dwarf", "wizard", "ninja", "mercenary"]:
                        conflicts.append(LoreConflict(
                            fact=LoreFact(category="Factions", fact=f"Available faction: {faction}"),
                            conflict_type="unknown_faction",
                            conflict_message=f"{faction} is not a recognized faction in this world",
                            severity="warning",
                            suggestion="Choose from established factions like 'The Iron Circle' or 'The Root-Walkers'"
                        ))

        return LLMValidationError(
            is_valid=len(conflicts) == 0,
            conflicts=conflicts,
            suggestions=suggestions
        )

    def negotiate(self, user_input: str, error: LLMValidationError) -> Optional[str]:
        """
        Negotiate with user to resolve conflicts.
        Returns the revised input or None if negotiation continues.
        """
        if not error.conflicts:
            return user_input

        # Build negotiation panel
        negotiation_text = self._build_negotiation_message(error)

        self.console.print(Panel(
            negotiation_text,
            title="Lore Inconsistency Detected",
            border_style="red",
            box=box.DOUBLED
        ))

        # Show available options
        options = self._build_revision_options(error)
        if options:
            self.console.print(options)

        # Wait for user response
        response = self.console.input("\n[yellow]How would you like to proceed?[/yellow] ")
        self.console.print("[gray](Enter 'y' to accept revision, 'n' to try again, 'c' to skip)[/gray]")

        if "y" in response.lower():
            # User accepts the suggested revision
            return self._apply_revision(user_input, error)
        elif "n" in response.lower():
            # User tries to revise themselves
            new_input = self.console.input("\n[blue]Please revise your input to match the lore:[/blue]\n>")
            return self.validate_input(new_input)
        else:
            # Skip the conflict
            return user_input

    def _build_negotiation_message(self, error: LLMValidationError) -> str:
        """Build the negotiation message."""
        lines = ["\n", "We detected the following inconsistencies with the established lore:\n"]

        for i, conflict in enumerate(error.conflicts, 1):
            severity_icon = {
                "error": "[red][!]",
                "warning": "[yellow][!]",
                "info": "[gray][i]"}
            lines.append(f"{severity_icon.get(conflict.severity, '[?]')} [?] Conflict {i}: {conflict.conflict_message}\n")

        lines.append("\n" + "=" * 50 + "\n")
        lines.append("To proceed, please revise your input to be consistent with the lore.\n")

        return "".join(lines)

    def _build_revision_options(self, error: LLMValidationError) -> Optional[Table]:
        """Build a table of suggested revisions."""
        if not error.conflicts:
            return None

        tables = []

        for conflict in error.conflicts:
            suggestion = conflict.suggestion or ""
            if suggestion:
                table = Table(
                    title=f"Suggested Revision: {conflict.conflict_type}",
                    box=box.SIMPLE,
                    show_header=True,
                    header_style="bold cyan"
                )
                table.add_column("Suggestion", style="cyan")
                table.add_row(suggestion)
                tables.append(table)

        return tables[0] if tables else None

    def _apply_revision(self, original_input: str, error: LLMValidationError) -> str:
        """Apply suggested revisions to the user's input."""
        if not error.suggestions:
            return original_input

        # If suggestions are structured revision options (e.g., "Revised: ..."), use them directly
        for s in error.suggestions:
            if s.startswith("Revised:"):
                return s[len("Revised:"):].strip()
        # Fallback: generate a revised version via LLM
        return self._generate_revision(original_input, error)

    def _generate_revision(self, original_input: str, error: LLMValidationError) -> str:
        """Generate a concrete revised backstory that resolves lore conflicts."""
        conflict_details = "\n".join(f"- {c.conflict_message}" for c in error.conflicts)
        revision_prompt = f"""Take the following user input and revise it so it is consistent with the lore.

LORE CONTEXT:
{self.parser.db.lore_summary[:8000]}

USER INPUT (has lore conflicts):
"{original_input}"

THESE ARE THE CONFLICTS:
{conflict_details}

PROVIDE ONLY A JSON ARRAY OF EXACTLY 3 REVISED BACKSTORIES IN THIS FORMAT:
["Revised version 1 that resolves all conflicts", "Revised version 2...", "Revised version 3..."]

Each revised backstory should preserve as much of the user's original intent and details as possible, while resolving the lore conflicts. Keep each one concise (1-3 sentences)."""

        try:
            response = self.llm_client.generate_flavor_text(
                context=revision_prompt,
                instruction="Respond with ONLY a JSON array of 3 revised backstory strings."
            )
            # Extract JSON array from response
            start = response.find('[')
            end = response.rfind(']') + 1
            if start != -1 and end > start:
                import json
                revisions = json.loads(response[start:end])
                return revisions[0]  # Return first suggestion
        except Exception:
            pass

        # Last resort fallback
        return f"Revised version of your input, adjusted to be consistent with the lore."

    def suggest_revisions(self) -> List[str]:
        """Generate multiple concrete revised backstory options for the user to choose from."""
        return [s.replace("Revised:", "").strip() if s.startswith("Revised:") else s for s in self._current_suggestions]

    def set_suggestions(self, suggestions: List[str]):
        """Store suggestions so suggest_revisions can access them."""
        self._current_suggestions = suggestions

    def get_concrete_revision_options(self) -> Optional[Table]:
        """Build a table of concrete revised backstory options using the LLM."""
        conflict_details = "\n".join(f"- {c.conflict_message}" for c in self.errors.conflicts[:3])
        revision_prompt = f"""Take the following user input and revise it so it is consistent with the lore.

LORE CONTEXT:
{self.parser.db.lore_summary[:4000]}

USER INPUT (has lore conflicts):
"{self.errors.input}"

THESE ARE THE CONFLICTS TO FIX:
{conflict_details}

PROVIDE ONLY A JSON ARRAY OF EXACTLY 3 REVISED BACKSTORIES IN THIS FORMAT:
["Revised version 1 that resolves all conflicts, preserving user's original intent", "Revised version 2...", "Revised version 3..."]

Each revised backstory should preserve as much of the user's original details as possible. Keep each one concise (1-3 sentences)."""

        try:
            response = self.llm_client.generate_flavor_text(
                context=revision_prompt,
                instruction="Respond with ONLY a JSON array of 3 revised backstory strings."
            )
            start = response.find('[')
            end = response.rfind(']') + 1
            if start != -1 and end > start:
                import json
                revisions = json.loads(response[start:end])
            else:
                # Fallback from parsed suggestion text
                suggestions = getattr(self, '_current_suggestions', None) or self.errors.suggestions
                if len(suggestions) >= 3:
                    revisions = [f"Suggestion {i+1}: {s}" for i, s in enumerate(suggestions)]
                else:
                    revisions = [f"Try a revision that addresses: {self.errors.conflicts[0].conflict_message}"]
        except Exception:
            suggestions = getattr(self, '_current_suggestions', None) or self.errors.suggestions
            if len(suggestions) >= 3:
                revisions = [f"Suggestion {i+1}: {s}" for i, s in enumerate(suggestions)]
            else:
                revisions = [f"Try a revision that addresses: {self.errors.conflicts[0].conflict_message}"]

        # Build table from revisions
        table = Table(
            title="Suggested Revisions",
            box=box.SIMPLE,
            show_header=True,
            header_style="bold cyan"
        )
        table.add_column("Option", style="cyan")
        for i, rev in enumerate(revisions[:3], 1):
            safe_rev = rev if not rev.startswith("Revised:") else rev[len("Revised:"):].strip()
            # Truncate long revisions for display
            display = safe_rev[:200] + "..." if len(safe_rev) > 200 else safe_rev
            table.add_row(f"[i]Option {i}:[/i] {display}")

        return table


# Convenience function to create a validator from a lore file
def create_validator(lore_file: str = "data/lore_summary.md") -> LoreValidator:
    """Create a lore validator from a markdown file."""
    parser = LoreParser(console)
    parser.parse_markdown(Path(lore_file))
    return LoreValidator(parser)