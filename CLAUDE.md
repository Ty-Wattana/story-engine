# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Architecture Overview

## Neuro-Symbolic Design
This project implements a **neuro-symbolic architecture** where:
- **Symbolic Engine**: Python dataclasses (`src/state.py`) maintain ground truth game state (inventory, locations, stats, quest progression)
- **Neural Engine**: Local LLM via Ollama (`src/llm_client.py`) acts only as interpreter and flavor-text generator
- **Validation Layer**: Pydantic schemas (`src/schemas.py`) force structured JSON outputs from LLM

## Project Structure
- `main.py` - Application entry point, game loop, CLI interface
- `src/state.py` - Symbolic state management (Player, WorldState dataclasses)
- `src/schemas.py` - Pydantic models for LLM structured output
- `src/llm_client.py` - Ollama client wrapper for text and structured generation
- `src/lore_validator.py` - Lore parsing and validation system
- `src/test_lore_validator.py` - Tests for lore validation
- `prompts/` - Markdown prompts for LLM interactions

## Key Dependencies
- `ollama`: Local LLM client (target model: `qwen3.5:9b-64k`)
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
python main.py

# Run a single test
pytest src/test_lore_validator.py -v

# Run all tests
pytest -v
```

# Critical Implementation Details

## Input Parsing Flow (main.py)
1. User provides free-text backstory
2. `llm.generate_structured()` converts text to `CharacterProfile` JSON
3. `Player` dataclass is initialized with parsed faction/motivation
4. Symbolic state is immutable ground truth

## LLM Interaction Patterns
- `generate_structured()`: Returns Pydantic model instance via JSON schema
- `generate_flavor_text()`: Returns raw narrative strings
- All LLM calls use `format=schema.model_json_schema()` to enforce structure

## State Management
- `Player`: name, faction, motivation, goal, inventory (List[str]), reputation (Dict[str, int])
- `WorldState`: current_location, active_npcs (List[str]), turn_count
- `advance_turn()`: Increments turn counter (location for hooks)

## Lore Validation System (src/lore_validator.py)
The lore validator:
1. Parses markdown lore files (`data/lore_summary.md`) into a structured database
2. Extracts facts about factions, magic, technology, setting, etc.
3. Validates user input against established lore constraints
4. Negotiates with users to resolve conflicts
5. Uses regex patterns to extract forbidden values and available options

# Common Patterns

## Adding New Game State
1. Add fields to `Player` or `WorldState` in `src/state.py`
2. Ensure fields use `dataclasses.field(default_factory=...)` for collections
3. Update LLM schema if new state requires parsing from text

## Adding New Action Types
1. Extend `ActionParseResult` schema in `src/schemas.py`
2. Add parsing logic in `src/llm_client.py`
3. Implement symbolic state update in game loop

## Adding New Lore Validation Rules
1. Add new lore sections to markdown files in `data/` directory
2. Update regex patterns in `LoreParser._parse_content()`
3. Add validation logic in `LoreValidator.validate_input()`

## Adding New Prompt Templates
1. Create markdown files in `prompts/` directory
2. Use `_load_system_prompt()` to load from file with fallback
3. Follow existing examples in `prompts/character_creation.md`

# Important Notes
- **Never** let LLM modify game state directly - always go through symbolic engine
- All LLM responses must pass through Pydantic validation
- Keep narrative descriptions under 3 sentences (system prompt constraint)
- The game is turn-based; `turn_count` advances each action
- Lore conflicts are resolved through negotiation with the user
- Markdown lore files use specific patterns: `Setting: ...`, `Key Factions: - Faction`, `Magic: ...`

# Project-Specific Guidelines
- This is a PoC for an isometric, turn-based RPG story generation engine
- The project emphasizes preventing AI hallucinations from breaking game logic
- Use `rich.console` for interactive prompts when validating user input
- Test lore validation by calling `validate_input()` with various user strings
