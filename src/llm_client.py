import json
from pathlib import Path
import ollama
import re
from pydantic import BaseModel
from typing import Type, TypeVar, Any

from src.schemas import ActionParseResult

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, model_name: str = "qwen3.5:64k"):
        self.model = model_name

    # ------------------------------------------------------------------
    # Prompt loading (reads from prompts/ at repo root)
    # ------------------------------------------------------------------

    def _load_system_prompt(self, prompt_file: str = "prompts/character_creation.md") -> str:
        """Load a system prompt from the prompts/ directory (repo root)."""
        # prompts/ is at repo root; llm_client.py lives under src/, so go up one level
        base = Path(__file__).resolve().parent.parent
        path = base / prompt_file
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @property
    def action_prompt(self) -> str:
        return self._load_system_prompt("prompts/action.md")

    @property
    def choice_prompt(self) -> str:
        return self._load_system_prompt("prompts/choices.md")

    @property
    def flavor_prompt(self) -> str:
        return self._load_system_prompt("prompts/flavor_text.md")

    @property
    def scene_prompt(self) -> str:
        return self._load_system_prompt("prompts/scene_description.md")

    @property
    def intro_prompt(self) -> str:
        return self._load_system_prompt("prompts/intro_scene.md")

    @property
    def outcome_prompt(self) -> str:
        return self._load_system_prompt("prompts/outcome_narration.md")

    @property
    def turn_scene_prompt(self) -> str:
        return self._load_system_prompt("prompts/turn_scene.md")

    @property
    def lore_validation_prompt(self) -> str:
        return self._load_system_prompt("prompts/lore_validation.md")

    @property
    def backstory_revision_template(self) -> str:
        return self._load_system_prompt("prompts/backstory_revision.md")

    # ------------------------------------------------------------------
    # Structured generation (Pydantic-validated JSON)
    # ------------------------------------------------------------------

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: Type[T], *, retries: int = 3) -> T:
        """Forces the LLM to return JSON matching the Pydantic schema.

        Handles common LLM quirks (markdown fences, conversational text) by
        extracting embedded JSON and retrying on validation failure.
        """
        last_exc: Exception | None = None

        for attempt in range(retries):
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                format="json"
            )

            text = response['message']['content']

            # 1. Strip markdown fences (````json` or ```) if present
            cleaned = self._extract_json_text(text)
            if cleaned is not None:
                parsed = schema.model_validate_json(cleaned)
                if self._looks_valid(parsed, schema):
                    return parsed

            # 2. Try the raw text as-is (LLM sometimes returns bare JSON)
            try:
                parsed = schema.model_validate_json(text.strip())
                if self._looks_valid(parsed, schema):
                    return parsed
            except Exception as raw_exc:
                last_exc = raw_exc
                continue

            # 3. Try to find and extract a JSON object from the text
            extracted = self._extract_json_from_text(text)
            if extracted is not None:
                try:
                    parsed = schema.model_validate_json(extracted)
                    if self._looks_valid(parsed, schema):
                        return parsed
                except Exception:
                    last_exc = raw_exc if 'raw_exc' in locals() else last_exc
                    continue

        # All retries exhausted — LLM didn't produce valid extraction.
        raise ValueError("LLM returned no parseable JSON after 3 attempts")

    def generate_flavor_text(self, context: str, instruction: str) -> str:
        """Standard text generation for narrative output."""
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.flavor_prompt},
                {"role": "user", "content": f"{context}\n\n{instruction}"}
            ]
        )
        result = response['message']['content'].strip()

        # Strip any remaining meta-commentary (defense-in-depth)
        for marker in ("Here is", "I will", "Let me", "*thinks*", "Thinking:", "Step 1", "Okay,", "Alright,"):
            if result.lower().startswith(marker.lower()):
                result = self._trim_meta(result)
                break

        # Detect mid-text revision cycles: the LLM "shows its work" by outputting
        # draft text, then meta-commentary (e.g. '" -> Wait, I shouldn't...'),
        # then repeats with another draft, and finally emits a clean version at
        # the end.  Extract only that final clean block.
        result = self._extract_final_draft(result)

        return result or "(nothing happens)"

    # ------------------------------------------------------------------
    # JSON extraction helpers — handle LLM quirks with markdown fences,
    # conversational text, and embedded objects
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_text(text: str) -> str | None:
        """Strip ```json or ``` fences from the edges of a response.

        Returns the cleaned interior if fences are found, else None.
        """
        m = re.search(r'```(?:json)?\s*([\[\s\S]*?)\s*```', text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _extract_json_from_text(text: str) -> str | None:
        """Find and extract a JSON object from arbitrary text.

        Looks for the first '{' … matching '}' pair, handling nesting.
        Returns the extracted JSON string if found, else None.
        """
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start:i + 1]
        return None

    @staticmethod
    def _looks_valid(parsed: Any, schema: Type[BaseModel]) -> bool:
        """Heuristic check that a Pydantic parse succeeded with sensible fields.

        Rejects results where required string fields are empty or contain
        likely-incorrect defaults (e.g., "assistant" when the model confused
        itself for the AI character).
        """
        # If already a Pydantic model, just check its attributes directly
        if isinstance(parsed, BaseModel):
            obj = parsed
        else:
            try:
                obj = schema.model_validate(parsed)
            except Exception:
                return False

        vals: dict[str, str] = {}
        for field_name, field_info in schema.model_fields.items():
            value = obj.__dict__.get(field_name)
            if isinstance(value, str):
                stripped = value.strip()
                # Reject empty or whitespace-only fields
                if not stripped:
                    return False
                vals[field_name] = stripped
                # Common hallucination patterns — model confuses itself for the AI character
                ai_defaults = {"assistant", "ai", "llm", "model", "chatbot", "bot", "system"}
                if field_name in ("origin_faction",) and stripped.lower() in ai_defaults:
                    return False
            else:
                vals[field_name] = ""

        # Known extraction defaults — the model may echo these from its prompt examples.
        # Reject only when ALL three fields are known extraction defaults simultaneously,
        # indicating the model didn't actually extract from the input.
        _EXTRACTION_DEFAULTS = {"Wanderer", "Discovery", "Forge your own path"}
        if vals.get("origin_faction") in _EXTRACTION_DEFAULTS and \
           vals.get("motivation") in _EXTRACTION_DEFAULTS and \
           vals.get("goal") in _EXTRACTION_DEFAULTS:
            return False

        return True

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

    @staticmethod
    def _extract_final_draft(text: str) -> str:
        """Extract clean output from LLM self-correction / draft-revision cycles.

        Some models output internal monologue like:

            "Some draft text." -> Wait, I shouldn't do that...
            Revised text here. -> Let me fix the quotes.
            Okay, final version:
            The actual clean output.

        Returns only the final narrative block, discarding all intermediate drafts
        and commentary.
        """
        lines = text.split('\n')

        # --- Pass 1: explicit revision markers (e.g. "Okay, final version:", "Revised text:") ---
        MARKER_PATTERNS = [
            r"(?i)^ok[a-z]*\s*,?\s*(alright\s+)?(?:final\s+)?(?:version|ver)\w*\b",
            r"(?i)^revised\s+text\b",
            r"(?i)^\s*final\s*:\s*$",
            r"(?i)^here\'?s?\s+(the\s+)?(clean|final|correct)\b",
        ]

        for i, line in enumerate(lines):
            for pat in MARKER_PATTERNS:
                if re.search(pat, line):
                    # Content after the marker is candidate text
                    candidate = '\n'.join(lines[i + 1:]).strip()
                    if candidate and len(candidate) > 3:
                        return candidate

        # Also handle "I will write:" or "Here goes:" type markers that _trim_meta might miss
        for i, line in enumerate(lines):
            if re.search(r'(?i)^you\'?ll?\s+get\s+:|^(?:I\s+)?(?:will|got)\s+(?:write|output|text):\s*$', line):
                candidate = '\n'.join(lines[i + 1:]).strip()
                if candidate and len(candidate) > 3:
                    return candidate

        # --- Pass 2: "draft -> commentary" pattern — take content after last meta line ---
        # Meta lines: contain '" ->' followed by thinking-like words (wait, I need, let me, etc.)
        LAST_META_RE = re.compile(r'"[^\n]*\s*->\s*(wait|I\s|let\s|oh\s|hmm|right|nope|sorry|ah )', re.IGNORECASE)
        last_meta_end = 0
        for i, line in enumerate(lines):
            if LAST_META_RE.search(line):
                # Find start of next non-empty line after this meta line
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        candidate = '\n'.join(lines[j:]).strip()
                        last_meta_end = j
                        break
                # Don't return yet — keep looking for deeper meta lines

        if last_meta_end > 0:
            candidate = '\n'.join(lines[last_meta_end:]).strip()
            # Strip trailing '" -> ...' fragments that follow the narrative itself
            for trim in ('" ->', '" ->\n', ' -> ', '.\" ->'):
                idx = candidate.find(trim)
                if idx != -1:
                    candidate = candidate[:idx].strip()
                    break
            if candidate and len(candidate) > 3:
                return candidate

        # --- Pass 3: "block-level" heuristic — find paragraphs separated by meta commentary. ---
        # Split on blank lines; skip commentary blocks; take the longest non-commentary block.
        blocks = [b.strip() for b in re.split(r'\n\s*\n', text) if b.strip()]
        if len(blocks) >= 2:
            best = None
            best_len = 0
            for block in reversed(blocks):
                # Skip commentary blocks (contain -> with thinking words)
                if LAST_META_RE.search(block):
                    continue
                words = len(block.split())
                if words < 3:
                    continue
                if words > best_len:
                    best = block
                    best_len = words
            if best:
                return best

        return text

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
                           player info, inventory, locations, etc. May also contain
                           ``story_events`` (str) for disambiguation against recent events.
        Returns:
            Validated ActionParseResult via Pydantic schema enforcement.
        """
        context_block = json.dumps(state_context, indent=2)
        story = state_context.get("story_events", "")
        prompt = f"Current game state:\n{context_block}"
        if story:
            prompt += f"\n\n{story}"
        prompt += f"\n\nPlayer input: {user_input!r}\n\nParse this action into structured mechanics."

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
        story = state_context.get("story_events", "")
        context_text = f"Game state:\n{context_block}"
        if story:
            context_text += f"\n\n{story}"
        context_text += "\n\nGenerate action options."

        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": self.choice_prompt},
                {"role": "user", "content": context_text},
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

        # Last-resort: split by bullets or numbered lines, skipping markdown noise
        fallback = []
        for line in text.splitlines():
            stripped = line.strip()

            # Skip empty, too short, or overly long lines
            if not stripped or len(stripped) > 120:
                continue

            # Skip markdown headers, horizontal rules, and pipe-delimited rows
            if re.match(r"^#{1,6}\s|^[-*_]{3,}$|^\|", stripped):
                continue

            # Strip leading bullets/numbers then remove structural chars (pipes, bold markers)
            core = re.sub(r"^\d+\.\s*", "", stripped)
            core = re.sub(r"^[-•*]+\s*", "", core).strip()
            core = re.sub(r"[|`_*]", "", core).strip()

            if 10 < len(core) < 120:
                fallback.append(core)
        return fallback[:6] if fallback else []
