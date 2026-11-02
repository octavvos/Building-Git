from __future__ import annotations

import argparse
import heapq
import os
import re
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from . import objects
from .objects import GitCommit, GitTree
from .refs import head_branch, ref_resolve
from .repository import GitRepository, repo_create, repo_find

parser = argparse.ArgumentParser(prog="wyag", description="wyag: a Git implementation in Python.")
subparsers = parser.add_subparsers(title="Commands", dest="command", required=True)

argsp = subparsers.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument("path", metavar="directory", nargs="?", default=".",
                   help="Where to create the repository.")

argsp = subparsers.add_parser("cat-file", help="Print the contents of a repository object.")
argsp.add_argument("-t", dest="show_type", action="store_true", help="Print the object's type instead.")
argsp.add_argument("object", help="The object to display: hash, HEAD, branch or tag.")

argsp = subparsers.add_parser("hash-object", help="Compute an object's hash, optionally storing it.")
argsp.add_argument("path", help="Read the object from this file.")
argsp.add_argument("-w", dest="write", action="store_true",
                   help="Actually write the object into the repository.")

argsp = subparsers.add_parser("log", help="Show commit history.")
argsp.add_argument("commit", nargs="?", default="HEAD", help="Commit to start from.")
argsp.add_argument("-n", dest="max_count", type=int, help="Show at most this many commits.")
argsp.add_argument("--oneline", action="store_true", help="One commit per line.")

argsp = subparsers.add_parser("ls-tree", help="List the contents of a tree.")
argsp.add_argument("-r", dest="recursive", action="store_true", help="Recurse into subtrees.")
argsp.add_argument("tree", help="A tree-ish: tree, commit, HEAD, branch or tag.")

argsp = subparsers.add_parser("checkout", help="Write a commit's files into an empty directory.")
argsp.add_argument("commit", help="The commit or tree to check out.")
argsp.add_argument("path", help="The EMPTY directory to check out into.")


def cmd_init(args: argparse.Namespace) -> None:
    repo_create(args.path)
    print(f"Initialized empty Git repository in {args.path}")


def cmd_cat_file(args: argparse.Namespace) -> None:
    repo = repo_find()
    sha = objects.object_find(repo, args.object)
    obj = objects.object_read(repo, sha)

    if args.show_type:
        print(obj.fmt.decode())
    elif isinstance(obj, GitTree):
        # Raw tree data is binary, so print it the way ls-tree does.
        ls_tree(repo, sha, recursive=False)
    else:
        sys.stdout.buffer.write(obj.serialize())


def cmd_hash_object(args: argparse.Namespace) -> None:
    repo = repo_find() if args.write else None
    with open(args.path, "rb") as fd:
        obj = objects.GitBlob(fd.read())
    print(objects.object_write(obj, repo))


# --- log ---

PERSON_RE = re.compile(rb"^(.*) (\d+) ([+-]\d{4})$")


def parse_person(raw: bytes) -> tuple[str, datetime, str]:
    """Split "Name <email> 1700000000 +0500" into name, time and timezone."""
    m = PERSON_RE.match(raw)
    if not m:
        raise Exception(f"Malformed author line: {raw!r}")
    who, ts, tz = m.group(1).decode("utf-8", "replace"), int(m.group(2)), m.group(3).decode()

    sign = -1 if tz[0] == "-" else 1
    offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    return who, datetime.fromtimestamp(ts, timezone(offset)), tz


def commit_time(commit: GitCommit) -> int:
    return int(parse_person(commit.kvlm[b"committer"][0])[1].timestamp())


def walk_commits(repo: GitRepository, start: str) -> Iterator[tuple[str, GitCommit]]:
    """Yield every commit reachable from start, newest first, like git log."""
    counter = 0
    first = objects.object_read(repo, start)
    heap = [(-commit_time(first), counter, start, first)]
    seen = {start}

    while heap:
        _, _, sha, commit = heapq.heappop(heap)
        yield sha, commit
        for parent in commit.parents:
            if parent in seen:
                continue
            seen.add(parent)
            counter += 1
            obj = objects.object_read(repo, parent)
            heapq.heappush(heap, (-commit_time(obj), counter, parent, obj))


def format_date(when: datetime, tz: str) -> str:
    return f"{when:%a %b} {when.day} {when:%H:%M:%S %Y} {tz}"


def cmd_log(args: argparse.Namespace) -> None:
    repo = repo_find()
    if args.commit == "HEAD" and ref_resolve(repo, "HEAD") is None:
        branch = head_branch(repo) or "HEAD"
        raise Exception(f"your current branch '{branch}' does not have any commits yet")

    start = objects.object_find(repo, args.commit, fmt=b"commit")

    for i, (sha, commit) in enumerate(walk_commits(repo, start)):
        if args.max_count is not None and i >= args.max_count:
            break

        if args.oneline:
            first_line = commit.message.split("\n", 1)[0]
            print(f"{sha[:7]} {first_line}")
            continue

        if i:
            print()
        print(f"commit {sha}")
        if len(commit.parents) > 1:
            print("Merge: " + " ".join(p[:7] for p in commit.parents))
        who, when, tz = parse_person(commit.kvlm[b"author"][0])
        print(f"Author: {who}")
        print(f"Date:   {format_date(when, tz)}")
        print()
        for line in commit.message.rstrip("\n").split("\n"):
            print(f"    {line}")


# --- ls-tree ---

def ls_tree(repo: GitRepository, sha: str, recursive: bool, prefix: str = "") -> None:
    tree = objects.object_read(repo, sha)
    for leaf in tree.items:
        path = f"{prefix}/{leaf.path}" if prefix else leaf.path
        if recursive and leaf.type == "tree":
            ls_tree(repo, leaf.sha, recursive, path)
        else:
            print(f"{leaf.mode.decode()} {leaf.type} {leaf.sha}\t{path}")


def cmd_ls_tree(args: argparse.Namespace) -> None:
    repo = repo_find()
    sha = objects.object_find(repo, args.tree, fmt=b"tree")
    ls_tree(repo, sha, args.recursive)


# --- checkout ---

def tree_checkout(repo: GitRepository, tree: GitTree, path: str) -> None:
    for leaf in tree.items:
        # A malicious tree could otherwise write outside the target directory.
        if leaf.path in ("", ".", "..", ".git") or "/" in leaf.path or "\\" in leaf.path:
            raise Exception(f"Refusing to check out unsafe path {leaf.path!r}")

        dest = os.path.join(path, leaf.path)
        if leaf.type == "tree":
            os.mkdir(dest)
            tree_checkout(repo, objects.object_read(repo, leaf.sha), dest)
        elif leaf.type == "blob":
            data = objects.object_read(repo, leaf.sha).serialize()
            if leaf.mode.startswith(b"12"):
                os.symlink(data.decode("utf-8"), dest)
            else:
                with open(dest, "wb") as f:
                    f.write(data)
                if leaf.mode == b"100755":
                    os.chmod(dest, 0o755)
        # Submodules (mode 160000) point to another repository; skip them.


def cmd_checkout(args: argparse.Namespace) -> None:
    repo = repo_find()
    sha = objects.object_find(repo, args.commit, fmt=b"tree")

    if os.path.exists(args.path):
        if not os.path.isdir(args.path):
            raise Exception(f"Not a directory {args.path}")
        if os.listdir(args.path):
            raise Exception(f"Not empty {args.path}")
    else:
        os.makedirs(args.path)

    tree_checkout(repo, objects.object_read(repo, sha), os.path.realpath(args.path))


COMMANDS = {
    "init": cmd_init,
    "cat-file": cmd_cat_file,
    "hash-object": cmd_hash_object,
    "log": cmd_log,
    "ls-tree": cmd_ls_tree,
    "checkout": cmd_checkout,
}


def main(argv: list[str] | None = None) -> None:
    args = parser.parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except Exception as e:
        sys.exit(f"fatal: {e}")


if __name__ == "__main__":
    main()
