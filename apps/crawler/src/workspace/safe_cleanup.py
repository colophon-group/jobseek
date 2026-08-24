"""Descriptor-anchored helpers for recursive cleanup of named directories."""

from __future__ import annotations

import os
import stat
import uuid
from contextlib import suppress
from pathlib import Path


def directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def open_absolute_directory_no_follow(path: Path) -> int:
    """Open every absolute path component as a real directory without following links."""
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or not absolute.anchor:
        raise RuntimeError(f"cleanup path is not absolute: {path}")
    flags = directory_open_flags()
    try:
        directory_fd = os.open(absolute.anchor, flags)
    except OSError as exc:
        raise RuntimeError(f"could not open cleanup anchor: {exc}") from exc
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    except OSError as exc:
        os.close(directory_fd)
        raise RuntimeError(f"cleanup path is unsafe: {exc}") from exc
    return directory_fd


def open_child_directory_no_follow(parent_fd: int, name: str) -> tuple[int, os.stat_result]:
    validate_child_name(name)
    try:
        expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        child_fd = os.open(name, directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeError(f"cleanup directory is unsafe: {exc}") from exc
    opened = os.fstat(child_fd)
    if (
        not stat.S_ISDIR(expected.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or opened.st_dev != expected.st_dev
        or opened.st_ino != expected.st_ino
    ):
        os.close(child_fd)
        raise RuntimeError("cleanup directory changed while opening")
    return child_fd, opened


def rmtree_child_at(
    parent_fd: int,
    name: str,
    *,
    child_fd: int,
    expected: os.stat_result,
) -> None:
    """Delete a validated direct child through its already-open descriptor."""
    validate_child_name(name)
    opened = os.fstat(child_fd)
    if not _same_entry(opened, expected) or not stat.S_ISDIR(opened.st_mode):
        raise RuntimeError("cleanup directory changed after opening")

    claimed_name = claim_child_at(parent_fd, name, expected=expected)
    try:
        _empty_directory_at(child_fd)
        current = os.stat(claimed_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_entry(current, expected) or not stat.S_ISDIR(current.st_mode):
            raise RuntimeError("cleanup directory changed before final removal")
        os.rmdir(claimed_name, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeError(f"descriptor-anchored recursive cleanup failed: {exc}") from exc


def unlink_child_at(
    parent_fd: int,
    name: str,
    *,
    expected: os.stat_result,
) -> None:
    """Unlink a validated non-directory child without mutating a replacement."""
    validate_child_name(name)
    claimed_name = claim_child_at(parent_fd, name, expected=expected)
    try:
        current = os.stat(claimed_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_entry(current, expected) or stat.S_ISDIR(current.st_mode):
            raise RuntimeError("cleanup entry changed before final removal")
        os.unlink(claimed_name, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeError(f"descriptor-anchored cleanup failed: {exc}") from exc


def safe_rmtree_child(parent: Path, name: str, *, missing_ok: bool = False) -> bool:
    """Safely remove one direct child of ``parent`` without pathname re-resolution."""
    try:
        parent_fd = open_absolute_directory_no_follow(parent)
    except RuntimeError as exc:
        if missing_ok and isinstance(exc.__cause__, FileNotFoundError):
            return False
        raise
    child_fd: int | None = None
    try:
        try:
            child_fd, opened = open_child_directory_no_follow(parent_fd, name)
        except RuntimeError as exc:
            if missing_ok and isinstance(exc.__cause__, FileNotFoundError):
                return False
            raise
        try:
            rmtree_child_at(parent_fd, name, child_fd=child_fd, expected=opened)
        except RuntimeError as exc:
            if missing_ok and isinstance(exc.__cause__, FileNotFoundError):
                return False
            raise
        return True
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(parent_fd)


def validate_child_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or (os.altsep and os.altsep in name):
        raise RuntimeError(f"invalid cleanup directory name: {name!r}")


def _same_entry(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def claim_child_at(
    parent_fd: int,
    name: str,
    *,
    expected: os.stat_result,
    claimed_name: str | None = None,
) -> str:
    """Atomically move one entry to an unpredictable private name, then validate it."""
    claimed_name = claimed_name or f".jobseek-cleanup-{uuid.uuid4().hex}"
    validate_child_name(claimed_name)
    try:
        os.stat(claimed_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"could not validate cleanup claim target: {exc}") from exc
    else:
        raise RuntimeError("cleanup claim target already exists")
    try:
        os.rename(name, claimed_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        claimed = os.stat(claimed_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"could not claim cleanup entry: {exc}") from exc
    if _same_entry(claimed, expected):
        return claimed_name

    # The named entry was replaced after validation. Put that replacement back
    # when the original name is still vacant; otherwise retain it under the
    # private name. In either case no unvalidated object is deleted.
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        with suppress(OSError):
            os.rename(claimed_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    except OSError:
        pass
    raise RuntimeError("cleanup entry changed at mutation boundary")


def restore_claimed_child_at(
    parent_fd: int,
    original_name: str,
    claimed_name: str,
    *,
    expected: os.stat_result,
) -> None:
    """Restore a claimed entry only when both names still have safe identities."""
    validate_child_name(original_name)
    validate_child_name(claimed_name)
    try:
        claimed = os.stat(claimed_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"claimed cleanup entry is unavailable: {exc}") from exc
    if not _same_entry(claimed, expected):
        raise RuntimeError("claimed cleanup entry changed before restoration")
    try:
        os.stat(original_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"could not validate cleanup restore target: {exc}") from exc
    else:
        raise RuntimeError("cleanup restore target was replaced; claimed evidence retained")
    try:
        os.rename(
            claimed_name,
            original_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        restored = os.stat(original_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"could not restore claimed cleanup entry: {exc}") from exc
    if not _same_entry(restored, expected):
        raise RuntimeError("cleanup entry changed while restoring")


def unlink_claimed_child_at(
    parent_fd: int,
    claimed_name: str,
    *,
    expected: os.stat_result,
) -> None:
    """Unlink an already-claimed non-directory child after one final identity check."""
    validate_child_name(claimed_name)
    try:
        current = os.stat(claimed_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_entry(current, expected) or stat.S_ISDIR(current.st_mode):
            raise RuntimeError("claimed cleanup entry changed before final removal")
        os.unlink(claimed_name, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeError(f"descriptor-anchored claimed cleanup failed: {exc}") from exc


def _empty_directory_at(directory_fd: int) -> None:
    try:
        names = os.listdir(directory_fd)
    except OSError as exc:
        raise RuntimeError(f"could not enumerate cleanup directory: {exc}") from exc

    for name in names:
        validate_child_name(name)
        try:
            expected = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"cleanup entry changed before claiming: {exc}") from exc
        claimed_name = claim_child_at(directory_fd, name, expected=expected)
        if stat.S_ISDIR(expected.st_mode):
            child_fd: int | None = None
            try:
                child_fd = os.open(claimed_name, directory_open_flags(), dir_fd=directory_fd)
                opened = os.fstat(child_fd)
                if not _same_entry(opened, expected) or not stat.S_ISDIR(opened.st_mode):
                    raise RuntimeError("cleanup directory changed after claiming")
                _empty_directory_at(child_fd)
                current = os.stat(
                    claimed_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if not _same_entry(current, expected) or not stat.S_ISDIR(current.st_mode):
                    raise RuntimeError("cleanup directory changed before final removal")
                os.rmdir(claimed_name, dir_fd=directory_fd)
            except OSError as exc:
                raise RuntimeError(f"descriptor-anchored recursive cleanup failed: {exc}") from exc
            finally:
                if child_fd is not None:
                    os.close(child_fd)
        else:
            try:
                current = os.stat(
                    claimed_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if not _same_entry(current, expected):
                    raise RuntimeError("cleanup entry changed before final removal")
                os.unlink(claimed_name, dir_fd=directory_fd)
            except OSError as exc:
                raise RuntimeError(f"descriptor-anchored cleanup failed: {exc}") from exc
