from dataclasses import dataclass, field
from typing import Dict, List

@dataclass
class Player:
    name: str
    faction: str = "Unknown"
    motivation: str = "Survive"
    goal: str = "None"
    inventory: List[str] = field(default_factory=list)
    reputation: Dict[str, int] = field(default_factory=dict)

@dataclass
class WorldState:
    current_location: str = "The Void"
    active_npcs: List[str] = field(default_factory=list)
    turn_count: int = 0

    def advance_turn(self):
        self.turn_count += 1