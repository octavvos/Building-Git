"""Branches: listing, creating, deleting and switching between them."""

from __future__ import annotations

from . import objects
from .history import CommitGraph
from .refs import (
    check_ref_name,
    head_branch,
    head_set,
    ref_create,
    ref_delete,
    ref_list,
    ref_resolve,
)
from .repository import GitRepository
from .worktree import checkout_tree, tree_flatten


def branch_list(repo: GitRepository) -> list[str]:
    """Lines in the format of `git branch`."""
    current = head_branch(repo)
    lines = []
    if current is None:
        lines.append(f"* (HEAD detached at {ref_resolve(repo, 'HEAD')[:7]})")
    for ref in ref_list(repo):
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            lines.append(f"{'*' if name == current else ' '} {name}")
    return lines


def branch_exists(repo: GitRepository, name: str) -> bool:
    return f"refs/heads/{name}" in ref_list(repo)


def branch_create(repo: GitRepository, name: str, start: str = "HEAD") -> str:
    check_ref_name(name)
    if branch_exists(repo, name):
        raise Exception(f"a branch named '{name}' already exists")
    if start == "HEAD" and ref_resolve(repo, "HEAD") is None:
        raise Exception(f"not a valid object name: '{head_branch(repo) or 'HEAD'}'")
    sha = objects.object_find(repo, start, fmt=b"commit")
    ref_create(repo, f"refs/heads/{name}", sha)
    return sha


def branch_delete(repo: GitRepository, name: str, force: bool = False) -> str:
    """Delete a branch and return the hash it pointed to."""
    ref = f"refs/heads/{name}"
    sha = ref_list(repo).get(ref)
    if sha is None:
        raise Exception(f"branch '{name}' not found")
    if name == head_branch(repo):
        raise Exception(f"cannot delete branch '{name}' used by the current worktree")

    head = ref_resolve(repo, "HEAD")
    if not force and (head is None or not CommitGraph(repo).is_ancestor(sha, head)):
        raise Exception(f"the branch '{name}' is not fully merged.\n"
                        f"If you are sure you want to delete it, run 'wyag branch -D {name}'")
    ref_delete(repo, ref)
    return sha


def switch(repo: GitRepository, target: str, create: bool = False,
           detach: bool = False) -> str:
    """Switch HEAD to a branch (or a detached commit) and update the files.

    Returns the message to print, worded like git's.
    """
    if create:
        check_ref_name(target)
        if branch_exists(repo, target):
            raise Exception(f"a branch named '{target}' already exists")
        if ref_resolve(repo, "HEAD") is None:
            # No commits yet: the new branch is born on the first commit.
            head_set(repo, branch=target)
            return f"Switched to a new branch '{target}'"
        sha = ref_resolve(repo, "HEAD")
    elif detach:
        sha = objects.object_find(repo, target, fmt=b"commit")
    else:
        if not branch_exists(repo, target):
            hint = " (use --detach to check out a commit)" \
                if objects.object_resolve(repo, target) else ""
            raise Exception(f"invalid reference: {target}{hint}")
        if target == head_branch(repo):
            return f"Already on '{target}'"
        sha = ref_resolve(repo, f"refs/heads/{target}")

    tree = objects.object_find(repo, sha, fmt=b"tree")
    checkout_tree(repo, tree_flatten(repo, tree), action="switch")

    if create:
        ref_create(repo, f"refs/heads/{target}", sha)
        head_set(repo, branch=target)
        return f"Switched to a new branch '{target}'"
    if detach:
        head_set(repo, sha=sha)
        summary = objects.object_read(repo, sha).message.split("\n", 1)[0]
        return f"HEAD is now at {sha[:7]} {summary}"
    head_set(repo, branch=target)
    return f"Switched to branch '{target}'"
