# Neuro-Symbolic RPG Story Engine (PoC)

## Overview

A Proof-of-Concept turn-based text RPG where player free-text input flows through a **neuro-symbolic pipeline**: an LLM parses intent, a deterministic engine resolves mechanics, and the LLM generates flavor text from engine facts. All game state lives in Python dataclasses — the LLM never touches it directly.

## Architecture: The Neuro-Symbolic Pipeline

The project splits responsibilities between two engines to prevent AI hallucinations from corrupting game logic:

| Engine | Role | Modifies game state? |
|--------|------|---------------------|
| **Symbolic** (`src/state.py`, `src/action_engine.py`) | Ground truth — stats, inventory, location, reputation, dice rolls, outcome effects | Yes (deterministic) |
| **Neural** (`src/llm_client.py`) | Input parsing → flavor text generation; read-only from state | No |

## Running the Application

### HTTP API Server

```bash
uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload
```

API docs: <http://127.0.0.1:8000/docs>

### Terminal Client (dumb HTTP-only CLI)

```bash
python main.py
```

The client sends no game logic — it just talks to the server over HTTP. It auto-waits for the server at `/health` before connecting.

## Game Loop via FastAPI

The entire campaign runs through four REST endpoints. A single turn is a request-response cycle; session state persists in SQLite between turns.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/health` | Health check (returns `{"status": "running", "version": "0.2.0"}`) |
| `POST` | `/game/start` | Start a new campaign from backstory text |
| `POST` | `/game/action` | Process one player action through the full pipeline |
| `POST` | `/system/save` | Assign a named save slot to a session |
| `POST` | `/system/load` | Duplicate a saved session into a new one |

### Sequence: Starting a Campaign

```
Client                              Server (FastAPI)
  |                                    |
  |-- POST /health -------------------->|  {"status":"running"}
  |<-------------------------------------|
  |                                    |
  |-- POST /game/start --------------->|  body: {"player_name":"Aria","backstory":"..."}
  |   (backstory text)                 |
  |                                    |  1. LLM parses backstory → CharacterProfile (Pydantic)
  |                                    |  2. Engine creates Player + WorldState from parsed profile
  |                                    |  3. Session persisted to SQLite (session_id returned)
  |                                    |  4. LLM generates intro narrative
  |<-------- {session_id, narrative}---|
```

**Start response:** `{"session_id":"<uuid>","narrative":"<DM opening scene>"}`

### Sequence: Each Turn

```
Client                              Server (FastAPI)
  |                                    |
  |-- POST /game/action ------------->|  body: {"session_id":"<uuid>","player_input":"I sneak past the guard"}
  |   (free-text action)               |
  |                                    |  ── Phase 1: Load state from SQLite ──
  |                                    |    1. fetch_session(session_id) → Player, WorldState
  |                                    |    2. fetch_messages(sid, limit=5) → recent chat context
  |                                    |
  |                                    |  ── Phase 2: LLM parses intent (structured output) ──
  |                                    |    3. LLM generates_action_result(input, snapshot)
  |                                    |       → ActionParseResult (Pydantic-validated):
  |                                    |         {intent, verb, action_type, target_entity, modifiers}
  |                                    |
  |                                    |  ── Phase 3: Symbolic engine resolves (deterministic) ──
  |                                    |    4. resolve_action(action_type, stat, proficiency, advantage)
  |                                    |       → {dice_roll, modifier, final_score, target_dc, outcome_level}
  |                                    |    5. apply_outcome_effects(outcome_level, action_type)
  |                                    |       → engine-computed effects list (inventory changes, HP, rep, etc.)
  |                                    |    6. world.advance_turn()
  |                                    |
  |                                    |  ── Phase 4: LLM generates flavor (stateless) ──
  |                                    |    7. LLM generates_flavor_text(engine_result + recent_messages)
  |                                    |       → narrative string (never touches game state)
  |                                    |
  |                                    |  ── Phase 5: Persist to SQLite ──
  |                                    |    8. update_session(player_state, world_state)
  |                                    |    9. append_message(sid, "user", input)
  |                                    |   10. append_message(sid, "assistant", narrative)
  |<-------- {session_id, narrative, outcome, ...}---|
```

**Action response:**
```json
{
  "session_id": "<uuid>",
  "narrative": "You slip silently through the shadows...",
  "outcome": {
    "dice_roll": 17,
    "modifier": 4,
    "final_score": 21,
    "target_dc": 14,
    "outcome_level": "success",
    "success": true,
    "effects_applied": {"player.inventory.add": "rusty_key"}
  },
  "updated_player_state": { ... },
  "updated_world_state": { ... }
}
```

### Sequence: Saving and Loading

```
POST /system/save          Duplicate the named session into a new one:
  body: {                   1. Find session by slot name
       "session_id":"<>",   2. duplicate_session(slot_name) → new_sid
       "slot_name":"Chapter1"}  3. Return new_sid + recent narrative context + state dicts
```

### Turn-by-Turn Flow Summary

```
┌──────────────┐     POST /game/start      ┌───────────────┐
│  No session   │ ───────────────────────→ │  LLM parses    │
│  (bootstrap)  │                           │  backstory →   │
└──────────────┘                           │  CharacterProfile│
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ Engine creates  │
                                            │ Player + World  │
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ LLM generates  │
                                            │ intro narrative│
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ Persist to     │
                                            │ SQLite + return│
                                            │ session_id     │
                                            └───────┬────────┘
                                                    │
                                              ══════╗╠══════  (session active)
                                                    ╚╝
┌──────────────┐     POST /game/action       ┌───────────────┐
│  Player type  │ ───────────────────────→ │  Load session  │
│  free-text    │                            │ from SQLite   │
└──────────────┘                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ LLM parse       │
                                            │ intent →        │
                                            │ ActionParseResult│
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ Deterministic   │
                                            │ dice roll +     │
                                            │ outcome engine  │
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ LLM generates   │
                                            │ outcome narrative│
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ Persist state +│
                                            │ messages to SQLite│
                                            └───────┬────────┘
                                                    │
                                            ┌───────▼────────┐
                                            │ Return narrative│
                                            │ + outcome +     │
                                            │ updated state   │
                                            └───────┬────────┘
                                                    │
                                              ══════╗╠══════  (next turn)
                                                    ╚╝
```

## Project Structure

```text
story_engine_poc/
├── README.md           # This file
├── CLAUDE.md           # Strict rules for AI agents (Claude Code)
├── requirements.txt    # Python dependencies
├── main.py             # Dumb terminal client (HTTP only, no game logic)
├── src/
│   ├── __init__.py
│   ├── server.py       # FastAPI endpoints + state reconstruction helpers
│   ├── database.py      # SQLite persistence (sessions + message history)
│   ├── state.py         # Symbolic engine: Player, WorldState, StateManager
│   ├── schemas.py       # Pydantic models for LLM JSON validation
│   ├── action_engine.py # D&D dice rolling, outcome evaluation, effect resolution
│   ├── llm_client.py    # Ollama client wrapper (structured + flavor generation)
│   ├── narrative.py     # Scene descriptions, story memory, outcome panel rendering
│   ├── engine/          # Character creation helpers
│   └── lore/            # Lore validation package
├── prompts/             # System prompts (character_creation, intro_scene, turn_scene, etc.)
└── data/
    ├── lore.md          # World lore: factions, magic rules, tech constraints
    ├── lore_summary.md  # Condensed lore for LLM context window
    └── game.db          # SQLite DB (auto-created)
```

## Key Implementation Details

### Golden Rules

1. **Never** let the LLM modify game state — all mutations go through `StateManager.apply_effect()` / `apply_outcome_effects()`.
2. All structured LLM responses pass through Pydantic validation before use.
3. Effects are engine-computed (deterministic), never LLM-predicted.
4. Use lightweight snapshots when feeding state to LLM for choices — avoids verbose echoing.

### Action Resolution (D&D 5e Style)

| Rule | Detail |
|------|--------|
| **Advantage** | Take max of two d20 rolls |
| **Disadvantage** | Take min of two d20 rolls |
| **Stat bonus** | `(score - 10) // 2` |
| **Proficiency bonus** | `2 + turn_count // 5` (scales every 5 turns) |
| **Final score** | `dice_roll + stat_bonus + proficiency + tool_modifier` |
| **Natural 20** | Auto-crit (`crit_fresh`) |
| **Natural 1** | Auto-fail (`failure`) |
| **margin >= 10** | Crit (`crit`) |
| **margin >= 0** | Success (`success`) |
| **margin > -5** | Partial success (`partial`) |
| **else** | Failure (`failure`) |

### State Mutation Grammar

Effects use the grammar `entity.field.operator <value>`:

| Pattern | Example | Effect |
|---------|---------|--------|
| `player.inventory.add` | `player.inventory.add rusty_key` | Add item |
| `player.inventory.remove` | `player.inventory.remove rusty_key` | Remove item |
| `player.reputation.inc` | `player.reputation.inc guard_guild` | Increase faction rep |
| `player.stats.<stat>_bonus.inc` | `player.stats.str_bonus.inc 2` | Temporary stat boost |

## Tech Stack

- **Language:** Python 3.10+
- **LLM Backend:** `ollama` running locally
- **Target Model:** `qwen3.5:9b` (configurable in `llm_client.py`)
- **Data Validation:** `pydantic` — forces structured JSON from LLM
- **Terminal UI:** `rich` — panels, tables, color-coded output
- **HTTP Server:** `fastapi` + `uvicorn`
