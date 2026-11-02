import os
import stat
import subprocess

import pytest
from helpers import IDENTITY_ENV, git, git_text, needs_git, wyag

from wyag.__main__ import main
from wyag.merge import merge_file

pytestmark = needs_git


def build(path):
    """base -> "side" and "master" diverge with every kind of change."""
    path.mkdir()
    git("init", "-q", "-b", "master", cwd=path)
    files = {"shared.txt": "".join(f"{i}\n" for i in range(1, 21)), "keep.txt": "keep\n",
             "del.txt": "del\n", "mode.sh": "m\n", "conflict.txt": "c\n", "moddel.txt": "md\n"}
    for name, content in files.items():
        (path / name).write_text(content)
    git("add", ".", cwd=path)
    git("commit", "-q", "-m", "base", cwd=path)

    git("checkout", "-q", "-b", "side", cwd=path)
    (path / "shared.txt").write_text(files["shared.txt"].replace("18\n", "eighteen\n"))
    os.remove(path / "del.txt")
    (path / "both.txt").write_text("same\n")
    (path / "mode.sh").chmod(0o755)
    (path / "side_only.txt").write_text("side\n")
    (path / "conflict.txt").write_text("theirs\n")
    (path / "addadd.txt").write_text("theirs-add\n")
    (path / "moddel.txt").write_text("changed\n")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "side", cwd=path)

    git("checkout", "-q", "master", cwd=path)
    (path / "shared.txt").write_text(files["shared.txt"].replace("2\n", "two\n", 1))
    (path / "both.txt").write_text("same\n")
    (path / "conflict.txt").write_text("ours\n")
    (path / "addadd.txt").write_text("ours-add\n")
    os.remove(path / "moddel.txt")
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "master", cwd=path)
    return path


def make_clean(path):
    """Make side agree with master on the files that would conflict."""
    git("checkout", "-q", "side", cwd=path)
    git("checkout", "-q", "master", "--", "conflict.txt", "addadd.txt", cwd=path)
    git("rm", "-q", "moddel.txt", cwd=path)
    git("commit", "-q", "-m", "side: agree", cwd=path)
    git("checkout", "-q", "master", cwd=path)
    return path


@pytest.fixture
def pair(tmp_path):
    return build(tmp_path / "ours"), build(tmp_path / "theirs")


@pytest.fixture
def clean_pair(pair):
    return make_clean(pair[0]), make_clean(pair[1])


def run(path, *args, monkeypatch, capsysbinary):
    return wyag(*args, cwd=path, monkeypatch=monkeypatch, capsysbinary=capsysbinary).decode()


def run_exit(path, *args, monkeypatch, capsysbinary):
    """Run a command that ends with sys.exit(); return its code and output."""
    monkeypatch.chdir(path)
    with pytest.raises(SystemExit) as exc:
        main(list(args))
    return exc.value.code, capsysbinary.readouterr().out.decode()


def git_merge(path, *args):
    env = {**os.environ, **IDENTITY_ENV}
    return subprocess.run(["git", "merge", *args], cwd=path, env=env, capture_output=True)


# --- conflicts, compared with git ---

def test_conflicted_merge_matches_git(pair, monkeypatch, capsysbinary):
    ours, theirs = pair
    code, out = run_exit(ours, "merge", "side", monkeypatch=monkeypatch,
                         capsysbinary=capsysbinary)
    assert code == 1
    assert "CONFLICT (content): Merge conflict in conflict.txt" in out
    assert "CONFLICT (add/add): Merge conflict in addadd.txt" in out
    assert "CONFLICT (modify/delete): moddel.txt deleted in HEAD and modified in side" in out
    assert out.endswith("Automatic merge failed; fix conflicts and then commit the result.\n")
    assert git_merge(theirs, "side").returncode == 1

    assert git("ls-files", "-s", cwd=ours) == git("ls-files", "-s", cwd=theirs)
    assert git("status", "--porcelain", cwd=ours) == git("status", "--porcelain", cwd=theirs)
    for name in ("conflict.txt", "addadd.txt", "moddel.txt", "shared.txt", "both.txt"):
        assert (ours / name).read_bytes() == (theirs / name).read_bytes(), name
    assert (ours / "conflict.txt").read_text() == \
        "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\n"
    assert stat.S_IMODE(os.stat(ours / "mode.sh").st_mode) == \
        stat.S_IMODE(os.stat(theirs / "mode.sh").st_mode)
    assert (ours / ".git" / "MERGE_HEAD").read_text() == \
        (theirs / ".git" / "MERGE_HEAD").read_text()


def test_status_during_merge(pair, monkeypatch, capsysbinary):
    ours, theirs = pair
    run_exit(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    git_merge(theirs, "side")

    short = run(ours, "status", "-s", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert short.encode() == git("status", "--porcelain", cwd=theirs)
    assert "UU conflict.txt\n" in short and "AA addadd.txt\n" in short
    assert "DU moddel.txt\n" in short

    long = run(ours, "status", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert "You have unmerged paths." in long
    assert "\tboth modified:   conflict.txt\n" in long
    assert "\tboth added:      addadd.txt\n" in long
    assert "\tdeleted by us:   moddel.txt\n" in long


def test_resolve_and_commit_matches_git(pair, monkeypatch, capsysbinary):
    ours, theirs = pair
    run_exit(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    git_merge(theirs, "side")

    monkeypatch.chdir(ours)
    with pytest.raises(SystemExit, match="unmerged files"):
        main(["commit", "-m", "too early"])

    for repo in (ours, theirs):
        (repo / "conflict.txt").write_text("resolved\n")
        (repo / "addadd.txt").write_text("resolved\n")
    run(ours, "add", "conflict.txt", "addadd.txt", "moddel.txt", monkeypatch=monkeypatch,
        capsysbinary=capsysbinary)
    long = run(ours, "status", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert "All conflicts fixed but you are still merging." in long

    run(ours, "commit", "-m", "Merge branch 'side'", monkeypatch=monkeypatch,
        capsysbinary=capsysbinary)
    git("add", "conflict.txt", "addadd.txt", "moddel.txt", cwd=theirs)
    git("commit", "-q", "-m", "Merge branch 'side'", cwd=theirs)

    assert git_text("rev-parse", "HEAD", cwd=ours) == git_text("rev-parse", "HEAD", cwd=theirs)
    assert git_text("rev-parse", "HEAD^2", cwd=ours) == git_text("rev-parse", "side", cwd=ours)
    assert not (ours / ".git" / "MERGE_HEAD").exists()
    assert not (ours / ".git" / "MERGE_MSG").exists()
    git("fsck", "--strict", cwd=ours)


def test_merge_abort(pair, monkeypatch, capsysbinary):
    ours, _ = pair
    before = git_text("rev-parse", "HEAD", cwd=ours)
    run_exit(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    run(ours, "merge", "--abort", monkeypatch=monkeypatch, capsysbinary=capsysbinary)

    assert git("status", "--porcelain", cwd=ours) == b""
    assert (ours / "conflict.txt").read_text() == "ours\n"
    assert not (ours / "moddel.txt").exists()  # deleted on our side, as before
    assert git_text("rev-parse", "HEAD", cwd=ours) == before
    assert not (ours / ".git" / "MERGE_HEAD").exists()


# --- clean merges ---

def test_clean_merge_commit_identical_to_git(clean_pair, monkeypatch, capsysbinary):
    ours, theirs = clean_pair
    out = run(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert "Auto-merging shared.txt" in out
    assert git_merge(theirs, "side").returncode == 0

    assert git_text("rev-parse", "HEAD", cwd=ours) == git_text("rev-parse", "HEAD", cwd=theirs)
    assert git("status", "--porcelain", cwd=ours) == b""
    assert (ours / "shared.txt").read_text().count("two\n") == 1
    assert "eighteen\n" in (ours / "shared.txt").read_text()
    assert not (ours / "del.txt").exists()
    assert os.access(ours / "mode.sh", os.X_OK)


def test_merge_into_other_branch_and_message(clean_pair, monkeypatch, capsysbinary):
    ours, theirs = clean_pair
    for repo in clean_pair:
        git("checkout", "-q", "-b", "topic", cwd=repo)
    run(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    git_merge(theirs, "side")
    assert git_text("log", "-1", "--format=%s", cwd=ours) == "Merge branch 'side' into topic"
    assert git_text("rev-parse", "HEAD", cwd=ours) == git_text("rev-parse", "HEAD", cwd=theirs)


def test_merge_custom_message_and_tag(clean_pair, monkeypatch, capsysbinary):
    ours, _ = clean_pair
    git("tag", "-a", "rel", "-m", "rel", "side", cwd=ours)
    run(ours, "merge", "-m", "O'zim yozgan xabar", "rel", monkeypatch=monkeypatch,
        capsysbinary=capsysbinary)
    assert git_text("log", "-1", "--format=%s", cwd=ours) == "O'zim yozgan xabar"
    assert git_text("rev-parse", "HEAD^2", cwd=ours) == git_text("rev-parse", "side", cwd=ours)


def test_merge_tag_default_message(clean_pair, monkeypatch, capsysbinary):
    ours, _ = clean_pair
    git("tag", "rel", "side", cwd=ours)
    run(ours, "merge", "rel", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert git_text("log", "-1", "--format=%s", cwd=ours) == "Merge tag 'rel'"


# --- fast-forward and friends ---

def test_fast_forward(git_repo, monkeypatch, capsysbinary):
    git("checkout", "-q", "-b", "ahead", cwd=git_repo)
    (git_repo / "a.txt").write_text("ahead\n")
    git("commit", "-q", "-am", "ahead", cwd=git_repo)
    git("checkout", "-q", "master", cwd=git_repo)

    out = run(git_repo, "merge", "ahead", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out.endswith("Fast-forward\n")
    assert git_text("rev-parse", "master", cwd=git_repo) == git_text("rev-parse", "ahead",
                                                                      cwd=git_repo)
    assert git("status", "--porcelain", cwd=git_repo) == b""

    out = run(git_repo, "merge", "ahead", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == "Already up to date.\n"


def test_no_ff_matches_git(tmp_path, monkeypatch, capsysbinary):
    repos = []
    for name in ("ours", "theirs"):
        path = tmp_path / name
        path.mkdir()
        git("init", "-q", "-b", "master", cwd=path)
        (path / "f").write_text("1\n")
        git("add", ".", cwd=path)
        git("commit", "-q", "-m", "base", cwd=path)
        git("checkout", "-q", "-b", "ahead", cwd=path)
        (path / "f").write_text("2\n")
        git("commit", "-q", "-am", "ahead", cwd=path)
        git("checkout", "-q", "master", cwd=path)
        repos.append(path)

    run(repos[0], "merge", "--no-ff", "ahead", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    git_merge(repos[1], "--no-ff", "ahead")
    assert git_text("rev-parse", "HEAD", cwd=repos[0]) == \
        git_text("rev-parse", "HEAD", cwd=repos[1])


def test_merge_into_empty_repo(git_repo, tmp_path, monkeypatch, capsysbinary):
    path = tmp_path / "empty"
    path.mkdir()
    git("init", "-q", "-b", "master", cwd=path)
    git("fetch", "-q", str(git_repo), "master:incoming", cwd=path)
    run(path, "merge", "incoming", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert git_text("rev-parse", "master", cwd=path) == git_text("rev-parse", "incoming",
                                                                  cwd=path)
    assert git("status", "--porcelain", cwd=path) == b""


# --- refusals ---

def test_merge_refuses_with_local_changes(pair, monkeypatch):
    ours, _ = pair
    (ours / "keep.txt").write_text("staged\n")
    git("add", "keep.txt", cwd=ours)
    monkeypatch.chdir(ours)
    with pytest.raises(SystemExit, match="local changes .* overwritten by merge"):
        main(["merge", "side"])
    assert (ours / "keep.txt").read_text() == "staged\n"


def test_merge_refuses_unstaged_change_to_merged_file(clean_pair, monkeypatch):
    ours, _ = clean_pair
    (ours / "shared.txt").write_text("my unsaved work\n")
    monkeypatch.chdir(ours)
    with pytest.raises(SystemExit, match="local changes .* overwritten by merge"):
        main(["merge", "side"])
    assert (ours / "shared.txt").read_text() == "my unsaved work\n"


def test_merge_refuses_unrelated_histories(git_repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    git("init", "-q", "-b", "master", cwd=other)
    (other / "x").write_text("x\n")
    git("add", ".", cwd=other)
    git("commit", "-q", "-m", "other", cwd=other)
    git("fetch", "-q", str(other), "master:unrelated", cwd=git_repo)
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="unrelated histories"):
        main(["merge", "unrelated"])


@pytest.mark.parametrize("args, error", [
    (["merge"], "no commit to merge"),
    (["merge", "--abort"], "no merge to abort"),
    (["merge", "nope"], "No such object nope"),
])
def test_merge_errors(git_repo, monkeypatch, args, error):
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match=error):
        main(args)


def test_merge_twice_while_conflicted(pair, monkeypatch, capsysbinary):
    ours, _ = pair
    run_exit(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    with pytest.raises(SystemExit, match="not concluded your merge"):
        main(["merge", "side"])


# --- merge-base ---

def test_merge_base(pair, monkeypatch, capsysbinary):
    ours, _ = pair
    for args in (["master", "side"], ["--all", "master", "side"], ["side", "side~1"]):
        out = run(ours, "merge-base", *args, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
        assert out.encode() == git("merge-base", *args, cwd=ours)


def test_merge_base_criss_cross(tmp_path, monkeypatch, capsysbinary):
    """Two branches that merged each other have two best common ancestors."""
    path = tmp_path / "cc"
    path.mkdir()
    git("init", "-q", "-b", "master", cwd=path)
    (path / "f").write_text("0\n")
    git("add", ".", cwd=path)
    git("commit", "-q", "-m", "base", cwd=path)
    git("branch", "b", cwd=path)
    (path / "a").write_text("a\n")
    git("add", ".", cwd=path)
    git("commit", "-q", "-m", "a1", cwd=path)
    git("checkout", "-q", "b", cwd=path)
    (path / "b").write_text("b\n")
    git("add", ".", cwd=path)
    git("commit", "-q", "-m", "b1", cwd=path)
    git("branch", "b1", cwd=path)
    git_merge(path, "-q", "-m", "b2", "master~0")
    git("checkout", "-q", "master", cwd=path)
    git_merge(path, "-q", "-m", "a2", "b1")

    out = run(path, "merge-base", "--all", "master", "b", monkeypatch=monkeypatch,
              capsysbinary=capsysbinary)
    expected = git("merge-base", "--all", "master", "b", cwd=path)
    assert sorted(out.split()) == sorted(expected.decode().split())
    assert len(out.split()) == 2


# --- commit: -a and the editor ---

def test_commit_all(git_repo, monkeypatch, capsysbinary):
    (git_repo / "a.txt").write_text("changed\n")
    os.remove(git_repo / "src" / "b.py")
    (git_repo / "untracked.txt").write_text("stays untracked\n")
    run(git_repo, "commit", "-a", "-m", "all", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert git("status", "--porcelain", cwd=git_repo) == b"?? untracked.txt\n"
    assert git("show", "HEAD:a.txt", cwd=git_repo) == b"changed\n"


@pytest.fixture
def editor(tmp_path, monkeypatch):
    """An "editor" that saves what it was shown and writes a canned message."""
    script = tmp_path / "editor.sh"
    seen = tmp_path / "seen.txt"
    reply = tmp_path / "reply.txt"
    script.write_text(f'#!/bin/sh\ncp "$1" "{seen}"\ncat "{reply}" > "$1"\n')
    script.chmod(0o755)
    monkeypatch.setenv("GIT_EDITOR", str(script))
    return seen, reply


def test_commit_with_editor(git_repo, editor, monkeypatch, capsysbinary):
    seen, reply = editor
    reply.write_text("# a comment\nEditor message\n\n\nbody  \n")
    (git_repo / "a.txt").write_text("x\n")
    git("add", "a.txt", cwd=git_repo)
    run(git_repo, "commit", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert git("log", "-1", "--format=%B", cwd=git_repo) == b"Editor message\n\nbody\n\n"
    assert "Lines starting\n# with '#' will be ignored" in seen.read_text()


def test_editor_shows_merge_message(clean_pair, editor, monkeypatch, capsysbinary):
    seen, reply = editor
    ours, _ = clean_pair
    (ours / "keep.txt").write_text("conflict on purpose\n")
    git("commit", "-q", "-am", "keep on master", cwd=ours)
    git("checkout", "-q", "side", cwd=ours)
    (ours / "keep.txt").write_text("other\n")
    git("commit", "-q", "-am", "keep on side", cwd=ours)
    git("checkout", "-q", "master", cwd=ours)

    run_exit(ours, "merge", "side", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    (ours / "keep.txt").write_text("fixed\n")
    run(ours, "add", "keep.txt", monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    reply.write_text("Merge branch 'side'\n\n# Conflicts:\n#\tkeep.txt\n")
    run(ours, "commit", monkeypatch=monkeypatch, capsysbinary=capsysbinary)

    assert seen.read_text().startswith("Merge branch 'side'\n\n# Conflicts:\n#\tkeep.txt\n")
    assert git("log", "-1", "--format=%B", cwd=ours) == b"Merge branch 'side'\n\n"
    assert len(git_text("log", "-1", "--format=%P", cwd=ours).split()) == 2


def test_empty_editor_message_aborts(git_repo, editor, monkeypatch, capsysbinary):
    _, reply = editor
    reply.write_text("# only comments\n\n")
    (git_repo / "a.txt").write_text("x\n")
    git("add", "a.txt", cwd=git_repo)
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="empty commit message"):
        main(["commit"])


def test_failing_editor(git_repo, monkeypatch):
    monkeypatch.setenv("GIT_EDITOR", "false")
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="problem with the editor"):
        main(["commit"])


# --- the line-level merge on its own ---

def lines(*items):
    return "".join(f"{x}\n" for x in items).encode()


@pytest.mark.parametrize("base, ours, theirs, expected", [
    # changes in different places merge cleanly
    (lines(1, 2, 3, 4, 5), lines(1, "two", 3, 4, 5), lines(1, 2, 3, 4, "five"),
     lines(1, "two", 3, 4, "five")),
    # the same change on both sides is not a conflict
    (lines(1, 2, 3), lines(1, "x", 3), lines(1, "x", 3), lines(1, "x", 3)),
    # one side deletes, the other leaves the line alone
    (lines(1, 2, 3), lines(1, 3), lines(1, 2, 3), lines(1, 3)),
    # insertions at both ends
    (lines(2), lines(1, 2), lines(2, 3), lines(1, 2, 3)),
])
def test_merge_file_clean(base, ours, theirs, expected):
    assert merge_file(base, ours, theirs, "HEAD", "side") == (expected, False)


def test_merge_file_conflict_is_trimmed():
    """Lines both sides agree on stay outside the markers."""
    base = lines(1, 2, 3)
    ours = lines(1, "same", "ours", "end", 3)
    theirs = lines(1, "same", "theirs", "end", 3)
    merged, conflicted = merge_file(base, ours, theirs, "HEAD", "side")
    assert conflicted
    assert merged == lines(1, "same", "<<<<<<< HEAD", "ours", "=======", "theirs",
                           ">>>>>>> side", "end", 3)


def test_merge_file_missing_final_newline():
    merged, conflicted = merge_file(b"a", b"ours", b"theirs", "HEAD", "side")
    assert conflicted
    assert merged == b"<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> side\n"
