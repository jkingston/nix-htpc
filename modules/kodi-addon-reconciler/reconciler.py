"""Fail-closed reconciliation of userdata and immutable Kodi add-ons."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ADDON_ID_PATTERN = re.compile(
    r"^(?:plugin|resource|script|service)\.[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_~-]{0,63}$")
MAX_MANIFEST_BYTES = 64 * 1024
AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCL = 0x00000004


class ReconcileError(RuntimeError):
    """Raised before unsafe or ambiguous state can be changed."""


@dataclass(frozen=True)
class Identity:
    version: str
    manifest_sha256: str

    @classmethod
    def from_mapping(cls, value: Any, label: str) -> "Identity":
        if not isinstance(value, dict) or set(value) != {
            "manifest_sha256",
            "version",
        }:
            raise ReconcileError(f"{label}: identity has unexpected fields")
        version = value["version"]
        manifest_sha256 = value["manifest_sha256"]
        if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
            raise ReconcileError(f"{label}: identity has an invalid version")
        if (
            not isinstance(manifest_sha256, str)
            or not HASH_PATTERN.fullmatch(manifest_sha256)
        ):
            raise ReconcileError(f"{label}: identity has an invalid hash")
        return cls(version, manifest_sha256)


@dataclass(frozen=True)
class Managed:
    addon_path: Path
    manifest_path: Path
    identity: Identity

    @classmethod
    def from_mapping(cls, value: Any, addon_id: str) -> "Managed | None":
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {
            "addon_path",
            "identity",
            "manifest_path",
        }:
            raise ReconcileError(f"{addon_id}: managed source has unexpected fields")
        addon_path = _absolute_path(value["addon_path"], addon_id)
        manifest_path = _absolute_path(value["manifest_path"], addon_id)
        if manifest_path != addon_path / "addon.xml":
            raise ReconcileError(
                f"{addon_id}: managed manifest must be the add-on's addon.xml"
            )
        return cls(
            addon_path,
            manifest_path,
            Identity.from_mapping(value["identity"], f"{addon_id} managed"),
        )


@dataclass(frozen=True)
class AddonSpec:
    addon_id: str
    userdata: Identity
    managed: Managed | None

    @classmethod
    def from_mapping(cls, value: Any) -> "AddonSpec":
        if not isinstance(value, dict) or set(value) != {
            "addon_id",
            "managed",
            "userdata",
        }:
            raise ReconcileError("add-on specification has unexpected fields")
        addon_id = value["addon_id"]
        if not isinstance(addon_id, str) or not ADDON_ID_PATTERN.fullmatch(addon_id):
            raise ReconcileError("add-on specification has an invalid ID")
        return cls(
            addon_id,
            Identity.from_mapping(value["userdata"], f"{addon_id} userdata"),
            Managed.from_mapping(value["managed"], addon_id),
        )


@dataclass(frozen=True)
class Move:
    addon_id: str
    operation: str
    source: Path
    destination: Path


@dataclass(frozen=True)
class Configuration:
    active_root: Path
    backup_root: Path
    backup_uid: int
    backup_gid: int
    backup_mode: int
    specs: tuple[AddonSpec, ...]


@dataclass(frozen=True)
class Runtime:
    rename_noreplace: Callable[[Path, Path], None]
    device: Callable[[Path], int]


def _absolute_path(value: Any, label: str) -> Path:
    if (
        not isinstance(value, str)
        or not Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ReconcileError(f"{label}: path must be absolute")
    return Path(value)


def load_configuration(path: Path) -> Configuration:
    raw = _read_bounded_regular(path, 256 * 1024, "immutable configuration")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReconcileError("immutable configuration is invalid JSON") from error
    expected = {
        "active_root",
        "backup_gid",
        "backup_mode",
        "backup_root",
        "backup_uid",
        "schema_version",
        "specs",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ReconcileError("configuration has unexpected fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ReconcileError("unsupported configuration schema")
    integers = ("backup_uid", "backup_gid", "backup_mode")
    if any(type(value[key]) is not int or value[key] < 0 for key in integers):
        raise ReconcileError("backup ownership and mode must be non-negative integers")
    if value["backup_mode"] != 0o700:
        raise ReconcileError("backup root mode must be 0700")
    if not isinstance(value["specs"], list):
        raise ReconcileError("configuration specs must be a list")
    specs = tuple(AddonSpec.from_mapping(item) for item in value["specs"])
    ids = [spec.addon_id for spec in specs]
    if len(ids) != len(set(ids)):
        raise ReconcileError("configuration contains duplicate add-on IDs")
    active_root = _absolute_path(value["active_root"], "active root")
    backup_root = _absolute_path(value["backup_root"], "backup root")
    if (
        active_root == backup_root
        or backup_root.is_relative_to(active_root)
        or active_root.is_relative_to(backup_root)
    ):
        raise ReconcileError("active and backup roots must not overlap")
    return Configuration(
        active_root,
        backup_root,
        value["backup_uid"],
        value["backup_gid"],
        value["backup_mode"],
        specs,
    )


def _path_kind(path: Path) -> str:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "regular"
    return "other"


def _read_bounded_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReconcileError(f"{label} could not be opened safely") from error
    source_stat = os.fstat(descriptor)
    if not stat.S_ISREG(source_stat.st_mode):
        os.close(descriptor)
        raise ReconcileError(f"{label} is not a regular file")
    with os.fdopen(descriptor, "rb") as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ReconcileError(f"{label} exceeds the size limit")
    return raw


def _validate_optional_root(
    path: Path,
    label: str,
    required_owner: tuple[int, int] | None = None,
    required_mode: int | None = None,
) -> None:
    kind = _path_kind(path)
    if kind == "absent":
        return
    if kind != "directory":
        raise ReconcileError(f"{label} must be absent or a real directory")
    root_stat = os.stat(path, follow_symlinks=False)
    if required_owner is not None and (
        root_stat.st_uid,
        root_stat.st_gid,
    ) != required_owner:
        raise ReconcileError(f"{label} has unexpected ownership")
    if required_mode is not None and stat.S_IMODE(root_stat.st_mode) != required_mode:
        raise ReconcileError(f"{label} has unexpected permissions")


def validate_addon(
    addon_path: Path,
    addon_id: str,
    identity: Identity,
    manifest_path: Path | None = None,
) -> None:
    if _path_kind(addon_path) != "directory":
        raise ReconcileError(f"{addon_id}: add-on path is not a real directory")
    manifest = manifest_path or addon_path / "addon.xml"
    raw = _read_bounded_regular(
        manifest,
        MAX_MANIFEST_BYTES,
        f"{addon_id}: manifest",
    )
    if hashlib.sha256(raw).hexdigest() != identity.manifest_sha256:
        raise ReconcileError(f"{addon_id}: manifest hash does not match")
    if b"<!DOCTYPE" in raw.upper():
        raise ReconcileError(f"{addon_id}: manifest must not contain a doctype")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ReconcileError(f"{addon_id}: manifest is invalid XML") from error
    if root.tag != "addon" or root.get("id") != addon_id:
        raise ReconcileError(f"{addon_id}: manifest ID does not match")
    if root.get("version") != identity.version:
        raise ReconcileError(f"{addon_id}: manifest version does not match")


def _device(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_dev


def plan_reconciliation(
    configuration: Configuration,
    device: Callable[[Path], int] = _device,
) -> tuple[Move, ...]:
    _validate_optional_root(configuration.active_root, "active root")
    _validate_optional_root(
        configuration.backup_root,
        "backup root",
        (configuration.backup_uid, configuration.backup_gid),
        configuration.backup_mode,
    )
    ids = [spec.addon_id for spec in configuration.specs]
    if len(ids) != len(set(ids)):
        raise ReconcileError("duplicate add-on IDs are not allowed")

    moves: list[Move] = []
    for spec in configuration.specs:
        active = configuration.active_root / spec.addon_id
        backup = configuration.backup_root / spec.addon_id
        active_kind = _path_kind(active)
        backup_kind = _path_kind(backup)
        if active_kind not in {"absent", "directory"}:
            raise ReconcileError(
                f"{spec.addon_id}: active entry is not a real directory"
            )
        if backup_kind not in {"absent", "directory"}:
            raise ReconcileError(
                f"{spec.addon_id}: backup entry is not a real directory"
            )
        if active_kind == "directory" and backup_kind == "directory":
            raise ReconcileError(
                f"{spec.addon_id}: active and backup copies both exist"
            )
        if active_kind == "directory":
            validate_addon(active, spec.addon_id, spec.userdata)
        if backup_kind == "directory":
            validate_addon(backup, spec.addon_id, spec.userdata)
        if spec.managed is not None:
            validate_addon(
                spec.managed.addon_path,
                spec.addon_id,
                spec.managed.identity,
                spec.managed.manifest_path,
            )
            if active_kind == "directory":
                moves.append(Move(spec.addon_id, "backup", active, backup))
        elif backup_kind == "directory":
            moves.append(Move(spec.addon_id, "restore", backup, active))

    for move in moves:
        source_device = device(move.source)
        destination_root = move.destination.parent
        destination_anchor = (
            destination_root
            if _path_kind(destination_root) == "directory"
            else destination_root.parent
        )
        if source_device != device(destination_anchor):
            raise ReconcileError(
                f"{move.addon_id}: source and destination differ by filesystem"
            )
    return tuple(moves)


def _ensure_root(path: Path, uid: int, gid: int, mode: int) -> bool:
    kind = _path_kind(path)
    if kind == "directory":
        return False
    if kind != "absent" or _path_kind(path.parent) != "directory":
        raise ReconcileError("cannot create reconciliation root safely")
    os.mkdir(path, mode)
    created = os.stat(path, follow_symlinks=False)
    if (created.st_uid, created.st_gid) != (uid, gid):
        os.chown(path, uid, gid)
    os.chmod(path, mode)
    return True


def rename_noreplace(source: Path, destination: Path) -> None:
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        libc.renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        libc.renameat2.restype = ctypes.c_int
        result = libc.renameat2(
            AT_FDCWD,
            source_bytes,
            AT_FDCWD,
            destination_bytes,
            RENAME_NOREPLACE,
        )
    elif sys.platform == "darwin":
        libc.renamex_np.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        libc.renamex_np.restype = ctypes.c_int
        result = libc.renamex_np(source_bytes, destination_bytes, RENAME_EXCL)
    else:
        raise ReconcileError("atomic no-replace rename is unsupported on this platform")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def _prepare_roots(
    configuration: Configuration,
    moves: tuple[Move, ...],
) -> tuple[Path, ...]:
    created: list[Path] = []
    try:
        if any(move.operation == "backup" for move in moves):
            if _ensure_root(
                configuration.backup_root,
                configuration.backup_uid,
                configuration.backup_gid,
                configuration.backup_mode,
            ):
                created.append(configuration.backup_root)
        if any(move.operation == "restore" for move in moves):
            parent_stat = os.stat(
                configuration.active_root.parent,
                follow_symlinks=False,
            )
            if _ensure_root(
                configuration.active_root,
                parent_stat.st_uid,
                parent_stat.st_gid,
                0o755,
            ):
                created.append(configuration.active_root)
    except (OSError, ReconcileError):
        for path in reversed(created):
            try:
                os.rmdir(path)
            except OSError:
                pass
        raise
    return tuple(created)


def apply_plan(
    configuration: Configuration,
    moves: tuple[Move, ...],
    rename: Callable[[Path, Path], None] = rename_noreplace,
) -> tuple[Move, ...]:
    if not moves:
        return moves
    try:
        created_roots = _prepare_roots(configuration, moves)
    except OSError as error:
        raise ReconcileError("could not prepare reconciliation roots") from error
    applied: list[Move] = []
    try:
        for move in moves:
            rename(move.source, move.destination)
            applied.append(move)
    except OSError as error:
        rollback_error = None
        for move in reversed(applied):
            try:
                rename(move.destination, move.source)
            except OSError as rollback_failure:
                rollback_error = rollback_failure
                break
        if rollback_error is not None:
            raise ReconcileError(
                "atomic move failed and rollback could not restore prior state"
            ) from rollback_error
        for path in reversed(created_roots):
            try:
                os.rmdir(path)
            except OSError:
                pass
        if error.errno == errno.EXDEV:
            raise ReconcileError("atomic move crossed filesystems") from error
        if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise ReconcileError(
                "atomic move refused to overwrite destination"
            ) from error
        raise ReconcileError("atomic move failed; prior state was restored") from error
    return moves


def reconcile(
    configuration: Configuration,
    runtime: Runtime | None = None,
) -> tuple[Move, ...]:
    runtime = runtime or Runtime(rename_noreplace, _device)
    try:
        moves = plan_reconciliation(configuration, runtime.device)
    except OSError as error:
        raise ReconcileError("filesystem preflight failed") from error
    return apply_plan(configuration, moves, runtime.rename_noreplace)
