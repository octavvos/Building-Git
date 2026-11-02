"""Reading objects from packfiles (.git/objects/pack/*.pack + *.idx).

git gc and git clone store most objects in packfiles: one file holding many
zlib-compressed objects, where an object may be stored as a delta against
another object to save space. The .idx file next to each pack maps hashes to
offsets in the pack.
"""

from __future__ import annotations

import bisect
import os
import struct
import zlib
from collections.abc import Callable

# Object types as stored in a pack header.
OBJ_COMMIT, OBJ_TREE, OBJ_BLOB, OBJ_TAG = 1, 2, 3, 4
OBJ_OFS_DELTA, OBJ_REF_DELTA = 6, 7

TYPE_NAMES = {OBJ_COMMIT: b"commit", OBJ_TREE: b"tree", OBJ_BLOB: b"blob", OBJ_TAG: b"tag"}

IDX_MAGIC = b"\377tOc"


class PackIndex:
    """A parsed version 2 .idx file."""

    def __init__(self, path: str) -> None:
        with open(path, "rb") as f:
            data = f.read()

        if data[:4] != IDX_MAGIC or struct.unpack(">I", data[4:8])[0] != 2:
            raise Exception(f"Unsupported pack index {path} (only version 2 is supported)")

        # 256 cumulative counts: fanout[b] = number of hashes whose first byte is <= b.
        fanout_end = 8 + 256 * 4
        count = struct.unpack(">I", data[fanout_end - 4:fanout_end])[0]

        shas_start = fanout_end
        crc_start = shas_start + 20 * count
        offsets_start = crc_start + 4 * count
        large_start = offsets_start + 4 * count

        self.shas: list[bytes] = [
            data[shas_start + 20 * i:shas_start + 20 * (i + 1)] for i in range(count)
        ]
        self.offsets: list[int] = []
        for i in range(count):
            (offset,) = struct.unpack(">I", data[offsets_start + 4 * i:offsets_start + 4 * i + 4])
            # Packs over 2 GiB keep big offsets in a separate 64-bit table.
            if offset & 0x80000000:
                pos = large_start + 8 * (offset & 0x7FFFFFFF)
                (offset,) = struct.unpack(">Q", data[pos:pos + 8])
            self.offsets.append(offset)

    def find(self, sha: str) -> int | None:
        """Offset of sha in the pack, or None if this pack doesn't have it."""
        key = bytes.fromhex(sha)
        i = bisect.bisect_left(self.shas, key)
        if i < len(self.shas) and self.shas[i] == key:
            return self.offsets[i]
        return None

    def with_prefix(self, prefix: str) -> list[str]:
        """All hashes starting with the given hex prefix."""
        # Compare on the even-length part with bisect, then filter by full prefix.
        even = bytes.fromhex(prefix[: len(prefix) // 2 * 2])
        i = bisect.bisect_left(self.shas, even)
        found = []
        while i < len(self.shas) and self.shas[i].startswith(even):
            hexsha = self.shas[i].hex()
            if hexsha.startswith(prefix):
                found.append(hexsha)
            i += 1
        return found


class Pack:
    def __init__(self, pack_path: str) -> None:
        self.path = pack_path
        self.index = PackIndex(pack_path[: -len(".pack")] + ".idx")

    def read_at(self, offset: int,
                read_by_sha: Callable[[str], tuple[bytes, bytes]]) -> tuple[bytes, bytes]:
        """Return (type, data) of the object at offset, resolving delta chains.

        read_by_sha is used for REF_DELTA bases, which may live elsewhere.
        """
        deltas: list[bytes] = []
        with open(self.path, "rb") as f:
            while True:
                f.seek(offset)
                obj_type, _ = read_type_and_size(f)

                if obj_type == OBJ_OFS_DELTA:
                    base_offset = offset - read_ofs_delta_offset(f)
                    deltas.append(decompress(f))
                    offset = base_offset
                elif obj_type == OBJ_REF_DELTA:
                    base_sha = f.read(20).hex()
                    deltas.append(decompress(f))
                    fmt, data = read_by_sha(base_sha)
                    break
                elif obj_type in TYPE_NAMES:
                    fmt, data = TYPE_NAMES[obj_type], decompress(f)
                    break
                else:
                    raise Exception(f"Bad object type {obj_type} in {self.path}")

        # The delta closest to the base was read last, so apply in reverse.
        for delta in reversed(deltas):
            data = apply_delta(data, delta)
        return fmt, data


def read_type_and_size(f) -> tuple[int, int]:
    """Object header: 3-bit type and a variable-length size."""
    byte = f.read(1)[0]
    obj_type = (byte >> 4) & 0x7
    size = byte & 0x0F
    shift = 4
    while byte & 0x80:
        byte = f.read(1)[0]
        size |= (byte & 0x7F) << shift
        shift += 7
    return obj_type, size


def read_ofs_delta_offset(f) -> int:
    """How far back the base object is. Each continuation byte adds 1 before
    shifting, so that every value has exactly one encoding."""
    byte = f.read(1)[0]
    offset = byte & 0x7F
    while byte & 0x80:
        byte = f.read(1)[0]
        offset = ((offset + 1) << 7) | (byte & 0x7F)
    return offset


def decompress(f) -> bytes:
    """Decompress one zlib stream starting at the current file position."""
    d = zlib.decompressobj()
    out = []
    while not d.eof:
        chunk = f.read(8192)
        if not chunk:
            raise Exception("Truncated packfile")
        out.append(d.decompress(chunk))
    return b"".join(out)


def read_varint(delta: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = delta[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def apply_delta(base: bytes, delta: bytes) -> bytes:
    """Rebuild an object from its base and a list of copy/insert instructions."""
    base_size, pos = read_varint(delta, 0)
    target_size, pos = read_varint(delta, pos)
    if base_size != len(base):
        raise Exception("Delta base size mismatch")

    out = bytearray()
    while pos < len(delta):
        op = delta[pos]
        pos += 1
        if op & 0x80:
            # Copy from base: bits 0-3 say which offset bytes follow, bits 4-6 size bytes.
            offset = size = 0
            for i in range(4):
                if op & (1 << i):
                    offset |= delta[pos] << (8 * i)
                    pos += 1
            for i in range(3):
                if op & (1 << (4 + i)):
                    size |= delta[pos] << (8 * i)
                    pos += 1
            if size == 0:
                size = 0x10000
            out += base[offset:offset + size]
        elif op:
            # Insert the next op bytes literally.
            out += delta[pos:pos + op]
            pos += op
        else:
            raise Exception("Invalid delta instruction 0")

    if len(out) != target_size:
        raise Exception("Delta result size mismatch")
    return bytes(out)


def repo_packs(gitdir: str) -> list[Pack]:
    pack_dir = os.path.join(gitdir, "objects", "pack")
    if not os.path.isdir(pack_dir):
        return []
    return [
        Pack(os.path.join(pack_dir, name))
        for name in sorted(os.listdir(pack_dir))
        if name.endswith(".pack") and os.path.exists(os.path.join(pack_dir, name[:-5] + ".idx"))
    ]
