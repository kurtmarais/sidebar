"""The scrolling list of cards, with views, sections and drag-and-drop."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QTabBar, QVBoxLayout, QWidget

from .cards import CARD_MIME, CardWidget

VIEW_CARDS, VIEW_TODAY, VIEW_ARCHIVE = "cards", "today", "archive"


def _matches(w: CardWidget, terms: list[str]) -> bool:
    if not terms:
        return True
    text = w.search_text()
    return all(t in text for t in terms)


def today_section(w: CardWidget, today: date, now: datetime) -> str | None:
    """Which Today-panel section a card belongs in, if any."""
    c = w.card
    due = c.due()
    rem = datetime.fromtimestamp(c.remind_at) if c.remind_at else None
    if (due and due < today) or (rem and rem < now):
        return "Overdue"
    if (due and due == today) or (rem and rem.date() == today):
        return "Today"
    if (due and due == today + timedelta(days=1)) or (rem and rem.date() == today + timedelta(days=1)):
        return "Tomorrow"
    return None


class CardList(QWidget):
    order_changed = Signal(list)

    def __init__(self, scroll: QScrollArea):
        super().__init__()
        self.setObjectName("cardList")
        self.scroll = scroll
        self.setAcceptDrops(True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 6, 8)
        self.lay.setSpacing(8)
        self.widgets: dict[int, CardWidget] = {}
        self.order: list[int] = []          # persistent order (all cards)
        self.displayed: list[CardWidget] = []
        self._labels: list[QLabel] = []
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("muted", True)
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(10, 30, 10, 30)
        self.indicator = QFrame(self)
        self.indicator.setFixedHeight(3)
        self.indicator.hide()
        self.reorder_enabled = True

        self.view = VIEW_CARDS
        self.board_id = 0
        self.query = ""
        self.context: tuple[str, str] | None = None

    # -------------------------------------------------------------- content
    def add(self, w: CardWidget, at_top: bool = True) -> None:
        self.widgets[w.card.id] = w
        if at_top:
            self.order.insert(0, w.card.id)
        else:
            self.order.append(w.card.id)
        if w.parentWidget() is not self:
            w.setParent(self)
        w.hide()

    def replace(self, card_id: int, new: CardWidget) -> None:
        """Swap in a rebuilt widget (after a change arrived from the phone)."""
        old = self.widgets.get(card_id)
        self.widgets[card_id] = new
        new.setParent(self)
        new.hide()
        if old is not None:
            if old in self.displayed:
                self.displayed[self.displayed.index(old)] = new
            self.lay.removeWidget(old)
            old.hide()
            old.deleteLater()

    def remove(self, card_id: int) -> None:
        w = self.widgets.pop(card_id, None)
        if card_id in self.order:
            self.order.remove(card_id)
        if w is not None:
            if w in self.displayed:
                self.displayed.remove(w)
            self.lay.removeWidget(w)
            w.deleteLater()

    def set_indicator_color(self, color: str) -> None:
        self.indicator.setStyleSheet(f"background: {color}; border-radius: 1px;")

    # ----------------------------------------------------------------- views
    def groups(self) -> list[tuple[str | None, list[CardWidget]]]:
        terms = self.query.lower().split()
        ordered = [self.widgets[i] for i in self.order if i in self.widgets]

        def in_board(w: CardWidget) -> bool:
            return self.board_id == 0 or w.card.board_id == self.board_id

        if self.view == VIEW_ARCHIVE:
            items = [w for w in ordered if w.card.archived and in_board(w) and _matches(w, terms)]
            items.sort(key=lambda w: -(w.card.archived_at or 0))
            return [(None, items)]

        live = [w for w in ordered if not w.card.archived and _matches(w, terms)]
        if self.view == VIEW_TODAY:
            today, now = date.today(), datetime.now()
            sections: dict[str, list[CardWidget]] = {"Overdue": [], "Today": [], "Tomorrow": []}
            for w in live:
                sec = today_section(w, today, now)
                if sec:
                    sections[sec].append(w)
            key = lambda w: (w.card.remind_at or 9e18, -w.card.priority)  # noqa: E731
            return [(name, sorted(ws, key=key)) for name, ws in sections.items() if ws]

        live = [w for w in live if in_board(w)]
        if self.context:
            exe, title = self.context
            linked = [w for w in live if w.card.matches_window(exe, title)]
            if linked:
                rest = [w for w in live if w not in linked]
                short = title if len(title) <= 40 else title[:39] + "…"
                return [(f"For this window · {short}", linked), ("Other notes" if rest else None, rest)]
        return [(None, live)]

    def refresh(self) -> int:
        """Re-lay out the list for the current view. Returns visible card count."""
        groups = self.groups()
        while self.lay.count():
            self.lay.takeAt(0)
        for lab in self._labels:
            lab.hide()
            lab.deleteLater()
        self._labels.clear()
        shown = set()
        self.displayed = []
        for title, ws in groups:
            if title and ws:
                lab = QLabel(title.upper())
                lab.setProperty("section", True)
                self._labels.append(lab)
                self.lay.addWidget(lab)
            for w in ws:
                self.lay.addWidget(w)
                w.show()
                shown.add(w.card.id)
                self.displayed.append(w)
        for cid, w in self.widgets.items():
            if cid not in shown:
                w.hide()
        if not self.displayed:
            self.empty.setText(self._empty_text())
            self.lay.addWidget(self.empty)
            self.empty.show()
        else:
            self.empty.hide()
        self.lay.addStretch(1)
        self.reorder_enabled = self.view == VIEW_CARDS
        return len(self.displayed)

    def _empty_text(self) -> str:
        if self.query:
            return "No notes match your search."
        return {
            VIEW_TODAY: "Nothing due or reminding you today.",
            VIEW_ARCHIVE: "The archive is empty.\nMark a note done with ✓ to archive it.",
        }.get(self.view, "No notes here yet.\nUse + New, paste with Ctrl+V or drop files here.")

    # ---------------------------------------------------------- drag & drop
    def _drop_target(self, pos: QPoint) -> CardWidget | None:
        for w in self.displayed:
            g = w.geometry()
            if pos.y() < g.center().y():
                return w
        return None

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasFormat(CARD_MIME) and self.reorder_enabled:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):  # noqa: N802
        if not (e.mimeData().hasFormat(CARD_MIME) and self.reorder_enabled):
            e.ignore()
            return
        e.acceptProposedAction()
        pos = e.position().toPoint()
        target = self._drop_target(pos)
        if target is not None:
            y = target.geometry().top() - self.lay.spacing() // 2 - 1
        elif self.displayed:
            y = self.displayed[-1].geometry().bottom() + self.lay.spacing() // 2
        else:
            y = 0
        self.indicator.setGeometry(4, max(0, y), self.width() - 14, 3)
        self.indicator.show()
        self.indicator.raise_()
        # Auto-scroll near the viewport edges.
        vp = self.scroll.viewport()
        vy = self.mapTo(vp, pos).y()
        bar = self.scroll.verticalScrollBar()
        if vy < 30:
            bar.setValue(bar.value() - 20)
        elif vy > vp.height() - 30:
            bar.setValue(bar.value() + 20)

    def dragLeaveEvent(self, _e):  # noqa: N802
        self.indicator.hide()

    def dropEvent(self, e):  # noqa: N802
        self.indicator.hide()
        if not (e.mimeData().hasFormat(CARD_MIME) and self.reorder_enabled):
            e.ignore()
            return
        card_id = int(bytes(e.mimeData().data(CARD_MIME)).decode())
        if card_id not in self.widgets:
            return
        target = self._drop_target(e.position().toPoint())
        if target is not None and target.card.id == card_id:
            e.acceptProposedAction()
            return
        self.order.remove(card_id)
        if target is not None:
            self.order.insert(self.order.index(target.card.id), card_id)
        else:
            others = [w.card.id for w in self.displayed if w.card.id != card_id]
            idx = self.order.index(others[-1]) + 1 if others else len(self.order)
            self.order.insert(idx, card_id)
        e.acceptProposedAction()
        self.refresh()
        self.order_changed.emit(list(self.order))


class BoardTabBar(QTabBar):
    """Category tabs that also accept cards dropped onto them."""

    card_dropped = Signal(int, int)  # card id, board id

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideRight)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def board_id(self, index: int) -> int:
        data = self.tabData(index)
        return int(data) if data is not None else 0

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasFormat(CARD_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        idx = self.tabAt(e.position().toPoint())
        if e.mimeData().hasFormat(CARD_MIME) and idx >= 0 and self.board_id(idx):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):  # noqa: N802
        idx = self.tabAt(e.position().toPoint())
        if idx < 0 or not self.board_id(idx):
            return
        card_id = int(bytes(e.mimeData().data(CARD_MIME)).decode())
        e.acceptProposedAction()
        self.card_dropped.emit(card_id, self.board_id(idx))
