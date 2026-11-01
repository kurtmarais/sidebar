"""Offscreen GUI tests. Run with QT_QPA_PLATFORM=offscreen."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sidebar_app import winplatform  # noqa: E402
from sidebar_app.cardlist import VIEW_ARCHIVE, VIEW_TODAY  # noqa: E402
from sidebar_app.storage import Database  # noqa: E402
from sidebar_app.window import SidebarWindow  # noqa: E402


def fake_protect(data, key):
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path):
    db = Database(tmp_path / "s.db")
    w = SidebarWindow(db, tmp_path)
    w.env.protect = w.env.unprotect = fake_protect
    w.env.secrets_available = True
    w.show()
    w.anim.setDuration(0)
    yield w
    w.shutdown()
    w.close()
    db.close()


def pump(app, n=5):
    for _ in range(n):
        app.processEvents()


def test_expand_collapse_geometry(app, win):
    strip = win.geometry()
    assert strip.width() == 6
    win.expand()
    pump(app)
    assert win.progress == 1.0
    assert win.geometry().width() == 6 + int(win.s["panel_width"]) + 8
    assert win.panel.isVisible()
    win.collapse(force=True)
    pump(app)
    assert win.progress == 0.0 and win.geometry() == strip


def test_pin_keeps_open(app, win):
    win.expand()
    pump(app)
    win.pin_btn.setChecked(True)
    win.collapse()
    pump(app)
    assert win.progress == 1.0
    win.pin_btn.setChecked(False)


def test_note_edit_persists(app, win):
    w = win.add_card("note", focus=False, title="Plan")
    w.body.edit.setPlainText("Line one\nLine two")
    w.flush()
    assert win.db.card(w.card.id).content == {"text": "Line one\nLine two"}
    assert win.list.displayed[0] is w


def test_checklist_add_items(app, win):
    w = win.add_card("checklist", focus=False)
    w.body.add_field.setText("Buy milk")
    w.body._commit_add_field()
    w.body.rows[0].check.setChecked(True)
    w.flush()
    assert win.db.card(w.card.id).content == {"items": [{"text": "Buy milk", "done": True}]}
    w.set_collapsed(True)
    assert "1 of 1 done" in w.summary.text()


def test_search_filters(app, win):
    a = win.add_card("note", focus=False, title="Alpha", content={"text": "zebra"})
    win.add_card("note", focus=False, title="Beta")
    win.search.setText("zebra")
    win.refresh()
    assert win.list.displayed == [a]


def test_archive_and_restore(app, win):
    w = win.add_card("note", focus=False, title="Done soon")
    win.archive_card(w.card.id, True)
    assert w not in win.list.displayed
    win.set_view(VIEW_ARCHIVE)
    assert w in win.list.displayed
    win.archive_card(w.card.id, False)
    win.set_view("cards")
    assert w in win.list.displayed


def test_reminder_alert_flow(app, win):
    w = win.add_card("note", focus=False, title="Call Dana")
    w.set_reminder(time.time() - 1)
    win.check_reminders()
    assert win._alerts == [w.card.id]
    assert "reminder" in win._banners
    win.collapse()                      # auto-collapse is blocked while an alert waits
    pump(app)
    assert win.progress == 1.0
    win._dismiss_alert(w.card.id, clear=True)
    assert not win._alerts and win.db.card(w.card.id).remind_at is None


def test_today_view(app, win):
    from datetime import date
    w = win.add_card("note", focus=False, title="Due")
    w.set_due(date.today())
    win.set_view(VIEW_TODAY)
    assert w in win.list.displayed
    assert win.view_btns[VIEW_TODAY].text().startswith("Today")


def test_notes_follow_window(app, win, monkeypatch):
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    w = win.add_card("note", focus=False, title="Budget notes")
    w.set_link("excel.exe", "")
    win.set_context("excel.exe", "Q4 budget.xlsx - Excel")
    groups = win.list.groups()
    assert groups[0][0].startswith("For this window") and groups[0][1] == [w]
    win.set_context("winword.exe", "Letter.docx - Word")
    assert win.list.groups()[0][0] is None


def test_link_mode_toggle(app, win, monkeypatch):
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    win.set_context("excel.exe", "Book1 - Excel")
    w = win.add_card("note", focus=False, title="x")
    win.start_link_mode()
    win._toggle_link(w.card.id)
    assert win.db.card(w.card.id).link_app == "excel.exe"
    win._toggle_link(w.card.id)
    assert win.db.card(w.card.id).link_app == ""
    win.stop_link_mode()


def test_meeting_suggestion(app, win, monkeypatch):
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    win.set_context("zoom.exe", "Zoom Meeting")
    assert "meeting" in win._banners
    win._start_meeting_notes("Zoom meeting")
    card = win.list.displayed[0].card
    assert card.title == "Zoom meeting" and card.link_title == "Zoom meeting"


def test_secret_card(app, win):
    w = win.add_card("secret", focus=False, title="Bank")
    body = w.body
    body._show_setup()
    from PySide6.QtWidgets import QLineEdit, QPushButton
    pin1, pin2 = body.stack.findChildren(QLineEdit)[:2]
    pin1.setText("4321")
    pin2.setText("4321")
    body.stack.findChildren(QPushButton)[0].click()
    body.edit.setPlainText("acct 99")
    w.flush()
    stored = win.db.card(w.card.id).content
    assert "acct 99" not in str(stored) and stored["cipher"]
    w.lock()
    assert body._key is None
    from sidebar_app import secrets_store
    assert secrets_store.reveal(win.db.card(w.card.id).content, "4321", fake_protect)[0] == "acct 99"


def test_files_and_paste(app, win, tmp_path):
    py = tmp_path / "x.py"
    py.write_text("def f():\n    return 1\n")
    w = win.add_file(py)
    assert w.card.type == "code" and w.card.content["language"] == "python"
    img = QImage(40, 30, QImage.Format_RGB32)
    img.fill(QColor("red"))
    p = tmp_path / "pic.png"
    img.save(str(p))
    w2 = win.add_file(p)
    assert w2.card.type == "image" and (win.assets_dir / w2.card.content["asset"]).exists()
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"%PDF")
    w3 = win.add_file(doc)
    assert w3.card.type == "file" and w3.card.content["path"] == str(doc)
    QApplication.clipboard().setText("pasted line\nmore")
    win.paste()
    assert win.list.displayed[0].card.title == "pasted line"


def test_type_conversion(app, win):
    w = win.add_card("note", focus=False, content={"text": "a\nb"})
    w.set_type("checklist")
    assert win.db.card(w.card.id).content == {"items": [{"text": "a", "done": False},
                                                        {"text": "b", "done": False}]}


def test_settings_theme_switch(app, win):
    win.s["theme"] = "light"
    win.apply_theme()
    assert win.p.name == "light"


def test_strip_stays_put_while_opening(app, win, monkeypatch):
    """The strip must not depend on the live window position, which Windows
    can update after the resize (that made the strip flash at the top)."""
    from PySide6.QtCore import QRect
    expected_top = win._strip_rect().top()
    stale = QRect(win.geometry())                  # position before the resize
    monkeypatch.setattr(win, "geometry", lambda: stale)
    win.progress = 0.5
    win._layout_children()
    full_top = win._full_rect().top()
    assert full_top + win.strip.y() == expected_top
