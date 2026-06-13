"""Status display helpers — Rich tables and panels for the game loop."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


def build_stats_table(state_mgr) -> Panel:
    """Build a Rich table of player ability scores with temporary bonuses."""
    s = state_mgr.player.stats
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))

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
        bonus = s.bonus(name) + temp_bonuses[name]
        display_val = f"  {val:>3}  ({bonus:+d})"
        if temp_bonuses[name]:
            sign = "+" if temp_bonuses[name] > 0 else ""
            display_val += f" [dim]{sign}{temp_bonuses[name]}[/dim]"
        t.add_row(f"[bold green]{name.capitalize():>10}[/] ", display_val)

    return Panel(t, title="[cyan]Stats", border_style="blue")


def build_inventory_table(state_mgr) -> Panel:
    """Build a Rich panel of the player's inventory."""
    inv = state_mgr.player.inventory
    if not inv:
        return Panel("[dim](empty)[/dim]", box=box.SIMPLE)

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    for item in inv:
        t.add_row("  •  ", item)

    return Panel(t, title="[dim]Inventory", border_style="dim")


def show_status(state_mgr) -> None:
    """Display the full status header for each turn."""
    w = state_mgr.world
    p = state_mgr.player

    console.print(f"\n[bold cyan]═══ [{w.current_location.capitalize()}] ═══[/bold cyan]\n")

    header = (
        f"[bold]{p.name}[/bold] — [bold]Faction:[/bold] {p.faction}\n"
        f"[bold]Goal:[/bold] {p.goal}  |  [bold]Motivation:[/bold] {p.motivation}\n"
        f"[dim](Turn {w.turn_count})[/dim]"
    )
    console.print(Panel(header, title="[green]Player Status", border_style="green"))

    stats_panel = build_stats_table(state_mgr)
    inv_panel = build_inventory_table(state_mgr)

    rep_items = [f"  •  {k}: +{v}" for k, v in p.reputation.items()]
    if not rep_items:
        rep_panel = Panel("[dim]Reputation: (none yet)[/dim]", title="[magenta]Reputation", border_style="magenta")
    else:
        rep_panel = Panel("\n".join(rep_items), title="[magenta]Reputation", border_style="magenta")

    console.print(stats_panel)
    console.print(inv_panel)
    console.print(rep_panel)
