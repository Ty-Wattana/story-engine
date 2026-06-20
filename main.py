"""Story Engine — dumb terminal client (HTTP only).

No game logic, no LLM, no state imports.  Just requests + rich talking to
the server at http://127.0.0.1:8000.

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

# Escapes Rich markup characters in untrusted text (LPL/LLM output, user input).
def _esc(s: str) -> str:
    return s.replace("[", "\\[").replace("]", "\\]")


CONSOLE = Console()
BASE = "http://127.0.0.1:8000"


# ── Helpers ----------------------------------------------------------------

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
        CONSOLE.print(Panel(f"Error {resp.status_code}: {resp.text}", style="red"))
        sys.exit(1)
    return resp.json()


def _print_narrative(text: str) -> None:
    """Wrap narrative in a styled panel for readability."""
    CONSOLE.print()
    CONSOLE.print(Panel(text.strip(), border_style="bright_cyan", padding=(0, 2)), markup=False)
    CONSOLE.print()


def _print_choices(choices: list[str]) -> None:
    """Print numbered action choices as a styled panel."""
    # Build plain string with escaped brackets — Rich markup can't parse LLM-generated text safely.
    body = "\n".join(f"[{i}] {_esc(c)}" for i, c in enumerate(choices, 1))
    CONSOLE.print(Panel(body, border_style="yellow", title="[bold]Choices[/]", padding=(0, 2)), markup=False)


# ── Main loop --------------------------------------------------------------

def main() -> None:
    _wait_for_server()
    session_id: str | None = None
    current_choices: list[str] = []

    while True:
        # Display help bar when no session is active.
        if session_id is None:
            _bootstrap_prompt(session_id)
        else:
            _action_prompt(session_id, current_choices)

        raw = CONSOLE.input().strip()
        if not raw:
            continue

        # ── Digit shortcut → mapped choice ──────────────────────────────
        if raw.isdigit() and 1 <= int(raw) <= len(current_choices):
            resolved = current_choices[int(raw) - 1]
            CONSOLE.print(f"[dim]> {resolved}[/]")
            current_choices = _send_action(session_id, resolved)
            if current_choices:
                _print_choices(current_choices)
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # ── Session-less commands ───────────────────────────────────────
        if cmd == "/quit" or cmd in ("q", "exit"):
            CONSOLE.print("[bold green]Goodbye![/]")
            break

        # ── Bootstrap: /start ───────────────────────────────────────────
        if cmd == "/start":
            name = arg.strip() or "Wanderer"
            CONSOLE.print(f"[dim]Parsing backstory for '{name}'…[/]")
            resp = _post("/game/start", {"player_name": name, "backstory": name})
            session_id = resp["session_id"]
            _print_narrative(resp["narrative"])
            current_choices = resp.get("choices", [])
            if current_choices:
                _print_choices(current_choices)
            continue

        # ── Bootstrap: /load ────────────────────────────────────────────
        if cmd == "/load" and arg:
            try:
                resp = _post("/system/load", {"slot_name": arg.strip()})
            except requests.RequestException as exc:
                CONSOLE.print(Panel(f"Load failed: {exc}", style="red"), markup=False)
                continue
            session_id = resp["session_id"]
            ctx = resp.get("narrative_context", [])
            if ctx:
                _print_narrative(ctx[-1])
            else:
                CONSOLE.print("[dim]Session loaded — no recent context.[/]")
            current_choices = resp.get("choices", [])
            if current_choices:
                _print_choices(current_choices)
            continue

        # ── In-session commands ─────────────────────────────────────────
        if session_id is None:
            CONSOLE.print("[dim]/start or /load first.[/]")
            continue

        if cmd == "/save" and arg:
            _post("/system/save", {"session_id": session_id, "slot_name": arg.strip()})
            CONSOLE.print(f"[green]Game saved as '{arg.strip()}'[/]")
            continue

        if cmd == "/load":
            # /load without arg shows help; with arg delegates above.
            CONSOLE.print("[dim]Usage: /load <slot_name>[/]")
            continue

        if cmd == "/delete" and arg:
            slot = arg.strip()
            r = requests.post(f"{BASE}/system/delete", json={"slot_name": slot}, timeout=5)
            if r.status_code >= 400:
                CONSOLE.print(Panel(f"Delete failed ({r.status_code}): {r.text}", style="red"), markup=False)
            else:
                resp = r.json()
                CONSOLE.print(f"[green]{resp.get('status', 'deleted')} slot '{slot}'[/]")
            continue

        # ── Player action (default) ─────────────────────────────────────
        current_choices = _send_action(session_id, raw)
        if current_choices:
            _print_choices(current_choices)

def _send_action(session_id: str, player_input: str) -> list[str]:
    """POST to /game/action, print narrative. Return new choices for caller to use."""
    resp = _post("/game/action", {"session_id": session_id, "player_input": player_input})
    _print_narrative(resp["narrative"])
    return resp.get("choices", [])


def _bootstrap_prompt(sid: str | None) -> None:
    tag = f"[bold yellow]{sid}[/]" if sid else "[dim]no active session[/]"
    CONSOLE.print(f"  Session: {tag}  [dim]> /start  /load <slot>  /delete <slot>  /quit[/]")


def _action_prompt(sid: str | None, choices: list[str] | None = None) -> None:
    tag = f"[bold yellow]{sid}[/]" if sid else "?"
    parts = [f"\n[dim]Session: {tag}  |  /save <name>  /load <slot>  /delete <slot>  /quit[/]"]
    if choices:
        parts.append(f"  [dim][[1-{len(choices)}] to choose, or type freely[/]")
    CONSOLE.print("\n".join(parts))


if __name__ == "__main__":
    main()
