"""Lore validation rules — fallback patterns and forbidden tech/magic lists."""

FORBIDDEN_TECH = ["gunpowder", "steam", "steam engine", "rocket", "cannon"]

FORBIDDEN_MAGIC = ["time travel", "resurrection", "godlike power"]

KNOWN_FACTIONS = {"oakhaven", "the void", "wanderer", "knight", "elf", "dwarf", "wizard", "ninja", "mercenary"}

FACTION_HINTS = "Choose from established factions like 'The Iron Circle' or 'The Root-Walkers'"

UNKNOWN_FACTION_PATTERN = r"\b(\w+\s*(?:knight|elf|dwarf|wizard|ninja|mercenary)?)\b"
