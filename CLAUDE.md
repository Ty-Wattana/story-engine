# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Architecture Overview

## Neuro-Symbolic Design
This project implements a **neuro-symbolic architecture** where:
- **Symbolic Engine**: Python dataclasses (`state.py`) maintain ground truth game state (inventory, locations, stats, quest progression)
- **Neural Engine**: Local LLM via Ollama (`llm_client.py`) acts only as interpreter and flavor-text generator
- **Validation Layer**: Pydantic schemas (`schemas.py`) force structured JSON outputs from LLM

## Project Structure
- `src/main.py` - Application entry point, game loop, CLI interface
- `src/state.py` - Symbolic state management (Player, WorldState dataclasses)
- `src/schemas.py` - Pydantic models for LLM structured output
- `src/llm_client.py` - Ollama client wrapper (`LLMClient` class: `generate_structured()`, `generate_flavor_text()`)
- `src/lore_validator.py` - Lore consistency system: `LoreParser` (parses markdown lore files into `LoreDatabase`), `LoreValidator` (conflict detection with error/warning/info severity, Rich negotiation UI), `create_validator()` convenience function

## Key Dependencies
- `ollama`: Local LLM client (default model: `qwen3.5:9b-64k`, configurable via `LLMClient.__init__`)
- `pydantic`: Data validation and schema enforcement
- `rich`: Terminal UI for game state visualization

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
# Run the game engine
python src/main.py

# Run a single test
pytest src/test_main.py -v

# Run all tests
pytest -v

# Run lore validator tests only
pytest src/test_lore_validator.py -v
```

# Critical Implementation Details

## Input Parsing Flow (src/main.py)
1. User provides free-text backstory
2. `llm.generate_structured()` converts text to `CharacterProfile` JSON
3. `Player` dataclass is initialized with parsed faction/motivation
4. Symbolic state is immutable ground truth

## LLM Interaction Patterns
- `generate_structured()`: Returns Pydantic model instance via JSON schema
- `generate_flavor_text()`: Returns raw narrative strings
- All LLM calls use `format=schema.model_json_schema()` to enforce structure

## State Management
- `Player`: name, faction, motivation, inventory (List[str]), reputation (Dict[str, int])
- `WorldState`: current_location, active_npcs (List[str]), turn_count
- `advance_turn()`: Increments turn counter (location for hooks)

# Common Patterns

## Adding New Game State
1. Add fields to `Player` or `WorldState` in `state.py`
2. Ensure fields use `dataclasses.field(default_factory=...)` for collections
3. Update LLM schema if new state requires parsing from text

## Adding New Action Types
1. Extend `ActionParseResult` schema in `schemas.py`
2. Add parsing logic in `llm_client.py`
3. Implement symbolic state update in game loop

# Important Notes
- **Never** let LLM modify game state directly - always go through symbolic engine
- All LLM responses must pass through Pydantic validation
- Keep narrative descriptions under 3 sentences (system prompt constraint)
- The game is turn-based; `turn_count` advances each action
