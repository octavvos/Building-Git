from __future__ import annotations

import hashlib
import os
import re
import zlib

from .refs import ref_resolve
from .repository import GitRepository


class GitObject:
    """Base class for Git objects: blob, commit, tree, tag."""

    fmt: bytes = b""

    def __init__(self, data: bytes | None = None) -> None:
        if data is not None:
            self.deserialize(data)
        else:
            self.init()

    def init(self) -> None:
        pass

    def serialize(self) -> bytes:
        raise NotImplementedError

    def deserialize(self, data: bytes) -> None:
        raise NotImplementedError


class GitBlob(GitObject):
    fmt = b"blob"

    def init(self) -> None:
        self.blobdata = b""

    def serialize(self) -> bytes:
        return self.blobdata

    def deserialize(self, data: bytes) -> None:
        self.blobdata = data


# --- Commits and tags: a list of "key value" lines, a blank line, a message ---

# Keys map to a list of values, because a commit can have several "parent"
# lines. The message is stored under the None key.
Kvlm = dict


def kvlm_parse(raw: bytes) -> Kvlm:
    kvlm: Kvlm = {}
    pos = 0
    while True:
        space = raw.find(b" ", pos)
        newline = raw.find(b"\n", pos)

        # A blank line (or no more spaces) means the headers are over.
        if space < 0 or newline < space:
            if newline != pos:
                raise Exception("Malformed commit: expected blank line before message")
            kvlm[None] = raw[pos + 1:]
            return kvlm

        key = raw[pos:space]

        # A value continues on the next line if that line starts with a space.
        end = space
        while True:
            end = raw.find(b"\n", end + 1)
            if raw[end + 1:end + 2] != b" ":
                break

        value = raw[space + 1:end].replace(b"\n ", b"\n")
        kvlm.setdefault(key, []).append(value)
        pos = end + 1


def kvlm_serialize(kvlm: Kvlm) -> bytes:
    out = b""
    for key, values in kvlm.items():
        if key is None:
            continue
        for value in values:
            out += key + b" " + value.replace(b"\n", b"\n ") + b"\n"
    return out + b"\n" + kvlm[None]


class GitCommit(GitObject):
    fmt = b"commit"

    def init(self) -> None:
        self.kvlm: Kvlm = {}

    def serialize(self) -> bytes:
        return kvlm_serialize(self.kvlm)

    def deserialize(self, data: bytes) -> None:
        self.kvlm = kvlm_parse(data)

    @property
    def parents(self) -> list[str]:
        return [p.decode("ascii") for p in self.kvlm.get(b"parent", [])]

    @property
    def tree(self) -> str:
        return self.kvlm[b"tree"][0].decode("ascii")

    @property
    def message(self) -> str:
        return self.kvlm[None].decode("utf-8", "replace")


class GitTag(GitCommit):
    fmt = b"tag"


# --- Trees: a list of (mode, path, sha) entries ---

class GitTreeLeaf:
    def __init__(self, mode: bytes, path: str, sha: str) -> None:
        self.mode = mode
        self.path = path
        self.sha = sha

    @property
    def type(self) -> str:
        if self.mode.startswith(b"04"):
            return "tree"
        if self.mode.startswith(b"16"):
            return "commit"  # submodule
        return "blob"  # regular file or symlink (12xxxx)


def tree_parse(raw: bytes) -> list[GitTreeLeaf]:
    """Each entry is "<mode> <path>\\0<20-byte binary sha>"."""
    leaves = []
    pos = 0
    while pos < len(raw):
        space = raw.find(b" ", pos)
        mode = raw[pos:space]
        if len(mode) == 5:
            mode = b"0" + mode  # git writes directories as 40000

        null = raw.find(b"\x00", space)
        path = raw[space + 1:null].decode("utf-8")
        sha = raw[null + 1:null + 21].hex()

        leaves.append(GitTreeLeaf(mode, path, sha))
        pos = null + 21
    return leaves


def tree_leaf_sort_key(leaf: GitTreeLeaf) -> str:
    # Git sorts directories as if their name ended with "/".
    return leaf.path + "/" if leaf.type == "tree" else leaf.path


def tree_serialize(leaves: list[GitTreeLeaf]) -> bytes:
    out = b""
    for leaf in sorted(leaves, key=tree_leaf_sort_key):
        out += leaf.mode.lstrip(b"0") + b" " + leaf.path.encode("utf-8")
        out += b"\x00" + bytes.fromhex(leaf.sha)
    return out


class GitTree(GitObject):
    fmt = b"tree"

    def init(self) -> None:
        self.items: list[GitTreeLeaf] = []

    def serialize(self) -> bytes:
        return tree_serialize(self.items)

    def deserialize(self, data: bytes) -> None:
        self.items = tree_parse(data)


OBJECT_TYPES: dict[bytes, type[GitObject]] = {
    cls.fmt: cls for cls in (GitBlob, GitCommit, GitTag, GitTree)
}


# --- Storage ---

def object_read(repo: GitRepository, sha: str) -> GitObject:
    """Read object sha from the repository and return a GitObject."""
    path = repo.repo_file("objects", sha[0:2], sha[2:])
    if not os.path.isfile(path):
        # Objects compressed by git gc or git clone live in packfiles.
        raise Exception(f"Object {sha} not found (packfiles are not supported yet)")

    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())

    space = raw.find(b" ")
    fmt = raw[0:space]

    null = raw.find(b"\x00", space)
    size = int(raw[space + 1:null].decode("ascii"))
    if size != len(raw) - null - 1:
        raise Exception(f"Malformed object {sha}: bad length")

    cls = OBJECT_TYPES.get(fmt)
    if cls is None:
        raise Exception(f"Unknown type {fmt.decode('ascii')} for object {sha}")

    return cls(raw[null + 1:])


def object_write(obj: GitObject, repo: GitRepository | None = None) -> str:
    """Serialize obj, store it in the repository (if given), and return its hash."""
    data = obj.serialize()
    header = obj.fmt + b" " + str(len(data)).encode() + b"\x00"
    result = header + data
    sha = hashlib.sha1(result).hexdigest()

    if repo:
        path = repo.repo_file("objects", sha[0:2], sha[2:], mkdir=True)
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(zlib.compress(result))

    return sha


# --- Name resolution ---

HASH_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")


def object_resolve(repo: GitRepository, name: str) -> list[str]:
    """All hashes a name could refer to: HEAD, a hash prefix, a branch or a tag."""
    candidates = []

    if name == "HEAD":
        sha = ref_resolve(repo, "HEAD")
        return [sha] if sha else []

    if HASH_RE.match(name):
        name_lower = name.lower()
        prefix_dir = repo.repo_dir("objects", name_lower[0:2])
        if prefix_dir:
            for entry in os.listdir(prefix_dir):
                if (name_lower[0:2] + entry).startswith(name_lower):
                    candidates.append(name_lower[0:2] + entry)

    for ref in (name, f"refs/tags/{name}", f"refs/heads/{name}", f"refs/remotes/{name}"):
        if ref.startswith("refs/"):
            sha = ref_resolve(repo, ref)
            if sha:
                candidates.append(sha)

    return list(dict.fromkeys(candidates))


def object_find(repo: GitRepository, name: str, fmt: bytes | None = None,
                follow: bool = True) -> str:
    """Resolve a name to a single hash, optionally peeling it to the given type.

    For example, object_find(repo, "HEAD", b"tree") returns the tree of the
    current commit.
    """
    candidates = object_resolve(repo, name)
    if not candidates:
        raise Exception(f"No such object {name}")
    if len(candidates) > 1:
        raise Exception(f"Name {name} is ambiguous: {', '.join(candidates)}")

    sha = candidates[0]
    if fmt is None:
        return sha

    while True:
        obj = object_read(repo, sha)
        if obj.fmt == fmt:
            return sha
        if not follow:
            raise Exception(f"{name} is a {obj.fmt.decode()}, not a {fmt.decode()}")

        if isinstance(obj, GitTag):
            sha = obj.kvlm[b"object"][0].decode("ascii")
        elif isinstance(obj, GitCommit) and fmt == b"tree":
            sha = obj.tree
        else:
            raise Exception(f"{name} is a {obj.fmt.decode()}, not a {fmt.decode()}")
