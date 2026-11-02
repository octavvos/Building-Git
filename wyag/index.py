"""The staging area: .git/index.

The index lists every tracked file with its blob hash, mode and the stat()
data from when it was added, which lets status skip re-hashing unchanged files.

Layout: "DIRC", version, entry count, the entries sorted by path, optional
extensions, and a SHA-1 of everything before it.
"""

from __future__ import annotations

import hashlib
import os
import stat
import struct

from .repository import GitRepository

# ctime s/ns, mtime s/ns, dev, ino, mode, uid, gid, size, sha, flags
ENTRY_HEAD = struct.Struct(">10I20sH")

FLAG_EXTENDED = 0x4000
FLAG_STAGE_SHIFT = 12
NAME_MASK = 0x0FFF


class GitIndexEntry:
    def __init__(self, path: str, sha: str, mode: int, stage: int = 0,
                 ctime: tuple[int, int] = (0, 0), mtime: tuple[int, int] = (0, 0),
                 dev: int = 0, ino: int = 0, uid: int = 0, gid: int = 0, size: int = 0,
                 extended_flags: int = 0) -> None:
        self.path = path
        self.sha = sha
        self.mode = mode
        self.stage = stage
        self.ctime = ctime
        self.mtime = mtime
        self.dev = dev
        self.ino = ino
        self.uid = uid
        self.gid = gid
        self.size = size
        # Version 3 flags such as intent-to-add and skip-worktree.
        self.extended_flags = extended_flags

    @classmethod
    def from_stat(cls, path: str, sha: str, mode: int, st: os.stat_result) -> GitIndexEntry:
        def u32(n: int) -> int:
            return n & 0xFFFFFFFF

        return cls(
            path, sha, mode,
            ctime=(u32(int(st.st_ctime)), st.st_ctime_ns % 1_000_000_000),
            mtime=(u32(int(st.st_mtime)), st.st_mtime_ns % 1_000_000_000),
            dev=u32(st.st_dev), ino=u32(st.st_ino), uid=u32(st.st_uid), gid=u32(st.st_gid),
            size=u32(st.st_size),
        )

    @property
    def mode_bytes(self) -> bytes:
        return f"{self.mode:06o}".encode()

    def stat_matches(self, st: os.stat_result) -> bool:
        """True if the file looks untouched since it was added."""
        return (self.size == st.st_size & 0xFFFFFFFF
                and self.mtime == (int(st.st_mtime) & 0xFFFFFFFF,
                                   st.st_mtime_ns % 1_000_000_000))


class GitIndex:
    def __init__(self, entries: list[GitIndexEntry] | None = None) -> None:
        self.entries = entries or []

    def by_path(self) -> dict[str, GitIndexEntry]:
        return {e.path: e for e in self.entries if e.stage == 0}

    def sort(self) -> None:
        self.entries.sort(key=lambda e: (e.path.encode("utf-8"), e.stage))


def file_mode(st: os.stat_result, trust_exec_bit: bool = True,
              old_mode: int | None = None) -> int:
    """The git mode for a file on disk."""
    if stat.S_ISLNK(st.st_mode):
        return 0o120000
    if not trust_exec_bit and old_mode in (0o100644, 0o100755):
        return old_mode  # core.filemode=false: keep what the index says
    if trust_exec_bit and st.st_mode & 0o100:
        return 0o100755
    return 0o100644


def index_read(repo: GitRepository) -> GitIndex:
    path = repo.repo_file("index")
    if not os.path.exists(path):
        return GitIndex()

    with open(path, "rb") as f:
        raw = f.read()

    if len(raw) < 32 or hashlib.sha1(raw[:-20]).digest() != raw[-20:]:
        raise Exception("index file is corrupt (bad checksum)")
    if raw[:4] != b"DIRC":
        raise Exception("index file is corrupt (bad signature)")

    version, count = struct.unpack(">II", raw[4:12])
    if version not in (2, 3):
        raise Exception(f"index version {version} is not supported (only 2 and 3)")

    entries = []
    pos = 12
    for _ in range(count):
        (ctime_s, ctime_ns, mtime_s, mtime_ns, dev, ino, mode, uid, gid, size,
         sha, flags) = ENTRY_HEAD.unpack_from(raw, pos)
        start = pos
        pos += ENTRY_HEAD.size

        extended = 0
        if flags & FLAG_EXTENDED:
            (extended,) = struct.unpack(">H", raw[pos:pos + 2])
            pos += 2

        null = raw.index(b"\x00", pos)
        name = raw[pos:null].decode("utf-8")
        # Entries are padded with 1-8 NUL bytes to a multiple of 8.
        pos = start + ((null - start) // 8 + 1) * 8

        entries.append(GitIndexEntry(
            name, sha.hex(), mode, stage=(flags >> FLAG_STAGE_SHIFT) & 0x3,
            ctime=(ctime_s, ctime_ns), mtime=(mtime_s, mtime_ns),
            dev=dev, ino=ino, uid=uid, gid=gid, size=size, extended_flags=extended,
        ))

    # Extensions: a 4-byte signature and a length. Optional ones start with an
    # uppercase letter and can be dropped; lowercase ones change how the index
    # must be read, so we can't safely continue.
    while pos < len(raw) - 20:
        signature = raw[pos:pos + 4]
        (length,) = struct.unpack(">I", raw[pos + 4:pos + 8])
        if not signature[:1].isupper():
            raise Exception(f"index extension {signature.decode(errors='replace')} "
                            "is not supported")
        pos += 8 + length

    return GitIndex(entries)


def index_write(repo: GitRepository, index: GitIndex) -> None:
    """Write the index atomically via index.lock, like git does.

    Extensions are not written: git rebuilds optional ones (such as the
    cached tree) as needed, and a stale cached tree would be wrong anyway.
    """
    index.sort()
    version = 3 if any(e.extended_flags for e in index.entries) else 2

    out = bytearray(b"DIRC" + struct.pack(">II", version, len(index.entries)))
    for e in index.entries:
        name = e.path.encode("utf-8")
        flags = (e.stage << FLAG_STAGE_SHIFT) | min(len(name), NAME_MASK)
        if e.extended_flags:
            flags |= FLAG_EXTENDED

        start = len(out)
        out += ENTRY_HEAD.pack(e.ctime[0], e.ctime[1], e.mtime[0], e.mtime[1], e.dev, e.ino,
                               e.mode, e.uid, e.gid, e.size, bytes.fromhex(e.sha), flags)
        if e.extended_flags:
            out += struct.pack(">H", e.extended_flags)
        out += name
        out += b"\x00" * (8 - (len(out) - start) % 8)

    out += hashlib.sha1(out).digest()

    lock = repo.repo_file("index.lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise Exception(f"Unable to create {lock}: File exists. "
                        "Another git process seems to be running.") from None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(out)
        os.replace(lock, repo.repo_file("index"))
    except BaseException:
        if os.path.exists(lock):
            os.remove(lock)
        raise
