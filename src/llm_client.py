import json
import ollama
from pydantic import BaseModel
from typing import Type, TypeVar

T = TypeVar('T', bound=BaseModel)

class LLMClient:
    def __init__(self, model_name: str = "qwen3.5:9b"):
        self.model = model_name

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
        
        # Parse the JSON string back into the Pydantic model
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