import os

import pytest
from helpers import git, git_text, needs_git, wyag

from wyag.__main__ import main

pytestmark = needs_git


@pytest.fixture
def two_branches(git_repo):
    """master plus a "feat" branch that adds, deletes and reshapes files."""
    git("checkout", "-q", "-b", "feat", cwd=git_repo)
    (git_repo / "feat.txt").write_text("feat\n")
    os.remove(git_repo / "src" / "b.py")
    git("rm", "-q", "-r", "src/sub", cwd=git_repo)  # also removes the now empty src/
    (git_repo / "src").mkdir(exist_ok=True)
    (git_repo / "src" / "sub").write_text("now a file\n")  # directory -> file
    (git_repo / "a.txt").write_text("feat version\n")
    git("add", "-A", cwd=git_repo)
    git("commit", "-q", "-m", "feat", cwd=git_repo)
    git("checkout", "-q", "master", cwd=git_repo)
    return git_repo


def run(repo, *args, monkeypatch, capsysbinary):
    return wyag(*args, cwd=repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary).decode()


def tree_of_worktree(path):
    """git's view of the files on disk, to compare with a commit's tree."""
    git("add", "-A", cwd=path)
    tree = git_text("write-tree", cwd=path)
    git("reset", "-q", cwd=path)
    return tree


# --- branch ---

def test_branch_list_matches_git(two_branches, monkeypatch, capsysbinary):
    out = run(two_branches, "branch", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out.encode() == git("branch", cwd=two_branches)


def test_branch_create_and_delete(two_branches, monkeypatch, capsysbinary):
    run(two_branches, "branch", "topic", "HEAD~1", monkeypatch=monkeypatch,
        capsysbinary=capsysbinary)
    assert git_text("rev-parse", "topic", cwd=two_branches) == \
        git_text("rev-parse", "HEAD~1", cwd=two_branches)

    out = run(two_branches, "branch", "-d", "topic", monkeypatch=monkeypatch,
              capsysbinary=capsysbinary)
    assert out.startswith("Deleted branch topic (was ")
    assert b"topic" not in git("branch", cwd=two_branches)


def test_branch_delete_packed_ref(two_branches, monkeypatch, capsysbinary):
    git("branch", "packed", "HEAD~1", cwd=two_branches)
    git("pack-refs", "--all", cwd=two_branches)
    run(two_branches, "branch", "-d", "packed", monkeypatch=monkeypatch,
        capsysbinary=capsysbinary)
    assert b"packed" not in git("show-ref", cwd=two_branches)
    assert b"refs/tags/v1" in git("show-ref", cwd=two_branches)  # neighbours survive
    git("fsck", "--strict", cwd=two_branches)


@pytest.mark.parametrize("args, error", [
    (["branch", "-d", "feat"], "not fully merged"),
    (["branch", "-d", "master"], "cannot delete branch 'master'"),
    (["branch", "-d", "nope"], "branch 'nope' not found"),
    (["branch", "feat"], "already exists"),
    (["branch", "bad..name"], "not a valid ref name"),
    (["branch", "-d"], "branch name required"),
])
def test_branch_errors(two_branches, monkeypatch, args, error):
    monkeypatch.chdir(two_branches)
    with pytest.raises(SystemExit, match=error):
        main(args)


def test_branch_force_delete(two_branches, monkeypatch, capsysbinary):
    run(two_branches, "branch", "-D", "feat", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert b"feat" not in git("branch", cwd=two_branches)


def test_branch_on_empty_repo(repo, monkeypatch):
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit, match="not a valid object name: 'master'"):
        main(["branch", "x"])


# --- switch ---

def test_switch_updates_files_and_index(two_branches, monkeypatch, capsysbinary):
    out = run(two_branches, "switch", "feat", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == "Switched to branch 'feat'\n"
    assert git_text("symbolic-ref", "HEAD", cwd=two_branches) == "refs/heads/feat"
    assert git("status", "--porcelain", cwd=two_branches) == b""
    assert (two_branches / "src" / "sub").read_text() == "now a file\n"
    assert not (two_branches / "src" / "b.py").exists()

    run(two_branches, "switch", "master", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert git("status", "--porcelain", cwd=two_branches) == b""
    assert (two_branches / "src" / "sub" / "c.txt").read_text() == "c\n"
    assert os.access(two_branches / "run.sh", os.X_OK)
    assert os.readlink(two_branches / "link") == "a.txt"
    assert tree_of_worktree(two_branches) == git_text("rev-parse", "master^{tree}",
                                                       cwd=two_branches)


def test_switch_carries_unrelated_changes(two_branches, monkeypatch, capsysbinary):
    (two_branches / "run.sh").write_text("#!/bin/sh\necho local\n")
    (two_branches / "notes.txt").write_text("untracked\n")
    run(two_branches, "switch", "feat", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert (two_branches / "run.sh").read_text().endswith("echo local\n")
    assert git("status", "--porcelain", cwd=two_branches) == b" M run.sh\n?? notes.txt\n"


def test_switch_keeps_staged_target_version(two_branches, monkeypatch, capsysbinary):
    """If the index already holds the target's version, extra edits survive."""
    (two_branches / "a.txt").write_text("feat version\n")
    git("add", "a.txt", cwd=two_branches)
    (two_branches / "a.txt").write_text("feat version\nplus more\n")
    run(two_branches, "switch", "feat", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert (two_branches / "a.txt").read_text() == "feat version\nplus more\n"


@pytest.mark.parametrize("prepare, error", [
    (lambda r: (r / "a.txt").write_text("unsaved\n"), "local changes .* would be overwritten"),
    (lambda r: ((r / "a.txt").write_text("staged\n"), git("add", "a.txt", cwd=r)),
     "local changes .* would be overwritten"),
    (lambda r: (r / "feat.txt").write_text("mine\n"), "untracked working tree files"),
])
def test_switch_refuses_to_lose_work(two_branches, monkeypatch, prepare, error):
    prepare(two_branches)
    before = git("status", "--porcelain", cwd=two_branches)
    monkeypatch.chdir(two_branches)
    with pytest.raises(SystemExit, match=error):
        main(["switch", "feat"])
    # Nothing was touched.
    assert git_text("symbolic-ref", "HEAD", cwd=two_branches) == "refs/heads/master"
    assert git("status", "--porcelain", cwd=two_branches) == before


def test_switch_refuses_directory_with_untracked_files(two_branches, monkeypatch):
    """On feat, src/sub is a file. On master it is a directory, and an
    untracked file in it must not be thrown away to make room."""
    (two_branches / "src" / "sub" / "mine.txt").write_text("mine\n")
    monkeypatch.chdir(two_branches)
    with pytest.raises(SystemExit, match="untracked working tree files"):
        main(["switch", "feat"])
    assert (two_branches / "src" / "sub" / "mine.txt").read_text() == "mine\n"
    assert (two_branches / "src" / "sub" / "c.txt").read_text() == "c\n"


def test_switch_create(two_branches, monkeypatch, capsysbinary):
    out = run(two_branches, "switch", "-c", "new", monkeypatch=monkeypatch,
              capsysbinary=capsysbinary)
    assert out == "Switched to a new branch 'new'\n"
    assert git_text("rev-parse", "new", cwd=two_branches) == git_text("rev-parse", "master",
                                                                      cwd=two_branches)


def test_switch_create_on_empty_repo(repo, monkeypatch, capsysbinary):
    out = wyag("switch", "-c", "trunk", cwd=repo.worktree, monkeypatch=monkeypatch,
               capsysbinary=capsysbinary)
    assert out == b"Switched to a new branch 'trunk'\n"
    with open(repo.repo_file("HEAD")) as f:
        assert f.read() == "ref: refs/heads/trunk\n"


def test_switch_detach(two_branches, monkeypatch, capsysbinary):
    out = run(two_branches, "switch", "--detach", "v1~1", monkeypatch=monkeypatch,
              capsysbinary=capsysbinary)
    sha = git_text("rev-parse", "v1~1", cwd=two_branches)
    assert out == f"HEAD is now at {sha[:7]} Birinchi commit\n"
    assert git_text("rev-parse", "HEAD", cwd=two_branches) == sha
    assert git("status", "--porcelain", cwd=two_branches) == b""
    # git names the revision as typed (from its reflog); wyag, which keeps no
    # reflog, shows the hash.
    out = run(two_branches, "branch", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == f"* (HEAD detached at {sha[:7]})\n  feat\n  master\n"


@pytest.mark.parametrize("args, error", [
    (["switch", "nope"], "invalid reference: nope$"),
    (["switch", "v1"], "use --detach"),
    (["switch", "-c", "feat"], "already exists"),
])
def test_switch_errors(two_branches, monkeypatch, args, error):
    monkeypatch.chdir(two_branches)
    with pytest.raises(SystemExit, match=error):
        main(args)


def test_switch_to_current_branch(two_branches, monkeypatch, capsysbinary):
    out = run(two_branches, "switch", "master", monkeypatch=monkeypatch,
              capsysbinary=capsysbinary)
    assert out == "Already on 'master'\n"
