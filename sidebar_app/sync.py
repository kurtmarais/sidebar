"""Phone sync through Supabase.

The desktop keeps its own SQLite database and exchanges changes with a
Supabase project over its REST API (no extra Python packages). Only text
cards sync (note, checklist, code, links) and their categories; images,
files and secret notes never leave the PC.

Flow of one sync cycle:
  1. prepare()   main thread  - collect local changes and cursors
  2. transfer()  worker       - refresh sign-in, download newer rows, upload changes
  3. apply()     main thread  - merge downloaded rows, clear uploaded flags

Conflicts: newest edit wins, per card. The database trigger ignores uploads
older than what it already holds, and the desktop ignores downloads older
than its own copy.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from . import winplatform
from .storage import Database

PAGE = 1000
TIMEOUT = 20
CARD_FIELDS = ("uid", "board_uid", "type", "title", "color", "content", "position", "collapsed",
               "priority", "due_date", "remind_at", "archived", "archived_at", "link_app",
               "link_title", "created_at", "updated_at", "starred")


class SyncError(Exception):
    pass


class AuthError(SyncError):
    """Sign-in rejected or session expired: the user must sign in again."""


Opener = Callable[..., Any]


class SupabaseClient:
    def __init__(self, url: str, key: str, opener: Opener | None = None):
        self.url = url.rstrip("/")
        self.key = key.strip()
        self._open = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body: Any = None, token: str | None = None,
                 params: dict | None = None, headers: dict | None = None) -> Any:
        url = f"{self.url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, safe=",.()*:")
        # The publishable/anon key goes in "apikey"; only a signed-in user's token
        # goes in Authorization (new-style publishable keys are not JWTs).
        h = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self._open(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (400, 401, 403) and ("/auth/" in path or "JWT" in detail or exc.code == 401):
                raise AuthError(_message(detail) or f"Sign-in failed ({exc.code})") from exc
            raise SyncError(f"{exc.code}: {_message(detail) or detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SyncError(f"Cannot reach Supabase ({getattr(exc, 'reason', exc)})") from exc
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------------ auth
    def _session(self, data: dict) -> dict:
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
            "user_id": (data.get("user") or {}).get("id", ""),
            "email": (data.get("user") or {}).get("email", ""),
        }

    def sign_in(self, email: str, password: str) -> dict:
        data = self._request("POST", "/auth/v1/token", {"email": email, "password": password},
                             params={"grant_type": "password"})
        return self._session(data)

    def refresh(self, refresh_token: str) -> dict:
        data = self._request("POST", "/auth/v1/token", {"refresh_token": refresh_token},
                             params={"grant_type": "refresh_token"})
        return self._session(data)

    # ------------------------------------------------------------------ data
    def changes_since(self, table: str, cursor: str, token: str) -> list[dict]:
        rows: list[dict] = []
        while True:
            params = {"select": "*", "order": "server_updated_at.asc", "limit": str(PAGE)}
            if cursor:
                params["server_updated_at"] = f"gt.{cursor}"
            page = self._request("GET", f"/rest/v1/{table}", token=token, params=params) or []
            rows.extend(page)
            if len(page) < PAGE:
                return rows
            cursor = page[-1]["server_updated_at"]

    def upsert(self, table: str, rows: list[dict], token: str) -> None:
        for i in range(0, len(rows), 500):
            self._request("POST", f"/rest/v1/{table}", rows[i:i + 500], token=token,
                          params={"on_conflict": "uid"},
                          headers={"Prefer": "resolution=merge-duplicates,return=minimal"})


def _message(detail: str) -> str:
    try:
        d = json.loads(detail)
    except ValueError:
        return ""
    return d.get("error_description") or d.get("msg") or d.get("message") or d.get("error") or ""


# --------------------------------------------------------- token storage
def protect_token(token: str) -> str:
    """Refresh tokens are stored encrypted for this Windows account."""
    if winplatform.dpapi_available():
        return "dpapi:" + base64.b64encode(winplatform.dpapi_protect(token.encode(), b"sidebar-sync")).decode()
    return "plain:" + token


def unprotect_token(stored: str) -> str:
    if not stored:
        return ""
    kind, _, value = stored.partition(":")
    if kind == "dpapi":
        try:
            return winplatform.dpapi_unprotect(base64.b64decode(value), b"sidebar-sync").decode()
        except (OSError, RuntimeError, ValueError):
            return ""
    return value if kind == "plain" else ""


# ------------------------------------------------------------- the cycle
@dataclass
class Payload:
    url: str
    key: str
    refresh_token: str
    access_token: str
    expires_at: float
    board_cursor: str
    card_cursor: str
    boards: list[dict]
    cards: list[dict]
    tombs: list[dict]
    known_board_uids: list[str] = field(default_factory=list)


@dataclass
class Result:
    session: dict | None = None
    boards: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)
    board_cursor: str = ""
    card_cursor: str = ""
    pushed: tuple[list, list, list] = ([], [], [])
    rekeyed: dict[str, str] = field(default_factory=dict)     # local board uid -> cloud uid
    needs_star_update: bool = False   # Supabase lacks the starred column (SQL update not run yet)


def is_configured(settings: dict) -> bool:
    return bool(settings.get("sync_url") and settings.get("sync_key") and settings.get("sync_refresh"))


def prepare(db: Database, settings: dict, access: dict) -> Payload:
    return Payload(
        url=settings["sync_url"], key=settings["sync_key"],
        refresh_token=unprotect_token(settings.get("sync_refresh", "")),
        access_token=access.get("access_token", ""), expires_at=access.get("expires_at", 0.0),
        board_cursor=settings.get("sync_board_cursor", ""), card_cursor=settings.get("sync_card_cursor", ""),
        boards=db.dirty_boards(), cards=db.dirty_cards(), tombs=db.tombstones(),
        known_board_uids=db.board_uids(),
    )


def transfer(p: Payload, opener: Opener | None = None) -> Result:
    client = SupabaseClient(p.url, p.key, opener)
    res = Result(board_cursor=p.board_cursor, card_cursor=p.card_cursor)
    token = p.access_token
    if not token or p.expires_at - time.time() < 120:
        if not p.refresh_token:
            raise AuthError("Not signed in")
        res.session = client.refresh(p.refresh_token)
        token = res.session["access_token"]

    try:
        res.boards = client.changes_since("boards", p.board_cursor, token)
    except AuthError:
        if not p.refresh_token or res.session:   # already fresh: a real rejection
            raise
        res.session = client.refresh(p.refresh_token)   # token expired early: refresh once
        token = res.session["access_token"]
        res.boards = client.changes_since("boards", p.board_cursor, token)
    res.cards = client.changes_since("cards", p.card_cursor, token)
    if res.boards:
        res.board_cursor = res.boards[-1]["server_updated_at"]
    if res.cards:
        res.card_cursor = res.cards[-1]["server_updated_at"]

    # A category that was never uploaded but has the same name as one already in
    # the cloud (e.g. the default Work/Research/Personal on a second device) is
    # merged into it rather than uploaded as a duplicate.
    known = set(p.known_board_uids)
    cloud_by_name: dict[str, str] = {}
    for b in res.boards:
        if not b.get("deleted") and b["uid"] not in known:
            cloud_by_name.setdefault(b["name"], b["uid"])
    for b in p.boards:
        if not b.get("pushed") and b["name"] in cloud_by_name:
            res.rekeyed[b["uid"]] = cloud_by_name.pop(b["name"])

    board_rows = [{"uid": b["uid"], "name": b["name"], "position": b["position"],
                   "updated_at": b["updated_at"], "deleted": False}
                  for b in p.boards if b["uid"] not in res.rekeyed]
    card_rows = [{**{k: c.get(k) for k in CARD_FIELDS}, "deleted": False} for c in p.cards]
    for row in card_rows:
        row["board_uid"] = res.rekeyed.get(row["board_uid"], row["board_uid"])
    board_tombs = [{"uid": t["uid"], "deleted": True, "updated_at": t["deleted_at"]}
                   for t in p.tombs if t["kind"] == "board"]
    card_tombs = [{"uid": t["uid"], "deleted": True, "updated_at": t["deleted_at"]}
                  for t in p.tombs if t["kind"] == "card"]
    for table, rows in (("boards", board_rows), ("cards", card_rows),
                        ("cards", card_tombs), ("boards", board_tombs)):
        if not rows:
            continue
        try:
            client.upsert(table, rows, token)
        except SyncError as exc:
            # Supabase not yet updated with the "starred" column: sync everything else.
            if "starred" not in str(exc) or not any("starred" in r for r in rows):
                raise
            client.upsert(table, [{k: v for k, v in r.items() if k != "starred"} for r in rows], token)
            res.needs_star_update = True
    res.pushed = (p.boards, p.cards, p.tombs)
    return res


def apply(db: Database, res: Result) -> tuple[set[int], bool]:
    """Merge downloaded rows. Returns (local card ids changed, categories changed)."""
    boards_changed = bool(res.rekeyed)
    for old, new in res.rekeyed.items():
        db.rekey_board(old, new)
    for b in res.boards:
        boards_changed |= db.apply_remote_board(b)
    changed: set[int] = set()
    for c in res.cards:
        cid = db.apply_remote_card(c)
        if cid is not None:
            changed.add(cid)
    db.mark_pushed(*res.pushed)
    db.set_setting("sync_board_cursor", res.board_cursor)
    db.set_setting("sync_card_cursor", res.card_cursor)
    if res.session:
        db.set_setting("sync_refresh", protect_token(res.session["refresh_token"]))
    db.set_setting("sync_last", time.time())
    return changed, boards_changed


def sync_now(db: Database, opener: Opener | None = None, access: dict | None = None) -> tuple[set[int], bool]:
    """Run one full cycle on the calling thread (used by tests and the Sync now button)."""
    access = access if access is not None else {}
    res = transfer(prepare(db, db.settings(), access), opener)
    if res.session:
        access.update(res.session)
    return apply(db, res)


def sign_in(db: Database, url: str, key: str, email: str, password: str,
            opener: Opener | None = None) -> dict:
    """Sign in and remember the session. A different account or project
    starts from scratch: everything local is uploaded again."""
    client = SupabaseClient(url, key, opener)
    session = client.sign_in(email, password)
    s = db.settings()
    if (s.get("sync_url"), s.get("sync_user")) != (url, session["user_id"]):
        db.mark_all_dirty()
        db.set_setting("sync_board_cursor", "")
        db.set_setting("sync_card_cursor", "")
    db.set_setting("sync_url", url)
    db.set_setting("sync_key", key)
    db.set_setting("sync_email", email)
    db.set_setting("sync_user", session["user_id"])
    db.set_setting("sync_refresh", protect_token(session["refresh_token"]))
    return session


def sign_out(db: Database) -> None:
    db.set_setting("sync_refresh", "")
