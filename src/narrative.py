"""
Narrative Engine -- polish, scene setting, and story memory.

Supplies the game loop with:
  1) LLM-driven scene descriptions (location/atmosphere)
  2) Short-term story memory for continuity in DM narration
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from src._utils import load_prompt as _load_prompt

# ---------------------------------------------------------------------------
# Scene description prompt — loaded from prompts/
# ---------------------------------------------------------------------------

SCENE_SYSTEM_PROMPT = _load_prompt("scene_description.md")


# ---------------------------------------------------------------------------
# Event types the narrative engine tracks internally
# ---------------------------------------------------------------------------

@dataclass
class StoryEvent:
    """One in-game event (an action + outcome) for continuity."""
    turn: int
    player_name: str
    intent: str
    action_type: str
    target_entity: str | None
    dice_roll: int
    final_score: int
    target_dc: int
    success: bool
    outcome_level: str
    narrative_summary: str  # brief 1-sentence summary
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Scene Description Generator (LLM-driven)
# ---------------------------------------------------------------------------

def generate_scene_description(
    location: str,
    recent_events: List[StoryEvent],
    llm_prompt_fn,
) -> str:
    """Generate a flavor-text scene description via the LLM.

    Args:
        location: current world location name (capitalized).
        recent_events: last ~5 StoryEvents for context continuity.
        llm_prompt_fn: callable(context_str) -> str that sends to the Ollama client.
    """
    events_block = "\n".join(
        f"Turn {e.turn}: {e.player_name} attempted '{e.intent}' -- outcome: {e.outcome_level.replace('_', ' ')}"
        for e in recent_events[-5:]
    )

    context_text = (
        f"Location: [{location}]\n\n"
        f"Recent events:\n{events_block or '(none yet)'}\n\n"
        "Describe the scene the player will encounter next."
    )

    try:
        return llm_prompt_fn(SCENE_SYSTEM_PROMPT + "\n\nScene description for "
                             f"[{location}]:\n{context_text}")
    except Exception:
        # Fallback inline generator when LLM is unavailable
        return _fallback_scene_description(location)


def _fallback_scene_description(location: str) -> str:
    """Simple procedural scene generator -- avoids an empty prompt if the LLM fails."""
    return f"The path before {location} stretches ahead, uncertain and quiet."


# ---------------------------------------------------------------------------
# Story Memory -- short-term event log across turns
# ---------------------------------------------------------------------------

class StoryMemory:
    """Keeps track of recent in-game events for context.

    Implements a fixed-length rolling buffer (default 20).
    """

    def __init__(self, max_events=20):
        self._events = deque(maxlen=max_events)

    def add_event(self, event: StoryEvent) -> None:
        """Record an in-game event."""
        if isinstance(event, dict):
            e = StoryEvent(**event)
        else:
            e = event
        self._events.append(e)

    def get_recent(self, n=None):
        """Return last N events (or all). Returns list of dicts."""
        seq = list(self._events)[-n:] if n else list(self._events)
        return [e.__dict__ for e in seq]

    def clear(self) -> None:
        self._events.clear()

    def format_summary(self, n: int = 5) -> str:
        """Return a lightweight text block of recent events for prompt injection.

        Returns an empty string when there are no events so callers can
        always inject verbatim without conditional logic.
        """
        events = list(self._events)[-n:]
        if not events:
            return ""
        lines = ["# RECENT STORY"]
        for e in events:
            label = "no outcome" if e.outcome_level == "failure" else e.outcome_level.replace("_", " ").title()
            target = f" → {e.target_entity}" if e.target_entity else ""
            lines.append(f"- Turn {e.turn}: {e.player_name} {e.intent}{target} ({label})")
        return "\n".join(lines)
