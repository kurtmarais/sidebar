import shutil
import subprocess

import pytest

from sidebar_app import updater

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(cwd)})


@pytest.fixture
def repos(tmp_path):
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "requirements.txt").write_text("PySide6>=6.6\n")
    (origin / "app.txt").write_text("v1\n")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "v1")
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    return origin, clone


def test_pull_new_version(repos, tmp_path):
    origin, clone = repos
    (origin / "app.txt").write_text("v2\n")
    git(origin, "commit", "-q", "-am", "v2")
    log = tmp_path / "update.log"
    assert updater.run_update(clone, log) == "updated"
    assert (clone / "app.txt").read_text() == "v2\n"
    assert "result: updated" in log.read_text()
    assert "pip" not in log.read_text()            # requirements unchanged -> no reinstall


def test_already_current(repos):
    _, clone = repos
    assert updater.run_update(clone) == "current"


def test_requirements_change_triggers_pip(repos, tmp_path):
    origin, clone = repos
    (origin / "requirements.txt").write_text("PySide6>=6.6\n# changed\n")
    git(origin, "commit", "-q", "-am", "deps")
    log = tmp_path / "update.log"
    assert updater.run_update(clone, log, python="python3") == "updated"
    assert "pip install" in log.read_text()


def test_local_edits_are_never_overwritten(repos):
    origin, clone = repos
    (origin / "app.txt").write_text("v2\n")
    git(origin, "commit", "-q", "-am", "v2")
    (clone / "app.txt").write_text("my edit\n")
    git(clone, "commit", "-q", "-am", "local")
    assert updater.run_update(clone) == "failed"
    assert (clone / "app.txt").read_text() == "my edit\n"


def test_not_a_git_install(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "repo_root", lambda: None)
    assert updater.spawn_update(tmp_path / "log") is False
