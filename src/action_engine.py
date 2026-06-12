"""
Action Engine — the mechanical heart of the game loop.

Handles all non-narrative game math:
  1) Dice rolling (d20 with advantage/disadvantage & stat mods)
  2) DC evaluation → outcome_level
  3) Effect application → in-place state mutation
"""
from __future__ import annotations

import random
import operator
from typing import Dict, Any, Literal, Union, Callable
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_D20 = 20
CRIT_THRESHOLD = 18          # final_score (not roll) exceeded by ≥10?
PARTIAL_RANGE = 5            # within this many points of DC


# ---------------------------------------------------------------------------
# Dice System
# ---------------------------------------------------------------------------

class DiceSystem:
    """Pure die-rolling logic."""

    @staticmethod
    def roll_d20() -> int:
        return random.randint(1, MAX_D20)

    @classmethod
    def roll_attack(cls) -> tuple[int, int]:
        """Return (roll_value, final_score_after_mod).

        Handles advantage/disadvantage and stat modifiers.

        Returns tuple of (raw_d20, final_score) — modifier is returned
        alongside so the caller can populate ActionResult fields exactly.
        """
        base_roll = cls.roll_d20()
        adv = random.choice(["none", "disadvantage"])  # TODO: wired into context later

        if adv == "disadvantage":
            roll_again = cls.roll_d20()
            raw = min(base_roll, roll_again)
        else:
            raw = base_roll

        modifier = DiceSystem._compute_modifier(adv)
        return raw, raw + modifier

    @staticmethod
    def _compute_modifier(adv: str) -> int:
        # Skeleton — real stat bonuses come from PlayerStats in Phase 3.
        # For now neutral so we don't accidentally bias outcomes.
        return 0


# ---------------------------------------------------------------------------
# Skill Resolver
# ---------------------------------------------------------------------------

ACTION_DC_MAP: Dict[str, Callable[[str], int]] = {
    "combat":     lambda _: 12,
    "stealth":    lambda ctx: 14 if "sleeping" in ctx.lower() else 15,
    "social":     lambda _: 10,  # negotiated later
    "exploration":lambda _: 12,
    "item":       lambda _: 12,
}


class SkillResolver:
    """Maps action_type → skill check and determines the DC."""

    @staticmethod
    def determine_dc(action_type: str, context: str = "") -> int:
        return ACTION_DC_MAP.get(action_type, lambda _: 12)(context)

    @classmethod
    def roll(
        cls,
        action_type: str,
        target_stat: str | None = None,
        context: str = "",
    ) -> tuple[int, int]:
        """Delegate to DiceSystem and return (raw_roll, final_score)."""
        return DiceSystem.roll_attack()


# ---------------------------------------------------------------------------
# Effect Applier  –  mutations via string-key → operator mapping
# ---------------------------------------------------------------------------

def _inc_rep(cur, val):
    """Helper for reputation increments."""
    d = cur or {}
    key = str(val) + "_by"
    return {**d, **{key: d.get(key, 0) + 1}}


_EFFECT_MAP = {
    "inventory.add":     lambda cur, val: cur + [val] if isinstance(cur, list) else None,
    "inventory.remove":  lambda cur, val: [x for x in cur if x != val] if isinstance(cur, list) else None,
    "reputation.inc":    _inc_rep,
}


def _set_nested(d: dict, key_path: str, value: Any) -> dict | None:
    """Set a nested dict value given dot-separated key path."""
    parts = key_path.rsplit(".", 1)
    if len(parts) != 2:
        return None
    outer, inner = parts
    d.setdefault(outer, {})[inner] = value
    return d


def apply_mechanical_effects(
    state: Any,              # Player + WorldState passed in via caller
    effects: Dict[str, Any],
) -> dict:
    """Mutate game state in place by interpreting effect keys.

    Each key is one of:
      inventory.add <val>   – append to player.inventory
      inventory.remove <v>  – remove first match from player.inventory
      reputation.inc <f>   – increment rep counter for faction f

    Returns a summary dict of what actually changed (for logging).
    """
    applied: Dict[str, Any] = {}

    for key_value_str, raw_val in effects.items():
        parts = key_value_str.split(None, 1)
        key = parts[0]
        val = parts[1] if len(parts) > 1 else raw_val

        # inventory additions/removals happen on *state* directly
        if key == "inventory.add":
            getattr(state, "inventory", []).append(val)
            applied[key] = val
        elif key == "inventory.remove":
            inv = getattr(state, "inventory", [])
            if val in inv:
                inv.remove(val)
            applied[key] = val
        else:
            # fallback: record but skip unknown effects silently
            applied[f"unknown:{key}"] = val

    return applied


# ---------------------------------------------------------------------------
# Outcome Evaluator
# ---------------------------------------------------------------------------

OutcomeLevel = Literal["crit_fresh", "failure", "partial", "success", "crit"]


def evaluate_outcome(
    final_score: int,
    target_dc: int,
    raw_roll: int | None = None,
) -> dict[str, Any]:
    """Map (raw_roll, final_score, DC) → outcome_level + success flag."""

    if final_score >= CRIT_THRESHOLD and final_score >= target_dc * 2:
        level: OutcomeLevel = "crit"
    elif final_score == MAX_D20 and not raw_roll == 1:
        level = "crit_fresh"
    elif final_score - target_dc < PARTIAL_RANGE and final_score < target_dc:
        level = "partial"
    elif final_score >= target_dc:
        level = "success"
    else:
        level = "failure"

    return {
        "outcome_level": level,
        "success": level in ("crit_fresh", "success", "crit"),
    }


# ---------------------------------------------------------------------------
# Unified Action Resolver (the public entry point for the game loop)
# ---------------------------------------------------------------------------

def resolve_action(
    action_type: str,
    modifiers_info: dict[str, Any] | None = None,
    world_context: str = "",
) -> dict[str, Any]:
    """Public API — call from the game loop to fully resolve an action.

    Returns a dict shaped like ActionResult (compatible for validation).
    """
    mod = modifiers_info or {}
    dc = SkillResolver.determine_dc(action_type, world_context)

    raw_roll, final_score = SkillResolver.roll(
        action_type,
        target_stat=mod.get("target_stat"),
        context=world_context,
    )

    outcome = evaluate_outcome(final_score, dc, raw_roll)

    return {
        "dice_roll":   raw_roll,
        "modifier":    final_score - raw_roll,
        "final_score": final_score,
        "target_dc":   dc,
        **outcome,
        "mechanical_effect": {},  # Phase 3: populated by EffectApplier on actual state mutation
        "narrative_prompt": "",  # Phase 6: StoryFormatter fills this
    }
