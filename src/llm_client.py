"""LLM client — Ollama wrapper for narrative and intent generation.

MUD-style action parsing removed (Step 1/3). New event methods:
- generate_npc_dialogue      (in-character NPC response)
- classify_dialogue_intent   (persuade / intimidate / inquire / threaten / general)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, TypeVar

import ollama
from pydantic import BaseModel, Field, model_validator

from src.schemas import ChoicesResponse, IntentClassification

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, model_name: str = "qwen3.5:64k"):
        self.model = model_name

    # ------------------------------------------------------------------
    # Prompt loading (reads from prompts/ at repo root)
    # ------------------------------------------------------------------

    def _load_system_prompt(self, prompt_file: str = "prompts/intro_scene.md") -> str:
        """Load a system prompt from the prompts/ directory (repo root)."""
        base = Path(__file__).resolve().parent.parent
        path = base / prompt_file
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    @property
    def choice_prompt(self) -> str:
        return self._load_system_prompt("prompts/choices.md")

    @property
    def flavor_prompt(self) -> str:
        return self._load_system_prompt("prompts/flavor_text.md")

    @property
    def intro_prompt(self) -> str:
        return self._load_system_prompt("prompts/intro_scene.md")

    @property
    def intent_classification_prompt(self) -> str:
        return self._load_system_prompt("prompts/intent_classification.md")

    # ------------------------------------------------------------------
    # Structured generation (Pydantic-validated JSON)
    # ------------------------------------------------------------------

    def generate_structured(self, system_prompt: str, user_prompt: str, schema: type[T], *, retries: int = 3) -> T:
        """Forces the LLM to return JSON matching the Pydantic schema."""
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

            cleaned = self._extract_json_text(text)
            if cleaned is not None:
                try:
                    parsed = schema.model_validate_json(cleaned)
                    return parsed
                except Exception as exc:
                    last_exc = exc

            try:
                parsed = schema.model_validate_json(text.strip())
                return parsed
            except Exception as raw_exc:
                last_exc = raw_exc
                continue

            extracted = self._extract_json_from_text(text)
            if extracted is not None:
                try:
                    parsed = schema.model_validate_json(extracted)
                    return parsed
                except Exception:
                    continue

        raise ValueError(f"LLM produced unparseable output after {retries} attempts (last error: {last_exc})")

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

        # Strip meta-commentary (defense-in-depth)
        for marker in ("Here is", "I will", "Let me", "*thinks*", "Thinking:", "Step 1", "Okay,", "Alright,"):
            if result.lower().startswith(marker.lower()):
                result = self._trim_meta(result)
                break

        # Detect mid-text revision cycles
        result = self._extract_final_draft(result)

        return result or "(nothing happens)"

    # ------------------------------------------------------------------
    # JSON extraction helpers — handle LLM quirks
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json_text(text: str) -> str | None:
        m = re.search(r'```(?:json)?\s*([\[\s\S]*?)\s*```', text)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _extract_json_from_text(text: str) -> str | None:
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
    def _trim_meta(text: str) -> str:
        """Remove leading meta-commentary and return only the narrative."""
        for sep in ("The ", "A ", "In ", "On ", "Under ", "Over ", "Through "):
            if text.startswith(sep):
                break
        parts = text.split(". ")
        skip = 0
        for i, part in enumerate(parts[:-1]):
            if len(part.strip()) > 8:
                break
            skip = i + 1
        rest = parts[skip:]
        combined = ". ".join(rest)
        return (combined + ".").strip() or "(nothing happens)"

    @staticmethod
    def _extract_final_draft(text: str) -> str:
        """Extract clean output from LLM self-correction / draft-revision cycles."""
        lines = text.split('\n')

        MARKER_PATTERNS = [
            r"(?i)^ok[a-z]*\s*,?\s*(alright\s+)?(?:final\s+)?(?:version|ver)\w*\b",
            r"(?i)^revised\s+text\b",
            r"(?i)^\s*final\s*:\s*$",
            r"(?i)^here\'?s?\s+(the\s+)?(clean|final|correct)\b",
        ]

        for i, line in enumerate(lines):
            for pat in MARKER_PATTERNS:
                if re.search(pat, line):
                    candidate = '\n'.join(lines[i + 1:]).strip()
                    if candidate and len(candidate) > 3:
                        return candidate

        for i, line in enumerate(lines):
            if re.search(r'(?i)^you\'?ll?\s+get\s+:|^(?:I\s+)?(?:will|got)\s+(?:write|output|text):\s*$', line):
                candidate = '\n'.join(lines[i + 1:]).strip()
                if candidate and len(candidate) > 3:
                    return candidate

        LAST_META_RE = re.compile(r'"[^\n]*\s*->\s*(wait|I\s|let\s|oh\s|hmm|right|nope|sorry|ah )', re.IGNORECASE)
        last_meta_end = 0
        for i, line in enumerate(lines):
            if LAST_META_RE.search(line):
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        candidate = '\n'.join(lines[j:]).strip()
                        last_meta_end = j
                        break

        if last_meta_end > 0:
            candidate = '\n'.join(lines[last_meta_end:]).strip()
            for trim in ('" ->', '" ->\n', ' -> ', '.\" ->'):
                idx = candidate.find(trim)
                if idx != -1:
                    candidate = candidate[:idx].strip()
                    break
            if candidate and len(candidate) > 3:
                return candidate

        blocks = [b.strip() for b in re.split(r'\n\s*\n', text) if b.strip()]
        if len(blocks) >= 2:
            best = None
            best_len = 0
            for block in reversed(blocks):
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
    # Event-driven methods (Step 3)
    # ------------------------------------------------------------------

    def generate_npc_dialogue(self, npc_name: str, npc_persona: str, context: str, instruction: str) -> str:
        """Generate in-character dialogue for a specific NPC.

        The system prompt instructs the model to fully roleplay as the named NPC.
        This is the primary method used by /event/dialogue for NPC replies.
        """
        persona_text = f"\n\nYou are roleplaying as: {npc_name}. " \
                       f"Persona: {npc_persona}. " \
                       f"Stay in character at all times — speak, think, and react exactly as they would."

        system_prompt = self._load_system_prompt("prompts/turn_scene.md") + persona_text
        return self.generate_flavor_text(context=context, instruction=instruction)

    def classify_dialogue_intent(self, conversation_history: str) -> str:
        """Classify the player's latest message intent.

        Returns one of: persuade, intimidate, inquire, threaten, general.
        Falls back to 'general' on any error.
        """
        prompt = (
            f"Read this conversation history and classify the PLAYER'S LAST message.\n\n"
            f"{conversation_history}\n\n"
            f"Reply with ONLY one word: persuade, intimidate, inquire, threaten, or general."
        )
        try:
            text = self.generate_flavor_text(context=prompt, instruction="Single word.").strip()
            return text.split()[0].lower() if text else "general"
        except Exception as exc:
            log.warning("intent classification failed: %s", exc)
            return "general"

    def classify_intent(self, player_input: str, world_context: str) -> IntentClassification:
        """Phase 1 of the BG3 Handshake — classify intent and whether a dice roll is needed.

        Uses low-temperature deterministic output via Pydantic validation.
        Mundane actions (conversation, obvious observations) should set requires_roll=False.
        """
        system_prompt = self.intent_classification_prompt
        user_prompt = (
            f"World context: {world_context}\n\n"
            f"Player action: {player_input}\n\n"
            "Output valid JSON matching the IntentClassification schema."
        )

        try:
            return self.generate_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=IntentClassification,
            )
        except Exception as exc:
            log.warning("intent classification failed: %s", exc)
            # Safe fallback — mundane, no roll.
            return IntentClassification(
                intent_type="DIALOGUE",
                requires_roll=False,
                skill_required=None,
                action_summary=player_input[:80],
            )

    def generate_synthesis(
        self,
        classification: IntentClassification,
        roll_metadata: dict | None,
        world_context: str,
    ) -> str:
        """Phase 3 of the BG3 Handshake — generate final flavor text.

        If roll_metadata is present, narrate the exact mechanical outcome.
        If roll_metadata is None, narrate the mundane action smoothly.
        """
        if roll_metadata is None:
            # Mundane action — no dice involved.
            context = (
                f"Action type: {classification.intent_type}\n"
                f"Skill: {classification.skill_required or 'N/A'}\n"
                f"{classification.action_summary}\n\n"
                "This was a mundane action with no risk. Describe the result naturally — "
                "the player succeeds at what they intended, but narrate it as an ordinary "
                "in-world moment (not triumphant, not disastrous). Keep it 2-4 sentences."
            )
        else:
            # Dice were rolled — must reflect the actual outcome.
            level = roll_metadata.get("outcome_level", "success")
            success_label = "succeed" if roll_metadata.get("success") else "fail"
            context = (
                f"Action type: {classification.intent_type}\n"
                f"Skill: {classification.skill_required or 'N/A'}\n"
                f"{classification.action_summary}\n\n"
                f"Dice result: raw roll={roll_metadata.get('dice_roll')}, "
                f"modifier={roll_metadata.get('modifier')}, "
                f"final score={roll_metadata.get('final_score')}, DC={roll_metadata.get('target_dc')}.\n"
                f"Outcome level: {level} (player {success_label}).\n\n"
                "You MUST narrate this exact outcome. If it was a critical success, make it vividly dramatic. "
                "If partial or success, acknowledge the effort and the result. "
                "If failure (including crit_fresh is special — celebrate it), narrate the setback gracefully: "
                "describe the struggle, the near-miss, or the awkward silence. Never contradict the dice."
            )

        try:
            return self.generate_flavor_text(context=context, instruction="Produce the final narrative.")
        except Exception as exc:
            log.warning("synthesis generation failed: %s", exc)
            if roll_metadata is None:
                return classification.action_summary + "."
            success_word = "You succeed." if roll_metadata.get("success") else "You fail."
            return f"{success_word} ({classification.action_summary})"

    def generate_dialogue_choices(self, player_name: str, npc_name: str, location: str, last_npc_line: str) -> list[str]:
        """Generate 3-4 conversational follow-up choices for the player."""
        context_text = (
            f"Player: {player_name}\n"
            f"NPC: {npc_name}\n"
            f"Location: {location}\n"
            f"Last NPC line: {last_npc_line[:200]}\n\n"
            "Generate 3-4 conversational follow-up choices for the player. "
            "Each should be a short quoted reply (something you'd say to this NPC). "
            "Vary them: one polite, one bold/aggressive, one inquisitive."
        )

        try:
            result = self.generate_structured(
                system_prompt=self.choice_prompt,
                user_prompt=context_text,
                schema=ChoicesResponse,
            )
            return result.choices
        except Exception as exc:
            log.warning("dialogue choices generation failed: %s", exc)
            return [
                "Tell me more about yourself",
                f"What do you know about {location}?",
                "I need your help with something.",
                "Leave quietly",
            ]

    def generate_choices(self, ctx: dict) -> list[str]:
        """Generate in-world DM choice options for the player.

        Kept for /game/start bootstrap compatibility. Procedural fallback on failure.
        """
        parts = []
        for key in ("player_name", "faction", "motivation", "location", "turn"):
            val = ctx.get(key)
            if val:
                parts.append(f"{key}: {val}")
        if outcome := ctx.get("outcome"):
            parts.append(f"last_outcome: {outcome}")
        story = ctx.get("story_events") or ctx.get("narrative", "")
        if story:
            parts.append(f"recent: {story[-300:]}")
        context_text = "\n".join(parts) + "\n\nGenerate action options."

        try:
            result = self.generate_structured(
                system_prompt=self.choice_prompt,
                user_prompt=context_text,
                schema=ChoicesResponse,
            )
            return result.choices
        except Exception as exc:
            log.warning("choices generation failed, using procedural fallback: %s", exc)
            loc = ctx.get("location", "the area")
            return [
                f"Examine the area around you",
                f"Check your inventory and notes",
                f"Search for other people or NPCs nearby",
                f"Move in a new direction",
            ]
