import sys
import types

import pytest

from sidebar_app import winplatform


class FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_winreg(store, block_writes):
    m = types.ModuleType("winreg")
    m.HKEY_CURRENT_USER, m.KEY_SET_VALUE, m.REG_SZ = 1, 2, 1
    m.OpenKey = lambda *a: FakeKey()

    def query(key, name):
        if name not in store:
            raise FileNotFoundError(name)
        return store[name], 1

    def set_value(key, name, _r, _t, value):
        if block_writes:
            raise PermissionError("Access is denied")
        store[name] = value

    def delete(key, name):
        if name not in store:
            raise FileNotFoundError(name)
        del store[name]

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
    assert winplatform.set_startup_enabled(True) == "registry"
    assert store["Sidebar"] == winplatform.startup_command()
    assert winplatform.is_startup_enabled() and not winplatform.startup_shortcut().exists()
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
