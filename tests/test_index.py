import hashlib
import os
import struct
import subprocess

import pytest
from helpers import IDENTITY_ENV, git, git_text, needs_git, wyag

from wyag.__main__ import main
from wyag.ignore import Ignore, glob_to_regex
from wyag.index import index_read, index_write
from wyag.repository import GitRepository

pytestmark = needs_git


def make_tree(root):
    """Files that exercise ignore rules, symlinks and exec bits."""
    for d in ("src/deep", "logs", "build", "node_modules/x", "docs/api"):
        (root / d).mkdir(parents=True, exist_ok=True)
    files = {
        "a.txt": "a\n", "src/b.py": "b\n", "src/deep/c.txt": "c\n", "logs/x.log": "l\n",
        "build/out.o": "o\n", "node_modules/x/i.js": "m\n", "keep.log": "k\n",
        "docs/api/index.md": "d\n", "docs/notes.tmp": "t\n",
        ".gitignore": "*.log\n/build/\nnode_modules\n!keep.log\n",
        "docs/.gitignore": "*.tmp\n",
    }
    for path, content in files.items():
        (root / path).write_text(content)
    (root / "run.sh").write_text("#!/bin/sh\n")
    (root / "run.sh").chmod(0o755)
    if not os.path.lexists(root / "link"):
        os.symlink("a.txt", root / "link")


@pytest.fixture
def pair(tmp_path):
    """Two identical fresh repositories: one driven by wyag, one by git."""
    ours, theirs = tmp_path / "ours", tmp_path / "theirs"
    for path in (ours, theirs):
        path.mkdir()
        git("init", "-q", "-b", "master", cwd=path)
        make_tree(path)
    return ours, theirs


class Both:
    """Run the same operation in both repositories and compare the results."""

    def __init__(self, pair, monkeypatch, capsysbinary):
        self.ours, self.theirs = pair
        self.monkeypatch = monkeypatch
        self.capsys = capsysbinary

    def wyag(self, *args):
        return wyag(*args, cwd=self.ours, monkeypatch=self.monkeypatch,
                    capsysbinary=self.capsys)

    def git(self, *args):
        return git(*args, cwd=self.theirs)

    def edit(self, func):
        func(self.ours)
        func(self.theirs)

    def assert_same_status(self):
        assert self.wyag("status", "--porcelain") == self.git("status", "--porcelain")

    def assert_same_head(self):
        assert git_text("rev-parse", "HEAD", cwd=self.ours) == \
            git_text("rev-parse", "HEAD", cwd=self.theirs)


@pytest.fixture
def both(pair, monkeypatch, capsysbinary):
    return Both(pair, monkeypatch, capsysbinary)


# --- the full workflow, step by step against git ---

def test_workflow_matches_git(both):
    both.assert_same_status()

    both.wyag("add", ".")
    both.git("add", ".")
    both.assert_same_status()
    assert both.wyag("ls-files", "--stage") == both.git("ls-files", "--stage")

    out = both.wyag("commit", "-m", "Birinchi")
    both.git("commit", "-q", "-m", "Birinchi")
    both.assert_same_head()
    assert out.decode().startswith("[master (root-commit) ")
    both.assert_same_status()

    def change(root):
        (root / "a.txt").write_text("a\na2\n")
        (root / "src" / "new.txt").write_text("n\n")
        os.remove(root / "src" / "deep" / "c.txt")
        (root / "run.sh").chmod(0o644)
        (root / "other" / "inner").mkdir(parents=True)
        (root / "other" / "inner" / "u.txt").write_text("u\n")

    both.edit(change)
    both.assert_same_status()

    both.wyag("add", "a.txt", "src")
    both.git("add", "a.txt", "src")
    both.assert_same_status()

    both.wyag("rm", "--cached", "link")
    both.git("rm", "-q", "--cached", "link")
    both.wyag("rm", "src/b.py")
    both.git("rm", "-q", "src/b.py")
    both.assert_same_status()
    assert not (both.ours / "src" / "b.py").exists()

    out = both.wyag("commit", "-m", "Ikkinchi\n\n\ntafsilot  ")
    both.git("commit", "-q", "-m", "Ikkinchi\n\n\ntafsilot  ")
    both.assert_same_head()
    assert out.decode().startswith("[master ")
    both.assert_same_status()

    git("fsck", "--strict", cwd=both.ours)


def test_add_whole_directory_stages_deletions(both):
    both.wyag("add", ".")
    both.git("add", ".")
    both.wyag("commit", "-m", "init")
    both.git("commit", "-q", "-m", "init")

    both.edit(lambda root: os.remove(root / "src" / "b.py"))
    both.wyag("add", "src")
    both.git("add", "src")
    both.assert_same_status()


def test_add_deleted_file_by_name(both):
    both.wyag("add", ".")
    both.git("add", ".")
    both.edit(lambda root: os.remove(root / "a.txt"))
    both.wyag("add", "a.txt")
    both.git("add", "a.txt")
    both.assert_same_status()


def test_file_replaced_by_directory(both):
    both.wyag("add", ".")
    both.git("add", ".")

    def swap(root):
        os.remove(root / "a.txt")
        (root / "a.txt").mkdir()
        (root / "a.txt" / "inside").write_text("i\n")

    both.edit(swap)
    both.assert_same_status()
    both.wyag("add", ".")
    both.git("add", ".")
    assert both.wyag("ls-files", "-s") == both.git("ls-files", "-s")


def test_commit_in_subdirectory_and_nested_paths(both):
    both.edit(lambda root: (root / "src" / "deep" / "more").mkdir())
    both.edit(lambda root: (root / "src" / "deep" / "more" / "x.txt").write_text("x\n"))
    both.monkeypatch.chdir(both.ours / "src")
    main(["add", "deep"])
    both.git("add", "src/deep")
    both.assert_same_status()


def test_status_long_format(both):
    out = both.wyag("status").decode()
    assert out.startswith("On branch master\n\nNo commits yet\n")
    assert "Untracked files:\n\t.gitignore\n" in out

    both.wyag("add", "a.txt")
    both.edit(lambda root: (root / "a.txt").write_text("changed\n"))
    out = both.wyag("status").decode()
    assert "Changes to be committed:\n\tnew file:   a.txt\n" in out
    assert "Changes not staged for commit:\n\tmodified:   a.txt\n" in out

    both.wyag("add", ".")
    both.wyag("commit", "-m", "x")
    assert both.wyag("status").decode().endswith("nothing to commit, working tree clean\n")


def test_status_detached_head(both):
    both.wyag("add", ".")
    both.wyag("commit", "-m", "x")
    git("checkout", "-q", "--detach", cwd=both.ours)
    head = git_text("rev-parse", "--short=7", "HEAD", cwd=both.ours)
    assert both.wyag("status").decode().startswith(f"HEAD detached at {head}\n")


def test_commit_on_detached_head(both):
    both.wyag("add", ".")
    both.wyag("commit", "-m", "x")
    git("checkout", "-q", "--detach", cwd=both.ours)
    both.edit(lambda root: (root / "a.txt").write_text("z\n"))
    both.wyag("add", "a.txt")
    out = both.wyag("commit", "-m", "detached").decode()
    assert out.startswith("[detached HEAD ")
    assert git_text("log", "-1", "--format=%s", cwd=both.ours) == "detached"


# --- errors ---

@pytest.mark.parametrize("args, error", [
    (["add", "nope"], "did not match any files"),
    (["add", "logs/x.log"], "ignored by one of your .gitignore"),
    (["add", "../outside"], "outside repository"),
    (["add", ".git/config"], "inside the .git directory"),
    (["rm", "a.txt"], "did not match any files"),
    (["commit", "-m", "x"], "nothing to commit"),
    (["commit", "-m", "  \n"], "empty commit message"),
])
def test_errors(both, args, error):
    both.monkeypatch.chdir(both.ours)
    with pytest.raises(SystemExit, match=error):
        main(args)


def test_add_force_ignored_file(both):
    both.wyag("add", "-f", "logs/x.log")
    both.git("add", "-f", "logs/x.log")
    both.assert_same_status()


def test_rm_needs_r_for_directories_and_keeps_local_changes(both):
    both.wyag("add", ".")
    both.wyag("commit", "-m", "x")
    both.monkeypatch.chdir(both.ours)
    with pytest.raises(SystemExit, match="recursively without -r"):
        main(["rm", "src"])

    (both.ours / "a.txt").write_text("unsaved work\n")
    with pytest.raises(SystemExit, match="has local modifications"):
        main(["rm", "a.txt"])
    assert (both.ours / "a.txt").read_text() == "unsaved work\n"

    both.wyag("rm", "-r", "src")
    assert not (both.ours / "src").exists()  # empty directories are removed too


def test_nothing_to_commit_after_commit(both):
    both.wyag("add", ".")
    both.wyag("commit", "-m", "x")
    both.monkeypatch.chdir(both.ours)
    with pytest.raises(SystemExit, match="nothing to commit, working tree clean"):
        main(["commit", "-m", "again"])


# --- merge conflicts ---

@pytest.fixture
def conflicted(tmp_path):
    path = tmp_path / "conflict"
    path.mkdir()
    git("init", "-q", "-b", "master", cwd=path)
    (path / "f.txt").write_text("base\n")
    git("add", ".", cwd=path)
    git("commit", "-q", "-m", "base", cwd=path)
    git("checkout", "-q", "-b", "other", cwd=path)
    (path / "f.txt").write_text("other\n")
    git("commit", "-q", "-am", "other", cwd=path)
    git("checkout", "-q", "master", cwd=path)
    (path / "f.txt").write_text("master\n")
    git("commit", "-q", "-am", "master", cwd=path)
    env = {**os.environ, **IDENTITY_ENV}
    subprocess.run(["git", "merge", "other"], cwd=path, env=env, capture_output=True)
    return path


def test_conflicts(conflicted, monkeypatch, capsysbinary):
    out = wyag("status", "-s", cwd=conflicted, monkeypatch=monkeypatch,
               capsysbinary=capsysbinary)
    assert out == git("status", "--porcelain", cwd=conflicted)
    assert out == b"UU f.txt\n"

    with pytest.raises(SystemExit, match="unmerged files"):
        main(["commit", "-m", "merge"])

    (conflicted / "f.txt").write_text("resolved\n")
    wyag("add", "f.txt", cwd=conflicted, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    out = wyag("status", "-s", cwd=conflicted, monkeypatch=monkeypatch,
               capsysbinary=capsysbinary)
    assert out == git("status", "--porcelain", cwd=conflicted) == b"M  f.txt\n"


# --- index format ---

def test_index_roundtrip_matches_git(git_repo):
    """Rewriting git's own index gives the same entries, byte for byte."""
    repo = GitRepository(str(git_repo))
    with open(repo.repo_file("index"), "rb") as f:
        original = f.read()
    index_write(repo, index_read(repo))
    with open(repo.repo_file("index"), "rb") as f:
        rewritten = f.read()

    body = len(rewritten) - 20
    assert rewritten[:body] == original[:body]
    assert git("status", "--porcelain", cwd=git_repo) == b""


def test_index_v3_extended_flags(git_repo, monkeypatch, capsysbinary):
    (git_repo / "later.txt").write_text("l\n")
    git("add", "-N", "later.txt", cwd=git_repo)  # intent-to-add sets a v3 flag
    repo = GitRepository(str(git_repo))
    index = index_read(repo)
    assert any(e.extended_flags for e in index.entries)

    index_write(repo, index)
    with open(repo.repo_file("index"), "rb") as f:
        assert struct.unpack(">I", f.read()[4:8])[0] == 3
    assert git("ls-files", "-s", cwd=git_repo) == wyag(
        "ls-files", "-s", cwd=git_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)


def test_index_with_submodule_entry(git_repo, monkeypatch, capsysbinary):
    head = git_text("rev-parse", "HEAD", cwd=git_repo)
    git("update-index", "--add", "--cacheinfo", f"160000,{head},sub", cwd=git_repo)
    (git_repo / "sub").mkdir()
    (git_repo / "sub" / "inner.txt").write_text("i\n")
    out = wyag("status", "-s", cwd=git_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git("status", "--porcelain", cwd=git_repo) == b"A  sub\n"


def write_index(repo, body):
    with open(repo.repo_file("index"), "wb") as f:
        f.write(body + hashlib.sha1(body).digest())


@pytest.mark.parametrize("body, error", [
    (b"DIRC" + struct.pack(">II", 4, 0), "version 4 is not supported"),
    (b"DIRX" + struct.pack(">II", 2, 0), "bad signature"),
    (b"DIRC" + struct.pack(">II", 2, 0) + b"link" + struct.pack(">I", 0),
     "extension link is not supported"),
])
def test_bad_index(repo, body, error):
    write_index(repo, body)
    with pytest.raises(Exception, match=error):
        index_read(repo)


def test_index_checksum(repo):
    with open(repo.repo_file("index"), "wb") as f:
        f.write(b"DIRC" + struct.pack(">II", 2, 0) + b"\0" * 20)
    with pytest.raises(Exception, match="bad checksum"):
        index_read(repo)


def test_optional_extension_is_skipped(repo):
    write_index(repo, b"DIRC" + struct.pack(">II", 2, 0) + b"TREE" + struct.pack(">I", 3) + b"abc")
    assert index_read(repo).entries == []


def test_index_lock(repo, monkeypatch):
    open(repo.repo_file("index.lock"), "w").close()
    (open(os.path.join(repo.worktree, "f"), "w")).close()
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit, match="Another git process"):
        main(["add", "f"])


def test_racy_git(both):
    """A file changed within the same second as the index write, keeping its
    size, must still be noticed."""
    both.wyag("add", "a.txt")
    entry = index_read(GitRepository(str(both.ours))).by_path()["a.txt"]
    (both.ours / "a.txt").write_text("b\n")  # same size
    st = os.stat(both.ours / "a.txt")
    os.utime(both.ours / "a.txt", ns=(st.st_atime_ns,
                                      entry.mtime[0] * 1_000_000_000 + entry.mtime[1]))
    assert b"AM a.txt" in both.wyag("status", "-s")


def test_filemode_false_ignores_exec_bit(both):
    both.wyag("add", ".")
    git("config", "core.filemode", "false", cwd=both.ours)
    (both.ours / "run.sh").chmod(0o644)
    assert b"A  run.sh\n" in both.wyag("status", "-s")  # staged, but no worktree change


# --- gitignore ---

IGNORE_CASES = [
    ("*.log", ["a.log", "dir/b.log", "a.log.txt", "log"]),
    ("/root.txt", ["root.txt", "dir/root.txt"]),
    ("build/", ["build", "build/x", "src/build", "src/build/y", "build.txt"]),
    ("doc/*.txt", ["doc/a.txt", "doc/sub/a.txt", "x/doc/a.txt"]),
    ("**/foo", ["foo", "a/foo", "a/b/foo", "afoo"]),
    ("a/**/b", ["a/b", "a/x/b", "a/x/y/b", "ab"]),
    ("logs/**", ["logs/a", "logs/b/c", "logs"]),
    ("file?.txt", ["file1.txt", "file12.txt", "file/.txt"]),
    ("[abc].md", ["a.md", "d.md"]),
    ("[!abc].md", ["a.md", "d.md"]),
    ("\\#hash", ["#hash"]),
    ("trailing\\ ", ["trailing ", "trailing"]),
    ("*.tmp\n!keep.tmp", ["x.tmp", "keep.tmp"]),
    ("secret/\n!secret/ok", ["secret/ok", "secret/no"]),
]


@pytest.mark.parametrize("rules, paths", IGNORE_CASES)
def test_ignore_matches_git_check_ignore(tmp_path, rules, paths):
    git("init", "-q", cwd=tmp_path)
    (tmp_path / ".gitignore").write_text(rules + "\n")
    for p in paths:
        # Make paths exist so git knows which ones are directories.
        target = tmp_path / p
        if p in ("build", "src/build", "logs", "secret"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text("x")

    env = {**os.environ, **IDENTITY_ENV}
    ignore = Ignore(GitRepository(str(tmp_path)))
    for p in paths:
        expected = subprocess.run(["git", "check-ignore", "-q", "--no-index", p],
                                  cwd=tmp_path, env=env).returncode == 0
        assert ignore.is_ignored(p, (tmp_path / p).is_dir()) == expected, (rules, p)


def test_nested_gitignore_and_info_exclude(tmp_path):
    git("init", "-q", cwd=tmp_path)
    (tmp_path / ".gitignore").write_text("*.a\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / ".gitignore").write_text("!keep.a\n/only-here\n")
    (tmp_path / ".git" / "info").mkdir(exist_ok=True)
    (tmp_path / ".git" / "info" / "exclude").write_text("*.b\n")

    ignore = Ignore(GitRepository(str(tmp_path)))
    assert ignore.is_ignored("x.a", False)
    assert ignore.is_ignored("sub/x.a", False)
    assert not ignore.is_ignored("sub/keep.a", False)
    assert ignore.is_ignored("sub/only-here", False)
    assert not ignore.is_ignored("only-here", False)
    assert ignore.is_ignored("deep/x.b", False)


def test_global_excludes_file(tmp_path, monkeypatch):
    git("init", "-q", cwd=tmp_path)
    excludes = tmp_path / "global-ignore"
    excludes.write_text(".DS_Store\n")
    config = tmp_path / "gitconfig"
    config.write_text(f"[core]\n\texcludesFile = {excludes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert Ignore(GitRepository(str(tmp_path))).is_ignored("a/.DS_Store", False)


def test_unclosed_bracket_is_literal():
    assert glob_to_regex("a[b") == "a\\[b"
