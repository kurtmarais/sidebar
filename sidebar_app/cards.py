"""Card widgets.

Every card is an independent object with a type-specific body. Add a new
card type by writing a ``CardBody`` subclass and registering it in
``CARD_KINDS``.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDate, QDateTime, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QDrag, QFont, QIntValidator, QKeyEvent, QPainter,
    QPainterPath, QPixmap, QSyntaxHighlighter, QTextCharFormat,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPlainTextEdit, QPushButton, QSizePolicy, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from . import dialogs, secrets_store
from .storage import PRIORITY_LABELS, Board, Card
from .theme import (
    CARD_COLORS, PRIORITY_COLORS, Palette, blend, card_accent, card_background, make_icon,
)

CARD_MIME = "application/x-sidebar-card"
SAVE_DELAY_MS = 400


class CardEnv:
    """Shared context every card needs."""

    def __init__(self, p: Palette, boards: Callable[[], list[Board]], assets_dir: Path,
                 window_context: Callable[[], tuple[str, str] | None],
                 protect: secrets_store.Protect, unprotect: secrets_store.Protect,
                 secrets_available: bool):
        self.p = p
        self.boards = boards
        self.assets_dir = assets_dir
        self.window_context = window_context
        self.protect = protect
        self.unprotect = unprotect
        self.secrets_available = secrets_available


# =========================================================== helper widgets
def icon_button(name: str, p: Palette, tooltip: str = "", size: int = 16) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(make_icon(name, p.muted, size))
    btn.setIconSize(QSize(size, size))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setProperty("iconName", name)
    return btn


def retint(btn: QToolButton, p: Palette, color: str | None = None) -> None:
    name = btn.property("iconName")
    if name:
        btn.setIcon(make_icon(name, color or p.muted, btn.iconSize().width()))


class AutoTextEdit(QTextEdit):
    """Plain-text editor that grows with its content up to ``max_height``."""

    def __init__(self, placeholder: str = "", min_height: int = 28, max_height: int = 380, pad: int = 6):
        super().__init__()
        self.setAcceptRichText(False)
        self.setPlaceholderText(placeholder)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self._min, self._max, self._pad = min_height, max_height, pad
        self.document().setDocumentMargin(2)
        self.document().contentsChanged.connect(self._fit)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._fit()

    def _fit(self) -> None:
        doc = self.document()
        doc.setTextWidth(max(10, self.viewport().width()))
        h = int(doc.size().height()) + self.frameWidth() * 2 + self._pad
        self.setFixedHeight(max(self._min, min(self._max, h)))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()


class ItemTextEdit(AutoTextEdit):
    """Wrapping single-paragraph editor for checklist items, with list-editing keys."""

    enter = Signal()
    backspace_empty = Signal()
    move = Signal(int)
    nav = Signal(int)
    textEdited = Signal(str)

    def __init__(self, text: str = ""):
        super().__init__("", min_height=22, max_height=200, pad=2)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setPlainText(text)
        self.textChanged.connect(lambda: self.textEdited.emit(self.text()))

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802
        self.setPlainText(text)

    def keyPressEvent(self, e: QKeyEvent):  # noqa: N802
        key, mods = e.key(), e.modifiers()
        cur = self.textCursor()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.enter.emit()
        elif key == Qt.Key_Backspace and not self.toPlainText():
            self.backspace_empty.emit()
        elif key in (Qt.Key_Up, Qt.Key_Down) and mods & Qt.AltModifier:
            self.move.emit(-1 if key == Qt.Key_Up else 1)
        elif key == Qt.Key_Up and cur.blockNumber() == 0 and \
                cur.block().layout().lineForTextPosition(cur.positionInBlock()).lineNumber() == 0:
            self.nav.emit(-1)
        elif key == Qt.Key_Down and cur.atEnd():
            self.nav.emit(1)
        else:
            super().keyPressEvent(e)

    def insertFromMimeData(self, source):  # noqa: N802
        self.insertPlainText(source.text().replace("\n", " "))


class ItemLineEdit(QLineEdit):
    """Line edit with list-editing keys for checklist items."""

    enter = Signal()
    backspace_empty = Signal()
    move = Signal(int)
    nav = Signal(int)

    def keyPressEvent(self, e: QKeyEvent):  # noqa: N802
        key, mods = e.key(), e.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.enter.emit()
        elif key == Qt.Key_Backspace and not self.text():
            self.backspace_empty.emit()
        elif key in (Qt.Key_Up, Qt.Key_Down) and mods & Qt.AltModifier:
            self.move.emit(-1 if key == Qt.Key_Up else 1)
        elif key in (Qt.Key_Up, Qt.Key_Down):
            self.nav.emit(-1 if key == Qt.Key_Up else 1)
        else:
            super().keyPressEvent(e)


def badge(text: str, fg: str, bg: str, icon: str | None = None, tooltip: str = "") -> QFrame:
    """Small rounded pill with an optional drawn icon."""
    frame = QFrame()
    frame.setObjectName("badge")
    frame.setStyleSheet(f"#badge {{ background: {bg}; border-radius: 8px; }}")
    frame.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    frame.setToolTip(tooltip)
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(6, 1, 7, 1)
    lay.setSpacing(3)
    if icon:
        ic = QLabel()
        ic.setPixmap(make_icon(icon, fg, 11).pixmap(11, 11))
        lay.addWidget(ic)
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {fg}; font-size: 8.5pt; font-weight: 600; background: transparent;")
    lay.addWidget(lab)
    return frame


# ================================================================== bodies
class CardBody(QWidget):
    """Base class for a card's editable content."""

    edited = Signal()

    def __init__(self, content: dict, env: CardEnv):
        super().__init__()
        self.env = env

    @property
    def p(self) -> Palette:
        return self.env.p

    def content(self) -> dict:
        raise NotImplementedError

    def summary(self) -> str:
        return ""

    def plain_text(self) -> str:
        return ""

    def focus_body(self) -> None:
        self.setFocus()

    def apply_theme(self) -> None:
        pass

    def lock(self) -> None:
        pass


class NoteBody(CardBody):
    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = AutoTextEdit("Write something…", min_height=44)
        self.edit.setPlainText(content.get("text", ""))
        self.edit.textChanged.connect(self.edited)
        lay.addWidget(self.edit)

    def content(self) -> dict:
        return {"text": self.edit.toPlainText()}

    def plain_text(self) -> str:
        return self.edit.toPlainText()

    def summary(self) -> str:
        text = self.edit.toPlainText().strip()
        if not text:
            return "Empty note"
        first = text.splitlines()[0]
        return first[:90] + ("…" if len(first) > 90 or "\n" in text else "")

    def focus_body(self) -> None:
        self.edit.setFocus()
        self.edit.moveCursor(self.edit.textCursor().MoveOperation.End)


# ---------------------------------------------------------------- checklist
class ChecklistRow(QWidget):
    def __init__(self, body: "ChecklistBody", text: str, done: bool):
        super().__init__()
        self.body = body
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.check = QCheckBox()
        self.check.setChecked(done)
        self.check.setCursor(Qt.PointingHandCursor)
        self.edit = ItemTextEdit(text)
        self.edit.setPlaceholderText("Item")
        self.remove = icon_button("close", body.p, "Remove item", 12)
        self.remove.setVisible(False)
        lay.addWidget(self.check, 0, Qt.AlignTop)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.remove, 0, Qt.AlignTop)
        self.check.setContentsMargins(0, 4, 0, 0)
        self.check.toggled.connect(self._on_toggled)
        self.edit.textEdited.connect(lambda _t: body.edited.emit())
        self.edit.enter.connect(lambda: body.insert_after(self))
        self.edit.setFocusPolicy(Qt.ClickFocus | Qt.TabFocus)
        self.edit.backspace_empty.connect(lambda: body.remove_row(self, focus_prev=True))
        self.edit.move.connect(lambda d: body.move_row(self, d))
        self.edit.nav.connect(lambda d: body.focus_neighbour(self, d))
        self.remove.clicked.connect(lambda: body.remove_row(self))
        self.style_done()

    def _on_toggled(self, _checked: bool) -> None:
        self.style_done()
        self.body.edited.emit()

    def style_done(self) -> None:
        f = self.edit.font()
        f.setStrikeOut(self.check.isChecked())
        self.edit.setFont(f)
        self.edit.setStyleSheet(f"color: {self.body.p.muted};" if self.check.isChecked() else "")

    def enterEvent(self, e):  # noqa: N802
        self.remove.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self.remove.setVisible(False)
        super().leaveEvent(e)

    def data(self) -> dict:
        return {"text": self.edit.text(), "done": self.check.isChecked()}


class ChecklistBody(CardBody):
    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(0)
        outer.addLayout(self.rows_box)
        self.rows: list[ChecklistRow] = []
        for item in content.get("items", []):
            self._add_row(item.get("text", ""), bool(item.get("done")))

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 2, 0, 0)
        add_row.setSpacing(2)
        self.add_icon = QLabel()
        self.add_icon.setFixedWidth(20)
        self.add_icon.setAlignment(Qt.AlignCenter)
        self.add_field = ItemLineEdit()
        self.add_field.setPlaceholderText("Add item")
        self.add_field.enter.connect(self._commit_add_field)
        self.add_field.nav.connect(lambda d: d < 0 and self.rows and self.rows[-1].edit.setFocus())
        add_row.addWidget(self.add_icon)
        add_row.addWidget(self.add_field, 1)
        outer.addLayout(add_row)
        self.apply_theme()

    def _add_row(self, text: str, done: bool, index: int | None = None) -> ChecklistRow:
        row = ChecklistRow(self, text, done)
        index = len(self.rows) if index is None else index
        self.rows.insert(index, row)
        self.rows_box.insertWidget(index, row)
        return row

    def _commit_add_field(self) -> None:
        text = self.add_field.text().strip()
        if not text:
            return
        self._add_row(text, False)
        self.add_field.clear()
        self.edited.emit()

    def insert_after(self, row: ChecklistRow) -> None:
        new = self._add_row("", False, self.rows.index(row) + 1)
        new.edit.setFocus()
        self.edited.emit()

    def remove_row(self, row: ChecklistRow, focus_prev: bool = False) -> None:
        idx = self.rows.index(row)
        self.rows.pop(idx)
        self.rows_box.removeWidget(row)
        row.deleteLater()
        if focus_prev:
            (self.rows[idx - 1].edit if idx > 0 else self.add_field).setFocus()
        self.edited.emit()

    def move_row(self, row: ChecklistRow, delta: int) -> None:
        idx = self.rows.index(row)
        new = idx + delta
        if not 0 <= new < len(self.rows):
            return
        self.rows.insert(new, self.rows.pop(idx))
        self.rows_box.removeWidget(row)
        self.rows_box.insertWidget(new, row)
        row.edit.setFocus()
        self.edited.emit()

    def focus_neighbour(self, row: ChecklistRow, delta: int) -> None:
        idx = self.rows.index(row) + delta
        if idx >= len(self.rows):
            self.add_field.setFocus()
        elif idx >= 0:
            self.rows[idx].edit.setFocus()

    def clear_completed(self) -> None:
        for row in [r for r in self.rows if r.check.isChecked()]:
            self.rows.remove(row)
            self.rows_box.removeWidget(row)
            row.deleteLater()
        self.edited.emit()

    def content(self) -> dict:
        return {"items": [r.data() for r in self.rows]}

    def plain_text(self) -> str:
        return "\n".join(f"[{'x' if r.check.isChecked() else ' '}] {r.edit.text()}" for r in self.rows)

    def summary(self) -> str:
        if not self.rows:
            return "No items"
        done = sum(r.check.isChecked() for r in self.rows)
        return f"{len(self.rows) - done} outstanding · {done} of {len(self.rows)} done"

    def focus_body(self) -> None:
        self.add_field.setFocus()

    def apply_theme(self) -> None:
        self.add_icon.setPixmap(make_icon("plus", self.p.muted, 14).pixmap(14, 14))
        self.add_field.setStyleSheet(f"color: {self.p.muted};")
        for r in self.rows:
            retint(r.remove, self.p)
            r.style_done()


# --------------------------------------------------------------------- code
CODE_LANGUAGES = {
    "text": "Plain text", "python": "Python", "javascript": "JavaScript",
    "sql": "SQL", "json": "JSON",
}
EXTENSION_LANGUAGES = {
    ".py": "python", ".pyw": "python", ".js": "javascript", ".mjs": "javascript",
    ".ts": "javascript", ".jsx": "javascript", ".tsx": "javascript",
    ".sql": "sql", ".json": "json", ".txt": "text",
}
_KEYWORDS = {
    "python": "and as assert async await break class continue def del elif else except False "
              "finally for from global if import in is lambda None nonlocal not or pass raise "
              "return True try while with yield self",
    "javascript": "async await break case catch class const continue default delete do else "
                  "export extends false finally for function if import in instanceof let new "
                  "null return super switch this throw true try typeof undefined var void while yield",
    "sql": "select from where and or not insert into values update set delete create table "
           "drop alter index join left right inner outer on group by order having limit as "
           "distinct null is in like between case when then else end union all primary key",
    "json": "true false null",
}
_COMMENTS = {"python": r"#[^\n]*", "javascript": r"//[^\n]*", "sql": r"--[^\n]*"}


class CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document, language: str, dark: bool):
        super().__init__(document)
        self.language = language
        self.dark = dark

    def _fmt(self, color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(QFont.DemiBold)
        f.setFontItalic(italic)
        return f

    def highlightBlock(self, text: str):  # noqa: N802
        lang = self.language
        if lang == "text":
            return
        d = self.dark
        kw = self._fmt("#c792ea" if d else "#7c3aed", bold=True)
        st = self._fmt("#c3e88d" if d else "#15803d")
        num = self._fmt("#f78c6c" if d else "#c2410c")
        com = self._fmt("#6b7394" if d else "#8a8fa3", italic=True)
        words = _KEYWORDS.get(lang, "")
        if words:
            flags = re.IGNORECASE if lang == "sql" else 0
            for m in re.finditer(r"\b(?:%s)\b" % "|".join(words.split()), text, flags):
                self.setFormat(m.start(), m.end() - m.start(), kw)
        for m in re.finditer(r"\b\d+(?:\.\d+)?\b", text):
            self.setFormat(m.start(), m.end() - m.start(), num)
        for m in re.finditer(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`", text):
            self.setFormat(m.start(), m.end() - m.start(), st)
        pattern = _COMMENTS.get(lang)
        if pattern:
            for m in re.finditer(pattern, text):
                self.setFormat(m.start(), m.end() - m.start(), com)


class CodeEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setObjectName("code")
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabChangesFocus(False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        lines = max(2, min(22, self.document().blockCount()))
        h = lines * self.fontMetrics().lineSpacing() + 18
        if self.horizontalScrollBar().isVisible():
            h += self.horizontalScrollBar().height()
        self.setFixedHeight(h)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key_Tab:
            self.insertPlainText("    ")
        else:
            super().keyPressEvent(e)


class CodeBody(CardBody):
    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        bar = QHBoxLayout()
        self.lang_btn = QToolButton()
        self.lang_btn.setPopupMode(QToolButton.InstantPopup)
        self.lang_btn.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self.lang_btn)
        for key, label in CODE_LANGUAGES.items():
            menu.addAction(label, lambda k=key: self.set_language(k))
        self.lang_btn.setMenu(menu)
        self.copy_btn = icon_button("copy", env.p, "Copy code", 14)
        self.copy_btn.clicked.connect(self.copy)
        self.copied = QLabel("")
        self.copied.setProperty("muted", True)
        bar.addWidget(self.lang_btn)
        bar.addStretch(1)
        bar.addWidget(self.copied)
        bar.addWidget(self.copy_btn)
        lay.addLayout(bar)
        self.edit = CodeEdit()
        self.edit.setPlainText(content.get("text", ""))
        self.edit.textChanged.connect(self.edited)
        lay.addWidget(self.edit)
        self.language = content.get("language", "text")
        self.highlighter = CodeHighlighter(self.edit.document(), self.language, env.p.name == "dark")
        self._update_lang_label()

    def _update_lang_label(self) -> None:
        self.lang_btn.setText(CODE_LANGUAGES.get(self.language, "Plain text") + "  ▾")
        self.lang_btn.setStyleSheet(f"color: {self.p.muted}; font-size: 8.5pt;")

    def set_language(self, language: str) -> None:
        self.language = language
        self.highlighter.language = language
        self.highlighter.rehighlight()
        self._update_lang_label()
        self.edited.emit()

    def copy(self) -> None:
        QApplication.clipboard().setText(self.edit.toPlainText())
        self.copied.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copied.setText(""))

    def content(self) -> dict:
        return {"text": self.edit.toPlainText(), "language": self.language}

    def plain_text(self) -> str:
        return self.edit.toPlainText()

    def summary(self) -> str:
        n = self.edit.document().blockCount() if self.edit.toPlainText() else 0
        return f"{CODE_LANGUAGES.get(self.language, 'Code')} · {n} line{'s' if n != 1 else ''}"

    def focus_body(self) -> None:
        self.edit.setFocus()

    def apply_theme(self) -> None:
        self.highlighter.dark = self.p.name == "dark"
        self.highlighter.rehighlight()
        retint(self.copy_btn, self.p)
        self._update_lang_label()


# -------------------------------------------------------------------- links
class LinkRow(QWidget):
    def __init__(self, body: "LinksBody", label: str, url: str):
        super().__init__()
        self.body = body
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 2)
        lay.setSpacing(2)
        fields = QVBoxLayout()
        fields.setSpacing(0)
        self.label = QLineEdit(label)
        self.label.setPlaceholderText("Label")
        f = self.label.font()
        f.setWeight(QFont.DemiBold)
        self.label.setFont(f)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://…")
        fields.addWidget(self.label)
        fields.addWidget(self.url)
        self.open = icon_button("open", body.p, "Open link")
        self.remove = icon_button("close", body.p, "Remove link", 12)
        self.remove.setVisible(False)
        lay.addLayout(fields, 1)
        lay.addWidget(self.open, 0, Qt.AlignTop)
        lay.addWidget(self.remove, 0, Qt.AlignTop)
        self.label.textEdited.connect(lambda _t: body.edited.emit())
        self.url.textEdited.connect(lambda _t: body.edited.emit())
        self.label.returnPressed.connect(self.url.setFocus)
        self.url.returnPressed.connect(self.open_link)
        self.open.clicked.connect(self.open_link)
        self.remove.clicked.connect(lambda: body.remove_row(self))
        self.style()

    def style(self) -> None:
        self.url.setStyleSheet(f"color: {self.body.p.muted}; font-size: 9pt;")
        retint(self.open, self.body.p)
        retint(self.remove, self.body.p)

    def open_link(self) -> None:
        text = self.url.text().strip()
        if text:
            QDesktopServices.openUrl(QUrl.fromUserInput(text))

    def enterEvent(self, e):  # noqa: N802
        self.remove.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self.remove.setVisible(False)
        super().leaveEvent(e)

    def data(self) -> dict:
        return {"label": self.label.text(), "url": self.url.text()}


class LinksBody(CardBody):
    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(4)
        outer.addLayout(self.rows_box)
        self.rows: list[LinkRow] = []
        for link in content.get("links", []):
            self._add_row(link.get("label", ""), link.get("url", ""))
        self.add_btn = QToolButton()
        self.add_btn.setText("Add link")
        self.add_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self.add_link)
        outer.addWidget(self.add_btn, 0, Qt.AlignLeft)
        self.apply_theme()

    def _add_row(self, label: str, url: str) -> LinkRow:
        row = LinkRow(self, label, url)
        self.rows.append(row)
        self.rows_box.addWidget(row)
        return row

    def add_link(self) -> None:
        self._add_row("", "").label.setFocus()
        self.edited.emit()

    def remove_row(self, row: LinkRow) -> None:
        self.rows.remove(row)
        self.rows_box.removeWidget(row)
        row.deleteLater()
        self.edited.emit()

    def content(self) -> dict:
        return {"links": [r.data() for r in self.rows]}

    def plain_text(self) -> str:
        return "\n".join(f"{r.label.text()} {r.url.text()}".strip() for r in self.rows)

    def summary(self) -> str:
        n = len(self.rows)
        if not n:
            return "No links"
        names = ", ".join(r.label.text() or r.url.text() for r in self.rows[:3])
        return f"{n} link{'s' if n != 1 else ''} · {names}"

    def focus_body(self) -> None:
        if self.rows:
            self.rows[0].label.setFocus()
        else:
            self.add_link()

    def apply_theme(self) -> None:
        self.add_btn.setIcon(make_icon("plus", self.p.muted, 14))
        self.add_btn.setStyleSheet(f"color: {self.p.muted};")
        for r in self.rows:
            r.style()


# -------------------------------------------------------------------- image
class ImageBody(CardBody):
    """An image stored in the local assets folder."""

    MAX_HEIGHT = 260

    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        self.asset = content.get("asset", "")
        self.name = content.get("name", "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setCursor(Qt.PointingHandCursor)
        self.view.setToolTip("Click to open")
        self.view.mousePressEvent = lambda _e: self.open()  # type: ignore[assignment]
        lay.addWidget(self.view)
        self._pixmap = QPixmap(str(self.path())) if self.asset else QPixmap()
        if self._pixmap.isNull():
            self.view.setText("Image not found")
            self.view.setProperty("muted", True)

    def path(self) -> Path:
        return self.env.assets_dir / self.asset

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self._pixmap.isNull():
            return
        w = max(50, self.width())
        scaled = self._pixmap.scaled(w * self.devicePixelRatio(), self.MAX_HEIGHT * self.devicePixelRatio(),
                                     Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(self.devicePixelRatio())
        self.view.setPixmap(scaled)
        self.view.setFixedHeight(int(scaled.height() / self.devicePixelRatio()))

    def open(self) -> None:
        if self.path().is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path())))

    def content(self) -> dict:
        return {"asset": self.asset, "name": self.name}

    def summary(self) -> str:
        if self._pixmap.isNull():
            return "Image"
        return f"Image · {self._pixmap.width()}×{self._pixmap.height()}"


# --------------------------------------------------------------------- file
class FileBody(CardBody):
    """A shortcut to a document you open often. The file stays where it is."""

    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        self.file_path = content.get("path", "")
        self.name = content.get("name", "") or Path(self.file_path).name
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 0)
        self.icon = QLabel()
        self.icon.setFixedSize(28, 28)
        self.icon.setAlignment(Qt.AlignCenter)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.name_label = QLabel(self.name)
        self.name_label.setStyleSheet("font-weight: 600;")
        self.path_label = QLabel()
        self.path_label.setProperty("muted", True)
        self.path_label.setToolTip(self.file_path)
        text.addWidget(self.name_label)
        text.addWidget(self.path_label)
        self.open_btn = QPushButton("Open")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self.open)
        self.folder_btn = icon_button("file", env.p, "Show in folder")
        self.folder_btn.clicked.connect(self.show_folder)
        lay.addWidget(self.icon)
        lay.addLayout(text, 1)
        lay.addWidget(self.folder_btn)
        lay.addWidget(self.open_btn)
        self.apply_theme()

    def exists(self) -> bool:
        return bool(self.file_path) and Path(self.file_path).exists()

    def apply_theme(self) -> None:
        self.icon.setPixmap(make_icon("file", self.p.muted, 22).pixmap(22, 22))
        retint(self.folder_btn, self.p)
        parent = str(Path(self.file_path).parent) if self.file_path else ""
        self.path_label.setText(parent if self.exists() else "File not found · " + parent)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        fm = self.path_label.fontMetrics()
        parent = str(Path(self.file_path).parent)
        prefix = "" if self.exists() else "File not found · "
        self.path_label.setText(prefix + fm.elidedText(parent, Qt.ElideMiddle, max(60, self.path_label.width())))

    def open(self) -> None:
        if self.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.file_path))

    def show_folder(self) -> None:
        if self.file_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.file_path).parent)))

    def content(self) -> dict:
        return {"path": self.file_path, "name": self.name}

    def plain_text(self) -> str:
        return self.file_path

    def summary(self) -> str:
        return self.name or "File"


# ------------------------------------------------------------------- secret
def _pin_field(placeholder: str, p: Palette) -> QLineEdit:
    f = QLineEdit()
    f.setStyleSheet(f"QLineEdit {{ background: {p.surface}; border: 1px solid {p.panel_border};"
                    f" border-radius: 6px; padding: 3px; }} QLineEdit:focus {{ border-color: {p.strip}; }}")
    f.setEchoMode(QLineEdit.Password)
    f.setMaxLength(4)
    f.setValidator(QIntValidator(0, 9999))
    f.setPlaceholderText(placeholder)
    f.setFixedWidth(90)
    f.setAlignment(Qt.AlignCenter)
    return f


class SecretBody(CardBody):
    """PIN-locked note, encrypted with Windows protected storage."""

    def __init__(self, content: dict, env: CardEnv):
        super().__init__(content, env)
        self._content = dict(content)
        self._key: bytes | None = None
        self.guard = secrets_store.PinGuard()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 2, 0, 0)
        self.stack: QWidget | None = None
        self._show_state()

    def _swap(self, w: QWidget) -> None:
        if self.stack is not None:
            self.lay.removeWidget(self.stack)
            self.stack.deleteLater()
        self.stack = w
        self.lay.addWidget(w)

    def _show_state(self) -> None:
        if not self.env.secrets_available:
            lab = QLabel("Secret notes need Windows protected storage.")
            lab.setProperty("muted", True)
            self._swap(lab)
        elif not secrets_store.is_initialised(self._content):
            self._show_setup()
        elif self._key is None:
            self._show_locked()
        else:
            self._show_unlocked()

    def _show_setup(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        info = QLabel("Choose a 4-digit PIN. It cannot be recovered if forgotten.")
        info.setWordWrap(True)
        info.setProperty("muted", True)
        row = QHBoxLayout()
        pin1, pin2 = _pin_field("PIN", self.p), _pin_field("Repeat", self.p)
        btn = QPushButton("Set PIN")
        btn.setProperty("primary", True)
        row.addWidget(pin1)
        row.addWidget(pin2)
        row.addWidget(btn)
        row.addStretch(1)
        err = QLabel("")
        err.setStyleSheet(f"color: {self.p.danger};")

        def submit() -> None:
            a, b = pin1.text(), pin2.text()
            if not secrets_store.valid_pin(a):
                err.setText("PIN must be 4 digits.")
            elif a != b:
                err.setText("PINs do not match.")
            else:
                self._content = secrets_store.create(a, "", self.env.protect)
                self._key = secrets_store.derive_key(self._content, a)
                self.edited.emit()
                self._show_state()
                self.focus_body()

        btn.clicked.connect(submit)
        pin1.returnPressed.connect(pin2.setFocus)
        pin2.returnPressed.connect(submit)
        lay.addWidget(info)
        lay.addLayout(row)
        lay.addWidget(err)
        self._swap(w)
        self._first_field = pin1

    def _show_locked(self) -> None:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setPixmap(make_icon("lock", self.p.muted, 16).pixmap(16, 16))
        pin = _pin_field("PIN", self.p)
        btn = QPushButton("Unlock")
        err = QLabel("")
        err.setStyleSheet(f"color: {self.p.danger}; font-size: 9pt;")

        def submit() -> None:
            wait = self.guard.wait_seconds()
            if wait:
                err.setText(f"Too many attempts. Wait {wait}s.")
                return
            try:
                text, key = secrets_store.reveal(self._content, pin.text(), self.env.unprotect)
            except ValueError:
                self.guard.record(False)
                wait = self.guard.wait_seconds()
                err.setText(f"Too many attempts. Wait {wait}s." if wait else "Wrong PIN.")
                pin.clear()
                return
            except OSError as exc:
                err.setText(f"Cannot decrypt on this account ({exc.__class__.__name__}).")
                return
            self.guard.record(True)
            self._key = key
            self._show_unlocked(text)

        btn.clicked.connect(submit)
        pin.returnPressed.connect(submit)
        lay.addWidget(icon)
        lay.addWidget(pin)
        lay.addWidget(btn)
        lay.addWidget(err, 1)
        self._swap(w)
        self._first_field = pin

    def _show_unlocked(self, text: str = "") -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = AutoTextEdit("Secret text…", min_height=44)
        self.edit.setPlainText(text)
        self.edit.textChanged.connect(self.edited)
        lock = QPushButton("Lock")
        lock.setProperty("flat", True)
        lock.clicked.connect(self.lock)
        lay.addWidget(self.edit)
        lay.addWidget(lock, 0, Qt.AlignRight)
        self._swap(w)
        self._first_field = self.edit

    def lock(self) -> None:
        if self._key is not None:
            self.content()       # seal the latest text before dropping the key
            self.edited.emit()
            self._key = None
            self._show_state()

    def content(self) -> dict:
        if self._key is not None and hasattr(self, "edit"):
            try:
                self._content = secrets_store.seal_with_key(
                    self._content, self._key, self.edit.toPlainText(), self.env.protect)
            except RuntimeError:  # edit already deleted
                pass
        return dict(self._content)

    def summary(self) -> str:
        return "Unlocked" if self._key is not None else "Locked · PIN required"

    def focus_body(self) -> None:
        field = getattr(self, "_first_field", None)
        if field is not None:
            field.setFocus()


# ------------------------------------------------------------------ registry
# type -> (body class, display name, title placeholder, icon)
CARD_KINDS: dict[str, tuple[type[CardBody], str, str, str]] = {
    "note": (NoteBody, "Note", "Untitled note", "note"),
    "checklist": (ChecklistBody, "Checklist", "Things to do", "checklist"),
    "code": (CodeBody, "Code", "Snippet", "code"),
    "links": (LinksBody, "Links", "Useful links", "links"),
    "image": (ImageBody, "Image", "Image", "image"),
    "file": (FileBody, "File", "File", "file"),
    "secret": (SecretBody, "Secret", "Secret", "lock"),
}
TEXT_TYPES = ("note", "checklist", "code", "links")


def content_lines(card_type: str, content: dict) -> list[str]:
    if card_type in ("note", "code"):
        return content.get("text", "").splitlines()
    if card_type == "checklist":
        return [i.get("text", "") for i in content.get("items", [])]
    if card_type == "links":
        return [" ".join(x for x in (l.get("label", ""), l.get("url", "")) if x)
                for l in content.get("links", [])]
    return []


def convert_content(old_type: str, content: dict, new_type: str) -> dict:
    """Carry text across when a card changes between text-based types."""
    if old_type == new_type:
        return content
    lines = content_lines(old_type, content)
    if new_type == "code":
        return {"text": "\n".join(lines), "language": "text"}
    lines = [l for l in lines if l.strip()]
    if new_type == "note":
        return {"text": "\n".join(lines)}
    if new_type == "checklist":
        return {"items": [{"text": l.strip(), "done": False} for l in lines]}
    if new_type == "links":
        links = []
        for l in lines:
            words = l.split()
            url = next((w for w in words if "://" in w or w.startswith("www.")), "")
            links.append({"label": " ".join(w for w in words if w != url), "url": url})
        return {"links": links}
    return {}


# ============================================================ date helpers
def due_badge(due: date | None, today: date) -> tuple[str, str] | None:
    """(text, kind) for a due date; kind is overdue/today/tomorrow/later."""
    if due is None:
        return None
    if due < today:
        return "Overdue", "overdue"
    if due == today:
        return "Today", "today"
    if due == today + timedelta(days=1):
        return "Tomorrow", "tomorrow"
    fmt = "%a %d %b" if due.year == today.year else "%d %b %Y"
    return f"Due {due.strftime(fmt).replace(' 0', ' ')}", "later"


def reminder_text(ts: float, now: datetime) -> str:
    when = datetime.fromtimestamp(ts)
    if when.date() == now.date():
        day = "Today"
    elif when.date() == now.date() + timedelta(days=1):
        day = "Tomorrow"
    else:
        day = when.strftime("%a %d %b").replace(" 0", " ")
    return f"{day} {when:%H:%M}"


def next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) or 7)


def tomorrow_at_nine(now: datetime) -> float:
    t = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return t.timestamp()


# ============================================================== card frame
class DragGrip(QToolButton):
    def __init__(self, card: "CardWidget"):
        super().__init__()
        self.card = card
        self.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Drag to reorder, or drop on a category to move")
        self.setProperty("iconName", "grip")
        self.setIconSize(QSize(14, 14))
        self._press: QPoint | None = None

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._press = e.position().toPoint()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._press is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        self.card.start_drag()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._press = None
        super().mouseReleaseEvent(e)


class CardWidget(QFrame):
    """One card. Persists its own edits through ``save_requested``."""

    save_requested = Signal(int, dict)
    delete_requested = Signal(int)
    archive_requested = Signal(int, bool)
    move_requested = Signal(int, int)
    link_toggled = Signal(int)          # in "link notes" mode
    meta_changed = Signal(int)          # due date / reminder / link changed

    def __init__(self, card: Card, env: CardEnv):
        super().__init__()
        self.card = card
        self.env = env
        self._pending: dict = {}
        self._hover = False
        self._link_mode = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.flush)

        self.setObjectName("card")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Maximum)
        self.setMinimumWidth(0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 8)
        lay.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(0)
        p = env.p
        self.grip = DragGrip(self)
        self.toggle = icon_button("chevron_down", p, "Collapse / expand", 14)
        self.toggle.clicked.connect(lambda: self.set_collapsed(not self.card.collapsed))
        self.type_icon = QLabel()
        self.type_icon.setFixedWidth(20)
        self.title = QLineEdit(card.title)
        self.title.setObjectName("cardTitle")
        self.title.textEdited.connect(self._title_edited)
        self.title.returnPressed.connect(self._title_return)
        self.done_btn = icon_button("check", p, "Mark done (move to archive)")
        self.done_btn.clicked.connect(lambda: self.archive_requested.emit(self.card.id, True))
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.clicked.connect(lambda: self.archive_requested.emit(self.card.id, False))
        self.link_btn = QPushButton("Link")
        self.link_btn.setCursor(Qt.PointingHandCursor)
        self.link_btn.clicked.connect(lambda: self.link_toggled.emit(self.card.id))
        self.more = icon_button("more", p, "Card options")
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self)
        self.menu.aboutToShow.connect(self._build_menu)
        self.more.setMenu(self.menu)
        for w in (self.grip, self.toggle, self.type_icon):
            header.addWidget(w)
        header.addWidget(self.title, 1)
        for w in (self.done_btn, self.restore_btn, self.link_btn, self.more):
            header.addWidget(w)
        lay.addLayout(header)

        self.meta = QHBoxLayout()
        self.meta.setContentsMargins(34, 0, 0, 0)
        self.meta.setSpacing(4)
        self.meta_widget = QWidget()
        self.meta_widget.setLayout(self.meta)
        lay.addWidget(self.meta_widget)

        self.summary = QLabel()
        self.summary.setProperty("muted", True)
        self.summary.setWordWrap(True)
        self.summary.setContentsMargins(36, 0, 4, 2)
        lay.addWidget(self.summary)

        self.body_holder = QVBoxLayout()
        self.body_holder.setContentsMargins(4, 0, 0, 0)
        lay.addLayout(self.body_holder)
        self.body: CardBody | None = None
        self._install_body(card.type, card.content)
        self._apply_collapsed()
        self.refresh_meta()
        self._update_actions()
        self.apply_theme()

    # ----------------------------------------------------------- structure
    @property
    def p(self) -> Palette:
        return self.env.p

    def _install_body(self, card_type: str, content: dict) -> None:
        if self.body is not None:
            self.body_holder.removeWidget(self.body)
            self.body.deleteLater()
        body_cls, _name, placeholder, _icon = CARD_KINDS[card_type]
        self.body = body_cls(content, self.env)
        self.body.edited.connect(self._body_edited)
        self.body_holder.addWidget(self.body)
        self.title.setPlaceholderText(placeholder)

    def _apply_collapsed(self) -> None:
        assert self.body is not None
        collapsed = self.card.collapsed
        self.body.setVisible(not collapsed)
        self.summary.setVisible(collapsed)
        if collapsed:
            self.summary.setText(self.body.summary())
        self.toggle.setProperty("iconName", "chevron_right" if collapsed else "chevron_down")
        retint(self.toggle, self.p)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self.card.collapsed:
            return
        self.card.collapsed = collapsed
        self._apply_collapsed()
        self._queue({"collapsed": collapsed}, immediate=True)

    def _update_actions(self) -> None:
        archived = self.card.archived
        self.restore_btn.setVisible(archived and not self._link_mode)
        self.done_btn.setVisible(not archived and self._hover and not self._link_mode)
        self.grip.setVisible(not archived and not self._link_mode)
        self.link_btn.setVisible(self._link_mode)
        self.more.setVisible(not self._link_mode)

    def set_link_mode(self, on: bool, linked: bool = False) -> None:
        self._link_mode = on
        self.link_btn.setText("Linked" if linked else "Link")
        self.link_btn.setProperty("primary", linked)
        self.link_btn.style().unpolish(self.link_btn)
        self.link_btn.style().polish(self.link_btn)
        self._update_actions()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self._update_actions()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self._update_actions()
        super().leaveEvent(e)

    def refresh_meta(self) -> None:
        """Rebuild the priority / due / reminder / link badges."""
        while self.meta.count():
            item = self.meta.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        p, c = self.p, self.card
        shown = False
        if c.priority:
            col = PRIORITY_COLORS[c.priority]
            self.meta.addWidget(badge(PRIORITY_LABELS[c.priority], col, blend(p.card_base, col, 0.18),
                                      tooltip="Priority"))
            shown = True
        db = due_badge(c.due(), date.today())
        if db:
            text, kind = db
            col = {"overdue": p.danger, "today": p.accent, "tomorrow": p.info}.get(kind, p.muted)
            self.meta.addWidget(badge(text, col, blend(p.card_base, col, 0.18), "today",
                                      f"Due {c.due_date}"))
            shown = True
        if c.remind_at:
            when = reminder_text(c.remind_at, datetime.now())
            self.meta.addWidget(badge(when.removeprefix("Today "), p.text,
                                      blend(p.card_base, p.accent, 0.14), "bell", "Reminder " + when))
            shown = True
        if c.link_app or c.link_title:
            target = c.link_app or "any app"
            if c.link_title:
                target += f" · “{c.link_title}”"
            short = c.link_title if c.link_title and len(c.link_title) < 18 else (c.link_app or "window")
            self.meta.addWidget(badge(short, p.info, blend(p.card_base, p.info, 0.15), "link",
                                      "Shown first in " + target))
            shown = True
        if c.archived and c.archived_at:
            self.meta.addWidget(badge(datetime.fromtimestamp(c.archived_at).strftime("%d %b"),
                                      p.muted, blend(p.card_base, p.muted, 0.12), "archive", "Archived"))
            shown = True
        self.meta.addStretch(1)
        self.meta_widget.setVisible(shown)

    def apply_theme(self) -> None:
        p = self.p
        for btn in (self.toggle, self.more, self.grip, self.done_btn):
            retint(btn, p)
        icon = CARD_KINDS[self.card.type][3]
        self.type_icon.setPixmap(make_icon(icon, card_accent(self.card.color), 15).pixmap(15, 15))
        if self.body:
            self.body.apply_theme()
        self.refresh_meta()
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        bg = QColor(card_background(self.p, self.card.color))
        if self.card.archived:
            bg.setAlphaF(0.7)
        painter.fillPath(path, bg)
        painter.setPen(QColor(blend(self.p.card_base, card_accent(self.card.color), 0.28)))
        painter.drawPath(path)
        painter.setClipPath(path)
        painter.fillRect(QRectF(r.left(), r.top(), 4, r.height()), QColor(card_accent(self.card.color)))

    # ---------------------------------------------------------------- menu
    def _build_menu(self) -> None:
        m = self.menu
        m.clear()
        c = self.card
        if c.archived:
            m.addAction("Restore", lambda: self.archive_requested.emit(c.id, False))
            m.addSeparator()
            m.addAction("Delete permanently", lambda: self.delete_requested.emit(c.id))
            return

        today = date.today()
        now = datetime.now()
        due = m.addMenu(make_icon("today", self.p.muted), "Due date")
        due.addAction("Today", lambda: self.set_due(today))
        due.addAction("Tomorrow", lambda: self.set_due(today + timedelta(days=1)))
        due.addAction("Next week", lambda: self.set_due(next_monday(today)))
        due.addAction("Pick a date…", self._pick_due)
        if c.due_date:
            due.addSeparator()
            due.addAction("Remove due date", lambda: self.set_due(None))

        rem = m.addMenu(make_icon("bell", self.p.muted), "Reminder")
        rem.addAction("In 1 hour", lambda: self.set_reminder(time.time() + 3600))
        evening = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if evening > now + timedelta(minutes=10):
            rem.addAction("This evening (18:00)", lambda: self.set_reminder(evening.timestamp()))
        rem.addAction("Tomorrow (09:00)", lambda: self.set_reminder(tomorrow_at_nine(now)))
        rem.addAction("Pick date and time…", self._pick_reminder)
        if c.remind_at:
            rem.addSeparator()
            rem.addAction("Remove reminder", lambda: self.set_reminder(None))

        pri = m.addMenu("Priority")
        for level in (3, 2, 1, 0):
            icon = make_icon("dot", PRIORITY_COLORS.get(level, self.p.muted))
            act = pri.addAction(icon, PRIORITY_LABELS[level])
            act.setCheckable(True)
            act.setChecked(c.priority == level)
            act.triggered.connect(lambda _c=False, lv=level: self.set_priority(lv))

        colours = m.addMenu("Colour")
        for key, (label, hexcol) in CARD_COLORS.items():
            act = colours.addAction(make_icon("dot", hexcol), label)
            act.setCheckable(True)
            act.setChecked(key == c.color)
            act.triggered.connect(lambda _c=False, k=key: self.set_color(k))

        boards = m.addMenu("Category")
        for b in self.env.boards():
            act = boards.addAction(b.name)
            act.setCheckable(True)
            act.setChecked(b.id == c.board_id)
            act.triggered.connect(lambda _c=False, bid=b.id: self.move_requested.emit(c.id, bid))

        link = m.addMenu(make_icon("link", self.p.muted), "Link to window")
        ctx = self.env.window_context()
        if ctx:
            exe, title = ctx
            link.addAction(f"Anywhere in {exe}", lambda: self.set_link(exe, ""))
            link.addAction(f"Only {exe} windows containing…", lambda: self._ask_link(exe, title))
            link.addAction("Any window containing…", lambda: self._ask_link("", title))
        else:
            na = link.addAction("Open the app first, then open this menu")
            na.setEnabled(False)
        if c.link_app or c.link_title:
            link.addSeparator()
            link.addAction("Remove link", lambda: self.set_link("", ""))

        if c.type in TEXT_TYPES:
            types = m.addMenu("Card type")
            for key in TEXT_TYPES:
                act = types.addAction(make_icon(CARD_KINDS[key][3], self.p.muted), CARD_KINDS[key][1])
                act.setCheckable(True)
                act.setChecked(key == c.type)
                act.triggered.connect(lambda _c=False, k=key: self.set_type(k))

        m.addSeparator()
        if isinstance(self.body, ChecklistBody):
            m.addAction("Clear completed items", self.body.clear_completed)
        if self.body and self.body.plain_text():
            m.addAction(make_icon("copy", self.p.muted), "Copy text",
                        lambda: QApplication.clipboard().setText(self.body.plain_text()))
        m.addAction("Expand" if c.collapsed else "Collapse", lambda: self.set_collapsed(not c.collapsed))
        m.addSeparator()
        m.addAction(make_icon("check", self.p.muted), "Mark done",
                    lambda: self.archive_requested.emit(c.id, True))
        m.addAction("Delete", lambda: self.delete_requested.emit(c.id))

    # ------------------------------------------------------- field setters
    def set_color(self, color: str) -> None:
        self.card.color = color
        self.apply_theme()
        self._queue({"color": color}, immediate=True)

    def set_priority(self, level: int) -> None:
        self.card.priority = level
        self.refresh_meta()
        self._queue({"priority": level}, immediate=True)

    def set_due(self, due: date | None) -> None:
        self.card.due_date = due.isoformat() if due else None
        self.refresh_meta()
        self._queue({"due_date": self.card.due_date}, immediate=True)
        self.meta_changed.emit(self.card.id)

    def set_reminder(self, ts: float | None) -> None:
        self.card.remind_at = ts
        self.refresh_meta()
        self._queue({"remind_at": ts}, immediate=True)
        self.meta_changed.emit(self.card.id)

    def set_link(self, app: str, title_part: str) -> None:
        self.card.link_app, self.card.link_title = app, title_part
        self.refresh_meta()
        self._queue({"link_app": app, "link_title": title_part}, immediate=True)
        self.meta_changed.emit(self.card.id)

    def _pick_due(self) -> None:
        d = self.card.due()
        picked = dialogs.pick_date(self.window(), "Due date",
                                   QDate(d.year, d.month, d.day) if d else None)
        if picked is not None:
            self.set_due(date(picked.year(), picked.month(), picked.day()))

    def _pick_reminder(self) -> None:
        initial = QDateTime.fromSecsSinceEpoch(int(self.card.remind_at)) if self.card.remind_at else None
        picked = dialogs.pick_datetime(self.window(), "Reminder", initial)
        if picked is not None:
            self.set_reminder(float(picked.toSecsSinceEpoch()))

    def _ask_link(self, exe: str, title: str) -> None:
        guess = re.split(r"\s+[-–—|]\s+", title)[0].strip() if title else ""
        where = f"{exe} windows" if exe else "any window"
        text = dialogs.ask_text(self.window(), "Link to window",
                                f"Show this note first in {where} whose title contains:", guess)
        if text:
            self.set_link(exe, text)

    def set_type(self, new_type: str) -> None:
        if new_type == self.card.type:
            return
        assert self.body is not None
        content = convert_content(self.card.type, self.body.content(), new_type)
        self.card.type = new_type
        self.card.content = content
        self._install_body(new_type, content)
        self._apply_collapsed()
        self.apply_theme()
        self._queue({"type": new_type, "content": content}, immediate=True)

    # -------------------------------------------------------------- saving
    def _title_edited(self, text: str) -> None:
        self.card.title = text
        self._queue({"title": text})

    def _title_return(self) -> None:
        if self.card.collapsed:
            self.set_collapsed(False)
        assert self.body is not None
        self.body.focus_body()

    def _body_edited(self) -> None:
        self._content_dirty = True
        self._queue({})
        if self.card.collapsed and self.body:
            self.summary.setText(self.body.summary())

    def _queue(self, fields: dict, immediate: bool = False) -> None:
        self._pending.update(fields)
        if immediate:
            self.flush()
        else:
            self._save_timer.start()

    def flush(self) -> None:
        self._save_timer.stop()
        if getattr(self, "_content_dirty", False) and self.body is not None:
            self._content_dirty = False
            self.card.content = self.body.content()
            self._pending["content"] = self.card.content
        if self._pending:
            fields, self._pending = self._pending, {}
            self.save_requested.emit(self.card.id, fields)

    # ---------------------------------------------------------------- misc
    def search_text(self) -> str:
        if self._save_timer.isActive() and self.body is not None and self.card.type != "secret":
            self.card.content = self.body.content()
        return self.card.search_text()

    def lock(self) -> None:
        if self.body is not None:
            self.body.lock()
            self.flush()

    def focus_title(self) -> None:
        self.title.setFocus()
        self.title.selectAll()

    def start_drag(self) -> None:
        self.flush()
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(CARD_MIME, str(self.card.id).encode())
        drag.setMimeData(mime)
        shot = self.grab()
        if shot.height() > 160 * shot.devicePixelRatio():
            shot = shot.copy(0, 0, shot.width(), int(160 * shot.devicePixelRatio()))
        faded = QPixmap(shot.size())
        faded.setDevicePixelRatio(shot.devicePixelRatio())
        faded.fill(Qt.transparent)
        painter = QPainter(faded)
        painter.setOpacity(0.85)
        painter.drawPixmap(0, 0, shot)
        painter.end()
        drag.setPixmap(faded)
        drag.setHotSpot(QPoint(20, 14))
        self.setEnabled(False)
        try:
            drag.exec(Qt.MoveAction)
        finally:
            self.setEnabled(True)


def confirm(parent: QWidget, title: str, text: str, ok: str = "Delete") -> bool:
    return dialogs.message(parent, title, text, "question", (ok, "Cancel")) == ok
