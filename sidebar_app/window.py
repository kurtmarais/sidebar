"""The edge-docked sidebar window.

Collapsed, the window is only a thin blue strip on the screen edge.
Resting the cursor on it (or clicking it, or Ctrl+Shift+S, or the tray
icon) slides the panel out. The panel slides away again when the cursor
leaves, unless it is pinned, you are typing in it, or a reminder is
waiting for an answer.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QPoint, QRect, QSize, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QGuiApplication, QImage, QKeySequence, QPainter, QPainterPath, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from . import backup, dialogs, winplatform
from .cardlist import VIEW_ARCHIVE, VIEW_CARDS, VIEW_TODAY, BoardTabBar, CardList, today_section
from .cards import (
    CARD_KINDS, EXTENSION_LANGUAGES, CardEnv, CardWidget, confirm, icon_button, retint,
    tomorrow_at_nine,
)
from .storage import Card, Database
from .theme import Palette, app_icon, make_icon, palette, stylesheet

STRIP_W = 6        # collapsed width: the visible blue strip
MARGIN = 8         # gap around the floating panel
ANIM_MS = 200

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_CODE_BYTES = 512 * 1024
NEW_TYPES = ("note", "checklist", "code", "links", "secret")


class EdgeStrip(QWidget):
    """The thin strip at the screen edge. Click toggles; drag moves it up or down."""

    clicked = Signal()
    dragged = Signal(int)       # global y of cursor
    drag_finished = Signal()

    def __init__(self, win: "SidebarWindow"):
        super().__init__(win)
        self.win = win
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Sidebar: rest here or click to open · drag to move")
        self._press_y: int | None = None
        self._dragging = False
        self._hover = False

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.win.p
        color = QColor(pal.strip_hover if self._hover else pal.strip)
        w = 4.0
        x = self.width() - w if self.win.edge == "right" else 0.0
        path = QPainterPath()
        path.addRoundedRect(x, 0, w, self.height(), 2, 2)
        p.fillPath(path, color)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._press_y = int(e.globalPosition().y())
            self._dragging = False

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._press_y is None:
            return
        gy = int(e.globalPosition().y())
        if not self._dragging and abs(gy - self._press_y) > 6 and self.win.progress == 0:
            self._dragging = True
            self.setCursor(Qt.SizeVerCursor)
        if self._dragging:
            self.dragged.emit(gy)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() != Qt.LeftButton:
            return
        if self._dragging:
            self.drag_finished.emit()
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.clicked.emit()
        self._press_y = None
        self._dragging = False


class Header(QWidget):
    """Panel header; dragging it to the other side of a screen snaps the sidebar there."""

    def __init__(self, win: "SidebarWindow"):
        super().__init__()
        self.win = win
        self._press: QPoint | None = None
        self._moved = False

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._moved = False

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._press is not None and (e.globalPosition().toPoint() - self._press).manhattanLength() > 30:
            if not self._moved:
                self._moved = True
                self.setCursor(Qt.SizeAllCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._press is not None and self._moved:
            self.unsetCursor()
            self.win.snap_to(e.globalPosition().toPoint())
        self._press = None
        self._moved = False


class SidebarWindow(QWidget):
    def __init__(self, db: Database, data_dir: Path):
        super().__init__(None)
        self.db = db
        self.data_dir = data_dir
        self.assets_dir = data_dir / "assets"
        self.backup_dir = data_dir / "backups"
        self.s = db.settings()
        self.p: Palette = palette(self.s["theme"])
        self.progress = 0.0
        self._target = 0.0
        self._hotkey_hwnd: int | None = None
        self._context: tuple[str, str] | None = None     # last external foreground window
        self._link_mode = False
        self._alerts: list[int] = []                     # card ids with a reminder waiting
        self._offered_meetings: set[str] = set()
        self._focus_end: float | None = None
        self.tray = None

        self.setWindowTitle("Sidebar")
        self.setWindowIcon(app_icon())
        self._apply_flags()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)

        self.env = CardEnv(
            self.p, db.boards, self.assets_dir, lambda: self._context,
            winplatform.dpapi_protect, winplatform.dpapi_unprotect,
            winplatform.dpapi_available(),
        )

        self.strip = EdgeStrip(self)
        self.strip.clicked.connect(self._strip_clicked)
        self.strip.dragged.connect(self._strip_dragged)
        self.strip.drag_finished.connect(lambda: self.db.set_setting("strip_position", self.s["strip_position"]))
        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        self._build_panel()

        self.anim = QVariantAnimation(self)
        self.anim.setDuration(ANIM_MS)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(lambda v: self._set_progress(float(v)))
        self.anim.finished.connect(self._anim_finished)

        self.open_timer = QTimer(self, singleShot=True)
        self.open_timer.timeout.connect(self._hover_open)
        self.close_timer = QTimer(self, singleShot=True)
        self.close_timer.timeout.connect(self._maybe_collapse)
        self.reshow_timer = QTimer(self, singleShot=True)
        self.reshow_timer.setInterval(60_000)
        self.reshow_timer.timeout.connect(lambda: self._alerts and self.expand())
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start(15_000)
        self.context_timer = QTimer(self)
        self.context_timer.timeout.connect(self.poll_context)
        self.focus_timer = QTimer(self)
        self.focus_timer.timeout.connect(self._focus_tick)
        self.day_timer = QTimer(self)                   # badges change at midnight
        self.day_timer.timeout.connect(self._refresh_badges)
        self.day_timer.start(10 * 60_000)

        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda _s: self.reposition())
        app.screenRemoved.connect(lambda _s: QTimer.singleShot(200, self.reposition))
        app.primaryScreenChanged.connect(lambda _s: self.reposition())
        for scr in QGuiApplication.screens():
            scr.availableGeometryChanged.connect(lambda _g: self.reposition())

        self.load_cards()
        self.apply_theme()
        self._update_context_timer()
        self.reposition()

    # ================================================================ build
    def _build_panel(self) -> None:
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(12, 10, 8, 10)
        lay.setSpacing(8)

        # Header: title, pin, settings menu
        self.header = Header(self)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(4, 0, 0, 0)
        h.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setObjectName("panelTitle")
        self.title_label.setToolTip("Drag to the other side of the screen to move the sidebar")
        self.pin_btn = icon_button("pin", self.p, "Keep the sidebar open")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setChecked(bool(self.s["pinned"]))
        self.pin_btn.toggled.connect(self._pin_toggled)
        self.menu_btn = icon_button("settings", self.p, "Settings and backup")
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        self.main_menu = QMenu(self)
        self.main_menu.aboutToShow.connect(self._build_main_menu)
        self.menu_btn.setMenu(self.main_menu)
        h.addWidget(self.title_label, 1)
        h.addWidget(self.pin_btn)
        h.addWidget(self.menu_btn)
        lay.addWidget(self.header)

        # View pills: Notes / Today / Archive
        views = QHBoxLayout()
        views.setSpacing(4)
        self.view_btns: dict[str, QToolButton] = {}
        for key, label, icon in ((VIEW_CARDS, "Notes", "cards"), (VIEW_TODAY, "Today", "today"),
                                 (VIEW_ARCHIVE, "Archive", "archive")):
            b = QToolButton()
            b.setText(label)
            b.setProperty("view", True)
            b.setProperty("iconName", icon)
            b.setIconSize(QSize(14, 14))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.set_view(k))
            self.view_btns[key] = b
            views.addWidget(b)
        views.addStretch(1)
        lay.addLayout(views)

        # Banners (reminders, meetings, link mode, focus)
        self.banner_box = QVBoxLayout()
        self.banner_box.setSpacing(6)
        lay.addLayout(self.banner_box)
        self._banners: dict[str, QFrame] = {}

        # Search
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search notes   Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self, singleShot=True, interval=120)
        self.search.textChanged.connect(lambda _t: self.search_timer.start())
        self.search_timer.timeout.connect(self.refresh)
        lay.addWidget(self.search)

        # Category tabs
        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(2)
        self.tabs = BoardTabBar()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.card_dropped.connect(self.move_card)
        self.tabs.customContextMenuRequested.connect(self._tab_menu)
        self.add_board_btn = icon_button("plus", self.p, "New category", 14)
        self.add_board_btn.clicked.connect(self.add_board)
        tabs_row.addWidget(self.tabs, 1)
        tabs_row.addWidget(self.add_board_btn)
        lay.addLayout(tabs_row)

        # "In front" context row for notes that follow your window
        self.context_row = QWidget()
        cr = QHBoxLayout(self.context_row)
        cr.setContentsMargins(4, 0, 0, 0)
        self.context_label = QLabel()
        self.context_label.setProperty("muted", True)
        self.link_notes_btn = QPushButton("Link notes")
        self.link_notes_btn.setProperty("flat", True)
        self.link_notes_btn.setCursor(Qt.PointingHandCursor)
        self.link_notes_btn.setToolTip("Choose notes to show first whenever this app is in front")
        self.link_notes_btn.clicked.connect(self.start_link_mode)
        cr.addWidget(self.context_label, 1)
        cr.addWidget(self.link_notes_btn)
        self.context_row.hide()
        lay.addWidget(self.context_row)

        # Cards
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.list = CardList(self.scroll)
        self.list.order_changed.connect(self.db.set_card_order)
        self.scroll.setWidget(self.list)
        lay.addWidget(self.scroll, 1)

        # Footer
        foot = QHBoxLayout()
        self.count_label = QLabel()
        self.count_label.setProperty("muted", True)
        self.new_btn = QPushButton("+  New")
        self.new_btn.setProperty("primary", True)
        self.new_btn.setCursor(Qt.PointingHandCursor)
        new_menu = QMenu(self.new_btn)
        for t in NEW_TYPES:
            act = new_menu.addAction(make_icon(CARD_KINDS[t][3], self.p.muted), CARD_KINDS[t][1],
                                     lambda tt=t: self.new_card(tt))
            if t == "secret" and not winplatform.dpapi_available():
                act.setEnabled(False)
                act.setText("Secret (Windows only)")
        new_menu.addSeparator()
        new_menu.addAction(make_icon("image", self.p.muted), "Image or file…", self.add_files_dialog)
        self.new_menu = new_menu
        self.new_btn.setMenu(new_menu)
        foot.addWidget(self.count_label, 1)
        foot.addWidget(self.new_btn)
        lay.addLayout(foot)

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+F"), self, self.focus_search)
        QShortcut(QKeySequence("Ctrl+N"), self, lambda: self.new_card("note"))
        QShortcut(QKeySequence("Escape"), self, self._escape)
        paste = QShortcut(QKeySequence.Paste, self.panel, self.paste)
        paste.setContext(Qt.WidgetWithChildrenShortcut)

    # ============================================================== cards
    def load_cards(self) -> None:
        for card in self.db.cards():
            self.list.add(self._make_widget(card), at_top=False)
        self._rebuild_tabs()
        self.set_view(VIEW_CARDS)

    def _make_widget(self, card: Card) -> CardWidget:
        w = CardWidget(card, self.env)
        w.save_requested.connect(self._save_card)
        w.delete_requested.connect(self.delete_card)
        w.archive_requested.connect(self.archive_card)
        w.move_requested.connect(self.move_card)
        w.link_toggled.connect(self._toggle_link)
        w.meta_changed.connect(self._meta_changed)
        return w

    def _save_card(self, card_id: int, fields: dict) -> None:
        self.db.update_card(card_id, **fields)

    def _target_board(self) -> int:
        bid = self.tabs.board_id(self.tabs.currentIndex()) if self.tabs.count() else 0
        return bid or self.db.boards()[0].id

    def add_card(self, type_: str, focus: bool = True, **kwargs) -> CardWidget:
        card = self.db.add_card(self._target_board(), type_, **kwargs)
        w = self._make_widget(card)
        self.list.add(w, at_top=True)
        if self.list.view != VIEW_CARDS:
            self.set_view(VIEW_CARDS)
        if self.search.text():
            self.search.clear()
        self.refresh()
        self.scroll.verticalScrollBar().setValue(0)
        if focus:
            self.expand(activate=True)
            QTimer.singleShot(ANIM_MS + 20, w.focus_title if type_ != "secret" else w.body.focus_body)
        return w

    def new_card(self, type_: str) -> None:
        self.add_card(type_, color={"checklist": "blue", "code": "purple", "links": "teal",
                                    "secret": "grey"}.get(type_, "yellow"))

    def delete_card(self, card_id: int) -> None:
        w = self.list.widgets.get(card_id)
        if w is None:
            return
        has_content = bool(w.card.title or (w.body and w.body.plain_text()) or w.card.type in ("image", "secret"))
        if has_content and not confirm(self, "Delete card",
                                       f"Delete “{w.card.title or 'Untitled'}” permanently?"):
            return
        asset = w.card.content.get("asset") if w.card.type == "image" else None
        self.db.delete_card(card_id)
        self.list.remove(card_id)
        if asset:
            try:
                (self.assets_dir / asset).unlink()
            except OSError:
                pass
        self.refresh()

    def archive_card(self, card_id: int, archived: bool) -> None:
        w = self.list.widgets.get(card_id)
        if w is None:
            return
        w.flush()
        self.db.archive_card(card_id, archived)
        w.card.archived = archived
        w.card.archived_at = time.time() if archived else None
        if card_id in self._alerts and archived:
            self._dismiss_alert(card_id)
        w._update_actions()
        w.refresh_meta()
        w.update()
        self.refresh()

    def move_card(self, card_id: int, board_id: int) -> None:
        w = self.list.widgets.get(card_id)
        if w is None or w.card.board_id == board_id:
            return
        self.db.update_card(card_id, board_id=board_id)
        w.card.board_id = board_id
        self.refresh()

    def _meta_changed(self, card_id: int) -> None:
        w = self.list.widgets.get(card_id)
        if w and card_id in self._alerts and not w.card.remind_at:
            self._dismiss_alert(card_id)
        if self.list.view == VIEW_TODAY or self.list.context:
            self.refresh()
        self._update_today_count()

    def _refresh_badges(self) -> None:
        for w in self.list.widgets.values():
            w.refresh_meta()
        self._update_today_count()

    # ----------------------------------------------------- files and paste
    def _store_image(self, image: QImage, name: str) -> CardWidget | None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        asset = f"{uuid.uuid4().hex}.png"
        if not image.save(str(self.assets_dir / asset), "PNG"):
            return None
        return self.add_card("image", focus=False, title=name, color="blue",
                             content={"asset": asset, "name": name})

    def add_file(self, path: Path) -> CardWidget | None:
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            asset = f"{uuid.uuid4().hex}{ext}"
            shutil.copy2(path, self.assets_dir / asset)
            return self.add_card("image", focus=False, title=path.stem, color="blue",
                                 content={"asset": asset, "name": path.name})
        if ext in EXTENSION_LANGUAGES and path.stat().st_size <= MAX_CODE_BYTES:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                text = None
            if text is not None:
                return self.add_card("code", focus=False, title=path.name, color="purple",
                                     content={"text": text, "language": EXTENSION_LANGUAGES[ext]})
        return self.add_card("file", focus=False, title=path.stem, color="grey",
                             content={"path": str(path), "name": path.name})

    def add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add images or files")
        for f in files:
            self.add_file(Path(f))
        if files:
            self.expand()

    def paste(self) -> None:
        mime = QApplication.clipboard().mimeData()
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                self._store_image(image, f"Screenshot {datetime.now():%d %b %H:%M}")
                return
        if mime.hasUrls() and all(u.isLocalFile() for u in mime.urls()):
            for u in mime.urls():
                self.add_file(Path(u.toLocalFile()))
            return
        if mime.hasText() and mime.text().strip():
            text = mime.text().strip()
            first = text.splitlines()[0][:60]
            self.add_card("note", focus=False, title=first, content={"text": text})

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.expand()
        elif e.mimeData().hasImage():
            e.acceptProposedAction()
            self.expand()
        else:
            e.ignore()

    def dragMoveEvent(self, e):  # noqa: N802
        e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        mime = e.mimeData()
        if mime.hasUrls():
            for u in mime.urls():
                if u.isLocalFile():
                    self.add_file(Path(u.toLocalFile()))
        elif mime.hasImage():
            self._store_image(QImage(mime.imageData()), "Image")
        e.acceptProposedAction()

    # ============================================================ views
    def set_view(self, view: str) -> None:
        self.list.view = view
        for key, b in self.view_btns.items():
            b.setChecked(key == view)
        if self._link_mode and view != VIEW_CARDS:
            self.stop_link_mode()
        self.refresh()

    def refresh(self) -> None:
        self.list.query = self.search.text().strip()
        self.list.board_id = self.tabs.board_id(self.tabs.currentIndex()) if self.tabs.count() else 0
        self.list.context = self._context if self._follow_enabled() else None
        n = self.list.refresh()
        noun = {VIEW_ARCHIVE: "archived", VIEW_TODAY: "for today"}.get(self.list.view, "")
        self.count_label.setText(f"{n} note{'s' if n != 1 else ''} {noun}".strip())
        self._update_today_count()
        self._update_context_row()

    def _update_today_count(self) -> None:
        today, now = datetime.now().date(), datetime.now()
        n = sum(1 for w in self.list.widgets.values()
                if not w.card.archived and today_section(w, today, now) in ("Overdue", "Today"))
        self.view_btns[VIEW_TODAY].setText(f"Today  {n}" if n else "Today")

    def _rebuild_tabs(self, select_board: int | None = None) -> None:
        current = select_board if select_board is not None else int(self.s.get("current_board", 0))
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        i = self.tabs.addTab("All")
        self.tabs.setTabData(i, 0)
        sel = 0
        for b in self.db.boards():
            i = self.tabs.addTab(b.name)
            self.tabs.setTabData(i, b.id)
            if b.id == current:
                sel = i
        self.tabs.setCurrentIndex(sel)
        self.tabs.blockSignals(False)

    def _tab_changed(self, index: int) -> None:
        bid = self.tabs.board_id(index)
        self.s["current_board"] = bid
        self.db.set_setting("current_board", bid)
        self.refresh()

    def add_board(self) -> None:
        name = dialogs.ask_text(self, "New category", "Category name:")
        if name:
            b = self.db.add_board(name)
            self._rebuild_tabs(select_board=b.id)
            self._tab_changed(self.tabs.currentIndex())

    def _tab_menu(self, pos: QPoint) -> None:
        idx = self.tabs.tabAt(pos)
        bid = self.tabs.board_id(idx) if idx >= 0 else 0
        if not bid:
            return
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_board(bid, self.tabs.tabText(idx)))
        boards = [b.id for b in self.db.boards()]
        pos_i = boards.index(bid)
        if pos_i > 0:
            menu.addAction("Move left", lambda: self._move_board(bid, -1))
        if pos_i < len(boards) - 1:
            menu.addAction("Move right", lambda: self._move_board(bid, 1))
        menu.addSeparator()
        act = menu.addAction("Delete category…", lambda: self._delete_board(bid, self.tabs.tabText(idx)))
        act.setEnabled(len(boards) > 1)
        menu.exec(self.tabs.mapToGlobal(pos))

    def _rename_board(self, bid: int, old: str) -> None:
        name = dialogs.ask_text(self, "Rename category", "Category name:", old)
        if name:
            self.db.rename_board(bid, name)
            self._rebuild_tabs(select_board=bid)

    def _move_board(self, bid: int, delta: int) -> None:
        ids = [b.id for b in self.db.boards()]
        i = ids.index(bid)
        ids.insert(i + delta, ids.pop(i))
        self.db.set_board_order(ids)
        self._rebuild_tabs(select_board=bid)

    def _delete_board(self, bid: int, name: str) -> None:
        n = sum(1 for w in self.list.widgets.values() if w.card.board_id == bid)
        if not confirm(self, "Delete category",
                       f"Delete “{name}” and its {n} note{'s' if n != 1 else ''}? This cannot be undone."):
            return
        self.db.delete_board(bid)
        for cid in [c for c, w in self.list.widgets.items() if w.card.board_id == bid]:
            self.list.remove(cid)
        self._rebuild_tabs(select_board=0)
        self._tab_changed(0)

    def focus_search(self) -> None:
        self.expand(activate=True)
        self.search.setFocus()
        self.search.selectAll()

    def _escape(self) -> None:
        if self._link_mode:
            self.stop_link_mode()
        elif self.search.text():
            self.search.clear()
        else:
            self.collapse(force=True)

    # ============================================================= banners
    def _show_banner(self, key: str, kind: str, title: str, text: str,
                     buttons: list[tuple[str, object, bool]]) -> QFrame:
        self._hide_banner(key)
        frame = QFrame()
        frame.setObjectName("banner")
        frame.setProperty("kind", kind)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 700;")
        t.setWordWrap(True)
        lay.addWidget(t)
        if text:
            body = QLabel(text)
            body.setProperty("muted", True)
            body.setWordWrap(True)
            lay.addWidget(body)
            frame.body_label = body  # type: ignore[attr-defined]
        row = QHBoxLayout()
        row.setSpacing(6)
        for label, action, primary in buttons:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("primary", primary)
            if isinstance(action, QMenu):
                b.setMenu(action)
            else:
                b.clicked.connect(action)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.banner_box.addWidget(frame)
        self._banners[key] = frame
        return frame

    def _hide_banner(self, key: str) -> None:
        frame = self._banners.pop(key, None)
        if frame is not None:
            self.banner_box.removeWidget(frame)
            frame.deleteLater()

    # ----------------------------------------------------------- reminders
    def check_reminders(self) -> None:
        due = [c for c in self.db.due_reminders() if c.id not in self._alerts]
        if not due:
            return
        for c in due:
            self._alerts.append(c.id)
            if self.tray is not None:
                self.tray.showMessage("Reminder", c.title or "Untitled note", app_icon(), 8000)
        self._show_alert()
        self.expand()

    def _show_alert(self) -> None:
        self._alerts = [cid for cid in self._alerts if cid in self.list.widgets]
        if not self._alerts:
            self._hide_banner("reminder")
            return
        cid = self._alerts[0]
        w = self.list.widgets[cid]
        snooze = QMenu(self)
        now = datetime.now()
        for label, ts in (("5 minutes", time.time() + 300), ("15 minutes", time.time() + 900),
                          ("1 hour", time.time() + 3600), ("Tomorrow 09:00", tomorrow_at_nine(now))):
            snooze.addAction(label, lambda t=ts: self._snooze(cid, t))
        more = f" · {len(self._alerts) - 1} more waiting" if len(self._alerts) > 1 else ""
        self._show_banner(
            "reminder", "reminder", f"⏰  {w.card.title or 'Untitled note'}",
            f"Reminder · {datetime.fromtimestamp(w.card.remind_at or time.time()):%H:%M}{more}",
            [("Got it!", lambda: self._dismiss_alert(cid, clear=True), True),
             ("Snooze", snooze, False),
             (f"Focus {self.s['focus_minutes']}m", lambda: self._alert_focus(cid), False)],
        )

    def _dismiss_alert(self, cid: int, clear: bool = False) -> None:
        if cid in self._alerts:
            self._alerts.remove(cid)
        w = self.list.widgets.get(cid)
        if clear and w is not None:
            w.set_reminder(None)
        self._show_alert()
        if not self._alerts:
            self.reshow_timer.stop()

    def _snooze(self, cid: int, ts: float) -> None:
        w = self.list.widgets.get(cid)
        if w is not None:
            w.set_reminder(ts)
        self._dismiss_alert(cid)

    def _alert_focus(self, cid: int) -> None:
        self._dismiss_alert(cid, clear=True)
        self.start_focus()

    # --------------------------------------------------------------- focus
    def start_focus(self) -> None:
        self._focus_end = time.time() + int(self.s["focus_minutes"]) * 60
        self._show_banner("focus", "focus", "Focus session", "",
                          [("Stop", self.stop_focus, False)])
        self.focus_timer.start(1000)
        self._focus_tick()

    def stop_focus(self) -> None:
        self._focus_end = None
        self.focus_timer.stop()
        self._hide_banner("focus")

    def _focus_tick(self) -> None:
        if self._focus_end is None:
            return
        left = int(self._focus_end - time.time())
        frame = self._banners.get("focus")
        if left <= 0:
            self.stop_focus()
            if self.tray is not None:
                self.tray.showMessage("Focus session complete", "Time for a break.", app_icon(), 8000)
            self._show_banner("focus", "focus", "Focus session complete",
                              "Nice work. Take a short break.",
                              [("OK", lambda: self._hide_banner("focus"), True)])
            self.expand()
            return
        if frame is not None:
            frame.findChild(QLabel).setText(f"Focus · {left // 60:02d}:{left % 60:02d} left")

    # ---------------------------------------------- notes that follow you
    def _follow_enabled(self) -> bool:
        return winplatform.IS_WINDOWS and bool(self.s["follow_window"])

    def _update_context_timer(self) -> None:
        wanted = winplatform.IS_WINDOWS and (self.s["follow_window"] or self.s["suggest_meeting_notes"])
        if wanted and not self.context_timer.isActive():
            self.context_timer.start(2000)
        elif not wanted:
            self.context_timer.stop()

    def set_context(self, exe: str, title: str) -> None:
        """Record the app in front (other than the sidebar). Public for tests."""
        old = self._context
        self._context = (exe, title)
        if self._follow_enabled():
            before = {c for c, w in self.list.widgets.items() if old and w.card.matches_window(*old)}
            after = {c for c, w in self.list.widgets.items() if w.card.matches_window(exe, title)}
            if before != after or (after and old != self._context):
                self.refresh()
        self._update_context_row()
        if self.s["suggest_meeting_notes"]:
            self._check_meeting(exe, title)

    def poll_context(self) -> None:
        fg = winplatform.foreground_window()
        if not fg:
            return
        exe, title, pid = fg
        if pid == os.getpid() or not title or (exe, title) == self._context:
            return
        if exe in ("explorer.exe", "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe") \
                and title in ("", "Program Manager"):
            return
        self.set_context(exe, title)

    def _update_context_row(self) -> None:
        show = self._follow_enabled() and self._context is not None and self.list.view == VIEW_CARDS
        self.context_row.setVisible(show)
        if show and self._context:
            exe, title = self._context
            fm = self.context_label.fontMetrics()
            self.context_label.setText(fm.elidedText(f"In front: {title}", Qt.ElideRight, 220))
            self.context_label.setToolTip(f"{exe} · {title}")

    def start_link_mode(self) -> None:
        if not self._context:
            return
        exe = self._context[0]
        self._link_mode = True
        self.set_view(VIEW_CARDS)
        self._link_mode = True
        self._show_banner("link", "link", f"Link notes to {exe}",
                          "Click Link on each note to show it first whenever this app is in front.",
                          [("Done", self.stop_link_mode, True)])
        for w in self.list.widgets.values():
            w.set_link_mode(True, linked=self._is_linked_here(w))
        self.expand(activate=True)

    def _is_linked_here(self, w: CardWidget) -> bool:
        return bool(self._context) and w.card.link_app == self._context[0] and not w.card.link_title

    def _toggle_link(self, card_id: int) -> None:
        w = self.list.widgets.get(card_id)
        if w is None or not self._context:
            return
        if self._is_linked_here(w):
            w.set_link("", "")
        else:
            w.set_link(self._context[0], "")
        w.set_link_mode(True, linked=self._is_linked_here(w))

    def stop_link_mode(self) -> None:
        self._link_mode = False
        self._hide_banner("link")
        for w in self.list.widgets.values():
            w.set_link_mode(False)
        self.refresh()

    def _check_meeting(self, exe: str, title: str) -> None:
        name = winplatform.detect_meeting(exe, title)
        if not name or name in self._offered_meetings:
            return
        self._offered_meetings.add(name)
        if any(not w.card.archived and w.card.matches_window(exe, title) for w in self.list.widgets.values()):
            return  # notes from last time are already shown first
        self._show_banner("meeting", "link", f"You're in a meeting: {name}", "",
                          [("Start meeting notes", lambda: self._start_meeting_notes(name), True),
                           ("Not now", lambda: self._hide_banner("meeting"), False)])
        self.expand()

    def _start_meeting_notes(self, name: str) -> None:
        self._hide_banner("meeting")
        stamp = datetime.now().strftime("%d %b %Y")
        w = self.add_card("note", focus=False, title=name, color="blue", link_title=name,
                          content={"text": f"{stamp}\n\n"})
        self.refresh()
        self.expand(activate=True)
        QTimer.singleShot(ANIM_MS + 20, w.body.focus_body)

    # ============================================================ geometry
    @property
    def edge(self) -> str:
        return "left" if self.s["edge"] == "left" else "right"

    def _screen(self):
        name = self.s.get("screen") or ""
        for scr in QGuiApplication.screens():
            if scr.name() == name:
                return scr
        return QGuiApplication.primaryScreen()

    def _strip_rect(self) -> QRect:
        avail = self._screen().availableGeometry()
        length = max(60, int(avail.height() * float(self.s["strip_length"])))
        y = avail.top() + int((avail.height() - length) * min(1.0, max(0.0, float(self.s["strip_position"]))))
        x = avail.right() + 1 - STRIP_W if self.edge == "right" else avail.left()
        return QRect(x, y, STRIP_W, length)

    def _geometry_for(self, progress: float) -> QRect:
        if progress <= 0:
            return self._strip_rect()
        avail = self._screen().availableGeometry()
        width = STRIP_W + round((int(self.s["panel_width"]) + MARGIN) * progress)
        x = avail.right() + 1 - width if self.edge == "right" else avail.left()
        return QRect(x, avail.top(), width, avail.height())

    def _layout_children(self) -> None:
        w, h = self.width(), self.height()
        pw = int(self.s["panel_width"])
        strip = self._strip_rect()
        sy = strip.top() - self.geometry().top() if self.progress > 0 else 0
        if self.edge == "right":
            self.strip.setGeometry(w - STRIP_W, sy, STRIP_W, strip.height())
            self.panel.setGeometry(0, MARGIN, pw, max(0, h - 2 * MARGIN))
        else:
            self.strip.setGeometry(0, sy, STRIP_W, strip.height())
            self.panel.setGeometry(w - pw, MARGIN, pw, max(0, h - 2 * MARGIN))
        self.panel.setVisible(self.progress > 0)
        self.strip.raise_()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._layout_children()

    def _set_progress(self, value: float) -> None:
        self.progress = value
        self.setGeometry(self._geometry_for(value))
        self._layout_children()

    def reposition(self) -> None:
        self.anim.stop()
        self._set_progress(self._target)

    def expand(self, activate: bool = False) -> None:
        self.open_timer.stop()
        self.close_timer.stop()
        if not self.isVisible():
            self.show()
        if self._target != 1.0:
            self._target = 1.0
            self.anim.stop()
            self.anim.setStartValue(self.progress)
            self.anim.setEndValue(1.0)
            self.anim.start()
        self.raise_()
        if activate:
            self.activateWindow()

    def collapse(self, force: bool = False) -> None:
        """Slide the panel away. ``force`` ignores the pin (Esc, hotkey, strip click)."""
        if not force and (self.pin_btn.isChecked() or self._alerts):
            return
        self.close_timer.stop()
        if self._link_mode:
            self.stop_link_mode()
        if self._target == 0.0:
            return
        self._target = 0.0
        for w in self.list.widgets.values():
            w.flush()
            if w.card.type == "secret":
                w.lock()
        if self._alerts:
            self.reshow_timer.start()
        self.anim.stop()
        self.anim.setStartValue(self.progress)
        self.anim.setEndValue(0.0)
        self.anim.start()

    def toggle(self) -> None:
        if self._target >= 1.0:
            self.collapse(force=True)
        else:
            self.expand(activate=True)

    def _anim_finished(self) -> None:
        if self._target == 0.0:
            self.panel.hide()

    def snap_to(self, global_pos: QPoint) -> None:
        scr = QGuiApplication.screenAt(global_pos) or self._screen()
        g = scr.availableGeometry()
        edge = "left" if global_pos.x() < g.center().x() else "right"
        primary = scr == QGuiApplication.primaryScreen()
        changed = {"edge": edge, "screen": "" if primary else scr.name()}
        if changed["edge"] == self.s["edge"] and changed["screen"] == self.s["screen"]:
            return
        for k, v in changed.items():
            self.s[k] = v
            self.db.set_setting(k, v)
        self.reposition()

    # ----------------------------------------------------- hover behaviour
    def _hover_opens(self) -> bool:
        return self.s["open_mode"] in ("hover", "both")

    def enterEvent(self, e):  # noqa: N802
        self.close_timer.stop()
        if self._target == 0.0 and self._hover_opens():
            self.open_timer.start(int(self.s["open_delay_ms"]))
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self.open_timer.stop()
        if self._target == 1.0 and self.s["open_mode"] != "click":
            self.close_timer.start(int(self.s["collapse_delay_ms"]))
        super().leaveEvent(e)

    def _hover_open(self) -> None:
        if self.geometry().contains(QCursor.pos()) and QApplication.mouseButtons() == Qt.NoButton:
            self.expand()

    def _strip_clicked(self) -> None:
        if self.s["open_mode"] == "hover" and self._target == 0.0:
            return
        self.toggle()

    def _strip_dragged(self, gy: int) -> None:
        avail = self._screen().availableGeometry()
        length = self._strip_rect().height()
        span = max(1, avail.height() - length)
        self.s["strip_position"] = min(1.0, max(0.0, (gy - avail.top() - length / 2) / span))
        self.reposition()

    def _keep_open(self) -> bool:
        if self.pin_btn.isChecked() or self._alerts or self._link_mode:
            return True
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return True
        if QApplication.mouseButtons() != Qt.NoButton:
            return True
        return False

    def _maybe_collapse(self) -> None:
        if self._keep_open() or self.geometry().contains(QCursor.pos()):
            return
        focus = QApplication.focusWidget()
        editing = (self.isActiveWindow() and focus is not None and self.isAncestorOf(focus)
                   and any(focus.inherits(k) for k in ("QLineEdit", "QTextEdit", "QPlainTextEdit"))
                   and not (focus is self.search and not self.search.text()))
        if editing:
            return   # typing: wait until the window is deactivated instead
        self.collapse()

    def changeEvent(self, e):  # noqa: N802
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self._target == 1.0:
            QTimer.singleShot(150, self._collapse_if_inactive)

    def _collapse_if_inactive(self) -> None:
        if self.isActiveWindow() or self._keep_open() or self.geometry().contains(QCursor.pos()):
            return
        self.collapse()

    def _pin_toggled(self, on: bool) -> None:
        self.pin_btn.setProperty("iconName", "pinned" if on else "pin")
        retint(self.pin_btn, self.p, self.p.accent if on else None)
        self.s["pinned"] = on
        self.db.set_setting("pinned", on)

    # ===================================================== menu & settings
    def _build_main_menu(self) -> None:
        m = self.main_menu
        m.clear()
        m.addAction(make_icon("settings", self.p.muted), "Settings…", self.open_settings)
        m.addSeparator()
        if self._focus_end is None:
            m.addAction(make_icon("focus", self.p.muted), f"Start focus session ({self.s['focus_minutes']} min)",
                        self.start_focus)
        else:
            m.addAction(make_icon("focus", self.p.muted), "Stop focus session", self.stop_focus)
        m.addSeparator()
        m.addAction("Export backup…", self.export_backup)
        m.addAction("Restore backup…", self.restore_backup)
        m.addAction("Export as Markdown…", self.export_markdown)
        m.addAction("Open data folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir))))
        m.addSeparator()
        m.addAction("Hide sidebar", lambda: self.collapse(force=True))
        m.addAction("Quit Sidebar", QApplication.quit)

    def open_settings(self) -> None:
        self.expand(activate=True)
        dlg = dialogs.SettingsDialog(self, self.s, str(self.data_dir))
        if dlg.exec() != dlg.Accepted:
            return
        values = dlg.values()
        old_top = self.s["always_on_top"]
        for k, v in values.items():
            self.s[k] = v
            self.db.set_setting(k, v)
        if winplatform.IS_WINDOWS and dlg.startup_enabled() != winplatform.is_startup_enabled():
            try:
                winplatform.set_startup_enabled(dlg.startup_enabled())
            except OSError as exc:
                QMessageBox.warning(self, "Start with Windows", f"Could not update the startup entry:\n{exc}")
        if old_top != self.s["always_on_top"]:
            self._apply_flags()
            self.show()
        self.apply_theme()
        self.register_hotkey()
        self._update_context_timer()
        self.reposition()
        self.refresh()

    def export_backup(self) -> None:
        include = False
        if any(w.card.type == "secret" for w in self.list.widgets.values()):
            include = QMessageBox.question(
                self, "Export backup",
                "Include secret notes?\nThey stay encrypted and can only be opened with their PIN "
                "on this Windows account.") == QMessageBox.Yes
        path, _ = QFileDialog.getSaveFileName(
            self, "Export backup", str(Path.home() / f"sidebar-backup-{datetime.now():%Y%m%d}.json"),
            "Sidebar backup (*.json)")
        if path:
            for w in self.list.widgets.values():
                w.flush()
            backup.export_json(self.db, Path(path), self.assets_dir, include_secrets=include)

    def restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Restore backup", str(Path.home()), "Sidebar backup (*.json)")
        if not path:
            return
        for w in self.list.widgets.values():
            w.flush()
        try:
            added = backup.import_json(self.db, Path(path), self.assets_dir, self.backup_dir)
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, "Restore backup", f"Could not restore this file:\n{exc}")
            return
        known = set(self.list.widgets)
        for card in self.db.cards():
            if card.id not in known:
                self.list.add(self._make_widget(card), at_top=False)
        self.list.order = [c.id for c in self.db.cards()]
        self._rebuild_tabs()
        self.refresh()
        QMessageBox.information(self, "Restore backup",
                                f"Added {added} note{'s' if added != 1 else ''}. Existing notes were left unchanged.")

    def export_markdown(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export as Markdown", str(Path.home() / "sidebar-notes.md"), "Markdown (*.md)")
        if path:
            for w in self.list.widgets.values():
                w.flush()
            backup.export_markdown(self.db, Path(path))

    # ================================================================ theme
    def apply_theme(self) -> None:
        self.p = palette(self.s["theme"])
        self.env.p = self.p
        QApplication.instance().setStyleSheet(stylesheet(self.p))
        self.title_label.setText(self.s["title"])
        for b in (self.pin_btn, self.menu_btn, self.add_board_btn):
            retint(b, self.p)
        self._pin_toggled(self.pin_btn.isChecked())
        for b in self.view_btns.values():
            retint(b, self.p)
        self.list.set_indicator_color(self.p.accent)
        for w in self.list.widgets.values():
            w.apply_theme()
        self.strip.update()

    def _apply_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.s["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self._hotkey_hwnd = None

    # =============================================================== hotkey
    def register_hotkey(self) -> None:
        if not winplatform.IS_WINDOWS:
            return
        if self._hotkey_hwnd is not None:
            winplatform.unregister_hotkey(self._hotkey_hwnd)
            self._hotkey_hwnd = None
        if self.s["hotkey_enabled"]:
            hwnd = int(self.winId())
            if winplatform.register_hotkey(hwnd):
                self._hotkey_hwnd = hwnd

    def nativeEvent(self, event_type, message):  # noqa: N802
        if winplatform.IS_WINDOWS and bytes(event_type) == b"windows_generic_MSG":
            if winplatform.is_hotkey_message(int(message)):
                self.toggle_from_hotkey()
                return True, 0
        return super().nativeEvent(event_type, message)

    def toggle_from_hotkey(self) -> None:
        if self._target >= 1.0:
            self.collapse(force=True)
        else:
            self.focus_search()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if self._hotkey_hwnd is None:
            QTimer.singleShot(0, self.register_hotkey)

    def shutdown(self) -> None:
        for w in self.list.widgets.values():
            w.flush()
        if self._hotkey_hwnd is not None:
            winplatform.unregister_hotkey(self._hotkey_hwnd)
            self._hotkey_hwnd = None
        self.db.set_setting("strip_position", self.s["strip_position"])
