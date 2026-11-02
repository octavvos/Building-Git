"""Walking the commit graph: ancestry and merge bases."""

from __future__ import annotations

from . import objects
from .objects import GitCommit
from .repository import GitRepository


class CommitGraph:
    """Reads commits on demand and remembers their parents and times."""

    def __init__(self, repo: GitRepository) -> None:
        self.repo = repo
        self._parents: dict[str, list[str]] = {}
        self._time: dict[str, int] = {}

    def _load(self, sha: str) -> None:
        commit = objects.object_read(self.repo, sha)
        if not isinstance(commit, GitCommit) or isinstance(commit, objects.GitTag):
            raise Exception(f"{sha} is not a commit")
        self._parents[sha] = commit.parents
        committer = commit.kvlm[b"committer"][0]
        self._time[sha] = int(committer.rsplit(b" ", 2)[1])

    def parents(self, sha: str) -> list[str]:
        if sha not in self._parents:
            self._load(sha)
        return self._parents[sha]

    def time(self, sha: str) -> int:
        if sha not in self._time:
            self._load(sha)
        return self._time[sha]

    def ancestors(self, sha: str) -> set[str]:
        """sha and every commit reachable from it."""
        seen = {sha}
        stack = [sha]
        while stack:
            for parent in self.parents(stack.pop()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return ancestor in self.ancestors(descendant)

    def merge_bases(self, a: str, b: str) -> list[str]:
        """The best common ancestors of a and b: common ancestors that are not
        themselves ancestors of another common ancestor. Newest first."""
        common = self.ancestors(a) & self.ancestors(b)

        best: list[str] = []
        redundant: set[str] = set()
        for sha in sorted(common, key=lambda s: (-self.time(s), s)):
            if sha in redundant:
                continue
            best.append(sha)
            # Everything below a best base is not a best base.
            stack = list(self.parents(sha))
            while stack:
                parent = stack.pop()
                if parent not in redundant:
                    redundant.add(parent)
                    stack.extend(self.parents(parent))
        return best
