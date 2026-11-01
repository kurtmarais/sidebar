"""End-to-end: desktop sync engine <-> stand-in Supabase <-> phone app in headless Chromium."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sidebar_app import sync
from sidebar_app.storage import Database

from .fake_supabase import FakeSupabase

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).with_name("phone_e2e.js")


def node_playwright() -> bool:
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "require('playwright')"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not node_playwright(), reason="needs node + playwright")


def phone(phase, profile, server):
    proc = subprocess.run(["node", str(SCRIPT), phase, str(profile), server.url, server.key],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["errors"] == [], out["errors"]
    return out


def by_title(db, title):
    return next((c for c in db.cards() if c.title == title), None)


def test_phone_and_desktop_round_trip(tmp_path):
    with FakeSupabase(static_dir=ROOT / "web") as server:
        pc = Database(tmp_path / "pc.db")
        sync.sign_in(pc, server.url, server.key, "me@example.com", "correct horse")
        work = pc.boards()[0].id
        pc.add_card(work, "checklist", title="Groceries",
                    content={"items": [{"text": "eggs", "done": False}, {"text": "bread", "done": False}]})
        pc.add_card(work, "note", title="<img src=x onerror=alert(1)>", content={"text": "markup test"})
        pc.add_card(work, "secret", title="Bank", content={"cipher": "x"})
        sync.sync_now(pc)

        profile = tmp_path / "phone-profile"
        a = phone("a", profile, server)
        assert a["badLogin"] and a["markupSafe"]
        assert "Groceries" in a["titles"] and "Bank" not in a["titles"]      # secret never reaches the phone
        assert {"All", "Work", "Research", "Personal"} <= set(a["chips"])

        sync.sync_now(pc)
        g = by_title(pc, "Groceries")
        assert g.content["items"][0] == {"text": "eggs", "done": True}       # ticked on the phone
        new = by_title(pc, "From my phone")
        assert new is not None and new.content["text"] == "typed on the bus"
        assert new.board_id == work

        pc.update_card(g.id, title="Edited on PC")
        sync.sync_now(pc)
        b = phone("b", profile, server)                                       # same phone, signed in already
        assert "Edited on PC" in b["titles"] and b["archive"] == ["Edited on PC"]

        sync.sync_now(pc)
        assert by_title(pc, "From my phone") is None                          # deleted on the phone
        assert pc.card(g.id).archived                                         # archived on the phone


GESTURES = Path(__file__).with_name("phone_gestures.js")


@pytest.mark.parametrize("no_star_column", [False, True])
def test_phone_gestures_and_editor(tmp_path, no_star_column):
    import os
    with FakeSupabase(static_dir=ROOT / "web") as server:
        server.no_star_column = no_star_column
        pc = Database(tmp_path / "pc.db")
        sync.sign_in(pc, server.url, server.key, "me@example.com", "correct horse")
        for c in pc.cards():
            pc.delete_card(c.id)
        work = pc.boards()[0].id
        for title in ("Delta", "Gamma", "Beta", "Alpha"):          # newest first: Alpha on top
            pc.add_card(work, "note", title=title, content={"text": f"{title} text"})
        a = by_title(pc, "Alpha")
        pc.update_card(a.id, priority=3)
        sync.sync_now(pc)

        proc = subprocess.run(["node", str(GESTURES), str(tmp_path / "profile"), server.url, server.key,
                               os.environ.get("SHOT_DIR", "")], capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stderr[-2000:]
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["errors"] == [], out["errors"]
        assert out["start"] == ["Alpha", "Beta", "Gamma", "Delta"]
        assert out["tagsInline"] and not out["deleteInList"]
        assert out["partialRight"] is True and "Alpha" not in out["afterFullRight"]
        assert out["alphaArchived"] is True                      # full right swipe archived
        assert out["betaAfterPartial"] is False                  # partial left swipe did nothing
        assert out["betaStarred"] is True and out["starVisible"] == 1
        assert out["reorder"]["after"] == ["Delta", "Beta", "Gamma"], out["reorder"]
        assert out["afterScroll"] == out["reorder"]["after"]     # vertical move changed nothing
        assert out["backLeft"] and out["doneRight"] and out["markDoneGone"] and out["deleteAtBottom"]
        assert not out["datesOverlap"] and out["datesFit"]
        assert out["emptyDropped"] and out["newKept"] and out["deleted"]
        assert out["queueLeft"] == 0 and out["noStarColumn"] is no_star_column

        sync.sync_now(pc)
        assert pc.card(a.id).archived
        assert by_title(pc, "Beta").starred is (not no_star_column)
        titles = [c.title for c in pc.cards() if not c.archived]
        assert titles == ["Delta", "Beta", "Gamma"]               # order from the phone
        assert by_title(pc, "Packing") is None
