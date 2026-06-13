"""Outcome rendering — Rich panels and tables for action results."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich.text import Text
from rich import box

console = Console()


def _score_color(score: int, dc: int) -> str:
    """Pick a Rich text colour string for the final score."""
    if score >= dc * 2:
        return "bold magenta"
    elif score >= dc:
        return "green"
    else:
        return "red"


def _outcome_label(outcome: str, success: bool, score: int, dc: int) -> Text:
    """Create a Rich-Text string with the outcome label + color."""
    if outcome == "crit" and not success:
        return Text("CRIT FRESH", style="bold green")
    elif not success:
        return Text("FAILURE", style="red dim")
    text_parts = {
        "success": ("SUCCESS", "green"),
        "partial": ("PARTIAL", "yellow"),
        "crit_fresh": ("CRIT FRESH!", "bold green"),
    }
    label, color = text_parts.get(outcome, ("SUCCESS", "white"))
    return Text(label, style=color)


def build_outcome_panel(result: dict) -> Panel:
    """Build a Rich-formatted outcome panel for the game loop to print directly."""
    lines = []

    intent = result.get("intent", "?")
    verb = result.get("verb", "?")
    action_type = result.get("action_type", "?")
    target = result.get("target_entity", None)
    lines.append("[bold]Action:[/] %s | %s (%s)" % (intent, verb, action_type))
    if target:
        lines.append("  Target: [cyan]%s[/cyan]" % target)

    dc = result.get("target_dc", 0)
    roll = result.get("dice_roll", 0)
    mod = result.get("modifier", 0)
    score = result.get("final_score", 0)

    roll_text = Text()
    roll_text.append("Raw: %s" % str(roll), style="bold yellow")
    sign = "+" if mod >= 0 else ""
    roll_text.append("%s%d = " % (sign, mod), style="bold cyan")
    roll_text.append("Score: [%d]" % score, style=_score_color(score, dc))

    lines.append("[dim]Roll:[/] %s" % roll_text)

    outcome = result["outcome_level"]
    text_result = _outcome_label(outcome, result.get("success", False), score, dc)
    lines.append("Outcome: [%s]" % text_result)

    raw_effects = result.get("effects", [])
    if isinstance(raw_effects, dict):
        effects = raw_effects
    else:
        effects = {e["key"]: e["value"] for e in raw_effects if isinstance(e, dict)} if isinstance(raw_effects, list) else {}

    if effects:
        lines.append("")
        lines.append("[bold]Effects:[/]")
        for k, v in effects.items():
            lines.append("  [cyan]%s[/cyan] -> %s" % (k, v))

    sep = "=" * 50
    lines.append(sep)

    return Panel(
        "\n".join(lines),
        title="[bold white]Result",
        border_style="blue",
    )


def display_outcome(result: dict, effects_applied: list[str], flavor_text: str | None = None) -> None:
    """Display a unified outcome panel for success / partial / failure."""
    lines: list[str] = []

    outcome = result["outcome_level"]
    dice_roll = result["dice_roll"]
    modifier = result["modifier"]
    final_score = result["final_score"]
    dc = result["target_dc"]
    advantage = result.get("advantage", "none")

    adv_marker = f" ({advantage})" if advantage != "none" else ""
    hit_color = "[green]" if result["success"] else "[red]"
    hit_text = "HIT" if result["success"] else "MISS"
    mod_str = f"{modifier:+d}"
    lines.append(f"[dim]Roll:[/dim] d20[{dice_roll}]{adv_marker} {mod_str} → {final_score} [{hit_color}{hit_text}[/] (DC={dc})")

    outcome_colors = {
        "crit_fresh": "[bold magenta]",
        "success": "[green]",
        "partial": "[yellow]",
        "failure": "[red dim]",
        "crit": "[bold green]",
    }
    color = outcome_colors.get(outcome, "[white]")
    lines.append(f"[{color}]Outcome: {outcome.replace('_', ' ').upper()}[/]")

    if effects_applied:
        lines.append("")
        lines.append("[bold]Effects:[/]")
        for e in effects_applied:
            lines.append(f"  [cyan]{e}[/cyan]")

    if flavor_text:
        lines.append("")
        lines.append(f"[italic][dim]The outcome:[/dim] {flavor_text}[/italic]")

    panel_border = "green" if result["success"] else ("yellow" if outcome == "partial" else "red")
    console.print(Rule("[bold]" + outcome.replace("_", " ").upper() + "[/]", style=panel_border))
    for line in lines:
        console.print(line)

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


def show_dm_choices(options: list[str]) -> None:
    """Present DM choices as a numbered table."""
    if not options:
        return
    t = Table(box=box.SIMPLE, title="[bold yellow]Available Actions[/bold yellow]", show_header=False)
    for i, opt in enumerate(options, 1):
        t.add_row(f"[bold cyan]{i}:[][dim]•[/dim]", opt)
    console.print(Panel(t, border_style="yellow", expand=True))
