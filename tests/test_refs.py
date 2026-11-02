import pytest
from helpers import git, git_text, needs_git, wyag

from wyag import objects
from wyag.__main__ import cleanup_message, main
from wyag.config import identity, local_timezone
from wyag.refs import check_ref_name
from wyag.repository import GitRepository


@pytest.fixture
def merge_repo(git_repo):
    """git_repo with a side branch merged in, a lightweight tag and a packed ref."""
    git("checkout", "-q", "-b", "side", "HEAD~1", cwd=git_repo)
    (git_repo / "side.txt").write_text("side\n")
    git("add", "side.txt", cwd=git_repo)
    git("commit", "-q", "-m", "side", cwd=git_repo)
    git("checkout", "-q", "master", cwd=git_repo)
    git("merge", "-q", "--no-ff", "side", "-m", "merge side", cwd=git_repo)
    git("tag", "light", "HEAD~2", cwd=git_repo)
    return git_repo


REVISIONS = [
    "HEAD", "HEAD~1", "HEAD~2", "HEAD^", "HEAD^1", "HEAD^2", "HEAD^2~1", "HEAD~0", "HEAD^0",
    "HEAD^{tree}", "HEAD^{commit}", "HEAD~1^{tree}", "v1", "v1^{}", "v1^{commit}",
    "v1^{tree}", "v1~1", "light", "master", "side", "refs/heads/side", "refs/tags/v1",
]


@needs_git
@pytest.mark.parametrize("rev", REVISIONS)
def test_rev_parse_matches_git(merge_repo, monkeypatch, capsysbinary, rev):
    out = wyag("rev-parse", rev, cwd=merge_repo, monkeypatch=monkeypatch,
               capsysbinary=capsysbinary)
    assert out == git("rev-parse", rev, cwd=merge_repo)


@needs_git
def test_rev_parse_short_and_many(merge_repo, monkeypatch, capsysbinary):
    out = wyag("rev-parse", "--short", "HEAD", "side", cwd=merge_repo,
               monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    head, side = git_text("rev-parse", "HEAD", "side", cwd=merge_repo).split()
    assert out.decode().split() == [head[:7], side[:7]]


@needs_git
@pytest.mark.parametrize("rev, error", [
    ("HEAD^3", "has no parent number 3"),
    ("HEAD~100", "has no parent number 1"),
    ("nope", "No such object nope"),
    ("HEAD^{blob}", "not a blob"),
    ("HEAD^^{", "Invalid revision"),
])
def test_rev_parse_errors(merge_repo, monkeypatch, rev, error):
    monkeypatch.chdir(merge_repo)
    with pytest.raises(SystemExit, match=error):
        main(["rev-parse", rev])


@needs_git
def test_ambiguous_name(merge_repo):
    repo = GitRepository(str(merge_repo))
    head = git_text("rev-parse", "HEAD", cwd=merge_repo)
    side = git_text("rev-parse", "side", cwd=merge_repo)
    # A branch named like a hash prefix of a different commit.
    git("branch", head[:8], "side", cwd=merge_repo)
    with pytest.raises(Exception, match="ambiguous"):
        objects.object_find(repo, head[:8])
    assert side != head


@needs_git
@pytest.mark.parametrize("flags", [[], ["--heads"], ["--tags"], ["--heads", "--tags"]])
def test_show_ref_matches_git(merge_repo, monkeypatch, capsysbinary, flags):
    out = wyag("show-ref", *flags, cwd=merge_repo, monkeypatch=monkeypatch,
               capsysbinary=capsysbinary)
    assert out == git("show-ref", *flags, cwd=merge_repo)


@needs_git
def test_show_ref_with_packed_refs(merge_repo, monkeypatch, capsysbinary):
    git("pack-refs", "--all", cwd=merge_repo)
    # A loose ref written after packing must win over the packed one.
    git("update-ref", "refs/heads/side", "HEAD", cwd=merge_repo)
    out = wyag("show-ref", cwd=merge_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git("show-ref", cwd=merge_repo)


# --- tag ---

@needs_git
def test_tag_list(merge_repo, monkeypatch, capsysbinary):
    out = wyag("tag", cwd=merge_repo, monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    assert out == git("tag", cwd=merge_repo)


@needs_git
def test_lightweight_tag(merge_repo, monkeypatch, capsysbinary):
    wyag("tag", "w", "HEAD~1", cwd=merge_repo, monkeypatch=monkeypatch,
         capsysbinary=capsysbinary)
    assert git_text("rev-parse", "w", cwd=merge_repo) == git_text("rev-parse", "HEAD~1",
                                                                   cwd=merge_repo)


@needs_git
@pytest.mark.parametrize("message", ["oddiy", "  bo'sh joylar  \n\n\n\nikkinchi qator  \n\n"])
def test_annotated_tag_is_identical_to_git(merge_repo, monkeypatch, capsysbinary, message):
    """Same identity, date and message must give the very same tag hash as git."""
    wyag("tag", "-a", "rel/1.0", "-m", message, "HEAD~1", cwd=merge_repo,
         monkeypatch=monkeypatch, capsysbinary=capsysbinary)
    ours = git_text("rev-parse", "rel/1.0", cwd=merge_repo)

    git("tag", "-d", "rel/1.0", cwd=merge_repo)
    git("tag", "-a", "rel/1.0", "-m", message, "HEAD~1", cwd=merge_repo)
    assert ours == git_text("rev-parse", "rel/1.0", cwd=merge_repo)
    git("fsck", "--strict", cwd=merge_repo)


@needs_git
@pytest.mark.parametrize("args, error", [
    (["tag", "v1"], "already exists"),
    (["tag", "-a", "x"], "needs a message"),
    (["tag", "-m", "  \n ", "x"], "message is empty"),
    (["tag", "bad..name"], "not a valid ref name"),
    (["tag", "x", "nope"], "No such object"),
])
def test_tag_errors(merge_repo, monkeypatch, args, error):
    monkeypatch.chdir(merge_repo)
    with pytest.raises(SystemExit, match=error):
        main(args)


@pytest.mark.parametrize("name", ["v1", "release/1.0", "a-b_c", "x.y"])
def test_valid_ref_names(name):
    check_ref_name(name)


@pytest.mark.parametrize("name", ["", "a..b", "a b", "a~1", "a^", "a:b", "a?", "a*", "a[",
                                  "a\\b", "-a", "/a", "a/", "a.", "a.lock", ".a", "a/.b",
                                  "a//b", "a@{1}", "@"])
def test_invalid_ref_names(name):
    with pytest.raises(Exception, match="not a valid ref name"):
        check_ref_name(name)


@pytest.mark.parametrize("raw, clean", [
    ("salom", "salom\n"),
    ("\n\n  salom  \n", "  salom\n"),
    ("a\n\n\n\nb\n\n", "a\n\nb\n"),
    ("   \n  \n", ""),
])
def test_cleanup_message(raw, clean):
    assert cleanup_message(raw) == clean


# --- identity ---

def test_identity_from_environment(repo):
    assert identity(repo, "AUTHOR") == b"Test User <test@example.com> 1700000000 +0500"


def test_identity_from_config(repo, monkeypatch, tmp_path):
    for key in ("GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE"):
        monkeypatch.delenv(key)
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Light\n\temail = light@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))

    who = identity(repo, "COMMITTER").decode()
    assert who.startswith("Light <light@example.com> ")
    assert who.endswith(local_timezone())


def test_repo_config_beats_global(repo, monkeypatch, tmp_path):
    monkeypatch.delenv("GIT_AUTHOR_NAME")
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tname = Global\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    with open(repo.repo_file("config"), "a") as f:
        f.write("[user]\n\tname = Local\n")

    repo = GitRepository(repo.worktree)
    assert identity(repo, "AUTHOR").startswith(b"Local <")


def test_missing_identity(repo, monkeypatch):
    monkeypatch.delenv("GIT_AUTHOR_NAME")
    with pytest.raises(Exception, match="Please tell me who you are"):
        identity(repo, "AUTHOR")


def test_bad_date_format(repo, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_DATE", "yesterday")
    with pytest.raises(Exception, match="Unsupported date format"):
        identity(repo, "AUTHOR")
