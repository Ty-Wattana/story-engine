# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Architecture Overview

## Neuro-Symbolic Design
This project implements a **neuro-symbolic architecture** for a D&D 5e-inspired text RPG where:
- **Symbolic Engine**: `state.py` — Python dataclasses maintain ground-truth game state (Player, WorldState, StateManager)
- **Neural Engine**: `llm_client.py` — Ollama client acts only as interpreter and flavor-text generator; never modifies game state
- **Validation Layer**: `schemas.py` — Pydantic schemas force structured JSON outputs from the LLM
- **Lore Consistency**: `src/lore/` — lore validation, parser, and rules

## Core Flow (Single Turn)
1. `advance_turn()` + snapshot (Player stats, inventory, reputation, location, turn count)
2. LLM generates DM choices from lightweight state snapshot
3. Player provides free-text input (or selects a numbered DM choice)
4. LLM parses intent → `ActionParseResult` (Pydantic-validated)
5. `resolve_action()` rolls d20, calculates score vs DC, determines outcome level
6. `StateManager.apply_outcome_effects()` applies deterministic state changes from engine (never LLM-predicted)
7. LLM generates flavor text grounded in current state facts
8. Rich-formatted outcome panel displayed to player

## Project Structure
### Entry Point
- `main.py` — repo root entry point; game loop, character creation, status display, action resolution

### State & Engine (`src/`)
- `src/state.py` — Symbolic state:
  - `PlayerStats` — D&D PHB-style ability scores (STR/DEX/INT/WIS/CON/CHA) with bonus formula `(score-10)//2`; `bonus()` and `bonus_for_choice()` helpers
  - `Player` — name, faction, motivation, goal, inventory, reputation; `check_rep_thresholds()` for unlocking content when rep levels are reached
  - `WorldState` — current_location, active_npcs, turn_count; `advance_turn()` method
  - `StateManager` — unified effect application with grammar ``entity.field.operator`` (e.g., `player.inventory.add`, `player.reputation.inc <faction>`); proficiency scaling (+2 base, +1 every 5 turns); outcome-based deterministic effects (success/crit/partial/failure per action type)

- `src/action_engine.py` — D&D 5e-style mechanical engine:
  - `DiceSystem` — d20 rolling with advantage/disadvantage
  - `SkillResolver` / `BASE_DC` — maps action_type → base DC (combat=12, stealth=14, social=10, exploration=12, item=12)
  - `resolve_action()` — public API: takes action type, stat score, proficiency, advantage, tool modifier; returns roll breakdown dict
  - `evaluate_outcome()` — D&D rules: natural 20=crit_fresh (auto-succeed), natural 1=failure (auto-fail), margin>=10=crit, margin>=0=success, margin>-5=partial, else failure
  - `apply_outcome_effects()` — delegates to StateManager; effects computed by engine, never LLM

- `src/narrative.py` — polish and continuity:
  - `StoryEvent` dataclass — tracks per-turn action outcomes for context
  - `generate_scene_description()` — LLM-driven scene/atmosphere text with recent event history
  - `_fallback_scene_description()` — procedural fallback when LLM is unavailable
  - `build_outcome_panel()` — Rich-formatted outcome panel (color-coded scores, dice breakdown, effects)
  - `StoryMemory` — fixed-length rolling deque buffer (default 20 events) for continuity

- `src/llm_client.py` — Ollama client wrapper (`LLMClient` class):
  - `generate_structured(system_prompt, input_text, schema)` — forces Pydantic-validated JSON output
  - `generate_flavor_text(context, instruction)` — returns raw narrative string
  - `generate_action_result(user_input, snapshot)` — parses player free-text into ActionParseResult (Phase 4)
  - `generate_choices(state_snapshot)` — generates DM action choices from lightweight state (Phase 5)
  - `_load_system_prompt()` — reads prompt files from `prompts/`
  - Default model: `qwen3.5:64k`, configurable via `LLMClient.__init__()`

- `src/schemas.py` — Pydantic validation schemas:
  - `CharacterProfile` — origin_faction, motivation (one-word tag), goal
  - `ActionModifiers` — target_stat, tool_used, advantage/disadvantage
  - `ActionParseResult` — intent, verb, target_entity, action_type (combat/stealth/social/exploration/item), modifiers, raw_input (Pydantic-validated)
  - `OutcomeEffect` — key (entity.field.operator grammar), value
  - `ActionResult` — full dice breakdown (dice_roll, modifier, final_score, target_dc, advantage), outcome_level, success flag, engine-computed effects list, narrative_prompt

- `src/lore/` — lore validation package:
  - `parser.py` — `LoreParser`, `LoreDatabase`, `LoreFact`, `LoreConstraint`, `LoreConflict`
  - `validator.py` — `LoreValidator`, `LLMValidationError`, `create_validator()`
  - `rules.py` — `FORBIDDEN_TECH`, `FORBIDDEN_MAGIC`, `KNOWN_FACTIONS`, `FACTION_HINTS`, `UNKNOWN_FACTION_PATTERN`

### Data & Prompts
- `data/lore.md` / `data/lore_summary.md` — World lore: factions, magic rules, technology constraints, tone guidelines
- `prompts/character_creation.md` — System prompt instructing LLM to extract CharacterProfile from backstory text (origin_faction, motivation one-word tag, goal)

## Key Dependencies
- `ollama==0.6.2` — Local LLM client; default model: `qwen3.5:64k`, configurable via `LLMClient.__init__()`
- `pydantic==2.13.3` — Data validation and schema enforcement (`model_json_schema()`, `model_validate_json()`)
- `rich==15.0.0` — Terminal UI panels/tables for negotiation display, status headers, outcome panels

# Critical Implementation Details

## Golden Rules
1. **Never** let the LLM modify game state directly — all state mutations go through `StateManager.apply_effect()` or `apply_outcome_effects()`
2. All structured LLM responses pass through Pydantic validation (`ActionParseResult`, `CharacterProfile`) before use
3. Effects are engine-computed (deterministic), never LLM-predicted
4. Use lightweight `choices_snapshot()` when feeding state to LLM for choices — avoids verbose inventory/stats echoing

## LLM Interaction Patterns
- `generate_structured(system_prompt, input, schema)` → validated Pydantic model instance
- `generate_flavor_text(context, instruction)` → raw narrative string
- `generate_action_result(user_input, snapshot)` → ActionParseResult (intent, verb, action_type, modifiers)
- `generate_choices(state_snapshot)` → list of DM choice strings
- All LLM calls go through `LLMClient`; never call `ollama.chat()` directly elsewhere

## Action Resolution Rules (D&D 5e Style)
- **Advantage**: take max of two d20 rolls; **Disadvantage**: take min
- **Stat bonus**: `(stat_score - 10) // 2` (standard D&D ability modifier formula)
- **Total modifier**: stat_bonus + proficiency + tool_modifier
- **Proficiency**: `2 + world.turn_count // 5` (scales every 5 turns)
- **Outcome levels** (margin = final_score - DC):
  - Natural 20 → `crit_fresh` (auto-succeed, bonus effect); natural 1 → `failure` (auto-fail)
  - margin >= 10 → `crit`; margin >= 0 → `success`; margin > -5 → `partial`; else → `failure`

## State Mutation Grammar (`entity.field.operator`)
- `player.inventory.add <val>` — append to inventory
- `player.inventory.remove <val>` — remove first match
- `player.reputation.inc <faction>` — increment rep for faction
- `player.stats.<stat>_bonus.inc <n>` — temporary stat bonus (stacking)
- Effects are determined by outcome_level + action_type via `StateManager._resolve_effects()`

## Reputation Threshold System
- `Player.check_rep_thresholds()` fires unlocks when cumulative reputation crosses thresholds
- Keys: `trust_gained`, `enemies_defeated`, `sneak_attempted`, `conversation_started` (positive = unlock content)
- Negative keys: `suspicion_raised`, `offended_officer` (immediate on first reach = penalty)
- Track via `_rep_frozen` dict to ensure each increment fires only once

## Lore Validation Flow
1. User provides free-text backstory
2. `LoreParser.parse_markdown()` loads world lore from `data/lore_summary.md`
3. `LoreValidator.validate_input()` calls LLM with lore context + user input to detect conflicts
4. If invalid, `_generate_revision_options()` produces 3 concrete revised backstories resolving the conflicts
5. `negotiate()` presents Rich UI panel and accepts user's decision (accept/retry/skip)

# Development Commands

## Installation
```bash
# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# Unix/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Running the Application
```bash
# Run the game engine (requires ollama running with qwen3.5:64k)
python main.py

# Run lore validator module directly for testing
python -c "from src.lore.validator import create_validator; v = create_validator()"
```
