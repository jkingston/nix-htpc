#!/usr/bin/env python3
"""Emit bounded, passive evidence for the managed Kodi screenshot directory."""

from __future__ import annotations

import grp
import os
import pwd
import re
import stat
import sys
from dataclasses import dataclass
from typing import Any, BinaryIO, Iterable, Sequence, TextIO


SCREENSHOT_DIRECTORY = "@KODI_SCREENSHOT_PATH@"
SCREENSHOT_PARENT, SCREENSHOT_NAME = os.path.split(SCREENSHOT_DIRECTORY)
PROTOCOL_VERSION = "KODI-SCREENSHOT-EVIDENCE/1"
MAX_ENTRIES = 4096
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_FIELD_BYTES = 4096
MAX_FILENAME_BYTES = 255
MAX_ERROR_BYTES = 512
OPEN_FLAGS = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | os.O_NOFOLLOW
    | os.O_CLOEXEC
)
_O_PATH = getattr(os, "O_PATH", None)
ENTRY_OPEN_FLAGS = (
    None
    if _O_PATH is None
    else _O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
)
_SCREENSHOT_NAME = re.compile(r"\Ascreenshot[0-9]+\.png\Z")


class EvidenceError(Exception):
    """The directory cannot be represented as safe, stable evidence."""


@dataclass(frozen=True)
class StatEvidence:
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


@dataclass(frozen=True)
class FileEvidence:
    name: str
    stat: StatEvidence


@dataclass(frozen=True)
class DirectoryEvidence:
    stat: StatEvidence
    owner: str
    group: str
    files: tuple[FileEvidence, ...]


class OsFilesystem:
    """The fixed, read-only POSIX calls used by the installed helper."""

    def open_parent(self) -> int:
        return os.open(SCREENSHOT_PARENT, OPEN_FLAGS)

    def open_directory(self, parent_fd: int) -> int:
        return os.open(
            SCREENSHOT_NAME,
            OPEN_FLAGS,
            dir_fd=parent_fd,
        )

    def iter_names(self, directory_fd: int) -> Iterable[str]:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                yield entry.name

    def fstat(self, directory_fd: int) -> os.stat_result:
        return os.fstat(directory_fd)

    def open_entry(
        self,
        directory_fd: int,
        name: str,
    ) -> int:
        if ENTRY_OPEN_FLAGS is None:
            raise EvidenceError(
                "kodi-screenshot-evidence requires Linux O_PATH"
            )
        return os.open(
            name,
            ENTRY_OPEN_FLAGS,
            dir_fd=directory_fd,
        )

    def fstat_entry(self, entry_fd: int) -> os.stat_result:
        return os.fstat(entry_fd)

    def close_entry(self, entry_fd: int) -> None:
        os.close(entry_fd)

    def stat_path(self, parent_fd: int) -> os.stat_result:
        return os.stat(
            SCREENSHOT_NAME,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )

    def owner_name(self, uid: int) -> str:
        return pwd.getpwuid(uid).pw_name

    def group_name(self, gid: int) -> str:
        return grp.getgrgid(gid).gr_name

    def close(self, directory_fd: int) -> None:
        os.close(directory_fd)

    def close_parent(self, parent_fd: int) -> None:
        os.close(parent_fd)


def collect_evidence(filesystem: Any) -> DirectoryEvidence:
    """Collect evidence through anchored parent and directory descriptors."""

    parent_fd = filesystem.open_parent()
    try:
        evidence = _collect_directory(filesystem, parent_fd)
    except BaseException as primary:
        _close_preserving(filesystem.close_parent, parent_fd, primary)
        raise
    else:
        filesystem.close_parent(parent_fd)
    return evidence


def _collect_directory(
    filesystem: Any,
    parent_fd: int,
) -> DirectoryEvidence:
    directory_fd = filesystem.open_directory(parent_fd)
    try:
        before = _stat_evidence(filesystem.fstat(directory_fd))
        _require_directory(before)
        owner = _strict_text(
            filesystem.owner_name(before.uid),
            "directory owner",
        )
        group = _strict_text(
            filesystem.group_name(before.gid),
            "directory group",
        )
        if owner != "htpc" or group != "users" or before.mode != 0o700:
            raise EvidenceError(
                "managed directory must be owned by htpc:users with mode 0700"
            )

        names = _collect_names(filesystem.iter_names(directory_fd))
        files = []
        for name in names:
            files.append(
                _collect_file(filesystem, directory_fd, name, before)
            )

        after = _stat_evidence(filesystem.fstat(directory_fd))
        if before != after:
            raise EvidenceError(
                "managed directory metadata changed during enumeration"
            )
        pathname = _stat_evidence(filesystem.stat_path(parent_fd))
        if pathname != after:
            raise EvidenceError(
                "managed pathname no longer identifies the open directory"
            )
    except BaseException as primary:
        try:
            filesystem.close(directory_fd)
        except BaseException as cleanup_error:
            raise primary from cleanup_error
        raise
    else:
        filesystem.close(directory_fd)

    return DirectoryEvidence(before, owner, group, tuple(files))


def _collect_file(
    filesystem: Any,
    directory_fd: int,
    name: str,
    directory: StatEvidence,
) -> FileEvidence:
    """Prove one name resolves twice to the same anchored regular file."""

    first_fd = filesystem.open_entry(directory_fd, name)
    try:
        first = _stat_evidence(filesystem.fstat_entry(first_fd))
        _require_file(first, directory)

        second_fd = filesystem.open_entry(directory_fd, name)
        try:
            second = _stat_evidence(filesystem.fstat_entry(second_fd))
            _require_file(second, directory)
            if first != second:
                raise EvidenceError(
                    "managed directory entry changed during collection"
                )
        except BaseException as primary:
            _close_preserving(
                filesystem.close_entry,
                second_fd,
                primary,
            )
            raise
        else:
            filesystem.close_entry(second_fd)
    except BaseException as primary:
        _close_preserving(filesystem.close_entry, first_fd, primary)
        raise
    else:
        filesystem.close_entry(first_fd)

    return FileEvidence(name, first)


def _close_preserving(
    close: Any,
    descriptor: int,
    primary: BaseException,
) -> None:
    try:
        close(descriptor)
    except BaseException as cleanup_error:
        raise primary from cleanup_error


def encode_evidence(evidence: DirectoryEvidence) -> bytes:
    """Encode one complete, versioned NUL-field protocol message."""

    if not isinstance(evidence, DirectoryEvidence):
        raise TypeError("evidence must be DirectoryEvidence")
    output = bytearray()
    _append_field(output, PROTOCOL_VERSION)
    _append_directory(output, evidence)
    for file in evidence.files:
        _append_file(output, file)
    _append_directory(output, evidence)
    return bytes(output)


def _run(
    arguments: Sequence[str],
    filesystem: Any,
    stdout: BinaryIO,
    stderr: TextIO,
) -> int:
    if arguments:
        _report_error(stderr, "accepts no arguments")
        return 2
    try:
        evidence = collect_evidence(filesystem)
        encoded = encode_evidence(evidence)
    except (EvidenceError, OSError, KeyError, UnicodeError, ValueError) as error:
        _report_error(stderr, str(error))
        return 1

    try:
        _write_all(stdout, encoded)
        stdout.flush()
    except (BrokenPipeError, OSError, ValueError) as error:
        _report_error(stderr, "output failed: %s" % error)
        return 1
    return 0


def main() -> int:
    return _run(
        sys.argv[1:],
        OsFilesystem(),
        sys.stdout.buffer,
        sys.stderr,
    )


def _collect_names(names: Iterable[str]) -> tuple[str, ...]:
    collected = []
    seen = set()
    for name in names:
        if len(collected) >= MAX_ENTRIES:
            raise EvidenceError(
                "managed directory exceeded %d entries" % MAX_ENTRIES
            )
        if not isinstance(name, str) or not _SCREENSHOT_NAME.fullmatch(name):
            raise EvidenceError("managed directory contains an unsafe name")
        try:
            encoded = name.encode("ascii", "strict")
        except UnicodeEncodeError as error:
            raise EvidenceError(
                "managed directory name is not ASCII"
            ) from error
        if len(encoded) > MAX_FILENAME_BYTES:
            raise EvidenceError("managed directory name is too long")
        if name in seen:
            raise EvidenceError("managed directory returned a duplicate name")
        seen.add(name)
        collected.append(name)
    return tuple(sorted(collected))


def _stat_evidence(value: Any) -> StatEvidence:
    raw_mode = _stat_integer(value, "st_mode")
    return StatEvidence(
        file_type=_file_type(raw_mode),
        device=_stat_integer(value, "st_dev"),
        inode=_stat_integer(value, "st_ino", minimum=1),
        mode=stat.S_IMODE(raw_mode),
        uid=_stat_integer(value, "st_uid"),
        gid=_stat_integer(value, "st_gid"),
        link_count=_stat_integer(value, "st_nlink", minimum=1),
        size=_stat_integer(value, "st_size"),
        mtime_ns=_stat_integer(value, "st_mtime_ns"),
        ctime_ns=_stat_integer(value, "st_ctime_ns"),
    )


def _stat_integer(
    value: Any,
    attribute: str,
    *,
    minimum: int = 0,
) -> int:
    result = getattr(value, attribute)
    if isinstance(result, bool) or not isinstance(result, int):
        raise EvidenceError("%s is not an integer" % attribute)
    if result < minimum:
        raise EvidenceError("%s is below its safe minimum" % attribute)
    return result


def _file_type(mode: int) -> str:
    kinds = (
        (stat.S_ISREG, "f"),
        (stat.S_ISDIR, "d"),
        (stat.S_ISLNK, "l"),
        (stat.S_ISFIFO, "p"),
        (stat.S_ISSOCK, "s"),
        (stat.S_ISBLK, "b"),
        (stat.S_ISCHR, "c"),
    )
    for predicate, code in kinds:
        if predicate(mode):
            return code
    return "?"


def _require_directory(value: StatEvidence) -> None:
    if value.file_type != "d":
        raise EvidenceError("managed screenshot path is not a directory")


def _require_file(value: StatEvidence, directory: StatEvidence) -> None:
    if value.file_type != "f":
        raise EvidenceError("managed directory entry is not a regular file")
    if value.link_count != 1:
        raise EvidenceError("managed directory entry is hard-linked")
    if value.device != directory.device:
        raise EvidenceError("managed directory entry is on another device")
    if value.uid != directory.uid or value.gid != directory.gid:
        raise EvidenceError(
            "managed directory entry ownership does not match its directory"
        )


def _strict_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError("%s is not a non-empty string" % name)
    encoded = value.encode("utf-8", "strict")
    if b"\0" in encoded or len(encoded) > MAX_FIELD_BYTES:
        raise EvidenceError("%s is not safely bounded" % name)
    return value


def _append_directory(
    output: bytearray,
    evidence: DirectoryEvidence,
) -> None:
    _append_field(output, "D")
    _append_stat(output, evidence.stat)
    _append_field(output, evidence.owner)
    _append_field(output, evidence.group)


def _append_file(output: bytearray, evidence: FileEvidence) -> None:
    _append_field(output, "F")
    _append_field(output, evidence.name)
    _append_stat(output, evidence.stat)


def _append_stat(output: bytearray, evidence: StatEvidence) -> None:
    _append_field(output, evidence.file_type)
    _append_integer(output, evidence.device)
    _append_integer(output, evidence.inode)
    _append_field(output, format(evidence.mode, "o"))
    _append_integer(output, evidence.uid)
    _append_integer(output, evidence.gid)
    _append_integer(output, evidence.link_count)
    _append_integer(output, evidence.size)
    _append_integer(output, evidence.mtime_ns)
    _append_integer(output, evidence.ctime_ns)


def _append_integer(output: bytearray, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceError("protocol integer is not non-negative")
    _append_field(output, str(value))


def _append_field(output: bytearray, value: str) -> None:
    if not isinstance(value, str):
        raise EvidenceError("protocol field is not text")
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as error:
        raise EvidenceError("protocol field is not ASCII") from error
    if not encoded or b"\0" in encoded:
        raise EvidenceError("protocol field is empty or contains NUL")
    if len(encoded) > MAX_FIELD_BYTES:
        raise EvidenceError("protocol field exceeded its size bound")
    if len(output) + len(encoded) + 1 > MAX_OUTPUT_BYTES:
        raise EvidenceError(
            "protocol output exceeded %d bytes" % MAX_OUTPUT_BYTES
        )
    output.extend(encoded)
    output.append(0)


def _write_all(stdout: BinaryIO, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = stdout.write(remaining)
        if (
            isinstance(written, bool)
            or not isinstance(written, int)
            or written <= 0
            or written > len(remaining)
        ):
            raise ValueError("binary output write made invalid progress")
        remaining = remaining[written:]


def _report_error(stderr: TextIO, detail: str) -> None:
    safe_detail = "".join(
        character if " " <= character <= "~" else "?"
        for character in detail
    )
    prefix = "kodi-screenshot-evidence: "
    available = MAX_ERROR_BYTES - len(prefix) - 1
    stderr.write(prefix + safe_detail[:available] + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
