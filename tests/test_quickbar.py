import pytest
from PySide6.QtWidgets import QApplication

from sidebar_app import quickbar, winplatform
from sidebar_app.quickbar import QuickBar, find_app, youtube_id
from sidebar_app.theme import DARK


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=co7KgV2edvI",
    "https://youtube.com/watch?feature=share&v=co7KgV2edvI&t=10",
    "https://youtu.be/co7KgV2edvI?si=abc",
    "https://www.youtube.com/embed/co7KgV2edvI",
    "co7KgV2edvI",
])
def test_youtube_id(url):
    assert youtube_id(url) == "co7KgV2edvI"


def test_youtube_id_rejects_junk():
    assert youtube_id("https://example.com") is None


def test_find_app_in_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(quickbar.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(quickbar.shutil, "which", lambda name: None)
    exe = tmp_path / "Downloads" / "caffeine" / "caffeine64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    assert find_app("caffeine64.exe") == str(exe)


def make_bar(store):
    return QuickBar(DARK, store.get, store.__setitem__)


def test_launch_tile_start_and_stop(app, tmp_path, monkeypatch):
    exe = tmp_path / "caffeine64.exe"
    exe.write_bytes(b"MZ")
    store = {"launch_app_path": str(exe)}
    started, killed, running = [], [], {"v": False}
    monkeypatch.setattr(winplatform, "process_running", lambda name: running["v"])
    monkeypatch.setattr(winplatform, "kill_process", lambda name: killed.append(name))
    monkeypatch.setattr(quickbar.QProcess, "startDetached",
                        staticmethod(lambda path, args, cwd: started.append(path) or True))
    bar = make_bar(store)
    tile = bar.app
    assert tile.title.text() == "Caffeine" and tile.status.text() == "Off"
    tile.toggle()
    assert started == [str(exe)] and tile.running
    running["v"] = True
    tile.toggle()
    assert killed == ["caffeine64.exe"] and not tile.running


def test_sound_tile_browser_mode(app, monkeypatch):
    opened = []
    monkeypatch.setattr(quickbar.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    store = {"focus_sound_browser": True}
    bar = make_bar(store)
    bar.sound.toggle()
    assert opened == [quickbar.DEFAULT_SOUND_URL]
    assert bar.sound.name() == "Airplane cabin"
