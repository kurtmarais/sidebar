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
    """The window keeps one size; only its mask changes, so Windows never
    has to show a resized window (the cause of the strip jumping)."""
    full = win.geometry()
    pw = int(win.s["panel_width"])
    assert full.width() == 6 + pw + 8
    strip_mask = win.mask().boundingRect()
    assert strip_mask.width() == 6 and not win.panel.isVisible()
    win.expand()
    pump(app)
    assert win.progress == 1.0 and win.geometry() == full
    assert win.panel.isVisible() and win.mask().boundingRect().width() > pw
    win.collapse(force=True)
    pump(app)
    assert win.progress == 0.0 and win.geometry() == full
    assert win.mask().boundingRect() == strip_mask and not win.panel.isVisible()


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


def test_dialogs_open_beside_panel_and_on_top(app, win):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog
    from sidebar_app import dialogs
    win.expand()
    pump(app)
    dlg = QDialog(win)
    dlg.resize(300, 200)
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    win.place_beside(dlg)
    full = win._full_rect()
    assert dlg.x() + 300 <= full.left()            # left of the right-edge panel, not over it
    assert dialogs._placer == win.place_beside


def test_settings_save_and_persist(app, win, monkeypatch, tmp_path):
    """Drive Settings → Save through open_settings and check every value
    reaches the database and survives reopening it."""
    from PySide6.QtWidgets import QDialog
    from sidebar_app import dialogs
    from sidebar_app.storage import Database

    def fill_and_save(dlg):
        if not isinstance(dlg, dialogs.SettingsDialog):
            return QDialog.DialogCode.Rejected
        dlg.title.setText("Kurt's board")
        dlg.edge.setCurrentIndex(dlg.edge.findData("left"))
        dlg.width.setValue(400)
        dlg.theme.setCurrentIndex(dlg.theme.findData("light"))
        dlg.open_mode.setCurrentIndex(dlg.open_mode.findData("click"))
        dlg.delay.setValue(800)
        dlg.strip_length.setValue(55)
        dlg.focus.setValue(40)
        dlg.quickbar.setChecked(False)
        dlg.sound_url.setText("https://youtu.be/co7KgV2edvI")
        dlg.sound_name.setText("Cabin")
        dlg.app_path.setText(r"C:\Tools\caffeine64.exe")
        dlg.on_top.setChecked(False)
        dlg.hotkey.setChecked(False)
        dlg.follow.setChecked(False)
        dlg.meetings.setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs, "run", fill_and_save)
    win.open_settings()

    expected = {
        "title": "Kurt's board", "edge": "left", "panel_width": 400, "theme": "light",
        "open_mode": "click", "open_delay_ms": 800, "strip_length": 0.55, "focus_minutes": 40,
        "show_quickbar": False, "focus_sound_url": "https://youtu.be/co7KgV2edvI",
        "focus_sound_name": "Cabin", "launch_app_path": r"C:\Tools\caffeine64.exe",
        "always_on_top": False, "hotkey_enabled": False, "follow_window": False,
        "suggest_meeting_notes": False,
    }
    assert win.title_label.text() == "Kurt's board"        # applied immediately
    assert win.p.name == "light" and win.quickbar.isHidden()
    reopened = Database(win.db.path).settings()             # persisted
    for key, value in expected.items():
        assert reopened[key] == value, key


def test_settings_cancel_changes_nothing(app, win, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from sidebar_app import dialogs

    def change_then_cancel(dlg):
        dlg.title.setText("Should not stick")
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(dialogs, "run", change_then_cancel)
    before = win.db.settings()["title"]
    win.open_settings()
    assert win.db.settings()["title"] == before and win.title_label.text() == before


def test_deferred_card_loading_at_startup(app, tmp_path):
    """Start-up shows the window before building cards, then loads them all in
    order and only then checks reminders."""
    db = Database(tmp_path / "d.db")
    b = db.boards()[0].id
    for i in range(30):
        db.add_card(b, "note", title=f"n{i}")
    due = db.add_card(b, "note", title="due now")
    db.update_card(due.id, remind_at=time.time() - 5)
    w = SidebarWindow(db, tmp_path, defer_cards=True)
    w.show()
    assert len(w.list.widgets) < 32                 # not all built yet: start-up didn't wait
    for _ in range(200):
        app.processEvents()
        if not w._loading:
            break
    assert not w._loading and len(w.list.widgets) == 32
    assert [c.title for c in db.cards()] == [x.card.title for x in w.list.displayed]
    assert w._alerts == [due.id]                    # reminder fired once cards were loaded
    w.shutdown()
    w.close()


def test_focus_sound_engine_loads_on_first_play(app, monkeypatch):
    import sys
    from sidebar_app import quickbar
    store = {}
    bar = quickbar.QuickBar(DARK_PALETTE(), store.get, store.__setitem__)
    assert "PySide6.QtWebEngineWidgets" not in sys.modules or quickbar._webengine is not None
    bar.sound.play()
    assert bar.sound.playing and bar.sound.view is not None
    bar.sound.stop()
    bar.shutdown()


def DARK_PALETTE():
    from sidebar_app.theme import DARK
    return DARK
