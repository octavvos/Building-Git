"""Working with the working tree: status, add, rm, commit and updating files
from a tree (used by switch and merge)."""

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


def submodule_paths(index: GitIndex) -> set[str]:
    return {e.path for e in index.entries if e.mode == 0o160000}


def trust_exec_bit(repo: GitRepository) -> bool:
    return (config_get(repo, "core", "filemode") or "true").lower() != "false"


def read_worktree_file(full: str, st: os.stat_result) -> bytes:
    if stat.S_ISLNK(st.st_mode):
        return os.fsencode(os.readlink(full))
    with open(full, "rb") as f:
        return f.read()


def walk_worktree(repo: GitRepository, ignore: Ignore, tracked: set[str],
                  start: str = "", skip_dirs: set[str] = frozenset()) -> Iterator[str]:
    """Yield every file under start that is tracked or not ignored.

    Ignored directories are skipped entirely unless they contain tracked files,
    so big folders like .venv/ or node_modules/ cost nothing. Directories in
    skip_dirs (submodules, for example) are not entered.
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
                if rel in skip_dirs:
                    continue
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
    # Like git, a directory sitting where the index has a file (or a
    # submodule) is not reported as untracked.
    for path in walk_worktree(repo, Ignore(repo), tracked, skip_dirs=tracked):
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
            on_disk = set(walk_worktree(repo, ignore, set(entries), rel,
                                        skip_dirs=submodule_paths(index)))
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


# --- updating the worktree from a tree ---

FileState = tuple  # (mode, sha) as in tree_flatten


def remove_worktree_file(repo: GitRepository, path: str) -> None:
    full = os.path.join(repo.worktree, path)
    if os.path.lexists(full):
        os.remove(full)
    for directory in parent_dirs(path):
        try:
            os.rmdir(os.path.join(repo.worktree, directory))
        except OSError:
            break  # not empty


def write_worktree_file(repo: GitRepository, path: str, mode: int, data: bytes) -> GitIndexEntry:
    """Write a file (or symlink) and return an index entry describing it."""
    full = os.path.join(repo.worktree, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    if os.path.isdir(full) and not os.path.islink(full):
        os.rmdir(full)  # fails, as it should, if something is still inside
    elif os.path.lexists(full):
        os.remove(full)

    if mode == 0o120000:
        os.symlink(os.fsdecode(data), full)
    else:
        with open(full, "wb") as f:
            f.write(data)
        os.chmod(full, 0o755 if mode == 0o100755 else 0o644)
    sha = objects.object_write(GitBlob(data))
    return GitIndexEntry.from_stat(path, sha, mode, os.lstat(full))


def check_untracked_in_the_way(repo: GitRepository, ignore: Ignore, index_paths: set[str],
                               paths: Iterable[str], action: str) -> None:
    """Refuse to overwrite untracked files, like git does. Ignored files are
    treated as expendable."""
    in_the_way = []
    for path in paths:
        full = os.path.join(repo.worktree, path)
        if os.path.isdir(full) and not os.path.islink(full):
            # A directory where a file should go: only fine if everything in
            # it is tracked (and so will be removed first).
            in_the_way += [p for p in walk_worktree(repo, ignore, index_paths, path)
                           if p not in index_paths]
        elif os.path.lexists(full) and path not in index_paths \
                and not ignore.is_ignored(path, False):
            in_the_way.append(path)
        for directory in parent_dirs(path):
            d = os.path.join(repo.worktree, directory)
            if os.path.lexists(d) and not os.path.isdir(d) and directory not in index_paths:
                in_the_way.append(directory)
    if in_the_way:
        files = "".join(f"\t{p}\n" for p in sorted(set(in_the_way)))
        raise Exception(f"The following untracked working tree files would be overwritten "
                        f"by {action}:\n{files}Please move or remove them before you {action}.")


def checkout_tree(repo: GitRepository, target: dict[str, FileState], action: str = "checkout",
                  force: bool = False) -> None:
    """Make the index and worktree match target, moving from the current HEAD.

    Like git's two-way merge: paths that are the same in HEAD and target keep
    any local changes; paths that differ must have no local changes, or the
    whole operation is refused before anything is touched. force=True throws
    local changes away instead.
    """
    index = index_read(repo)
    if not force and any(e.stage > 0 for e in index.entries):
        raise Exception("you need to resolve your current index first")

    current = head_files(repo)
    entries = index.by_path()
    index_file = repo.repo_file("index")
    index_mtime = int(os.stat(index_file).st_mtime) if os.path.exists(index_file) else 0
    trust_exec = trust_exec_bit(repo)

    def staged(path: str) -> FileState | None:
        e = entries.get(path)
        return (e.mode, e.sha) if e else None

    changed = sorted(p for p in set(current) | set(target) | set(entries)
                     if current.get(p) != target.get(p)
                     or (force and staged(p) != target.get(p)))

    if not force:
        dirty = []
        for path in changed:
            if path in entries and staged(path) not in (current.get(path), target.get(path)):
                dirty.append(path)  # staged change that would be lost
            elif path in entries and staged(path) != target.get(path) and \
                    worktree_changed(repo, entries[path], index_mtime, trust_exec) == "M":
                dirty.append(path)  # unstaged change that would be lost
        if dirty:
            files = "".join(f"\t{p}\n" for p in dirty)
            raise Exception(f"Your local changes to the following files would be overwritten "
                            f"by {action}:\n{files}Please commit your changes or stash them "
                            f"before you {action}.")
        new_paths = [p for p in changed if p in target and p not in current]
        check_untracked_in_the_way(repo, Ignore(repo), {e.path for e in index.entries},
                                   new_paths, action)

    # Deletions first, so a file can be replaced by a directory and vice versa.
    for path in changed:
        if path not in target:
            remove_worktree_file(repo, path)
            entries.pop(path, None)
    for path in changed:
        # If the index already holds the target version, keep the worktree as is.
        if path in target and (force or staged(path) != target[path]):
            mode, sha = target[path]
            data = objects.object_read(repo, sha).serialize()
            entries[path] = write_worktree_file(repo, path, mode, data)

    conflicts = [] if force else [e for e in index.entries if e.stage > 0]
    index_write(repo, GitIndex(list(entries.values()) + conflicts))


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
