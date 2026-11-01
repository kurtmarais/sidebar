import json
import time
from datetime import date

import pytest

from sidebar_app import backup, secrets_store
from sidebar_app.storage import Database
from sidebar_app.winplatform import detect_meeting


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "s.db")
    yield d
    d.close()


def test_seed_and_persistence(tmp_path):
    d = Database(tmp_path / "s.db")
    assert [b.name for b in d.boards()] == ["Work", "Research", "Personal"]
    assert len(d.cards()) == 1
    c = d.add_card(d.boards()[1].id, "note", title="Hello", content={"text": "World"})
    d.update_card(c.id, priority=3, due_date="2026-11-03", link_app="excel.exe")
    d.close()
    d2 = Database(tmp_path / "s.db")
    again = d2.card(c.id)
    assert again.title == "Hello" and again.content == {"text": "World"}
    assert again.priority == 3 and again.due() == date(2026, 11, 3)
    assert again.link_app == "excel.exe"
    d2.close()


def test_new_cards_go_on_top_and_order(db):
    a = db.add_card(db.boards()[0].id, "note", title="a")
    b = db.add_card(db.boards()[0].id, "note", title="b")
    assert [c.title for c in db.cards()][:2] == ["b", "a"]
    seed = db.cards()[-1]
    db.set_card_order([a.id, seed.id, b.id])
    assert [c.title for c in db.cards()] == ["a", "Getting started", "b"]


def test_archive_and_reminders(db):
    c = db.add_card(db.boards()[0].id, "note", title="call")
    db.update_card(c.id, remind_at=time.time() - 5)
    assert [r.id for r in db.due_reminders()] == [c.id]
    db.archive_card(c.id)
    assert db.card(c.id).archived and db.card(c.id).archived_at
    assert db.due_reminders() == []
    db.archive_card(c.id, False)
    assert not db.card(c.id).archived


def test_delete_board_cascades_and_keeps_last(db):
    b = db.add_board("Temp")
    c = db.add_card(b.id, "note")
    db.delete_board(b.id)
    assert db.card(c.id) is None
    for other in db.boards()[1:]:
        db.delete_board(other.id)
    with pytest.raises(ValueError):
        db.delete_board(db.boards()[0].id)


def test_search_text_and_window_match(db):
    c = db.add_card(db.boards()[0].id, "checklist", title="Trip",
                    content={"items": [{"text": "Book Hotel", "done": False}]})
    assert "book hotel" in c.search_text()
    s = db.add_card(db.boards()[0].id, "secret", title="Bank", content={"cipher": "xxx"})
    assert s.search_text() == "bank"
    c.link_app, c.link_title = "excel.exe", "budget"
    assert c.matches_window("EXCEL.EXE", "Q4 Budget.xlsx - Excel")
    assert not c.matches_window("excel.exe", "Other.xlsx")
    assert not c.matches_window("winword.exe", "budget.docx")
    c.link_app = ""
    assert c.matches_window("chrome.exe", "Budget sheet - Google Chrome")


def test_restore_merges_without_overwriting(tmp_path, db):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "x.png").write_bytes(b"png")
    db.add_card(db.boards()[0].id, "image", title="pic", content={"asset": "x.png"})
    db.add_card(db.boards()[0].id, "secret", title="hidden", content={"cipher": "c"})
    out = tmp_path / "b.json"
    backup.export_json(db, out, assets)
    data = json.loads(out.read_text())
    assert "x.png" in data["assets"]
    assert all(c["type"] != "secret" for c in data["cards"])

    other = Database(tmp_path / "other.db")
    other.add_board("Extra")
    assets2 = tmp_path / "assets2"
    added = backup.import_json(other, out, assets2, tmp_path / "bk")
    assert added == 2  # seed checklist + image
    assert (assets2 / "x.png").read_bytes() == b"png"
    assert backup.import_json(other, out, assets2, tmp_path / "bk") == 0  # idempotent
    assert {b.name for b in other.boards()} >= {"Work", "Research", "Personal", "Extra"}
    other.close()


def test_markdown_export(tmp_path, db):
    db.add_card(db.boards()[0].id, "code", title="Snip", content={"text": "x = 1", "language": "python"})
    out = tmp_path / "n.md"
    backup.export_markdown(db, out)
    text = out.read_text()
    assert "```python" in text and "- [x] Rest the cursor" in text


def test_auto_backup_rolls(tmp_path, db):
    first = backup.auto_backup(db, tmp_path / "bk")
    assert first and first.exists()
    assert backup.auto_backup(db, tmp_path / "bk") is None


# ---------------------------------------------------------------- secrets
def fake_protect(data, key):
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def test_secret_roundtrip_and_wrong_pin():
    c = secrets_store.create("1234", "pw: hunter2", fake_protect)
    assert "hunter2" not in json.dumps(c)
    assert secrets_store.check_pin(c, "1234") and not secrets_store.check_pin(c, "0000")
    text, key = secrets_store.reveal(c, "1234", fake_protect)
    assert text == "pw: hunter2"
    c2 = secrets_store.seal_with_key(c, key, "new", fake_protect)
    assert secrets_store.reveal(c2, "1234", fake_protect)[0] == "new"
    with pytest.raises(ValueError):
        secrets_store.reveal(c, "9999", fake_protect)
    with pytest.raises(ValueError):
        secrets_store.create("12a4", "x", fake_protect)


def test_pin_guard_lockout():
    now = [100.0]
    g = secrets_store.PinGuard(clock=lambda: now[0])
    for _ in range(5):
        assert g.wait_seconds() == 0
        g.record(False)
    assert g.wait_seconds() == 30
    now[0] += 31
    assert g.wait_seconds() == 0


# --------------------------------------------------------------- meetings
@pytest.mark.parametrize("exe,title,expected", [
    ("chrome.exe", "Meet - abc-defg-hij - Google Chrome", "Google Meet"),
    ("msedge.exe", "Meet - Weekly sync and 2 more pages - Personal - Microsoft​ Edge", "Weekly sync"),
    ("zoom.exe", "Zoom Meeting", "Zoom meeting"),
    ("ms-teams.exe", "Weekly sync | Meeting | Microsoft Teams", "Weekly sync"),
    ("ms-teams.exe", "Chat | Dana | Microsoft Teams", None),
    ("slack.exe", "Huddle with Dana - Slack", "Slack huddle"),
    ("excel.exe", "Budget.xlsx - Excel", None),
])
def test_detect_meeting(exe, title, expected):
    assert detect_meeting(exe, title) == expected
