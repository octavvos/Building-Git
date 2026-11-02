import glob
import os
import struct

import pytest
from helpers import git, git_text, needs_git, wyag

from wyag import objects
from wyag.pack import (
    OBJ_OFS_DELTA,
    OBJ_REF_DELTA,
    PackIndex,
    apply_delta,
    read_type_and_size,
)
from wyag.repository import GitRepository

pytestmark = needs_git


@pytest.fixture
def history_repo(git_repo):
    """git_repo plus 40 small edits to one file, so packing produces delta chains."""
    lines = [f"line {i}\n" for i in range(200)]
    for i in range(40):
        lines[i * 5] = f"changed in commit {i}\n"
        (git_repo / "big.txt").write_text("".join(lines))
        git("add", "big.txt", cwd=git_repo)
        git("commit", "-q", "-m", f"edit {i}", cwd=git_repo)
    return git_repo


def all_git_objects(path):
    """{sha: (type, content)} for every object, straight from git."""
    raw = git("cat-file", "--batch-all-objects", "--batch", cwd=path)
    result = {}
    pos = 0
    while pos < len(raw):
        header_end = raw.index(b"\n", pos)
        sha, obj_type, size = raw[pos:header_end].split()
        start = header_end + 1
        end = start + int(size)
        result[sha.decode()] = (obj_type, raw[start:end])
        pos = end + 1
    return result


def pack_object_types(path):
    """Count how each object in the repository's packs is stored."""
    counts = {}
    for idx_path in glob.glob(str(path / ".git" / "objects" / "pack" / "*.idx")):
        index = PackIndex(idx_path)
        with open(idx_path[:-4] + ".pack", "rb") as f:
            for offset in index.offsets:
                f.seek(offset)
                obj_type, _ = read_type_and_size(f)
                counts[obj_type] = counts.get(obj_type, 0) + 1
    return counts


def assert_reads_everything(path):
    repo = GitRepository(str(path))
    expected = all_git_objects(path)
    assert expected
    for sha, (obj_type, content) in expected.items():
        assert objects.object_read_raw(repo, sha) == (obj_type, content), sha


def test_reads_all_objects_after_gc(history_repo):
    git("gc", "-q", cwd=history_repo)
    assert not glob.glob(str(history_repo / ".git" / "objects" / "??" / "*"))
    assert pack_object_types(history_repo).get(OBJ_OFS_DELTA, 0) > 0
    assert_reads_everything(history_repo)


def test_deep_delta_chains(history_repo):
    git("repack", "-adf", "--depth=50", "--window=250", cwd=history_repo)
    stats = git("verify-pack", "-v",
                *glob.glob(str(history_repo / ".git/objects/pack/*.idx")), cwd=history_repo)
    assert b"chain length = 10" in stats
    assert_reads_everything(history_repo)


def test_ref_deltas(history_repo):
    git("-c", "repack.useDeltaBaseOffset=false", "repack", "-adf", cwd=history_repo)
    assert pack_object_types(history_repo).get(OBJ_REF_DELTA, 0) > 0
    assert_reads_everything(history_repo)


def test_large_offset_table(history_repo):
    """Packs over 2 GiB store offsets in a 64-bit table. index-pack can be told
    to use that table for every object past 1000 bytes, which tests the code
    without 2 GiB of data."""
    git("repack", "-adq", cwd=history_repo)
    (pack,) = glob.glob(str(history_repo / ".git/objects/pack/*.pack"))
    for old in glob.glob(pack[:-5] + ".*"):
        if not old.endswith(".pack"):
            os.remove(old)
    git("index-pack", "--index-version=2,1000", pack, cwd=history_repo)

    with open(pack[:-5] + ".idx", "rb") as f:
        data = f.read()
    count = struct.unpack(">I", data[8 + 255 * 4:8 + 256 * 4])[0]
    offsets_start = 8 + 1024 + 24 * count
    flags = [struct.unpack(">I", data[offsets_start + 4 * i:offsets_start + 4 * i + 4])[0]
             & 0x80000000 for i in range(count)]
    assert any(flags) and not all(flags)

    assert_reads_everything(history_repo)


def test_loose_and_packed_together(history_repo, monkeypatch, capsysbinary):
    git("gc", "-q", cwd=history_repo)
    (history_repo / "new.txt").write_text("loose\n")
    git("add", "new.txt", cwd=history_repo)
    git("commit", "-q", "-m", "after gc", cwd=history_repo)

    out = wyag("log", "--oneline", cwd=history_repo,
               monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git("log", "--oneline", "--no-decorate", cwd=history_repo)

    out = wyag("ls-tree", "-r", "HEAD~20", cwd=history_repo,
               monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git("ls-tree", "-r", "HEAD~20", cwd=history_repo)


def test_short_hash_found_in_pack(history_repo):
    git("gc", "-q", cwd=history_repo)
    repo = GitRepository(str(history_repo))
    head = git_text("rev-parse", "HEAD", cwd=history_repo)
    assert objects.object_find(repo, head[:7]) == head
    assert objects.object_find(repo, head[:9]) == head  # odd length prefix


def test_missing_object(history_repo):
    git("gc", "-q", cwd=history_repo)
    repo = GitRepository(str(history_repo))
    with pytest.raises(Exception, match="not found"):
        objects.object_read(repo, "0" * 40)


# --- delta format, without git ---

def test_apply_delta_copy_and_insert():
    base = b"Hello, world!"
    # sizes 13 -> 15; copy 7 bytes from offset 0, insert "there", copy "!" back
    delta = bytes([13, 13]) + bytes([0x90, 7]) + bytes([5]) + b"there" + bytes([0x91, 12, 1])
    assert apply_delta(base, delta) == b"Hello, there!"


def test_apply_delta_size_zero_means_64k():
    base = bytes(range(256)) * 300  # 76800 bytes
    size = len(base)
    varint = bytes([size & 0x7F | 0x80, (size >> 7) & 0x7F | 0x80, size >> 14])
    target = bytes([0x80, 0x80, 0x04])  # 65536
    delta = varint + target + bytes([0x80])  # copy, offset 0, size 0
    assert apply_delta(base, delta) == base[:0x10000]


@pytest.mark.parametrize("delta, error", [
    (bytes([3, 3, 0]), "Invalid delta instruction"),
    (bytes([9, 3, 3]) + b"abc", "base size mismatch"),
    (bytes([3, 4, 3]) + b"abc", "result size mismatch"),
])
def test_apply_delta_rejects_bad_input(delta, error):
    with pytest.raises(Exception, match=error):
        apply_delta(b"abc", delta)


def test_unsupported_index_version(tmp_path):
    path = tmp_path / "x.idx"
    path.write_bytes(b"\377tOc" + struct.pack(">I", 3) + bytes(1024))
    with pytest.raises(Exception, match="only version 2"):
        PackIndex(str(path))
