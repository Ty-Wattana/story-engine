# Neuro-Symbolic RPG Story Engine (PoC)

## 📖 Overview
This project is a Proof-of-Concept (PoC) for an automatic story generation engine designed for an isometric, turn-based RPG. It allows players to input free-text backstories and actions, which dynamically alter the narrative and game world. 

Crucially, this is **NOT a chatbot**. It utilizes a **Neuro-Symbolic Architecture** where a local Large Language Model (LLM) acts purely as an interpreter and flavor-text generator, while a strict symbolic system (Python classes) maintains the ground truth of the game state.

## 🧠 Core Philosophy: The Neuro-Symbolic Approach
To prevent AI hallucinations from breaking game logic, the responsibilities are strictly divided:

1. **The Symbolic Engine (The Master):**
   * Manages inventory, location, stats, and logical quest progression.
   * Defined entirely in standard code (Python dataclasses).
   * Dictates *what* is physically happening in the game.
2. **The Neural Engine (The Translator):**
   * Powered by a local LLM (`qwen3.5:9b` via Ollama).
   * **Input Parsing:** Converts messy player input ("I want to sneak up and steal the guard's keys") into structured data (JSON matching Pydantic schemas) that the Symbolic Engine can process.
   * **Narrative Generation:** Takes the updated Symbolic State and translates it into immersive descriptions ("You slip silently through the shadows...").

## 🛠️ Tech Stack
* **Language:** Python 3.10+
* **LLM Backend:** `ollama` running locally.
* **Target Model:** `qwen3.5:9b`
* **Data Validation:** `pydantic` (Crucial for forcing structured JSON outputs).
* **Terminal UI:** `rich` (For visual separation of game state, narrative, and debug info).

## 📁 Project Structure
```text
story_engine_poc/
├── README.md           # Project documentation and architectural goals
├── CLAUDE.md           # Strict rules for AI agents (Claude Code)
├── requirements.txt    # Python dependencies
└── src/
    ├── __init__.py
    ├── main.py         # The core game loop and application entry point
    ├── state.py        # The Symbolic Engine (Classes tracking World/Player state)
    ├── schemas.py      # Pydantic models defining strict LLM JSON output structures
    └── llm_client.py   # The wrapper for interacting with Ollama