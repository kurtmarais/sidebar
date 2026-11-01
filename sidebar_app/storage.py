"""SQLite persistence for categories (boards), cards and settings.

Every change is committed immediately, so nothing depends on the
application shutting down cleanly.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 3

# Card types that sync to the phone (Supabase). Images, files and secrets stay on the PC.
SYNC_TYPES = ("note", "checklist", "code", "links")

CARD_TYPES = ("note", "checklist", "code", "links", "image", "file", "secret")

PRIORITY_LABELS = {0: "None", 1: "Low", 2: "Medium", 3: "High"}

DEFAULT_SETTINGS: dict[str, Any] = {
    "title": "My Desktop",
    "edge": "right",              # "right" | "left"
    "screen": "",                 # screen name; "" = primary
    "panel_width": 340,
    "theme": "dark",              # "dark" | "light"
    "open_mode": "both",          # "both" | "hover" | "click"
    "open_delay_ms": 300,         # cursor must rest on the strip this long
    "collapse_delay_ms": 450,
    "always_on_top": True,
    "strip_position": 0.30,       # 0..1, top of the edge strip
    "strip_length": 0.40,         # fraction of screen height
    "pinned": False,
    "current_board": 0,           # 0 = all categories
    "hotkey_enabled": True,       # Ctrl+Shift+S
    "follow_window": True,        # notes that follow the window in front
    "suggest_meeting_notes": True,
    "focus_minutes": 25,
    "auto_update": True,          # git installs: pull the latest version when closed
    "show_quickbar": True,        # focus-sound and app-launcher tiles
    "focus_sound_url": "https://www.youtube.com/watch?v=co7KgV2edvI",
    "focus_sound_name": "Airplane cabin",
    "focus_sound_browser": False, # True = open YouTube in the browser instead of playing in-app
    "launch_app_path": "",        # "" = look for caffeine64.exe
    "launch_app_name": "",
}


@dataclass
class Board:
    id: int
    name: str
    position: int
    uid: str = ""


@dataclass
class Card:
    id: int
    uid: str
    board_id: int
    type: str
    title: str
    color: str
    content: dict
    position: int
    collapsed: bool = False
    priority: int = 0
    due_date: str | None = None     # ISO date "YYYY-MM-DD"
    remind_at: float | None = None  # epoch seconds
    archived: bool = False
    archived_at: float | None = None
    link_app: str = ""              # process name, e.g. "excel.exe"
    link_title: str = ""            # window title must contain this
    starred: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def search_text(self) -> str:
        """All searchable text on the card, lower-cased.

        Secret notes only expose their title.
        """
        parts = [self.title]
        c = self.content or {}
        if self.type in ("note", "code"):
            parts.append(c.get("text", ""))
        elif self.type == "checklist":
            parts.extend(i.get("text", "") for i in c.get("items", []))
        elif self.type == "links":
            for link in c.get("links", []):
                parts.append(link.get("label", ""))
                parts.append(link.get("url", ""))
        elif self.type in ("file", "image"):
            parts.append(c.get("name", ""))
            parts.append(c.get("path", ""))
        return "\n".join(parts).lower()

    def due(self) -> date | None:
        if not self.due_date:
            return None
        try:
            return date.fromisoformat(self.due_date)
        except ValueError:
            return None

    def matches_window(self, exe: str, title: str) -> bool:
        """True if this card is linked to the given foreground window."""
        if not (self.link_app or self.link_title):
            return False
        if self.link_app and self.link_app.lower() != (exe or "").lower():
            return False
        if self.link_title and self.link_title.lower() not in (title or "").lower():
            return False
        return True


_COLUMNS = (
    "id", "uid", "board_id", "type", "title", "color", "content", "position",
    "collapsed", "priority", "due_date", "remind_at", "archived", "archived_at",
    "link_app", "link_title", "created_at", "updated_at", "starred",
)


def _card_from_row(row: sqlite3.Row) -> Card:
    try:
        content = json.loads(row["content"] or "{}")
    except json.JSONDecodeError:
        content = {}
    return Card(
        id=row["id"], uid=row["uid"], board_id=row["board_id"], type=row["type"],
        title=row["title"], color=row["color"], content=content,
        position=row["position"], collapsed=bool(row["collapsed"]),
        priority=row["priority"], due_date=row["due_date"],
        remind_at=row["remind_at"], archived=bool(row["archived"]),
        archived_at=row["archived_at"], link_app=row["link_app"] or "",
        link_title=row["link_title"] or "", created_at=row["created_at"],
        updated_at=row["updated_at"], starred=bool(row["starred"]),
    )


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        memory = str(path) == ":memory:"
        if not memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if not memory:
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self._migrate()
        self._seed()

    # ------------------------------------------------------------------ setup
    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            with self.conn:
                self.conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS boards (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        position INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS cards (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        uid TEXT NOT NULL UNIQUE,
                        board_id INTEGER NOT NULL
                            REFERENCES boards(id) ON DELETE CASCADE,
                        type TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        color TEXT NOT NULL DEFAULT 'yellow',
                        content TEXT NOT NULL DEFAULT '{}',
                        position INTEGER NOT NULL DEFAULT 0,
                        collapsed INTEGER NOT NULL DEFAULT 0,
                        priority INTEGER NOT NULL DEFAULT 0,
                        due_date TEXT,
                        remind_at REAL,
                        archived INTEGER NOT NULL DEFAULT 0,
                        archived_at REAL,
                        link_app TEXT NOT NULL DEFAULT '',
                        link_title TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_cards_position ON cards(position);
                    CREATE INDEX IF NOT EXISTS idx_cards_remind ON cards(remind_at);
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    """
                )
                self.conn.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            # Sync support: stable board ids, change tracking and delete markers.
            with self.conn:
                self.conn.executescript(
                    """
                    ALTER TABLE boards ADD COLUMN uid TEXT;
                    ALTER TABLE boards ADD COLUMN updated_at REAL NOT NULL DEFAULT 0;
                    ALTER TABLE boards ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1;
                    ALTER TABLE boards ADD COLUMN pushed INTEGER NOT NULL DEFAULT 0;
                    ALTER TABLE cards ADD COLUMN dirty INTEGER NOT NULL DEFAULT 1;
                    ALTER TABLE cards ADD COLUMN pushed INTEGER NOT NULL DEFAULT 0;
                    CREATE TABLE IF NOT EXISTS tombstones (
                        kind TEXT NOT NULL,
                        uid TEXT NOT NULL,
                        deleted_at REAL NOT NULL,
                        PRIMARY KEY (kind, uid)
                    );
                    """
                )
                for (bid,) in self.conn.execute("SELECT id FROM boards WHERE uid IS NULL").fetchall():
                    self.conn.execute("UPDATE boards SET uid = ?, updated_at = ? WHERE id = ?",
                                      (uuid.uuid4().hex, time.time(), bid))
                self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_boards_uid ON boards(uid)")
                self.conn.execute("PRAGMA user_version = 2")
            version = 2
        if version < 3:
            with self.conn:
                self.conn.execute("ALTER TABLE cards ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
                self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _seed(self) -> None:
        if self.conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0]:
            return
        work = self.add_board("Work")
        self.add_board("Research")
        self.add_board("Personal")
        self.add_card(
            work.id, "checklist", title="Getting started", color="yellow",
            content={"items": [
                {"text": "Rest the cursor on the blue strip to open the sidebar", "done": True},
                {"text": "Ctrl+Shift+S opens or closes it from any app", "done": False},
                {"text": "+ New adds a note, checklist, code, links, secret…", "done": False},
                {"text": "Drag files or paste a screenshot (Ctrl+V) onto the sidebar", "done": False},
                {"text": "Hover a card and press the tick to archive it", "done": False},
                {"text": "Card menu: due date, reminder, priority, link to window", "done": False},
            ]},
        )

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        fallback = DEFAULT_SETTINGS.get(key) if default is None else default
        if row is None:
            return fallback
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return fallback

    def set_setting(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def settings(self) -> dict[str, Any]:
        result = dict(DEFAULT_SETTINGS)
        for row in self.conn.execute("SELECT key, value FROM settings"):
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                pass
        return result

    # ----------------------------------------------------------------- boards
    def boards(self) -> list[Board]:
        rows = self.conn.execute("SELECT id, name, position, uid FROM boards ORDER BY position, id")
        return [Board(r["id"], r["name"], r["position"], r["uid"] or "") for r in rows]

    def add_board(self, name: str) -> Board:
        pos = self.conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM boards").fetchone()[0]
        uid = uuid.uuid4().hex
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO boards(name, position, uid, updated_at, dirty) VALUES(?, ?, ?, ?, 1)",
                (name, pos, uid, time.time()))
        return Board(cur.lastrowid, name, pos, uid)

    def rename_board(self, board_id: int, name: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE boards SET name = ?, updated_at = ?, dirty = 1 WHERE id = ?",
                              (name, time.time(), board_id))

    def delete_board(self, board_id: int) -> None:
        """Delete a category and its cards. The last remaining one is kept."""
        if len(self.boards()) <= 1:
            raise ValueError("At least one category must remain")
        now = time.time()
        with self.conn:
            row = self.conn.execute("SELECT uid FROM boards WHERE id = ?", (board_id,)).fetchone()
            if row and row["uid"]:
                self._tombstone("board", row["uid"], now)
            for c in self.conn.execute("SELECT uid, type FROM cards WHERE board_id = ?", (board_id,)).fetchall():
                if c["type"] in SYNC_TYPES:
                    self._tombstone("card", c["uid"], now)
            self.conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))

    def set_board_order(self, board_ids: Iterable[int]) -> None:
        now = time.time()
        with self.conn:
            for pos, bid in enumerate(board_ids):
                self.conn.execute("UPDATE boards SET position = ?, updated_at = ?, dirty = 1 "
                                  "WHERE id = ? AND position != ?", (pos, now, bid, pos))

    # ------------------------------------------------------------------ cards
    def cards(self) -> list[Card]:
        rows = self.conn.execute("SELECT * FROM cards ORDER BY position, id")
        return [_card_from_row(r) for r in rows]

    def card(self, card_id: int) -> Card | None:
        row = self.conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return _card_from_row(row) if row else None

    def add_card(self, board_id: int, type: str, title: str = "", color: str = "yellow",
                 content: dict | None = None, **extra: Any) -> Card:
        if type not in CARD_TYPES:
            raise ValueError(f"Unknown card type: {type}")
        unknown = set(extra) - {"link_app", "link_title", "due_date", "remind_at", "priority"}
        if unknown:
            raise ValueError(f"Unknown fields: {sorted(unknown)}")
        now = time.time()
        pos = self.conn.execute("SELECT COALESCE(MIN(position), 1) - 1 FROM cards").fetchone()[0]
        fields = {
            "uid": uuid.uuid4().hex, "board_id": board_id, "type": type, "title": title,
            "color": color, "content": json.dumps(content or {}), "position": pos,
            "created_at": now, "updated_at": now, **extra,
        }
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self.conn:
            cur = self.conn.execute(f"INSERT INTO cards({cols}) VALUES({marks})", tuple(fields.values()))
        return self.card(cur.lastrowid)  # type: ignore[return-value]

    _UPDATABLE = {
        "board_id", "type", "title", "color", "content", "collapsed", "priority",
        "due_date", "remind_at", "archived", "archived_at", "link_app", "link_title", "starred",
    }

    def update_card(self, card_id: int, **fields: Any) -> None:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"Cannot update fields: {sorted(unknown)}")
        if not fields:
            return
        if "content" in fields:
            fields["content"] = json.dumps(fields["content"])
        for flag in ("collapsed", "archived", "starred"):
            if flag in fields:
                fields[flag] = int(bool(fields[flag]))
        fields["updated_at"] = time.time()
        fields["dirty"] = 1
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn:
            self.conn.execute(f"UPDATE cards SET {cols} WHERE id = ?", (*fields.values(), card_id))

    def archive_card(self, card_id: int, archived: bool = True) -> None:
        self.update_card(card_id, archived=archived,
                         archived_at=time.time() if archived else None)

    def delete_card(self, card_id: int) -> None:
        with self.conn:
            row = self.conn.execute("SELECT uid, type FROM cards WHERE id = ?", (card_id,)).fetchone()
            if row and row["type"] in SYNC_TYPES:
                self._tombstone("card", row["uid"], time.time())
            self.conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))

    def set_card_order(self, card_ids: Iterable[int]) -> None:
        now = time.time()
        with self.conn:
            for pos, cid in enumerate(card_ids):
                self.conn.execute("UPDATE cards SET position = ?, updated_at = ?, dirty = 1 "
                                  "WHERE id = ? AND position != ?", (pos, now, cid, pos))

    # -------------------------------------------------------------------- sync
    def _tombstone(self, kind: str, uid: str, when: float) -> None:
        self.conn.execute("INSERT OR REPLACE INTO tombstones(kind, uid, deleted_at) VALUES(?, ?, ?)",
                          (kind, uid, when))

    def dirty_boards(self) -> list[dict]:
        rows = self.conn.execute("SELECT uid, name, position, updated_at, pushed FROM boards WHERE dirty = 1")
        return [dict(r) for r in rows]

    def dirty_cards(self) -> list[dict]:
        marks = ", ".join("?" for _ in SYNC_TYPES)
        rows = self.conn.execute(
            f"SELECT c.*, b.uid AS board_uid FROM cards c JOIN boards b ON b.id = c.board_id "
            f"WHERE c.dirty = 1 AND c.type IN ({marks})", SYNC_TYPES).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys() if k not in ("id", "board_id", "dirty", "pushed")}
            try:
                d["content"] = json.loads(d["content"] or "{}")
            except json.JSONDecodeError:
                d["content"] = {}
            d["collapsed"] = bool(d["collapsed"])
            d["archived"] = bool(d["archived"])
            d["starred"] = bool(d["starred"])
            out.append(d)
        return out

    def tombstones(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT kind, uid, deleted_at FROM tombstones")]

    def mark_pushed(self, boards: list[dict], cards: list[dict], tombs: list[dict]) -> None:
        """Clear change flags for rows that were uploaded and not edited since."""
        with self.conn:
            for b in boards:
                self.conn.execute("UPDATE boards SET dirty = 0, pushed = 1 WHERE uid = ? AND updated_at = ?",
                                  (b["uid"], b["updated_at"]))
            for c in cards:
                self.conn.execute("UPDATE cards SET dirty = 0, pushed = 1 WHERE uid = ? AND updated_at = ?",
                                  (c["uid"], c["updated_at"]))
            for t in tombs:
                self.conn.execute("DELETE FROM tombstones WHERE kind = ? AND uid = ? AND deleted_at = ?",
                                  (t["kind"], t["uid"], t["deleted_at"]))

    def mark_all_dirty(self) -> None:
        """Re-upload everything (first sign-in, or a new Supabase project)."""
        with self.conn:
            self.conn.execute("UPDATE boards SET dirty = 1, pushed = 0")
            self.conn.execute("UPDATE cards SET dirty = 1, pushed = 0")

    def board_uids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT uid FROM boards")]

    def rekey_board(self, old_uid: str, new_uid: str) -> None:
        """A local category was matched to an existing cloud one: take its id."""
        with self.conn:
            self.conn.execute("UPDATE boards SET uid = ?, pushed = 1, dirty = 0 WHERE uid = ?", (new_uid, old_uid))

    def apply_remote_board(self, r: dict) -> bool:
        """Merge a board from the server (newest edit wins). Returns True if changed."""
        local = self.conn.execute("SELECT * FROM boards WHERE uid = ?", (r["uid"],)).fetchone()
        if r.get("deleted"):
            if local is None or (local["dirty"] and local["updated_at"] > r["updated_at"]):
                return False
            if self.conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0] <= 1:
                return False                      # never remove the last category
            with self.conn:
                self.conn.execute("DELETE FROM boards WHERE id = ?", (local["id"],))
            return True
        with self.conn:
            if local is None:
                self.conn.execute(
                    "INSERT INTO boards(name, position, uid, updated_at, dirty, pushed) VALUES(?, ?, ?, ?, 0, 1)",
                    (r["name"], r.get("position", 0), r["uid"], r["updated_at"]))
                return True
            if r["updated_at"] > local["updated_at"]:
                self.conn.execute("UPDATE boards SET name = ?, position = ?, updated_at = ?, dirty = 0, pushed = 1 "
                                  "WHERE id = ?", (r["name"], r.get("position", 0), r["updated_at"], local["id"]))
                return True
        return False

    def apply_remote_card(self, r: dict) -> int | None:
        """Merge a card from the server (newest edit wins). Returns the local id it
        affects (inserted, updated or deleted), or None if nothing changed."""
        local = self.conn.execute("SELECT * FROM cards WHERE uid = ?", (r["uid"],)).fetchone()
        if r.get("deleted"):
            if local is None or (local["dirty"] and local["updated_at"] > r["updated_at"]):
                return None
            with self.conn:
                self.conn.execute("DELETE FROM cards WHERE id = ?", (local["id"],))
            return local["id"]
        if r.get("type") not in SYNC_TYPES:
            return None
        if local is not None and r["updated_at"] <= local["updated_at"]:
            return None
        board = self.conn.execute("SELECT id FROM boards WHERE uid = ?", (r.get("board_uid"),)).fetchone()
        board_id = board["id"] if board else self.boards()[0].id
        values = (
            board_id, r["type"], r.get("title") or "", r.get("color") or "yellow",
            json.dumps(r.get("content") or {}), r.get("position", 0), int(bool(r.get("collapsed"))),
            int(r.get("priority") or 0), r.get("due_date"), r.get("remind_at"), int(bool(r.get("archived"))),
            r.get("archived_at"), r.get("link_app") or "", r.get("link_title") or "", r["updated_at"],
            int(bool(r.get("starred"))),
        )
        with self.conn:
            if local is None:
                cur = self.conn.execute(
                    "INSERT INTO cards(board_id, type, title, color, content, position, collapsed, priority, "
                    "due_date, remind_at, archived, archived_at, link_app, link_title, updated_at, starred, "
                    "uid, created_at, dirty, pushed) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)",
                    (*values, r["uid"], r.get("created_at") or r["updated_at"]))
                return cur.lastrowid
            self.conn.execute(
                "UPDATE cards SET board_id = ?, type = ?, title = ?, color = ?, content = ?, position = ?, "
                "collapsed = ?, priority = ?, due_date = ?, remind_at = ?, archived = ?, archived_at = ?, "
                "link_app = ?, link_title = ?, updated_at = ?, starred = ?, dirty = 0, pushed = 1 WHERE id = ?",
                (*values, local["id"]))
            return local["id"]

    def due_reminders(self, now: float | None = None) -> list[Card]:
        now = time.time() if now is None else now
        rows = self.conn.execute(
            "SELECT * FROM cards WHERE remind_at IS NOT NULL AND remind_at <= ? "
            "AND archived = 0 ORDER BY remind_at", (now,))
        return [_card_from_row(r) for r in rows]

    # ---------------------------------------------------------------- backups
    def export_data(self, include_secrets: bool = False) -> dict:
        boards = {b.id: b.name for b in self.boards()}
        cards = []
        for c in self.cards():
            if c.type == "secret" and not include_secrets:
                continue
            row = {k: getattr(c, k) for k in _COLUMNS if k not in ("id", "board_id")}
            row["board"] = boards.get(c.board_id, "")
            cards.append(row)
        return {
            "app": "sidebar",
            "schema": SCHEMA_VERSION,
            "exported_at": time.time(),
            "boards": [{"name": b.name, "position": b.position} for b in self.boards()],
            "cards": cards,
            "settings": self.settings(),
        }

    def import_data(self, data: dict) -> int:
        """Merge a backup: add missing categories and cards, never overwrite.

        Cards are matched by ``uid``; categories by name. Returns the number
        of cards added. Safe to run more than once.
        """
        if data.get("app") != "sidebar":
            raise ValueError("Not a Sidebar backup file")
        by_name = {b.name: b.id for b in self.boards()}
        existing = {r[0] for r in self.conn.execute("SELECT uid FROM cards")}
        added = 0
        for b in sorted(data.get("boards") or [], key=lambda b: b.get("position", 0)):
            if b.get("name") and b["name"] not in by_name:
                by_name[b["name"]] = self.add_board(b["name"]).id
        fallback = self.boards()[0].id
        now = time.time()
        with self.conn:
            for c in data.get("cards") or []:
                uid = c.get("uid")
                if not uid or uid in existing or c.get("type") not in CARD_TYPES:
                    continue
                board_id = by_name.get(c.get("board", ""), fallback)
                self.conn.execute(
                    "INSERT INTO cards(uid, board_id, type, title, color, content, position, "
                    "collapsed, priority, due_date, remind_at, archived, archived_at, "
                    "link_app, link_title, created_at, updated_at, starred) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid, board_id, c["type"], c.get("title", ""), c.get("color", "yellow"),
                        json.dumps(c.get("content") or {}), c.get("position", 0),
                        int(bool(c.get("collapsed"))), int(c.get("priority") or 0),
                        c.get("due_date"), c.get("remind_at"), int(bool(c.get("archived"))),
                        c.get("archived_at"), c.get("link_app", ""), c.get("link_title", ""),
                        c.get("created_at", now), c.get("updated_at", now), int(bool(c.get("starred"))),
                    ),
                )
                existing.add(uid)
                added += 1
        return added

    def backup_to(self, path: str | Path) -> None:
        """Write a consistent copy of the database file to ``path``."""
        dest = sqlite3.connect(str(path))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
