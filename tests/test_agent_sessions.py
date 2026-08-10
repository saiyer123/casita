import sqlite3

from casita.agent import ConversationState
from casita.agent_sessions import delete_session, load_session, save_session
from casita.preferences import PreferenceProfile


def test_session_round_trip_persists_only_structured_state():
    conn = sqlite3.connect(":memory:")
    state = ConversationState(
        profile=PreferenceProfile(max_price=5000, min_beds=2),
        last_result_keys=["manual:one"],
    )

    save_session(conn, "demo", state)
    loaded = load_session(conn, "demo")

    assert loaded == state
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(agent_sessions)").fetchall()
    }
    assert "message" not in columns
    assert "history" not in columns


def test_delete_session_removes_saved_state():
    conn = sqlite3.connect(":memory:")
    save_session(conn, "demo", ConversationState())

    deleted = delete_session(conn, "demo")

    assert deleted is True
    assert load_session(conn, "demo") is None
