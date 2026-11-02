import os
import subprocess
import sys

import pytest
from helpers import git, needs_git, wyag

from wyag import objects
from wyag.__main__ import main
from wyag.repository import GitRepository, repo_create, repo_find

HELLO = b"hello world\n"
HELLO_SHA = "3b18e512dba79e4c8300dd08aeb37f8e728b8dad"


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


# --- blobs ---

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
    assert objects.object_find(repo, HELLO_SHA[:7].upper()) == HELLO_SHA


def test_object_find_missing(repo):
    with pytest.raises(Exception, match="No such object"):
        objects.object_find(repo, "deadbeef")


def test_head_of_empty_repo_is_missing(repo):
    with pytest.raises(Exception, match="No such object HEAD"):
        objects.object_find(repo, "HEAD")


# --- commits (key-value list with message) ---

SIGNED_COMMIT = (
    b"tree 29ff16c9c14e2652b22f8b78bb08a5a07930c147\n"
    b"parent 206941306e8a8af65b66eaaaea388a7ae24d49a0\n"
    b"parent 7eae1a72f0875a321ce7a790b5d010fb58ad8a8e\n"
    b"author Test User <test@example.com> 1700000000 +0500\n"
    b"committer Test User <test@example.com> 1700000000 +0500\n"
    b"gpgsig -----BEGIN PGP SIGNATURE-----\n"
    b" \n"
    b" iQIzBAABCAAdFiEExwXquOM8bWb4Q2zVGxM2FxoLkGQFAlqGJ\n"
    b" -----END PGP SIGNATURE-----\n"
    b"\n"
    b"Merge branch 'f'\n\nBatafsil.\n"
)


def test_kvlm_roundtrip_with_multiline_value():
    commit = objects.GitCommit(SIGNED_COMMIT)
    assert commit.serialize() == SIGNED_COMMIT
    assert commit.tree == "29ff16c9c14e2652b22f8b78bb08a5a07930c147"
    assert len(commit.parents) == 2
    assert commit.kvlm[b"gpgsig"][0].startswith(b"-----BEGIN PGP SIGNATURE-----\n\n")
    assert commit.message == "Merge branch 'f'\n\nBatafsil.\n"


def test_kvlm_rejects_garbage():
    with pytest.raises(Exception, match="Malformed commit"):
        objects.GitCommit(b"no headers here\nand no blank line")


# --- trees ---

def leaf(mode, path):
    return objects.GitTreeLeaf(mode, path, "0" * 40)


def test_tree_roundtrip_and_git_sort_order():
    tree = objects.GitTree()
    tree.items = [leaf(b"100644", "a.txt"), leaf(b"040000", "a"), leaf(b"100644", "a-b")]
    parsed = objects.GitTree(tree.serialize())

    # "a-b" < "a.txt" < "a/" because git compares directories as "a/".
    assert [x.path for x in parsed.items] == ["a-b", "a.txt", "a"]
    assert parsed.items[2].mode == b"040000"
    assert b"40000 a\x00" in tree.serialize()  # no leading zero on disk


@needs_git
def test_tree_hash_matches_git(git_repo):
    repo = GitRepository(str(git_repo))
    tree_sha = git("rev-parse", "HEAD^{tree}", cwd=git_repo).decode().strip()
    tree = objects.object_read(repo, tree_sha)
    assert objects.object_write(tree) == tree_sha


# --- name resolution ---

@needs_git
def test_object_find_names(git_repo):
    repo = GitRepository(str(git_repo))
    head = git("rev-parse", "HEAD", cwd=git_repo).decode().strip()

    assert objects.object_find(repo, "HEAD") == head
    assert objects.object_find(repo, "master") == head
    assert objects.object_find(repo, "refs/heads/master") == head
    # An annotated tag is its own object; peeling it gives the commit.
    assert objects.object_find(repo, "v1") != head
    assert objects.object_find(repo, "v1", fmt=b"commit") == head


@needs_git
def test_object_find_peels_to_tree(git_repo):
    repo = GitRepository(str(git_repo))
    tree = git("rev-parse", "HEAD^{tree}", cwd=git_repo).decode().strip()
    assert objects.object_find(repo, "v1", fmt=b"tree") == tree

    with pytest.raises(Exception, match="not a tree"):
        objects.object_find(repo, "HEAD", fmt=b"tree", follow=False)


@needs_git
def test_packed_refs(git_repo):
    git("pack-refs", "--all", cwd=git_repo)
    assert not os.path.exists(git_repo / ".git" / "refs" / "heads" / "master")

    repo = GitRepository(str(git_repo))
    head = git("rev-parse", "HEAD", cwd=git_repo).decode().strip()
    assert objects.object_find(repo, "master") == head


# --- CLI: basics ---

def test_cli_end_to_end(tmp_path, monkeypatch, capsysbinary):
    wyag("init", "demo", cwd=tmp_path, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    demo = tmp_path / "demo"
    (demo / "f.txt").write_bytes(HELLO)

    out = wyag("hash-object", "-w", "f.txt", cwd=demo,
               monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out.decode().strip() == HELLO_SHA

    out = wyag("cat-file", HELLO_SHA[:8], cwd=demo,
               monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == HELLO

    out = wyag("cat-file", "-t", HELLO_SHA, cwd=demo,
               monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == b"blob\n"


def test_cli_reports_errors(repo, monkeypatch):
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit, match="fatal: No such object deadbeef"):
        main(["cat-file", "deadbeef"])


def test_cli_log_on_empty_repo(repo, monkeypatch):
    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit, match="'master' does not have any commits yet"):
        main(["log"])


def test_python_dash_m_entry_point(tmp_path):
    out = subprocess.run([sys.executable, "-m", "wyag", "init", "demo"],
                         cwd=tmp_path, capture_output=True, check=True)
    assert b"Initialized" in out.stdout
    assert (tmp_path / "demo" / ".git" / "HEAD").is_file()


# --- CLI: output must match real git byte for byte ---

@needs_git
@pytest.mark.parametrize("args, git_args", [
    (["log"], ["log", "--no-decorate"]),
    (["log", "--oneline"], ["log", "--oneline", "--no-decorate"]),
    (["log", "-n", "1"], ["log", "-n", "1", "--no-decorate"]),
    (["log", "v1"], ["log", "--no-decorate", "v1"]),
    (["ls-tree", "HEAD"], ["ls-tree", "HEAD"]),
    (["ls-tree", "-r", "v1"], ["ls-tree", "-r", "v1"]),
    (["cat-file", "HEAD"], ["cat-file", "commit", "HEAD"]),
    (["cat-file", "v1"], ["cat-file", "tag", "v1"]),
    (["cat-file", "-t", "v1"], ["cat-file", "-t", "v1"]),
])
def test_cli_matches_git(git_repo, monkeypatch, capsysbinary, args, git_args):
    out = wyag(*args, cwd=git_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git(*git_args, cwd=git_repo)


@needs_git
def test_cat_file_prints_trees_like_ls_tree(git_repo, monkeypatch, capsysbinary):
    tree = git("rev-parse", "HEAD^{tree}", cwd=git_repo).decode().strip()
    out = wyag("cat-file", tree, cwd=git_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git("cat-file", "-p", tree, cwd=git_repo)


@needs_git
def test_log_with_merge(git_repo, monkeypatch, capsysbinary):
    git("checkout", "-q", "-b", "feature", "HEAD~1", cwd=git_repo)
    git("commit", "-q", "--allow-empty", "-m", "feature", cwd=git_repo)
    git("checkout", "-q", "master", cwd=git_repo)
    git("merge", "-q", "--no-ff", "feature", "-m", "merge", cwd=git_repo)

    out = wyag("log", cwd=git_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert b"Merge: " in out
    assert out == git("log", "--no-decorate", cwd=git_repo)


# --- checkout ---

@needs_git
def test_checkout_recreates_files(git_repo, tmp_path, monkeypatch, capsysbinary):
    dest = tmp_path / "out"
    wyag("checkout", "v1", str(dest), cwd=git_repo,
         monkeypatch=monkeypatch, capsysbinary=capsysbinary)

    assert (dest / "a.txt").read_text() == "a\na2\n"
    assert (dest / "src" / "sub" / "c.txt").read_text() == "c\n"
    assert os.readlink(dest / "link") == "a.txt"
    assert os.access(dest / "run.sh", os.X_OK)
    assert not os.access(dest / "a.txt", os.X_OK)


@needs_git
def test_checkout_refuses_non_empty_dir(git_repo, tmp_path, monkeypatch):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "keep.txt").write_text("muhim")
    monkeypatch.chdir(git_repo)
    with pytest.raises(SystemExit, match="Not empty"):
        main(["checkout", "HEAD", str(dest)])
    assert (dest / "keep.txt").read_text() == "muhim"


@pytest.mark.parametrize("bad_path", ["..", ".git", "a/b"])
def test_checkout_refuses_unsafe_paths(repo, tmp_path, monkeypatch, bad_path):
    blob = objects.object_write(objects.GitBlob(b"x"), repo)
    tree = objects.GitTree()
    tree.items = [objects.GitTreeLeaf(b"100644", bad_path, blob)]
    tree_sha = objects.object_write(tree, repo)

    monkeypatch.chdir(repo.worktree)
    with pytest.raises(SystemExit, match="unsafe path"):
        main(["checkout", tree_sha, str(tmp_path / "out")])


# --- interoperability with real git ---

@needs_git
def test_real_git_reads_wyag_objects(repo):
    sha = objects.object_write(objects.GitBlob(HELLO), repo)
    assert git("cat-file", "-p", sha, cwd=repo.worktree) == HELLO


@needs_git
def test_wyag_reads_real_git_objects(repo):
    path = os.path.join(repo.worktree, "f.txt")
    with open(path, "wb") as f:
        f.write(b"git yozgan fayl\n")
    sha = git("hash-object", "-w", "f.txt", cwd=repo.worktree).decode().strip()
    assert objects.object_read(repo, sha).serialize() == b"git yozgan fayl\n"


@needs_git
def test_hash_matches_real_git(tmp_path):
    path = tmp_path / "data.bin"
    data = bytes(range(256)) * 10
    path.write_bytes(data)
    expected = git("hash-object", str(path), cwd=tmp_path).decode().strip()
    assert objects.object_write(objects.GitBlob(data)) == expected


@needs_git
def test_real_git_accepts_wyag_commit(repo):
    """A commit built entirely by wyag passes git fsck and shows up in git log."""
    blob = objects.object_write(objects.GitBlob(HELLO), repo)
    tree = objects.GitTree()
    tree.items = [objects.GitTreeLeaf(b"100644", "hello.txt", blob)]
    tree_sha = objects.object_write(tree, repo)

    commit = objects.GitCommit()
    who = b"Test User <test@example.com> 1700000000 +0500"
    commit.kvlm = {b"tree": [tree_sha.encode()], b"author": [who], b"committer": [who],
                   None: b"wyag yozgan commit\n"}
    sha = objects.object_write(commit, repo)
    with open(repo.repo_path("refs", "heads", "master"), "w") as f:
        f.write(sha + "\n")

    git("fsck", "--strict", cwd=repo.worktree)
    assert git("log", "--format=%s", cwd=repo.worktree) == b"wyag yozgan commit\n"
    assert git("show", "HEAD:hello.txt", cwd=repo.worktree) == HELLO
