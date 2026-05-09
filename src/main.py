import os
import sys

# Add current directory to path for imports
sys.path.insert(0, os.path.abspath("."))

from src.state import Player, WorldState
from src.schemas import CharacterProfile
from rich.console import Console
from rich.panel import Panel

from llm_client import LLMClient

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

    # Create player with extracted profile
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
