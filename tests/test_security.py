"""Checks for the security fixes: unsafe link schemes, asset paths, passphrases."""

import json

import pytest
from PySide6.QtWidgets import QApplication

from sidebar_app import backup, secrets_store
from sidebar_app.cards import safe_link
from sidebar_app.storage import Database


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("text", ["https://example.com", "example.com/page", "http://x.y", "mailto:me@x.com"])
def test_web_links_open(app, text):
    assert safe_link(text) is not None


@pytest.mark.parametrize("text", [
    "file:///C:/Windows/System32/calc.exe",
    "ms-msdt:/id PCWDiagnostic",            # protocol-handler abuse (CVE-2022-30190 style)
    "javascript:alert(1)",
    "search-ms:query=x&crumb=location:\\\\evil\\share",
    "C:\\Windows\\System32\\cmd.exe",
    "",
])
def test_dangerous_links_do_not_open(app, text):
    assert safe_link(text) is None


def test_backup_cannot_reach_outside_assets(tmp_path):
    """A crafted backup whose image points outside the assets folder must not
    leak that file into an export, nor let 'delete card' remove it."""
    secret_file = tmp_path / "private.txt"
    secret_file.write_text("do not export me")
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    db = Database(tmp_path / "data" / "s.db")
    db.add_card(db.boards()[0].id, "image", title="evil", content={"asset": "../../private.txt"})
    out = tmp_path / "export.json"
    backup.export_json(db, out, assets)
    assert "private" not in json.dumps(json.loads(out.read_text())["assets"])


def test_window_delete_stays_inside_assets(app, tmp_path):
    from sidebar_app import dialogs
    from sidebar_app.window import SidebarWindow
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    db = Database(tmp_path / "data" / "s.db")
    win = SidebarWindow(db, tmp_path / "data")
    w = win.add_card("image", focus=False, title="evil", content={"asset": "../../victim.txt"})
    orig = dialogs.message
    dialogs.message = lambda *a, **k: "Delete"
    try:
        win.delete_card(w.card.id)
    finally:
        dialogs.message = orig
    assert victim.exists()
    win.close()


def test_passphrases_allowed_and_slower_to_guess():
    fake = lambda d, k: bytes(b ^ k[i % len(k)] for i, b in enumerate(d))  # noqa: E731
    c = secrets_store.create("correct horse battery", "x", fake)
    assert c["iter"] >= 600_000
    assert secrets_store.reveal(c, "correct horse battery", fake)[0] == "x"
    assert not secrets_store.valid_pin("123")
