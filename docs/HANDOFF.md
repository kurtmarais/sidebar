# Sidebar: handoff for a new chat

Context for continuing work on this project in a fresh conversation. Current version **1.3.0**. All work is on branch **`claude/new-session-wmu4rs`** of `kurtmarais/sidebar` (public repo); it has **not** been merged into `main`.

## What it is

1. **Desktop app (Windows, Python + PySide6).** A sticky-notes sidebar that hides as a thin blue strip on the screen edge and slides out on hover, click, Ctrl+Shift+S or the tray icon. The look and features are modelled on Notiq, without AI. Notes are cards (note, checklist, code, links, image, file, secret), stored in local SQLite.
2. **Phone app (static web app, installed to the iPhone home screen).** Views and edits the text cards.
3. **Sync** between them through the user's own **Supabase** project. Only text cards sync; images, files and secret notes stay on the PC.

## Repo layout

```
run_sidebar.py         launcher (logs "process started", catches early crashes -> startup-crash.log)
sidebar_app/
  app.py               entry: single instance, tray, launch.log, deferred housekeeping, error.log
  window.py            edge strip, slide animation, panel, views, reminders, window-follow, sync hookup
  cards.py             card widgets/types (CARD_KINDS), badges, menus, secret cards
  cardlist.py          card list, sections, drag-reorder, category tabs
  storage.py           SQLite schema v4 (dirty flags, tombstones, starred, due_time), sync merge helpers
  sync.py              Supabase REST sync engine (urllib, no deps); newest edit wins per card
  sync_qt.py           background sync controller (daemon thread) + "Phone sync…" dialog
  quickbar.py          tiles: focus sound (YouTube in off-the-record Qt WebEngine, loaded lazily), app launcher (caffeine64.exe)
  updater.py           git pull --ff-only on quit / "Update and restart"
  winplatform.py       Windows: startup (Run key + Startup-folder shortcut), hotkey, DPAPI, foreground window, meeting detection
  secrets_store.py     PIN/passphrase-locked notes (PBKDF2 + DPAPI)
  dialogs.py, theme.py, backup.py, paths.py
supabase/migrations/   20261101120000_sidebar_sync.sql (tables, RLS, newest-wins trigger), 20261102090000_starred.sql, 20261103090000_due_time.sql
web/                   phone app: index.html, app.js, supabase.js, styles.css, sw.js, config.js (URL + publishable key), _headers (CSP)
wrangler.jsonc         Cloudflare Workers static-assets config (serves web/)
docs/phone-sync.md     user-facing setup guide
tests/                 pytest (offscreen Qt), fake_supabase.py, Playwright (Node) phone tests
```

## User's setup (as of 2 Nov 2026)

- **PC:** Windows, **work-managed**. Python 3.14 from Microsoft's Python install manager (`%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe`), not running in an app package. App cloned at `$HOME\Apps\sidebar` (note: `$HOME\Desktop` is not the visible, OneDrive-synced Desktop). Desktop shortcut via `pyw`. Data in `%APPDATA%\Sidebar` (`sidebar.db`, `assets\`, `backups\`, `launch.log`, `error.log`, `update.log`).
- **Updates:** the user right-clicks the tray icon and chooses **Update and restart**, or quits; the updater runs `git pull` on the checked-out branch.
- **Start with Windows:** works. The startup entry and Startup-folder shortcut both exist; `launch.log` showed an AT-STARTUP launch about 6 minutes after boot, so the delay is Windows on a managed PC. Start-up was then made about 5x faster (strip appears in about 0.3 s). The user has not yet reported the next restart's timings (`process started` lines).
- **Supabase:** project `qdtjghnvizeampzvlxrm` (URL and publishable key are in `web/config.js`). The first migration was run in the SQL Editor; the user has been given the `starred` SQL to run. Sign-ups should be disabled. Leaked-password protection was deliberately left off.
- **Phone:** Cloudflare Worker `sidebar-phone` (workers.dev) deploys from `claude/new-session-wmu4rs`. Installed on an iPhone 17 home screen. The user is happy with the current mobile UI.

## Key behaviours and decisions

- **Sync:** newest edit wins per card. A database trigger ignores older uploads; tombstones are kept for deletes. Same-named default categories merge across devices. Cards being typed in are not replaced mid-edit. The phone queues offline edits. Both clients leave out newer optional columns (`starred`, `due_time`) if their SQL update hasn't been run yet.
- **Phone gestures:** a full **left** swipe archives (restores in Archive); a full **right** swipe stars/unstars; partial swipes spring back. Hold then drag reorders (Notes view, no search). Notes can only be deleted from inside the note. In the editor, Back is top left, Done top right, and Delete note at the bottom. Empty new notes are dropped.
- **Haptics:** the user said to ignore them. An inert iOS "hidden switch" haptic helper is still in `web/app.js`; leave it or remove it.
- **Security hardening done:** links only open http/https/mailto; asset paths are confined to the assets folder; secret notes take passphrases (600k PBKDF2); focus sound uses an off-the-record web profile; the phone app has a strict CSP and renders with no `innerHTML`.

## Gotchas learned

- PySide6 6.12: use `QDialog.DialogCode.Accepted`. `dlg.Accepted` raises, which silently broke Settings → Save earlier.
- Never resize the translucent window. It stays full size and is masked to the strip; resizing made the strip flash at the top on Windows.
- Changing window flags recreates the native window: re-register the hotkey and reapply the mask.
- Show all dialogs through `dialogs.run()` / `dialogs.message()` (on top, placed beside the panel).
- Qt WebEngine: set `AA_ShareOpenGLContexts` before `QApplication`; import lazily; delete view → page → profile in that order.
- iOS home-screen apps have storage separate from Safari.

## Testing

- Run `python -m pytest`: 89 tests. GUI tests use offscreen Qt. Sync tests use `tests/fake_supabase.py` (it mimics auth, PostgREST, the newest-wins trigger, row-level security, and a missing `starred` column). Phone tests drive headless Chromium through Node Playwright with real touch input (`tests/phone_e2e.js`, `tests/phone_gestures.js`).
- The Supabase SQL was verified against local Postgres 16 with stubbed `auth` and roles.
- This cloud environment cannot reach `supabase.co`, so live-project checks have to be run by the user.

## Possible next steps

- Merge `claude/new-session-wmu4rs` into `main`, then switch the PC checkout and Cloudflare's branch to `main`.
- Check the next restart's `launch.log` timing. If Windows is the delay, look into a per-user startup-delay setting.
- Optional: end-to-end encryption of synced notes (sync passphrase); a Supabase backup of non-text cards; a desktop "Starred" filter.

## Working with this user

Be direct and practical; no flattery or patronising remarks. Execute rather than argue, and ask only when a detail is genuinely uncertain. Don't end with "let me know"-style prompts. No emojis as section markers. Don't restate earlier warnings as if they weren't considered. Give PowerShell commands one per code block. Push changes to the working branch, and say plainly what was and wasn't tested on a real device.
