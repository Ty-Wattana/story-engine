from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal


# ---------------------------------------------------------------------------
# Character creation schemas (unchanged from original)
# ---------------------------------------------------------------------------

class CharacterProfile(BaseModel):
    origin_faction: str = Field(description="The faction, race, or group the character comes from.")
    motivation: str = Field(description="A one-word tag defining their core drive (e.g., Revenge, Wealth, Atonement).")
    goal: str = Field(description="Their specific, actionable objective.")


# ---------------------------------------------------------------------------
# Action input schemas — what the LLM extracts from free-text
# ---------------------------------------------------------------------------

class ActionModifiers(BaseModel):
    """Extra context about how the action is performed."""
    target_stat: Optional[str] = Field(None, description="Which player stat governs this action (e.g. 'strength', 'dexterity').")
    tool_used: Optional[str] = Field(None, description="Weapon, tool, or item being wielded for this action.")
    advantage: Literal["none", "advantage", "disadvantage"] = Field("none", description="advantage, disadvantage, or normal.")


class ActionParseResult(BaseModel):
    """Structured interpretation of user free-text input.

    The LLM only answers *what* the player tried — not what happens.
    Outcome effects are determined by the engine from deterministic rules.
    """

    intent: str = Field(description="A 1-3 word summary of the action.")
    target_entity: Optional[str] = Field(None, description="The NPC, item, or location targeted by the action.")
    is_combat: bool = Field(False, description="True if the action involves physical violence or hostile magic.")

    # Expanded fields (Phase 1 additions)
    action_type: Literal["combat", "stealth", "social", "exploration", "item"] = Field(
        description="The category this action falls under."
    )
    verb: str = Field(description="The dominant action verb extracted from the user input (e.g. 'sneak', 'attack', 'persuade').")
    modifiers: ActionModifiers = Field(
        default_factory=ActionModifiers,
        description="Additional mechanical context for resolving this action."
    )
    raw_input: str = Field(description="The exact original text the player typed or selected.")


# ---------------------------------------------------------------------------
# Action resolution schemas — what the engine produces per outcome level
# ---------------------------------------------------------------------------

OutcomeLevel = Literal["crit_fresh", "failure", "partial", "success", "crit"]


class OutcomeEffect(BaseModel):
    """A single state-mutation effect applied by the engine."""
    key: str = Field(description="Effect key in entity.field.operator form. Examples: 'player.inventory.add', 'world.health.decrement'.")
    value: Any = Field(description="The value to apply.")


class ActionResult(BaseModel):
    """Outcome of resolving an action through the action engine."""

    intent: str = Field(description="What the player attempted.")
    verb: str = Field(description="The dominant action verb.")
    target_entity: Optional[str] = Field(None, description="Target of the action (None if none).")

    dice_roll: int = Field(description="The raw d20 roll value.")
    modifier: int = Field(description="Stat + proficiency + tool modifiers applied to the roll.")
    final_score: int = Field(description="dice_roll + modifier — compared against the Difficulty Class.")
    target_dc: int = Field(description="The Difficulty Class this action needed to meet or exceed.")
    advantage: str = Field(description="advantage / disadvantage / none context for this roll.")

    outcome_level: OutcomeLevel = Field(
        description=(
            "crit_fresh — rolled a 20 (automatically succeed, bonus effect)\n"
            "failure  — rolled a 1  (automatically fail, penalty applied)\n"
            "partial  — within 5 of meeting the DC\n"
            "success  — met or exceeded the DC\n"
            "crit     — exceeded the DC by 10 or more"
        )
    )

    success: bool = Field(description="Whether the target DC was met or exceeded.")

    effects: list[OutcomeEffect] = Field(
        default_factory=list,
        description="Engine-computed state changes applied to the world. Empty only when nothing changes."
    )

    narrative_prompt: str = Field(
        description="Plain-text instructions for the narrative engine to generate flavor text."
    )
