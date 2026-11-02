# wyag — Git in Python

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)
![coverage](https://img.shields.io/badge/coverage-96%25-brightgreen)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

**English** | [O'zbekcha](README.uz.md)

A from-scratch implementation of Git's core in pure Python, built to understand how
Git actually stores and reads history. Based on
[Write yourself a Git!](https://wyag.thb.lt/).

wyag reads and writes the same on-disk format as Git, so it works on real repositories:
its `log`, `ls-tree` and `cat-file` output is tested to match `git` byte for byte, and
commits it writes pass `git fsck --strict`.

## Commands

| Command | What it does |
|---|---|
| `init [dir]` | Create an empty repository (`.git`, `HEAD`, `config`, `objects/`, `refs/`) |
| `hash-object [-w] file` | Hash a file as a blob; with `-w`, store it in `.git/objects` |
| `cat-file [-t] object` | Print an object's contents, or its type with `-t` |
| `log [-n N] [--oneline] [commit]` | Show history, newest first, including merges |
| `ls-tree [-r] tree-ish` | List a tree; `-r` recurses into subdirectories |
| `checkout commit dir` | Write a commit's files into an empty directory |

Anywhere an object is expected you can use a full or short hash, `HEAD`, a branch or a tag.

## Quick start

Requires Python 3.9+. No runtime dependencies.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Point it at any git repository
cd ~/some-project
wyag log --oneline -n 5
wyag ls-tree -r HEAD
wyag checkout HEAD /tmp/snapshot
```

## How it works

Git is a content-addressed store. Every object is saved as
`<type> <size>\0<content>`, zlib-compressed, under `.git/objects/` and named by its SHA-1.

- **Blob**: raw file contents.
- **Tree**: a directory listing; each entry is `<mode> <name>\0<20-byte sha>`.
  Git sorts directories as if their name ended in `/`, which wyag reproduces so that
  tree hashes match.
- **Commit / tag**: `key value` header lines, a blank line, then the message. Multi-line
  values such as GPG signatures continue on lines that start with a space.
- **Refs**: `HEAD` and branches are text files pointing to a hash or to another ref.
  `packed-refs` is supported as well.

`log` walks the commit graph with a priority queue ordered by commit time, which is how it
lists merge histories in the same order as `git log`.

## Project layout

```
wyag/
├── __main__.py     # CLI: argument parsing and commands
├── repository.py   # GitRepository, init, finding the repository root
├── objects.py      # Blob, commit, tree and tag objects; storage; name resolution
└── refs.py         # Resolving HEAD, branches, tags and packed-refs
tests/
└── test_wyag.py    # Unit tests, CLI tests, and comparisons against real git
```

## Tests

```bash
pytest       # runs with coverage, fails below 90%
ruff check .
```

Most CLI tests build a repository with real `git` (subdirectories, an executable, a
symlink, an annotated tag, a merge) and assert that wyag's output is identical. CI runs
everything on Python 3.9, 3.11 and 3.13.

## Limitations

- **Packfiles are not supported yet.** After `git gc` or `git clone`, most objects live in
  packfiles, and wyag reports a clear error instead of reading them. Fresh repositories and
  ones with recent loose commits work.
- No staging area yet, so wyag cannot create commits from the working tree on its own.
- SHA-1 repositories only.

## Roadmap

- [x] `init`, `hash-object`, `cat-file`
- [x] Commit, tree and tag objects; `log`, `ls-tree`, `checkout`
- [x] Refs: `HEAD`, branches, tags, `packed-refs`
- [ ] Packfile reading (`.idx` v2, delta objects)
- [ ] `show-ref`, `tag`, `rev-parse`
- [ ] Index: `ls-files`, `status`, `add`, `rm`, `commit`

## License

[MIT](LICENSE)
