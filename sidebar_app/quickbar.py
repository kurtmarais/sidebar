"""Quick tiles at the top of the panel: a focus sound and an app launcher."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QProcess, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import dialogs, winplatform
from .theme import Palette, blend, make_icon

DEFAULT_SOUND_URL = "https://www.youtube.com/watch?v=co7KgV2edvI"
DEFAULT_SOUND_NAME = "Airplane cabin"
DEFAULT_APP_NAME = "caffeine64.exe"

_webengine = None   # loaded on first play: it is a very large library and slows start-up


def webengine():
    """The Qt WebEngine classes, imported on first use; None if not installed."""
    global _webengine
    if _webengine is None:
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
            _webengine = (QWebEngineView, QWebEnginePage, QWebEngineProfile, QWebEngineSettings)
        except ImportError:  # pragma: no cover - depends on the installed wheels
            _webengine = False
    return _webengine or None


def have_webengine() -> bool:
    from importlib.util import find_spec
    return find_spec("PySide6.QtWebEngineWidgets") is not None


def youtube_id(url: str) -> str | None:
    """Video id from a watch, youtu.be, shorts or embed link (or a bare id)."""
    url = (url or "").strip()
    for pattern in (r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})",
                    r"/(?:embed|shorts|live)/([\w-]{11})", r"^([\w-]{11})$"):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def embed_html(video_id: str) -> str:
    src = (f"https://www.youtube.com/embed/{video_id}?autoplay=1&loop=1&playlist={video_id}"
           f"&controls=0&rel=0&playsinline=1")
    return (
        "<!doctype html><html><body style='margin:0;background:#000'>"
        f"<iframe width='320' height='180' src='{src}' frameborder='0' "
        "allow='autoplay; encrypted-media' referrerpolicy='strict-origin-when-cross-origin'>"
        "</iframe></body></html>"
    )


def find_app(name: str) -> str | None:
    """Look for an executable on PATH and in the usual places."""
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    stem = Path(name).stem.rstrip("0123456789")       # caffeine64 -> caffeine
    roots = [home / "Downloads", home / "Desktop", home / "Documents", home / "OneDrive" / "Desktop",
             home / "Apps", home / "Tools"]
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]))
    for root in roots:
        for candidate in (root / name, root / stem / name, root / stem.capitalize() / name):
            if candidate.is_file():
                return str(candidate)
    for root in roots[:3]:   # one level of sub-folders in Downloads, Desktop, Documents
        try:
            for sub in root.iterdir():
                if sub.is_dir() and (sub / name).is_file():
                    return str(sub / name)
        except OSError:
            continue
    return None


class ElidedLabel(QLabel):
    """Label that shortens its text with … instead of clipping it."""

    def __init__(self, text: str = ""):
        super().__init__()
        self._full = text
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        super().setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(10, self.width())))
        self.setToolTip(self._full if self.fontMetrics().horizontalAdvance(self._full) > self.width() else "")


class Tile(QFrame):
    """Rounded tile: icon, title, status line and an action button."""

    def __init__(self, p: Palette, icon: str, title: str):
        super().__init__()
        self.setObjectName("tile")
        self.p = p
        self.icon_name = icon
        self.setCursor(Qt.PointingHandCursor)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 2, 5)
        lay.setSpacing(5)
        self.icon = QLabel()
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = ElidedLabel(title)
        self.title.setStyleSheet("font-weight: 600; font-size: 9.5pt; background: transparent;")
        self.status = ElidedLabel()
        self.status.setStyleSheet("background: transparent;")
        text.addWidget(self.title)
        text.addWidget(self.status)
        self.button = QToolButton()
        self.button.setIconSize(QSize(16, 16))
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.icon)
        lay.addLayout(text, 1)
        lay.addWidget(self.button)
        self.active = False

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.button.click()
        super().mouseReleaseEvent(e)

    def set_state(self, active: bool, status: str, action_icon: str, tooltip: str) -> None:
        self.active = active
        p = self.p
        col = p.accent if active else p.muted
        self.icon.setPixmap(make_icon(self.icon_name, col, 18).pixmap(18, 18))
        self.status.setText(status)
        self.status.setStyleSheet(f"color: {p.accent if active else p.muted}; font-size: 8.5pt;"
                                  " background: transparent;")
        self.button.setIcon(make_icon(action_icon, p.text if active else p.muted, 16))
        self.button.setToolTip(tooltip)
        self.setToolTip(tooltip + "\nRight-click for options")
        border = p.accent if active else p.panel_border
        bg = blend(p.surface, p.accent, 0.08) if active else p.surface
        self.setStyleSheet(f"#tile {{ background: {bg}; border: 1px solid {border}; border-radius: 12px; }}")

    def apply_theme(self, p: Palette) -> None:
        self.p = p


class FocusSoundTile(Tile):
    """Plays a YouTube video's sound inside the app; keeps playing while the sidebar is hidden."""

    def __init__(self, p: Palette, get: Callable[[str], object], put: Callable[[str, object], None]):
        super().__init__(p, "headphones", "Focus sound")
        self._get, self._put = get, put
        self.view = None
        self.playing = False
        self.button.clicked.connect(self.toggle)
        self.customContextMenuRequested.connect(self._menu)
        self._render()

    def url(self) -> str:
        return str(self._get("focus_sound_url") or DEFAULT_SOUND_URL)

    def name(self) -> str:
        return str(self._get("focus_sound_name") or DEFAULT_SOUND_NAME)

    def _render(self) -> None:
        self.title.setText(self.name())
        if self.playing:
            self.set_state(True, "Playing", "stop", f"Stop {self.name()}")
        else:
            self.set_state(False, "Focus sound", "play", f"Play {self.name()}")

    def apply_theme(self, p: Palette) -> None:
        super().apply_theme(p)
        self._render()

    def toggle(self) -> None:
        self.stop() if self.playing else self.play()

    def play(self) -> None:
        vid = youtube_id(self.url())
        if not vid:
            self.status.setText("Invalid YouTube link")
            return
        engine = None if self._get("focus_sound_browser") else webengine()
        if engine is None:
            self.open_in_browser()
            return
        if self.view is None:
            QWebEngineView, QWebEnginePage, QWebEngineProfile, QWebEngineSettings = engine
            self.view = QWebEngineView()
            # Off-the-record profile: YouTube cookies and history are never written to disk.
            self._profile = QWebEngineProfile(self.view)
            self.view.setPage(QWebEnginePage(self._profile, self.view))
            self.view.setAttribute(Qt.WA_DontShowOnScreen, True)
            self.view.resize(320, 180)
            s = self.view.settings()
            s.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
            self.view.show()   # never visible on screen, but a live page so audio plays
        # An https base URL gives the embed a referrer, which YouTube requires.
        self.view.setHtml(embed_html(vid), QUrl("https://sidebar.localhost/"))
        self.playing = True
        self._render()

    def stop(self) -> None:
        if self.view is not None:
            self.view.setHtml("<html></html>")
        self.playing = False
        self._render()

    def open_in_browser(self) -> None:
        QDesktopServices.openUrl(QUrl(self.url()))

    def _menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction("Stop" if self.playing else "Play", self.toggle)
        m.addAction("Open in browser now", self.open_in_browser)
        always = m.addAction("Always open in browser")
        always.setCheckable(True)
        always.setChecked(bool(self._get("focus_sound_browser")) or not have_webengine())
        always.setEnabled(have_webengine())
        always.toggled.connect(lambda on: (self.stop(), self._put("focus_sound_browser", on)))
        m.addSeparator()
        m.addAction("Change video…", self.change_video)
        m.exec(self.mapToGlobal(pos))

    def change_video(self) -> None:
        url = dialogs.ask_text(self.window(), "Focus sound", "YouTube link:", self.url())
        if not url:
            return
        if not youtube_id(url):
            self.status.setText("Invalid YouTube link")
            return
        name = dialogs.ask_text(self.window(), "Focus sound", "Short name for the tile:", self.name())
        self._put("focus_sound_url", url)
        if name:
            self._put("focus_sound_name", name)
        if self.playing:
            self.play()
        self._render()

    def shutdown(self) -> None:
        if self.view is not None:
            self.view.setHtml("<html></html>")
            self.view.deleteLater()
            self.view = None


class LaunchAppTile(Tile):
    """Starts a program (Caffeine by default); click again to close it."""

    def __init__(self, p: Palette, get: Callable[[str], object], put: Callable[[str, object], None]):
        super().__init__(p, "power", "Caffeine")
        self._get, self._put = get, put
        self.running = False
        self.button.clicked.connect(self.toggle)
        self.customContextMenuRequested.connect(self._menu)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh)
        self.poll.start(5000)
        self.refresh(force=True)

    def path(self) -> str:
        saved = str(self._get("launch_app_path") or "")
        if saved and Path(saved).is_file():
            return saved
        found = find_app(DEFAULT_APP_NAME) if not saved else None
        if found:
            self._put("launch_app_path", found)
            return found
        return saved

    def exe_name(self) -> str:
        p = self.path()
        return Path(p).name if p else DEFAULT_APP_NAME

    def label(self) -> str:
        return str(self._get("launch_app_name") or Path(self.exe_name()).stem.rstrip("0123456789").capitalize())

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        if not force and not self.isVisible():
            return   # only poll while the panel is out
        self.title.setText(self.label())
        self.running = winplatform.process_running(self.exe_name())
        self._render()

    def _render(self) -> None:
        if self.running:
            self.set_state(True, "On", "stop", f"Close {self.label()}")
        elif self.path():
            self.set_state(False, "Off", "play", f"Start {self.label()}")
        else:
            self.set_state(False, "Not found", "play",
                           f"Choose where {DEFAULT_APP_NAME} is on this PC")

    def apply_theme(self, p: Palette) -> None:
        super().apply_theme(p)
        self._render()

    def toggle(self) -> None:
        if self.running:
            winplatform.kill_process(self.exe_name())
        else:
            path = self.path() or self.choose()
            if not path:
                return
            if not QProcess.startDetached(path, [], str(Path(path).parent)):
                self.status.setText("Could not start the program")
                return
        QTimer.singleShot(1000, lambda: self.refresh(force=True))
        self.running = not self.running
        self._render()

    def choose(self) -> str | None:
        start = str(Path(self.path()).parent) if self.path() else str(Path.home() / "Downloads")
        path, _ = QFileDialog.getOpenFileName(self.window(), "Choose the program to start", start,
                                              "Programs (*.exe);;All files (*)")
        if path:
            self._put("launch_app_path", path)
            self.refresh(force=True)
        return path or None

    def _menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction(f"Close {self.label()}" if self.running else f"Start {self.label()}", self.toggle)
        if self.path():
            m.addAction("Show in folder",
                        lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.path()).parent))))
        m.addSeparator()
        m.addAction("Choose a different program…", self.choose)
        m.addAction("Rename tile…", self._rename)
        m.exec(self.mapToGlobal(pos))

    def _rename(self) -> None:
        name = dialogs.ask_text(self.window(), "Rename tile", "Tile name:", self.label())
        if name:
            self._put("launch_app_name", name)
            self.refresh(force=True)


class QuickBar(QWidget):
    """The row of tiles under the header."""

    changed = Signal()

    def __init__(self, p: Palette, get: Callable[[str], object], put: Callable[[str, object], None]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.sound = FocusSoundTile(p, get, put)
        self.app = LaunchAppTile(p, get, put)
        lay.addWidget(self.sound, 1)
        lay.addWidget(self.app, 1)

    def apply_theme(self, p: Palette) -> None:
        self.sound.apply_theme(p)
        self.app.apply_theme(p)

    def shutdown(self) -> None:
        self.sound.shutdown()
