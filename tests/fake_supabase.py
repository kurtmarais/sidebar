"""In-process stand-in for the parts of Supabase the sidebar and phone app use.

Mimics: password and refresh-token sign-in (with refresh-token rotation),
PostgREST select with ``server_updated_at=gt.<cursor>``, ordering and limit,
upsert with ``on_conflict=uid``, the newest-edit-wins trigger, per-user row
isolation, CORS, and serving the phone app's static files.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULTS = {
    "boards": {"name": "", "position": 0, "deleted": False},
    "cards": {"board_uid": "", "type": "note", "title": "", "color": "yellow", "content": {},
              "position": 0, "collapsed": False, "priority": 0, "due_date": None, "remind_at": None,
              "archived": False, "archived_at": None, "link_app": "", "link_title": "",
              "deleted": False, "created_at": 0},
}
CARD_TYPES = {"note", "checklist", "code", "links"}


class FakeSupabase:
    def __init__(self, static_dir: Path | None = None):
        self.users = {"me@example.com": ("correct horse", "11111111-1111-1111-1111-111111111111"),
                      "other@example.com": ("pw", "22222222-2222-2222-2222-222222222222")}
        self.tables: dict[str, dict[str, dict]] = {"boards": {}, "cards": {}}
        self.access: dict[str, str] = {}      # access token -> user id
        self.refresh: dict[str, str] = {}     # refresh token -> user id
        self.key = "sb_publishable_test"
        self.static_dir = static_dir
        self.config_js: str | None = 'export const SUPABASE_URL = "";\nexport const SUPABASE_KEY = "";\n'
        self.lock = threading.Lock()
        self.requests: list[str] = []
        self._tick = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None, ctype="application/json"):
                data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):  # noqa: N802
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "apikey, authorization, content-type, prefer")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

            def _user(self):
                if self.headers.get("apikey") != server.key:
                    return None, (401, {"message": "Invalid API key"})
                auth = self.headers.get("Authorization", "")
                token = auth.removeprefix("Bearer ").strip()
                uid = server.access.get(token)
                if not uid:
                    return None, (401, {"message": "JWT expired"})
                return uid, None

            def do_GET(self):  # noqa: N802
                url = urllib.parse.urlparse(self.path)
                server.requests.append(f"GET {url.path}")
                if url.path.startswith("/rest/v1/"):
                    uid, err = self._user()
                    if err:
                        return self._send(*err)
                    table = url.path.rsplit("/", 1)[-1]
                    q = urllib.parse.parse_qs(url.query)
                    with server.lock:
                        rows = [dict(r) for r in server.tables[table].values() if r["user_id"] == uid]
                    if "server_updated_at" in q:
                        cursor = q["server_updated_at"][0].removeprefix("gt.")
                        rows = [r for r in rows if r["server_updated_at"] > cursor]
                    rows.sort(key=lambda r: r["server_updated_at"])
                    rows = rows[: int(q.get("limit", ["1000"])[0])]
                    return self._send(200, rows)
                if server.static_dir is not None:
                    rel = url.path.lstrip("/") or "index.html"
                    if rel == "config.js" and server.config_js is not None:
                        return self._send(200, server.config_js.encode(), "text/javascript")
                    f = (server.static_dir / rel).resolve()
                    if f.is_file() and server.static_dir.resolve() in f.parents:
                        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                        if f.suffix == ".webmanifest":
                            ctype = "application/manifest+json"
                        return self._send(200, f.read_bytes(), ctype)
                return self._send(404, {"message": "not found"})

            def do_POST(self):  # noqa: N802
                url = urllib.parse.urlparse(self.path)
                server.requests.append(f"POST {url.path}")
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                q = urllib.parse.parse_qs(url.query)
                if url.path == "/auth/v1/token":
                    if self.headers.get("apikey") != server.key:
                        return self._send(401, {"message": "Invalid API key"})
                    grant = q.get("grant_type", [""])[0]
                    with server.lock:
                        if grant == "password":
                            pw_uid = server.users.get(body.get("email", ""))
                            if not pw_uid or pw_uid[0] != body.get("password"):
                                return self._send(400, {"error_description": "Invalid login credentials"})
                            uid, email = pw_uid[1], body["email"]
                        elif grant == "refresh_token":
                            uid = server.refresh.pop(body.get("refresh_token", ""), None)   # rotation
                            if not uid:
                                return self._send(400, {"error_description": "Invalid Refresh Token"})
                            email = next(e for e, (_, u) in server.users.items() if u == uid)
                        else:
                            return self._send(400, {"error_description": "bad grant"})
                        access, refresh = uuid.uuid4().hex, uuid.uuid4().hex
                        server.access[access] = uid
                        server.refresh[refresh] = uid
                    return self._send(200, {"access_token": access, "refresh_token": refresh, "expires_in": 3600,
                                            "token_type": "bearer", "user": {"id": uid, "email": email}})
                if url.path.startswith("/rest/v1/"):
                    uid, err = self._user()
                    if err:
                        return self._send(*err)
                    table = url.path.rsplit("/", 1)[-1]
                    rows = body if isinstance(body, list) else [body]
                    with server.lock:
                        for row in rows:
                            if table == "cards" and row.get("type", "note") not in CARD_TYPES:
                                return self._send(400, {"message": "violates check constraint cards_type_check"})
                            existing = server.tables[table].get(row["uid"])
                            if existing is not None and existing["user_id"] != uid:
                                return self._send(403, {"message": "row-level security"})
                            if existing is None:
                                new = {**DEFAULTS[table], **row, "user_id": uid}
                            elif row.get("updated_at", 0) < existing["updated_at"]:
                                continue                                  # newest edit wins
                            else:
                                new = {**existing, **row, "user_id": uid}
                            new["server_updated_at"] = server.stamp()
                            server.tables[table][row["uid"]] = new
                    return self._send(201)
                return self._send(404, {"message": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def stamp(self) -> str:
        self._tick += 1
        base = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        return f"{base}.{self._tick:06d}+00:00"

    def expire_tokens(self) -> None:
        self.access.clear()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def epoch() -> float:
    return time.time()
