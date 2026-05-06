import os, sys
sys.path.insert(0, os.path.abspath("."))

from rich.console import Console
from src.llm_client import LLMClient
from src.state import Player, WorldState
from src.schemas import CharacterProfile

console = Console()

def initialize_game(llm: LLMClient) -> Player:
    console.print("[blue]Welcome to the Story Engine PoC[/blue]")
    console.print("[blue]Who are you, and what do you seek?[/blue]")
    backstory = console.input("> ")

    system_prompt = "Extract the character's core details from the provided backstory."
    
    with console.status("[yellow]Parsing background with local LLM...[/yellow]"):
        profile = llm.generate_structured(system_prompt, backstory, CharacterProfile)

    player = Player(
        name="Protagonist",
        faction=profile.origin_faction,
        motivation=profile.motivation
    )
    
    console.print("\n[green]Character Established:[/green]")
    console.print(f"Faction: {player.faction}")
    console.print(f"Motivation: {player.motivation}")
    
    return player

def main():
    llm = LLMClient()
    world = WorldState(current_location="Starting Village")
    
    player = initialize_game(llm)
    
    # Placeholder for the main loop
    console.print("\n[blue]--- Engine Initialized. Ready for Game Loop. ---[/blue]")

if __name__ == "__main__":
    main()