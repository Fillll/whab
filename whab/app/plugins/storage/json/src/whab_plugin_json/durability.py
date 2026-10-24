"""Stable cooperative writer locks and filesystem durability for one JSON file."""

import hashlib
import os
import stat
from pathlib import Path

from whab.domain.errors import StoreError

from .codec import MAX_FILE_BYTES


class ExternalChange(StoreError):
    """A runtime/lock file changed outside its owning backend."""


class CommitUncertain(StoreError):
    """Replacement may have committed; inspect durable identity before retrying."""

    def __init__(self, operation_id, generation):
        super().__init__(
            "The JSON commit outcome is uncertain. Reopen this budget and compare "
            "its durable operation identity before retrying the financial change."
        )
        self.operation_id = operation_id
        self.generation = generation


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path):
    """Reject links/nonregular files and bound bytes before allocating a parser."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > MAX_FILE_BYTES:
            raise StoreError("JSON storage requires one bounded regular data file.")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise StoreError("JSON storage exceeds its supported size limit.")
        return raw
    finally:
        os.close(descriptor)


def flush_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_durable_directory(path):
    """Durably create missing ancestry without changing existing modes/symlinks.

    Flush the existing anchor and its entry before extending it. This also makes
    retry after a failed directory flush safe: the previously created directory
    may now exist, but is not assumed durable merely because it is present.
    """
    missing = []
    anchor = Path(path)
    while not anchor.exists():
        missing.append(anchor)
        anchor = anchor.parent
    if not anchor.is_dir():
        raise StoreError("JSON storage requires a directory for its runtime data.")
    flush_directory(anchor.parent)
    flush_directory(anchor)
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise StoreError("JSON storage directory creation conflicted with another file.") from None
        flush_directory(directory.parent)
        flush_directory(directory)


class WriterLock:
    """A separate inode remains locked while atomic replacement changes data inodes.

    Advisory POSIX locking contains cooperative writers. It is neither a security
    sandbox nor protection against arbitrary code running as the same OS user.
    """

    def __init__(self, path):
        try:
            import fcntl
        except ImportError:
            raise StoreError("JSON storage currently requires POSIX file locking.") from None
        self.path = Path(path)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            metadata = os.fstat(self.fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise StoreError("JSON storage requires a regular writer lock file.")
            self.identity = metadata.st_dev, metadata.st_ino
        except BaseException as error:
            os.close(self.fd)
            self.fd = None
            if not isinstance(error, Exception):
                raise
            raise StoreError("Another JSON writer holds this budget, or its writer lock is invalid.") from None

    def check(self):
        try:
            metadata = self.path.lstat()
        except OSError:
            raise ExternalChange("The JSON writer lock changed; stop and reopen this budget safely.") from None
        if (metadata.st_dev, metadata.st_ino) != self.identity or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ExternalChange("The JSON writer lock changed; stop and reopen this budget safely.")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
