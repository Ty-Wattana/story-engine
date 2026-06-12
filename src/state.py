from dataclasses import dataclass, field
from typing import Dict, List, Any


# ---------------------------------------------------------------------------
# Stat Bonuses per PHB-style table (stat → modifier)
# ---------------------------------------------------------------------------

STAT_BONUS: Dict[int, int] = {
    i: (i - 10) // 2 for i in range(1, 21)
}


@dataclass
class PlayerStats:
    """Core ability scores and derived bonuses per PHB-style table."""

    strength: int = 10
    dexterity: int = 10
    intelligence: int = 10
    wisdom: int = 10
    constitution: int = 10
    charisma: int = 10

    _bonus_cache: Dict[str, int] = field(default_factory=dict)

    def bonus(self, stat: str) -> int:
        """Return the ability modifier for a given stat (e.g. 'strength' → +3 for score 16)."""
        if stat not in self._bonus_cache:
            self._bonus_cache[stat] = STAT_BONUS.get(
                getattr(self, stat), 0
            )
        return self._bonus_cache[stat]

    def bonus_for_choice(self) -> int:
        """Return the max bonus across all stats (used when player picks their best)."""
        return max(
            self.bonus("strength"),
            self.bonus("dexterity"),
            self.bonus("intelligence"),
            self.bonus("wisdom"),
            self.bonus("constitution"),
            self.bonus("charisma"),
        )


@dataclass
class Player:
    name: str
    faction: str = "Unknown"
    motivation: str = "Survive"
    goal: str = "None"
    inventory: List[str] = field(default_factory=list)
    reputation: Dict[str, int] = field(default_factory=dict)

    # Phase 3 additions
    stats: PlayerStats = field(default_factory=PlayerStats)


@dataclass
class WorldState:
    current_location: str = "The Void"
    active_npcs: List[str] = field(default_factory=list)
    turn_count: int = 0

    def advance_turn(self):
        self.turn_count += 1


# ---------------------------------------------------------------------------
# StateManager — utility for bulk/typed mutations (replaces ad-hoc dict)
# ---------------------------------------------------------------------------

class StateManager:
    """Thin wrapper over Player + WorldState that standardises effect application."""

    def __init__(self, player: Player, world: WorldState):
        self.player = player
        self.world = world

    def apply_effect(self, effects: Dict[str, Any]) -> None:
        """Mutate player/world state given a dict of string-key → value.

        Supported keys (prefix-based dispatch):
            inventory.add <val>   – append to player.inventory
            inventory.remove <v>  – remove first match from player.inventory
            reputation.inc <f>    – increment rep by faction f
        """
        for key_value_str, _raw_val in effects.items():
            parts = str(key_value_str).split(None, 1)
            if len(parts) < 2:
                continue
            key, val = parts[0], parts[1]

            if key == "inventory.add":
                self.player.inventory.append(val)
            elif key == "inventory.remove":
                inv = getattr(self.player, "inventory", [])
                if val in inv:
                    inv.remove(val)
            elif key == "reputation.inc":
                rep = self.player.reputation
                # use a simple string representation as the faction key
                rep_key = str(val)
                rep[rep_key] = rep.get(rep_key, 0) + 1

    def snapshot(self) -> dict:
        """Return a copy-friendly dict of current state."""
        return {
            "player_name": self.player.name,
            "faction": self.player.faction,
            "inventory": list(self.player.inventory),
            "reputation": dict(self.player.reputation),
            "location": self.world.current_location,
            "turn": self.world.turn_count,
            # stats
            **{f"stat.{s}": getattr(self.player.stats, s) for s in ["strength","dexterity","intelligence","wisdom","constitution","charisma"]},
        }
