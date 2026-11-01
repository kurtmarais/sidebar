"""Small dialogs: date / date-time pickers, text prompt and settings."""

from __future__ import annotations

from PySide6.QtCore import QDate, QDateTime, Qt, QTime
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCalendarWidget, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from . import winplatform


def _buttons(dialog: QDialog, ok_text: str = "OK") -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = box.button(QDialogButtonBox.Ok)
    ok.setText(ok_text)
    ok.setProperty("primary", True)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class DatePickerDialog(QDialog):
    """Calendar with an optional time field."""

    def __init__(self, parent: QWidget | None, title: str, initial: QDateTime, with_time: bool):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QVBoxLayout(self)
        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.calendar.setSelectedDate(initial.date())
        lay.addWidget(self.calendar)
        self.time: QTimeEdit | None = None
        if with_time:
            row = QHBoxLayout()
            row.addWidget(QLabel("Time"))
            self.time = QTimeEdit(initial.time())
            self.time.setDisplayFormat("HH:mm")
            row.addWidget(self.time, 1)
            lay.addLayout(row)
        lay.addWidget(_buttons(self, "Set"))

    def value(self) -> QDateTime:
        t = self.time.time() if self.time else QTime(9, 0)
        return QDateTime(self.calendar.selectedDate(), t)


def pick_date(parent: QWidget | None, title: str, initial: QDate | None = None) -> QDate | None:
    start = QDateTime(initial or QDate.currentDate(), QTime(9, 0))
    dlg = DatePickerDialog(parent, title, start, with_time=False)
    return dlg.value().date() if dlg.exec() == QDialog.Accepted else None


def pick_datetime(parent: QWidget | None, title: str, initial: QDateTime | None = None) -> QDateTime | None:
    if initial is None:
        initial = QDateTime.currentDateTime().addSecs(3600)
        initial.setTime(QTime(initial.time().hour(), 0))
    dlg = DatePickerDialog(parent, title, initial, with_time=True)
    return dlg.value() if dlg.exec() == QDialog.Accepted else None


def ask_text(parent: QWidget | None, title: str, label: str, text: str = "") -> str | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    lab = QLabel(label)
    lab.setWordWrap(True)
    lay.addWidget(lab)
    edit = QLineEdit(text)
    edit.selectAll()
    lay.addWidget(edit)
    lay.addWidget(_buttons(dlg))
    dlg.setMinimumWidth(320)
    if dlg.exec() != QDialog.Accepted:
        return None
    return edit.text().strip()


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None, settings: dict, data_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Sidebar settings")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.title = QLineEdit(settings["title"])
        form.addRow("Sidebar title", self.title)

        self.edge = QComboBox()
        self.edge.addItem("Right", "right")
        self.edge.addItem("Left", "left")
        self.edge.setCurrentIndex(max(0, self.edge.findData(settings["edge"])))
        form.addRow("Sidebar side", self.edge)

        self.screen = QComboBox()
        self.screen.addItem("Primary monitor", "")
        for s in QGuiApplication.screens():
            g = s.geometry()
            self.screen.addItem(f"{s.name()}  ({g.width()}×{g.height()})", s.name())
        idx = self.screen.findData(settings["screen"])
        self.screen.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Monitor", self.screen)

        self.width = QSpinBox()
        self.width.setRange(260, 640)
        self.width.setSingleStep(10)
        self.width.setSuffix(" px")
        self.width.setValue(int(settings["panel_width"]))
        form.addRow("Panel width", self.width)

        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings["theme"])))
        form.addRow("Appearance", self.theme)

        self.open_mode = QComboBox()
        self.open_mode.addItem("Rest cursor on the strip, or click it", "both")
        self.open_mode.addItem("Rest cursor on the strip only", "hover")
        self.open_mode.addItem("Click the strip only", "click")
        self.open_mode.setCurrentIndex(max(0, self.open_mode.findData(settings["open_mode"])))
        form.addRow("Open by", self.open_mode)

        self.delay = QSpinBox()
        self.delay.setRange(0, 3000)
        self.delay.setSingleStep(100)
        self.delay.setSuffix(" ms")
        self.delay.setValue(int(settings["open_delay_ms"]))
        form.addRow("Open delay", self.delay)

        self.strip_length = QSpinBox()
        self.strip_length.setRange(10, 90)
        self.strip_length.setSuffix(" % of screen height")
        self.strip_length.setValue(round(float(settings["strip_length"]) * 100))
        form.addRow("Edge strip length", self.strip_length)

        self.focus = QSpinBox()
        self.focus.setRange(5, 120)
        self.focus.setSuffix(" min")
        self.focus.setValue(int(settings["focus_minutes"]))
        form.addRow("Focus session", self.focus)

        self.on_top = QCheckBox("Keep above other windows")
        self.on_top.setChecked(bool(settings["always_on_top"]))
        form.addRow("", self.on_top)

        self.hotkey = QCheckBox("Ctrl+Shift+S opens or closes the sidebar from any app")
        self.hotkey.setChecked(bool(settings["hotkey_enabled"]))
        self.hotkey.setEnabled(winplatform.IS_WINDOWS)
        form.addRow("", self.hotkey)

        self.follow = QCheckBox("Notes that follow your window")
        self.follow.setToolTip("Linked notes move to the top while their app or window is in front.\n"
                               "Only the title of the front window is read; nothing is stored or sent.")
        self.follow.setChecked(bool(settings["follow_window"]))
        self.follow.setEnabled(winplatform.IS_WINDOWS)
        form.addRow("", self.follow)

        self.meetings = QCheckBox("Suggest meeting notes when a call starts")
        self.meetings.setChecked(bool(settings["suggest_meeting_notes"]))
        self.meetings.setEnabled(winplatform.IS_WINDOWS)
        form.addRow("", self.meetings)

        self.startup = QCheckBox("Start with Windows")
        self.startup.setToolTip("Also keeps reminders working, since the sidebar must be running to alert you.")
        self.startup.setChecked(winplatform.is_startup_enabled())
        self.startup.setEnabled(winplatform.IS_WINDOWS)
        form.addRow("", self.startup)

        lay.addLayout(form)
        path = QLabel(f"Notes are stored only on this PC, in:\n{data_dir}")
        path.setProperty("muted", True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path.setWordWrap(True)
        lay.addWidget(path)
        lay.addWidget(_buttons(self, "Save"))

    def values(self) -> dict:
        return {
            "title": self.title.text().strip() or "My Desktop",
            "edge": self.edge.currentData(),
            "screen": self.screen.currentData(),
            "panel_width": self.width.value(),
            "theme": self.theme.currentData(),
            "open_mode": self.open_mode.currentData(),
            "open_delay_ms": self.delay.value(),
            "strip_length": self.strip_length.value() / 100,
            "focus_minutes": self.focus.value(),
            "always_on_top": self.on_top.isChecked(),
            "hotkey_enabled": self.hotkey.isChecked(),
            "follow_window": self.follow.isChecked(),
            "suggest_meeting_notes": self.meetings.isChecked(),
        }

    def startup_enabled(self) -> bool:
        return self.startup.isChecked()
