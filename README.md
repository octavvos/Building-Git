# wyag — Git in Python

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)
![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/octavvos/Building-Git/badges/coverage.json)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**English** | [O'zbekcha](README.uz.md)

A from-scratch implementation of Git in pure Python, built to understand how Git really
stores history, tracks changes and merges branches. It started from
[Write yourself a Git!](https://wyag.thb.lt/) and goes much further: packfiles, the binary
index, `.gitignore`, branches, diffs and real three-way merges with conflict markers.

wyag reads and writes the same on-disk format as Git, so it works on real repositories,
including freshly cloned ones, and you can switch between `git` and `wyag` at any point.
The test suite runs every command next to real `git` and checks the results match:
commits, tags and merge commits get the **same hashes** git would produce,
and `status`, `diff`, `log`, `ls-files` and conflict files are compared byte for byte.

## Commands

**Everyday work**

| Command | What it does |
|---|---|
| `init [dir]` | Create an empty repository |
| `status [-s]` | Staged, unstaged, untracked and conflicted files; `-s` matches `git status --porcelain` |
| `add [-f] path...` | Stage files or directories (respects `.gitignore`, stages deletions) |
| `rm [--cached] [-r] path...` | Unstage and delete files; refuses to drop uncommitted changes |
| `commit [-a] [-m msg]` | Commit the index; without `-m`, opens your editor like git |
| `diff [--cached] [rev [rev]]` | Unified diff: files vs index, index vs `HEAD`, or two commits |
| `log [-n N] [--oneline] [rev]` | History, newest first, including merges |

**Branches and merging**

| Command | What it does |
|---|---|
| `branch [-d \| -D] [name [start]]` | List, create or delete branches (`-d` only if merged) |
| `switch [-c] [--detach] target` | Move to a branch, updating files and index safely |
| `merge [--no-ff] [-m msg] rev` | Fast-forward or three-way merge, with conflict markers |
| `merge --abort` | Give up a conflicted merge |
| `merge-base [--all] a b` | Best common ancestor(s) |
| `tag [-a] [-m msg] [name [rev]]` | List tags, or create a lightweight or annotated one |

**Plumbing**

| Command | What it does |
|---|---|
| `cat-file [-t \| -p] rev` | Print an object, or its type |
| `hash-object [-w] file` | Hash a file as a blob, optionally storing it |
| `ls-tree [-r] rev` | List a tree |
| `ls-files [-s]` | List the index, with stages during a conflict |
| `rev-parse [--short] rev...` | Resolve revisions to hashes |
| `show-ref [--heads] [--tags]` | List refs |
| `checkout rev dir` | Export a commit's files into an empty directory |

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
wyag add . && wyag commit -m "First commit"

wyag switch -c feature
echo "from feature" >> hello.txt
wyag commit -a -m "Feature work"

wyag switch master
wyag merge feature
wyag log --oneline
```

It also works on any existing git repository, packed or not: try `wyag log --oneline -n 5`,
`wyag status` or `wyag diff HEAD~1 HEAD` in one of your projects.

## How it works

**Objects.** Git is a content-addressed store. Every object is saved as
`<type> <size>\0<content>`, zlib-compressed, and named by its SHA-1. Blobs hold file
contents; trees list a directory as `<mode> <name>\0<20-byte sha>` entries (sorted as if
directory names ended in `/`, or the hash comes out wrong); commits and tags are
`key value` header lines, a blank line, then the message.

**Packfiles.** `git gc` and `git clone` pack objects into one file, many stored as
*deltas*: copy/insert instructions against another object. wyag reads the `.idx`
(including the 64-bit offset table used by packs over 2 GiB), follows `OFS_DELTA` /
`REF_DELTA` chains back to a full object and replays them.

**The index.** `.git/index` is a binary file listing every staged path with its blob hash,
mode and `stat()` data (versions 2, 3 and the path-compressed version 4). `status` uses the
`stat()` data to avoid re-hashing unchanged files, but re-hashes anything modified in the
same second the index was written ("racy git"). Writes go through `index.lock` and an
atomic rename, as in git.

**Switching branches.** Moving between two commits is a two-way merge: only paths that
differ between them are touched, local edits to other files are carried along, and the
switch is refused up front if it would overwrite uncommitted work or an untracked file.

**Diffs.** Hunks come from Python's `difflib` with git's headers, `\ No newline at end of
file` markers, binary detection and git's "function name" hunk context. Git uses Myers'
algorithm with extra heuristics, so on larger edits the hunks can be split differently,
but every patch applies with `git apply` and produces exactly the target tree.

**Merging.** The merge base comes from walking the commit graph. Each path is then merged
three ways: if only one side changed it, that side wins; if both changed a text file, a
*diff3* merge combines the lines, trims what both sides agree on out of the conflict, and
writes `<<<<<<<` / `=======` / `>>>>>>>` markers where they really disagree. Conflicts go
into the index as stages 1–3 (base, ours, theirs) with `MERGE_HEAD` and `MERGE_MSG`, so
`status`, `commit` and even real `git` pick up where wyag left off.

## Project layout

```
wyag/
├── __main__.py     # CLI: argument parsing and output formatting
├── repository.py   # GitRepository, init, finding the repository root
├── objects.py      # Blob, commit, tree, tag; reading/writing; revision syntax
├── pack.py         # Packfile and .idx reading, delta decoding
├── refs.py         # HEAD, branches, tags, packed-refs, ref name rules
├── index.py        # Reading and writing .git/index (v2, v3, v4)
├── ignore.py       # .gitignore, info/exclude and core.excludesFile rules
├── worktree.py     # status, add, rm, commit, updating files from a tree
├── branch.py       # branch and switch
├── diff.py         # Unified diffs
├── merge.py        # diff3 line merge, tree merge, merge/abort
├── history.py      # Ancestry and merge bases
└── config.py       # git config lookup and author identity
tests/              # ~260 tests, most of them comparing against real git
```

## Tests

```bash
pytest       # runs with coverage and fails below 90%
ruff check .
```

The tests build repositories with real `git` (merges, criss-cross histories, annotated
tags, symlinks, executables, deep delta chains, merge conflicts, submodule entries) and
compare wyag's output and hashes with git's. `.gitignore` handling is checked against
`git check-ignore`. CI runs everything on Python 3.9, 3.11 and 3.13.

## Limitations

- No networking (`clone`, `fetch`, `push`), `rebase`, `stash`, `reset` or reflog.
- Merges use the first best merge base; git's "recursive" handling of criss-cross
  histories (merging several bases into a virtual one) and rename detection are not
  implemented, and a file replaced by a directory on one side is reported as unsupported.
- `diff` hunks can differ from git's on large edits (see above); there's no rename
  detection or `--stat`.
- SHA-1 repositories only.

## License

[MIT](LICENSE)
