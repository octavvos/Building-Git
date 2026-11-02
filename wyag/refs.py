from __future__ import annotations

import os
import re

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


def ref_list(repo: GitRepository) -> dict[str, str]:
    """Every ref under refs/ with the hash it points to, sorted by name.

    Loose refs override packed ones, since git writes updates as loose files.
    """
    refs = packed_refs(repo)
    refs_dir = repo.repo_path("refs")
    for root, _, files in os.walk(refs_dir):
        for name in files:
            full = os.path.join(root, name)
            ref = "refs/" + os.path.relpath(full, refs_dir).replace(os.sep, "/")
            sha = ref_resolve(repo, ref)
            if sha:
                refs[ref] = sha
    return dict(sorted(refs.items()))


# Rules from git check-ref-format, applied to one name such as a tag or branch.
BAD_REF_RE = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//")


def check_ref_name(name: str) -> None:
    if (not name or BAD_REF_RE.search(name) or name.startswith(("-", "/", "."))
            or name.endswith(("/", ".", ".lock")) or "/." in name or name == "@"):
        raise Exception(f"'{name}' is not a valid ref name")


def ref_create(repo: GitRepository, ref: str, sha: str) -> None:
    with open(repo.repo_file(*ref.split("/"), mkdir=True), "w") as f:
        f.write(sha + "\n")


def ref_delete(repo: GitRepository, ref: str) -> None:
    """Remove a ref, whether it is a loose file or a line in packed-refs."""
    loose = repo.repo_file(*ref.split("/"))
    if os.path.isfile(loose):
        os.remove(loose)

    packed = repo.repo_file("packed-refs")
    if not os.path.isfile(packed):
        return
    with open(packed) as f:
        lines = f.readlines()
    kept, skip_peeled = [], False
    for line in lines:
        if line.startswith("^"):
            # A "^sha" line belongs to the annotated tag on the line above it.
            if not skip_peeled:
                kept.append(line)
            continue
        skip_peeled = line.rstrip("\n").endswith(" " + ref)
        if not skip_peeled:
            kept.append(line)
    if kept != lines:
        with open(packed, "w") as f:
            f.writelines(kept)


def head_set(repo: GitRepository, branch: str | None = None, sha: str | None = None) -> None:
    """Point HEAD at a branch, or detach it at a commit."""
    with open(repo.repo_file("HEAD"), "w") as f:
        f.write(f"ref: refs/heads/{branch}\n" if branch else f"{sha}\n")


def head_branch(repo: GitRepository) -> str | None:
    """Name of the checked-out branch, or None for a detached HEAD."""
    with open(repo.repo_file("HEAD")) as f:
        data = f.read().strip()
    if data.startswith("ref: refs/heads/"):
        return data[len("ref: refs/heads/"):]
    return None
