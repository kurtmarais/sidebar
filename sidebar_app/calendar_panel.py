"""The sidebar's own month calendar.

A bird's-eye view of the month at the bottom of the panel. Days can carry
marks and notes (stored locally in SQLite, never synced to the phone or to
Outlook, Teams or Google). Cards with a due date show on their day, and a
card dragged onto a day gets that day as its due date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from PySide6.QtCore import QLocale, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QMenu, QPlainTextEdit, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from . import dialogs
from .cards import CARD_MIME
from .storage import CalEntry
from .theme import CARD_COLORS, Palette, blend, card_accent, make_icon

ROW_H = 24
HEAD_H = 18
MAX_DOTS = 3


@dataclass
class DueCard:
    """A card with a due date, as the calendar shows it."""
    id: int
    title: str
    color: str
    day: date
    due_time: str | None = None


def first_weekday() -> int:
    """Python weekday (0 = Monday) the week starts on, from the Windows locale."""
    return (int(QLocale.system().firstDayOfWeek().value) - 1) % 7


def month_grid(year: int, month: int, week_start: int = 0) -> list[date]:
    """The 42 days (6 weeks) shown for a month."""
    first = date(year, month, 1)
    start = first - timedelta(days=(first.weekday() - week_start) % 7)
    return [start + timedelta(days=i) for i in range(42)]


def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    m = year * 12 + (month - 1) + delta
    return m // 12, m % 12 + 1


def short_day(d: date, today: date) -> str:
    text = f"{d:%a} {d.day} {d:%b}"
    return text if d.year == today.year else f"{text} {d.year}"


def upcoming_text(d: date, today: date) -> str:
    n = (d - today).days
    if n <= 0:
        return "Today"
    if n == 1:
        return "Tomorrow"
    return f"in {n} days · {short_day(d, today)}"


def _dot(color: str, size: int = 8) -> QLabel:
    lab = QLabel()
    lab.setFixedSize(size, size)
    lab.setStyleSheet(f"background: {color}; border-radius: {size // 2}px;")
    return lab


class MonthGrid(QWidget):
    """Painted month grid. Click selects a day, double-click adds a note to it,
    the mouse wheel changes month, and cards can be dropped onto a day."""

    day_clicked = Signal(object)          # date
    day_activated = Signal(object)        # date (double-click)
    month_step = Signal(int)
    card_dropped = Signal(int, object)    # card id, date

    def __init__(self, p: Palette):
        super().__init__()
        self.p = p
        self.year, self.month = date.today().year, date.today().month
        self.selected = date.today()
        self.week_start = first_weekday()
        self.dots: dict[date, list[str]] = {}       # day -> colours (notes and due cards)
        self.marks: dict[date, str] = {}            # day -> colour of a plain mark
        self._drop_day: date | None = None
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumHeight(HEAD_H + 6 * ROW_H)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(280, HEAD_H + 6 * ROW_H)

    def days(self) -> list[date]:
        return month_grid(self.year, self.month, self.week_start)

    def _cell(self, i: int) -> QRectF:
        w = self.width() / 7
        return QRectF((i % 7) * w, HEAD_H + (i // 7) * ROW_H, w, ROW_H)

    def day_at(self, pos: QPoint) -> date | None:
        if pos.y() < HEAD_H or self.width() <= 0:
            return None
        col = min(6, int(pos.x() / (self.width() / 7)))
        row = int((pos.y() - HEAD_H) / ROW_H)
        if not 0 <= row < 6:
            return None
        return self.days()[row * 7 + col]

    # ------------------------------------------------------------ painting
    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.p
        today = date.today()
        w = self.width() / 7
        small = QFont(self.font())
        small.setPointSizeF(7.5)
        small.setBold(True)
        p.setFont(small)
        p.setPen(QColor(pal.muted))
        for i in range(7):
            name = date(2024, 1, 1 + (self.week_start + i) % 7).strftime("%a")[:2]   # 1 Jan 2024 = Monday
            p.drawText(QRectF(i * w, 0, w, HEAD_H), Qt.AlignCenter, name)

        normal = QFont(self.font())
        normal.setPointSizeF(9)
        bold = QFont(normal)
        bold.setBold(True)
        for i, d in enumerate(self.days()):
            r = self._cell(i)
            in_month = d.month == self.month
            cx, cy = r.center().x(), r.top() + 10
            circle = QRectF(cx - 10, cy - 9, 20, 18)
            mark = self.marks.get(d)
            if mark and d != today:
                tint = QColor(blend(pal.surface, mark, 0.55 if pal.name == "dark" else 0.45))
                if not in_month:
                    tint.setAlphaF(0.5)
                p.setPen(Qt.NoPen)
                p.setBrush(tint)
                p.drawRoundedRect(circle, 9, 9)
            if d == self._drop_day:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(blend(pal.panel, pal.strip, 0.45)))
                p.drawRoundedRect(r.adjusted(2, 1, -2, -1), 6, 6)
            if d == today:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(pal.accent))
                p.drawRoundedRect(circle, 9, 9)
            elif d == self.selected and not mark:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(pal.hover))
                p.drawRoundedRect(circle, 9, 9)
            if d == self.selected and (d == today or mark):
                p.setPen(QPen(QColor(pal.strip), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(circle.adjusted(-1.5, -1.5, 1.5, 1.5), 10, 10)
            if d == today:
                color = QColor(pal.accent_text)
            elif not in_month:
                color = QColor(pal.muted)
                color.setAlphaF(0.55)
            else:
                color = QColor(pal.text)
            p.setFont(bold if d in (today, self.selected) else normal)
            p.setPen(color)
            p.drawText(circle, Qt.AlignCenter, str(d.day))
            dots = self.dots.get(d, [])[:MAX_DOTS]
            if dots:
                span = len(dots) * 5 - 1
                x = cx - span / 2
                p.setPen(Qt.NoPen)
                for c in dots:
                    dc = QColor(c)
                    if not in_month:
                        dc.setAlphaF(0.5)
                    p.setBrush(dc)
                    p.drawEllipse(QRectF(x, r.bottom() - 4.5, 4, 4))
                    x += 5
        p.end()

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            d = self.day_at(e.position().toPoint())
            if d is not None:
                self.day_clicked.emit(d)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            d = self.day_at(e.position().toPoint())
            if d is not None:
                self.day_activated.emit(d)

    def wheelEvent(self, e):  # noqa: N802
        dy = e.angleDelta().y()
        if dy:
            self.month_step.emit(-1 if dy > 0 else 1)
        e.accept()

    # ---------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasFormat(CARD_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        d = self.day_at(e.position().toPoint())
        if not e.mimeData().hasFormat(CARD_MIME) or d is None:
            e.ignore()
            return
        e.acceptProposedAction()
        if d != self._drop_day:
            self._drop_day = d
            self.update()

    def dragLeaveEvent(self, _e):  # noqa: N802
        self._drop_day = None
        self.update()

    def dropEvent(self, e):  # noqa: N802
        d = self.day_at(e.position().toPoint())
        self._drop_day = None
        self.update()
        if d is None or not e.mimeData().hasFormat(CARD_MIME):
            return
        e.acceptProposedAction()
        self.card_dropped.emit(int(bytes(e.mimeData().data(CARD_MIME)).decode()), d)


class EntryDialog(QDialog):
    """Add or edit a calendar note. ``deleted`` is set when Delete is pressed."""

    def __init__(self, parent: QWidget | None, day: date, entry: CalEntry | None, default_lead: int):
        super().__init__(parent)
        self.deleted = False
        self.setWindowTitle("Edit calendar note" if entry else "Add to calendar")
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Day"))
        self.day = QDateEdit()
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("ddd d MMM yyyy")
        self.day.setDate(entry.date if entry else day)
        row.addWidget(self.day, 1)
        row.addWidget(QLabel("Colour"))
        self.color = QComboBox()
        for key, (name, hex_) in CARD_COLORS.items():
            self.color.addItem(make_icon("dot", hex_), name, key)
        self.color.setCurrentIndex(max(0, self.color.findData(entry.color if entry else "blue")))
        row.addWidget(self.color)
        lay.addLayout(row)

        self.text = QPlainTextEdit(entry.text if entry else "")
        self.text.setPlaceholderText("Note for this day (leave empty to just mark the day)")
        self.text.setFixedHeight(84)
        self.text.setTabChangesFocus(True)
        lay.addWidget(self.text)

        lead = QHBoxLayout()
        self.notify = QCheckBox("Show at the top of my notes")
        self.lead = QSpinBox()
        self.lead.setRange(0, 365)
        self.lead.setSuffix(" days before")
        self.lead.setSpecialValueText("on the day")
        on = entry.lead_days is not None if entry else True
        self.lead.setValue(entry.lead_days if entry and entry.lead_days is not None else default_lead)
        self.notify.setChecked(on)
        self.lead.setEnabled(on)
        self.notify.toggled.connect(self.lead.setEnabled)
        lead.addWidget(self.notify)
        lead.addWidget(self.lead, 1)
        lay.addLayout(lead)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setProperty("primary", True)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        if entry is not None:
            delete = box.addButton("Delete", QDialogButtonBox.DestructiveRole)
            delete.clicked.connect(self._delete)
        lay.addWidget(box)
        self.text.setFocus()

    def _delete(self) -> None:
        self.deleted = True
        self.accept()

    def values(self) -> dict:
        return {
            "day": self.day.date().toPython(),
            "text": self.text.toPlainText().strip(),
            "color": self.color.currentData(),
            "lead_days": self.lead.value() if self.notify.isChecked() else None,
        }


class CalendarPanel(QFrame):
    """Month header, grid and the selected day's notes and due cards."""

    changed = Signal()                     # calendar entries added/edited/deleted
    open_card = Signal(int)
    card_dropped = Signal(int, object)     # card id, date
    open_changed = Signal(bool)

    def __init__(self, p: Palette, db, due_cards: Callable[[date, date], list[DueCard]],
                 get_setting: Callable[[str], object]):
        super().__init__()
        self.setObjectName("calendar")
        self.p = p
        self.db = db
        self.due_cards = due_cards
        self.get_setting = get_setting
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(2)
        self.fold_btn = QToolButton()
        self.fold_btn.setToolTip("Show or hide the calendar")
        self.fold_btn.setIconSize(QSize(14, 14))
        self.fold_btn.clicked.connect(lambda: self.set_open(not self.is_open))
        self.title = QPushButton()
        self.title.setObjectName("calendarTitle")
        self.title.setProperty("flat", True)
        self.title.setCursor(Qt.PointingHandCursor)
        self.title.setToolTip("Go to today")
        self.title.clicked.connect(self.go_today)
        self.prev_btn = QToolButton()
        self.prev_btn.setText("‹")
        self.prev_btn.setToolTip("Previous month")
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = QToolButton()
        self.next_btn.setText("›")
        self.next_btn.setToolTip("Next month")
        self.next_btn.clicked.connect(lambda: self.step(1))
        for b in (self.prev_btn, self.next_btn):
            b.setObjectName("calendarStep")
            b.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.fold_btn)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.prev_btn)
        head.addWidget(self.next_btn)
        lay.addLayout(head)

        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        self.grid = MonthGrid(p)
        self.grid.day_clicked.connect(self.select)
        self.grid.day_activated.connect(self.add_entry)
        self.grid.month_step.connect(self.step)
        self.grid.card_dropped.connect(self.card_dropped)
        bl.addWidget(self.grid)

        day_head = QHBoxLayout()
        self.day_label = QLabel()
        self.day_label.setStyleSheet("font-weight: 700;")
        self.add_btn = QPushButton("+ Add")
        self.add_btn.setProperty("flat", True)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setToolTip("Add a note or mark to this day (or double-click a day)")
        self.add_btn.clicked.connect(lambda: self.add_entry(self.grid.selected))
        day_head.addWidget(self.day_label, 1)
        day_head.addWidget(self.add_btn)
        bl.addLayout(day_head)
        self.items_box = QVBoxLayout()
        self.items_box.setSpacing(2)
        bl.addLayout(self.items_box)
        lay.addWidget(self.body)

        self.is_open = bool(get_setting("calendar_open"))
        self.body.setVisible(self.is_open)
        self.apply_theme(p)

    # ----------------------------------------------------------- navigation
    def set_open(self, on: bool) -> None:
        self.is_open = on
        self.body.setVisible(on)
        self._update_head()
        self.open_changed.emit(on)

    def step(self, delta: int) -> None:
        self.grid.year, self.grid.month = add_months(self.grid.year, self.grid.month, delta)
        self.refresh()

    def go_today(self) -> None:
        self.select(date.today())

    def select(self, d: date) -> None:
        self.grid.selected = d
        self.grid.year, self.grid.month = d.year, d.month
        if not self.is_open:
            self.set_open(True)
        self.refresh()

    # ------------------------------------------------------------- entries
    def add_entry(self, d: date) -> None:
        self.grid.selected = d
        dlg = EntryDialog(self.window(), d, None, int(self.get_setting("calendar_lead_days") or 0))
        if dialogs.run(dlg) != QDialog.DialogCode.Accepted:
            self.refresh()
            return
        v = dlg.values()
        self.db.add_calendar_entry(v["day"], v["text"], v["color"], v["lead_days"])
        self.grid.selected = v["day"]
        self.changed.emit()
        self.select(v["day"])

    def edit_entry(self, entry_id: int) -> None:
        entry = self.db.calendar_entry(entry_id)
        if entry is None:
            return
        dlg = EntryDialog(self.window(), entry.date, entry, int(self.get_setting("calendar_lead_days") or 0))
        if dialogs.run(dlg) != QDialog.DialogCode.Accepted:
            return
        if dlg.deleted:
            self.db.delete_calendar_entry(entry_id)
            self.changed.emit()
            self.refresh()
            return
        v = dlg.values()
        self.db.update_calendar_entry(entry_id, **v)
        self.changed.emit()
        self.select(v["day"])

    def delete_entry(self, entry_id: int) -> None:
        self.db.delete_calendar_entry(entry_id)
        self.changed.emit()
        self.refresh()

    def _entry_menu(self, entry_id: int, widget: QWidget, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Edit…", lambda: self.edit_entry(entry_id))
        menu.addAction("Delete", lambda: self.delete_entry(entry_id))
        menu.exec(widget.mapToGlobal(pos))

    # ------------------------------------------------------------- refresh
    def _update_head(self) -> None:
        g = self.grid
        self.title.setText(date(g.year, g.month, 1).strftime("%B %Y"))
        self.fold_btn.setIcon(make_icon("chevron_down" if self.is_open else "chevron_right", self.p.muted))
        self.prev_btn.setVisible(self.is_open)
        self.next_btn.setVisible(self.is_open)

    def refresh(self) -> None:
        self._update_head()
        days = self.grid.days()
        start, end = days[0], days[-1]
        entries = self.db.calendar_entries(start, end)
        cards = self.due_cards(start, end)
        dots: dict[date, list[str]] = {}
        marks: dict[date, str] = {}
        for e in entries:
            if e.text.strip():
                dots.setdefault(e.date, []).append(card_accent(e.color))
            else:
                marks.setdefault(e.date, card_accent(e.color))
        for c in cards:
            dots.setdefault(c.day, []).append(card_accent(c.color))
        self.grid.dots = dots
        self.grid.marks = marks
        self.grid.update()
        self._fill_day([e for e in entries if e.date == self.grid.selected],
                       sorted((c for c in cards if c.day == self.grid.selected),
                              key=lambda c: c.due_time or "99"))

    def _fill_day(self, entries: list[CalEntry], cards: list[DueCard]) -> None:
        while self.items_box.count():
            item = self.items_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        today = date.today()
        d = self.grid.selected
        label = "Today" if d == today else "Tomorrow" if d == today + timedelta(days=1) else ""
        self.day_label.setText(f"{label} · {short_day(d, today)}" if label else short_day(d, today))
        for e in entries:
            self.items_box.addWidget(self._row(
                card_accent(e.color), e.text.strip() or "Marked",
                "" if e.lead_days is None else
                ("heads-up on the day" if e.lead_days == 0 else f"heads-up {e.lead_days}d before"),
                lambda _=None, i=e.id: self.edit_entry(i),
                lambda pos, w, i=e.id: self._entry_menu(i, w, pos),
                muted_text=not e.text.strip()))
        for c in cards:
            self.items_box.addWidget(self._row(
                card_accent(c.color), c.title or "Untitled note",
                f"due {c.due_time}" if c.due_time else "due",
                lambda _=None, i=c.id: self.open_card.emit(i), None, icon="cards"))
        if not entries and not cards:
            empty = QLabel("Nothing on this day. Double-click a day to add a note, "
                           "or drag a card here to set its due date.")
            empty.setProperty("muted", True)
            empty.setWordWrap(True)
            self.items_box.addWidget(empty)

    def _row(self, color: str, text: str, note: str, on_click, on_menu, icon: str = "",
             muted_text: bool = False) -> QWidget:
        row = QFrame()
        row.setObjectName("calendarItem")
        row.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        if icon:
            ic = QLabel()
            ic.setPixmap(make_icon(icon, color, 12).pixmap(12, 12))
            h.addWidget(ic, 0, Qt.AlignTop)
        else:
            h.addWidget(_dot(color), 0, Qt.AlignVCenter)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.PlainText)
        if muted_text:
            lab.setProperty("muted", True)
        h.addWidget(lab, 1)
        if note:
            n = QLabel(note)
            n.setProperty("muted", True)
            h.addWidget(n, 0, Qt.AlignTop)
        row.mouseReleaseEvent = lambda e: on_click() if e.button() == Qt.LeftButton else None  # type: ignore
        if on_menu is not None:
            row.setContextMenuPolicy(Qt.CustomContextMenu)
            row.customContextMenuRequested.connect(lambda pos, r=row: on_menu(pos, r))
            row.setToolTip("Click to edit · right-click for more")
        else:
            row.setToolTip("Show this note")
        return row

    def apply_theme(self, p: Palette) -> None:
        self.p = p
        self.grid.p = p
        self._update_head()
        self.grid.update()


class UpcomingBox(QFrame):
    """'Coming up': calendar notes whose heads-up period has started."""

    day_clicked = Signal(object)
    MAX_ROWS = 5

    def __init__(self):
        super().__init__()
        self.setObjectName("upcoming")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(2)
        self.head = QLabel("COMING UP")
        self.head.setProperty("section", True)
        lay.addWidget(self.head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        lay.addLayout(self.rows)

    def set_entries(self, entries: list[CalEntry], today: date) -> int:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for e in entries[: self.MAX_ROWS]:
            row = QFrame()
            row.setObjectName("calendarItem")
            row.setCursor(Qt.PointingHandCursor)
            row.setToolTip("Show this day in the calendar")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            h.addWidget(_dot(card_accent(e.color)), 0, Qt.AlignVCenter)
            lab = QLabel(e.headline or "Marked day")
            lab.setTextFormat(Qt.PlainText)
            h.addWidget(lab, 1)
            when = QLabel(upcoming_text(e.date, today))
            when.setProperty("muted", True)
            if e.date == today:
                when.setStyleSheet("font-weight: 700;")
            h.addWidget(when)
            row.mouseReleaseEvent = (lambda ev, d=e.date: self.day_clicked.emit(d)  # type: ignore
                                     if ev.button() == Qt.LeftButton else None)
            self.rows.addWidget(row)
        if len(entries) > self.MAX_ROWS:
            more = QLabel(f"+{len(entries) - self.MAX_ROWS} more in the calendar")
            more.setProperty("muted", True)
            more.setContentsMargins(8, 0, 0, 0)
            self.rows.addWidget(more)
        return len(entries)
