import os

import pytest
from helpers import IDENTITY_ENV, git

from wyag.repository import repo_create


@pytest.fixture(autouse=True)
def isolated_identity(monkeypatch, tmp_path):
    """wyag runs in-process, so give it the same fixed identity as git and
    keep the user's own config and ignore files out of the way."""
    for key, value in IDENTITY_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


@pytest.fixture
def repo(tmp_path):
    return repo_create(str(tmp_path / "repo"))


@pytest.fixture
def git_repo(tmp_path):
    """A repository built by real git: two commits, a subdirectory,
    an executable, a symlink and an annotated tag."""
    path = tmp_path / "gitrepo"
    path.mkdir()
    git("init", "-q", "-b", "master", cwd=path)

    (path / "a.txt").write_text("a\n")
    (path / "src" / "sub").mkdir(parents=True)
    (path / "src" / "b.py").write_text("print('b')\n")
    (path / "src" / "sub" / "c.txt").write_text("c\n")
    (path / "run.sh").write_text("#!/bin/sh\n")
    (path / "run.sh").chmod(0o755)
    os.symlink("a.txt", path / "link")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "Birinchi commit", cwd=path)

    (path / "a.txt").write_text("a\na2\n")
    git("commit", "-q", "-am", "Ikkinchi commit\n\nBatafsil tavsif.", cwd=path)
    git("tag", "-a", "v1", "-m", "versiya 1", cwd=path)
    return path
