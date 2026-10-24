"""Private host journal persistence; never a portable payload or worker handle.

One stable POSIX lock protects the complete record across atomic replacements.
Acknowledged writes flush the file and directory. An ambiguous publication fences
the handle until recovery validates and flushes the observed durable decision.
"""

import fcntl
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock
from uuid import uuid4


class EffectError(ValueError):
    """Fixed diagnostics deliberately omit namespace, provider and path contents."""

    _MESSAGES = {
        "invalid_effect": "The plugin effect data is invalid or exceeds its supported bounds.",
        "effect_unavailable": "The private plugin effect journal could not be opened. Preserve its files.",
        "effect_busy": "Another writer owns this private plugin effect journal.",
        "effect_changed": "The private plugin effect journal changed outside its writer. Preserve its files.",
        "effect_closed": "This plugin effect generation has closed.",
        "effect_commit_failed": "The plugin effect was not published; its previous journal remains authoritative.",
        "effect_commit_uncertain": "The plugin effect publication is uncertain. Recover its durable decision before retrying.",
        "effect_conflict": "The plugin operation identity or expected namespace no longer matches.",
        "materialization_pending": "The committed plugin namespace still requires recovery before activation.",
        "read_only_effect": "This staged plugin context cannot change its namespace.",
        "continuation_missing": "The bank continuation is unavailable in this scope.",
        "continuation_busy": "Finish or reconcile the bank continuation before replacing this provider.",
        "continuation_expired": "This bank continuation expired before an external exchange began.",
        "exchange_uncertain": "The provider exchange outcome is uncertain. Do not exchange or reconnect automatically.",
        "apply_uncertain": "The bank save outcome requires durable receipt reconciliation before retrying.",
    }

    def __init__(self, code):
        self.code = code
        super().__init__(self._MESSAGES[code])


def invalid():
    raise EffectError("invalid_effect")


def _plain(value, depth=0):
    if depth > 40:
        invalid()
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= 2**53 - 1:
        return
    if type(value) is float and math.isfinite(value) and abs(value) <= 2**53 - 1:
        return
    if type(value) is str:
        if "\x00" in value:
            invalid()
        value.encode("utf-8")
        return
    if type(value) is list and len(value) <= 10000:
        for row in value:
            _plain(row, depth + 1)
        return
    if type(value) is dict and len(value) <= 10000 and all(type(key) is str for key in value):
        for key, row in value.items():
            _plain(key, depth + 1)
            _plain(row, depth + 1)
        return
    invalid()


def encode(value, limit):
    """Bound serialization, including Unicode and nonfinite/unsupported primitives."""
    try:
        _plain(value)
        chunks, length = [], 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).iterencode(value):
            raw = chunk.encode("utf-8")
            length += len(raw)
            if length > limit:
                invalid()
            chunks.append(raw)
        return b"".join(chunks)
    except (TypeError, UnicodeError, OverflowError, RecursionError, ValueError):
        invalid()


def decode(raw, limit):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                invalid()
            result[key] = value
        return result

    try:
        if type(raw) is not bytes or not raw or len(raw) > limit:
            invalid()
        value = json.loads(raw, object_pairs_hook=unique)
        _plain(value)
        return value
    except (TypeError, UnicodeError, OverflowError, RecursionError, ValueError):
        invalid()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir(path):
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            invalid()
        return
    _mkdir(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not path.is_dir() or path.is_symlink():
            invalid()
    fsync_directory(path)
    fsync_directory(path.parent)


class EffectStore:
    """A bounded, validated complete-record transaction owned only by the host.

    ``root`` is an intentional runtime boundary, resolved once by the host.
    Existing ancestor permissions are preserved. New directories/files are
    private; existing journal/lock files must already be private regular files.
    ``validate`` accepts only the owner's schema; no corruption becomes empty.
    """

    def __init__(self, root, filename, scope, initial, validate, *, limit, fault=None):
        self.lock = RLock()
        self._descriptor = None
        self._closed = False
        self._changed = False
        self._uncertain = None
        self.fault = fault
        self.validate, self.limit = validate, limit
        try:
            if type(filename) is not str or filename not in {"namespace-effects.json", "bank-continuations.json"}:
                invalid()
            if type(limit) is not int or not 1024 <= limit <= 128 * 1024 * 1024:
                invalid()
            self.root = Path(root).resolve()
            self.path = self.root / filename
            self.lock_path = self.root / (filename + ".lock")
            self.scope = decode(encode(scope, 4096), 4096)
            _mkdir(self.root)
            existing = self.path.exists() or self.path.is_symlink()
            self._descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW | (0 if existing else os.O_CREAT), 0o600)
            self._private(self._descriptor)
            try:
                fcntl.flock(self._descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EffectError("effect_busy") from None
            information = os.fstat(self._descriptor)
            self._lock_identity = information.st_dev, information.st_ino
            self._lock_token = None
            self._check_writer()
            token = os.pread(self._descriptor, 65, 0)
            if not token and not (self.path.exists() or self.path.is_symlink()):
                token = uuid4().hex.encode("ascii") + b"\n"
                if os.write(self._descriptor, token) != len(token):
                    invalid()
                os.fsync(self._descriptor)
            if re.fullmatch(rb"[a-f0-9]{32}\n", token) is None:
                invalid()
            self._lock_token = token.decode("ascii").strip()
            fsync_directory(self.root)
            if self.path.exists() or self.path.is_symlink():
                self._raw = self._read()
                self._record = self._validated(self._raw)
            else:
                self._raw = None
                self._record = {"format": 1, "scope": self.scope, "lock": self._writer_record(), "revision": 0, "body": deepcopy(initial)}
                self.validate(deepcopy(initial))
                self._publish(encode(self._record, self.limit))
            # An earlier process may have died after replace, before dir fsync.
            fsync_directory(self.root)
        except BaseException as error:
            self.close()
            if isinstance(error, EffectError):
                raise
            if isinstance(error, Exception):
                raise EffectError("effect_unavailable") from None
            raise

    @staticmethod
    def _private(descriptor):
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077:
            invalid()

    def _read(self):
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            self._private(descriptor)
            if os.fstat(descriptor).st_size > self.limit:
                invalid()
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(self.limit + 1)
            if not raw or len(raw) > self.limit:
                invalid()
            return raw
        finally:
            os.close(descriptor)

    def _validated(self, raw):
        record = decode(raw, self.limit)
        if (type(record) is not dict or record.keys() != {"format", "scope", "lock", "revision", "body"}
                or type(record["format"]) is not int or record["format"] != 1 or record["scope"] != self.scope
                or type(record["revision"]) is not int or not 0 <= record["revision"] <= 2**53 - 1):
            invalid()
        if record["lock"] != self._writer_record():
            raise EffectError("effect_changed")
        self.validate(deepcopy(record["body"]))
        return record

    def _writer_record(self):
        # Native file identities are decimal strings, not portable JSON numbers.
        # A copied token on a new inode cannot manufacture a second writer.
        device, inode = self._lock_identity
        return {"token": self._lock_token, "device": str(device), "inode": str(inode)}

    def _check_writer(self):
        try:
            information = os.stat(self.lock_path, follow_symlinks=False)
            if (not stat.S_ISREG(information.st_mode) or information.st_mode & 0o077
                    or (information.st_dev, information.st_ino) != self._lock_identity
                    or (self._lock_token is not None and os.pread(self._descriptor, 65, 0) != self._lock_token.encode("ascii") + b"\n")):
                raise ValueError()
        except Exception:
            self._changed = True
            raise EffectError("effect_changed") from None

    def _check(self):
        if self._closed:
            raise EffectError("effect_closed")
        if self._changed:
            raise EffectError("effect_changed")
        if self._uncertain is not None:
            raise EffectError("effect_commit_uncertain")
        try:
            self._check_writer()
            if self._read() != self._raw:
                raise EffectError("effect_changed")
        except Exception:
            self._changed = True
            raise EffectError("effect_changed") from None

    def _publish(self, raw):
        temporary = None
        replacing = False
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".effect-", dir=self.root)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as target:
                    target.write(raw)
                    target.flush()
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if self.fault:
                self.fault("before_replace")
            self._check_writer()
            if self._raw is not None and self._read() != self._raw:
                self._changed = True
                raise EffectError("effect_changed")
            replacing = True
            os.replace(temporary, self.path)
            if self.fault:
                self.fault("after_replace")
            fsync_directory(self.root)
            if self.fault:
                self.fault("after_directory_fsync")
            self._check_writer()
            if self._read() != raw:
                self._changed = True
                raise EffectError("effect_changed")
        except BaseException as error:
            if replacing:
                self._uncertain = (self._raw, raw)
            if isinstance(error, Exception):
                if isinstance(error, EffectError) and not replacing:
                    raise
                raise EffectError("effect_commit_uncertain" if replacing else "effect_commit_failed") from None
            raise
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
        self._raw = raw

    def snapshot(self):
        with self.lock:
            self._check()
            return deepcopy(self._record["body"])

    def commit(self, body):
        with self.lock:
            self._check()
            self.validate(deepcopy(body))
            candidate = {**self._record, "revision": self._record["revision"] + 1, "body": deepcopy(body)}
            raw = encode(candidate, self.limit)
            self._validated(raw)
            self._publish(raw)
            self._record = candidate

    def recover(self):
        with self.lock:
            if self._closed:
                raise EffectError("effect_closed")
            if self._changed:
                raise EffectError("effect_changed")
            try:
                self._check_writer()
                raw = self._read()
                if self._uncertain is None:
                    if raw != self._raw:
                        raise EffectError("effect_changed")
                elif raw not in self._uncertain:
                    raise EffectError("effect_changed")
                record = self._validated(raw)
            except Exception:
                self._changed = True
                raise EffectError("effect_changed") from None
            try:
                fsync_directory(self.root)
            except Exception:
                raise EffectError("effect_commit_uncertain" if self._uncertain is not None else "effect_unavailable") from None
            self._record, self._raw, self._uncertain = record, raw, None
            return deepcopy(record["body"])

    def close(self):
        with self.lock:
            self._closed = True
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
