"""Character creation — backstory parsing and lore validation."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from src.state import Player, WorldState, StateManager, PlayerStats
from src.schemas import CharacterProfile
from src.llm_client import LLMClient
from src.lore import LoreParser, create_validator, LoreConflict

console = Console()


def initialize_game(llm: LLMClient) -> Player | None:
    """Run character creation flow: parse backstory → validate lore → return Player."""
    console.print("[blue]Welcome to the Story Engine PoC[/blue]")
    console.print("[blue]Who are you, and what do you seek?[/blue]\n")

    backstory = console.input("[blue]>[/blue] Enter your backstory: ")

    system_prompt = llm._load_system_prompt()

    console.print("\n[yellow]=== System Prompt Preview ===[/yellow]")
    console.print(system_prompt[:500] + "[\n\n", markup=False)

    profile: CharacterProfile | None = None
    use_fallback = False

    try:
        with console.status("[yellow]Parsing background with local LLM...[/yellow]"):
            profile = llm.generate_structured(system_prompt, backstory, CharacterProfile)
    except Exception as e:
        console.print(f"\n[red]Error parsing your backstory: {e}[/red]")
        console.print("[yellow]Trying keyword extraction fallback...[/yellow]")

        fallback = _extract_fallback_profile(backstory)
        if fallback:
            profile = fallback
            use_fallback = True
        else:
            console.print("[yellow]No fallback available. Please try a different backstory.[/yellow]")
            return None

    current_profile = profile  # Either from LLM or keyword fallback

    # === Lore Validation ===
    console.print("\n[yellow]=== Validating against lore database ===[/yellow]")
    parser = LoreParser(console)
    parser.parse_markdown("data/lore_summary.md")
    validator = create_validator("data/lore_summary.md")

    result = validator.validate_input(backstory)
    is_valid = result.is_valid
    conflicts = result.conflicts
    suggestions = result.suggestions
    current_backstory = backstory

    if not is_valid:
        console.print(Panel(
            "[red]The extracted character profile conflicts with established lore.[/red]\n"
            f"[yellow]Conflicts found: {len(conflicts)}[/yellow]",
            title="Lore Conflict Detected",
            border_style="red",
            box=box.DOUBLE,
        ))

        max_attempts = 5
        attempts = 0
        revision_options: list[str] | None = None

        while conflicts and attempts < max_attempts:
            attempts += 1
            console.print(f"\n[gray]Attempt {attempts}/{max_attempts}[/gray]")
            for i, conflict in enumerate(conflicts, 1):
                severity_marker = {"error": "[red][!]", "warning": "[yellow][!]", "info": "[cyan][i]"}.get(
                    conflict.severity, "[?]"
                )
                console.print(f"{severity_marker} {conflict.fact.fact}")

            if suggestions:
                revision_options = suggestions
            else:
                revision_options = None

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

                        rev_system_prompt = llm._load_system_prompt()
                        revised_profile = llm.generate_structured(rev_system_prompt, revised_backstory, CharacterProfile)

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

                system_prompt = llm._load_system_prompt()
                try:
                    revised_profile = llm.generate_structured(system_prompt, revised_backstory, CharacterProfile)
                except Exception as e:
                    console.print(f"\n[red]Error parsing your revised backstory: {e}[/red]")
                    console.print("[yellow]Please try again or use a different revision.[/yellow]")
                    continue

                result = validator.validate_input(revised_backstory)
                is_valid = result.is_valid
                conflicts = result.conflicts
                suggestions = result.suggestions

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
        goal=current_profile.goal,
    )

    # Assign baseline stats based on backstory keywords
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


def _extract_fallback_profile(backstory: str) -> CharacterProfile | None:
    """Extract character profile from backstory using keyword heuristics.

    Used as a fallback when the LLM fails to produce valid structured output.
    Returns a reasonable default if keywords match, else None.
    """
    text = backstory.lower()

    # Faction detection
    factions = {
        "mercenary": "Independent Mercenary",
        "soldier": "Veteran Soldier",
        "knight": "Fallen Knight",
        "ranger": "Woods Ranger",
        "wizard": "Arcane Scholar",
        "mage": "Arcane Mage",
        "rogue": "Shadow Rogue",
        "thief": "Street Thief",
        "merchant": "Desert Caravan Merchant",
        "healer": "Temple Healer",
        "cleric": "Temple Cleric",
        "paladin": "Holy Paladin",
        "barbarian": "Wild Barbarian",
        "monk": "Wandering Monk",
        "druid": "Forest Druid",
        "assassin": "Shadow Assassin",
        "guard": "Village Guard",
        "noble": "Disgraced Noble",
        "wizard|mage|sorcerer": "Arcane Scholar",
    }
    faction = "Wanderer"
    for pattern, name in factions.items():
        if any(w in text for w in pattern.split("|")):
            faction = name
            break

    # Motivation detection
    motivations = {
        "rescue|save|find": "Rescue",
        "revenge|vengeance|kill|slay": "Vengeance",
        "wealth|fortune|gold|riches": "Wealth",
        "power|conquer|rule": "Power",
        "knowledge|lore|study|learn": "Knowledge",
        "redemption|atonement|penance|sin": "Redemption",
        "glory|honor|fame": "Glory",
        "freedom|escape|flee": "Freedom",
        "justice|truth|protect|defend": "Justice",
        "love|loyalty| devotion": "Loyalty",
    }
    motivation = "Discovery"
    for pattern, tag in motivations.items():
        if any(w in text for w in pattern.split("|")):
            motivation = tag
            break

    # Goal detection - build from key verbs/nouns
    goal_words = []
    goal_patterns = {
        "rescue|save": "rescue",
        "revenge|vengeance": "avenge",
        "kill|slay|destroy": "destroy",
        "find|search": "find",
        "learn|study|discover": "discover",
        "conquer|rule": "conquer",
        "protect|defend": "defend",
        "seek|pursue": "pursue",
    }
    for pattern, verb in goal_patterns.items():
        if any(w in text for w in pattern.split("|")):
            goal_words.append(verb)
            break

    # Extract key nouns (simplified: look for capitalized-like or specific nouns)
    noun_indicators = {
        "village": "the village",
        "kingdom": "the kingdom",
        "friend|companion|ally": "an ally",
        "artifact|relic|artefact": "a lost relic",
        "fortress|castle|tower": "the enemy stronghold",
    }
    for pattern, noun in noun_indicators.items():
        if any(w in text for w in pattern.split("|")):
            goal_words.append(noun)
            break

    if not goal_words:
        goal = f"Forge a new destiny among {faction.lower()}"
    else:
        obj = goal_words[1] if len(goal_words) > 1 else "the target"
        goal = f"{goal_words[0].title()} {obj}".rstrip()

    return CharacterProfile(
        origin_faction=faction,
        motivation=motivation,
        goal=goal,
    )
