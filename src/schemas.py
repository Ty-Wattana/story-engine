from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal, Union


# ---------------------------------------------------------------------------
# Character creation schemas (unchanged from original)
# ---------------------------------------------------------------------------

class CharacterProfile(BaseModel):
    origin_faction: str = Field(description="The faction, race, or group the character comes from.")
    motivation: str = Field(description="A one-word tag defining their core drive (e.g., Revenge, Wealth, Atonement).")
    goal: str = Field(description="Their specific, actionable objective.")


# ---------------------------------------------------------------------------
# Action input schemas  –  what the LLM extracts from free-text
# ---------------------------------------------------------------------------

class ActionModifiers(BaseModel):
    """Extra context about how the action is performed."""
    target_stat: Optional[str] = Field(None, description="Which player stat governs this action (e.g. 'strength', 'dexterity').")
    tool_used: Optional[str] = Field(None, description="Weapon, tool, or item being wielded for this action.")
    advantage: Literal["none", "advantage", "disadvantage"] = Field("none", description="advantage, disadvantage, or normal.")


class ActionParseResult(BaseModel):
    """Structured interpretation of user free-text input."""

    intent: str = Field(description="A 1-3 word summary of the player's attempted action.")
    target_entity: Optional[str] = Field(None, description="The NPC, item, or location targeted by the action.")
    is_combat: bool = Field(False, description="True if the action involves physical violence or hostile magic.")

    # Expanded fields (Phase 1 additions)
    action_type: Literal["combat", "stealth", "social", "exploration", "item"] = Field(
        description="The category this action falls under."
    )
    verb: str = Field(description="The dominant action verb extracted from the user input (e.g. 'sneak', 'attack', ' persuade').")
    modifiers: ActionModifiers = Field(
        default_factory=ActionModifiers,
        description="Additional mechanical context for resolving this action."
    )
    raw_input: str = Field(description="The exact original text the player typed or selected.")

    # Aliases so legacy code that reads `is_combat` from parsed JSON still works
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Action resolution schemas  –  what the action engine produces
# ---------------------------------------------------------------------------

class ActionResult(BaseModel):
    """Outcome of resolving an action through the action engine."""

    success: bool = Field(description="Whether the target DC was met or exceeded.")
    dice_roll: int = Field(description="The raw d20 roll value.")
    modifier: int = Field(description="Stat + proficiency + misc modifiers applied to the roll.")
    final_score: int = Field(description="dice_roll + modifier — compared against the Difficulty Class.")
    target_dc: int = Field(description="The Difficulty Class this action needed to meet or exceed.")
    outcome_level: Literal["crit_fresh", "failure", "partial", "success", "crit"] = Field(
        description=(
            "crit_fresh — rolled a 20 (automatically succeed, bonus effect)\n"
            "failure  — rolled a 1  (automatically fail, penalty applied)\n"
            "partial  — within 5 of meeting the DC\n"
            "success  — met or exceeded the DC\n"
            "crit     — exceeded the DC by 10 or more"
        )
    )
    mechanical_effect: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs describing state mutation (e.g. {'inventory.add': 'iron_key', 'reputation.Iron Circle': +1})."
    )
    narrative_prompt: str = Field(
        description="Plain-text instructions for the narrative engine to generate flavor text."
    )
