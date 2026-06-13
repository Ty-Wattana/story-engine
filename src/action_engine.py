"""
Action Engine — the mechanical heart of the game loop.

Handles all non-narrative game math:
  1) Dice rolling (d20 with advantage/disadvantage & stat/proficiency mods)
  2) DC evaluation -> outcome_level
  3) Effect application -> deterministic state mutation via StateManager

Design principle: effects are computed by the engine, never predicted by LLM.
"""
from __future__ import annotations

import random
from typing import Any, Dict, Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_D20 = 20
PARTIAL_RANGE = 5            # "within" margin (exclusive): final_score - target_dc > -PARTIAL_RANGE -> partial


# ---------------------------------------------------------------------------
# Dice System
# ---------------------------------------------------------------------------

class DiceSystem:
    """Pure die-rolling logic."""

    @staticmethod
    def roll_d20() -> int:
        return random.randint(1, MAX_D20)

    @classmethod
    def roll_attack(
        cls,
        advantage: Literal["none", "advantage", "disadvantage"] = "none",
    ) -> tuple[int, int]:
        """Roll a d20 with optional advantage/disadvantage.

        Returns (raw_d20, pre_stat_score) — stat/proficiency modifiers are applied
        by the caller *after* this returns so they can display them independently.
        """
        if advantage == "advantage":
            raw = max(cls.roll_d20(), cls.roll_d20())
        elif advantage == "disadvantage":
            raw = min(cls.roll_d20(), cls.roll_d20())
        else:
            raw = cls.roll_d20()

        return raw, raw


# ---------------------------------------------------------------------------
# DC Resolver — maps action_type -> base DC
# ---------------------------------------------------------------------------

BASE_DC: Dict[str, int] = {
    "combat":      12,
    "stealth":     14,        # overridden to 15 in SkillResolver if context indicates sleeping target
    "social":      10,
    "exploration": 12,
    "item":        12,
}


class SkillResolver:
    """Maps action_type -> skill check and determines the DC."""

    @staticmethod
    def determine_dc(action_type: str, context: str = "") -> int:
        dc = BASE_DC.get(action_type, BASE_DC["item"])
        if action_type == "stealth" and "sleeping" in context.lower():
            dc = 15
        return dc


# ---------------------------------------------------------------------------
# Outcome Evaluator
# ---------------------------------------------------------------------------

OutcomeLevel = Literal["crit_fresh", "failure", "partial", "success", "crit"]


def evaluate_outcome(
    final_score: int,
    target_dc: int,
    raw_roll: int | None = None,
) -> dict[str, Any]:
    """Map (raw_roll, final_score, DC) -> outcome_level + success flag.

    Rules (D&D 5e style):
      - Natural 20 -> crit_fresh (auto-succeed, bonus effect)
      - Natural 1  -> failure   (auto-fail, penalty applied)
      - final_score >= target_dc and margin >= 10 -> crit
      - final_score >= target_dc                    -> success
      - final_score < target_dc and margin <= 5    -> partial
      - otherwise                                  -> failure
    """

    # Auto crit / auto fail on raw roll before any modifier
    if raw_roll == MAX_D20:
        return {"outcome_level": "crit_fresh", "success": True}
    if raw_roll == 1:
        return {"outcome_level": "failure", "success": False}

    margin = final_score - target_dc

    if margin >= 10:
        level: OutcomeLevel = "crit"
    elif margin >= 0:
        level = "success"
    elif margin > -PARTIAL_RANGE:
        level = "partial"
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
    stat_name: str | None = None,
    tool_modifier: int = 0,
    advantage: Literal["none", "advantage", "disadvantage"] = "none",
    proficiency: int = 2,
    world_context: str = "",
) -> dict[str, Any]:
    """Public API — call from the game loop to fully resolve an action.

    Args:
        action_type:   one of combat/stealth/social/exploration/item
        stat_name:     player stats field ('strength', 'dexterity', etc.)
        tool_modifier: bonus from wielded tool/weapon (0 if none)
        advantage:     context-derived advantage/disadvantage for this roll
        proficiency:   current proficiency bonus (from WorldState.turn_count)
        world_context: current location string for DC calculation

    Returns a dict shaped like ActionResult. Effects are *not* computed here —
    StateManager.apply_outcome_effects() handles that based on outcome_level + action_type.
    """
    dc = SkillResolver.determine_dc(action_type, world_context)

    # Roll
    raw_roll, pre_score = DiceSystem.roll_attack(advantage)

    # Total modifier chain: stat_bonus + proficiency + tool
    modifier = 0
    if stat_name:
        # Placeholder for now — caller passes stat value directly
        pass
    modifier += proficiency
    modifier += tool_modifier

    final_score = pre_score + modifier

    outcome = evaluate_outcome(final_score, dc, raw_roll)

    return {
        "dice_roll":      raw_roll,
        "pre_score":      pre_score,
        "modifier":       modifier,
        "stat_bonus":     0,          # caller provides stat via action_type mapping
        "proficiency":    proficiency,
        "tool_modifier":  tool_modifier,
        "advantage":      advantage,
        "final_score":    final_score,
        "target_dc":      dc,
        **outcome,
        "mechanical_effect": {},       # legacy key kept for compatibility
        "narrative_prompt": "",         # StoryFormatter fills this later
    }


def apply_outcome_effects(
    state: Any,              # StateManager passed in by caller
    outcome_level: str,
    action_type: str,
) -> dict[str, Any]:
    """Apply engine-determined effects based on outcome + action type.

    This is the *single* point where effects are computed — never LLM-predicted.
    Returns a summary dict of what actually changed (for logging/display).
    """
    return state.apply_outcome_effects(outcome_level, action_type)
