import configparser
import os


class GitRepository:
    """A Git repository: a working tree plus its .git directory."""

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception(f"Not a Git repository {path}")

        self.conf = None
        config_file = self.repo_file("config")
        if os.path.exists(config_file):
            self.conf = configparser.ConfigParser()
            self.conf.read([config_file])
        elif not force:
            raise Exception("Configuration file missing")

        if not force:
            version = int(self.conf.get("core", "repositoryformatversion"))
            if version != 0:
                raise Exception(f"Unsupported repositoryformatversion {version}")

    def repo_path(self, *path):
        """Path inside .git."""
        return os.path.join(self.gitdir, *path)

    def repo_file(self, *path, mkdir=False):
        """Path inside .git; create its parent directory if mkdir is set."""
        if mkdir:
            self.repo_dir(*path[:-1], mkdir=True)
        return self.repo_path(*path)

    def repo_dir(self, *path, mkdir=False):
        """Directory inside .git; return None if it is missing and mkdir is off."""
        full = self.repo_path(*path)
        if os.path.exists(full):
            if os.path.isdir(full):
                return full
            raise Exception(f"Not a directory {full}")
        if mkdir:
            os.makedirs(full)
            return full
        return None


def repo_create(path):
    """Create a new repository at path."""
    repo = GitRepository(path, True)

    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f"{path} is not a directory")
        if os.path.exists(repo.gitdir) and os.listdir(repo.gitdir):
            raise Exception(f"{path} is not empty")
    else:
        os.makedirs(repo.worktree)

    repo.repo_dir("branches", mkdir=True)
    repo.repo_dir("objects", mkdir=True)
    repo.repo_dir("refs", "tags", mkdir=True)
    repo.repo_dir("refs", "heads", mkdir=True)

    with open(repo.repo_file("description"), "w") as f:
        f.write("Unnamed repository; edit this file 'description' to name the repository.\n")

    with open(repo.repo_file("HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")

    with open(repo.repo_file("config"), "w") as f:
        config = repo_default_config()
        config.write(f)

    return repo


def repo_default_config():
    ret = configparser.ConfigParser()
    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")
    return ret


def repo_find(path=".", required=True):
    """Walk up from path until a directory containing .git is found."""
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    parent = os.path.realpath(os.path.join(path, ".."))
    if parent == path:
        if required:
            raise Exception("No git directory.")
        return None

    return repo_find(parent, required)
