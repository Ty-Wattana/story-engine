"""Game loop — main turn cycle, action resolution, and outcome rendering."""

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich import box

from src.state import Player, WorldState, PlayerStats, StateManager
from src.schemas import CharacterProfile
from src.llm_client import LLMClient
from src.lore import LoreParser, create_validator
from src.action_engine import resolve_action

from src.ui.status import show_status
from src.ui.output import display_outcome, show_dm_choices
from src.narrative import generate_scene_description, StoryMemory, StoryEvent

console = Console()


def game_loop(player: Player, world: WorldState, llm: LLMClient) -> None:
    """Main turn-based loop. Keeps running until the player quits."""
    state_mgr = StateManager(player, world)

    console.print("\n[blue]--- Game Loop Starting ---[/blue]")
    console.print("[dim]Type any action, or choose a numbered option shown.[/]")
    console.print("[dim]Type 'quit' at the prompt to exit.[/dim]\n")

    # First status dump so player sees what they have on turn 1
    show_status(state_mgr)

    last_input: str | None = None
    memory = StoryMemory()

    while True:
        world.advance_turn()

        # --- Snapshot ONCE this turn ---
        snapshot = state_mgr.snapshot()

        # --- Opening scene description (DM sets the stage before offering choices) ---
        loc = snapshot.get("location", "Unknown")
        if world.turn_count == 1:
            # Turn 1 — fresh introductory scene for a new character
            intro_context = (
                "You are the dungeon master narrator for a dark fantasy RPG.\n"
                "Write an atmospheric introductory paragraph (5-10 sentences) for a player\n"
                "entering this location for the first time. Include sensory details and end\n"
                "with a hook or tension point suggesting what might happen next.\n"
                "Do NOT offer choices or ask what the player does — just set the scene."
                f"\n\nLocation: [{loc}]\nFirst turn — introduce the setting. Describe the place, the mood, and any notable features. Make it feel alive and immersive."
            )
            try:
                console.print("[dim][generating intro…][/dim]")
                narrative = llm.generate_flavor_text(intro_context,
                    instruction="DM introduces the starting location in 2-3 atmospheric sentences. End with a hook.")
                if narrative:
                    console.print(f"\n[dim]{narrative}[/dim]")
            except Exception as e:
                console.print(f"[dim][intro skipped] {e}[/dim]")
        else:
            # Post-turn scene — set the stage for what comes next
            recent = memory.get_recent(5)  # last 5 events for context
            recent_events = [StoryEvent(**e) if isinstance(e, dict) else e for e in recent]
            try:
                def scene_prompt_fn(ctx_str: str) -> str:
                    return llm.generate_flavor_text(context=ctx_str, instruction="DM sets the scene in 2-3 atmospheric sentences. Ground it in what just happened and end with a hint of what might come next.")
                narrative = generate_scene_description(loc, recent_events, scene_prompt_fn)
            except Exception as e:
                console.print(f"[dim][scene generation skipped] {e}[/dim]")
                narrative = None
            if narrative:
                console.print(f"\n[dim]{narrative}[/dim]")

        # --- DM choices ---
        try:
            choices_list = llm.generate_choices(state_mgr.choices_snapshot())
        except Exception as e:
            console.print(f"[dim][choice-gen failed, continuing with free-text only] {e}[/dim]")
            choices_list = []

        if choices_list:
            show_dm_choices(choices_list)
            console.print("[yellow]Type the number to select, or type your own action.[/]")

        user_input_raw = console.input("\n[bold green]>[/bold green] ").strip()

        if user_input_raw.lower() in ("quit", "q", "exit"):
            console.print("\n[yellow]Goodbye![/yellow]")
            break

        # Debounce: skip duplicate inputs instantly
        if user_input_raw == last_input:
            console.print("[dim](duplicate — skipped. The previous turn already resolved this input.)[/dim]")
            show_status(state_mgr)
            continue
        last_input = user_input_raw

        # If they typed a number and choices were shown, use that option
        parsed_input = user_input_raw
        if choices_list and user_input_raw.isdigit():
            idx = int(user_input_raw) - 1
            if 0 <= idx < len(choices_list):
                parsed_input = choices_list[idx]

        # --- Parse action ---
        try:
            console.print("[dim][parsing your action…][/dim]")
            action_result_raw = llm.generate_action_result(parsed_input, snapshot)
        except Exception as e:
            console.print(f"\n[red]Action parse failed: {e}[/red]")
            show_status(state_mgr)
            continue

        # --- Resolve the action mechanically ---
        stat_name = getattr(action_result_raw.modifiers, "target_stat", None)
        adv = getattr(action_result_raw.modifiers, "advantage", "none") or "none"
        tool_used = getattr(action_result_raw.modifiers, "tool_used", None)

        if stat_name:
            raw_stat_score = player.stats.__dict__.get(stat_name.lower(), 10)
        else:
            raw_stat_score = 10
        console.print(f"[dim][rolling dice…][/dim]")

        resolve_output = resolve_action(
            action_type=action_result_raw.action_type,
            stat_name=stat_name,
            stat_value=raw_stat_score,
            tool_modifier=1 if tool_used else 0,
            advantage=adv,
            proficiency=state_mgr.proficiency,
            world_context=snapshot.get("location", ""),
        )

        # Apply engine effects — for ALL outcomes
        effects_applied = state_mgr.apply_outcome_effects(resolve_output["outcome_level"], action_result_raw.action_type)
        effect_display = [f"{k} → {v}" for k, v in effects_applied.items()] if effects_applied else ["(no mechanical change this turn)"]

        # Check reputation thresholds
        new_unlocks = state_mgr.player.check_rep_thresholds()
        unlock_display: list[str] = []
        if new_unlocks:
            for unlock in new_unlocks:
                label = unlock.replace("_", " ").title()
                unlock_display.append(f"[magenta]🔓 {label}[/magenta]")

        # --- Generate flavor text ---
        try:
            inventory_list = ", ".join(state_mgr.player.inventory[-3:]) or "(empty)"
            rep_items = ", ".join(f"{k}(+{v})" for k, v in list(state_mgr.player.reputation.items())[-2:]) or "(none)"
            new_effects = "; ".join(effect_display) if effect_display else "(no effects)"
            flavor_context = (
                f"You just {action_result_raw.intent} at '{world.current_location}'.\n"
                f"Inventory now: {inventory_list}\n"
                f"Reputation: {rep_items}\n"
                f"Effects this turn: {new_effects}\n"
                f"Outcome: {resolve_output['outcome_level']}\n"
                f"Turn: {world.turn_count}"
            )
            console.print("[dim][generating narrative…][/dim]")
            narrative = llm.generate_flavor_text(
                context=flavor_context,
                instruction="DM narrates the outcome of this action in 1-2 sentences. Ground it in what just happened — reference specific items, NPCs, or locations that changed."
            )
        except Exception as e:
            console.print(f"[dim][flavor-text failed] {e}[/dim]")
            narrative = None

        # --- Display outcome ---
        resolve_output["turn"] = world.turn_count
        resolve_output["advantage"] = adv
        display_outcome(resolve_output, effect_display, narrative)

        if unlock_display:
            console.print("\n[yellow]═══ Thresholds Reached ═══[/yellow]")
            for u in unlock_display:
                console.print(f"  {u}")

        # Record event for next turn's scene description
        memory.add_event(StoryEvent(
            turn=world.turn_count,
            player_name=player.name,
            intent=action_result_raw.intent,
            action_type=action_result_raw.action_type,
            target_entity=action_result_raw.target_entity,
            dice_roll=resolve_output["dice_roll"],
            final_score=resolve_output["final_score"],
            target_dc=resolve_output["target_dc"],
            success=resolve_output.get("success", False),
            outcome_level=resolve_output["outcome_level"],
            narrative_summary=action_result_raw.intent,
        ))

        show_status(state_mgr)


def main() -> None:
    llm = LLMClient()
    world = WorldState(current_location="Starting Village")

    from src.engine.creation import initialize_game
    player = initialize_game(llm)

    if player:
        console.print("\n[blue]--- Engine Initialized. Ready for Game Loop. ---[/blue]")
        game_loop(player, world, llm)
    else:
        console.print("\n[red]Failed to initialize character. Exiting...[/red]")
        sys.exit(1)
