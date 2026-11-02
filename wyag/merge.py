"""Three-way merges: lines within a file, files within a tree, and commits."""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterator

from . import objects
from .history import CommitGraph
from .index import GitIndex, GitIndexEntry, index_read, index_write
from .objects import GitBlob
from .refs import head_branch, head_set, ref_create, ref_list, ref_resolve
from .repository import GitRepository
from .worktree import (
    FileState,
    checkout_tree,
    commit,
    head_files,
    remove_worktree_file,
    tree_flatten,
    write_worktree_file,
)

# --- merging the lines of one file (diff3) ---

SyncRegion = tuple  # (base_start, base_end, a_start, a_end, b_start, b_end)


def matching_blocks(base: list[bytes], other: list[bytes]) -> list[tuple[int, int, int]]:
    return difflib.SequenceMatcher(None, base, other, autojunk=False).get_matching_blocks()


def sync_regions(base: list[bytes], a: list[bytes], b: list[bytes]) -> list[SyncRegion]:
    """Stretches of base that both a and b left untouched, with where they
    sit in a and b. These split the files into stable and changed parts."""
    am, bm = matching_blocks(base, a), matching_blocks(base, b)
    regions = []
    ia = ib = 0
    while ia < len(am) and ib < len(bm):
        abase, amatch, alen = am[ia]
        bbase, bmatch, blen = bm[ib]
        start = max(abase, bbase)
        end = min(abase + alen, bbase + blen)
        if start < end:
            asub = amatch + (start - abase)
            bsub = bmatch + (start - bbase)
            regions.append((start, end, asub, asub + end - start, bsub, bsub + end - start))
        if abase + alen < bbase + blen:
            ia += 1
        else:
            ib += 1
    regions.append((len(base), len(base), len(a), len(a), len(b), len(b)))
    return regions


def merge_regions(base: list[bytes], a: list[bytes], b: list[bytes]) -> Iterator[tuple]:
    """Yield ("ok", lines) or ("conflict", a_lines, b_lines)."""
    iz = ia = ib = 0
    for zmatch, zend, amatch, aend, bmatch, bend in sync_regions(base, a, b):
        base_part, a_part, b_part = base[iz:zmatch], a[ia:amatch], b[ib:bmatch]
        if a_part or b_part or base_part:
            if a_part == b_part:
                yield "ok", a_part          # same change on both sides
            elif a_part == base_part:
                yield "ok", b_part          # only b changed this part
            elif b_part == base_part:
                yield "ok", a_part          # only a changed this part
            else:
                yield from trim_conflict(a_part, b_part)
        if zend > zmatch:
            yield "ok", base[zmatch:zend]
        iz, ia, ib = zend, aend, bend


def trim_conflict(a: list[bytes], b: list[bytes]) -> Iterator[tuple]:
    """Move lines both sides agree on out of the conflict, as git does."""
    prefix = 0
    while prefix < min(len(a), len(b)) and a[prefix] == b[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(a), len(b)) - prefix and a[-1 - suffix] == b[-1 - suffix]:
        suffix += 1
    if prefix:
        yield "ok", a[:prefix]
    yield "conflict", a[prefix:len(a) - suffix], b[prefix:len(b) - suffix]
    if suffix:
        yield "ok", a[len(a) - suffix:]


def merge_file(base: bytes, ours: bytes, theirs: bytes, our_label: str,
               their_label: str) -> tuple[bytes, bool]:
    """Merge two versions of a file. Returns (content, had_conflicts); on a
    conflict the content holds git-style <<<<<<< ======= >>>>>>> markers."""
    out: list[bytes] = []
    conflicted = False

    def add_lines(lines: list[bytes]) -> None:
        out.extend(lines)

    def end_line() -> None:
        # A marker must start on its own line.
        if out and not out[-1].endswith(b"\n"):
            out[-1] += b"\n"

    split = [x.splitlines(keepends=True) for x in (base, ours, theirs)]
    for region in merge_regions(*split):
        if region[0] == "ok":
            add_lines(region[1])
            continue
        conflicted = True
        end_line()
        out.append(f"<<<<<<< {our_label}\n".encode())
        add_lines(region[1])
        end_line()
        out.append(b"=======\n")
        add_lines(region[2])
        end_line()
        out.append(f">>>>>>> {their_label}\n".encode())
    return b"".join(out), conflicted


# --- merging trees ---

class Conflict:
    def __init__(self, path: str, kind: str, stages: dict[int, FileState],
                 mode: int, content: bytes) -> None:
        self.path = path
        self.kind = kind          # content, add/add, modify/delete
        self.stages = stages      # 1 = base, 2 = ours, 3 = theirs
        self.mode = mode          # what goes in the worktree
        self.content = content


class TreeMerge:
    def __init__(self) -> None:
        self.result: dict[str, FileState] = {}  # cleanly merged paths
        self.conflicts: list[Conflict] = []
        self.messages: list[str] = []


REGULAR = (0o100644, 0o100755)


def merge_mode(base: FileState | None, ours: FileState, theirs: FileState) -> int:
    if ours[0] == theirs[0] or (base and base[0] == theirs[0]):
        return ours[0]
    return theirs[0] if base and base[0] == ours[0] else ours[0]


def merge_trees(repo: GitRepository, base: dict[str, FileState], ours: dict[str, FileState],
                theirs: dict[str, FileState], our_label: str, their_label: str) -> TreeMerge:
    merged = TreeMerge()

    def read(state: FileState | None) -> bytes:
        return objects.object_read(repo, state[1]).serialize() if state else b""

    for path in sorted(set(base) | set(ours) | set(theirs)):
        b, o, t = base.get(path), ours.get(path), theirs.get(path)
        if o == t:
            resolved = o
        elif b == o:
            resolved = t
        elif b == t:
            resolved = o
        elif o and t and o[0] in REGULAR and t[0] in REGULAR \
                and (b is None or b[0] in REGULAR):
            mode = merge_mode(b, o, t)
            merged.messages.append(f"Auto-merging {path}")
            content, conflicted = merge_file(read(b), read(o), read(t), our_label, their_label)
            if not conflicted:
                resolved = (mode, objects.object_write(GitBlob(content), repo))
            else:
                kind = "content" if b else "add/add"
                stages = {n: s for n, s in ((1, b), (2, o), (3, t)) if s}
                merged.conflicts.append(Conflict(path, kind, stages, mode, content))
                merged.messages.append(f"CONFLICT ({kind}): Merge conflict in {path}")
                continue
        else:
            # Deleted on one side and changed on the other (or a symlink or
            # submodule changed differently): keep whichever version exists.
            kept, kept_label, gone_label = (o, our_label, their_label) if o \
                else (t, their_label, our_label)
            stages = {n: s for n, s in ((1, b), (2, o), (3, t)) if s}
            if o and t:
                kind = "content"
                message = f"CONFLICT ({kind}): Merge conflict in {path}"
            else:
                kind = "modify/delete"
                message = (f"CONFLICT (modify/delete): {path} deleted in {gone_label} and "
                           f"modified in {kept_label}. Version {kept_label} of {path} "
                           f"left in tree.")
            merged.conflicts.append(Conflict(path, kind, stages, kept[0], read(kept)))
            merged.messages.append(message)
            continue

        if resolved:
            merged.result[path] = resolved

    paths = set(merged.result) | {c.path for c in merged.conflicts}
    for path in paths:
        for parent in [path.rsplit("/", i)[0] for i in range(1, path.count("/") + 1)]:
            if parent in paths:
                raise Exception(f"merge conflict between a file and a directory at "
                                f"'{parent}' is not supported")
    return merged


# --- merging commits ---

def merge_message(repo: GitRepository, name: str) -> str:
    refs = ref_list(repo)
    if f"refs/heads/{name}" in refs:
        message = f"Merge branch '{name}'"
    elif f"refs/tags/{name}" in refs:
        message = f"Merge tag '{name}'"
    else:
        message = f"Merge commit '{name}'"
    current = head_branch(repo)
    if current not in ("master", "main"):
        message += f" into {current or 'HEAD'}"
    return message + "\n"


def move_head(repo: GitRepository, sha: str) -> None:
    branch = head_branch(repo)
    if branch:
        ref_create(repo, f"refs/heads/{branch}", sha)
    else:
        head_set(repo, sha=sha)


def merge(repo: GitRepository, name: str, message: str | None = None,
          no_ff: bool = False) -> tuple[list[str], bool]:
    """Merge the named commit into HEAD.

    Returns the lines to print and whether the merge stopped on conflicts.
    """
    if os.path.exists(repo.repo_file("MERGE_HEAD")):
        raise Exception("You have not concluded your merge (MERGE_HEAD exists).\n"
                        "Please, commit your changes before you merge.")

    theirs = objects.object_find(repo, name, fmt=b"commit")
    head = ref_resolve(repo, "HEAD")
    graph = CommitGraph(repo)

    if head is None:
        checkout_tree(repo, tree_flatten(repo, objects.object_find(repo, theirs, fmt=b"tree")),
                      action="merge")
        move_head(repo, theirs)
        return [f"Fast-forward to {theirs[:7]}"], False
    if graph.is_ancestor(theirs, head):
        return ["Already up to date."], False

    index = index_read(repo)
    if any(e.stage > 0 for e in index.entries):
        raise Exception("Merging is not possible because you have unmerged files.")
    ours = head_files(repo)
    staged = {e.path: (e.mode, e.sha) for e in index.entries}
    if staged != ours:
        files = "".join(f"\t{p}\n" for p in sorted(set(staged) ^ set(ours) | {
            p for p in set(staged) & set(ours) if staged[p] != ours[p]}))
        raise Exception(f"Your local changes to the following files would be overwritten "
                        f"by merge:\n{files}Please commit your changes or stash them "
                        f"before you merge.")

    their_files = tree_flatten(repo, objects.object_find(repo, theirs, fmt=b"tree"))
    if graph.is_ancestor(head, theirs) and not no_ff:
        checkout_tree(repo, their_files, action="merge")
        move_head(repo, theirs)
        return [f"Updating {head[:7]}..{theirs[:7]}", "Fast-forward"], False

    bases = graph.merge_bases(head, theirs)
    if not bases:
        raise Exception("refusing to merge unrelated histories")
    base_files = tree_flatten(repo, objects.object_find(repo, bases[0], fmt=b"tree"))

    merged = merge_trees(repo, base_files, ours, their_files, "HEAD", name)

    # Update files and index for everything that merged cleanly; conflicted
    # paths keep (or get) one side's version for now.
    target = dict(merged.result)
    for c in merged.conflicts:
        target[c.path] = c.stages.get(2) or c.stages[3]
    checkout_tree(repo, target, action="merge")

    message = message or merge_message(repo, name)
    if merged.conflicts:
        record_conflicts(repo, merged.conflicts)
        with open(repo.repo_file("MERGE_HEAD"), "w") as f:
            f.write(theirs + "\n")
        with open(repo.repo_file("MERGE_MSG"), "w") as f:
            f.write(message + "\n# Conflicts:\n"
                    + "".join(f"#\t{c.path}\n" for c in merged.conflicts))
        lines = merged.messages + ["Automatic merge failed; "
                                   "fix conflicts and then commit the result."]
        return lines, True

    commit(repo, message, parents=[head, theirs])
    return merged.messages + ["Merge made by the 'wyag three-way' strategy."], False


def record_conflicts(repo: GitRepository, conflicts: list[Conflict]) -> None:
    """Write conflict markers into the files and stages 1-3 into the index."""
    index = index_read(repo)
    entries = index.by_path()
    staged: list[GitIndexEntry] = []
    for c in conflicts:
        entries.pop(c.path, None)
        write_worktree_file(repo, c.path, c.mode, c.content)
        for stage, (mode, sha) in sorted(c.stages.items()):
            staged.append(GitIndexEntry(c.path, sha, mode, stage=stage))
    index_write(repo, GitIndex(list(entries.values()) + staged))


def merge_abort(repo: GitRepository) -> None:
    if not os.path.exists(repo.repo_file("MERGE_HEAD")):
        raise Exception("There is no merge to abort (MERGE_HEAD missing).")
    head = head_files(repo)
    for e in index_read(repo).entries:
        if e.stage > 0 and e.path not in head:
            remove_worktree_file(repo, e.path)
    checkout_tree(repo, head, action="merge", force=True)
    for name in ("MERGE_HEAD", "MERGE_MSG"):
        os.remove(repo.repo_file(name))
