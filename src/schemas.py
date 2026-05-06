from pydantic import BaseModel, Field
from typing import List, Optional

class CharacterProfile(BaseModel):
    origin_faction: str = Field(description="The faction, race, or group the character comes from.")
    motivation: str = Field(description="A one-word tag defining their core drive (e.g., Revenge, Wealth, Atonement).")
    goal: str = Field(description="Their specific, actionable objective.")

class ActionParseResult(BaseModel):
    intent: str = Field(description="A 1-3 word summary of the player's attempted action.")
    target_entity: Optional[str] = Field(description="The NPC, item, or location targeted by the action.")
    is_combat: bool = Field(description="True if the action involves physical violence or hostile magic.")