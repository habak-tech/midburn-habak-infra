#!/usr/bin/env python3
"""Stage a complete home backup, apply local overrides, then install its files."""
import argparse
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import tarfile


OVERLAY = ".overwrite-after-restore"


def protected(path):
    first = path.parts[0] if path.parts else ""
    return first == OVERLAY or (len(path.parts) == 1 and
        first.startswith(".habak1-server-backup-") and first.endswith(".lock"))


def archive_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("home", "habak"):
        raise ValueError(f"Archive path is outside home/habak: {name}")
    return PurePosixPath(*path.parts[2:])


def validate_link(path, target):
    if posixpath.isabs(target):
        raise ValueError(f"Absolute link is not allowed: {path}")
    resolved = PurePosixPath(posixpath.normpath(posixpath.join(str(path.parent), target)))
    if resolved.parts[:1] == ("..",) or protected(resolved):
        raise ValueError(f"Link leaves the restored home or targets protected files: {path}")


def set_metadata(path, mode, uid, gid, mtime, *, symlink=False):
    if os.geteuid() == 0:
        os.chown(path, uid, gid, follow_symlinks=not symlink)
    if not symlink:
        os.chmod(path, mode & 0o777)
    os.utime(path, (mtime, mtime), follow_symlinks=not symlink)


def stage_archive(archive_pathname, destination, home):
    owner = home.stat()
    with tarfile.open(archive_pathname, "r:gz") as archive:
        entries = {}
        for member in archive:
            relative = archive_path(member.name)
            if protected(relative):
                continue
            if relative in entries:
                raise ValueError(f"Duplicate home archive entry: {relative}")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported home archive entry: {relative}")
            if relative == PurePosixPath(".") and not member.isdir():
                raise ValueError("The home root must be a directory")
            if member.issym():
                validate_link(relative, member.linkname)
            if member.islnk() and protected(archive_path(member.linkname)):
                raise ValueError(f"Hard link targets protected files: {relative}")
            entries[relative] = member
        for relative, member in entries.items():
            for parent in relative.parents:
                if parent in entries and not entries[parent].isdir():
                    raise ValueError(f"Archive entry has a non-directory parent: {relative}")
            if member.islnk():
                target = entries.get(archive_path(member.linkname))
                if target is None or not (target.isfile() or target.islnk()):
                    raise ValueError(f"Invalid hard link target: {relative}")
        if not entries:
            raise ValueError("Home backup contains no restorable files")

        destination.mkdir(mode=0o700)
        pending_links = []
        directories = []
        for relative, member in entries.items():
            target = destination / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
                directories.append((target, member))
            elif member.isfile():
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
            elif member.issym():
                target.symlink_to(member.linkname)
            else:
                pending_links.append((target, member))
                continue
            if not member.isdir():
                set_metadata(target, member.mode, 0 if member.uid == 0 else owner.st_uid,
                    0 if member.gid == 0 else owner.st_gid, member.mtime, symlink=member.issym())
        while pending_links:
            remaining = []
            for target, member in pending_links:
                source = destination / str(archive_path(member.linkname))
                if source.is_file() and not source.is_symlink():
                    os.link(source, target)
                else:
                    remaining.append((target, member))
            if len(remaining) == len(pending_links):
                raise ValueError("Unresolvable home archive hard links")
            pending_links = remaining
        for target, member in sorted(directories, key=lambda item: len(item[0].parts), reverse=True):
            set_metadata(target, member.mode, 0 if member.uid == 0 else owner.st_uid,
                         0 if member.gid == 0 else owner.st_gid, member.mtime)


def validate_tree(source, relative=PurePosixPath(".")):
    for item in source.iterdir():
        path = relative / item.name
        if protected(path):
            raise ValueError(f"Overlay cannot replace protected files: {path}")
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            validate_link(path, os.readlink(item))
        elif stat.S_ISDIR(mode):
            validate_tree(item, path)
        elif not stat.S_ISREG(mode):
            raise ValueError(f"Unsupported overlay file: {path}")


def remove_target(target):
    if target.is_symlink() or not target.is_dir():
        target.unlink(missing_ok=True)
    else:
        shutil.rmtree(target)


def merge_tree(source, destination):
    """Overwrite supplied paths without deleting unrelated files or following links."""
    for item in source.iterdir():
        target = destination / item.name
        info = item.lstat()
        if item.is_symlink():
            remove_target(target)
            target.symlink_to(os.readlink(item))
        elif item.is_dir():
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                remove_target(target)
            target.mkdir(exist_ok=True)
            merge_tree(item, target)
        else:
            # Unlink even a regular file: an old hard link must not cause
            # writes to another inode name outside the restored home.
            remove_target(target)
            shutil.copyfile(item, target, follow_symlinks=False)
        set_metadata(target, info.st_mode, info.st_uid, info.st_gid, info.st_mtime,
                     symlink=item.is_symlink())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "apply"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--home-root", type=Path, default=Path("/home/habak"))
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "stage":
        overlay = args.home_root / OVERLAY
        if overlay.is_symlink():
            raise ValueError("The overwrite directory must be a real directory")
        if overlay.exists():
            validate_tree(overlay)
        stage_archive(args.source, args.destination, args.home_root)
        if overlay.exists():
            merge_tree(overlay, args.destination)
        print("Full home backup staged with recursive local overrides", flush=True)
    else:
        validate_tree(args.source)
        if args.destination.is_symlink():
            raise ValueError("The home root must be a real directory")
        merge_tree(args.source, args.destination)
        print("Home files restored; local overwrite directory preserved", flush=True)


if __name__ == "__main__":
    main()
