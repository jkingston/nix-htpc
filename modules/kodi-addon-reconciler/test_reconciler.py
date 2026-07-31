from __future__ import annotations

import errno
import hashlib
import itertools
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from reconciler import (
    AddonSpec,
    Configuration,
    Identity,
    Managed,
    ReconcileError,
    Runtime,
    load_configuration,
    reconcile,
    rename_noreplace,
)


ADDONS = (
    ("script.module.simplejson", "3.19.1+matrix.1"),
    ("script.bingie.helper", "1.1.2"),
)
STATES = ("neither", "active", "backup", "both")


def _manifest(addon_id: str, version: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<addon id="{addon_id}" version="{version}" name="fixture"/>\n'
    ).encode()


def _identity(addon_id: str, version: str) -> Identity:
    return Identity(version, hashlib.sha256(_manifest(addon_id, version)).hexdigest())


def _write_addon(path: Path, addon_id: str, version: str) -> None:
    path.mkdir(parents=True)
    (path / "addon.xml").write_bytes(_manifest(addon_id, version))
    (path / "payload.txt").write_text(addon_id, encoding="utf-8")


def _snapshot(root: Path) -> dict[str, tuple]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            result[relative] = (
                "symlink",
                os.lstat(path).st_uid,
                os.lstat(path).st_gid,
                os.readlink(path),
            )
        elif stat.S_ISDIR(mode):
            result[relative] = (
                "directory",
                os.lstat(path).st_uid,
                os.lstat(path).st_gid,
                stat.S_IMODE(mode),
            )
        elif stat.S_ISREG(mode):
            result[relative] = (
                "regular",
                os.lstat(path).st_uid,
                os.lstat(path).st_gid,
                stat.S_IMODE(mode),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            result[relative] = ("other",)
    return result


def _rename_noreplace(source: Path, destination: Path) -> None:
    if os.path.lexists(destination):
        raise OSError(errno.EEXIST, "destination exists")
    os.rename(source, destination)


TEST_RUNTIME = Runtime(_rename_noreplace, lambda path: os.stat(path).st_dev)


class Fixture:
    def __init__(
        self,
        root: Path,
        states: tuple[str, str],
        managed=False,
        production_topology=False,
    ):
        self.root = root
        self.kodi_root = (
            root / "home" / "htpc" / ".kodi" if production_topology else root / ".kodi"
        )
        self.active_root = self.kodi_root / "addons"
        self.backup_root = (
            root / "var" / "lib" / "nix-htpc" / "kodi-addon-backups"
            if production_topology
            else self.kodi_root / "htpc-addon-backups"
        )
        self.addon_data = self.kodi_root / "userdata" / "addon_data"
        self.addon_data.mkdir(parents=True)
        (self.addon_data / "settings.xml").write_text("preserve", encoding="utf-8")
        self.managed_root = root / "nix-store"
        self.specs = []
        for (addon_id, version), state in zip(ADDONS, states):
            if state in {"active", "both"}:
                _write_addon(self.active_root / addon_id, addon_id, version)
            if state in {"backup", "both"}:
                _write_addon(self.backup_root / addon_id, addon_id, version)
            managed_value = None
            if managed:
                managed_version = f"9.{len(self.specs)}"
                managed_path = self.managed_root / addon_id
                _write_addon(managed_path, addon_id, managed_version)
                managed_value = Managed(
                    managed_path,
                    managed_path / "addon.xml",
                    _identity(addon_id, managed_version),
                )
            self.specs.append(
                AddonSpec(addon_id, _identity(addon_id, version), managed_value)
            )
        if self.backup_root.exists():
            self.backup_root.chmod(0o700)
        self.configuration = Configuration(
            self.active_root,
            self.backup_root,
            os.getuid(),
            os.getgid(),
            0o700,
            tuple(self.specs),
        )


class ReconcilerTest(unittest.TestCase):
    def test_all_sixteen_unmanaged_state_combinations(self):
        for states in itertools.product(STATES, repeat=2):
            with self.subTest(states=states), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory), states)
                before_addon_data = _snapshot(fixture.addon_data)
                before_inodes = {
                    addon_id: os.lstat(fixture.backup_root / addon_id).st_ino
                    for (addon_id, _version), state in zip(ADDONS, states)
                    if state == "backup"
                }
                if "both" in states:
                    before = _snapshot(fixture.root)
                    with self.assertRaisesRegex(ReconcileError, "both exist"):
                        reconcile(fixture.configuration, TEST_RUNTIME)
                    self.assertEqual(_snapshot(fixture.root), before)
                    continue

                moves = reconcile(fixture.configuration, TEST_RUNTIME)

                self.assertEqual(
                    [move.addon_id for move in moves],
                    [
                        addon_id
                        for (addon_id, _version), state in zip(ADDONS, states)
                        if state == "backup"
                    ],
                )
                for (addon_id, _version), state in zip(ADDONS, states):
                    active = fixture.active_root / addon_id
                    backup = fixture.backup_root / addon_id
                    self.assertEqual(active.exists(), state in {"active", "backup"})
                    self.assertFalse(backup.exists())
                    if state == "backup":
                        self.assertEqual(os.lstat(active).st_ino, before_inodes[addon_id])
                self.assertEqual(_snapshot(fixture.addon_data), before_addon_data)

    def test_generation_a_active_only_is_a_byte_exact_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), ("active", "active"))
            before = _snapshot(fixture.root)
            calls = []
            runtime = Runtime(
                lambda source, destination: calls.append((source, destination)),
                TEST_RUNTIME.device,
            )

            self.assertEqual(reconcile(fixture.configuration, runtime), ())

            self.assertEqual(calls, [])
            self.assertEqual(_snapshot(fixture.root), before)
            self.assertFalse(fixture.backup_root.exists())

    def test_restore_order_is_simplejson_then_helper_and_replay_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), ("backup", "backup"))

            first = reconcile(fixture.configuration, TEST_RUNTIME)
            second = reconcile(fixture.configuration, TEST_RUNTIME)

            self.assertEqual(
                [move.addon_id for move in first],
                ["script.module.simplejson", "script.bingie.helper"],
            )
            self.assertEqual(second, ())

    def test_two_addon_a_to_managed_b_to_a_round_trip_is_exact(self):
        for production_topology in (False, True):
            with (
                self.subTest(production_topology=production_topology),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = Fixture(
                    Path(directory),
                    ("active", "active"),
                    managed=True,
                    production_topology=production_topology,
                )
                fixture.backup_root.mkdir(parents=True)
                fixture.backup_root.chmod(0o700)
                for index, (addon_id, _version) in enumerate(ADDONS):
                    addon_path = fixture.active_root / addon_id
                    addon_path.chmod(0o750 - index)
                    (addon_path / "payload.txt").chmod(0o640 + index)
                before = _snapshot(fixture.root)
                before_inodes = {
                    addon_id: os.lstat(fixture.active_root / addon_id).st_ino
                    for addon_id, _version in ADDONS
                }

                backed_up = reconcile(fixture.configuration, TEST_RUNTIME)
                unmanaged = Configuration(
                    fixture.active_root,
                    fixture.backup_root,
                    os.getuid(),
                    os.getgid(),
                    0o700,
                    tuple(
                        AddonSpec(spec.addon_id, spec.userdata, None)
                        for spec in fixture.specs
                    ),
                )
                restored = reconcile(unmanaged, TEST_RUNTIME)

                self.assertEqual(
                    [move.operation for move in backed_up],
                    ["backup", "backup"],
                )
                self.assertEqual(
                    [move.operation for move in restored],
                    ["restore", "restore"],
                )
                for addon_id, _version in ADDONS:
                    self.assertEqual(
                        os.lstat(fixture.active_root / addon_id).st_ino,
                        before_inodes[addon_id],
                    )
                self.assertEqual(_snapshot(fixture.root), before)

    def test_all_sixteen_managed_state_combinations(self):
        for states in itertools.product(STATES, repeat=2):
            with (
                self.subTest(states=states),
                tempfile.TemporaryDirectory() as directory,
            ):
                fixture = Fixture(Path(directory), states, managed=True)
                before = _snapshot(fixture.root)
                before_addon_data = _snapshot(fixture.addon_data)
                if "both" in states:
                    with self.assertRaisesRegex(ReconcileError, "both exist"):
                        reconcile(fixture.configuration, TEST_RUNTIME)
                    self.assertEqual(_snapshot(fixture.root), before)
                    continue

                moves = reconcile(fixture.configuration, TEST_RUNTIME)

                self.assertEqual(
                    [move.addon_id for move in moves],
                    [
                        addon_id
                        for (addon_id, _version), state in zip(ADDONS, states)
                        if state == "active"
                    ],
                )
                self.assertTrue(
                    all(move.operation == "backup" for move in moves)
                )
                for (addon_id, _version), state in zip(ADDONS, states):
                    active = fixture.active_root / addon_id
                    backup = fixture.backup_root / addon_id
                    self.assertFalse(active.exists())
                    self.assertEqual(
                        backup.exists(),
                        state in {"active", "backup"},
                    )
                self.assertEqual(
                    _snapshot(fixture.addon_data),
                    before_addon_data,
                )

    def test_global_preflight_prevents_first_restore_when_second_is_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), ("backup", "backup"))
            second_manifest = fixture.backup_root / ADDONS[1][0] / "addon.xml"
            second_manifest.write_bytes(_manifest("script.wrong", ADDONS[1][1]))
            before = _snapshot(fixture.root)

            with self.assertRaises(ReconcileError):
                reconcile(fixture.configuration, TEST_RUNTIME)

            self.assertEqual(_snapshot(fixture.root), before)

    def test_invalid_entries_and_manifests_fail_without_mutation(self):
        mutations = (
            ("add-on symlink", self._active_symlink),
            ("add-on regular file", self._active_regular),
            ("manifest symlink", self._manifest_symlink),
            ("manifest directory", self._manifest_directory),
            ("wrong ID", lambda fixture: self._replace_manifest(fixture, "script.wrong", "1.1.2")),
            ("wrong version", lambda fixture: self._replace_manifest(fixture, ADDONS[1][0], "0")),
            ("wrong hash", self._wrong_hash),
            ("oversized manifest", self._oversized_manifest),
            ("doctype manifest", self._doctype_manifest),
            ("backup root permissions", self._backup_permissions),
            ("backup root ownership", self._backup_ownership),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                fixture = Fixture(Path(directory), ("backup", "active"))
                mutate(fixture)
                before = _snapshot(fixture.root)
                with self.assertRaises(ReconcileError):
                    reconcile(fixture.configuration, TEST_RUNTIME)
                self.assertEqual(_snapshot(fixture.root), before)

    def test_cross_device_is_rejected_before_root_creation_or_move(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), ("backup", "neither"))
            before = _snapshot(fixture.root)

            def different_devices(path):
                return 2 if path == fixture.active_root.parent else 1

            with self.assertRaisesRegex(ReconcileError, "differ by filesystem"):
                reconcile(
                    fixture.configuration,
                    Runtime(_rename_noreplace, different_devices),
                )
            self.assertEqual(_snapshot(fixture.root), before)

    def test_injected_second_move_failure_rolls_back_first_and_created_root(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), ("backup", "backup"))
            before = _snapshot(fixture.root)
            calls = 0

            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError(errno.EIO, "injected")
                _rename_noreplace(source, destination)

            with self.assertRaisesRegex(ReconcileError, "prior state was restored"):
                reconcile(
                    fixture.configuration,
                    Runtime(fail_second, TEST_RUNTIME.device),
                )
            self.assertEqual(_snapshot(fixture.root), before)

    def test_racing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Fixture(Path(directory), ("backup", "neither"))
            source = fixture.backup_root / ADDONS[0][0]

            def race(_source, destination):
                destination.mkdir(parents=True)
                (destination / "race").write_text("external", encoding="utf-8")
                raise OSError(errno.EEXIST, "raced")

            with self.assertRaisesRegex(ReconcileError, "refused to overwrite"):
                reconcile(
                    fixture.configuration,
                    Runtime(race, TEST_RUNTIME.device),
                )
            self.assertTrue(source.is_dir())
            self.assertEqual(
                (fixture.active_root / ADDONS[0][0] / "race").read_text(),
                "external",
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") or sys.platform == "darwin",
        "requires renameat2 or renamex_np",
    )
    def test_platform_atomic_rename_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.write_text("source", encoding="utf-8")
            destination.write_text("destination", encoding="utf-8")

            with self.assertRaises(OSError) as raised:
                rename_noreplace(source, destination)

            self.assertIn(raised.exception.errno, {errno.EEXIST, errno.ENOTEMPTY})
            self.assertEqual(source.read_text(encoding="utf-8"), "source")
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                "destination",
            )

    def test_configuration_loader_is_closed_and_rejects_unsafe_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "configuration.json"
            valid = {
                "schema_version": 1,
                "active_root": str(root / "active"),
                "backup_root": str(root / "backup"),
                "backup_uid": os.getuid(),
                "backup_gid": os.getgid(),
                "backup_mode": 0o700,
                "specs": [
                    {
                        "addon_id": addon_id,
                        "userdata": {
                            "version": version,
                            "manifest_sha256": _identity(
                                addon_id,
                                version,
                            ).manifest_sha256,
                        },
                        "managed": None,
                    }
                    for addon_id, version in ADDONS
                ],
            }
            cases = {
                "unknown field": {**valid, "unknown": True},
                "relative active root": {**valid, "active_root": "relative"},
                "duplicate IDs": {
                    **valid,
                    "specs": [valid["specs"][0], valid["specs"][0]],
                },
                "wrong mode": {**valid, "backup_mode": 0o755},
                "backup inside scan root": {
                    **valid,
                    "backup_root": str(root / "active" / "backup"),
                },
                "parent traversal": {
                    **valid,
                    "backup_root": str(root / "active" / ".." / "backup"),
                },
            }
            for label, value in cases.items():
                with self.subTest(label=label):
                    config_path.write_text(json.dumps(value), encoding="utf-8")
                    with self.assertRaises(ReconcileError):
                        load_configuration(config_path)

            target = root / "target.json"
            target.write_text(json.dumps(valid), encoding="utf-8")
            config_path.unlink()
            config_path.symlink_to(target)
            with self.assertRaisesRegex(ReconcileError, "opened safely"):
                load_configuration(config_path)

    @staticmethod
    def _active_symlink(fixture):
        active = fixture.active_root / ADDONS[1][0]
        target = active.with_name("target")
        active.rename(target)
        active.symlink_to(target, target_is_directory=True)

    @staticmethod
    def _active_regular(fixture):
        active = fixture.active_root / ADDONS[1][0]
        for path in active.iterdir():
            path.unlink()
        active.rmdir()
        active.write_text("not a directory", encoding="utf-8")

    @staticmethod
    def _manifest_symlink(fixture):
        manifest = fixture.backup_root / ADDONS[0][0] / "addon.xml"
        target = manifest.with_name("manifest-target")
        manifest.rename(target)
        manifest.symlink_to(target)

    @staticmethod
    def _manifest_directory(fixture):
        manifest = fixture.backup_root / ADDONS[0][0] / "addon.xml"
        manifest.unlink()
        manifest.mkdir()

    @staticmethod
    def _replace_manifest(fixture, addon_id, version):
        manifest = fixture.active_root / ADDONS[1][0] / "addon.xml"
        manifest.write_bytes(_manifest(addon_id, version))

    @staticmethod
    def _wrong_hash(fixture):
        manifest = fixture.backup_root / ADDONS[0][0] / "addon.xml"
        manifest.write_bytes(_manifest(ADDONS[0][0], "0"))

    @staticmethod
    def _oversized_manifest(fixture):
        manifest = fixture.backup_root / ADDONS[0][0] / "addon.xml"
        manifest.write_bytes(b"x" * (64 * 1024 + 1))

    @staticmethod
    def _doctype_manifest(fixture):
        addon_id, version = ADDONS[0]
        raw = (
            f'<!DOCTYPE addon [<!ENTITY x "x">]>'
            f'<addon id="{addon_id}" version="{version}"/>'
        ).encode()
        manifest = fixture.backup_root / addon_id / "addon.xml"
        manifest.write_bytes(raw)
        fixture.specs[0] = AddonSpec(
            addon_id,
            Identity(version, hashlib.sha256(raw).hexdigest()),
            None,
        )
        fixture.configuration = Configuration(
            fixture.active_root,
            fixture.backup_root,
            os.getuid(),
            os.getgid(),
            0o700,
            tuple(fixture.specs),
        )

    @staticmethod
    def _backup_permissions(fixture):
        fixture.backup_root.chmod(0o755)

    @staticmethod
    def _backup_ownership(fixture):
        fixture.configuration = Configuration(
            fixture.active_root,
            fixture.backup_root,
            os.getuid() + 1,
            os.getgid(),
            0o700,
            tuple(fixture.specs),
        )


if __name__ == "__main__":
    unittest.main()
