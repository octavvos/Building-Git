from __future__ import annotations

import configparser
import os
import re
import time
from datetime import datetime

from .repository import GitRepository


def read_config(path: str) -> configparser.ConfigParser | None:
    # Git config allows repeated keys and keys without values.
    conf = configparser.ConfigParser(strict=False, allow_no_value=True, interpolation=None)
    try:
        conf.read([path])
    except configparser.Error:
        return None
    return conf


def global_config_paths() -> list[str]:
    """User-level config files, highest priority first, like git."""
    override = os.environ.get("GIT_CONFIG_GLOBAL")
    if override is not None:
        return [override]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return [os.path.expanduser("~/.gitconfig"), os.path.join(xdg, "git", "config")]


def config_get(repo: GitRepository, section: str, key: str) -> str | None:
    """Look a value up in the repository config, then in the user's config."""
    configs = [repo.conf] + [read_config(p) for p in global_config_paths()]
    for conf in configs:
        if conf is not None and conf.has_option(section, key):
            return conf.get(section, key)
    return None


DATE_RE = re.compile(r"^\d+ [+-]\d{4}$")


def local_timezone() -> str:
    offset = datetime.now().astimezone().utcoffset()
    minutes = int(offset.total_seconds()) // 60
    sign = "-" if minutes < 0 else "+"
    return f"{sign}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"


def identity(repo: GitRepository, role: str = "COMMITTER") -> bytes:
    """Build "Name <email> <timestamp> <tz>" for an author, committer or tagger.

    Like git, GIT_AUTHOR_* / GIT_COMMITTER_* environment variables win over
    user.name and user.email from the config.
    """
    name = os.environ.get(f"GIT_{role}_NAME") or config_get(repo, "user", "name")
    email = os.environ.get(f"GIT_{role}_EMAIL") or config_get(repo, "user", "email")
    if not name or not email:
        raise Exception(
            "Please tell me who you are: set user.name and user.email, e.g.\n"
            '  git config --global user.name "Your Name"\n'
            '  git config --global user.email "you@example.com"'
        )

    date = os.environ.get(f"GIT_{role}_DATE")
    if date and not DATE_RE.match(date):
        raise Exception(f"Unsupported date format in GIT_{role}_DATE: {date!r}")
    if not date:
        date = f"{int(time.time())} {local_timezone()}"

    return f"{name} <{email}> {date}".encode()
