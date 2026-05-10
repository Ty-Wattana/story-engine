import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.abspath("."))

from src.state import Player, WorldState
from src.schemas import CharacterProfile
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src.llm_client import LLMClient
from src.lore_validator import LoreParser, LoreValidator, create_validator

console = Console()

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

    # Initialize lore validator
    parser = LoreParser(console)
    parser.parse_markdown("data/lore_summary.md")
    validator = LoreValidator(parser)

    is_valid, conflicts, suggestions = validator.validate_input(backstory)

    if not is_valid:
        console.print(Panel(
            "[red]The extracted character profile conflicts with established lore.[/red]\n"
            f"[yellow]Conflicts found: {len(conflicts)}[/yellow]",
            title="Lore Conflict Detected",
            border_style="red",
            box=box.DOUBLED
        ))

        # Negotiation loop
        max_attempts = 5
        attempts = 0

        while conflicts and attempts < max_attempts:
            attempts += 1
            console.print(f"\n[gray]Attempt {attempts}/{max_attempts}[/gray]")

            # Show conflict details
            for i, conflict in enumerate(conflicts, 1):
                severity_marker = {
                    "error": "[red][!]",
                    "warning": "[yellow][!]}",
                    "info": "[cyan][i]"
                }.get(conflict.severity, "[?]")
                console.print(f"{severity_marker} {conflict.conflict}")

            # Build revision suggestions
            if suggestions:
                table = Table(
                    title="Suggested Revisions",
                    box=box.SIMPLE,
                    show_header=True,
                    header_style="bold cyan"
                )
                table.add_column("Option", style="cyan")
                for i, suggestion in enumerate(suggestions, 1):
                    table.add_row(f"[i]Option {i}:[/i] {suggestion}")

                console.print(table)

            # Ask user how to proceed
            response = console.input(
                "\n[bold yellow]How would you like to proceed?[/bold yellow]\n"
                "[yellow] (a) Accept suggested revision[/yellow]\n"
                "[yellow] (r) Revise the input yourself[/yellow]\n"
                "[yellow] (s) Skip this validation[/yellow]\n"
                "> [/yellow]"
            )

            if "a" in response.lower():
                # Accept suggested revision
                console.print("\n[green]Accepting revision suggestions...[/green]")
                # For now, we'll just keep the extracted profile
                # In a full implementation, we'd regenerate with modified prompt
                conflicts = []
                suggestions = []
            elif "r" in response.lower():
                # User wants to revise
                console.print("\n[blue]=== Please revise your backstory ===[/blue]")
                console.print("[yellow]Make it consistent with the lore:[/yellow]")
                console.print("[dim](e.g., choose an existing faction, adjust magic usage, etc.)[/dim]\n>")
                revised_backstory = console.input("> ")

                # Re-validate the revised input
                is_valid, conflicts, suggestions = validator.validate_input(revised_backstory)
            else:
                # Skip validation - warn but continue
                console.print("\n[yellow]Skipping validation. Using extracted profile as-is.[/yellow]")
                conflicts = []
                suggestions = []

        if conflicts and attempts >= max_attempts:
            console.print("\n[red]Could not resolve lore conflicts after multiple attempts.[/red]")
            console.print("[yellow]Character creation failed. Exiting...[/yellow]")
            return None

    # === Create Player Object ===
    player = Player(
        name="Protagonist",
        faction=profile.origin_faction,
        motivation=profile.motivation,
        goal=profile.goal
    )

    # === Display Format 1: Structured Block ===
    console.print("\n[green]=== Character Established ===[/green]")
    console.print(f"[italic]Faction:[/italic] [bold white]{player.faction}[/bold white]")
    console.print(f"[italic]Motivation:[/italic] [bold red]{player.motivation}[/bold red]")
    console.print(f"[italic]Goal:[/italic] [bold cyan]{player.goal}[/bold cyan]")

    # === Display Format 2: Narrative Paragraph ===
    console.print("\n[green]=== Character Profile ===[/green]")
    console.print(Panel(
        f"[bold]{player.name}[/bold]\n"
        f"A [bold]{player.faction}[/bold] driven by [bold]{player.motivation}[/bold],\n"
        f"seeking to [bold]{player.goal}[/bold].",
        title="Character Profile",
        border_style="blue"
    ))

    return player

def main():
    llm = LLMClient()
    world = WorldState(current_location="Starting Village")

    player = initialize_game(llm)

    if player:
        console.print("\n[blue]--- Engine Initialized. Ready for Game Loop. ---[/blue]")
    else:
        console.print("\n[red]Failed to initialize character. Exiting...[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
