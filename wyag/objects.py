import hashlib
import os
import zlib


class GitObject:
    """Base class for Git objects: blob, commit, tree, tag."""

    fmt = None

    def __init__(self, data=None):
        if data is not None:
            self.deserialize(data)

    def serialize(self):
        raise NotImplementedError

    def deserialize(self, data):
        raise NotImplementedError


class GitBlob(GitObject):
    fmt = b"blob"

    def serialize(self):
        return self.blobdata

    def deserialize(self, data):
        self.blobdata = data


def object_read(repo, sha):
    """Read object sha from the repository and return a GitObject."""
    path = repo.repo_file("objects", sha[0:2], sha[2:])

    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())

    space = raw.find(b" ")
    fmt = raw[0:space]

    null = raw.find(b"\x00", space)
    size = int(raw[space + 1:null].decode("ascii"))
    if size != len(raw) - null - 1:
        raise Exception(f"Malformed object {sha}: bad length")

    if fmt == b"blob":
        c = GitBlob
    else:
        raise Exception(f"Unknown type {fmt.decode('ascii')} for object {sha}")

    return c(raw[null + 1:])


def object_write(obj, repo=None):
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


def object_find(repo, name, fmt=None, follow=True):
    """Resolve a name (full or unique-prefix hash) to a full hash."""
    prefix_dir = os.path.join(repo.gitdir, "objects", name[0:2])
    if not os.path.isdir(prefix_dir):
        raise Exception(f"No such object {name}")

    matches = [
        name[0:2] + entry
        for entry in os.listdir(prefix_dir)
        if (name[0:2] + entry).startswith(name)
    ]
    if len(matches) != 1:
        raise Exception(f"Name {name} is ambiguous or missing ({len(matches)} matches)")
    return matches[0]
