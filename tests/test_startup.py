import sys
import types

import pytest

from sidebar_app import winplatform


class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_winreg(store, block_writes):
    """Registry stand-in keyed by (key path, value name)."""
    m = types.ModuleType("winreg")
    m.HKEY_CURRENT_USER, m.HKEY_LOCAL_MACHINE, m.KEY_SET_VALUE, m.REG_SZ = 1, 3, 2, 1
    m.OpenKey = lambda root, path, *a: FakeKey(path)

    def query(key, name):
        if (key.path, name) not in store:
            raise FileNotFoundError(name)
        return store[(key.path, name)], 1

    def set_value(key, name, _r, _t, value):
        if block_writes and key.path == winplatform.RUN_KEY:
            raise PermissionError("Access is denied")
        store[(key.path, name)] = value

    def delete(key, name):
        if (key.path, name) not in store:
            raise FileNotFoundError(name)
        del store[(key.path, name)]

    m.QueryValueEx, m.SetValueEx, m.DeleteValue = query, set_value, delete
    return m


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(winplatform, "_create_shortcut", lambda lnk: (lnk.parent.mkdir(parents=True, exist_ok=True),
                                                                      lnk.write_bytes(b"lnk")))
    return tmp_path


def test_registry_used_when_allowed(windows, monkeypatch):
    store = {}
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(store, block_writes=False))
    assert winplatform.set_startup_enabled(True) == "registry + startup folder"
    assert store[(winplatform.RUN_KEY, "Sidebar")] == winplatform.startup_command()
    assert winplatform.startup_command().endswith("--startup")
    assert winplatform.is_startup_enabled() and winplatform.startup_shortcut().exists()
    assert winplatform.set_startup_enabled(False) == "off"
    assert not winplatform.is_startup_enabled()


def test_falls_back_to_startup_folder_when_registry_blocked(windows, monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg({}, block_writes=True))
    assert winplatform.set_startup_enabled(True) == "startup folder"
    assert winplatform.startup_shortcut().exists()
    assert winplatform.is_startup_enabled()          # so the checkbox stays ticked
    winplatform.set_startup_enabled(False)
    assert not winplatform.is_startup_enabled()


def test_reports_failure_when_both_blocked(windows, monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg({}, block_writes=True))

    def boom(lnk):
        raise OSError("policy")
    monkeypatch.setattr(winplatform, "_create_shortcut", boom)
    with pytest.raises(OSError, match="Access is denied"):
        winplatform.set_startup_enabled(True)


def test_switched_off_in_task_manager(windows, monkeypatch):
    approved = winplatform.APPROVED_KEY + "\\Run"
    store = {(winplatform.RUN_KEY, "Sidebar"): winplatform.startup_command(),
             (approved, "Sidebar"): b"\x03" + bytes(11)}            # Task Manager: Disabled
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(store, block_writes=False))
    assert winplatform.startup_disabled_in_task_manager()
    assert not winplatform.is_startup_enabled()                       # checkbox shows the truth
    winplatform.set_startup_enabled(True)                             # ticking it turns it back on
    assert (approved, "Sidebar") not in store
    assert winplatform.is_startup_enabled()


def test_stale_entry_is_repointed(windows, monkeypatch):
    store = {(winplatform.RUN_KEY, "Sidebar"): '"C:\\old\\pythonw.exe" "C:\\Users\\x\\Desktop\\sidebar\\run_sidebar.py"'}
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(store, block_writes=False))
    assert winplatform.refresh_startup_entry()
    assert store[(winplatform.RUN_KEY, "Sidebar")] == winplatform.startup_command()


def test_existing_registry_entry_gains_startup_folder_shortcut(windows, monkeypatch):
    store = {(winplatform.RUN_KEY, "Sidebar"): winplatform.startup_command()}
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg(store, block_writes=False))
    assert not winplatform.startup_shortcut().exists()
    assert winplatform.refresh_startup_entry()            # upgrade on next launch, no re-ticking needed
    assert winplatform.startup_shortcut().exists()
    assert not winplatform.refresh_startup_entry()        # nothing more to do


def test_off_stays_off(windows, monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg({}, block_writes=False))
    assert not winplatform.refresh_startup_entry()
    assert not winplatform.startup_shortcut().exists()


def test_policy_block_detected(windows, monkeypatch):
    path = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg({(path, "DisableCurrentUserRun"): 1}, False))
    assert winplatform.run_entries_blocked_by_policy()


def test_second_startup_launch_is_silent(tmp_path):
    """When both startup methods fire, the second copy must exit without
    sliding the first one open."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtNetwork import QLocalServer
    from PySide6.QtWidgets import QApplication
    from sidebar_app import app as app_mod
    qapp = QApplication.instance() or QApplication([])
    server = QLocalServer()
    QLocalServer.removeServer(app_mod.SERVER_NAME)
    assert server.listen(app_mod.SERVER_NAME)
    received = []
    server.newConnection.connect(lambda: (c := server.nextPendingConnection()) and
                                 c.readyRead.connect(lambda: received.append(bytes(c.readAll()))))
    assert app_mod._signal_running_instance(show=False)
    for _ in range(20):
        qapp.processEvents()
    assert received == []
    assert app_mod._signal_running_instance(show=True)
    for _ in range(50):
        qapp.processEvents()
    assert received == [b"show"]
    server.close()


def test_launch_log_line_is_written(tmp_path):
    """The diagnostic line must actually be written (it swallows its own errors)."""
    from sidebar_app import app as app_mod
    log = tmp_path / "launch.log"
    app_mod._log_launch(log, True)
    line = log.read_text()
    assert "AT-STARTUP" in line and "package=" in line and "blocked_by_policy=" in line
