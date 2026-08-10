"""Local HTTP interface for the single Casita conversational agent."""

import json
import sqlite3
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import storage
from .agent import CasitaAgent, ConversationState, Interpreter
from .agent_sessions import load_session, save_session
from .verifier import ResponseVerifier


MAX_MESSAGE_BYTES = 16_384


def process_chat_message(
    *,
    listing_db: Path,
    session_db: Path,
    session_id: str,
    message: str,
    interpreter: Interpreter,
    verifier: ResponseVerifier | None = None,
):
    with sqlite3.connect(session_db) as session_conn:
        state = load_session(session_conn, session_id) or ConversationState()
        with storage.connect_path(listing_db) as listing_conn:
            agent = CasitaAgent(
                listing_conn,
                interpreter,
                state=state,
                verifier=verifier,
            )
            response = agent.respond(message)
        save_session(session_conn, session_id, agent.state)
    return response


def create_chat_server(
    *,
    listing_db: Path,
    session_db: Path,
    interpreter: Interpreter,
    verifier: ResponseVerifier | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> ThreadingHTTPServer:
    lock = threading.Lock()

    class ChatHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path)
            if path.path == "/":
                self._send(HTTPStatus.OK, CHAT_HTML, "text/html; charset=utf-8")
                return
            if path.path == "/api/state":
                session_id = parse_qs(path.query).get("session", [""])[0]
                if not _valid_session_id(session_id):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid session"})
                    return
                with sqlite3.connect(session_db) as session_conn:
                    state = load_session(session_conn, session_id) or ConversationState()
                self._json(HTTPStatus.OK, {"state": state.model_dump(mode="json")})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/chat":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_MESSAGE_BYTES:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid request size"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            session_id = str(payload.get("session", ""))
            message = str(payload.get("message", "")).strip()
            if not _valid_session_id(session_id) or not message:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "session and message are required"})
                return
            with lock:
                response = process_chat_message(
                    listing_db=listing_db,
                    session_db=session_db,
                    session_id=session_id,
                    message=message,
                    interpreter=interpreter,
                    verifier=verifier,
                )
            self._json(HTTPStatus.OK, response.model_dump(mode="json"))

        def log_message(self, format: str, *args) -> None:
            return

        def _json(self, status: HTTPStatus, payload: dict) -> None:
            self._send(status, json.dumps(payload), "application/json; charset=utf-8")

        def _send(self, status: HTTPStatus, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(encoded)

    return ThreadingHTTPServer((host, port), ChatHandler)


def _valid_session_id(value: str) -> bool:
    return bool(value and len(value) <= 64 and all(char.isalnum() or char in "-_" for char in value))


CHAT_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Casita Chat</title>
<style>
:root { color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: light-dark(#f4f1e9, #162019); color: light-dark(#17251c, #edf5ef); }
main { width: min(860px, 100%); min-height: 100vh; margin: auto; display: grid; grid-template-rows: auto 1fr auto; }
header { padding: 24px 20px 16px; border-bottom: 1px solid light-dark(#d7d2c6, #34443a); }
h1 { margin: 0; font-family: Georgia, serif; font-weight: 500; }
header p { margin: 6px 0 0; color: light-dark(#5c665f, #b5c3b9); }
#messages { padding: 22px 20px; display: flex; flex-direction: column; gap: 14px; }
.message { max-width: 88%; padding: 13px 15px; border-radius: 16px; white-space: pre-wrap; line-height: 1.45; }
.user { align-self: flex-end; background: light-dark(#2f6a4a, #72b98d); color: light-dark(#fff, #102017); }
.agent { align-self: flex-start; background: light-dark(#fff, #25342b); border: 1px solid light-dark(#ded9ce, #3d5044); }
.results { align-self: stretch; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.result { display: block; padding: 12px; border: 1px solid light-dark(#d8d3c7, #3a4b40); border-radius: 12px; color: inherit; text-decoration: none; background: light-dark(#faf9f5, #202d25); }
.result strong, .result span { display: block; }
.result span { margin-top: 4px; color: light-dark(#637067, #b4c3b8); }
form { position: sticky; bottom: 0; display: grid; grid-template-columns: 1fr auto; gap: 10px; padding: 16px 20px 22px; background: light-dark(#f4f1e9ee, #162019ee); backdrop-filter: blur(10px); }
input, button { font: inherit; border-radius: 999px; padding: 13px 16px; }
input { border: 1px solid light-dark(#c8c2b5, #435648); background: light-dark(#fff, #223027); color: inherit; }
button { border: 0; background: light-dark(#284f39, #80c798); color: light-dark(#fff, #102017); font-weight: 600; cursor: pointer; }
button:disabled { opacity: .55; cursor: wait; }
@media (max-width: 520px) { .message { max-width: 96%; } form { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<main>
  <header><h1>Casita</h1><p>Grounded conversational rental search</p></header>
  <section id="messages" aria-live="polite">
    <div class="message agent">Tell me what you need in a rental. You can refine the results or ask me to compare them.</div>
  </section>
  <form id="chat-form">
    <input id="message" autocomplete="off" maxlength="4000" placeholder="Two bedrooms under $5,500 near a trail…" aria-label="Message">
    <button id="send" type="submit">Send</button>
  </form>
</main>
<script>
const session = localStorage.casitaSession || (localStorage.casitaSession = crypto.randomUUID());
const form = document.getElementById('chat-form');
const input = document.getElementById('message');
const send = document.getElementById('send');
const messages = document.getElementById('messages');
function addMessage(text, kind) {
  const item = document.createElement('div');
  item.className = `message ${kind}`;
  item.textContent = text;
  messages.appendChild(item);
  item.scrollIntoView({behavior: 'smooth', block: 'end'});
}
function addResults(searchResults) {
  if (!searchResults || !searchResults.matches.length) return;
  const grid = document.createElement('div');
  grid.className = 'results';
  for (const match of searchResults.matches) {
    const facts = match.listing;
    const card = document.createElement(facts.url ? 'a' : 'div');
    card.className = 'result';
    if (facts.url) { card.href = facts.url; card.target = '_blank'; card.rel = 'noopener'; }
    const title = document.createElement('strong');
    title.textContent = facts.address || facts.key;
    const detail = document.createElement('span');
    detail.textContent = [facts.price == null ? 'Price unknown' : `$${facts.price.toLocaleString()}`, facts.neighborhood].filter(Boolean).join(' · ');
    card.append(title, detail);
    grid.appendChild(card);
  }
  messages.appendChild(grid);
}
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addMessage(text, 'user');
  input.value = '';
  send.disabled = true;
  try {
    const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session, message: text})});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Request failed');
    addMessage(payload.message, 'agent');
    addResults(payload.search_results);
  } catch (error) {
    addMessage(`Could not reach Casita: ${error.message}`, 'agent');
  } finally {
    send.disabled = false;
    input.focus();
  }
});
</script>
</body>
</html>
"""
