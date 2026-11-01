"""Runs phone sync in the background of the sidebar window."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from . import dialogs, sync
from .storage import Database

INTERVAL_MS = 30_000        # regular check for changes from the phone
AFTER_EDIT_MS = 4_000       # upload soon after a local edit


class SyncController(QObject):
    """Schedules sync cycles: network work on a worker thread, database work here."""

    applied = Signal(set, bool)        # changed local card ids, categories changed
    status_changed = Signal(str, str)  # short text, detail / error

    def __init__(self, db: Database, parent: QObject | None = None):
        super().__init__(parent)
        self.db = db
        self.access: dict = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")
        self.future: Future | None = None
        self.again = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_soon)
        self.timer.start(INTERVAL_MS)
        self.debounce = QTimer(self, singleShot=True)
        self.debounce.timeout.connect(self.run)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self._check)
        self.last_error = ""

    def enabled(self) -> bool:
        return sync.is_configured(self.db.settings())

    def local_change(self) -> None:
        if self.enabled():
            self.debounce.start(AFTER_EDIT_MS)

    def sync_soon(self) -> None:
        if self.enabled() and not self.debounce.isActive():
            self.debounce.start(300)

    def run(self) -> None:
        if not self.enabled():
            self.status_changed.emit("", "")
            return
        if self.future is not None:
            self.again = True
            return
        payload = sync.prepare(self.db, self.db.settings(), self.access)
        self.future = self.pool.submit(sync.transfer, payload)
        self.poll.start(150)

    def _check(self) -> None:
        if self.future is None or not self.future.done():
            return
        self.poll.stop()
        fut, self.future = self.future, None
        try:
            res = fut.result()
        except sync.AuthError as exc:
            self.access.clear()
            self.last_error = f"Sign in again: {exc}"
            self.status_changed.emit("Phone sync: sign in again", self.last_error)
            return
        except Exception as exc:          # network, server: retry on the next tick
            self.last_error = str(exc)
            self.status_changed.emit("Phone sync paused", self.last_error)
            return
        if res.session:
            self.access.update(res.session)
        changed, boards_changed = sync.apply(self.db, res)
        self.last_error = ""
        self.status_changed.emit(f"Synced {datetime.now():%H:%M}", "Phone sync is on")
        if changed or boards_changed:
            self.applied.emit(changed, boards_changed)
        if self.again:
            self.again = False
            self.sync_soon()

    def shutdown(self) -> None:
        """Last upload on quit, bounded so closing never hangs."""
        self.timer.stop()
        self.debounce.stop()
        self.poll.stop()
        if self.enabled():
            try:
                if self.future is not None:
                    self.future.result(timeout=8)
                    self.future = None
                res = self.pool.submit(sync.transfer, sync.prepare(self.db, self.db.settings(), self.access)
                                       ).result(timeout=8)
                sync.apply(self.db, res)
            except Exception:
                pass
        self.pool.shutdown(wait=False, cancel_futures=True)


class SyncDialog(QDialog):
    """Sign in to Supabase and see sync status."""

    def __init__(self, parent: QWidget | None, db: Database, controller: SyncController,
                 on_signed_in: Callable[[], None]):
        super().__init__(parent)
        self.db, self.controller, self.on_signed_in = db, controller, on_signed_in
        self.setWindowTitle("Phone sync")
        self.setMinimumWidth(430)
        s = db.settings()
        lay = QVBoxLayout(self)
        intro = QLabel("Sync notes, checklists, code and links with the phone app through your "
                       "Supabase project. Images, files and secret notes stay on this PC.")
        intro.setWordWrap(True)
        intro.setProperty("muted", True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.url = QLineEdit(s.get("sync_url", ""))
        self.url.setPlaceholderText("https://xxxx.supabase.co")
        self.key = QLineEdit(s.get("sync_key", ""))
        self.key.setPlaceholderText("Publishable (anon) key")
        self.email = QLineEdit(s.get("sync_email", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Only used to sign in; not stored")
        form.addRow("Project URL", self.url)
        form.addRow("Publishable key", self.key)
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        lay.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.sign_in_btn = QPushButton("Sign in and sync")
        self.sign_in_btn.setProperty("primary", True)
        self.sign_in_btn.clicked.connect(self._sign_in)
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.clicked.connect(self._sync_now)
        self.sign_out_btn = QPushButton("Turn off")
        self.sign_out_btn.clicked.connect(self._sign_out)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (self.sign_in_btn, self.sync_btn, self.sign_out_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.password.returnPressed.connect(self._sign_in)
        self._refresh()

    def _refresh(self) -> None:
        s = self.db.settings()
        on = sync.is_configured(s)
        self.sync_btn.setEnabled(on)
        self.sign_out_btn.setEnabled(on)
        if self.controller.last_error and on:
            self.status.setText(f"Problem: {self.controller.last_error}")
        elif on:
            last = s.get("sync_last")
            when = datetime.fromtimestamp(last).strftime("%d %b %H:%M") if last else "not yet"
            self.status.setText(f"On, signed in as {s.get('sync_email')}. Last sync: {when}.")
        else:
            self.status.setText("Off.")

    def _busy(self, on: bool) -> None:
        for b in (self.sign_in_btn, self.sync_btn, self.sign_out_btn):
            b.setEnabled(not on)
        if on:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _sign_in(self) -> None:
        url, key, email = self.url.text().strip(), self.key.text().strip(), self.email.text().strip()
        if not (url.startswith("https://") and key and email and self.password.text()):
            self.status.setText("Fill in the project URL (https://…), publishable key, email and password.")
            return
        self._busy(True)
        try:
            session = sync.sign_in(self.db, url, key, email, self.password.text())
            self.controller.access = dict(session)
            self.password.clear()
            sync.sync_now(self.db, access=self.controller.access)
            self.controller.last_error = ""
        except sync.SyncError as exc:
            self.status.setText(f"Could not sign in: {exc}")
            return
        finally:
            self._busy(False)
        self.on_signed_in()
        self._refresh()

    def _sync_now(self) -> None:
        self._busy(True)
        try:
            sync.sync_now(self.db, access=self.controller.access)
            self.controller.last_error = ""
        except sync.SyncError as exc:
            self.controller.last_error = str(exc)
        finally:
            self._busy(False)
        self.on_signed_in()
        self._refresh()

    def _sign_out(self) -> None:
        if dialogs.message(self, "Phone sync", "Turn off phone sync on this PC? Your notes stay here and in "
                           "Supabase.", "question", ("Turn off", "Cancel")) != "Turn off":
            return
        sync.sign_out(self.db)
        self.controller.access.clear()
        self.controller.status_changed.emit("", "")
        self._refresh()


def last_sync_text(db: Database) -> str:
    last = db.get_setting("sync_last")
    if not last:
        return ""
    return f"Synced {datetime.fromtimestamp(last):%H:%M}" if time.time() - last < 86400 else "Synced"
