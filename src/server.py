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

from src.state import Player, PlayerStats, WorldState, QuestNode, StateManager
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
    RollResult,
)
from src.llm_client import LLMClient
from src.schemas import ChoicesResponse, IntentClassification
from src.action_engine import resolve_action
from src.narrative import NarrativeDirector
import logging

log = logging.getLogger(__name__)

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


def _load_quest_nodes(qd: dict[str, dict]) -> dict[str, QuestNode]:
    """Reconstruct QuestNode objects from deserialized JSON dicts."""
    if not qd:
        return {}
    return {k: QuestNode(**v) for k, v in qd.items()}


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
        quests=_load_quest_nodes(wdata.get("quests", {})),
    )


def _sanitized_state(player: Player, world: WorldState) -> dict[str, Any]:
    """Return a minimal state snapshot for the thick client."""
    return {
        "player": _dc_asdict(player),
        "world": _dc_asdict(world),
    }


def _game_snapshot(player: Player, world: WorldState) -> dict[str, Any]:
    """Godot-ready snapshot via StateManager.snapshot()."""
    # ponytail: StateManager constructor is minimal here — name/faction/motivation/goal/inventory/reputation + stats + location/turn_count/quests
    sm = StateManager(
        player=Player(
            name=player.name, faction=player.faction, motivation=player.motivation,
            goal=player.goal, inventory=list(player.inventory), reputation=dict(player.reputation),
            stats=PlayerStats(**{s: getattr(player.stats, s) for s in ["strength","dexterity","intelligence","wisdom","constitution","charisma"]}),
        ),
        world=WorldState(
            current_location=world.current_location, active_npcs=list(world.active_npcs),
            turn_count=world.turn_count, quests={k: QuestNode(**v) for k, v in world.quests.items()},
        ),
    )
    return sm.snapshot()


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

    # ---- Update quest state (bootstrap — may not trigger yet) ------------------
    NarrativeDirector.update_quests(world, player, world.current_location)

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
    """Handle a dialogue event using the BG3 Handshake (3-phase intent pipeline).

    Phase 1 — classify intent and whether a dice roll is needed.
    Phase 2 — resolve mechanics if requires_roll == True.
    Phase 3 — synthesize final narrative from classification + optional roll result.
    """
    client = LLMClient()

    # ---- Load session state ---------------------------------------------------
    session_data = fetch_session(session_id=req.session_id)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, f"No session found for '{req.session_id}'")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    # ---- NPC persona (unchanged helper) ----------------------------------------
    npc_persona = "a mysterious traveler"
    for npc_name in world.active_npcs:
        if npc_name.lower() == req.npc_name.lower():
            npc_persona = f"{npc_name} (an NPC currently present in the area)"
            break

    # ---- Update quest state ----------------------------------------------------
    NarrativeDirector.update_quests(world, player, world.current_location)

    # ---- Phase 1: classify intent ----------------------------------------------
    quest_ctx = NarrativeDirector.format_quest_context(world)
    world_context_for_llm = (
        f"Location: {world.current_location}, Turn: {world.turn_count}\n"
        f"Player: {player.name} ({player.faction}, motivation: {player.motivation})\n"
        f"NPC: {req.npc_name}"
    )
    if quest_ctx:
        world_context_for_llm += "\n" + quest_ctx

    if not req.player_message:
        # NPC initiates — skip handshake, use old flow.
        dialogue_context = (
            f"Player: {player.name}, Faction: {player.faction}, Motivation: {player.motivation}\n"
            f"Location: {world.current_location}, Turn: {world.turn_count}\n"
            f"You are playing the role of: {npc_persona}\n\n"
            "You are initiating this conversation. Generate an opening line."
        )
        npc_response = client.generate_flavor_text(
            context=dialogue_context,
            instruction=f"Speak your opening line to {player.name}. Stay in character.",
        )
        intent_classification = IntentClassification(
            intent_type="DIALOGUE",
            requires_roll=False,
            skill_required=None,
            action_summary=f"{req.npc_name} initiates dialogue.",
        )
    else:
        classification = client.classify_intent(req.player_message, world_context_for_llm)
        # Persist the classification for logging/traceability.
        intent_classification = classification

    # ---- Phase 2: resolve mechanics (only if required) --------------------------
    roll_metadata: dict | None = None
    roll_result: RollResult | None = None

    if intent_classification.requires_roll and req.player_message:
        dc = 15  # simulated target DC for events
        action_type_map = {
            "Persuasion": "social",
            "Intimidation": "social",
            "Deception": "social",
            "Insight": "exploration",
            "Investigation": "exploration",
            "Thievery": "stealth",
        }
        action_type = action_type_map.get(intent_classification.skill_required, "social")
        skill_to_stat = {
            "Persuasion": ("charisma", player.stats.charisma),
            "Intimidation": ("charisma", player.stats.charisma),
            "Deception": ("charisma", player.stats.charisma),
            "Insight": ("wisdom", player.stats.wisdom),
            "Investigation": ("intelligence", player.stats.intelligence),
            "Thievery": ("dexterity", player.stats.dexterity),
        }
        _, stat_value = skill_to_stat.get(intent_classification.skill_required, ("charisma", 10))

        # Proficiency scales with turn count per CLAUDE.md rules.
        proficiency = 2 + world.turn_count // 5

        roll_metadata = resolve_action(
            action_type=action_type,
            stat_name=None,
            stat_value=stat_value,
            advantage="none",
            proficiency=proficiency,
            world_context=f"{world.current_location} DC={dc}",
        )
        roll_metadata["target_dc"] = dc  # override engine default with our simulated DC

        # Wire roll data into BG3-style handshake for Godot.
        roll_result = RollResult(
            skill_used=intent_classification.skill_required,
            target_dc=roll_metadata["target_dc"],
            roll_total=roll_metadata["final_score"],
            is_success=roll_metadata["success"],
        )

    # ---- Phase 3: synthesize narrative ------------------------------------------
    npc_response = client.generate_synthesis(
        classification=intent_classification,
        roll_metadata=roll_metadata,
        world_context=world_context_for_llm,
    )

    # ---- Generate dialogue choices (unchanged) ----------------------------------
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
        log.warning("dialogue choices generation failed: %s", exc)  # pyright: ignore[reportUndefinedVariable]
        choices = [
            f"Tell me more about yourself",
            f"What do you know about {world.current_location}?",
            f"I need your help with something.",
            "Leave quietly",
        ]

    # ---- Persist and return -----------------------------------------------------
    append_message(req.session_id, "system", f"[Dialogue with {req.npc_name}]\n{npc_response}")
    if req.player_message:
        append_message(req.session_id, "user", req.player_message)
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
        updated_state=_game_snapshot(player, world),
        roll_metadata=roll_result,
    )


@app.post("/event/interact", response_model=InteractResponse)
async def event_interact(req: InteractRequest):
    """Handle object interaction using the BG3 Handshake (3-phase intent pipeline)."""
    client = LLMClient()

    # ---- Load session state ---------------------------------------------------
    session_data = fetch_session(session_id=req.session_id)
    if not session_data or "player_state" not in session_data:
        raise HTTPException(404, f"No session found for '{req.session_id}'")

    player = _load_player(session_data["player_state"])
    world = _load_world(session_data["world_state"])

    # ---- Update quest state ----------------------------------------------------
    NarrativeDirector.update_quests(world, player, world.current_location)
    quest_ctx_interact = NarrativeDirector.format_quest_context(world)

    # ---- Phase 1: classify intent ----------------------------------------------
    world_context_for_llm = (
        f"Location: {world.current_location}, Turn: {world.turn_count}\n"
        f"Object: '{req.target_object}'\n"
        f"Inventory: {', '.join(player.inventory[:5])}{'...' if len(player.inventory) > 5 else ''}"
    )
    if quest_ctx_interact:
        world_context_for_llm += "\n" + quest_ctx_interact

    classification = client.classify_intent(
        player_input=f"Interact with object: {req.target_object}",
        world_context=world_context_for_llm,
    )
    intent_classification = classification

    # ---- Phase 2: resolve mechanics (only if required) --------------------------
    roll_metadata: dict | None = None
    roll_result: RollResult | None = None

    if intent_classification.requires_roll:
        action_type_map = {
            "Investigation": "exploration",
            "Perception": "exploration",
            "Arcana": "exploration",
            "Nature": "exploration",
            "History": "exploration",
        }
        action_type = action_type_map.get(intent_classification.skill_required, "exploration")

        skill_to_stat = {
            "Investigation": ("intelligence", player.stats.intelligence),
            "Perception": ("wisdom", player.stats.wisdom),
            "Arcana": ("intelligence", player.stats.intelligence),
            "Nature": ("intelligence", player.stats.intelligence),
            "History": ("intelligence", player.stats.intelligence),
        }
        _, stat_value = skill_to_stat.get(intent_classification.skill_required, ("intelligence", 10))

        proficiency = 2 + world.turn_count // 5
        roll_metadata = resolve_action(
            action_type=action_type,
            stat_name=None,
            stat_value=stat_value,
            advantage="none",
            proficiency=proficiency,
            world_context=f"{world.current_location} DC=12",
        )
        roll_metadata["target_dc"] = 12

        # Wire roll data into BG3-style handshake for Godot.
        roll_result = RollResult(
            skill_used=intent_classification.skill_required,
            target_dc=roll_metadata["target_dc"],
            roll_total=roll_metadata["final_score"],
            is_success=roll_metadata["success"],
        )

    # ---- Phase 3: synthesize narrative ------------------------------------------
    narrative = client.generate_synthesis(
        classification=intent_classification,
        roll_metadata=roll_metadata,
        world_context=world_context_for_llm,
    )

    # ---- Persist and return -----------------------------------------------------
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
        updated_state=_game_snapshot(player, world),
        roll_metadata=roll_result,
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
