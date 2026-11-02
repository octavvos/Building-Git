"""Unified diffs in git's format."""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterator

from . import objects
from .index import file_mode, index_read
from .objects import GitBlob
from .repository import GitRepository
from .worktree import (
    head_files,
    read_worktree_file,
    tree_flatten,
    trust_exec_bit,
    worktree_changed,
)

ZERO_SHA = "0" * 40
CONTEXT = 3

# One side of a diff: path -> (mode, sha, data loader)
Side = dict


def tree_side(repo: GitRepository, rev: str) -> Side:
    tree = objects.object_find(repo, rev, fmt=b"tree")
    return {path: (mode, sha) for path, (mode, sha) in tree_flatten(repo, tree).items()}


def index_side(repo: GitRepository) -> Side:
    return {e.path: (e.mode, e.sha) for e in index_read(repo).entries if e.stage == 0}


def worktree_side(repo: GitRepository) -> tuple[Side, dict[str, bytes]]:
    """Tracked files as they are on disk now. Returns the side and the file
    contents, which have to be read anyway to hash them."""
    index = index_read(repo)
    index_file = repo.repo_file("index")
    index_mtime = int(os.stat(index_file).st_mtime) if os.path.exists(index_file) else 0
    trust_exec = trust_exec_bit(repo)

    side: Side = {}
    contents: dict[str, bytes] = {}
    for e in index.entries:
        if e.stage != 0 or e.mode == 0o160000:
            continue
        change = worktree_changed(repo, e, index_mtime, trust_exec)
        if change == "D":
            continue
        if change is None:
            side[e.path] = (e.mode, e.sha)
            continue
        full = os.path.join(repo.worktree, e.path)
        st = os.lstat(full)
        data = read_worktree_file(full, st)
        side[e.path] = (file_mode(st, trust_exec, e.mode), objects.object_write(GitBlob(data)))
        contents[e.path] = data
    return side, contents


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def hunk_range(start: int, length: int) -> str:
    # An empty range names the line before it, and a length of 1 is implied.
    if length == 0:
        return f"{start},0"
    return f"{start + 1}" if length == 1 else f"{start + 1},{length}"


def function_context(old: list[bytes], start: int) -> bytes:
    """The text git puts after a hunk header: the closest line above the hunk
    that starts with a letter, "_" or "$" (git's default "funcname" rule),
    without trailing whitespace and cut to 80 bytes."""
    for line in reversed(old[:start]):
        if line[:1].isalpha() or line[:1] in (b"_", b"$"):
            return b" " + line.rstrip()[:80]
    return b""


def unified_hunks(old: list[bytes], new: list[bytes]) -> Iterator[bytes]:
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for group in matcher.get_grouped_opcodes(CONTEXT):
        i1, i2 = group[0][1], group[-1][2]
        j1, j2 = group[0][3], group[-1][4]
        header = f"@@ -{hunk_range(i1, i2 - i1)} +{hunk_range(j1, j2 - j1)} @@".encode()
        yield header + function_context(old, i1) + b"\n"
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                for line in old[a1:a2]:
                    yield from diff_line(b" ", line)
                continue
            for line in old[a1:a2]:
                yield from diff_line(b"-", line)
            for line in new[b1:b2]:
                yield from diff_line(b"+", line)


def diff_line(prefix: bytes, line: bytes) -> Iterator[bytes]:
    if line.endswith(b"\n"):
        yield prefix + line
    else:
        yield prefix + line + b"\n"
        yield b"\\ No newline at end of file\n"


def file_diff(path: str, old: tuple | None, new: tuple | None,
              old_data: bytes, new_data: bytes) -> Iterator[bytes]:
    old_mode, old_sha = old if old else (None, ZERO_SHA)
    new_mode, new_sha = new if new else (None, ZERO_SHA)

    yield f"diff --git a/{path} b/{path}\n".encode()
    if old is None:
        yield f"new file mode {new_mode:06o}\n".encode()
    elif new is None:
        yield f"deleted file mode {old_mode:06o}\n".encode()
    elif old_mode != new_mode:
        yield f"old mode {old_mode:06o}\nnew mode {new_mode:06o}\n".encode()

    if old_sha == new_sha:
        return  # only the mode changed

    same_mode = f" {old_mode:06o}" if old and new and old_mode == new_mode else ""
    yield f"index {old_sha[:7]}..{new_sha[:7]}{same_mode}\n".encode()

    a = f"a/{path}" if old else "/dev/null"
    b = f"b/{path}" if new else "/dev/null"
    if is_binary(old_data) or is_binary(new_data):
        yield f"Binary files {a} and {b} differ\n".encode()
        return
    yield f"--- {a}\n+++ {b}\n".encode()
    yield from unified_hunks(old_data.splitlines(keepends=True),
                             new_data.splitlines(keepends=True))


def diff(repo: GitRepository, old: Side, new: Side,
         new_contents: dict[str, bytes] | None = None) -> Iterator[bytes]:
    """Yield a git-style patch turning old into new."""
    new_contents = new_contents or {}

    def load(side_entry: tuple | None, path: str, contents: dict[str, bytes]) -> bytes:
        if side_entry is None:
            return b""
        if path in contents:
            return contents[path]
        if side_entry[0] == 0o160000:
            return f"Subproject commit {side_entry[1]}\n".encode()
        return objects.object_read(repo, side_entry[1]).serialize()

    for path in sorted(set(old) | set(new)):
        o, n = old.get(path), new.get(path)
        if o == n:
            continue
        yield from file_diff(path, o, n, load(o, path, {}), load(n, path, new_contents))


def diff_command(repo: GitRepository, revs: list[str], cached: bool) -> Iterator[bytes]:
    """What `git diff` shows for the given arguments."""
    if len(revs) > 2:
        raise Exception("too many revisions (at most two)")
    if len(revs) == 2:
        return diff(repo, tree_side(repo, revs[0]), tree_side(repo, revs[1]))
    if cached:
        old = tree_side(repo, revs[0]) if revs else head_files(repo)
        return diff(repo, old, index_side(repo))
    new, contents = worktree_side(repo)
    old = tree_side(repo, revs[0]) if revs else index_side(repo)
    return diff(repo, old, new, contents)
