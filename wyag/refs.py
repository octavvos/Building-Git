from __future__ import annotations

import os

from .repository import GitRepository


def ref_resolve(repo: GitRepository, ref: str) -> str | None:
    """Follow a ref (e.g. HEAD, refs/heads/master) down to an object hash.

    Returns None if the ref does not exist, e.g. HEAD of a repository
    without commits.
    """
    path = repo.repo_file(ref)
    if os.path.isfile(path):
        with open(path) as f:
            data = f.read().strip()
        if data.startswith("ref: "):
            return ref_resolve(repo, data[5:])
        return data

    return packed_refs(repo).get(ref)


def packed_refs(repo: GitRepository) -> dict[str, str]:
    """Read .git/packed-refs, where git gc and git clone keep refs."""
    path = repo.repo_file("packed-refs")
    refs: dict[str, str] = {}
    if not os.path.isfile(path):
        return refs

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            sha, name = line.split(" ", 1)
            refs[name] = sha
    return refs


def head_branch(repo: GitRepository) -> str | None:
    """Name of the checked-out branch, or None for a detached HEAD."""
    with open(repo.repo_file("HEAD")) as f:
        data = f.read().strip()
    if data.startswith("ref: refs/heads/"):
        return data[len("ref: refs/heads/"):]
    return None
