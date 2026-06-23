"""Pydantic schemas for the Event-Driven (CRPG) architecture.

Old MUD-style schemas (ActionRequest, ActionParseResult, etc.) removed.
New schemas model discrete game events: dialogue, object interaction, combat resolution.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Legacy helpers (still used by /game/start bootstrap)
# ---------------------------------------------------------------------------

class ChoicesResponse(BaseModel):
    """LLM output for choice generation — kept for /game/start compatibility."""
    choices: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="before")
    @classmethod
    def coerce_array(cls, data: Any) -> Any:
        if isinstance(data, list):
            return {"choices": data}
        return data


# ---------------------------------------------------------------------------
# Character creation (unchanged — still used by /game/start)
# ---------------------------------------------------------------------------

class CharacterProfile(BaseModel):
    origin_faction: str = Field(description="The faction, race, or group the character comes from.")
    motivation: str = Field(description="A one-word tag defining their core drive (e.g., Revenge, Wealth, Atonement).")
    goal: str = Field(description="Their specific, actionable objective.")


# ---------------------------------------------------------------------------
# Event schemas — thick-client triggers
# ---------------------------------------------------------------------------

class DialogueRequest(BaseModel):
    session_id: str = Field(min_length=1)
    npc_name: str = Field(min_length=1, description="Which NPC is in this dialogue.")
    player_message: str = Field(default="", description="Player's reply. Empty if NPC initiates greeting.")


class DialogueResponse(BaseModel):
    session_id: str
    npc_response: str = Field(description="The NPC's full spoken response.")
    dialogue_choices: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Options presented to the player as follow-up replies.",
    )
    updated_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Sanitized player + world state snapshot for the client.",
    )


class InteractRequest(BaseModel):
    session_id: str = Field(min_length=1)
    target_object: str = Field(min_length=1, max_length=256, description="What the player is interacting with (e.g. 'Strange Monolith').")


class InteractResponse(BaseModel):
    session_id: str
    narrative_description: str = Field(description="Flavor text describing the interaction result.")
    updated_state: dict[str, Any] = Field(default_factory=dict)


class CombatResolvedRequest(BaseModel):
    session_id: str = Field(min_length=1)
    victor: str = Field(description="Who won — player name or faction string.")
    defeated_enemies: list[str] = Field(default_factory=list, description="Names of the defeated opponents.")


class CombatResolvedResponse(BaseModel):
    session_id: str
    narrative_summary: str = Field(description="Post-combat atmospheric summary.")
    updated_state: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# BG3 Handshake — Phase 1 intent classification
# ---------------------------------------------------------------------------

class IntentClassification(BaseModel):
    """Phase-1 parser output: tells the server whether to roll dice."""
    intent_type: str = Field(
        description="Action category: DIALOGUE, INTERACT, ATTACK, or other named type.",
    )
    requires_roll: bool = Field(
        description=(
            "True ONLY if the action involves risk, hidden information, or opposed NPC "
            "resistance. Mundane conversation and obvious observations must be False."
        ),
    )
    skill_required: str | None = Field(
        default=None,
        description="Skill name (e.g. 'Persuasion', 'Investigation'). None when requires_roll is False.",
    )
    action_summary: str = Field(
        description="One-sentence summary of what the player is attempting.",
    )
