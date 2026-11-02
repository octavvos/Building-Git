# wyag — Git in Python

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)
![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/octavvos/Building-Git/badges/coverage.json)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**English** | [O'zbekcha](README.uz.md)

A from-scratch implementation of Git's core in pure Python, built to understand how
Git actually stores and reads history. It started from
[Write yourself a Git!](https://wyag.thb.lt/) and goes further: packfiles, the
binary index, `.gitignore` and revision syntax like `HEAD~2^{tree}`.

wyag reads and writes the same on-disk format as Git, so it works on real repositories,
including freshly cloned ones. The test suite runs every command next to real `git` and
checks that the output is the same byte for byte; commits and tags that wyag creates
have the **same hashes** git would produce and pass `git fsck --strict`.

## Commands

| Command | What it does |
|---|---|
| `init [dir]` | Create an empty repository |
| `add [-f] path...` | Stage files or whole directories (respects `.gitignore`, stages deletions) |
| `rm [--cached] [-r] path...` | Unstage and delete files; refuses to drop uncommitted changes |
| `commit -m msg` | Record the index as a commit and move the current branch |
| `status [-s]` | Staged, unstaged and untracked changes; `-s` matches `git status --porcelain` |
| `ls-files [-s]` | List the index |
| `log [-n N] [--oneline] [rev]` | History, newest first, including merges |
| `ls-tree [-r] rev` | List a tree |
| `cat-file [-t] rev` | Print an object, or its type |
| `hash-object [-w] file` | Hash a file as a blob, optionally storing it |
| `checkout rev dir` | Write a commit's files into an empty directory |
| `rev-parse [--short] rev...` | Resolve revisions to hashes |
| `show-ref [--heads] [--tags]` | List refs |
| `tag [-a] [-m msg] [name [rev]]` | List tags, or create a lightweight or annotated one |

A `rev` can be a full or short hash, `HEAD`, a branch, a tag, or any of those followed by
`~N`, `^N` or `^{type}` — for example `HEAD~3`, `HEAD^2` (a merge's second parent) or
`v1.0^{tree}`.

## Quick start

Requires Python 3.9+. No runtime dependencies.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

wyag init demo && cd demo
echo "hello" > hello.txt
wyag add .
wyag commit -m "First commit"
wyag log

# It also works on any existing git repository, packed or not
cd ~/some-project
wyag log --oneline -n 5
wyag status
```

wyag takes the author from `user.name` / `user.email` in your git config, or from the
`GIT_AUTHOR_*` / `GIT_COMMITTER_*` environment variables, just like git.

## How it works

**Objects.** Git is a content-addressed store. Every object is saved as
`<type> <size>\0<content>`, zlib-compressed, and named by its SHA-1.
Blobs hold file contents; trees list a directory as `<mode> <name>\0<20-byte sha>`
entries (sorted as if directory names ended in `/`, or the hash comes out wrong);
commits and tags are `key value` header lines, a blank line, then the message.

**Packfiles.** `git gc` and `git clone` pack objects into a single file, many of them
stored as *deltas* — copy/insert instructions against another object. wyag reads the
`.idx` (v2, including the 64-bit offset table for packs over 2 GiB), finds the object,
follows `OFS_DELTA` / `REF_DELTA` chains back to a full object and replays them.

**The index.** `.git/index` is a binary file listing every staged path with its blob
hash, mode and `stat()` data. `status` uses the `stat()` data to skip re-hashing
unchanged files, but re-hashes anything modified in the same second the index was written
("racy git"), since timestamps can't tell those changes apart. Writes go through
`index.lock` and an atomic rename, as in git.

**Committing.** `commit` turns the flat index into nested tree objects, deepest
directories first, then writes a commit pointing at the root tree and the previous `HEAD`.

**History.** `log` walks the commit graph with a priority queue ordered by commit time,
which lists merge histories in the same order as `git log`.

## Project layout

```
wyag/
├── __main__.py     # CLI: argument parsing and output formatting
├── repository.py   # GitRepository, init, finding the repository root
├── objects.py      # Blob, commit, tree, tag; reading/writing; revision syntax
├── pack.py         # Packfile and .idx reading, delta decoding
├── refs.py         # HEAD, branches, tags, packed-refs, ref name rules
├── index.py        # Reading and writing .git/index (v2 and v3)
├── ignore.py       # .gitignore, info/exclude and core.excludesFile rules
├── worktree.py     # status, add, rm, commit
└── config.py       # git config lookup and author identity
tests/              # ~180 tests, most of them comparing against real git
```

## Tests

```bash
pytest       # runs with coverage and fails below 90%
ruff check .
```

The tests build repositories with real `git` (merges, annotated tags, symlinks,
executables, deep delta chains, merge conflicts, submodule entries) and compare wyag's
output and hashes with git's. `.gitignore` handling is checked against
`git check-ignore`. CI runs everything on Python 3.9, 3.11 and 3.13.

## Limitations

- No `branch`, `switch`, `merge`, `diff`, `fetch` or `push` yet; `checkout` only writes a
  snapshot into an empty directory.
- `status` doesn't detect renames, and shows every conflict as `UU`.
- Index version 4 and the split index are not supported (git doesn't use them by default).
- SHA-1 repositories only; commit messages come from `-m` only.

## License

[MIT](LICENSE)
