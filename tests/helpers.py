import os
import shutil
import subprocess

import pytest

from wyag.__main__ import main

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git o'rnatilmagan")

# Fixed identity and dates make commit hashes reproducible, and an empty
# global config keeps the developer's own git settings out of the tests.
IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "1700000000 +0500",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_COMMITTER_DATE": "1700000000 +0500",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(*args, cwd):
    env = {**os.environ, **IDENTITY_ENV}
    return subprocess.run(["git", *args], cwd=cwd, env=env,
                          capture_output=True, check=True).stdout


def git_text(*args, cwd):
    return git(*args, cwd=cwd).decode().strip()


def wyag(*args, cwd, monkeypatch, capsysbinary):
    """Run the wyag CLI in-process and return its stdout."""
    monkeypatch.chdir(cwd)
    main(list(args))
    return capsysbinary.readouterr().out
