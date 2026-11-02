"""gitignore rules.

Rules come from .gitignore files in each directory, .git/info/exclude and the
user's core.excludesFile. The last matching rule wins, and rules from a deeper
.gitignore beat those from its parents.
"""

from __future__ import annotations

import os
import re

from .config import config_get
from .repository import GitRepository


class IgnoreRule:
    def __init__(self, pattern: str, base: str) -> None:
        """base is the directory (relative to the worktree, "" for the root)
        that holds the file the rule came from."""
        self.negate = pattern.startswith("!")
        if self.negate:
            pattern = pattern[1:]
        elif pattern.startswith("\\"):
            pattern = pattern[1:]  # escaped leading "#" or "!"

        self.dir_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")

        # A slash anywhere but the end ties the pattern to its .gitignore's
        # directory; otherwise it matches a name at any depth.
        self.anchored = "/" in pattern
        self.base = base
        self.regex = re.compile(glob_to_regex(pattern.lstrip("/")) + r"\Z", re.DOTALL)

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.base:
            if not path.startswith(self.base + "/"):
                return False
            path = path[len(self.base) + 1:]
        target = path if self.anchored else path.rsplit("/", 1)[-1]
        return bool(self.regex.match(target))


def glob_to_regex(pattern: str) -> str:
    out = ""
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i) and (i == 0 or pattern[i - 1] == "/"):
            out += "(?:.*/)?"  # zero or more directories
            i += 3
        elif pattern.startswith("**", i) and i + 2 == n and (i == 0 or pattern[i - 1] == "/"):
            out += ".*"  # everything inside
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 2)
            if end < 0:
                out += re.escape("[")
                i += 1
                continue
            body = pattern[i + 1:end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out += "[" + body.replace("\\", "\\\\") + "]"
            i = end + 1
        elif pattern[i] == "\\" and i + 1 < n:
            out += re.escape(pattern[i + 1])
            i += 2
        else:
            out += re.escape(pattern[i])
            i += 1
    return out


def parse_rules(text: str, base: str) -> list[IgnoreRule]:
    rules = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        # Trailing spaces are ignored unless escaped with a backslash.
        stripped = line.rstrip(" ")
        if stripped.endswith("\\") and len(stripped) < len(line):
            stripped += " "
        if not stripped or stripped.startswith("#"):
            continue
        rules.append(IgnoreRule(stripped, base))
    return rules


def read_rules(path: str, base: str) -> list[IgnoreRule]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_rules(f.read(), base)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return []


class Ignore:
    def __init__(self, repo: GitRepository) -> None:
        self.repo = repo
        excludes = config_get(repo, "core", "excludesfile")
        if excludes is None:
            xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
            excludes = os.path.join(xdg, "git", "ignore")

        # Lowest priority first.
        self.global_rules = (read_rules(os.path.expanduser(excludes), "")
                             + read_rules(repo.repo_file("info", "exclude"), ""))
        self.dir_rules: dict[str, list[IgnoreRule]] = {}

    def rules_for(self, directory: str) -> list[IgnoreRule]:
        if directory not in self.dir_rules:
            gitignore = os.path.join(self.repo.worktree, directory, ".gitignore")
            self.dir_rules[directory] = read_rules(gitignore, directory)
        return self.dir_rules[directory]

    def is_ignored(self, path: str, is_dir: bool) -> bool:
        """path is relative to the worktree, with "/" separators."""
        # A file inside an ignored directory can't be re-included.
        parts = path.split("/")
        for i in range(1, len(parts)):
            if self._match("/".join(parts[:i]), True):
                return True
        return self._match(path, is_dir)

    def _match(self, path: str, is_dir: bool) -> bool:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        rules = list(self.global_rules)
        dirs = [""] + [parent[:i] for i, c in enumerate(parent) if c == "/"] + (
            [parent] if parent else [])
        for d in dirs:
            rules += self.rules_for(d)

        result = False
        for rule in rules:
            if rule.matches(path, is_dir):
                result = not rule.negate
        return result
