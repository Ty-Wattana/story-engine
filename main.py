import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.abspath("."))

from src.state import Player, WorldState, PlayerStats, StateManager
from src.schemas import CharacterProfile, ActionResult
from src.llm_client import LLMClient
from src.lore_validator import LoreParser, LoreValidator, create_validator
from src.action_engine import resolve_action, apply_mechanical_effects

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich import box

console = Console()


# ---------------------------------------------------------------------------
# Phase 1: Status display helpers
# ---------------------------------------------------------------------------

def _show_player_stats(state_mgr: StateManager) -> None:
    """Display player stats as a Rich panel."""
    p = state_mgr.player
    s = p.stats

    rows = [
        f"[cyan]Str:[/cyan] {s.strength:+3} ({s.bonus('strength'):+d})",
        f"[cyan]Dex:[/cyan] {s.dexterity:+3} ({s.bonus('dexterity'):+d})",
        f"[cyan]Int:[/cyan] {s.intelligence:+3} ({s.bonus('intelligence'):+d})",
        f"[cyan]Wis:[/cyan] {s.wisdom:+3} ({s.bonus('wisdom'):+d})",
        f"[cyan]Con:[/cyan] {s.constitution:+3} ({s.bonus('constitution'):+d})",
        f"[cyan]Cha:[/cyan] {s.charisma:+3} ({s.bonus('charisma'):+d})",
    ]

    table = Table(box=box.SIMPLE, show_header=False, header_style="bold magenta")
    for r in rows:
        table.add_row(r)

    console.print(Panel(table, title="[bold white]Stats", border_style="blue"))


def _show_inventory(state_mgr: StateManager) -> None:
    """Display inventory as a compact list."""
    inv = state_mgr.player.inventory
    if not inv:
        console.print("[dim]Inventory: (empty)[/dim]")
        return
    t = Table(box=box.SIMPLE, title="[white]Inventory", show_header=False)
    for item in inv:
        t.add_row(f"  •  {item}")
    console.print(t)


def _show_status(state_mgr: StateManager) -> None:
    """Full status header for each turn."""
    w = state_mgr.world

    # Location banner
    console.print(f"\n[bold cyan]═══ [{w.current_location.capitalize()}][bold cyan] ═══[/bold cyan]\n")

    with console.status("[dim]Loading player data...[/dim]") as st:
        console.clear()  # clear status text but keep panel
        st.stop()

    p = state_mgr.player
    header = (
        f"[bold]{p.name}[/bold] — [bold]Faction:[/bold] {p.faction}\n"
        f"[bold]Goal:[/bold] {p.goal}  |  [bold]Motivation:[/bold] {p.motivation}\n"
        f"[dim](Turn {w.turn_count})[/dim]"
    )
    console.print(Panel(header, title="[green]Player Status", border_style="green"))

    stats_panel = Panel(_build_stats_table(state_mgr), title="[cyan]Stats", border_style="blue")
    inv_panel = Panel(_build_inventory_table(state_mgr), title="[dim]Inventory", border_style="dim")
    rep_items = [f"  •  {k}: +{v}" for k, v in p.reputation.items()]
    if not rep_items:
        rep_panel = Panel("[dim]Reputation: (none yet)[/dim]", title="[magenta]Reputation", border_style="magenta")
    else:
        rep_panel = Panel("\n".join(rep_items), title="[magenta]Reputation", border_style="magenta")

    console.print(stats_panel)
    console.print(inv_panel)


def _build_stats_table(state_mgr: StateManager) -> Table:
    s = state_mgr.player.stats
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    for name in ("strength", "dexterity", "intelligence", "wisdom", "constitution", "charisma"):
        val = getattr(s, name)
        bonus = s.bonus(name)
        t.add_row(f"[bold green]{name.capitalize():>10}[/] ", f"  {val:>3}  ({bonus:+d})")
    return t


def _build_inventory_table(state_mgr: StateManager) -> Table:
    inv = state_mgr.player.inventory
    if not inv:
        return Panel("[dim](empty)[/dim]", box=box.SIMPLE)
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    for item in inv:
        t.add_row("  •  ", item)
    # Strip panel wrapper — return the raw string
    return t


# ---------------------------------------------------------------------------
# Phase 2: Display resolved action results
# ---------------------------------------------------------------------------

def _display_roll(dice_roll: int, modifier: int, final_score: int, dc: int) -> None:
    """Show dice roll result as an inline narrative-style line."""
    if final_score >= dc:
        marker = "[green]✓ HIT[/green]"
    else:
        marker = "[red]✗ MISS[/red]"
    console.print(f"  [dim]Roll:[/dim] d20[{dice_roll:+3}] {'+' if modifier >= 0 else ''}{modifier} → {final_score} [bold {marker}] (DC={dc})")


def _display_action_result(result: dict) -> None:
    """Print the full resolve output in a Rich panel."""
    lines = []

    # Roll line
    lines.append(f"[dim]Action:[/dim] {result['action_type']}  |  [dim]Verb:[/dim] {result.get('verb', '?')}")
    if result.get("target_entity"):
        lines.append(f"[dim]Target:[/dim] {result['target_entity']}")

    # Roll display
    _display_roll(result["dice_roll"], result["modifier"], result["final_score"], result["target_dc"])

    # Outcome level
    outcome = result["outcome_level"]
    level_colors = {
        "crit_fresh": "bold green",
        "success": "green",
        "partial": "yellow",
        "failure": "red dim",
        "crit": "bold magenta",
    }
    color = level_colors.get(outcome, "white")
    lines.append(f"[{color}]Outcome: {outcome.replace('_', ' ').upper()}[/]")

    # Effects summary
    effects = result.get("mechanical_effect", {})
    if effects:
        lines.append("\n[bold]Effects:[/]")
        for k, v in effects.items():
            lines.append(f"  [cyan]{k}[/cyan] → {v}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Old Phase (unchanged): initialization & character creation
# ---------------------------------------------------------------------------

def initialize_game(llm: LLMClient) -> Player:
    console.print("[blue]Welcome to the Story Engine PoC[/blue]")
    console.print("[blue]Who are you, and what do you seek?[/blue]\n")

    backstory = console.input("[blue]>[/blue] Enter your backstory: ")

    system_prompt = llm._load_system_prompt()

    console.print("\n[yellow]=== System Prompt Preview ===[/yellow]")
    console.print(system_prompt[:500] + "[\n\n")

    try:
        with console.status("[yellow]Parsing background with local LLM...[/yellow]"):
            profile = llm.generate_structured(system_prompt, backstory, CharacterProfile)
    except Exception as e:
        console.print(f"\n[red]Error parsing your backstory: {e}[/red]")
        console.print("[yellow]Please try with a clearer backstory or check your network.[/yellow]")
        return None

    # === Lore Validation ===
    console.print("\n[yellow]=== Validating against lore database ===[/yellow]")
    parser = LoreParser(console)
    parser.parse_markdown("data/lore_summary.md")
    validator = create_validator("data/lore_summary.md")

    is_valid, conflicts, suggestions = validator.validate_input(backstory)

    if not is_valid:
        console.print(Panel(
            "[red]The extracted character profile conflicts with established lore.[/red]\n"
            f"[yellow]Conflicts found: {len(conflicts)}[/yellow]",
            title="Lore Conflict Detected",
            border_style="red",
            box=box.DOUBLED
        ))

        max_attempts = 5
        attempts = 0
        while conflicts and attempts < max_attempts:
            attempts += 1
            console.print(f"\n[gray]Attempt {attempts}/{max_attempts}[/gray]")
            for i, conflict in enumerate(conflicts, 1):
                severity_marker = {"error": "[red][!]", "warning": "[yellow][!]", "info": "[cyan][i]"}.get(
                    conflict.severity, "[?]"
                )
                console.print(f"{severity_marker} {conflict.conflict}")

            if suggestions:
                table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
                table.add_column("Option", style="cyan")
                for i, suggestion in enumerate(suggestions, 1):
                    table.add_row(f"[i]Option {i}:[/i] {suggestion}")
                console.print(table)

            response = console.input(
                "\n[bold yellow]How would you like to proceed?[/bold yellow]\n"
                "[yellow] (a) Accept suggested revision[/yellow]\n"
                "[yellow] (r) Revise the input yourself[/yellow]\n"
                "[yellow] (s) Skip this validation[/yellow]\n"
                "> [/yellow]"
            )

            if "a" in response.lower():
                conflicts = []
                suggestions = []
            elif "r" in response.lower():
                console.print("\n[blue]=== Please revise your backstory ===[/blue]")
                revised_backstory = console.input("> ")
                is_valid, conflicts, suggestions = validator.validate_input(revised_backstory)
            else:
                console.print("\n[yellow]Skipping validation. Using extracted profile as-is.[/yellow]")
                conflicts = []
                suggestions = []

        if conflicts and attempts >= max_attempts:
            console.print("\n[red]Could not resolve lore conflicts after multiple attempts.[/]")
            console.print("[yellow]Character creation failed. Exiting...[/yellow]")
            return None

    player = Player(
        name="Protagonist",
        faction=profile.origin_faction,
        motivation=profile.motivation,
        goal=profile.goal,
    )
    # Assign some baseline stats when generating from profile
    if "fight" in backstory.lower() or "strength" in backstory.lower():
        player.stats.strength = 14
    if "dodge" in backstory.lower() or "sneak" in backstory.lower():
        player.stats.dexterity = 14
    if "lore" in backstory.lower() or "wisdom" in backstory.lower():
        player.stats.intelligence = 13
        player.stats.wisdom = 13

    console.print("\n[green]=== Character Established ===[/green]")
    console.print(f"[italic]Faction:[/italic] [bold white]{player.faction}[/bold white]")
    console.print(f"[italic]Motivation:[/italic] [bold red]{player.motivation}[/bold red]")
    console.print(f"[italic]Goal:[/italic] [bold cyan]{player.goal}[/bold cyan]")

    return player


# ---------------------------------------------------------------------------
# Phase 5: The real game loop
# ---------------------------------------------------------------------------

def _show_dm_choices(options: list[str]) -> int | None:
    """Present DM choices as a numbered table. Returns the user's selection index (1-based) or None."""
    if not options:
        return None
    t = Table(box=box.SIMPLE, title="[bold yellow]Available Actions[/bold yellow]", show_header=False)
    for i, opt in enumerate(options, 1):
        t.add_row(f"[bold cyan]{i}:[][dim]•[/dim]", opt)
    console.print(Panel(t, border_style="yellow", expand=True))
    return None  # Let the caller handle input


def game_loop(player: Player, world: WorldState, llm: LLMClient) -> None:
    """Main turn-based loop. Keeps running until the player quits."""
    state_mgr = StateManager(player, world)

    console.print("\n[blue]--- Game Loop Starting ---[/blue]")
    console.print("[dim]Type any action, or choose a numbered option shown.[/]")
    console.print("[dim]Type 'quit' at the prompt to exit.[/dim]\n")

    # First status dump so player sees what they have on turn 1
    _show_status(state_mgr)

    while True:
        world.advance_turn()

        choices_list: list[str] = []
        try:
            states_snapshot = state_mgr.snapshot()
            choices_list = llm.generate_choices(states_snapshot)
        except Exception as e:
            console.print(f"[dim][choice-gen failed, continuing with free-text only]{e}[/dim]")

        # Show choices alongside a prompt
        if choices_list:
            _show_dm_choices(choices_list)
            console.print("[yellow]Type the number to select, or type your own action.[/]")

        user_input = console.input("\n[bold green]>[/bold green] ").strip()

        if user_input.lower() in ("quit", "q", "exit"):
            console.print("\n[yellow]Goodbye![/yellow]")
            break

        # If they typed a number and choices were shown, treat that as the chosen option
        parsed_input = user_input
        if choices_list and user_input.isdigit():
            idx = int(user_input) - 1
            if 0 <= idx < len(choices_list):
                parsed_input = choices_list[idx]

        # --- Parse action ---
        try:
            action_result_raw = llm.generate_action_result(parsed_input, state_mgr.snapshot())
            # Also get stat bonus from player for the modifiers
            stat_name = getattr(action_result_raw.modifiers, "target_stat", None)
            stat_modifier = 0
            if stat_name:
                stat_modifier = player.stats.bonus_for_choice() if not hasattr(player.stats, stat_name) else player.stats.bonus(stat_name)

        except Exception as e:
            console.print(f"\n[red]Action parse failed: {e}[/red]")
            continue

        # --- Resolve the action mechanically ---
        mod_kwargs = {}
        if stat_name:
            mod_kwargs["target_stat"] = stat_name

        resolve_output = resolve_action(
            action_type=action_result_raw.action_type,
            modifiers_info=mod_kwargs,
            world_context=state_mgr.snapshot().get("location", ""),
        )
        # Inject computed modifier into the output dict
        resolve_output["modifier"] += stat_modifier
        resolve_output["final_score"] = resolve_output["dice_roll"] + resolve_output["modifier"]

        if not resolve_output["success"]:
            console.print("\n[yellow]Action failed. Your attempt does not succeed.[/yellow]")
            _show_status(state_mgr)  # refresh for feedback
            continue

        # --- Apply mechanical effects to state ---
        try:
            state_mgr.apply_effect(resolve_output.get("mechanical_effect", {}))
        except Exception as e:
            console.print(f"[dim][effect-apply failed]{e}[/dim]")

        _display_action_result(construct_action_result_dict(action_result_raw, resolve_output))

        # --- Flavor text via LLM ---
        try:
            narrative = llm.generate_flavor_text(
                context=f"action: {action_result_raw.intent}, outcome: success, location: {world.current_location}",
                instruction=f"Dungeon master describes the successful outcome of '{parsed_input}'. Keep under 3 sentences."
            )
            console.print(f"\n[italic]The outcome: [white]{narrative}[/italics]")
        except Exception as e:
            console.print(f"[dim][flavor-text failed] {e}[/dim]")

        # Refresh status for next turn
        _show_status(state_mgr)


def construct_action_result_dict(parsed, resolved: dict) -> dict:
    """Merge parsed action fields into a combined output dict."""
    return {
        **resolved,
        "intent": parsed.intent,
        "target_entity": parsed.target_entity,
        "is_combat": parsed.is_combat,
        "action_type": parsed.action_type,
        "verb": parsed.verb,
        "modifiers": {
            "target_stat": getattr(parsed.modifiers, "target_stat", None),
            "tool_used": getattr(parsed.modifiers, "tool_used", None),
            "advantage": getattr(parsed.modifiers, "advantage", "none"),
        },
    }


def main():
    llm = LLMClient()
    world = WorldState(current_location="Starting Village")

    player = initialize_game(llm)

    if player:
        console.print("\n[blue]--- Engine Initialized. Ready for Game Loop. ---[/blue]")
        game_loop(player, world, llm)  # NEW: actual turn loop
    else:
        console.print("\n[red]Failed to initialize character. Exiting...[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
