"""Launcher used by PyInstaller and the Windows startup entry."""

import sys

from sidebar_app.app import main

if __name__ == "__main__":
    sys.exit(main())
