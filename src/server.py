"""FastAPI server — event-driven (CRPG backend) HTTP API.

All game-logic lives in src.state; the LLM is a pure text-parser / flavor-generator.

Run with:  uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.state import Player, PlayerStats, WorldState
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
from src.schemas import (
    CharacterProfile,
    DialogueRequest,
    DialogueResponse,
    InteractRequest,
    InteractResponse,
    CombatResolvedRequest,
    CombatResolvedResponse,
)
from src.llm_client import LLMClient
from src.schemas import ChoicesResponse

# ---------------------------------------------------------------------------
# FastAPI application & legacy I/O schemas (keep /game/start for bootstrap)
# ---------------------------------------------------------------------------

app = FastAPI(title="Neuro-Symbolic RPG", version="0.3.0")


class StartRequest(BaseModel):
    player_name: str = Field(min_length=1)
    backstory: str = Field(min_length=1)


class StartResponse(BaseModel):
    session_id: str
    narrative: str
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
    """Convert a dataclass to dict, stripping `_`-prefixed (internal) keys."""
    d = dataclasses.asdict(obj)
    return _strip_private(d)

def _strip_private(d: dict) -> dict:
    """Recursively remove keys starting with '_' from a dict tree."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out[k] = _strip_private(v)
        else:
            out[k] = v
    return out


def _load_player(pj) -> Player:
    """Reconstruct a Player dataclass from JSON string or dict."""
    if isinstance(pj, str):
        pdata = json.loads(pj)
    else:
        pdata = dict(pj)

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


def _sanitized_state(player: Player, world: WorldState) -> dict[str, Any]:
    """Return a minimal state snapshot for the thick client."""
    return {
        "player": _dc_asdict(player),
        "world": _dc_asdict(world),
    }


# ---------------------------------------------------------------------------
# Endpoints: Bootstrap
# ---------------------------------------------------------------------------

@app.post("/game/start", response_model=StartResponse)
async def start_game(req: StartRequest):
    """Start a new campaign from backstory text."""
    client = LLMClient()

    system_prompt = client._load_system_prompt("prompts/character_creation.md")
    parsed = client.generate_structured(
        system_prompt=system_prompt,
        user_prompt=req.backstory,
        schema=CharacterProfile,
    )

    player = Player(
        name=req.player_name,
        faction=parsed.origin_faction,
        motivation=parsed.motivation,
        goal=parsed.goal,
        stats=PlayerStats(),
    )
    world = WorldState(current_location="The Void")

    intro_system = client._load_system_prompt("prompts/intro_scene.md")
    narrative = client.generate_flavor_text(
        context=f"Player: {player.name}, Faction: {player.faction}",
        instruction="Write an opening scene for the adventure.",
    )

    try:
        choices = client.generate_choices({
            "player_name": player.name,
            "faction": player.faction,
            "location": world.current_location,
            "outcome": "started",
            "narrative": narrative,
        })
    except Exception:
        choices = []

    sid = create_session(player_state=player, world_state=world, last_choices=choices)
    append_message(sid, "system", f"[Game Started — Backstory parsed]\n{narrative}", save_slot=None)

    return StartResponse(session_id=sid, narrative=narrative, choices=choices)


# ---------------------------------------------------------------------------
# Endpoints: Events (CRPG triggers)
# ---------------------------------------------------------------------------

@app.post("/event/dialogue", response_model=DialogueResponse)
async def event_dialogue(req: DialogueRequest):
    """Handle a dialogue event between the player and an NPC.

    If player_message is empty, generate an opening greeting for the NPC.
    Otherwise, determine the player's intent (persuade / intimidate / inquire)
    and generate the NPC's response in character.
    Always returns 3-4 follow-up dialogue choices.
    """
    client = LLMClient()

    # 1. Load session state
    session_data = fetch_session(session_id=req.session_id)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, f"No session found for '{req.session_id}'")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    recent_messages = fetch_messages(req.session_id, limit=8)

    # 2. Build NPC persona from active NPCs or generate on the fly
    npc_persona = "a mysterious traveler"
    for npc_name in world.active_npcs:
        if npc_name.lower() == req.npc_name.lower():
            npc_persona = f"{npc_name} (an NPC currently present in the area)"
            break

    context_block = {
        "player_name": player.name,
        "player_faction": player.faction,
        "player_motivation": player.motivation,
        "location": world.current_location,
        "turn": world.turn_count,
    }

    # 3. Generate NPC dialogue
    dialogue_context = (
        f"Player: {player.name}, Faction: {player.faction}, Motivation: {player.motivation}\n"
        f"Location: {world.current_location}, Turn: {world.turn_count}\n"
        f"You are playing the role of: {npc_persona}\n\n"
    )

    if not req.player_message:
        # NPC initiates — generate greeting
        dialogue_context += "You are initiating this conversation. Generate an opening line."
        system_prompt = client._load_system_prompt("prompts/turn_scene.md")
        npc_response = client.generate_flavor_text(
            context=dialogue_context,
            instruction=f"Speak your opening line to {player.name}. Stay in character.",
        )
    else:
        # Player responds — determine intent and generate NPC reply
        dialogue_context += (
            f"Previous conversation history:\n"
        )
        for msg in recent_messages[-6:]:
            role = "Player" if msg["role"] == "user" else ("DM/NPC" if msg["role"] == "system" else msg["role"])
            dialogue_context += f"  <{role}>: {msg['content']}\n"

        dialogue_context += f"\nYour turn to reply as {req.npc_name}.\n"
        dialogue_context += f"Player said: {req.player_message!r}\n"

        # Try intent classification via LLM structured output
        intent = "general"  # default fallback
        try:
            intent_prompt = (
                f"Analyze the following player utterance and classify its intent.\n\n"
                f"{dialogue_context}\n\n"
                f"Reply with ONLY one word: persuade, intimidate, inquire, threaten, or general."
            )
            intent_text = client.generate_flavor_text(context=intent_prompt, instruction="Single word.")
            intent = intent_text.strip().lower().split()[0] if intent_text.strip() else "general"
        except Exception:
            pass  # default to generic response

        dialogue_system = client._load_system_prompt("prompts/turn_scene.md")
        npc_response = client.generate_flavor_text(
            context=dialogue_context,
            instruction=(
                f"Reply as {req.npc_name}. The player's tone was '{intent}'. "
                f"Respond naturally in character."
            ),
        )

    # 4. Generate dialogue choices for the player's next reply
    try:
        choice_ctx = {
            "player_name": player.name,
            "npc_name": req.npc_name,
            "location": world.current_location,
            "last_message": npc_response[:200],
            "turn": world.turn_count,
        }
        choices_text = (
            f"Player: {choice_ctx['player_name']}\n"
            f"NPC: {req.npc_name}\n"
            f"Location: {choice_ctx['location']}\n"
            f"Last NPC line: {choice_ctx['last_message']}\n\n"
            "Generate 3-4 conversational follow-up choices for the player. "
            "Each should be a short quoted reply (like something you'd say to this NPC). "
            "Vary them: one polite, one bold/aggressive, one inquisitive."
        )
        choices = client.generate_structured(
            system_prompt=client.choice_prompt,
            user_prompt=choices_text,
            schema=ChoicesResponse,
        ).choices
    except Exception as exc:
        client.logger.warning("dialogue choices generation failed: %s", exc) if hasattr(client, 'logger') else None
        choices = [
            f"Tell me more about yourself",
            f"What do you know about {world.current_location}?",
            f"I need your help with something.",
            "Leave quietly",
        ]

    # 5. Persist conversation turn
    append_message(req.session_id, "system", f"[Dialogue with {req.npc_name} — NPC]\n{npc_response}")
    append_message(req.session_id, "user", req.player_message if req.player_message else f"[Initiated dialogue with {req.npc_name}]")
    update_session(
        session_id=req.session_id,
        player_state=player,
        world_state=world,
        last_choices=choices,
    )

    return DialogueResponse(
        session_id=req.session_id,
        npc_response=npc_response,
        dialogue_choices=choices,
        updated_state=_sanitized_state(player, world),
    )


@app.post("/event/interact", response_model=InteractResponse)
async def event_interact(req: InteractRequest):
    """Generate flavor text for a player interacting with an object in the world."""
    client = LLMClient()

    # 1. Load session state
    session_data = fetch_session(session_id=req.session_id)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, f"No session found for '{req.session_id}'")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    # 2. Generate interaction flavor text
    context = (
        f"Location: {world.current_location}\n"
        f"Turn: {world.turn_count}\n"
        f"Player inventory: {', '.join(player.inventory[:5])}{'...' if len(player.inventory) > 5 else ''}\n"
        f"The player interacts with the object: '{req.target_object}'.\n"
        f"Describe what they see, feel, or discover. Keep it atmospheric and concise."
    )

    try:
        narrative = client.generate_flavor_text(
            context=context,
            instruction="Describe the interaction result vividly.",
        )
    except Exception as exc:
        client.logger.warning("interact flavor failed: %s", exc) if hasattr(client, 'logger') else None
        narrative = f"You examine '{req.target_object}'. Nothing happens — or does it?"

    # 3. Persist
    append_message(req.session_id, "system", f"[Interacted with {req.target_object}]\n{narrative}")
    update_session(
        session_id=req.session_id,
        player_state=player,
        world_state=world,
        last_choices=[],
    )

    return InteractResponse(
        session_id=req.session_id,
        narrative_description=narrative,
        updated_state=_sanitized_state(player, world),
    )


@app.post("/event/combat_resolved", response_model=CombatResolvedResponse)
async def event_combat_resolved(req: CombatResolvedRequest):
    """Process post-combat: advance turn count, generate summary, update state."""
    client = LLMClient()

    # 1. Load session state
    session_data = fetch_session(session_id=req.session_id)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, f"No session found for '{req.session_id}'")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    # 2. Advance turn count
    world.advance_turn()

    # 3. Generate post-combat summary
    context = (
        f"Victor: {req.victor}\n"
        f"Defeated enemies: {', '.join(req.defeated_enemies) if req.defeated_enemies else 'none listed'}\n"
        f"Location: {world.current_location}\n"
        f"Turn: {world.turn_count}\n"
        f"Generate a brief (2-3 sentence) post-combat atmospheric summary. "
        f"The dust settles mood — describe the aftermath."
    )

    try:
        summary = client.generate_flavor_text(context=context, instruction="Post-combat summary.")
    except Exception as exc:
        client.logger.warning("combat summary failed: %s", exc) if hasattr(client, 'logger') else None
        defeated_str = ", ".join(req.defeated_enemies) if req.defeated_enemies else "enemies"
        summary = f"The dust settles. {req.victor} stands victorious over the fallen {defeated_str}."

    # 4. Persist
    append_message(
        req.session_id,
        "system",
        f"[Combat Resolved — Victor: {req.victor}, Defeated: {', '.join(req.defeated_enemies)}]\n{summary}",
    )
    update_session(
        session_id=req.session_id,
        player_state=player,
        world_state=world,
        last_choices=[],
    )

    return CombatResolvedResponse(
        session_id=req.session_id,
        narrative_summary=summary,
        updated_state=_sanitized_state(player, world),
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
# Health-check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "running", "version": "0.3.0"}
