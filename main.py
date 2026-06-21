"""Story Engine — simulated Godot client (thick-client backend simulation).

No game logic, no LLM, no state imports.  Just HTTP triggers + Rich display
talking to the server at http://127.0.0.1:8000.

Run the server in one terminal::

    uvicorn src.server:app --host 127.0.0.1 --port 8000

Then run this client in another::

    python main.py
"""

import time

import requests
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import InvalidResponse, Prompt
from rich.text import Text
from rich.table import Table

CONSOLE = Console()
BASE = "http://127.0.0.1:8000"

# Escapes Rich markup characters in untrusted text (LPL/LLM output, user input).
def _esc(s: str) -> str:
    return s.replace("[", "\\[").replace("]", "\\]")


# ── Numeric-choice prompt — lets users type "1" instead of "1. Dialogue" ----

_MENU_ORDER = ["0", "1", "2", "3", "4", "5"]  # display order: 0 last, numbered first


class _MenuPrompt(Prompt):
    """Rich Prompt that accepts single-digit input and maps to labeled choices."""

    _labels: dict[str, str]  # e.g. {"1": "Dialogue", "2": "Interact", ...}

    def pre_prompt(self) -> None:
        labels = self._labels  # type: ignore[assignment]
        if labels:
            parts = [f"{k}. {labels[k]}" for k in _MENU_ORDER if k in labels]
            display = "/".join(parts)
            text = Text(f"[{display}]")
            self.console.print(text, markup=False)

    def process_response(self, value: str) -> str:
        v = value.strip()
        # Allow bare digit input → return just the key (matches downstream if/elif)
        if v in self._labels:  # type: ignore[operator]
            return v
        # Also accept the full label text ("1. Dialogue") and strip to digit
        for k in self._labels:  # type: ignore[operator]
            if value.strip() == f"{k}. {self._labels[k]}":
                return k
        raise InvalidResponse("Please select a valid option")


# ── Helpers ------------------------------------------------------------------

def _wait_for_server(timeout: int = 30) -> None:
    """Poll /health until the server is alive or timeout fires."""
    deadline = time.time() + timeout
    while True:
        try:
            r = requests.get(f"{BASE}/health", timeout=2)
            if r.status_code == 200:
                return
        except Exception:
            pass
        if time.time() > deadline:
            break
    CONSOLE.print(Panel(
        "[red]Server not running.[/]\n"
        "Start it in another terminal:\n"
        "  [bold]uvicorn src.server:app --host 127.0.0.1 --port 8000[/]",
        style="yellow",
    ))
    sys.exit(1)


def _post(path: str, body: dict) -> dict:
    """Send a POST and die on non-2xx."""
    resp = requests.post(f"{BASE}{path}", json=body, timeout=None)
    if resp.status_code >= 400:
        CONSOLE.print(Panel(f"Error {resp.status_code}: {resp.text}", style="red"), markup=False)
        sys.exit(1)
    return resp.json()


# ── State display ------------------------------------------------------------

def _render_state(player_data: dict, world_data: dict) -> None:
    """Show current session state as a Rich table (simulating Godot HUD)."""
    CONSOLE.print()
    table = Table(title="[bold]HUD[/]", show_header=True, expand=True)
    table.add_column("Field", style="cyan", width=16)
    table.add_column("Value", style="white")

    player_name = player_data.get("name", "Unknown")
    faction = player_data.get("faction", "—")
    motivation = player_data.get("motivation", "—")
    inventory = ", ".join(player_data.get("inventory", [])[:5])
    inv_note = "... (truncated)" if len(player_data.get("inventory", [])) > 5 else ""

    location = world_data.get("current_location", "Unknown")
    turn = world_data.get("turn_count", 0)
    npcs = ", ".join(world_data.get("active_npcs", [])[:3]) if world_data.get("active_npcs") else "[None]"

    table.add_row("Character", player_name)
    table.add_row("Faction", faction)
    table.add_row("Motivation", motivation)
    table.add_row("Inventory", f"{inventory}{inv_note}")
    table.add_row("Location", location)
    table.add_row("Turn", str(turn))
    table.add_row("Active NPCs", npcs)

    CONSOLE.print(table)


# ── Narrative display --------------------------------------------------------

def _print_narrative(text: str) -> None:
    """Wrap narrative in a styled panel for readability."""
    CONSOLE.print()
    CONSOLE.print(Panel(text.strip(), border_style="bright_cyan", padding=(0, 2)), markup=False)


def _print_choices(choices: list[str], title: str = "Responses") -> None:
    """Print numbered choices as a styled panel."""
    body = "\n".join(f"[{i}] {_esc(c)}" for i, c in enumerate(choices, 1))
    CONSOLE.print(Panel(body, border_style="yellow", title=f"[bold]{title}[/]", padding=(0, 2)), markup=False)


# ── Session management -------------------------------------------------------

def _save_session(session_id: str) -> None:
    slot = Prompt.ask("Slot name").strip()
    if not slot:
        return
    try:
        r = requests.post(f"{BASE}/system/save", json={"session_id": session_id, "slot_name": slot}, timeout=5)
        if r.status_code == 200:
            CONSOLE.print(f"[green]Saved as '{slot}'[/]")
    except requests.RequestException as exc:
        CONSOLE.print(Panel(f"Save failed: {exc}", style="red"), markup=False)


def _delete_session() -> None:
    slot = Prompt.ask("Slot to delete").strip()
    if not slot:
        return
    try:
        r = requests.post(f"{BASE}/system/delete", json={"slot_name": slot}, timeout=5)
        if r.status_code == 200:
            CONSOLE.print(f"[green]Deleted slot '{slot}'[/]")
    except requests.RequestException as exc:
        CONSOLE.print(Panel(f"Delete failed: {exc}", style="red"), markup=False)


# ── Event simulation (Godot engine triggers) --------------------------------

def _trigger_dialogue(session_id: str, player_data: dict, world_data: dict) -> dict:
    """Simulate a Godot dialogue event trigger."""
    npcs = world_data.get("active_npcs", []) or [
        "Vex (shady merchant)", "Eldara (mysterious sage)", "Grom (gruff guard)"
    ]

    choice_text = "\n".join(f"[{i}]{_esc(npc)}" for i, npc in enumerate(npcs, 1))
    if not npcs:
        npcs = ["Vex (shady merchant)"]
        choice_text = "[1]Vex (shady merchant)"

    CONSOLE.print()
    CONSOLE.print(Panel(choice_text, border_style="yellow", title="[bold]Choose NPC to speak with[/]", padding=(0, 2)), markup=False)

    npc_choice = Prompt.ask("NPC choice", choices=[str(i) for i in range(1, len(npcs) + 1)], default="1")
    npc_name = npcs[int(npc_choice) - 1]

    # Check if there's conversation history to continue
    choice_labels = {"1": "New greeting", "2": "Continue existing dialogue"}
    choice = _MenuPrompt(
        "Start fresh or reply?",
        choices=[f"{k} ({v})" for k, v in choice_labels.items()],
    )
    choice._labels = choice_labels  # type: ignore[attr-defined]
    choice = choice(default="1")

    player_msg = ""
    if choice == "2":
        player_msg = Prompt.ask("Your response").strip()

    CONSOLE.print(f"[dim]> Dialoguing with {npc_name}...[/]")
    resp = _post("/event/dialogue", {
        "session_id": session_id,
        "npc_name": npc_name,
        "player_message": player_msg,
    })
    _print_narrative(resp["npc_response"])

    if resp.get("dialogue_choices"):
        _print_choices(resp["dialogue_choices"], title="Dialogue Choices")

        # Option to auto-pick a choice or reply manually
        num = Prompt.ask(
            "Pick choice (number) or type custom reply",
            default=str(len(resp["dialogue_choices"]) + 1),
        )
        if num.isdigit() and 1 <= int(num) <= len(resp["dialogue_choices"]):
            followup = resp["dialogue_choices"][int(num) - 1]
            CONSOLE.print(f"[dim]> {followup}[/]")
            # Send the follow-up as another dialogue event
            next_resp = _post("/event/dialogue", {
                "session_id": session_id,
                "npc_name": npc_name,
                "player_message": followup,
            })
            CONSOLE.print()
            CONSOLE.print("[bold]NPC Reply:[/]")
            _print_narrative(next_resp["npc_response"])
            if next_resp.get("dialogue_choices"):
                _print_choices(next_resp["dialogue_choices"], title="Follow-up Choices")
        else:
            custom = Prompt.ask("Custom reply").strip()
            if custom:
                next_resp = _post("/event/dialogue", {
                    "session_id": session_id,
                    "npc_name": npc_name,
                    "player_message": custom,
                })
                CONSOLE.print()
                CONSOLE.print("[bold]NPC Reply:[/]")
                _print_narrative(next_resp["npc_response"])

    return resp.get("updated_state", {})


def _trigger_interact(session_id: str, world_data: dict) -> dict:
    """Simulate a Godot object interaction event trigger."""
    obj = Prompt.ask(
        "Target object",
        default="Strange Monolith",
    ).strip()

    CONSOLE.print(f"[dim]> Interacting with '{obj}'...[/]")
    resp = _post("/event/interact", {
        "session_id": session_id,
        "target_object": obj,
    })
    _print_narrative(resp["narrative_description"])

    return resp.get("updated_state", {})


def _trigger_combat(session_id: str, player_data: dict) -> dict:
    """Simulate a Godot post-combat event trigger."""
    CONSOLE.print("[bold yellow]Post-Combat Resolution[/]")
    victor = Prompt.ask("Victor", default=player_data.get("name", "Player")).strip()
    enemies_raw = Prompt.ask("Defeated enemies (comma-separated)", default="Goblin A, Goblin B").strip()
    defeated = [e.strip() for e in enemies_raw.split(",") if e.strip()]

    CONSOLE.print("[dim]> Sending combat result to server...[/]")
    resp = _post("/event/combat_resolved", {
        "session_id": session_id,
        "victor": victor,
        "defeated_enemies": defeated,
    })
    _print_narrative(resp["narrative_summary"])

    return resp.get("updated_state", {})

# ── Main menu & game loop ---------------------------------------------------

def main() -> None:
    _wait_for_server()
    sid: str | None = None
    pdata: dict = {}
    wdata: dict = {}

    while True:
        if sid is None:
            # --- Bootstrap phase (no session yet) ----
            CONSOLE.print(Panel(
                "1. Start new campaign\n2. Load saved game\n3. Quit",
                title="[dim]Welcome to Story Engine[/]",
                border_style="blue",
            ))
            action = Prompt.ask("Action", choices=["1", "2", "3"], default="1")

            if action == "3":
                CONSOLE.print("[bold green]Goodbye![/]")
                break

            if action == "1":
                name = Prompt.ask("Character name", default="Wanderer").strip()
                CONSOLE.print(f"[dim]Parsing backstory for '{name}'...[/]")
                resp = _post("/game/start", {"player_name": name, "backstory": f"Backstory: {name}"})
                sid = resp["session_id"]
                pdata = resp.get("updated_player_state", {})
                wdata = resp.get("updated_world_state", {})
                CONSOLE.print(f"[green]Session:[/] [bold yellow]{sid}[/]")
                _print_narrative(resp["narrative"])
                if resp.get("choices"):
                    _print_choices(resp["choices"], title="Choices")

            else:  # action == "2"
                slot = Prompt.ask("Slot name").strip()
                if not slot:
                    continue
                try:
                    resp = _post("/system/load", {"slot_name": slot})
                    sid = resp["session_id"]
                    pdata = resp.get("player_state", {})
                    wdata = resp.get("world_state", {})
                    ctx = resp.get("narrative_context", [])
                    if ctx:
                        _print_narrative(ctx[-1])
                    else:
                        CONSOLE.print("[dim]Session loaded — no recent context.[/]")
                except Exception as exc:
                    CONSOLE.print(Panel(f"Load failed: {exc}", style="red"), markup=False)

            continue

        # --- Active session phase ----
        CONSOLE.print()
        _render_state(pdata, wdata)

        trigger_labels = {
            "1": "Dialogue", "2": "Interact", "3": "Combat Resolved",
            "4": "Zone Transition", "5": "Save", "0": "Quit/Delete",
        }
        action = _MenuPrompt(
            "\n[dim]Engine trigger?[/]",
            choices=[f"{k}. {v}" for k, v in trigger_labels.items()],
        )
        action._labels = trigger_labels  # type: ignore[attr-defined]
        action = action(default="1")

        if action == "1":
            resp = _trigger_dialogue(sid, pdata, wdata)
            pdata.update(resp.get("player", {}))
            wdata.update(resp.get("world", {}))

        elif action == "2":
            resp = _trigger_interact(sid, wdata)
            pdata.update(resp.get("player", {}))
            wdata.update(resp.get("world", {}))

        elif action == "3":
            resp = _trigger_combat(sid, pdata)
            pdata.update(resp.get("player", {}))
            wdata.update(resp.get("world", {}))

        elif action == "4":
            new_loc = Prompt.ask("Destination zone", default="Dark Forest").strip()
            CONSOLE.print(f"[dim]> Transitioning to '{new_loc}'...[/]")
            wdata["current_location"] = new_loc
            CONSOLE.print(f"[green]Arrived at '[bold]{new_loc}[/bold]'.[/]")

        elif action == "5":
            _save_session(sid)

        else:  # "0" — Quit / Delete session
            slot = Prompt.ask("Delete which slot?").strip()
            if slot:
                try:
                    r = requests.post(f"{BASE}/system/delete", json={"slot_name": slot}, timeout=5)
                    if r.status_code == 200:
                        CONSOLE.print(f"[green]Deleted '{slot}'[/]")
                except requests.RequestException as exc:
                    CONSOLE.print(Panel(f"Delete failed: {exc}", style="red"), markup=False)
            sid = None
            pdata.clear()
            wdata.clear()


if __name__ == "__main__":
    main()