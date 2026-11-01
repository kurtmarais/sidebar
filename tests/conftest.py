import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Test containers run as root, where Chromium (Qt WebEngine) refuses its sandbox.
# Test-only: the app itself never sets this.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

QGuiApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
