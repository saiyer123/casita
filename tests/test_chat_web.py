import json
import shutil
import sqlite3
import threading
from urllib.request import Request, urlopen

import casita
from casita.agent import RuleBasedInterpreter
from casita.agent_sessions import load_session
from casita.chat_web import CHAT_HTML, chat_html, create_chat_server


def test_chat_html_uses_local_api_and_safe_text_rendering():
    assert "fetch('/api/chat'" in CHAT_HTML
    assert "textContent = text" in CHAT_HTML
    assert "innerHTML" not in CHAT_HTML
    assert "Offline demo snapshot" in CHAT_HTML
    assert "Availability unverified" in CHAT_HTML
    assert "Check current source" in CHAT_HTML
    assert "price not recorded" in CHAT_HTML


def test_live_chat_html_distinguishes_current_search_observations():
    page = chat_html("live")

    assert "Live refresh enabled" in page
    assert "Open current source" in page
    assert "Observed in live search" in page
    assert "Offline demo snapshot" not in page


def test_chat_server_processes_message_and_persists_structured_state(tmp_path):
    listing_db = tmp_path / "listings.sqlite"
    session_db = tmp_path / "sessions.sqlite"
    shutil.copy2(casita.DEMO_FIXTURE, listing_db)
    server = create_chat_server(
        listing_db=listing_db,
        session_db=session_db,
        interpreter=RuleBasedInterpreter(),
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        request = Request(
            f"http://{host}:{port}/api/chat",
            data=json.dumps({
                "session": "browser-test",
                "message": "Find 2 bedrooms under $5,000",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)

        assert response.status == 200
        assert payload["search_results"]["total_matched"] > 0
        first_listing = payload["search_results"]["matches"][0]["listing"]
        assert first_listing["last_seen"] is not None
        with sqlite3.connect(session_db) as conn:
            state = load_session(conn, "browser-test")
        assert state is not None
        assert state.profile.max_price == 5000
        assert state.profile.min_beds == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
