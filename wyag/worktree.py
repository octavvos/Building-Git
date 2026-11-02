"""Working with the working tree: status, add, rm and commit."""

from __future__ import annotations

import os
import posixpath
import stat
from collections.abc import Iterable, Iterator

from . import objects
from .config import config_get, identity
from .ignore import Ignore
from .index import GitIndex, GitIndexEntry, file_mode, index_read, index_write
from .objects import GitBlob, GitCommit, GitTree, GitTreeLeaf
from .refs import head_branch, ref_resolve
from .repository import GitRepository


def rel_path(repo: GitRepository, path: str) -> str:
    """Turn a user-supplied path into a worktree-relative path with "/" separators."""
    absolute = os.path.abspath(path)
    # Resolve symlinked parent directories, but not a symlink at the end itself.
    absolute = os.path.join(os.path.realpath(os.path.dirname(absolute)),
                            os.path.basename(absolute))
    rel = os.path.relpath(absolute, os.path.realpath(repo.worktree))
    if rel == ".":
        return ""
    rel = rel.replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        raise Exception(f"'{path}' is outside repository")
    if rel == ".git" or rel.startswith(".git/"):
        raise Exception(f"'{path}' is inside the .git directory")
    return rel


def is_under(path: str, directory: str) -> bool:
    return directory == "" or path == directory or path.startswith(directory + "/")


def parent_dirs(path: str) -> Iterator[str]:
    while "/" in path:
        path = path.rsplit("/", 1)[0]
        yield path


def trust_exec_bit(repo: GitRepository) -> bool:
    return (config_get(repo, "core", "filemode") or "true").lower() != "false"


def read_worktree_file(full: str, st: os.stat_result) -> bytes:
    if stat.S_ISLNK(st.st_mode):
        return os.fsencode(os.readlink(full))
    with open(full, "rb") as f:
        return f.read()


def walk_worktree(repo: GitRepository, ignore: Ignore, tracked: set[str],
                  start: str = "") -> Iterator[str]:
    """Yield every file under start that is tracked or not ignored.

    Ignored directories are skipped entirely unless they contain tracked files,
    so big folders like .venv/ or node_modules/ cost nothing.
    """
    tracked_dirs = {d for path in tracked for d in parent_dirs(path)}
    stack = [start]
    while stack:
        directory = stack.pop()
        for name in sorted(os.listdir(os.path.join(repo.worktree, directory))):
            if name == ".git":
                continue
            rel = f"{directory}/{name}" if directory else name
            full = os.path.join(repo.worktree, rel)
            if os.path.isdir(full) and not os.path.islink(full):
                if rel in tracked:
                    continue  # a submodule
                if rel in tracked_dirs or not ignore.is_ignored(rel, True):
                    stack.append(rel)
            elif rel in tracked or not ignore.is_ignored(rel, False):
                yield rel


# --- trees ---

def tree_flatten(repo: GitRepository, sha: str, prefix: str = "") -> dict[str, tuple[int, str]]:
    """Map every file path in a tree to (mode, blob sha)."""
    files = {}
    for leaf in objects.object_read(repo, sha).items:
        path = f"{prefix}/{leaf.path}" if prefix else leaf.path
        if leaf.type == "tree":
            files.update(tree_flatten(repo, leaf.sha, path))
        else:
            files[path] = (int(leaf.mode, 8), leaf.sha)
    return files


def head_files(repo: GitRepository) -> dict[str, tuple[int, str]]:
    head = ref_resolve(repo, "HEAD")
    if head is None:
        return {}
    return tree_flatten(repo, objects.object_find(repo, head, fmt=b"tree"))


def write_tree(repo: GitRepository, entries: Iterable[GitIndexEntry]) -> str:
    """Store the index as nested tree objects and return the root tree's hash."""
    contents: dict[str, list[GitTreeLeaf]] = {"": []}
    for entry in entries:
        directory = posixpath.dirname(entry.path)
        key = directory
        while key not in contents:
            contents[key] = []
            key = posixpath.dirname(key)
        contents[directory].append(
            GitTreeLeaf(entry.mode_bytes, posixpath.basename(entry.path), entry.sha))

    # Deepest directories first, so each subtree's hash is known before its parent.
    for directory in sorted(contents, key=len, reverse=True):
        tree = GitTree()
        tree.items = contents[directory]
        sha = objects.object_write(tree, repo)
        if directory == "":
            return sha
        contents[posixpath.dirname(directory)].append(
            GitTreeLeaf(b"040000", posixpath.basename(directory), sha))
    raise AssertionError("unreachable")


# --- status ---

class Status:
    def __init__(self) -> None:
        self.staged: dict[str, str] = {}    # path -> A / M / D (HEAD vs index)
        self.unstaged: dict[str, str] = {}  # path -> M / D (index vs worktree)
        self.unmerged: list[str] = []
        self.untracked: list[str] = []

    @property
    def clean(self) -> bool:
        return not (self.staged or self.unstaged or self.unmerged or self.untracked)


def worktree_changed(repo: GitRepository, entry: GitIndexEntry, index_mtime: int,
                     trust_exec: bool) -> str | None:
    """M or D if the file on disk differs from its index entry, else None."""
    if entry.mode == 0o160000:
        return None  # a submodule: its content is another repository's business
    full = os.path.join(repo.worktree, entry.path)
    try:
        st = os.lstat(full)
    except (FileNotFoundError, NotADirectoryError):
        return "D"
    if stat.S_ISDIR(st.st_mode):
        return "D"

    if file_mode(st, trust_exec, entry.mode) != entry.mode:
        return "M"
    # Same size and mtime means unchanged, unless the file was modified in the
    # same second the index was written ("racy git"): then hash to be sure.
    if entry.stat_matches(st) and entry.mtime[0] < index_mtime:
        return None
    sha = objects.object_write(GitBlob(read_worktree_file(full, st)))
    return None if sha == entry.sha else "M"


def status(repo: GitRepository) -> Status:
    result = Status()
    index = index_read(repo)
    entries = index.by_path()
    result.unmerged = sorted({e.path for e in index.entries if e.stage > 0})

    head = head_files(repo)
    for path in sorted(set(head) | set(entries)):
        if path in result.unmerged:
            continue
        if path not in head:
            result.staged[path] = "A"
        elif path not in entries:
            result.staged[path] = "D"
        elif head[path] != (entries[path].mode, entries[path].sha):
            result.staged[path] = "M"

    index_file = repo.repo_file("index")
    index_mtime = int(os.stat(index_file).st_mtime) if os.path.exists(index_file) else 0
    trust_exec = trust_exec_bit(repo)
    for path, entry in entries.items():
        change = worktree_changed(repo, entry, index_mtime, trust_exec)
        if change:
            result.unstaged[path] = change

    # Untracked files, collapsed to their topmost directory with nothing tracked
    # in it, the way git shows "dir/" instead of every file inside.
    tracked = {e.path for e in index.entries}
    tracked_dirs = {d for path in tracked for d in parent_dirs(path)}
    untracked = set()
    for path in walk_worktree(repo, Ignore(repo), tracked):
        if path in tracked:
            continue
        shown = path
        for directory in parent_dirs(path):
            if directory not in tracked_dirs:
                shown = directory + "/"
        untracked.add(shown)
    result.untracked = sorted(untracked)
    return result


# --- add and rm ---

def stage_file(repo: GitRepository, entries: dict[str, GitIndexEntry], rel: str,
               trust_exec: bool) -> None:
    full = os.path.join(repo.worktree, rel)
    st = os.lstat(full)
    old = entries.get(rel)
    mode = file_mode(st, trust_exec, old.mode if old else None)
    sha = objects.object_write(GitBlob(read_worktree_file(full, st)), repo)

    # A file replaces a directory of the same name, and vice versa.
    for path in [p for p in entries if p.startswith(rel + "/")]:
        del entries[path]
    for directory in parent_dirs(rel):
        entries.pop(directory, None)

    entries[rel] = GitIndexEntry.from_stat(rel, sha, mode, st)


def add(repo: GitRepository, paths: list[str], force: bool = False) -> None:
    index = index_read(repo)
    entries = index.by_path()
    ignore = Ignore(repo)
    trust_exec = trust_exec_bit(repo)
    resolved = set()

    for path in paths:
        rel = rel_path(repo, path)
        full = os.path.join(repo.worktree, rel)

        if os.path.isdir(full) and not os.path.islink(full):
            on_disk = set(walk_worktree(repo, ignore, set(entries), rel))
            for p in on_disk:
                stage_file(repo, entries, p, trust_exec)
            # Tracked files that disappeared from this directory get removed.
            for p in [p for p in entries if is_under(p, rel) and p not in on_disk]:
                del entries[p]
        elif os.path.lexists(full):
            if rel not in entries and not force and ignore.is_ignored(rel, False):
                raise Exception(f"The following path is ignored by one of your .gitignore "
                                f"files:\n{rel}\nUse -f if you really want to add it.")
            stage_file(repo, entries, rel, trust_exec)
        elif any(is_under(p, rel) for p in entries):
            for p in [p for p in entries if is_under(p, rel)]:
                del entries[p]  # staging a deletion
        else:
            raise Exception(f"pathspec '{path}' did not match any files")
        resolved.add(rel)

    # Adding a conflicted path marks it as resolved: drop its stage 1-3 entries.
    conflicts = [e for e in index.entries if e.stage > 0
                 and not any(is_under(e.path, r) for r in resolved)]
    index_write(repo, GitIndex(list(entries.values()) + conflicts))


def rm(repo: GitRepository, paths: list[str], cached: bool = False,
       recursive: bool = False) -> list[str]:
    index = index_read(repo)
    entries = index.by_path()
    trust_exec = trust_exec_bit(repo)
    index_file = repo.repo_file("index")
    index_mtime = int(os.stat(index_file).st_mtime) if os.path.exists(index_file) else 0

    removed = []
    for path in paths:
        rel = rel_path(repo, path)
        matches = [p for p in entries if is_under(p, rel)]
        if not matches:
            raise Exception(f"pathspec '{path}' did not match any files")
        if matches != [rel] and not recursive:
            raise Exception(f"not removing '{path}' recursively without -r")
        removed.extend(matches)

    if not cached:
        # Like git, refuse to throw away changes that were never committed.
        for p in removed:
            if worktree_changed(repo, entries[p], index_mtime, trust_exec) == "M":
                raise Exception(f"'{p}' has local modifications (use --cached to keep the file)")

    for p in removed:
        del entries[p]
        if not cached:
            full = os.path.join(repo.worktree, p)
            if os.path.lexists(full):
                os.remove(full)
            for directory in parent_dirs(p):
                try:
                    os.rmdir(os.path.join(repo.worktree, directory))
                except OSError:
                    break  # not empty

    index_write(repo, GitIndex(list(entries.values())
                               + [e for e in index.entries if e.stage > 0]))
    return removed


# --- commit ---

def commit(repo: GitRepository, message: str) -> tuple[str, str]:
    """Create a commit from the index and move the current branch to it.

    Returns the new commit's hash and the line git prints, e.g.
    "[master 1a2b3c4] Fix bug".
    """
    index = index_read(repo)
    if any(e.stage > 0 for e in index.entries):
        raise Exception("Committing is not possible because you have unmerged files.")
    if not message:
        raise Exception("Aborting commit due to empty commit message.")

    tree = write_tree(repo, index.entries)
    parent = ref_resolve(repo, "HEAD")
    if parent and objects.object_find(repo, parent, fmt=b"tree") == tree:
        raise Exception("nothing to commit, working tree clean")
    if not parent and not index.entries:
        raise Exception("nothing to commit (create/copy files and use \"wyag add\" to track)")

    c = GitCommit()
    c.kvlm = {b"tree": [tree.encode()]}
    if parent:
        c.kvlm[b"parent"] = [parent.encode()]
    c.kvlm[b"author"] = [identity(repo, "AUTHOR")]
    c.kvlm[b"committer"] = [identity(repo, "COMMITTER")]
    c.kvlm[None] = message.encode()
    sha = objects.object_write(c, repo)

    branch = head_branch(repo)
    if branch:
        ref = repo.repo_file("refs", *f"heads/{branch}".split("/"), mkdir=True)
        label = branch
    else:
        ref = repo.repo_file("HEAD")
        label = "detached HEAD"
    with open(ref, "w") as f:
        f.write(sha + "\n")

    root = "" if parent else " (root-commit)"
    first_line = message.split("\n", 1)[0]
    return sha, f"[{label}{root} {sha[:7]}] {first_line}"
