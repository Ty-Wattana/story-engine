"""
Narrative Engine -- polish, scene setting, and story memory.

Supplies Phase 5's game loop with:
  1) Beautiful Rich-formatted outcome panels
  2) LLM-driven scene descriptions (location/atmosphere)
  3) Short-term memory for continuity in DM narration
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Scene description prompt — loaded from prompts/
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


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
        "Turn %d: %s attempted '%s' -- outcome: %s"
        % (e.turn, e.player_name, e.intent, e.outcome_level.replace("_", " "))
        for e in recent_events[-5:]
    )

    context_text = (
        "Location: [%s]\n\n"
        "Recent events:\n%s\n\n"
        "Describe the scene the player will encounter next."
        % (location, events_block or "(none yet)", location)
    )

    try:
        return llm_prompt_fn(SCENE_SYSTEM_PROMPT + "\n\nScene description for "
                             "[%s]:\n%s" % (location, context_text))
    except Exception:
        # Fallback inline generator when LLM is unavailable
        return _fallback_scene_description(location)


def _fallback_scene_description(location: str) -> str:
    """Simple procedural scene generator -- avoids an empty prompt if the LLM fails."""
    locations = {
        "void": (
            "Shadows cling to every surface. The air tastes of ash and silence.\n"
            "An old path forks ahead -- left into whispering dark, right along a crumbling ledge."
        ),
        "starting_village": (
            "Flickering lanterns cast long shadows over cobblestone streets. Smoke curls "
            "from hearth chimneys overhead.\n"
            "A tavern door stands slightly ajar with muffled laughter spilling through."
        ),
    }
    key = location.lower().replace(" ", "_")
    return locations.get(key, "The path before %s stretches ahead, uncertain and quiet." % location)


# ---------------------------------------------------------------------------
# Outcome Panel Renderer -- polished Rich output
# ---------------------------------------------------------------------------

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box

console = Console()


def _score_color(score: int, dc: int) -> str:
    """Pick a Rich text colour string for the final score."""
    if score >= dc * 2:
        return "bold magenta"
    elif score >= dc:
        return "green"
    else:
        return "red"


def _outcome_label(outcome: str, success: bool, score: int, dc: int) -> Text:
    """Create a Rich-Text string with the outcome label + color."""
    if outcome == "crit" and not success:
        return Text("CRIT FRESH", style="bold green")
    elif not success:
        return Text("FAILURE", style="red dim")
    text_parts = {
        "success": ("SUCCESS", "green"),
        "partial": ("PARTIAL", "yellow"),
        "crit_fresh": ("CRIT FRESH!", "bold green"),
    }
    label, color = text_parts.get(outcome, ("SUCCESS", "white"))
    return Text(label, style=color)


def build_outcome_panel(result: Dict[str, Any]) -> Panel:
    """Build a Rich-formatted outcome panel for the game loop to print directly."""

    lines = []

    # Action header
    intent = result.get("intent", "?")
    verb = result.get("verb", "?")
    action_type = result.get("action_type", "?")
    target = result.get("target_entity", None)
    lines.append("[bold]Action:[/] %s | %s (%s)" % (intent, verb, action_type))
    if target:
        lines.append("  Target: [cyan]%s[/cyan]" % target)

    # Roll display -- colour-coded final score bar
    dc = result.get("target_dc", 0)
    roll = result.get("dice_roll", 0)
    mod = result.get("modifier", 0)
    score = result.get("final_score", 0)

    # Roll text with inline colours
    roll_text = Text()
    roll_text.append("Raw: %s" % str(roll), style="bold yellow")
    sign = "+" if mod >= 0 else ""
    roll_text.append("%s%d = " % (sign, mod), style="bold cyan")
    roll_text.append("Score: [%d]" % score, style=_score_color(score, dc))

    lines.append("[dim]Roll:[/] %s" % roll_text)

    # Outcome level with coloured label
    outcome = result["outcome_level"]
    text_result = _outcome_label(outcome, result.get("success", False), score, dc)

    lines.append("Outcome: [%s]" % text_result)

    # Effects summary — supports both legacy dict and new list-of-OutcomeEffect formats
    raw_effects = result.get("effects", [])
    if isinstance(raw_effects, dict):
        effects = raw_effects  # legacy: mechanical_effect stored here as dict
    else:
        effects = {e["key"]: e["value"] for e in raw_effects if isinstance(e, dict)} if isinstance(raw_effects, list) else {}

    if effects:
        lines.append("")
        lines.append("[bold]Effects:[/]")
        for k, v in effects.items():
            lines.append("  [cyan]%s[/cyan] -> %s" % (k, v))

    # Separator bar visual
    sep = "=" * 50
    lines.append(sep)

    return Panel(
        "\n".join(lines),
        title="[bold white]Result",
        border_style="blue",
    )


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
