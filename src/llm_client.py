import json
from pathlib import Path
import ollama
from pydantic import BaseModel
from typing import Type, TypeVar

T = TypeVar('T', bound=BaseModel)

class LLMClient:
    def __init__(self, model_name: str = "qwen3.5:9b-64k"):
        self.model = model_name

    def _load_system_prompt(self, prompt_file: str = "prompts/character_creation.md") -> str:
        """Load the system prompt from a markdown file."""
        path = Path(__file__).parent.parent / prompt_file
        if not path.exists():
            return "Extract the character's core details: origin_faction, motivation (one word), and goal."
        return path.read_text(encoding="utf-8").strip()

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

    def generate_flavor_text(self, context: str, instruction: str) -> str:
        """Standard text generation for narrative output."""
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a dungeon master for a dark fantasy RPG. Keep descriptions under 3 sentences."},
                {"role": "user", "content": f"Context: {context}\nInstruction: {instruction}"}
            ]
        )
        return response['message']['content']