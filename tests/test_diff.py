import os

import pytest
from helpers import git, needs_git, wyag

from wyag.__main__ import main
from wyag.diff import function_context, hunk_range

pytestmark = needs_git


@pytest.fixture
def changed(tmp_path):
    """A commit, then every kind of change: edits in several hunks, a lost
    final newline, a deletion, a new file, binary data, mode and symlink changes."""
    path = tmp_path / "d"
    path.mkdir()
    git("init", "-q", "-b", "master", cwd=path)
    (path / "nums.txt").write_text("".join(f"{i}\n" for i in range(1, 41)))
    (path / "code.py").write_text("def first():\n    a = 1\n    b = 2\n    c = 3\n\n"
                                  "def second():\n    x = 1\n    y = 2\n    z = 3\n")
    (path / "nonl.txt").write_text("no newline")
    (path / "gone.txt").write_text("bye\n")
    (path / "b.bin").write_bytes(b"bin\0ary")
    (path / "mode.sh").write_text("same\n")
    os.symlink("nums.txt", path / "link")
    git("add", ".", cwd=path)
    git("commit", "-q", "-m", "base", cwd=path)

    lines = [f"{i}\n" for i in range(1, 41)]
    lines[4], lines[29] = "five\n", "thirty\n"
    (path / "nums.txt").write_text("".join(lines) + "41\n")
    (path / "code.py").write_text("def first():\n    a = 1\n    b = 2\n    c = 3\n\n"
                                  "def second():\n    x = 1\n    y = 20\n    z = 3\n")
    (path / "nonl.txt").write_text("no newline, changed")
    os.remove(path / "gone.txt")
    (path / "new.txt").write_text("new\n")
    (path / "b.bin").write_bytes(b"bin\0ary2")
    (path / "mode.sh").chmod(0o755)
    os.remove(path / "link")
    os.symlink("nonl.txt", path / "link")
    git("add", "new.txt", cwd=path)
    return path


def wyag_diff(path, *args, monkeypatch, capsysbinary):
    return wyag("diff", *args, cwd=path, monkeypatch=monkeypatch, capsysbinary=capsysbinary)


def git_diff(path, *args):
    return git("diff", "--no-color", "--no-renames", *args, cwd=path)


@pytest.mark.parametrize("args", [[], ["--cached"], ["--staged"], ["HEAD"]])
def test_diff_matches_git(changed, monkeypatch, capsysbinary, args):
    assert wyag_diff(changed, *args, monkeypatch=monkeypatch, capsysbinary=capsysbinary) \
        == git_diff(changed, *args)


def test_diff_between_commits(changed, monkeypatch, capsysbinary):
    git("add", "-A", cwd=changed)
    git("commit", "-q", "-m", "second", cwd=changed)
    for args in (["HEAD~1", "HEAD"], ["HEAD", "HEAD~1"]):
        assert wyag_diff(changed, *args, monkeypatch=monkeypatch, capsysbinary=capsysbinary) \
            == git_diff(changed, *args)


def test_function_context_in_hunk_header(changed, monkeypatch, capsysbinary):
    out = wyag_diff(changed, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    # The hunk starts at line 5; the closest line above that starts with a
    # letter is "def first():".
    assert b"@@ -5,5 +5,5 @@ def first():\n" in out


def test_patch_applies_to_exact_tree(changed, tmp_path, monkeypatch, capsysbinary):
    """Rebuild the second commit from the first with wyag's patch. The binary
    file is left alone: like git's own default diff, it isn't applicable."""
    git("checkout", "HEAD", "--", "b.bin", cwd=changed)
    git("add", "-A", cwd=changed)
    git("commit", "-q", "-m", "second", cwd=changed)

    patch = tmp_path / "p.patch"
    patch.write_bytes(wyag_diff(changed, "HEAD~1", "HEAD", monkeypatch=monkeypatch,
                                capsysbinary=capsysbinary))
    git("checkout", "-q", "--detach", "HEAD~1", cwd=changed)
    git("apply", "--index", str(patch), cwd=changed)
    assert git("write-tree", cwd=changed) == git("rev-parse", "master^{tree}", cwd=changed)


def test_no_changes_no_output(git_repo, monkeypatch, capsysbinary):
    assert wyag_diff(git_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary) == b""


def test_too_many_revisions(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="too many revisions"):
        main(["diff", "HEAD", "HEAD", "HEAD"])


@pytest.mark.parametrize("start, length, text", [(0, 0, "0,0"), (4, 1, "5"), (4, 3, "5,3")])
def test_hunk_range(start, length, text):
    assert hunk_range(start, length) == text


def test_function_context_rules():
    lines = [b"def f():\n", b"    pass\n", b"_private = 1\n", b"  indented\n", b"x\n"]
    assert function_context(lines, 4) == b" _private = 1"
    assert function_context(lines, 2) == b" def f():"
    assert function_context(lines, 0) == b""
    assert function_context([b"a" * 100 + b"   \n"], 1) == b" " + b"a" * 80
