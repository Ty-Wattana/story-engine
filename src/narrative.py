"""Narrative Director -- tracks long-term plot hooks and feeds them into LLM context.

Provides a master quest pool, condition-based activation, and turn-count
progression so NPCs can organically reference active plot hooks.
"""
from __future__ import annotations

from src.state import QuestNode, QuestStatus, WorldState, Player


# ---------------------------------------------------------------------------
# Hardcoded quest pool (expand with lore-driven quests later)
# ---------------------------------------------------------------------------

def _master_pool() -> list[QuestNode]:
    """Return the canonical quest pool -- every quest starts as HIDDEN."""
    return [
        QuestNode(
            quest_id="missing_blacksmith",
            title="The Missing Blacksmith",
            description="Old man Hemmington, the village blacksmith, hasn't been seen in three days. Last known to be heading toward the old cellar beneath the market.",
            current_objective="Find Hemmington's journal from his workshop",
            status=QuestStatus.HIDDEN,
            flags={"trigger_location": "Old Town Square", "journal_found": False},
        ),
        QuestNode(
            quest_id="clear_cellar",
            title="Clear the Cellar",
            description="Goblins have been spotted in the cellar beneath the market. The guild has put a bounty on them.",
            current_objective="Defeat the goblin pack (0/5)",
            status=QuestStatus.HIDDEN,
            flags={"trigger_location": "Old Town Square", "goblins_killed": 0},
        ),
    ]


class NarrativeDirector:
    """Manages quest lifecycle and context injection for LLM prompts."""

    @staticmethod
    def get_master_quest_pool() -> list[QuestNode]:
        """Return the full set of discoverable quests (all start HIDDEN)."""
        return _master_pool()

    @staticmethod
    def update_quests(
        world: WorldState,
        player: Player,
        current_location: str,
    ) -> None:
        """Activate / advance / fail quests based on game state.

        Mutates *world* in-place (modifies world.quests directly).
        """
        pool = {q.quest_id: q for q in _master_pool()}

        # --- Activation: HIDDEN -> ACTIVE when player reaches trigger location ---
        for quest_id, proto in pool.items():
            if quest_id not in world.quests and proto.flags.get("trigger_location") == current_location:
                new_quest = QuestNode(
                    quest_id=quest_id,
                    title=proto.title,
                    description=proto.description,
                    current_objective=proto.current_objective,
                    status=QuestStatus.ACTIVE,
                    flags=dict(proto.flags),          # shallow copy so mutations don't affect proto
                )
                world.quests[quest_id] = new_quest

        # --- Progression: advance ACTIVE quests by turn count & inventory --------
        for quest in world.quests.values():
            if quest.status != QuestStatus.ACTIVE:
                continue

            if quest.quest_id == "clear_cellar":
                # After 10 turns, objective shifts from finding goblins to confronting them
                if world.turn_count >= 10 and "goblin_ear" in player.inventory:
                    remaining = max(0, 5 - quest.flags.get("goblins_killed", 0))
                    quest.current_objective = f"Report to the guild master ({5 - remaining} goblins killed)"
                    if remaining == 0:
                        quest.status = QuestStatus.COMPLETED
                        quest.current_objective = "Complete report to guild master"

            if quest.quest_id == "missing_blacksmith":
                # After 5 turns, objective shifts from journal-finding to searching the cellar
                if world.turn_count >= 5 and not quest.flags.get("journal_found", False):
                    quest.current_objective = f"Search the cellar beneath the market ({world.turn_count}/5 turns elapsed)"
                elif quest.flags.get("journal_found", False):
                    quest.status = QuestStatus.COMPLETED
                    quest.current_objective = "Deliver Hemmington's journal to the mayor"

    @staticmethod
    def format_quest_context(world: WorldState) -> str:
        """Return a text block of active quests for LLM context injection.

        Returns empty string when no quests are active so callers can always
        concatenate without conditional logic.
        """
        active = [q for q in world.quests.values() if q.status == QuestStatus.ACTIVE]
        if not active:
            return ""
        lines = ["[ACTIVE PLOT HOOKS]"]
        for q in active:
            lines.append(f"- **{q.title}**: {q.current_objective}")
        return "\n".join(lines)
