"""Persistence for structured agent state without storing raw messages."""

import sqlite3

from .agent import ConversationState


SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
  session_id TEXT PRIMARY KEY,
  state_json TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def load_session(
    conn: sqlite3.Connection,
    session_id: str,
) -> ConversationState | None:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT state_json FROM agent_sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return ConversationState.model_validate_json(row[0])


def save_session(
    conn: sqlite3.Connection,
    session_id: str,
    state: ConversationState,
) -> None:
    ensure_schema(conn)
    conn.execute(
        """INSERT INTO agent_sessions (session_id, state_json, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(session_id) DO UPDATE SET
             state_json=excluded.state_json,
             updated_at=CURRENT_TIMESTAMP""",
        (session_id, state.model_dump_json()),
    )
    conn.commit()


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    ensure_schema(conn)
    cursor = conn.execute(
        "DELETE FROM agent_sessions WHERE session_id=?",
        (session_id,),
    )
    conn.commit()
    return cursor.rowcount > 0
