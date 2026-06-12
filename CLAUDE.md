# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Architecture Overview

## Neuro-Symbolic Design
This project implements a **neuro-symbolic architecture** for a text RPG where:
- **Symbolic Engine**: Python dataclasses (`state.py`) maintain ground truth game state
- **Neural Engine**: Local LLM via Ollama (`llm_client.py`) acts only as interpreter and flavor-text generator
- **Validation Layer**: Pydantic schemas (`schemas.py`) force structured JSON outputs from the LLM
- **Lore Consistency**: `lore_validator.py` validates character backstories against world lore

## Project Structure
- `src/state.py` - Symbolic state: `Player` (name, faction, motivation, goal, inventory, reputation) and `WorldState` (current_location, active_npcs, turn_count, advance_turn()) dataclasses
- `src/schemas.py` - Pydantic models: `CharacterProfile` (origin_faction, motivation, goal) and `ActionParseResult` (intent, target_entity, is_combat) for structured LLM output
- `src/llm_client.py` - Ollama client wrapper (`LLMClient` class: `generate_structured()`, `generate_flavor_text()`); default model: `qwen3.5:64k`
- `src/lore_validator.py` - Lore consistency system: `LoreParser` (parses markdown lore into `LoreDatabase`), `LoreValidator` (LLM-based semantic validation with conflict detection, Rich negotiation UI, revision suggestion generation), `create_validator()` convenience function
- `data/lore.md` / `data/lore_summary.md` - World lore definitions: factions, magic rules, technology constraints, tone guidelines
- `prompts/character_creation.md` - System prompt instructing the LLM how to extract character profiles from backstory text

## Key Dependencies
- `ollama==0.6.2` - Local LLM client (default model: `qwen3.5:64k`, configurable via `LLMClient.__init__`)
- `pydantic==2.13.3` - Data validation and schema enforcement (`model_json_schema()`, `model_validate_json()`)
- `rich==15.0.0` - Terminal UI panels/tables for negotiation display

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
python src/main.py

# Run lore validator module directly for testing
python -c "from src.lore_validator import create_validator; v = create_validator()"
```

# Critical Implementation Details

## LLM Interaction Patterns
- `generate_structured(schema)` - Uses `ollama.chat()` with `format=schema.model_json_schema()` to force JSON output matching the Pydantic schema; returns validated model instance
- `generate_flavor_text(context, instruction)` - Returns raw narrative string (used by lore validator and game DM prompts)
- All LLM calls go through `LLMClient`; never call `ollama.chat` directly elsewhere

## Lore Validation Flow (lore_validator.py)
1. User provides free-text backstory
2. `LoreParser.parse_markdown()` loads world lore from `data/lore_summary.md`
3. `LoreValidator.validate_input()` calls LLM with lore context + user input to detect conflicts
4. If invalid, `_generate_revision_options()` produces 3 concrete revised backstories resolving the conflicts
5. `negotiate()` presents Rich UI panel and accepts user's decision (accept/retry/skip)

## State Management
- `Player`: name (str), faction (str, default "Unknown"), motivation (str, default "Survive"), goal (str, default "None"), inventory (List[str]), reputation (Dict[str, int])
- `WorldState`: current_location (str, default "The Void"), active_npcs (List[str]), turn_count (int), advance_turn() method

# Important Notes
- **Never** let the LLM modify game state directly - all state mutations go through symbolic engine
- All structured LLM responses pass through Pydantic validation before use
- The lore system's fallback validation checks for forbidden tech (gunpowder, steam) and magic violations (time travel, resurrection) when the LLM call fails
