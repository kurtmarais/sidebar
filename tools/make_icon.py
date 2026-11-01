"""Render the app icon to sidebar.ico for the PyInstaller build."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from sidebar_app.theme import app_icon  # noqa: E402

app = QGuiApplication([])
out = Path(sys.argv[1] if len(sys.argv) > 1 else "sidebar.ico")
if not app_icon().pixmap(256, 256).save(str(out), "ICO"):
    sys.exit("Could not write icon")
print(f"Wrote {out}")
