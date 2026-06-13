"""Lore module — parsing, validation, and lore rules."""

from src.lore.parser import LoreParser, LoreDatabase, LoreFact, LoreConstraint, LoreConflict
from src.lore.validator import LoreValidator, LLMValidationError, create_validator
from src.lore.rules import FORBIDDEN_TECH, FORBIDDEN_MAGIC, KNOWN_FACTIONS, FACTION_HINTS, UNKNOWN_FACTION_PATTERN

__all__ = [
    "LoreParser", "LoreDatabase", "LoreFact", "LoreConstraint", "LoreConflict",
    "LoreValidator", "LLMValidationError", "create_validator",
    "FORBIDDEN_TECH", "FORBIDDEN_MAGIC", "KNOWN_FACTIONS", "FACTION_HINTS", "UNKNOWN_FACTION_PATTERN",
]
