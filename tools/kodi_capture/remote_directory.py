"""Stable observations from the fixed Pi screenshot-evidence helper."""

from __future__ import annotations

import math
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable

from .process import (
    BoundedProcess,
    ProcessTimeout,
    ProcessTransportError,
)
from .ssh_policy import (
    SSH_BASE_OPTIONS,
    SSH_OPTION_TERMINATOR,
    SSH_PROGRAM,
    validate_ssh_host,
)


REMOTE_EVIDENCE_PROGRAM = (
    "/run/current-system/sw/bin/kodi-screenshot-evidence"
)
PROTOCOL_VERSION = "KODI-SCREENSHOT-EVIDENCE/1"
DIRECTORY_TAG = "D"
FILE_TAG = "F"
DIRECTORY_FIELD_COUNT = 13
FILE_FIELD_COUNT = 12
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_ENTRIES = 4096
MAX_FIELD_BYTES = 4096
MAX_FILENAME_BYTES = 255
EXPECTED_DIRECTORY_OWNER = "htpc"
EXPECTED_DIRECTORY_GROUP = "users"
EXPECTED_DIRECTORY_MODE = 0o700
_FILENAME_PATTERN = re.compile(r"\Ascreenshot[0-9]+\.png\Z")
_INTEGER_PATTERN = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
_OCTAL_PATTERN = re.compile(r"\A(?:0|[1-7][0-7]{0,3})\Z")


class RemoteDirectoryError(Exception):
    """Base class for remote screenshot-evidence failures."""


class RemoteDirectoryTimeout(RemoteDirectoryError):
    """The absolute stable-observation deadline expired."""


class RemoteDirectoryTransportError(RemoteDirectoryError):
    """SSH or the fixed remote evidence helper failed."""


class RemoteDirectoryProtocolError(RemoteDirectoryError):
    """The fixed helper returned malformed or unsafe evidence."""


class DirectoryNotQuiescent(RemoteDirectoryError):
    """Two consecutive complete observations do not establish stability."""


@dataclass(frozen=True)
class StatStamp:
    file_type: str
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    link_count: int
    size: int
    mtime_ns: int
    ctime_ns: int

    def __post_init__(self) -> None:
        _require_string(self.file_type, "file type", nonempty=True)
        _require_integer(self.device, "device", minimum=0)
        _require_integer(self.inode, "inode", minimum=1)
        _require_integer(self.mode, "mode", minimum=0)
        _require_integer(self.uid, "uid", minimum=0)
        _require_integer(self.gid, "gid", minimum=0)
        _require_integer(self.link_count, "link count", minimum=1)
        _require_integer(self.size, "size", minimum=0)
        _require_integer(self.mtime_ns, "mtime_ns", minimum=0)
        _require_integer(self.ctime_ns, "ctime_ns", minimum=0)
        if self.mode > 0o7777:
            raise ValueError("mode exceeds four octal digits")


@dataclass(frozen=True)
class DirectoryStamp:
    stat: StatStamp
    owner: str
    group: str

    def __post_init__(self) -> None:
        if not isinstance(self.stat, StatStamp):
            raise TypeError("directory stat must be a StatStamp")
        _require_string(self.owner, "directory owner", nonempty=True)
        _require_string(self.group, "directory group", nonempty=True)
        if self.stat.file_type != "d":
            raise ValueError("screenshot path must be a directory")
        if self.stat.mode != EXPECTED_DIRECTORY_MODE:
            raise ValueError("screenshot directory mode must be 0700")
        if self.owner != EXPECTED_DIRECTORY_OWNER:
            raise ValueError("screenshot directory owner must be htpc")
        if self.group != EXPECTED_DIRECTORY_GROUP:
            raise ValueError("screenshot directory group must be users")


@dataclass(frozen=True)
class RemoteFileStamp:
    name: str
    stat: StatStamp

    def __post_init__(self) -> None:
        _require_string(self.name, "file name", nonempty=True)
        if not isinstance(self.stat, StatStamp):
            raise TypeError("file stat must be a StatStamp")
        if not _FILENAME_PATTERN.fullmatch(self.name):
            raise ValueError("file name is not a Kodi screenshot name")
        if len(self.name.encode("ascii", "strict")) > MAX_FILENAME_BYTES:
            raise ValueError("file name exceeds its byte bound")
        if self.stat.file_type != "f":
            raise ValueError("screenshot entry must be a regular file")
        if self.stat.link_count != 1:
            raise ValueError("screenshot entry link count must be one")


@dataclass(frozen=True)
class DirectorySnapshot:
    directory: DirectoryStamp
    files: tuple[RemoteFileStamp, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.directory, DirectoryStamp):
            raise TypeError("directory must be a DirectoryStamp")
        if not isinstance(self.files, tuple):
            raise TypeError("files must be a tuple")
        if any(not isinstance(file, RemoteFileStamp) for file in self.files):
            raise TypeError("files must contain RemoteFileStamp values")
        names = tuple(file.name for file in self.files)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("files must be strictly sorted and unique")
        for file in self.files:
            if (
                file.stat.device != self.directory.stat.device
                or file.stat.uid != self.directory.stat.uid
                or file.stat.gid != self.directory.stat.gid
            ):
                raise ValueError(
                    "screenshot entries must match directory identity"
                )


class RemoteScreenshotDirectory:
    """Establish stable evidence through one fixed remote capability."""

    def __init__(
        self,
        host: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        terminate_timeout: float = 1.0,
        max_stderr_bytes: int = 64 * 1024,
    ):
        validate_ssh_host(host)
        if not callable(clock):
            raise ValueError("clock must be callable")
        if (
            isinstance(terminate_timeout, bool)
            or not isinstance(terminate_timeout, (int, float))
            or not math.isfinite(float(terminate_timeout))
            or terminate_timeout <= 0
        ):
            raise ValueError("terminate_timeout must be positive")
        if (
            isinstance(max_stderr_bytes, bool)
            or not isinstance(max_stderr_bytes, int)
            or max_stderr_bytes <= 0
        ):
            raise ValueError("max_stderr_bytes must be positive")
        if not callable(popen_factory):
            raise ValueError("popen_factory must be callable")

        self._argv = [
            SSH_PROGRAM,
            "-T",
            *SSH_BASE_OPTIONS,
            SSH_OPTION_TERMINATOR,
            host,
            REMOTE_EVIDENCE_PROGRAM,
        ]
        self._clock = clock
        self._popen_factory = popen_factory
        self._terminate_timeout = terminate_timeout
        self._max_stderr_bytes = max_stderr_bytes

    def observe_stable(self, deadline: float) -> DirectorySnapshot:
        """Return the second of two internally consecutive equal snapshots."""

        _validate_deadline(deadline)
        first = self._observe_once(deadline)
        second = self._observe_once(deadline)
        _require_before_deadline(
            self._clock,
            deadline,
            "stable observation comparison",
        )
        if first != second:
            raise DirectoryNotQuiescent(
                "remote screenshot evidence changed between observations"
            )
        _require_before_deadline(
            self._clock,
            deadline,
            "stable observation completion",
        )
        return second

    def _observe_once(self, deadline: float) -> DirectorySnapshot:
        _require_before_deadline(
            self._clock,
            deadline,
            "remote evidence process start",
        )
        try:
            process = BoundedProcess(
                self._argv,
                clock=self._clock,
                popen_factory=self._popen_factory,
                terminate_timeout=self._terminate_timeout,
                max_stderr_bytes=self._max_stderr_bytes,
                description="remote screenshot evidence",
            )
        except ProcessTransportError as error:
            raise RemoteDirectoryTransportError(str(error)) from error

        try:
            output = _read_and_close(process, deadline)
        except ProcessTimeout as error:
            raise RemoteDirectoryTimeout(str(error)) from error
        except ProcessTransportError as error:
            raise RemoteDirectoryTransportError(str(error)) from error
        _require_before_deadline(
            self._clock,
            deadline,
            "remote evidence process completion",
        )

        try:
            snapshot = _parse_snapshot(output)
        except RemoteDirectoryProtocolError:
            raise
        except (TypeError, ValueError) as error:
            raise RemoteDirectoryProtocolError(str(error)) from error
        _require_before_deadline(
            self._clock,
            deadline,
            "remote evidence parsing",
        )
        return snapshot


def _read_and_close(process: BoundedProcess, deadline: float) -> bytes:
    try:
        output = process.read_all(
            max_bytes=MAX_OUTPUT_BYTES,
            deadline=deadline,
        )
    except BaseException as primary:
        try:
            process.close()
        except BaseException as cleanup_error:
            raise primary from cleanup_error
        raise
    process.close()
    return output


def _parse_snapshot(output: bytes) -> DirectorySnapshot:
    if not isinstance(output, bytes):
        raise TypeError("evidence output must be bytes")
    if len(output) > MAX_OUTPUT_BYTES:
        raise RemoteDirectoryProtocolError(
            "remote evidence output exceeded its size bound"
        )
    if (
        not output
        or not output.endswith(b"\0")
        or output.endswith(b"\0\0")
    ):
        raise RemoteDirectoryProtocolError(
            "remote evidence must have exactly one terminal NUL"
        )

    raw_fields = output.split(b"\0")[:-1]
    if any(not field for field in raw_fields):
        raise RemoteDirectoryProtocolError(
            "remote evidence contains an empty field"
        )
    for field in raw_fields:
        if len(field) > MAX_FIELD_BYTES:
            raise RemoteDirectoryProtocolError(
                "remote evidence field exceeded its size bound"
            )
    try:
        fields = tuple(field.decode("ascii", "strict") for field in raw_fields)
    except UnicodeDecodeError as error:
        raise RemoteDirectoryProtocolError(
            "remote evidence is not strict ASCII"
        ) from error

    if not fields or fields[0] != PROTOCOL_VERSION:
        raise RemoteDirectoryProtocolError(
            "remote evidence protocol version does not match"
        )
    record_fields = fields[1:]
    minimum_fields = DIRECTORY_FIELD_COUNT * 2
    if len(record_fields) < minimum_fields:
        raise RemoteDirectoryProtocolError(
            "remote evidence is missing its directory fence"
        )
    middle_count = len(record_fields) - minimum_fields
    if middle_count % FILE_FIELD_COUNT:
        raise RemoteDirectoryProtocolError(
            "remote evidence has an incomplete file record"
        )
    entry_count = middle_count // FILE_FIELD_COUNT
    if entry_count > MAX_ENTRIES:
        raise RemoteDirectoryProtocolError(
            "remote evidence exceeded its entry bound"
        )

    header = _parse_directory(record_fields[:DIRECTORY_FIELD_COUNT])
    footer = _parse_directory(record_fields[-DIRECTORY_FIELD_COUNT:])
    if header != footer:
        raise RemoteDirectoryProtocolError(
            "remote directory fence does not match"
        )

    files = []
    middle = record_fields[DIRECTORY_FIELD_COUNT:-DIRECTORY_FIELD_COUNT]
    for offset in range(0, len(middle), FILE_FIELD_COUNT):
        files.append(
            _parse_file(middle[offset:offset + FILE_FIELD_COUNT])
        )
    try:
        return DirectorySnapshot(header, tuple(files))
    except (TypeError, ValueError) as error:
        raise RemoteDirectoryProtocolError(str(error)) from error


def _parse_directory(fields: tuple[str, ...]) -> DirectoryStamp:
    if len(fields) != DIRECTORY_FIELD_COUNT or fields[0] != DIRECTORY_TAG:
        raise RemoteDirectoryProtocolError(
            "remote directory record is malformed"
        )
    try:
        return DirectoryStamp(
            stat=_parse_stat(fields[1:11]),
            owner=fields[11],
            group=fields[12],
        )
    except (TypeError, ValueError) as error:
        raise RemoteDirectoryProtocolError(str(error)) from error


def _parse_file(fields: tuple[str, ...]) -> RemoteFileStamp:
    if len(fields) != FILE_FIELD_COUNT or fields[0] != FILE_TAG:
        raise RemoteDirectoryProtocolError(
            "remote file record is malformed"
        )
    try:
        return RemoteFileStamp(
            name=fields[1],
            stat=_parse_stat(fields[2:12]),
        )
    except (TypeError, ValueError) as error:
        raise RemoteDirectoryProtocolError(str(error)) from error


def _parse_stat(fields: tuple[str, ...]) -> StatStamp:
    if len(fields) != 10:
        raise RemoteDirectoryProtocolError(
            "remote stat record is malformed"
        )
    return StatStamp(
        file_type=fields[0],
        device=_parse_integer(fields[1], "device"),
        inode=_parse_integer(fields[2], "inode"),
        mode=_parse_octal(fields[3], "mode"),
        uid=_parse_integer(fields[4], "uid"),
        gid=_parse_integer(fields[5], "gid"),
        link_count=_parse_integer(fields[6], "link count"),
        size=_parse_integer(fields[7], "size"),
        mtime_ns=_parse_integer(fields[8], "mtime_ns"),
        ctime_ns=_parse_integer(fields[9], "ctime_ns"),
    )


def _parse_integer(value: str, name: str) -> int:
    if not _INTEGER_PATTERN.fullmatch(value):
        raise RemoteDirectoryProtocolError(
            "%s is not a canonical non-negative integer" % name
        )
    return int(value, 10)


def _parse_octal(value: str, name: str) -> int:
    if not _OCTAL_PATTERN.fullmatch(value):
        raise RemoteDirectoryProtocolError(
            "%s is not canonical one-to-four-digit octal" % name
        )
    return int(value, 8)


def _require_string(
    value: Any,
    name: str,
    *,
    nonempty: bool = False,
) -> None:
    if not isinstance(value, str):
        raise TypeError("%s must be a string" % name)
    if nonempty and not value:
        raise ValueError("%s must not be empty" % name)


def _require_integer(value: Any, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be an integer" % name)
    if value < minimum:
        raise ValueError("%s must be at least %d" % (name, minimum))


def _validate_deadline(deadline: float) -> None:
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("deadline must be a finite absolute timestamp")


def _require_before_deadline(
    clock: Callable[[], float],
    deadline: float,
    phase: str,
) -> None:
    if clock() >= deadline:
        raise RemoteDirectoryTimeout("%s missed its deadline" % phase)
