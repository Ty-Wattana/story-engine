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

    # Temporary stat bonuses/penalties from crit effects and failure penalties
    temp_bonuses = {
        "strength": getattr(s, "_str_bonus", 0),
        "dexterity": getattr(s, "_dex_bonus", 0),
        "intelligence": getattr(s, "_int_bonus", 0),
        "wisdom": getattr(s, "_wisdom_bonus", 0),
        "constitution": getattr(s, "_con_penalty", 0),
        "charisma": getattr(s, "_cha_bonus", 0),
    }

    for name in ("strength", "dexterity", "intelligence", "wisdom", "constitution", "charisma"):
        val = getattr(s, name)
        bonus = s.bonus(name) + temp_bonuses[name]   # effective bonus including temporary boosts
        display_val = f"  {val:>3}  ({bonus:+d})"
        if temp_bonuses[name]:
            sign = "+" if temp_bonuses[name] > 0 else ""
            display_val += f" [dim]{sign}{temp_bonuses[name]}[/dim]"
        t.add_row(f"[bold green]{name.capitalize():>10}[/] ", display_val)
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
    console.print(system_prompt[:500] + "[\n\n", markup=False)

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

    result = validator.validate_input(backstory)
    is_valid = result.is_valid
    conflicts = result.conflicts
    suggestions = result.suggestions
    # Track the currently-used profile (starts as what was parsed from user's input)
    current_profile = profile
    current_backstory = backstory

    if not is_valid:
        console.print(Panel(
            "[red]The extracted character profile conflicts with established lore.[/red]\n"
            f"[yellow]Conflicts found: {len(conflicts)}[/yellow]",
            title="Lore Conflict Detected",
            border_style="red",
            box=box.DOUBLE
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
                console.print(f"{severity_marker} {conflict.fact.fact}")

            # Build revision options (concrete backstories generated by validator's LLM)
            if suggestions:
                table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
                table.add_column("Option", style="cyan")
                for i, suggestion in enumerate(suggestions, 1):
                    # Truncate long suggestions for display
                    display = suggestion[:200] + "..." if len(suggestion) > 200 else suggestion
                    table.add_row(f"[i]Option {i}:[/i] {display}")

                revision_options = suggestions

            response = console.input(
                "\n[bold yellow]How would you like to proceed?[/bold yellow]\n"
                "[yellow] (a) Accept suggested revision option below[/yellow]\n"
                "[yellow] (r) Revise the input yourself[/yellow]\n"
                "[yellow] (s) Skip this validation[/yellow]\n"
                "> "
            )

            if "a" in response.lower():
                if not revision_options:
                    console.print("\n[yellow]No suggestions available. Please revise manually.[/yellow]")
                    continue

                # Show concrete revision options for the user to pick from
                console.print("\n[bold]Choose a revision option (1, 2, or 3):[/bold]")
                for i, opt in enumerate(revision_options[:3], 1):
                    safe_opt = opt if not opt.startswith("Revised:") else opt[len("Revised:"):].strip()
                    console.print(f"  [cyan]{i}.[/cyan] {safe_opt}")

                choice = console.input("> ").strip()
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(revision_options):
                        revised_backstory = revision_options[idx]
                        console.print(f"\n[yellow]You selected option {idx+1}. Parsing...[/yellow]")

                        # Parse the selected revision through the LLM to get a new profile
                        rev_system_prompt = llm._load_system_prompt()
                        revised_profile = llm.generate_structured(rev_system_prompt, revised_backstory, CharacterProfile)

                        # Re-validate
                        result = validator.validate_input(revised_backstory)
                        is_valid = result.is_valid
                        conflicts = result.conflicts
                        suggestions = result.suggestions

                        if is_valid:
                            current_profile = revised_profile
                            current_backstory = revised_backstory
                    else:
                        console.print("[yellow]Invalid option. Please try again.[/yellow]")
                except ValueError:
                    console.print("[yellow]Invalid input. Expected a number (1, 2, or 3).[/yellow]")
            elif "r" in response.lower():
                console.print("\n[blue]=== Please revise your backstory ===[/blue]")
                revised_backstory = console.input("> ")

                # Re-parse the revised backstory through the LLM to get a new profile
                system_prompt = llm._load_system_prompt()
                try:
                    revised_profile = llm.generate_structured(system_prompt, revised_backstory, CharacterProfile)
                except Exception as e:
                    console.print(f"\n[red]Error parsing your revised backstory: {e}[/red]")
                    console.print("[yellow]Please try again or use a different revision.[/yellow]")
                    continue

                # Re-validate the revised input
                result = validator.validate_input(revised_backstory)
                is_valid = result.is_valid
                conflicts = result.conflicts
                suggestions = result.suggestions

                # Use the newly parsed profile if validation passed
                if is_valid:
                    current_profile = revised_profile
                    current_backstory = revised_backstory
            else:
                console.print("\n[yellow]Skipping validation. Using extracted profile as-is.[/yellow]")
                conflicts = []
                suggestions = []

        if conflicts and attempts >= max_attempts:
            console.print("\n[red]Could not resolve lore conflicts after multiple attempts.[/]")
            console.print("[yellow]Character creation failed. Exiting...[/yellow]")
            return None

    # === Create Player Object (use revised profile if available) ===
    player = Player(
        name="Protagonist",
        faction=current_profile.origin_faction,
        motivation=current_profile.motivation,
        goal=current_profile.goal
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
      4. resolve_action() with advantage/proficiency + actual stat score
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

    # Debounce: skip identical consecutive inputs immediately (no LLM cost).
    last_input: str | None = None

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

        user_input_raw = console.input("\n[bold green]>[/bold green] ").strip()

        if user_input_raw.lower() in ("quit", "q", "exit"):
            console.print("\n[yellow]Goodbye![/yellow]")
            break

        # Debounce: skip duplicate inputs instantly (fixes slow processing)
        if user_input_raw == last_input:
            console.print("[dim](duplicate — skipped. The previous turn already resolved this input.)[/dim]")
            _show_status(state_mgr)
            continue
        last_input = user_input_raw

        # If they typed a number and choices were shown, treat that as the chosen option
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
            _show_status(state_mgr)
            continue

        # --- Resolve the action mechanically (using advantage from parser + computed proficiency) ---
        stat_name = getattr(action_result_raw.modifiers, "target_stat", None)
        adv = getattr(action_result_raw.modifiers, "advantage", "none") or "none"
        tool_used = getattr(action_result_raw.modifiers, "tool_used", None)

        # Look up the actual stat score from PlayerStats (fixes issue: stat_bonus always 0).
        if stat_name:
            raw_stat_score = player.stats.__dict__.get(stat_name.lower(), 10)
        else:
            raw_stat_score = 10
        console.print(f"[dim][rolling dice…][/dim]")

        resolve_output = resolve_action(
            action_type=action_result_raw.action_type,
            stat_name=stat_name,
            stat_value=raw_stat_score,        # pass the actual ability score (was silently ignored)
            tool_modifier=1 if tool_used else 0,
            advantage=adv,
            proficiency=state_mgr.proficiency,
            world_context=snapshot.get("location", ""),
        )

        # Always apply engine effects — for ALL outcomes (success / partial / failure)
        effects_applied = state_mgr.apply_outcome_effects(resolve_output["outcome_level"], action_result_raw.action_type)
        effect_display = [f"{k} → {v}" for k, v in effects_applied.items()] if effects_applied else ["(no mechanical change this turn)"]

        # Check reputation thresholds — unlock new content when reached
        new_unlocks = state_mgr.player.check_rep_thresholds()
        unlock_display: list[str] = []
        if new_unlocks:
            for unlock in new_unlocks:
                label = unlock.replace("_", " ").title()
                unlock_display.append(f"[magenta]🔓 {label}[/magenta]")

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
            console.print("[dim][generating narrative…][/dim]")
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

        # Show reputation unlocks separately (not part of effects list)
        if unlock_display:
            console.print("\n[yellow]═══ Thresholds Reached ═══[/yellow]")
            for u in unlock_display:
                console.print(f"  {u}")

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
