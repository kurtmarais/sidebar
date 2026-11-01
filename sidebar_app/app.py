"""Application entry point: single instance, tray icon, startup."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import __version__, backup, updater, winplatform
from .paths import default_data_dir
from .storage import Database
from .theme import app_icon
from .window import SidebarWindow

SERVER_NAME = f"sidebar-notes-{getpass.getuser()}"


def _signal_running_instance() -> bool:
    """Ask an already running Sidebar to slide out. True if one answered."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def _pick_font() -> QFont:
    families = set(QFontDatabase.families())
    for name in ("Inter", "Segoe UI Variable Text", "Segoe UI"):
        if name in families:
            f = QFont(name)
            f.setPointSizeF(9.5)
            return f
    return QApplication.font()


def build_tray(app: QApplication, win: SidebarWindow) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    tray = QSystemTrayIcon(app_icon(), app)
    tray.setToolTip("Sidebar  ·  Ctrl+Shift+S")
    menu = QMenu()
    menu.addAction("Open sidebar", lambda: win.expand(activate=True))
    menu.addAction("New note", lambda: win.new_card("note"))
    menu.addAction("Settings…", win.open_settings)
    if updater.repo_root() is not None:
        menu.addAction("Update and restart", win.update_and_restart)
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: win.toggle() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    tray._menu = menu  # keep a reference
    return tray


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sidebar", description="Edge-docked sticky-notes sidebar")
    parser.add_argument("--data-dir", type=Path, help="folder for the notes database and backups")
    parser.add_argument("--version", action="version", version=__version__)
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    QGuiApplication.setAttribute(Qt.AA_ShareOpenGLContexts)   # required by Qt WebEngine
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Sidebar")
    app.setOrganizationName("Sidebar")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    if _signal_running_instance():
        return 0
    server = QLocalServer(app)
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)

    app.setFont(_pick_font())
    try:
        winplatform.refresh_startup_entry()   # keep "Start with Windows" pointing at this copy
    except OSError:
        pass
    data_dir = args.data_dir or default_data_dir()
    db = Database(data_dir / "sidebar.db")
    try:
        backup.auto_backup(db, data_dir / "backups")
    except OSError:
        pass

    win = SidebarWindow(db, data_dir)
    win.tray = build_tray(app, win)

    def on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: (conn.readAll(), win.expand(activate=True)))

    server.newConnection.connect(on_connection)

    def on_quit() -> None:
        win.shutdown()
        restart = win.restart_after_update
        auto = bool(win.s.get("auto_update", True))
        db.close()
        if restart or auto:
            updater.spawn_update(data_dir / "update.log", restart=restart)

    app.aboutToQuit.connect(on_quit)
    win.show()
    win.check_reminders()   # reminders that came due while the app was closed
    return app.exec()


if __name__ == "__main__":
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    sys.exit(main())
