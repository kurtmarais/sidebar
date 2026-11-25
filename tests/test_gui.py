"""Offscreen GUI tests. Run with QT_QPA_PLATFORM=offscreen."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel  # noqa: E402

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
        dlg.calendar.setChecked(False)
        dlg.lead.setValue(7)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs, "run", fill_and_save)
    win.open_settings()

    expected = {
        "title": "Kurt's board", "edge": "left", "panel_width": 400, "theme": "light",
        "open_mode": "click", "open_delay_ms": 800, "strip_length": 0.55, "focus_minutes": 40,
        "show_quickbar": False, "focus_sound_url": "https://youtu.be/co7KgV2edvI",
        "focus_sound_name": "Cabin", "launch_app_path": r"C:\Tools\caffeine64.exe",
        "always_on_top": False, "hotkey_enabled": False, "follow_window": False,
        "show_calendar": False, "calendar_lead_days": 7,
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


# ------------------------------------------------------------- calendar
def test_calendar_shows_entries_and_due_cards(app, win):
    from datetime import date, timedelta
    from sidebar_app.theme import card_accent
    today = date.today()
    win.db.add_calendar_entry(today, "Dentist", "teal", 0)
    win.db.add_calendar_entry(today, "", "green", None)            # plain mark
    w = win.add_card("note", focus=False, title="Report", color="red")
    w.set_due(today, "14:00")
    win.calendar.go_today()
    assert win.calendar.grid.dots[today] == [card_accent("teal"), card_accent("red")]
    assert win.calendar.grid.marks[today] == card_accent("green")
    texts = [lab.text() for lab in win.calendar.findChildren(QLabel) if not lab.isHidden()]
    assert "Dentist" in texts and "Marked" in texts and "Report" in texts and "due 14:00" in texts
    # Archived cards leave the calendar.
    win.archive_card(w.card.id, True)
    assert win.calendar.grid.dots[today] == [card_accent("teal")]


def test_card_dropped_on_day_gets_due_date(app, win):
    from datetime import date, timedelta
    w = win.add_card("note", focus=False, title="Pay rent")
    w.set_due(date.today(), "09:30")
    target = date.today() + timedelta(days=10)
    win.calendar.card_dropped.emit(w.card.id, target)
    card = win.db.card(w.card.id)
    assert card.due_date == target.isoformat() and card.due_time == "09:30"   # time kept
    assert win.calendar.grid.selected == target


def test_calendar_add_entry_and_coming_up(app, win, monkeypatch):
    from datetime import date, timedelta
    from sidebar_app import calendar_panel
    day = date.today() + timedelta(days=2)

    def fill(dlg):
        dlg.text.setPlainText("Mum's birthday")
        dlg.color.setCurrentIndex(dlg.color.findData("pink"))
        dlg.lead.setValue(5)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(calendar_panel.dialogs, "run", fill)
    win.calendar.add_entry(day)
    [e] = win.db.calendar_entries()
    assert (e.date, e.text, e.color, e.lead_days) == (day, "Mum's birthday", "pink", 5)
    assert not win.upcoming.isHidden()
    labels = [lab.text() for lab in win.upcoming.findChildren(QLabel)]
    assert "Mum's birthday" in labels and any(t.startswith("in 2 days") for t in labels)
    # Not shown while searching or outside the Notes view.
    win.set_view(VIEW_ARCHIVE)
    assert win.upcoming.isHidden()
    win.set_view("cards")

    # Edit: switch the heads-up off.
    def no_notify(dlg):
        dlg.notify.setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(calendar_panel.dialogs, "run", no_notify)
    win.calendar.edit_entry(e.id)
    assert win.db.calendar_entry(e.id).lead_days is None
    assert win.upcoming.isHidden()

    def delete(dlg):
        dlg._delete()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(calendar_panel.dialogs, "run", delete)
    win.calendar.edit_entry(e.id)
    assert win.db.calendar_entries() == []


def test_calendar_fold_and_hide(app, win, tmp_path):
    win.calendar.set_open(False)
    assert win.calendar.body.isHidden()
    assert Database(win.db.path).settings()["calendar_open"] is False
    win.s["show_calendar"] = False
    win.apply_theme()
    assert win.calendar.isHidden()


def test_show_card_from_calendar(app, win):
    w = win.add_card("note", focus=False, title="Find me")
    other = win.db.boards()[1].id
    win.move_card(w.card.id, other)
    win.tabs.setCurrentIndex(1)                 # a different category
    win.set_view(VIEW_ARCHIVE)
    win.calendar.open_card.emit(w.card.id)
    assert win.list.view == "cards" and w in win.list.displayed


def _mouse(widget, kind, y, buttons):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    local = QPointF(2, y)
    glob = QPointF(widget.mapToGlobal(local.toPoint()))
    btn = Qt.LeftButton if kind != QEvent.MouseMove else Qt.NoButton
    ev = QMouseEvent(kind, local, glob, btn, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


def test_strip_moves_only_after_press_and_hold(app, win):
    from PySide6.QtCore import QEvent, Qt
    strip = win.strip
    clicks = []
    strip.clicked.connect(lambda: clicks.append(1))
    start = win.s["strip_position"]

    # Quick press and drag: nothing moves, and it isn't a click either.
    _mouse(strip, QEvent.MouseButtonPress, 20, Qt.LeftButton)
    _mouse(strip, QEvent.MouseMove, 200, Qt.LeftButton)
    strip.hold_timer.timeout.emit()          # the hold time passes after moving
    _mouse(strip, QEvent.MouseMove, 260, Qt.LeftButton)
    _mouse(strip, QEvent.MouseButtonRelease, 260, Qt.NoButton)
    assert win.s["strip_position"] == start and not clicks

    # Press and hold, then drag: the strip moves and the position is saved.
    _mouse(strip, QEvent.MouseButtonPress, 20, Qt.LeftButton)
    strip.hold_timer.timeout.emit()
    assert strip._dragging
    _mouse(strip, QEvent.MouseMove, 400, Qt.LeftButton)
    _mouse(strip, QEvent.MouseButtonRelease, 400, Qt.NoButton)
    assert win.s["strip_position"] != start and not clicks
    assert Database(win.db.path).settings()["strip_position"] == win.s["strip_position"]

    # A short click still opens the panel.
    _mouse(strip, QEvent.MouseButtonPress, 20, Qt.LeftButton)
    _mouse(strip, QEvent.MouseButtonRelease, 20, Qt.NoButton)
    assert clicks == [1]
