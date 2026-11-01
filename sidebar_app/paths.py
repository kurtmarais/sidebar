"""Location of the local data folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Sidebar"


def default_data_dir() -> Path:
    """%APPDATA%\\Sidebar on Windows, a sensible equivalent elsewhere.

    ``SIDEBAR_DATA_DIR`` overrides the location (useful for testing or a
    portable install on a USB stick / synced folder).
    """
    override = os.environ.get("SIDEBAR_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_DIR_NAME.lower()
