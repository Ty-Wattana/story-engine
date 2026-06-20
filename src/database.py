"""SQLite persistence layer for the Neuro-Symbolic RPG.

Uses standard library ``sqlite3`` — no external DB dependencies.
All game state is serialised as JSON text columns in a single-row-per-session design.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional



# Resolve the repo root (project dir, one parent above src/)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _REPO_ROOT / "data" / "game.db"


# ── SQLite helpers ───────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Return a new sqlite3 connection with row_factory set."""
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db(conn: sqlite3.Connection | None = None) -> None:
    """Create tables if they don't exist.  Caller can skip by passing an existing connection."""
    if not conn:
        conn = _get_conn()
        _owns = True
    else:
        _owns = False

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS game_sessions (
                session_id TEXT PRIMARY KEY,
                save_slot  TEXT,           -- nullable named save slot
                player_state TEXT NOT NULL, -- JSON
                world_state  TEXT NOT NULL, -- JSON
                last_choices TEXT DEFAULT '[]',  -- JSON array of action choice strings
                last_updated TEXT NOT NULL  -- ISO-8601 timestamp
            )
        """)
        # Migration: add last_choices column to existing databases (no DROP COLUMN support)
        try:
            conn.execute("SELECT last_choices FROM game_sessions LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE game_sessions ADD COLUMN last_choices TEXT DEFAULT '[]'")
        # Migration: add save_slot column to message_history
        try:
            conn.execute("SELECT save_slot FROM message_history LIMIT 0")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE message_history ADD COLUMN save_slot TEXT DEFAULT NULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS message_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT    NOT NULL REFERENCES game_sessions(session_id),
                save_slot  TEXT,           -- nullable: copied from parent session on load
                role       TEXT    NOT NULL CHECK(role IN ('user', 'system', 'assistant')),
                content    TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_msg_hist
                ON message_history(session_id, id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_gs_save_slot
                ON game_sessions(save_slot)
        """)
        conn.commit()
    finally:
        if _owns:
            conn.close()


# ── Public API ───────────────────────────────────────────────────────

def create_session(
    session_id: Optional[str] = None,
    player_state: Any = None,   # Pydantic model or dict
    world_state: Any = None,     # Pydantic model or dict
    last_choices: Any = None,    # list of action choice strings
) -> str:
    """Create a new game session and persist initial state.

    Returns the session_id (generated or passed in).
    """
    conn = _get_conn()
    try:
        sid = session_id or uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        pjson = _to_json(player_state)
        wjson = _to_json(world_state)
        cjson = json.dumps(last_choices or [])
        conn.execute(
            "INSERT INTO game_sessions (session_id, player_state, world_state, last_choices, last_updated, save_slot) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, pjson, wjson, cjson, now, None),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def update_session(
    session_id: str,
    player_state: Any = None,
    world_state: Any = None,
    save_slot: Optional[str] = None,
    last_choices: Any = None,   # list of action choice strings
) -> None:
    """Persist (possibly partially updated) state for a session."""
    conn = _get_conn()
    try:
        cols = []
        vals: list[Any] = []

        if player_state is not None:
            cols.append("player_state = ?")
            vals.append(_to_json(player_state))
        if world_state is not None:
            cols.append("world_state = ?")
            vals.append(_to_json(world_state))
        if save_slot is not None:
            cols.append("save_slot = ?")
            vals.append(save_slot)
        if last_choices is not None:
            cols.append("last_choices = ?")
            vals.append(json.dumps(last_choices))

        cols.append("last_updated = ?")
        vals.append(datetime.now(timezone.utc).isoformat())
        vals.append(session_id)

        conn.execute(
            f"UPDATE game_sessions SET {', '.join(cols)} WHERE session_id = ?",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_session(session_id: str | None = None, save_slot: str | None = None) -> dict[str, Any] | None:
    """Return {session_id, player_state, world_state} or None if not found."""
    conn = _get_conn()
    try:
        if session_id:
            row = conn.execute(
                "SELECT * FROM game_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        elif save_slot:
            row = conn.execute(
                "SELECT * FROM game_sessions WHERE save_slot = ?", (save_slot,)
            ).fetchone()
        else:
            return None

        if row is None:
            return None

        player_state = _from_json(row["player_state"])
        world_state = _from_json(row["world_state"])
        last_choices = json.loads(row["last_choices"]) if row["last_choices"] else []
        return {
            "session_id": row["session_id"],
            "save_slot": row["save_slot"],
            "player_state": player_state,
            "world_state": world_state,
            "last_choices": last_choices,
            "last_updated": row["last_updated"],
        }
    finally:
        conn.close()


def save_slot_name(session_id: str, slot_name: str) -> None:
    """Assign / overwrite the named save-slot for a session."""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE game_sessions SET save_slot = ?, last_updated = ? WHERE session_id = ?",
            (slot_name, datetime.now(timezone.utc).isoformat(), session_id),
        )
        conn.commit()
    finally:
        conn.close()


def append_message(session_id: str, role: str, content: str, save_slot: str | None = None) -> int:
    """Append a message to history. Returns the new row id."""
    # Enforce max 5 messages — delete oldest if at capacity
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM message_history WHERE session_id = ?",
            (session_id,),
        ).fetchone()["cnt"]

        if existing >= 5:
            # Remove oldest to make room
            conn.execute(
                "DELETE FROM message_history WHERE id = ("
                "  SELECT id FROM message_history WHERE session_id = ? ORDER BY id ASC LIMIT 1"
                ")",
                (session_id,),
            )

        cur = conn.execute(
            "INSERT INTO message_history (session_id, save_slot, role, content) VALUES (?, ?, ?, ?)",
            (session_id, save_slot, role, content),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def fetch_messages(session_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Fetch the last *limit* messages for a session (most recent on end)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT role, content FROM message_history WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        # Return newest first; caller may reverse if needed
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    finally:
        conn.close()


def duplicate_session(old_slot: str) -> str:
    """Duplicate every row under *old_slot* into a brand-new session.

    Returns the new session_id.
    """
    src = fetch_session(save_slot=old_slot)
    if src is None:
        raise ValueError(f"No session found with save slot '{old_slot}'")

    new_sid = uuid.uuid4().hex
    conn = _get_conn()
    try:
        # Insert new session row (don't preserve last_updated from original to avoid confusion)
        now = datetime.now(timezone.utc).isoformat()
        last_choices = json.loads(src.get("last_choices", "[]")) if src.get("last_choices") else []
        conn.execute(
            "INSERT INTO game_sessions (session_id, save_slot, player_state, world_state, last_choices, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_sid, None, _to_json(src["player_state"]), _to_json(src["world_state"]), json.dumps(last_choices), now),
        )

        # Copy message history (carry forward save_slot from source session)
        src_slot = src.get("save_slot")
        rows = conn.execute(
            "SELECT role, content, save_slot FROM message_history WHERE session_id = ? ORDER BY id",
            (src["session_id"],),
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO message_history (session_id, save_slot, role, content) VALUES (?, ?, ?, ?)",
                (new_sid, src_slot, r["role"], r["content"]),
            )
        conn.commit()
    finally:
        conn.close()

    return new_sid


# ── JSON serialisation helpers ───────────────────────────────────────

def _to_json(obj: Any) -> str:
    """Convert state objects to deterministic JSON string.

    Handles Pydantic v2 BaseModel (.model_dump()), dataclasses (@dataclasses.asdict), and plain dicts.
    """
    if isinstance(obj, dict):
        return json.dumps(_strip_internal(obj))
    # Pydantic v2
    dump_method = getattr(obj, "model_dump", None)
    if dump_method:
        return json.dumps(_strip_internal(dump_method()))
    # Python dataclasses
    asdict = getattr(obj, "__dataclass_fields__", None)
    if asdict is not None:
        import dataclasses
        return json.dumps(_strip_internal(dataclasses.asdict(obj)))
    raise TypeError(f"Cannot serialise type {type(obj).__name__}")


def _from_json(text: str) -> dict[str, Any]:
    """Parse JSON back into a plain dict.

    Caller must decide which Pydantic / dataclass to instantiate on top of the dict.
    """
    return json.loads(text)


def _strip_internal(d: dict) -> dict:
    """Recursively remove keys starting with `_` (internal / computed fields)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            out[k] = _strip_internal(v)
        else:
            out[k] = v
    return out


# ── Module-level initialisation ─────────────────────────────────────

_init_db()  # tables ready on import
