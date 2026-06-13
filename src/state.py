from dataclasses import dataclass, field
from typing import Dict, List, Any


# ---------------------------------------------------------------------------
# Stat Bonuses per PHB-style table (stat -> modifier)
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
        """Return the ability modifier for a given stat (e.g. 'strength' -> +3 for score 16)."""
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
    """Thin wrapper over Player + WorldState that standardises effect application.

    Supported effect keys use the unified grammar ``entity.field.operator``:
        player.inventory.add <val>       – append to inventory
        player.inventory.remove <val>    – remove first match from inventory
        player.reputation.inc <faction>  – increment rep for faction f
    """

    def __init__(self, player: Player, world: WorldState):
        self.player = player
        self.world = world

    @property
    def proficiency(self) -> int:
        """Natural progression: +2 base, scaling every 5 turns.

        Matches D&D-style adventuring milestone: a new tier roughly every 5 levels.
        """
        return 2 + self.world.turn_count // 5

    def apply_effect(self, effects: Dict[str, Any]) -> Dict[str, Any]:
        """Mutate player/world state given a dict of effect keys -> values.

        Returns a summary dict of what actually changed for logging/display.
        """
        applied: Dict[str, Any] = {}

        for key, val in effects.items():
            # Sanitize value — coerce non-strings to str (Bug #6)
            safe_val = str(val) if not isinstance(val, bool) else int(val)

            if key == "player.inventory.add":
                self.player.inventory.append(safe_val)
                applied[key] = safe_val
            elif key == "player.inventory.remove":
                inv = getattr(self.player, "inventory", [])
                if safe_val in inv:
                    inv.remove(safe_val)
                    applied[key] = f"removed {safe_val}"
                else:
                    applied[key] = f"{safe_val} not found — skipped"
            elif key == "player.reputation.inc":
                rep_key = str(safe_val)
                self.player.reputation[rep_key] = self.player.reputation.get(rep_key, 0) + 1
                applied[key] = self.player.reputation[rep_key]

        return applied

    def apply_outcome_effects(self, outcome_level: str, action_type: str) -> Dict[str, Any]:
        """Apply effects determined by the engine based on outcome + action type.

        This is the *only* place state-mutation effects are computed — never the LLM.
        Returns summary of what changed.
        """
        effects = self._resolve_effects(outcome_level, action_type)
        if not effects:
            return {}
        return self.apply_effect(effects)

    # ------------------------------------------------------------------
    # Internal effect resolution tables (expandable, deterministic)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_effects(outcome_level: str, action_type: str) -> Dict[str, Any]:
        """Return raw {effect_key: value} dict for the given outcome + type."""

        if outcome_level in ("success", "crit", "crit_fresh"):
            return StateManager._on_success(action_type, outcome_level)
        else:
            # partial or failure — always apply *something* (partial benefits, failure penalties)
            return StateManager._on_failure(outcome_level, action_type)

    @staticmethod
    def _on_success(action_type: str, outcome: str) -> Dict[str, Any]:
        """Effects when the target DC is met or exceeded."""
        bonus = "+1" if outcome == "crit_fresh" else ""
        bonus += "+2" if outcome == "crit" else ""

        rules = {
            "combat":  lambda: {"player.reputation.inc": "enemies_defeated"},
            "stealth": lambda: {"player.inventory.add": f"shadow_ward{bonus}"},
            "social":  lambda: {"player.reputation.inc": "trust_gained"},
            "exploration": lambda: {f"player.inventory.add": f"discovery{bonus}"},
            "item":    lambda: {f"player.inventory.add": "scrap_metal"},
        }
        return rules.get(action_type, lambda: {})()

    @staticmethod
    def _on_failure(outcome_level: str, action_type: str) -> Dict[str, Any]:
        """Effects when the target DC is missed (partial or full failure)."""
        if outcome_level == "partial":
            # Partial — still get something small; engine picks a modest benefit.
            partial_rules = {
                "combat":  lambda: {"player.inventory.add": "rusty_blade"},
                "stealth": lambda: {"player.reputation.inc": "sneak_attempted"},
                "social":  lambda: {"player.reputation.inc": "conversation_started"},
                "exploration": lambda: {f"player.inventory.add": "clue_fragment"},
                "item":    lambda: {f"player.inventory.add": "useless_cog"},
            }
            return partial_rules.get(action_type, lambda: {})()
        else:
            # Full failure — penalty effects.
            penalty_rules = {
                "combat":  lambda: {"player.inventory.remove": "weapon"},
                "stealth": lambda: {"player.reputation.inc": "suspicion_raised"},
                "social":  lambda: {"player.reputation.inc": "offended_officer"},
                "exploration": lambda: {},           # exploration never penalizes on failure
                "item":    lambda: {"player.inventory.remove": "tool_kit"},
            }
            return penalty_rules.get(action_type, lambda: {})()

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
            # proficiency (Phase 3: natural progression)
            "proficiency": self.proficiency,
        }

    def choices_snapshot(self) -> dict:
        """Lightweight snapshot for DM choices — only location and turn.

        Avoids feeding verbose inventory/stats/reputation which causes the LLM
        to echo state text back as choice content.
        """
        return {
            "location": self.world.current_location,
            "turn": self.world.turn_count,
        }
