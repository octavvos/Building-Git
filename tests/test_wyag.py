import os
import shutil
import subprocess
import sys

import pytest

from wyag import objects
from wyag.repository import GitRepository, repo_create, repo_find

HELLO = b"hello world\n"
HELLO_SHA = "3b18e512dba79e4c8300dd08aeb37f8e728b8dad"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git o'rnatilmagan")


def wyag(*args, cwd):
    """Run the wyag CLI as a separate process."""
    return subprocess.run(
        [sys.executable, "-m", "wyag", *args],
        cwd=cwd, capture_output=True, check=False,
    )


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    return repo_create(str(tmp_path / "repo"))


# --- init ---

def test_init_creates_git_layout(repo):
    for d in ("branches", "objects", "refs/tags", "refs/heads"):
        assert os.path.isdir(repo.repo_path(d))
    with open(repo.repo_path("HEAD")) as f:
        assert f.read() == "ref: refs/heads/master\n"
    reopened = GitRepository(repo.worktree)
    assert reopened.conf.get("core", "repositoryformatversion") == "0"


def test_init_refuses_non_empty_gitdir(repo):
    with pytest.raises(Exception, match="not empty"):
        repo_create(repo.worktree)


def test_repo_find_walks_up(repo):
    nested = os.path.join(repo.worktree, "a", "b")
    os.makedirs(nested)
    found = repo_find(nested)
    assert os.path.realpath(found.worktree) == os.path.realpath(repo.worktree)


def test_repo_find_outside_repo(tmp_path):
    assert repo_find(str(tmp_path), required=False) is None


def test_open_rejects_unknown_format_version(repo):
    with open(repo.repo_path("config"), "w") as f:
        f.write("[core]\nrepositoryformatversion = 1\n")
    with pytest.raises(Exception, match="Unsupported"):
        GitRepository(repo.worktree)


# --- objects ---

def test_blob_hash_matches_git_format():
    assert objects.object_write(objects.GitBlob(HELLO)) == HELLO_SHA


def test_write_then_read_roundtrip(repo):
    sha = objects.object_write(objects.GitBlob(HELLO), repo)
    assert os.path.isfile(repo.repo_path("objects", sha[:2], sha[2:]))
    assert objects.object_read(repo, sha).serialize() == HELLO


def test_write_without_repo_stores_nothing(repo):
    objects.object_write(objects.GitBlob(HELLO))
    assert os.listdir(repo.repo_path("objects")) == []


def test_empty_blob(repo):
    sha = objects.object_write(objects.GitBlob(b""), repo)
    assert sha == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert objects.object_read(repo, sha).serialize() == b""


def test_object_find_by_prefix(repo):
    objects.object_write(objects.GitBlob(HELLO), repo)
    assert objects.object_find(repo, HELLO_SHA[:7]) == HELLO_SHA


def test_object_find_missing(repo):
    with pytest.raises(Exception, match="No such object"):
        objects.object_find(repo, "deadbeef")


# --- CLI ---

def test_cli_end_to_end(tmp_path):
    assert wyag("init", "demo", cwd=tmp_path).returncode == 0
    demo = tmp_path / "demo"
    (demo / "f.txt").write_bytes(HELLO)

    out = wyag("hash-object", "-w", "f.txt", cwd=demo)
    assert out.stdout.decode().strip() == HELLO_SHA

    out = wyag("cat-file", HELLO_SHA[:8], cwd=demo)
    assert out.stdout == HELLO


def test_cli_reports_errors(tmp_path):
    wyag("init", "demo", cwd=tmp_path)
    out = wyag("cat-file", "deadbeef", cwd=tmp_path / "demo")
    assert out.returncode != 0
    assert b"fatal:" in out.stderr


# --- Haqiqiy git bilan moslik ---

@needs_git
def test_real_git_reads_wyag_objects(repo):
    sha = objects.object_write(objects.GitBlob(HELLO), repo)
    out = git("cat-file", "-p", sha, cwd=repo.worktree)
    assert out.stdout == HELLO


@needs_git
def test_wyag_reads_real_git_objects(repo):
    path = os.path.join(repo.worktree, "f.txt")
    with open(path, "wb") as f:
        f.write(b"git yozgan fayl\n")
    sha = git("hash-object", "-w", "f.txt", cwd=repo.worktree).stdout.decode().strip()
    assert objects.object_read(repo, sha).serialize() == b"git yozgan fayl\n"


@needs_git
def test_hash_matches_real_git(tmp_path):
    path = tmp_path / "data.bin"
    data = bytes(range(256)) * 10
    path.write_bytes(data)
    expected = git("hash-object", str(path), cwd=tmp_path).stdout.decode().strip()
    assert objects.object_write(objects.GitBlob(data)) == expected
