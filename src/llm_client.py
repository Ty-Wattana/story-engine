import json
from pathlib import Path
import ollama
import re
from pydantic import BaseModel
from typing import Type, TypeVar

from src.schemas import ActionParseResult

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, model_name: str = "qwen3.5:64k"):
        self.model = model_name

    # ------------------------------------------------------------------
    # Prompt loading (reads from src/prompts/*.md)
    # ------------------------------------------------------------------

    def _load_system_prompt(self, prompt_file: str = "prompts/character_creation.md") -> str:
        """Load a system prompt from the prompts/ directory."""
        path = Path(__file__).parent / prompt_file
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @property
    def action_prompt(self) -> str:
        return self._load_system_prompt("prompts/action.md")

    @property
    def choice_prompt(self) -> str:
        return self._load_system_prompt("prompts/choices.md")

    # ------------------------------------------------------------------
    # Structured generation (Pydantic-validated JSON)
    # ------------------------------------------------------------------

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Type[T]) -> T:
        """Forces the LLM to return JSON matching the Pydantic schema."""

        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            format=schema.model_json_schema()
        )

        raw_json = response['message']['content']
        return schema.model_validate_json(raw_json)

    FLAVOR_SYSTEM_PROMPT = (
        "You are a dungeon master narrator for a dark fantasy RPG.\n"
        "Write ONLY the narrative scene description — no thinking, no planning, no meta-commentary.\n"
        "CRITICAL OUTPUT RULES:\n"
        "1. Start your response directly with the first word of the scene. Never preface with \"Here is\" or \"The scene shows\"\n"
        "2. Output ONLY the narrative text — no thinking blocks, no step explanations, no 'let me write'\n"
        "3. Use exactly 1-2 short sentences. End with a period.\n"
        "4. If uncertain what to write, describe sensory details (sight, sound, smell) of the immediate environment.\n"
        "5. Reference specific locations, NPCs, or items by name when possible.\n\n"
        "WRONG EXAMPLES (do NOT output these):\n"
        "- \"Here is a description:\" <- meta-commentary\n"
        "- \"I will write: ...\" <- planning text\n"
        "- *thinks* \"...\" <- internal monologue\n\n"
        "CORRECT EXAMPLES:\n"
        "- \"Dust swirls in the flickering torchlight as a shadow detaches itself from the corner.\"\n"
        "- \"The elder nods slowly, his gnarled fingers tracing worn symbols on a leather map.\""
    )

    def generate_flavor_text(self, context: str, instruction: str) -> str:
        """Standard text generation for narrative output."""
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.FLAVOR_SYSTEM_PROMPT},
                {"role": "user", "content": f"{context}\n\n{instruction}"}
            ]
        )
        result = response['message']['content'].strip()

        # Strip any remaining meta-commentary (defense-in-depth)
        for marker in ("Here is", "I will", "Let me", "*thinks*", "Thinking:", "Step 1", "Okay,", "Alright,"):
            if result.lower().startswith(marker.lower()):
                result = self._trim_meta(result)
                break

        return result or "(nothing happens)"

    @staticmethod
    def _trim_meta(text: str) -> str:
        """Remove leading meta-commentary and return only the narrative."""
        for sep in ("The ", "A ", "In ", "On ", "Under ", "Over ", "Through "):
            if text.startswith(sep):
                break
        # Try to find first sentence-ending period that looks like actual narrative
        parts = text.split(". ")
        # Skip the first 1-2 segments if they look like meta-commentary (too short)
        skip = 0
        for i, part in enumerate(parts[:-1]):
            if len(part.strip()) > 8:  # meta-phrases are typically short
                break
            skip = i + 1
        rest = parts[skip:]
        # Combine until we get something reasonable
        combined = ". ".join(rest)
        return (combined + ".").strip() or "(nothing happens)"

    # ------------------------------------------------------------------
    # Action-loop endpoints (Phase 4 additions)
    # ------------------------------------------------------------------

    def generate_action_result(self, user_input: str, state_context: dict) -> ActionParseResult:
        """Parse free-text player input into a structured ActionParseResult.

        The LLM only answers *what* the player tried to do — not what happens.
        Outcome effects are determined by the engine from deterministic rules.

        Args:
            user_input: exact text the player typed or selected
            state_context: snapshot dict from StateManager.snapshot() containing
                           player info, inventory, locations, etc.
        Returns:
            Validated ActionParseResult via Pydantic schema enforcement.
        """
        context_block = json.dumps(state_context, indent=2)
        prompt = f"Current game state:\n{context_block}\n\nPlayer input: {user_input!r}\n\nParse this action into structured mechanics."

        return self.generate_structured(
            system_prompt=self.action_prompt,
            user_prompt=prompt,
            schema=ActionParseResult,
        )

    def generate_choices(self, state_context: dict) -> list[str]:
        """Generate in-world DM choice options for the player.

        Returns a JSON array of strings parsed by Pydantic auto-validation.
        Falls back to an empty list on error so the loop never dies.
        """
        context_block = json.dumps(state_context, indent=2)
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.choice_prompt},
                {"role": "user", "content": f"Game state:\n{context_block}\n\nGenerate action options."},
            ],
        )

        text = response["message"]["content"].strip()

        # Try markdown fences first
        m = re.search(r"```(?:json)?\s*([\[\s\S]*?)\s*```", text)
        if m:
            text = m.group(1)

        try:
            result = json.loads(text)
            if isinstance(result, list):
                return [str(c) for c in result]
        except (json.JSONDecodeError, ValueError):
            pass

        # Last-resort: split by bullets or numbered lines
        fallback = []
        for line in text.splitlines():
            stripped = line.strip().lstrip("-•*0123456789.). ").strip()
            if 10 < len(stripped) < 100:
                fallback.append(stripped)
        return fallback[:6] if fallback else []
