# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Architecture Overview

## Two Entry Points — Same Backend
- **HTTP API**: `uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload` — event-driven API (default for development/testing)
- **Thick Client**: `python main.py` — terminal app that polls the server, simulates a Godot-style HUD with event triggers (dialogue/interact/combat), auto-waits `/health` before connecting

## Neuro-Symbolic Architecture
A D&D 5e-inspired text RPG where game state lives in Python dataclasses and the LLM never touches it:
- **Symbolic Engine**: `src/state.py` — Player, PlayerStats, WorldState, StateManager (ground truth)
- **Event Engine**: `src/action_engine.py` — deterministic dice rolling, outcome evaluation, effect resolution
- **Neural/LLM Layer**: `src/llm_client.py` — Ollama wrapper for intent classification, NPC dialogue, flavor text; structured outputs via Pydantic
- **Validation**: `src/schemas.py` — Pydantic models for all LLM JSON outputs and event requests/responses
- **Lore Consistency**: `src/lore/` — parser, validator, rules (faction/magic/tech constraints)
- **Persistence**: `src/database.py` — SQLite with `sqlite3` stdlib; WAL mode, sessions as JSON text columns, message_history table

## Core Flow (Event-Driven)
1. Client sends event request (dialogue / interact / combat_resolved) to server
2. Server loads session from SQLite → StateManager
3. LLM classifies intent (if dialogue) or generates outcome/narrative (stateless)
4. Engine resolves mechanics deterministically (dice, outcome level, effects)
5. `StateManager.apply_outcome_effects()` applies state mutations (never LLM-predicted)
6. Server persists updated state + message to SQLite; returns narrative + outcome + sanitized state

### Bootstrap (Campaign Start)
1. `POST /game/start` with `player_name` + `backstory`
2. LLM parses → `CharacterProfile` (Pydantic-validated)
3. Engine creates `Player` + `WorldState`; persists to SQLite; returns `session_id` + intro narrative + choices

## Project Structure

### Entry Points
```
main.py              # Thick client (HTTP-only terminal, event triggers, HUD)
src/server.py        # FastAPI server (event-driven API)
python -m src.engine.loop  # Legacy CLI (MUD-style game loop, still functional)
```

### Packages (`src/`)
```
src/
├── server.py        # FastAPI: /game/start, /event/*, /system/*, /health
├── database.py      # SQLite persistence (sqlite3 stdlib; WAL mode)
├── state.py         # Player, PlayerStats, WorldState, StateManager
├── action_engine.py # Dice rolling (advantage/disadvantage), resolve_action, evaluate_outcome, apply_outcome_effects
├── schemas.py       # Pydantic: CharacterProfile, DialogueRequest/Response, InteractRequest/Response, CombatResolvedRequest/Response, ChoicesResponse
├── llm_client.py    # LLMClient (qwen3.5:64k): generate_structured, generate_flavor_text, generate_npc_dialogue, classify_dialogue_intent
├── narrative.py     # StoryEvent, StoryMemory, generate_scene_description, build_outcome_panel
├── engine/          # loop.py (game loop), creation.py (character creation)
├── lore/            # parser.py, validator.py, rules.py
└── ui/              # status.py (HUD display), output.py (outcome panels)
```

### Data & Prompts
- `data/lore.md` / `data/lore_summary.md` — World lore: factions, magic rules, tech constraints
- `data/game.db` — SQLite DB (auto-created)
- `prompts/` — character_creation.md, intro_scene.md, turn_scene.md, scene_description.md, outcome_narration.md, choices.md, action.md, lore_validation.md, backstory_revision.md, flavor_text.md

### HTTP API Endpoints (`src/server.py`, version 0.3.0)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/game/start` | Start new campaign from backstory; returns `session_id` + intro narrative + choices |
| POST | `/event/dialogue` | NPC dialogue event (intent classification, in-character response, follow-up choices) |
| POST | `/event/interact` | Object interaction event (narrative description) |
| POST | `/event/combat_resolved` | Post-combat resolution (victor + defeated enemies) |
| POST | `/system/save` | Assign named save slot to session |
| POST | `/system/load` | Duplicate saved session by slot name into new one |
| POST | `/system/delete` | Delete session by slot name |
| GET | `/health` | Health check (`{"status":"running","version":"0.3.0"}`) |

## Key Implementation Details

### Golden Rules
1. **Never** let the LLM modify game state — all mutations go through `StateManager.apply_effect()` / `apply_outcome_effects()`
2. All structured LLM responses pass through Pydantic validation before use
3. Effects are engine-computed (deterministic), never LLM-predicted
4. Use lightweight snapshots when feeding state to LLM for choices — avoids verbose echoing

### LLM Client (`LLMClient`, default model `qwen3.5:64k`)
- `generate_structured(system_prompt, user_prompt, schema)` → Pydantic model (retries=3, JSON extraction handles markdown fences)
- `generate_flavor_text(context, instruction)` → raw narrative string
- `generate_npc_dialogue(npc_name, npc_persona, context, instruction)` → in-character NPC dialogue
- `classify_dialogue_intent(conversation_history)` → one of: persuade, intimidate, inquire, threaten, general
- `generate_dialogue_choices(player_name, npc_name, location, last_npc_line)` → 3-4 follow-up options
- `generate_choices(ctx)` → DM action options (for /game/start bootstrap)

### Action Resolution Rules (D&D 5e Style)
- **Advantage**: max of two d20 rolls; **Disadvantage**: min
- **Stat bonus**: `(score - 10) // 2`
- **Total modifier**: stat_bonus + proficiency + tool_modifier
- **Proficiency**: `2 + world.turn_count // 5` (scales every 5 turns)
- **Outcome levels** (margin = final_score - DC):
  - Natural 20 → `crit_fresh`; natural 1 → `failure`
  - margin >= 10 → `crit`; margin >= 0 → `success`; margin > -5 → `partial`; else → `failure`

### State Mutation Grammar (`entity.field.operator`)
- `player.inventory.add <val>` — append to inventory
- `player.inventory.remove <val>` — remove first match
- `player.reputation.inc <faction>` — increment rep for faction
- `player.stats.<stat>_bonus.inc <n>` — temporary stat bonus (stacking)

### Reputation Threshold System
- `Player.check_rep_thresholds()` fires unlocks when cumulative reputation crosses thresholds
- Positive keys: `trust_gained`, `enemies_defeated`, `sneak_attempted`, `conversation_started`
- Negative keys: `suspicion_raised`, `offended_officer` (fire immediately on first reach)
- Track via `_rep_frozen` dict to ensure each increment fires only once

### Database Schema (`src/database.py`)
- `game_sessions`: `session_id` (PK), `save_slot` (UNIQUE nullable), `player_state` (JSON), `world_state` (JSON), `last_choices` (JSON array), `last_updated` (ISO-8601)
- `message_history`: `id` (autoincrement PK), `session_id` (FK), `save_slot` (nullable, copied from parent on load), `role` (CHECK user/system/assistant), `content` (TEXT)

### Lore Validation (`src/lore/`)
1. `LoreParser.parse_markdown()` loads world lore from `data/lore_summary.md`
2. `create_validator()` → `LoreValidator` with lore database
3. `validate_input()` calls LLM to detect conflicts between backstory and lore
4. If invalid, `_generate_revision_options()` produces 3 revised backstories

## Development Commands

### Running
```bash
# FastAPI server (development)
uvicorn src.server:app --host 127.0.0.1 --port 8000 --reload

# Thick terminal client (requires server running)
python main.py

# Legacy CLI game loop
python -m src.engine.loop

# API docs at http://127.0.0.1:8000/docs
```

### Testing
```bash
# Test lore validator
python -c "from src.lore.validator import create_validator; v = create_validator()"

# Test database layer
python -c "from src.database import _get_conn; c = _get_conn(); print(c.execute('SELECT name FROM sqlite_master').fetchall())"

# Verify server health
curl http://127.0.0.1:8000/health
```

### Dependencies (requirements.txt)
- `ollama==0.6.2` — LLM backend (qwen3.5:64k default, configurable in LLMClient)
- `fastapi==0.137.2`, `starlette==1.3.1`, `uvicorn` — HTTP server
- `pydantic==2.13.3` — structured LLM output validation
- `rich==15.0.0` — terminal UI (panels, tables, color-coded output)
- `sqlite3` stdlib — persistence (no external DB dependency)
