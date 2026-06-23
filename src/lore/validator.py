"""Lore validator — LLM-based semantic validation against world lore."""

import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src._utils import load_prompt as _load_prompt
from src.llm_client import LLMClient

from .parser import LoreParser, LoreDatabase, LoreFact, LoreConstraint, LoreConflict


class LLMValidationError:
    """Error returned by LLM validation."""

    def __init__(
        self,
        is_valid: bool,
        conflicts: List[LoreConflict],
        suggestions: List[str],
        llm_response: Optional[str] = None,
    ):
        self.is_valid = is_valid
        self.conflicts = conflicts
        self.suggestions = suggestions
        self.llm_response = llm_response


class LoreValidator:
    """Validates user input against lore using LLM-based semantic analysis."""

    def __init__(
        self,
        lore_parser: LoreParser,
        model_name: str = "qwen3.5:64k",
        console: Console | None = None,
        llm_client: LLMClient | None = None,
    ):
        self.parser = lore_parser
        self.console = console or Console()
        self.model_name = model_name
        # Accept an injected client for testing/composition; create one only when needed.
        self.llm_client = llm_client or LLMClient(model_name=model_name)

    def validate_input(self, user_input: str) -> LLMValidationError:
        """Validate user input against lore using LLM.

        Returns: LLMValidationError with validation results
        """
        self.console.print(f"[cyan]Validating input: {user_input}[/cyan]")

        context = self.parser.get_context(user_input)
        validation_prompt = self._create_validation_prompt(context)

        llm_response = self.llm_client.generate_flavor_text(
            context=json.dumps(context, indent=2),
            instruction=validation_prompt,
        )

        try:
            result = self._parse_llm_response(llm_response)
        except Exception as e:
            self.console.print(f"[yellow]Failed to parse LLM response: {e}[/yellow]")
            return self._fallback_validation(user_input)

        conflicts = result.get("conflicts", [])
        if conflicts and isinstance(conflicts, list):
            converted: list[LoreConflict] = []
            for c in conflicts:
                if isinstance(c, dict):
                    converted.append(self._create_conflict_from_llm(
                        llm_type=c.get("type", "unknown"),
                        message=c.get("message", ""),
                        severity=c.get("severity", "error"),
                    ))
                else:
                    converted.append(c)
            conflicts = converted

        is_valid = result["is_valid"]
        suggestions = result.get("suggestions", [])

        if not is_valid and self.llm_client:
            suggestions = self._generate_revision_options(context.get("user_input", ""), conflicts)

        return LLMValidationError(is_valid=is_valid, conflicts=conflicts, suggestions=suggestions, llm_response=llm_response)

    def _generate_revision_options(self, user_input: str, conflicts: List[LoreConflict]) -> List[str]:
        """Generate exactly 3 concrete revised backstory options that resolve the given lore conflicts."""
        conflict_details = "\n".join(f"- {c.conflict_message}" for c in conflicts)
        template = _load_prompt("backstory_revision.md") or (
            "Revise this user's backstory so it fits the world lore.\n\n"
            "LORE CONTEXT:\n{lore_summary}\n\n"
            "USER INPUT: {user_input}\n\n"
            "CONFLICTS TO FIX:\n{conflict_details}\n\n"
            'Return EXACTLY a JSON array of 3 revised backstory strings.\n'
            "Format: [\"revision 1\", \"revision 2\", \"revision 3\"]\n"
            "No markdown, no code blocks, no explanation."
        )
        revision_prompt = template.format(
            lore_summary=self.parser.db.lore_summary[:4000],
            user_input=user_input,
            conflict_details=conflict_details,
        )

        try:
            response = self.llm_client.generate_flavor_text(
                context=revision_prompt,
                instruction="Return EXACTLY a JSON array of exactly 3 revised backstory strings. No markdown, no code blocks, no explanation.",
            )
            start = response.find('[')
            end = response.rfind(']') + 1
            if start != -1 and end > start:
                revisions = json.loads(response[start:end])
                valid = [rev for rev in revisions[:3] if isinstance(rev, str)]
                if len(valid) == 3:
                    return valid
        except Exception as e:
            self.console.print(f"[dim]Revision generation debug: {e}[/dim]")

        # Fallback — always generate exactly 3 concrete revised backstories from lore data
        factions = [f for f in ["Iron Circle", "Root-Walkers", "Oakhaven Guard", "Void Monks"] if f.lower() in self.parser.db.lore_summary.lower()]
        if not factions:
            factions = ["Iron Circle", "Root-Walkers"]

        options: list[str] = []
        for i in range(3):
            faction = factions[i % len(factions)]
            conflict = conflicts[i % len(conflicts)]
            prefix = user_input.split('.')[0] if '.' in user_input else user_input
            options.append(f"{prefix}. Instead of {conflict.fact.category}, you belong to the {faction}.")

        return options

    def _create_validation_prompt(self, context: dict) -> str:
        """Create the LLM prompt for validation."""
        system = _load_prompt("lore_validation.md")
        return (
            f"{system}\n\n"
            f"LORE CONTEXT:\n{context.get('lore_summary', '')}\n\n"
            f'USER INPUT:\n"{context.get("user_input", "")}"'
        )

    def _parse_llm_response(self, response: str) -> dict:
        """Parse the LLM's JSON response."""
        response = response.strip()

        # Try to extract JSON from markdown code blocks
        markdown_patterns = [
            r'```json\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)```',
            r'```\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)```',
            r'` `` `json\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)` `` `',
            r'` `` `\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}\s*)` `` `',
        ]

        json_content: str | None = None
        for pattern in markdown_patterns:
            match = re.search(pattern, response, re.DOTALL)
            if match:
                json_content = match.group(1)
                break

        if not json_content:
            try:
                json_content = response
            except json.JSONDecodeError:
                json_start = response.find('{')
                json_end = response.rfind('}') + 1
                if json_start != -1:
                    json_content = response[json_start:json_end]
                else:
                    self.console.print("[yellow]No JSON found in LLM response[/yellow]")
                    return {"is_valid": True, "conflicts": [], "suggestions": []}

        try:
            return json.loads(json_content)
        except json.JSONDecodeError as e:
            self.console.print(f"[yellow]Failed to parse LLM JSON: {e}[/yellow]")
            repaired = self._repair_json(json_content)
            if repaired is not None:
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
            return {"is_valid": True, "conflicts": [], "suggestions": []}

    @staticmethod
    def _repair_json(text: str) -> Optional[str]:
        """Attempt to fix common LLM JSON issues before giving up."""
        repaired = re.sub(r"(?<=\s)'([^']+)'\s*:", r'"\1":', text)
        repaired = re.sub(r':\s+([a-zA-Z_]\w*)\s*([,}\]])', r': "\1"\2', repaired)
        return repaired

    def _create_conflict_from_llm(self, llm_type: str, message: str, severity: str = "error") -> LoreConflict:
        """Create a LoreConflict from LLM response."""
        return LoreConflict(
            fact=LoreFact(category="General", fact=message),
            conflict_type=llm_type,
            conflict_message=message,
            severity=severity,
            suggestion="",
        )

    def _fallback_validation(self, user_input: str) -> LLMValidationError:
        """Fallback validation using basic rules if LLM fails."""
        conflicts: list[LoreConflict] = []
        suggestions: list[str] = []
        input_lower = user_input.lower()

        if any(term in input_lower for term in ["gunpowder", "steam", "steam engine", "rocket", "cannon"]):
            conflicts.append(LoreConflict(
                fact=LoreFact(category="Technology Level", fact="Medieval era only"),
                conflict_type="tech_violation",
                conflict_message="Sci-Fi technology (gunpowder/steam) is not allowed in this medieval setting",
                severity="error",
                suggestion="Use medieval-appropriate technology like wooden weapons or stone castles",
            ))
            suggestions.append("Try a medieval-appropriate action instead")

        if any(term in input_lower for term in ["time travel", "resurrection", "godlike power"]):
            conflicts.append(LoreConflict(
                fact=LoreFact(category="Magic Rules", fact="Magic is rare and dangerous"),
                conflict_type="magic_violation",
                conflict_message="That level of power violates the magic system rules",
                severity="error",
                suggestion="Use standard rare and taxing magic instead",
            ))
            suggestions.append("Choose standard magic effects instead")

        if ":" in user_input or "," in user_input:
            potential_factions = re.findall(r"\b(\w+\s*(?:knight|elf|dwarf|wizard|ninja|mercenary)?)\b", input_lower)
            for faction in potential_factions:
                if faction.lower() not in ["oakhaven", "the void", "wanderer", "knight", "elf", "dwarf", "wizard", "ninja", "mercenary"]:
                    conflicts.append(LoreConflict(
                        fact=LoreFact(category="Factions", fact=f"Available faction: {faction}"),
                        conflict_type="unknown_faction",
                        conflict_message=f"{faction} is not a recognized faction in this world",
                        severity="warning",
                        suggestion="Choose from established factions like 'The Iron Circle' or 'The Root-Walkers'",
                    ))

        return LLMValidationError(is_valid=len(conflicts) == 0, conflicts=conflicts, suggestions=suggestions)


# Convenience function
def create_validator(
    lore_file: str = "data/lore_summary.md",
    llm_client: LLMClient | None = None,
) -> LoreValidator:
    """Create a lore validator from a markdown file.

    Args:
        lore_file: path to the world-lore markdown file.
        llm_client: optional injected LLMClient for testing/composition.
    """
    parser = LoreParser(Console())
    parser.parse_markdown(Path(lore_file))
    return LoreValidator(parser, llm_client=llm_client)
