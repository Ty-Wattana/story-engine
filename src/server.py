"""FastAPI server — stateless LLM + deterministic engine = HTTP API.

All game-logic lives in src.state; the LLM is a pure text-parser / flavor-generator.

Run with:  uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.state import StateManager, Player, PlayerStats, WorldState
from src.database import (
    create_session,
    update_session,
    fetch_session,
    append_message,
    fetch_messages,
    save_slot_name,
    duplicate_session,
    delete_session_by_slot,
)
from src.schemas import CharacterProfile, ActionParseResult
from src.action_engine import resolve_action, apply_outcome_effects
from src.llm_client import LLMClient

# ---------------------------------------------------------------------------
# FastAPI application & I/O schemas
# ---------------------------------------------------------------------------

app = FastAPI(title="Neuro-Symbolic RPG", version="0.2.0")


class StartRequest(BaseModel):
    player_name: str = Field(min_length=1)
    backstory: str = Field(min_length=1)


class StartResponse(BaseModel):
    session_id: str
    narrative: str
    choices: list[str] = Field(default_factory=list, description="Suggested next actions for the player.")


class ActionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    player_input: str = Field(min_length=1, max_length=4096)


class ActionResponse(BaseModel):
    session_id: str
    narrative: str
    outcome: dict[str, Any]
    updated_player_state: dict[str, Any]
    updated_world_state: dict[str, Any]
    choices: list[str] = Field(default_factory=list, description="Suggested next actions for the player.")


class SaveRequest(BaseModel):
    session_id: str = Field(min_length=1)
    slot_name: str = Field(min_length=1, max_length=64)


class LoadRequest(BaseModel):
    slot_name: str = Field(min_length=1, max_length=64)


class LoadResponse(BaseModel):
    session_id: str
    narrative_context: list[str]
    player_state: dict[str, Any]
    world_state: dict[str, Any]
    choices: list[str] = Field(default_factory=list, description="Suggested next actions for the player.")


class DeleteRequest(BaseModel):
    slot_name: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dc_asdict(obj):
    """Convert a dataclass to dict for API response (avoids leaking internal fields like _bonus_cache)."""
    return dataclasses.asdict(obj)


def _load_player(pj) -> Player:
    """Reconstruct a Player dataclass from JSON string or dict.

    Accepts either a JSON string (from DB) or a dict (from direct API).
    Handles both ``model_dump()``-style keys ("stats") and legacy snapshot-style keys ("stat.s").
    """
    if isinstance(pj, str):
        pdata = json.loads(pj)
    else:
        pdata = dict(pj)  # shallow copy — preserve original

    stats_raw = pdata.pop("stats", {})
    if isinstance(stats_raw, str):
        stats_raw = json.loads(stats_raw)
    stats_kwargs = {
        s: stats_raw.get(s, 10)
        for s in ["strength", "dexterity", "intelligence", "wisdom", "constitution", "charisma"]
    }
    return Player(
        name=pdata.pop("name", pdata.pop("player_name", "Unknown")),
        faction=pdata.pop("faction", "Unknown"),
        motivation=pdata.pop("motivation", "Survive"),
        goal=pdata.pop("goal", "None"),
        inventory=pdata.pop("inventory", []),
        reputation=pdata.pop("reputation", {}),
        stats=PlayerStats(**stats_kwargs),
    )


def _load_world(wj) -> WorldState:
    """Reconstruct a WorldState from JSON text or dict."""
    if isinstance(wj, str):
        wdata = json.loads(wj)
    else:
        wdata = dict(wj)
    return WorldState(
        current_location=wdata.get("current_location", "The Void"),
        active_npcs=wdata.get("active_npcs", []),
        turn_count=wdata.get("turn_count", 0),
    )


# ---------------------------------------------------------------------------
# Endpoints: Game
# ---------------------------------------------------------------------------

@app.post("/game/start", response_model=StartResponse)
async def start_game(req: StartRequest):
    """Start a new campaign from backstory text.

    Flow: LLM parses CharacterProfile (Pydantic validated) -> deterministic
    Player/WorldState created by engine -> session persisted to SQLite ->
    LLM generates intro narrative -> history message logged.
    """
    client = LLMClient()

    # 1. Parse backstory -> CharacterProfile
    system_prompt = client._load_system_prompt("prompts/character_creation.md")
    parsed = client.generate_structured(
        system_prompt=system_prompt,
        user_prompt=req.backstory,
        schema=CharacterProfile,
    )

    # 2. Build initial state (engine-driven, NOT LLM-predicted — golden rule)
    player = Player(
        name=req.player_name,
        faction=parsed.origin_faction,
        motivation=parsed.motivation,
        goal=parsed.goal,
        stats=PlayerStats(),
    )
    world = WorldState(current_location="The Void")

    # 4. Intro narrative (LLM flavor only — never modifies state)
    intro_system = client._load_system_prompt("prompts/intro_scene.md")
    narrative = client.generate_flavor_text(
        context=f"Player: {player.name}, Faction: {player.faction}",
        instruction="Write an opening scene for the adventure.",
    )

    # 5. Generate initial choices (lightweight context only)
    choices = client.generate_choices({
        "player_name": player.name,
        "faction": player.faction,
        "location": world.current_location,
        "outcome": "started",
        "narrative": narrative,
    })

    # 6. Persist session + intro message (single call)
    sid = create_session(
        player_state=player,
        world_state=world,
        last_choices=choices,
    )
    append_message(sid, "system", f"[Game Started — Backstory parsed]\n{narrative}", save_slot=None)

    return StartResponse(session_id=sid, narrative=narrative, choices=choices)


@app.post("/game/action", response_model=ActionResponse)
async def game_action(req: ActionRequest):
    """Process a player action through the full neuro-symbolic pipeline."""
    client = LLMClient()

    # 1. Load session state from SQLite
    sid = req.session_id
    session_data = fetch_session(session_id=sid)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, f"No session found for '{sid}'")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    # 2. Load last 5 messages for conversational continuity (prevents LLM context overflow)
    recent_messages = fetch_messages(sid, limit=5)

    # 3. Use symbolic engine (deterministic, NEVER the LLM) to parse intent + resolve mechanics.
    snapshot_for_llm = {"location": world.current_location, "turn": world.turn_count}

    parse_result: ActionParseResult | None = None
    try:
        parse_result = client.generate_action_result(req.player_input, snapshot_for_llm)
    except Exception as exc:
        raise HTTPException(502, f"Action intent parsing failed: {exc}")

    # ---- Symbolic action resolution (STRICT golden rule — engine only) ----
    target_stat_name = (
        parse_result.modifiers.target_stat if parse_result.modifiers and parse_result.modifiers.target_stat  # type: ignore[union-attr]
        else "dexterity"
    )
    stat_value = getattr(player.stats, target_stat_name, 10)

    prof_bonus = StateManager(player, world).proficiency

    action_type = parse_result.action_type
    if hasattr(action_type, "value"):
        action_type = action_type.value  # pydantic Literal[str] may be an Enum

    resolved = resolve_action(
        action_type=action_type,
        stat_name=target_stat_name,
        stat_value=stat_value,
        proficiency=prof_bonus,
        advantage=parse_result.modifiers.advantage if parse_result.modifiers else "none",
        world_context=world.current_location,
    )

    state_manager = StateManager(player, world)
    engine_effects = apply_outcome_effects(
        state=state_manager,
        outcome_level=resolved["outcome_level"],
        action_type=action_type,
    )

    world.advance_turn()

    # ---- LLM generates outcome narrative (stateless, read-only role) ----
    narrative_prompt = (
        f"Outcome: {resolved['outcome_level']} (success={resolved['success']}).\n"
        f"Action: {parse_result.intent}\n"
        f"d20 roll: dice={resolved['dice_roll']} + modifier({stat_value} +{prof_bonus}) = {resolved['final_score']} vs DC={resolved['target_dc']}\n"
        f"Location: {world.current_location}, Turn: {world.turn_count}.\n"
        f"Previous messages (max 5):\n"
    )
    for msg in recent_messages[:5]:
        role_label = "You" if msg["role"] == "user" else ("DM" if msg["role"] == "system"
                                                             else "Assistant")
        narrative_prompt += f"<{role_label}>: {msg['content']}\n"

    try:
        narrative = client.generate_flavor_text(
            context=narrative_prompt,
            instruction="Write the outcome of this encounter.",
        )
    except Exception as narr_exc:
        narrative = (
            f"**Engine Result**: [{resolved['outcome_level'].upper()}] "
            f"You {parse_result.intent}. Roll: {resolved['dice_roll']}, "
            f"Score: {resolved['final_score']}, DC: {resolved['target_dc']}."
        )

    # 5. Generate choices for next turn
    action_snapshot = {
        "location": world.current_location,
        "turn": world.turn_count,
        "player_name": player.name,
        "faction": player.faction,
        "motivation": player.motivation,
        "outcome": resolved["outcome_level"],
        "story_events": narrative[-500:],  # recent context for choices
    }
    choices = client.generate_choices(action_snapshot)

    # 6. Persist to SQLite (state mutations come from engine, never LLM)
    update_session(
        session_id=sid,
        player_state=player,
        world_state=world,
        last_choices=choices,
    )
    append_message(sid, "user", req.player_input)
    append_message(sid, "assistant", narrative)

    return ActionResponse(
        session_id=sid,
        narrative=narrative,
        outcome={
            "dice_roll": resolved["dice_roll"],
            "modifier": resolved["modifier"],
            "final_score": resolved["final_score"],
            "target_dc": resolved["target_dc"],
            "outcome_level": resolved["outcome_level"],
            "success": resolved["success"],
            "effects_applied": engine_effects or {},
        },
        updated_player_state=_dc_asdict(player),
        updated_world_state=_dc_asdict(world),
        choices=choices,
    )


# ---------------------------------------------------------------------------
# Endpoints: System (save / load)
# ---------------------------------------------------------------------------

@app.post("/system/save", response_model=dict[str, str])
async def save_game(req: SaveRequest):
    """Assign a human-readable slot name to the given session."""
    try:
        save_slot_name(req.session_id, req.slot_name)
    except Exception as exc:
        raise HTTPException(500, f"Save failed: {exc}")
    return {"status": "ok", "session_id": req.session_id, "slot_name": req.slot_name}


@app.post("/system/load")
async def load_game(req: LoadRequest):
    """Continue play from a named save slot — returns the session's current state."""
    try:
        sid = duplicate_session(req.slot_name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Load failed: {exc}")

    session_data = fetch_session(session_id=sid)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, "Session data missing.")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    # Return last message as resume context (shows "where you left off")
    recent = fetch_messages(sid, limit=1)
    return LoadResponse(
        session_id=sid,
        narrative_context=[m["content"] for m in recent],
        player_state=_dc_asdict(player),
        world_state=_dc_asdict(world),
        choices=session_data.get("last_choices", []),
    )


@app.post("/system/delete")
async def delete_game(req: DeleteRequest):
    """Delete a named save session and its message history."""
    try:
        deleted = delete_session_by_slot(req.slot_name)
    except Exception as exc:
        raise HTTPException(500, f"Delete failed: {exc}")
    if not deleted:
        raise HTTPException(404, f"No session with slot '{req.slot_name}'")
    return {"status": "deleted", "slot_name": req.slot_name}


# ---------------------------------------------------------------------------
# Health-check (monitoring / smoke tests)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "running", "version": "0.2.0"}
