# Sidebar

A sticky-notes sidebar for the Windows desktop. It hides as a thin blue strip on the edge of the screen and slides out the moment you need it. Notes are separate cards, not one long list, and everything is saved locally as you type, so nothing is lost when the PC is switched off.

The design and feature set follow [Notiq](https://notiq-app.com/), without its AI, voice and cloud-calendar features. There is no account, no cloud and no network access.

## Features

**Opening and closing**
- Rest the cursor on the blue strip for 0.3 s (adjustable up to 3 s) and the panel slides out. You can also click the strip, press **Ctrl+Shift+S** from any app, or click the tray icon.
- The panel slides away when the cursor leaves. It stays open while you are typing in a card, while a reminder is waiting, or while it is **pinned** (pin icon in the header).
- Drag the strip up or down to move it. Drag the panel header to the other side of the screen, or to another monitor, and it snaps there.
- The strip covers only part of the edge, so it never catches the cursor in screen corners or on window close buttons.

**Card types** (**+ New**)

| Type | What it holds |
| --- | --- |
| Note | Free text that grows with its content |
| Checklist | Items with wrapping text. **Enter** adds an item, **Backspace** on an empty item removes it, **Alt+↑/↓** reorders |
| Code | Monospace text with syntax highlighting (Python, JavaScript, SQL, JSON) and a copy button |
| Links | Labelled URLs that open in your browser |
| Image | Paste a screenshot with **Ctrl+V** or drag in an image file |
| File | Drag in a PDF, Word, Excel or other document you open often; the card opens the original |
| Secret | A 4-digit PIN protects the text, which is encrypted with Windows protected storage. The PIN is never stored. Five wrong PINs force a 30-second wait, and secret cards re-lock whenever the sidebar hides |

Dropping `.py`, `.js`, `.sql`, `.json` or `.txt` files creates a Code card.

**Organising**
- Categories (Work, Research, Personal, plus your own) appear as tabs, with **All** first. Drop a card on a tab to move it there.
- Drag a card's grip to reorder it. Collapse a card to a one-line summary.
- Card menu (**⋯**) options: colour, priority, due date, reminder, category, card type, link to window, copy text, mark done, delete.
- **Due dates** show Today, Tomorrow or Overdue badges.
- **Reminders** slide the sidebar out with **Got it!**, **Snooze** (5 min, 15 min, 1 hour, tomorrow 09:00) and **Focus**. An unanswered reminder keeps the sidebar open, comes back about a minute after you hide it, and survives restarts.
- **Today** view: notes that are overdue, or due or reminding you today or tomorrow.
- **Archive** view: marking a card done (**✓** on hover) archives it, and **Restore** brings it back.
- **Ctrl+F** searches titles, notes, checklist items, code, links and file names. Secret notes are searched by title only.
- **Focus session**: a timer from the settings menu or a reminder (25 min by default).

**Notes that follow your window** (Windows)
- Link a card to an app (for example `excel.exe`), or to windows whose title contains a word. While that window is in front, the card appears at the top under *For this window*.
- **Link notes** next to the *In front:* line lets you click the notes to link to the current app. The card menu has finer options.
- When a Teams, Zoom, Google Meet, Webex or Slack huddle window is detected, the sidebar offers **Start meeting notes**. This creates a note named after the meeting, and the note is shown first the next time that meeting comes round.
- Only the title and program name of the front window are read. Nothing is stored or sent. Both features can be turned off in Settings.

**Data and backups**
- Notes are stored in `%APPDATA%\Sidebar\sidebar.db` (SQLite), with images in `assets\`. Every edit is committed within half a second.
- A rolling daily snapshot is kept in `backups\` (the last 14).
- **Export backup** writes one JSON file containing all notes, including archived ones and images. Secret notes are included only if you choose to include them.
- **Restore backup** adds missing notes and never overwrites or deletes existing ones, so it is safe to run more than once. A snapshot is taken first.
- **Export as Markdown** gives a readable copy of all non-secret notes.

**Settings** (gear icon): side, monitor, panel width, dark or light theme, open by hover and/or click, open delay, strip length, focus length, keep on top, Ctrl+Shift+S, notes that follow your window, meeting notes, and **Start with Windows**. Start with Windows is also what keeps reminders working, since the app must be running to alert you.

## Keyboard

| Keys | Action |
| --- | --- |
| Ctrl+Shift+S | Open or close the sidebar (any app) |
| Ctrl+F | Search |
| Ctrl+N | New note |
| Ctrl+V | Paste an image, files or text as a new card (when not typing in a card) |
| Esc | Leave link mode, clear search, or hide the sidebar |

## Run from source

Requires Python 3.10+ on Windows 10 or 11.

```powershell
py -m pip install -r requirements.txt
py run_sidebar.py
```

Use `py` (the launcher installed with Python from python.org) rather than `python`: on some PCs `python` points at a copy bundled with another app, such as Inkscape, that has no pip.

`--data-dir <folder>` or the `SIDEBAR_DATA_DIR` environment variable stores notes somewhere else, for example a synced folder.

## Build a standalone .exe

```powershell
.\build.ps1
```

This creates a virtual environment, installs PySide6 and PyInstaller, and produces `dist\Sidebar.exe`. Copy the exe anywhere and run it, then turn on **Start with Windows** in Settings.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

The GUI tests run headless (`QT_QPA_PLATFORM=offscreen` is set automatically).

## Project layout

```
sidebar_app/
  app.py           entry point, tray icon, single-instance handling
  window.py        edge strip, slide animation, panel, views, reminders, window following
  cardlist.py      card list with sections and drag-and-drop, category tabs
  cards.py         card frame and the card types (add new types to CARD_KINDS)
  storage.py       SQLite schema and data access
  backup.py        snapshots, JSON backup and restore, Markdown export
  secrets_store.py PIN-protected encryption for secret notes
  winplatform.py   Windows APIs: startup entry, hotkey, front window, DPAPI, meeting detection
  dialogs.py       settings and date pickers
  theme.py         colours, stylesheet, drawn icons
```
