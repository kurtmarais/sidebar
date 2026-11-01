"""Launcher used by the desktop shortcut, the Windows startup entry and PyInstaller.

The app runs without a console (pythonw), so a crash while starting up would
otherwise vanish without a trace: record it and show a message instead.
"""

import os
import sys
import time
import traceback
from pathlib import Path


def _crash_report() -> None:
    text = traceback.format_exc()
    base = os.environ.get("SIDEBAR_DATA_DIR") or os.path.join(
        os.environ.get("APPDATA") or str(Path.home()), "Sidebar")
    log = Path(base) / "startup-crash.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} argv={sys.argv} exe={sys.executable}\n{text}\n")
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"Sidebar could not start.\n\nDetails were saved to:\n{log}\n\n{text[-700:]}", "Sidebar", 0x10)


if __name__ == "__main__":
    try:
        from sidebar_app.app import main
        code = main()
    except SystemExit:
        raise
    except BaseException:
        _crash_report()
        code = 1
    sys.exit(code)
