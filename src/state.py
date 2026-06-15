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

    # Reputation thresholds — unlock new content when reached.
    # When reputation for a faction reaches the threshold, the player gains access to that faction's trust benefits.
    # Not a dataclass field (no type annotation) so Python doesn't try to make it mutable default.
    REPUTATION_THRESHOLDS = {
        "trust_gained": 3,
        "enemies_defeated": 2,
        "sneak_attempted": 2,
        "conversation_started": 4,
        "suspicion_raised": -1,         # negative rep thresholds are immediate (penalties)
        "offended_officer": -1,
    }

    # Track cumulative reputation per faction for threshold checking.
    _rep_frozen: dict[str, int] = field(default_factory=dict)

    def check_rep_thresholds(self) -> list[str]:
        """Check if any reputation thresholds have been newly reached this turn.

        Returns a list of new threshold unlocks or penalties triggered.
        Clears tracked values after checking so each increment fires only once.
        """
        new_unlocks: list[str] = []
        for rep_key, total in self.reputation.items():
            prev = self._rep_frozen.get(rep_key, 0)
            threshold = self.REPUTATION_THRESHOLDS.get(rep_key, float("inf"))

            # Positive thresholds: check if we crossed upward through the threshold
            if total > 0 and threshold > 0 and prev < threshold <= total:
                new_unlocks.append(f"unlocked_{rep_key}")

            # Negative thresholds: immediate penalty on first reach
            if rep_key in ("suspicion_raised", "offended_officer") and total >= 1:
                unlock_key = f"{rep_key}_penalty"
                if unlock_key not in new_unlocks:
                    new_unlocks.append(unlock_key)

            self._rep_frozen[rep_key] = max(prev, total)

        # Clear cumulative counters that were just consumed by threshold checks
        for rep_key in list(self.reputation.keys()):
            if rep_key not in ("trust_gained", "enemies_defeated"):
                if self._rep_frozen.get(rep_key, 0) > 0:
                    pass  # keep it

        return new_unlocks


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
            elif key == "player.stats.str_bonus.inc":
                # Temporary stat bonus/penalty that stacks — represents a tangible improvement from a crit or penalty from failure.
                old_val = getattr(self.player.stats, "_str_bonus", 0)
                setattr(self.player.stats, "_str_bonus", old_val + safe_val)
                applied[key] = safe_val
            elif key == "player.stats.dex_bonus.inc":
                old_val = getattr(self.player.stats, "_dex_bonus", 0)
                setattr(self.player.stats, "_dex_bonus", old_val + safe_val)
                applied[key] = safe_val
            elif key == "player.stats.int_bonus.inc":
                old_val = getattr(self.player.stats, "_int_bonus", 0)
                setattr(self.player.stats, "_int_bonus", old_val + safe_val)
                applied[key] = safe_val
            elif key == "player.stats.con_penalty.inc":
                # Negative bonus (penalty from injury/failure)
                old_val = getattr(self.player.stats, "_con_penalty", 0)
                setattr(self.player.stats, "_con_penalty", old_val + int(safe_val))
                applied[key] = safe_val

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
        """Effects when the target DC is met or exceeded.

        Success always produces a meaningful mechanical change — not just collectible items.
        Crit variants give bonus effects on top of the base reward.
        """
        bonus = "+1" if outcome == "crit_fresh" else ""
        bonus += "+2" if outcome == "crit" else ""

        # Base rewards (always applied)
        base_rewards = {
            "combat":  {"player.reputation.inc": "enemies_defeated"},
            "stealth": {"player.inventory.add": f"shadow_ward{bonus}"},
            "social":  {"player.reputation.inc": "trust_gained"},
            "exploration": {f"player.inventory.add": f"discovery{bonus}"},
            "item":    {f"player.inventory.add": "scrap_metal"},
        }

        # Bonus effects for crit/fresh (stacked on top of base)
        bonus_effects = {
            "combat":  lambda: {"player.stats.str_bonus.inc": 1} if outcome in ("crit", "crit_fresh") else {},
            "stealth": lambda: {"player.inventory.add": f"shadow_ward_bonus{bonus}"} if bonus else {},
            "social":  lambda: {"player.reputation.inc": "ally_found"} if bonus else {},
            "exploration": lambda: {f"player.inventory.add": f"clue{bonus}"} if bonus else {},
            "item":    lambda: {f"player.inventory.add": "fine_tool"} if bonus else {},
        }

        result = dict(base_rewards.get(action_type, {"player.reputation.inc": "noted_by_village"}))

        # Stack on bonus effects
        extra = bonus_effects.get(action_type, lambda: {})()
        result.update(extra)

        return result

    @staticmethod
    def _on_failure(outcome_level: str, action_type: str) -> Dict[str, Any]:
        """Effects when the target DC is missed (partial or full failure).

        Partial outcomes give a real benefit (not trivial collectibles).
        Full failures apply targeted consequences — no arbitrary inventory deletion.
        """
        if outcome_level == "partial":
            # Partial — meaningful rewards, not junk items. These are tangible progress markers.
            partial_rewards = {
                "combat":  {"player.stats.dex_bonus.inc": 1},           # near-miss combat training
                "stealth": {"player.inventory.add": "dark_cloak"},       # stealthy gear from the attempt
                "social":  {"player.reputation.inc": "conversation_started"},
                "exploration": {"player.inventory.add": "ancient_map"},   # real navigational aid
                "item":    {"player.stats.int_bonus.inc": 1},            # crafting insight gained
            }
            return partial_rewards.get(action_type, {"player.reputation.inc": "noted_by_village"})
        else:
            # Full failure — targeted consequences (no arbitrary inventory deletion).
            penalty_rules = {
                "combat":  lambda: {"player.stats.con_penalty.inc": -1},       # injury reduces con temporarily
                "stealth": lambda: {"player.reputation.inc": "suspicion_raised"},     # now tracked as rep threshold
                "social":  lambda: {"player.reputation.inc": "offended_officer"},     # now tracked as rep threshold
                "exploration": lambda: {},   # exploration never penalizes — you simply don't find anything
                "item":    lambda: {"player.inventory.add": "broken_tool"},   # lose the tool, get a broken one back
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
        """Lightweight snapshot for DM choices — location, turn, and story context.

        The `story_events` field is injected by the game loop (via
        ``StoryMemory.format_summary``) so the LLM can reference what happened
        recently when generating choices.  Inventory/stats/reputation are
        intentionally omitted to keep output compact.
        """
        return {
            "location": self.world.current_location,
            "turn": self.world.turn_count,
            "story_events": "",  # populated by game loop via format_summary()
        }
