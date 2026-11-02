import argparse
import sys

from . import objects
from .repository import repo_create, repo_find

parser = argparse.ArgumentParser(description="wyag: a Git implementation in Python.")
subparsers = parser.add_subparsers(title="Commands", dest="command", required=True)

argsp = subparsers.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument("path", metavar="directory", nargs="?", default=".",
                   help="Where to create the repository.")

argsp = subparsers.add_parser("cat-file", help="Print the contents of a repository object.")
argsp.add_argument("object", metavar="object", help="The object to display (full or unique-prefix hash).")

argsp = subparsers.add_parser("hash-object", help="Compute an object's hash, optionally storing it.")
argsp.add_argument("path", help="Read the object from this file.")
argsp.add_argument("-w", dest="write", action="store_true",
                   help="Actually write the object into the repository.")


def cmd_init(args):
    repo_create(args.path)
    print(f"Initialized empty Git repository in {args.path}")


def cmd_cat_file(args):
    repo = repo_find()
    sha = objects.object_find(repo, args.object)
    obj = objects.object_read(repo, sha)
    sys.stdout.buffer.write(obj.serialize())


def cmd_hash_object(args):
    repo = repo_find() if args.write else None
    with open(args.path, "rb") as fd:
        obj = objects.GitBlob(fd.read())
    print(objects.object_write(obj, repo))


COMMANDS = {
    "init": cmd_init,
    "cat-file": cmd_cat_file,
    "hash-object": cmd_hash_object,
}


def main():
    args = parser.parse_args()
    try:
        COMMANDS[args.command](args)
    except Exception as e:
        sys.exit(f"fatal: {e}")


if __name__ == "__main__":
    main()
