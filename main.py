import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.abspath("."))

from src.state import Player, WorldState, PlayerStats, StateManager
from src.schemas import CharacterProfile
from src.llm_client import LLMClient
from src.lore_validator import LoreParser, LoreValidator, create_validator
from src.action_engine import resolve_action

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich import box

console = Console()


# ---------------------------------------------------------------------------
# Phase 1: Status display helpers
# ---------------------------------------------------------------------------

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
    return t


def _show_status(state_mgr: StateManager) -> None:
    """Full status header for each turn."""
    w = state_mgr.world
    p = state_mgr.player

    # Location banner
    console.print(f"\n[bold cyan]═══ [{w.current_location.capitalize()}] ═══[/bold cyan]\n")

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
    console.print(rep_panel)


# ---------------------------------------------------------------------------
# Phase 2: Display resolved action results
# ---------------------------------------------------------------------------

def _display_outcome(result: dict, effects_applied: list[str], flavor_text: str | None = None) -> None:
    """Display a unified outcome panel for success / partial / failure."""

    lines: list[str] = []
    outcome = result["outcome_level"]
    dice_roll = result["dice_roll"]
    modifier = result["modifier"]
    final_score = result["final_score"]
    dc = result["target_dc"]
    advantage = result.get("advantage", "none")

    # Compact turn summary (shown after the panel as a one-liner)
    compact: list[str] = []

    # Dice line
    adv_marker = f" ({advantage})" if advantage != "none" else ""
    hit_color = "[green]" if result["success"] else "[red]"
    hit_text = "HIT" if result["success"] else "MISS"
    mod_str = f"{modifier:+d}"  # e.g. +3 or -2
    lines.append(f"[dim]Roll:[/dim] d20[{dice_roll}]{adv_marker} {mod_str} → {final_score} [{hit_color}{hit_text}[/] (DC={dc})")

    # Outcome level
    outcome_colors = {
        "crit_fresh": "[bold magenta]",
        "success": "[green]",
        "partial": "[yellow]",
        "failure": "[red dim]",
        "crit": "[bold green]",
    }
    color = outcome_colors.get(outcome, "[white]")
    lines.append(f"[{color}]Outcome: {outcome.replace('_', ' ').upper()}[/]")

    # Effects summary (shown for ALL outcomes now)
    if effects_applied:
        lines.append("")
        lines.append("[bold]Effects:[/]")
        for e in effects_applied:
            lines.append(f"  [cyan]{e}[/cyan]")

    # Flavor text (shown for all outcomes)
    if flavor_text:
        lines.append("")
        lines.append(f"[italic][dim]The outcome:[/dim] {flavor_text}[/italic]")

    panel_border = "green" if result["success"] else ("yellow" if outcome == "partial" else "red")
    console.print(Rule("[bold]" + outcome.replace("_", " ").upper() + "[/]", style=panel_border))
    for line in lines:
        console.print(line)

    # Compact summary (shown at very end of turn, before next prompt)
    compact = f"[dim][Turn {result.get('turn', '?')}][/dim]"
    if result["success"]:
        if outcome == "crit_fresh":
            compact += f" [bold magenta]CRIT![/]"
        elif outcome == "crit":
            compact += f" [bold green]CRIT![/]"
        else:
            compact += f" [green]✓[/]"
    else:
        if outcome == "partial":
            compact += f" [yellow]~[/]"
        else:
            compact += f" [red]✗[/]"
    console.print(compact)


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
# Phase 5: The real game loop (revamped)
# ---------------------------------------------------------------------------

def _show_dm_choices(options: list[str]) -> None:
    """Present DM choices as a numbered table."""
    if not options:
        return
    t = Table(box=box.SIMPLE, title="[bold yellow]Available Actions[/bold yellow]", show_header=False)
    for i, opt in enumerate(options, 1):
        t.add_row(f"[bold cyan]{i}:[][dim]•[/dim]", opt)
    console.print(Panel(t, border_style="yellow", expand=True))


def game_loop(player: Player, world: WorldState, llm: LLMClient) -> None:
    """Main turn-based loop. Keeps running until the player quits.

    Revamped flow per turn:
      1. advance_turn() + snapshot (once)
      2. LLM choices (if state changed or first time)
      3. Parse input via LLM -> ActionParseResult
      4. resolve_action() with advantage/proficiency
      5. apply_engine_effects(outcome_level, action_type) — for ALL outcomes
      6. generate_flavor_text() — for ALL outcomes
      7. display outcome panel + compact summary
    """
    state_mgr = StateManager(player, world)

    console.print("\n[blue]--- Game Loop Starting ---[/blue]")
    console.print("[dim]Type any action, or choose a numbered option shown.[/]")
    console.print("[dim]Type 'quit' at the prompt to exit.[/dim]\n")

    # First status dump so player sees what they have on turn 1
    _show_status(state_mgr)

    while True:
        world.advance_turn()

        # --- Snapshot ONCE this turn ---
        snapshot = state_mgr.snapshot()

        # --- DM choices (uses lightweight snapshot to avoid LLM echoing state text) ---
        try:
            choices_list = llm.generate_choices(state_mgr.choices_snapshot())
        except Exception as e:
            console.print(f"[dim][choice-gen failed, continuing with free-text only] {e}[/dim]")
            choices_list = []

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
            action_result_raw = llm.generate_action_result(parsed_input, snapshot)
        except Exception as e:
            console.print(f"\n[red]Action parse failed: {e}[/red]")
            _show_status(state_mgr)
            continue

        # --- Resolve the action mechanically (using advantage from parser + computed proficiency) ---
        stat_name = getattr(action_result_raw.modifiers, "target_stat", None)
        adv = getattr(action_result_raw.modifiers, "advantage", "none") or "none"
        tool_used = getattr(action_result_raw.modifiers, "tool_used", None)

        resolve_output = resolve_action(
            action_type=action_result_raw.action_type,
            stat_name=stat_name,
            tool_modifier=1 if tool_used else 0,       # placeholder: real tool bonus from lookup
            advantage=adv,
            proficiency=state_mgr.proficiency,
            world_context=snapshot.get("location", ""),
        )

        # Always apply engine effects — for ALL outcomes (success / partial / failure)
        effects_applied = state_mgr.apply_outcome_effects(resolve_output["outcome_level"], action_result_raw.action_type)
        effect_display = [f"{k} → {v}" for k, v in effects_applied.items()] if effects_applied else ["(no mechanical change this turn)"]

        # Always generate flavor text — for ALL outcomes. Use rich dynamic state so the LLM
        # never recycles the same prose (it gets fresh facts to work with each turn).
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
            narrative = llm.generate_flavor_text(
                context=flavor_context,
                instruction=f"DM narrates the outcome of this action in 1-2 sentences. Ground it in what just happened — reference specific items, NPCs, or locations that changed."
            )
        except Exception as e:
            console.print(f"[dim][flavor-text failed] {e}[/dim]")
            narrative = None

        # --- Display unified outcome panel ---
        resolve_output["turn"] = world.turn_count
        resolve_output["advantage"] = adv
        _display_outcome(resolve_output, effect_display, narrative)

        # Refresh status for next turn
        _show_status(state_mgr)


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
