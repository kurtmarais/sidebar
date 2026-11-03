"""Automatic rolling snapshots, JSON backup/restore and Markdown export."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from pathlib import Path

from .storage import PRIORITY_LABELS, Database

KEEP_SNAPSHOTS = 14
SNAPSHOT_INTERVAL_S = 24 * 60 * 60


def snapshot(db: Database, backup_dir: Path, tag: str = "auto") -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"sidebar-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    db.backup_to(path)
    return path


def auto_backup(db: Database, backup_dir: Path) -> Path | None:
    """Snapshot the database if the newest automatic snapshot is over a day old."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(backup_dir.glob("sidebar-auto-*.db"), key=lambda p: p.stat().st_mtime)
    if existing and time.time() - existing[-1].stat().st_mtime < SNAPSHOT_INTERVAL_S:
        return None
    path = snapshot(db, backup_dir, "auto")
    existing.append(path)
    for old in existing[:-KEEP_SNAPSHOTS]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def export_json(db: Database, path: Path, assets_dir: Path, include_secrets: bool = False) -> None:
    """Write all notes, including archived ones and image data, to one file."""
    data = db.export_data(include_secrets=include_secrets)
    blobs = {}
    for card in data["cards"]:
        asset = (card.get("content") or {}).get("asset")
        asset = Path(asset).name if asset else asset            # never read outside assets/
        if asset and asset not in blobs and (assets_dir / asset).is_file():
            blobs[asset] = base64.b64encode((assets_dir / asset).read_bytes()).decode("ascii")
    data["assets"] = blobs
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def import_json(db: Database, path: Path, assets_dir: Path, backup_dir: Path) -> int:
    """Merge a backup into the current data. Snapshots current data first."""
    data = json.loads(path.read_text(encoding="utf-8"))
    snapshot(db, backup_dir, "before-restore")
    assets_dir.mkdir(parents=True, exist_ok=True)
    for name, blob in (data.get("assets") or {}).items():
        target = assets_dir / Path(name).name
        if not target.exists():
            target.write_bytes(base64.b64decode(blob))
    return db.import_data(data)


def export_markdown(db: Database, path: Path) -> None:
    """Readable export of every non-secret note. Images and files are referenced by name."""
    boards = {b.id: b.name for b in db.boards()}
    lines = ["# Sidebar notes", "", f"Exported {datetime.now():%d %B %Y %H:%M}", ""]
    for archived in (False, True):
        cards = [c for c in db.cards() if c.archived == archived and c.type != "secret"]
        if not cards:
            continue
        lines += ["## Archive" if archived else "## Notes", ""]
        for c in cards:
            lines.append(f"### {c.title or 'Untitled'}")
            meta = [boards.get(c.board_id, "")]
            if c.priority:
                meta.append(f"Priority: {PRIORITY_LABELS[c.priority]}")
            if c.due_date:
                meta.append(f"Due: {c.due_date}" + (f" {c.due_time}" if c.due_time else ""))
            lines += [f"*{' · '.join(m for m in meta if m)}*", ""]
            body = c.content or {}
            if c.type == "note":
                lines.append(body.get("text", ""))
            elif c.type == "checklist":
                lines += [f"- [{'x' if i.get('done') else ' '}] {i.get('text', '')}"
                          for i in body.get("items", [])]
            elif c.type == "code":
                lines += [f"```{body.get('language', '')}", body.get("text", ""), "```"]
            elif c.type == "links":
                lines += [f"- [{l.get('label') or l.get('url')}]({l.get('url', '')})"
                          for l in body.get("links", [])]
            elif c.type in ("image", "file"):
                lines.append(f"[{c.type}: {body.get('name', '')}]")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
