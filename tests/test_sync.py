import sqlite3
import time

import pytest

from sidebar_app import sync
from sidebar_app.storage import Database

from .fake_supabase import FakeSupabase

EMAIL, PASSWORD = "me@example.com", "correct horse"


@pytest.fixture
def server():
    with FakeSupabase() as s:
        yield s


def device(tmp_path, name, server):
    db = Database(tmp_path / f"{name}.db")
    sync.sign_in(db, server.url, server.key, EMAIL, PASSWORD)
    return db


def card_by_title(db, title):
    return next((c for c in db.cards() if c.title == title), None)


def test_wrong_password_is_reported(tmp_path, server):
    db = Database(tmp_path / "a.db")
    with pytest.raises(sync.AuthError, match="Invalid login credentials"):
        sync.sign_in(db, server.url, server.key, EMAIL, "nope")


def test_first_sync_uploads_text_cards_only(tmp_path, server):
    pc = device(tmp_path, "pc", server)
    work = pc.boards()[0].id
    pc.add_card(work, "note", title="Plan", content={"text": "x"})
    pc.add_card(work, "secret", title="Bank", content={"cipher": "zzz"})
    pc.add_card(work, "image", title="Pic", content={"asset": "a.png"})
    sync.sync_now(pc)
    titles = {r["title"] for r in server.tables["cards"].values()}
    assert titles == {"Plan", "Getting started"}            # secret and image never uploaded
    assert {r["name"] for r in server.tables["boards"].values()} == {"Work", "Research", "Personal"}
    assert not pc.dirty_cards() and not pc.dirty_boards()


def test_edit_on_one_device_reaches_the_other(tmp_path, server):
    pc, laptop = device(tmp_path, "pc", server), device(tmp_path, "laptop", server)
    pc.add_card(pc.boards()[0].id, "checklist", title="Shop",
                content={"items": [{"text": "milk", "done": False}]})
    sync.sync_now(pc)
    changed, boards_changed = sync.sync_now(laptop)
    shop = card_by_title(laptop, "Shop")
    assert shop is not None and shop.content["items"][0]["text"] == "milk" and shop.id in changed
    # same-named default categories are merged, not duplicated, on both devices and the server
    sync.sync_now(pc)
    for db in (pc, laptop):
        assert [b.name for b in db.boards()] == ["Work", "Research", "Personal"]
    assert len(server.tables["boards"]) == 3
    assert shop.board_id == laptop.boards()[0].id

    time.sleep(0.01)
    laptop.update_card(shop.id, content={"items": [{"text": "milk", "done": True}]})
    sync.sync_now(laptop)
    sync.sync_now(pc)
    assert card_by_title(pc, "Shop").content["items"][0]["done"] is True


def test_newest_edit_wins(tmp_path, server):
    pc, phone = device(tmp_path, "pc", server), device(tmp_path, "phone", server)
    c = pc.add_card(pc.boards()[0].id, "note", title="Draft")
    sync.sync_now(pc)
    sync.sync_now(phone)
    other = card_by_title(phone, "Draft")
    phone.update_card(other.id, title="Phone edit")     # older
    time.sleep(0.01)
    pc.update_card(c.id, title="PC edit")               # newer
    sync.sync_now(phone)                                 # phone uploads first
    sync.sync_now(pc)                                    # PC's newer edit still wins
    sync.sync_now(phone)
    assert pc.card(c.id).title == "PC edit"
    assert phone.card(other.id).title == "PC edit"


def test_deletes_propagate(tmp_path, server):
    pc, phone = device(tmp_path, "pc", server), device(tmp_path, "phone", server)
    c = pc.add_card(pc.boards()[0].id, "note", title="Temp")
    sync.sync_now(pc)
    sync.sync_now(phone)
    pc.delete_card(c.id)
    sync.sync_now(pc)
    assert server.tables["cards"][c.uid]["deleted"] is True
    changed, _ = sync.sync_now(phone)
    assert card_by_title(phone, "Temp") is None and changed
    assert not pc.tombstones()


def test_deleted_category_removes_its_cards_elsewhere(tmp_path, server):
    pc, phone = device(tmp_path, "pc", server), device(tmp_path, "phone", server)
    b = pc.add_board("Holiday")
    pc.add_card(b.id, "note", title="Flights")
    sync.sync_now(pc)
    sync.sync_now(phone)
    assert card_by_title(phone, "Flights") is not None
    pc.delete_board(b.id)
    sync.sync_now(pc)
    _, boards_changed = sync.sync_now(phone)
    assert boards_changed and "Holiday" not in [x.name for x in phone.boards()]
    assert card_by_title(phone, "Flights") is None


def test_reorder_and_rename_sync(tmp_path, server):
    pc, phone = device(tmp_path, "pc", server), device(tmp_path, "phone", server)
    sync.sync_now(pc)
    sync.sync_now(phone)
    ids = [b.id for b in pc.boards()]
    pc.set_board_order(list(reversed(ids)))
    pc.rename_board(ids[0], "Job")
    sync.sync_now(pc)
    sync.sync_now(phone)
    assert [b.name for b in phone.boards()] == ["Personal", "Research", "Job"]


def test_expired_session_refreshes_and_rotates(tmp_path, server):
    pc = device(tmp_path, "pc", server)
    access: dict = {}
    sync.sync_now(pc, access=access)
    first_refresh = pc.get_setting("sync_refresh")
    server.expire_tokens()
    access["expires_at"] = time.time() + 3000            # looks valid locally, rejected by server
    pc.add_card(pc.boards()[0].id, "note", title="After expiry")
    sync.sync_now(pc, access=access)
    assert any(r["title"] == "After expiry" for r in server.tables["cards"].values())
    assert pc.get_setting("sync_refresh") != first_refresh   # rotated token saved


def test_other_account_cannot_see_rows(tmp_path, server):
    pc = device(tmp_path, "pc", server)
    sync.sync_now(pc)
    stranger = Database(tmp_path / "x.db")
    sync.sign_in(stranger, server.url, server.key, "other@example.com", "pw")
    for c in stranger.cards():          # its own local seed card only
        stranger.delete_card(c.id)
    sync.sync_now(stranger)
    assert [c.title for c in stranger.cards()] == []


def test_upgrade_from_v1_database(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE boards (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL UNIQUE,
            board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE, type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT 'yellow', content TEXT NOT NULL DEFAULT '{}',
            position INTEGER NOT NULL DEFAULT 0, collapsed INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 0,
            due_date TEXT, remind_at REAL, archived INTEGER NOT NULL DEFAULT 0, archived_at REAL,
            link_app TEXT NOT NULL DEFAULT '', link_title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO boards(name, position) VALUES ('Mine', 0);
        INSERT INTO cards(uid, board_id, type, title, created_at, updated_at) VALUES ('u1', 1, 'note', 'Kept', 1, 1);
        PRAGMA user_version = 1;
    """)
    con.commit()
    con.close()
    db = Database(path)
    assert [b.name for b in db.boards()] == ["Mine"] and db.boards()[0].uid
    assert db.cards()[0].title == "Kept"
    assert [c["title"] for c in db.dirty_cards()] == ["Kept"]


def test_window_shows_phone_edits_but_not_mid_typing(tmp_path, server):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from sidebar_app.window import SidebarWindow

    app = QApplication.instance() or QApplication([])
    pc_db = device(tmp_path, "pc", server)
    win = SidebarWindow(pc_db, tmp_path)
    win.show()
    win.add_card("note", focus=False, title="Groceries", content={"text": "eggs"})
    typing = win.add_card("note", focus=False, title="Typing here")

    def cycle():
        win.syncer.run()
        for _ in range(200):
            app.processEvents()
            if win.syncer.future is None:
                break
            time.sleep(0.02)
        app.processEvents()

    cycle()                                              # PC uploads
    phone = device(tmp_path, "phone", server)
    sync.sync_now(phone)
    p_note, p_typing = card_by_title(phone, "Groceries"), card_by_title(phone, "Typing here")
    time.sleep(0.01)
    phone.update_card(p_note.id, content={"text": "eggs, bread"})
    phone.update_card(p_typing.id, title="Changed on phone")
    phone.add_card(phone.boards()[0].id, "note", title="New from phone")
    sync.sync_now(phone)

    typing.title.setText("still typing")
    typing._queue({"title": "still typing"})              # pending local save = being edited
    cycle()                                              # PC downloads
    shown = {w.card.title: w for w in win.list.widgets.values()}
    assert "New from phone" in shown
    assert shown["Groceries"].body.edit.toPlainText() == "eggs, bread"
    assert typing.title.text() == "still typing"          # not yanked away mid-edit
    typing.flush()                                       # user's newer edit is saved and wins
    cycle()
    sync.sync_now(phone)
    assert phone.card(p_typing.id).title == "still typing"
    win.syncer.shutdown()
    win.close()
