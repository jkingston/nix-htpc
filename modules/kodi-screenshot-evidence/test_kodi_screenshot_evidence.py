from __future__ import annotations

import ast
import io
import os
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import kodi_screenshot_evidence as evidence


class FakeFilesystem:
    def __init__(self, names=(), entries=None):
        self.directory = stat_value(
            stat.S_IFDIR | 0o700,
            inode=100,
            link_count=2,
            size=4096,
        )
        self.names = list(names)
        self.entries = entries or {}
        self.fstats = [self.directory, self.directory]
        self.pathname = self.directory
        self.entry_reads = {}
        self.entry_values = {}
        self.next_entry_fd = 20
        self.open_count = 0
        self.parent_open_count = 0
        self.closed = []
        self.closed_parents = []
        self.closed_entries = []

    def open_parent(self):
        self.parent_open_count += 1
        return 16

    def open_directory(self, parent_fd):
        if parent_fd != 16:
            raise AssertionError("wrong parent descriptor")
        self.open_count += 1
        return 17

    def iter_names(self, directory_fd):
        if directory_fd != 17:
            raise AssertionError("wrong directory descriptor")
        return iter(self.names)

    def fstat(self, directory_fd):
        if directory_fd != 17:
            raise AssertionError("wrong directory descriptor")
        return self.fstats.pop(0)

    def open_entry(self, directory_fd, name):
        if directory_fd != 17:
            raise AssertionError("wrong directory descriptor")
        self.entry_reads[name] = self.entry_reads.get(name, 0) + 1
        values = self.entries[name]
        if isinstance(values, list):
            value = values.pop(0)
        else:
            value = values
        descriptor = self.next_entry_fd
        self.next_entry_fd += 1
        self.entry_values[descriptor] = value
        return descriptor

    def fstat_entry(self, entry_fd):
        return self.entry_values[entry_fd]

    def close_entry(self, entry_fd):
        self.closed_entries.append(entry_fd)

    def stat_path(self, parent_fd):
        if parent_fd != 16:
            raise AssertionError("wrong parent descriptor")
        return self.pathname

    def owner_name(self, uid):
        return "htpc" if uid == 1000 else "other"

    def group_name(self, gid):
        return "users" if gid == 100 else "other"

    def close(self, directory_fd):
        self.closed.append(directory_fd)

    def close_parent(self, parent_fd):
        self.closed_parents.append(parent_fd)


class PartialWriter:
    def __init__(self, step):
        self.step = step
        self.value = bytearray()
        self.flush_count = 0

    def write(self, value):
        written = min(self.step, len(value))
        self.value.extend(value[:written])
        return written

    def flush(self):
        self.flush_count += 1


class EvidenceCollectionTest(unittest.TestCase):
    def test_os_adapter_uses_fixed_no_follow_directory_and_entry_calls(self):
        filesystem = evidence.OsFilesystem()
        with mock.patch.object(
            evidence.os,
            "open",
            return_value=16,
        ) as opened_parent:
            self.assertEqual(filesystem.open_parent(), 16)
        opened_parent.assert_called_once_with(
            evidence.SCREENSHOT_PARENT,
            evidence.OPEN_FLAGS,
        )
        with mock.patch.object(
            evidence.os,
            "open",
            return_value=23,
        ) as opened:
            self.assertEqual(filesystem.open_directory(16), 23)
        opened.assert_called_once_with(
            evidence.SCREENSHOT_NAME,
            evidence.OPEN_FLAGS,
            dir_fd=16,
        )
        self.assertTrue(evidence.OPEN_FLAGS & os.O_DIRECTORY)
        self.assertTrue(evidence.OPEN_FLAGS & os.O_NOFOLLOW)
        self.assertTrue(evidence.OPEN_FLAGS & os.O_CLOEXEC)

        if evidence.ENTRY_OPEN_FLAGS is None:
            with self.assertRaisesRegex(evidence.EvidenceError, "O_PATH"):
                filesystem.open_entry(23, "screenshot1.png")
        else:
            with mock.patch.object(
                evidence.os,
                "open",
                return_value=24,
            ) as opened_entry:
                self.assertEqual(
                    filesystem.open_entry(23, "screenshot1.png"),
                    24,
                )
            opened_entry.assert_called_once_with(
                "screenshot1.png",
                evidence.ENTRY_OPEN_FLAGS,
                dir_fd=23,
            )
        result = stat_value(stat.S_IFREG | 0o600)
        with mock.patch.object(
            evidence.os,
            "stat",
            return_value=result,
        ) as stated:
            self.assertIs(filesystem.stat_path(16), result)
        stated.assert_called_once_with(
            evidence.SCREENSHOT_NAME,
            dir_fd=16,
            follow_symlinks=False,
        )

    def test_normal_collection_is_sorted_stable_and_versioned(self):
        entries = {
            "screenshot2.png": stat_value(
                stat.S_IFREG | 0o600,
                inode=202,
                size=20,
                mtime_ns=2000000002,
                ctime_ns=3000000002,
            ),
            "screenshot1.png": stat_value(
                stat.S_IFREG | 0o640,
                inode=201,
                size=10,
                mtime_ns=2000000001,
                ctime_ns=3000000001,
            ),
        }
        filesystem = FakeFilesystem(
            names=("screenshot2.png", "screenshot1.png"),
            entries=entries,
        )
        collected = evidence.collect_evidence(filesystem)
        self.assertEqual(
            tuple(file.name for file in collected.files),
            ("screenshot1.png", "screenshot2.png"),
        )
        self.assertEqual(
            filesystem.entry_reads,
            {"screenshot1.png": 2, "screenshot2.png": 2},
        )
        self.assertEqual(filesystem.open_count, 1)
        self.assertEqual(filesystem.parent_open_count, 1)
        self.assertEqual(filesystem.closed, [17])
        self.assertEqual(filesystem.closed_parents, [16])
        self.assertEqual(len(filesystem.closed_entries), 4)

        fields = evidence.encode_evidence(collected).split(b"\0")
        self.assertEqual(fields[-1], b"")
        self.assertEqual(
            fields[:-1],
            [
                b"KODI-SCREENSHOT-EVIDENCE/1",
                b"D",
                b"d",
                b"9",
                b"100",
                b"700",
                b"1000",
                b"100",
                b"2",
                b"4096",
                b"2000000000",
                b"3000000000",
                b"htpc",
                b"users",
                b"F",
                b"screenshot1.png",
                b"f",
                b"9",
                b"201",
                b"640",
                b"1000",
                b"100",
                b"1",
                b"10",
                b"2000000001",
                b"3000000001",
                b"F",
                b"screenshot2.png",
                b"f",
                b"9",
                b"202",
                b"600",
                b"1000",
                b"100",
                b"1",
                b"20",
                b"2000000002",
                b"3000000002",
                b"D",
                b"d",
                b"9",
                b"100",
                b"700",
                b"1000",
                b"100",
                b"2",
                b"4096",
                b"2000000000",
                b"3000000000",
                b"htpc",
                b"users",
            ],
        )

    def test_empty_directory_emits_only_exact_header_and_footer(self):
        filesystem = FakeFilesystem()
        collected = evidence.collect_evidence(filesystem)
        fields = evidence.encode_evidence(collected).split(b"\0")
        self.assertEqual(fields.count(b"D"), 2)
        self.assertNotIn(b"F", fields)
        self.assertEqual(filesystem.closed, [17])

    def test_root_symlink_open_failure_emits_no_output(self):
        filesystem = FakeFilesystem()
        filesystem.open_directory = mock.Mock(
            side_effect=OSError("too many levels of symbolic links")
        )
        stdout = io.BytesIO()
        stderr = io.StringIO()
        self.assertEqual(
            evidence._run((), filesystem, stdout, stderr),
            1,
        )
        self.assertEqual(stdout.getvalue(), b"")
        self.assertLessEqual(len(stderr.getvalue().encode("ascii")), 512)

    def test_entry_symlink_hardlink_and_wrong_identity_are_rejected(self):
        invalid = {
            "symlink": stat_value(stat.S_IFLNK | 0o777),
            "hardlink": stat_value(stat.S_IFREG | 0o600, link_count=2),
            "device": stat_value(stat.S_IFREG | 0o600, device=10),
            "uid": stat_value(stat.S_IFREG | 0o600, uid=1001),
            "gid": stat_value(stat.S_IFREG | 0o600, gid=101),
        }
        for name, value in invalid.items():
            with self.subTest(name=name):
                filesystem = FakeFilesystem(
                    names=("screenshot1.png",),
                    entries={"screenshot1.png": value},
                )
                with self.assertRaises(evidence.EvidenceError):
                    evidence.collect_evidence(filesystem)
                self.assertEqual(filesystem.closed, [17])

    def test_unexpected_and_duplicate_names_are_rejected_before_stat(self):
        names = (
            ("other.png",),
            ("../screenshot1.png",),
            ("screenshot1.png", "screenshot1.png"),
        )
        for values in names:
            with self.subTest(values=values):
                filesystem = FakeFilesystem(names=values)
                with self.assertRaises(evidence.EvidenceError):
                    evidence.collect_evidence(filesystem)
                self.assertEqual(filesystem.entry_reads, {})

    def test_second_file_stat_pass_must_match_exactly(self):
        first = stat_value(stat.S_IFREG | 0o600, size=10)
        second = stat_value(stat.S_IFREG | 0o600, size=11)
        filesystem = FakeFilesystem(
            names=("screenshot1.png",),
            entries={"screenshot1.png": [first, second]},
        )
        with self.assertRaisesRegex(evidence.EvidenceError, "entry changed"):
            evidence.collect_evidence(filesystem)
        self.assertEqual(
            filesystem.entry_reads,
            {"screenshot1.png": 2},
        )

    def test_entry_fstat_error_stays_primary_when_close_also_fails(self):
        value = stat_value(stat.S_IFREG | 0o600)
        filesystem = FakeFilesystem(
            names=("screenshot1.png",),
            entries={"screenshot1.png": value},
        )
        primary = OSError("second fstat failed")
        cleanup = OSError("second close failed")
        original_fstat = filesystem.fstat_entry

        def fstat_entry(descriptor):
            if descriptor == 21:
                raise primary
            return original_fstat(descriptor)

        def close_entry(descriptor):
            filesystem.closed_entries.append(descriptor)
            if descriptor == 21:
                raise cleanup

        filesystem.fstat_entry = fstat_entry
        filesystem.close_entry = close_entry
        with self.assertRaises(OSError) as raised:
            evidence.collect_evidence(filesystem)
        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__cause__, cleanup)
        self.assertEqual(filesystem.closed_entries, [21, 20])
        self.assertEqual(filesystem.closed, [17])

    def test_second_entry_close_failure_still_closes_first_descriptor(self):
        value = stat_value(stat.S_IFREG | 0o600)
        filesystem = FakeFilesystem(
            names=("screenshot1.png",),
            entries={"screenshot1.png": value},
        )
        cleanup = OSError("second close failed")

        def close_entry(descriptor):
            filesystem.closed_entries.append(descriptor)
            if descriptor == 21:
                raise cleanup

        filesystem.close_entry = close_entry
        with self.assertRaises(OSError) as raised:
            evidence.collect_evidence(filesystem)
        self.assertIs(raised.exception, cleanup)
        self.assertEqual(filesystem.closed_entries, [21, 20])
        self.assertEqual(filesystem.closed, [17])

    def test_directory_fence_and_final_path_identity_are_exact(self):
        changed = stat_value(
            stat.S_IFDIR | 0o700,
            inode=101,
            link_count=2,
            size=4096,
        )
        filesystem = FakeFilesystem()
        filesystem.fstats = [filesystem.directory, changed]
        filesystem.pathname = changed
        with self.assertRaisesRegex(evidence.EvidenceError, "metadata changed"):
            evidence.collect_evidence(filesystem)

        filesystem = FakeFilesystem()
        filesystem.pathname = changed
        with self.assertRaisesRegex(
            evidence.EvidenceError,
            "pathname no longer",
        ):
            evidence.collect_evidence(filesystem)

    def test_collection_error_stays_primary_when_close_also_fails(self):
        filesystem = FakeFilesystem(names=("unsafe",))
        cleanup_error = OSError("close failed")
        filesystem.close = mock.Mock(side_effect=cleanup_error)
        with self.assertRaisesRegex(
            evidence.EvidenceError,
            "unsafe name",
        ) as raised:
            evidence.collect_evidence(filesystem)
        self.assertIs(raised.exception.__cause__, cleanup_error)

    def test_entry_and_output_caps_fail_closed(self):
        filesystem = FakeFilesystem(
            names=(
                "screenshot1.png",
                "screenshot2.png",
                "screenshot3.png",
            )
        )
        with mock.patch.object(evidence, "MAX_ENTRIES", 2):
            with self.assertRaisesRegex(evidence.EvidenceError, "entries"):
                evidence.collect_evidence(filesystem)
        self.assertEqual(filesystem.entry_reads, {})

        collected = evidence.collect_evidence(FakeFilesystem())
        with mock.patch.object(evidence, "MAX_OUTPUT_BYTES", 32):
            with self.assertRaisesRegex(evidence.EvidenceError, "output"):
                evidence.encode_evidence(collected)


@unittest.skipUnless(
    sys.platform.startswith("linux")
    and evidence.ENTRY_OPEN_FLAGS is not None,
    "requires Linux O_PATH",
)
class LinuxFilesystemIntegrationTest(unittest.TestCase):
    def test_real_directory_and_entry_descriptors_collect_normally(self):
        with ManagedDirectory() as directory:
            write_file(os.path.join(directory, "screenshot1.png"), b"frame")
            with patch_managed_path(directory):
                collected = evidence.collect_evidence(TestOsFilesystem())
        self.assertEqual(
            tuple(file.name for file in collected.files),
            ("screenshot1.png",),
        )
        self.assertEqual(collected.files[0].stat.size, 5)

    def test_real_root_symlink_is_rejected_by_open(self):
        with tempfile.TemporaryDirectory() as parent:
            target = os.path.join(parent, "target")
            link = os.path.join(parent, "screens")
            os.mkdir(target, 0o700)
            os.symlink(target, link)
            with patch_managed_path(link):
                with self.assertRaises(OSError):
                    evidence.collect_evidence(TestOsFilesystem())

    def test_real_root_move_away_and_back_cannot_mix_directories(self):
        with ManagedDirectory() as directory:
            path = os.path.join(directory, "screenshot1.png")
            write_file(
                path,
                b"original",
            )
            expected_directory = evidence._stat_evidence(os.stat(directory))
            expected_file = evidence._stat_evidence(os.stat(path))
            filesystem = RootRebindingFilesystem(directory)
            collected = None
            raised = None
            with patch_managed_path(directory):
                try:
                    collected = evidence.collect_evidence(filesystem)
                except evidence.EvidenceError as error:
                    raised = error

        self.assertTrue(filesystem.rebound)
        if filesystem.metadata_changed:
            self.assertIsNone(collected)
            self.assertEqual(
                str(raised),
                "managed directory metadata changed during enumeration",
            )
        else:
            self.assertIsNone(raised)
            self.assertEqual(collected.stat, expected_directory)
            self.assertEqual(
                tuple(file.name for file in collected.files),
                ("screenshot1.png",),
            )
            self.assertEqual(collected.files[0].stat, expected_file)

    def test_real_entry_rebind_between_anchored_opens_is_rejected(self):
        with ManagedDirectory() as directory:
            path = os.path.join(directory, "screenshot1.png")
            write_file(path, b"first")
            filesystem = EntryRebindingFilesystem(path)
            with patch_managed_path(directory):
                with self.assertRaisesRegex(
                    evidence.EvidenceError,
                    "entry changed",
                ):
                    evidence.collect_evidence(filesystem)


class CommandTest(unittest.TestCase):
    def test_partial_binary_writes_are_completed(self):
        filesystem = FakeFilesystem()
        expected = evidence.encode_evidence(
            evidence.collect_evidence(FakeFilesystem())
        )
        stdout = PartialWriter(3)
        stderr = io.StringIO()
        self.assertEqual(
            evidence._run((), filesystem, stdout, stderr),
            0,
        )
        self.assertEqual(bytes(stdout.value), expected)
        self.assertEqual(stdout.flush_count, 1)
        self.assertEqual(stderr.getvalue(), "")

    def test_pre_emit_failure_has_no_stdout_and_bounded_sanitized_stderr(self):
        filesystem = FakeFilesystem()
        filesystem.open_directory = mock.Mock(
            side_effect=OSError("bad\n" + "x" * 2000)
        )
        stdout = io.BytesIO()
        stderr = io.StringIO()
        self.assertEqual(
            evidence._run((), filesystem, stdout, stderr),
            1,
        )
        self.assertEqual(stdout.getvalue(), b"")
        error = stderr.getvalue().encode("ascii")
        self.assertLessEqual(len(error), evidence.MAX_ERROR_BYTES)
        self.assertEqual(error.count(b"\n"), 1)

    def test_arguments_are_rejected_without_opening_or_emitting(self):
        filesystem = FakeFilesystem()
        stdout = io.BytesIO()
        stderr = io.StringIO()
        self.assertEqual(
            evidence._run(("--path", "/tmp/other"), filesystem, stdout, stderr),
            2,
        )
        self.assertEqual(filesystem.open_count, 0)
        self.assertEqual(filesystem.parent_open_count, 0)
        self.assertEqual(stdout.getvalue(), b"")
        self.assertLessEqual(
            len(stderr.getvalue().encode("ascii")),
            evidence.MAX_ERROR_BYTES,
        )

    def test_adapter_and_imports_are_an_exact_read_only_allowlist(self):
        public_adapter = {
            name
            for name in vars(evidence.OsFilesystem)
            if not name.startswith("_")
        }
        self.assertEqual(
            public_adapter,
            {
                "open_directory",
                "open_parent",
                "iter_names",
                "fstat",
                "open_entry",
                "fstat_entry",
                "close_entry",
                "stat_path",
                "owner_name",
                "group_name",
                "close",
                "close_parent",
            },
        )

        with open(evidence.__file__, "r", encoding="utf-8") as source_file:
            source = source_file.read()
        tree = ast.parse(source)
        imports = set()
        os_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                os_calls.add(node.func.attr)
        self.assertEqual(
            imports,
            {
                "__future__",
                "dataclasses",
                "grp",
                "os",
                "pwd",
                "re",
                "stat",
                "sys",
                "typing",
            },
        )
        self.assertEqual(
            os_calls,
            {"close", "fstat", "open", "scandir", "stat"},
        )


def stat_value(
    mode,
    *,
    device=9,
    inode=200,
    uid=1000,
    gid=100,
    link_count=1,
    size=0,
    mtime_ns=2000000000,
    ctime_ns=3000000000,
):
    return SimpleNamespace(
        st_mode=mode,
        st_dev=device,
        st_ino=inode,
        st_uid=uid,
        st_gid=gid,
        st_nlink=link_count,
        st_size=size,
        st_mtime_ns=mtime_ns,
        st_ctime_ns=ctime_ns,
    )


class TestOsFilesystem(evidence.OsFilesystem):
    def owner_name(self, _uid):
        return "htpc"

    def group_name(self, _gid):
        return "users"


class RootRebindingFilesystem(TestOsFilesystem):
    def __init__(self, directory):
        self.directory = directory
        self.rebound = False
        self.metadata_changed = None

    def iter_names(self, directory_fd):
        before = evidence._stat_evidence(os.fstat(directory_fd))
        names = tuple(super().iter_names(directory_fd))
        moved = self.directory + ".moved"
        replacement = self.directory + ".replacement"
        os.rename(self.directory, moved)
        os.mkdir(replacement, 0o700)
        write_file(
            os.path.join(replacement, "screenshot999.png"),
            b"replacement",
        )
        os.rename(replacement, self.directory)
        os.unlink(os.path.join(self.directory, "screenshot999.png"))
        os.rmdir(self.directory)
        os.rename(moved, self.directory)
        after = evidence._stat_evidence(os.fstat(directory_fd))
        self.metadata_changed = before != after
        self.rebound = True
        return iter(names)


class EntryRebindingFilesystem(TestOsFilesystem):
    def __init__(self, path):
        self.path = path
        self.opens = 0

    def open_entry(self, directory_fd, name):
        self.opens += 1
        if self.opens == 2:
            moved = self.path + ".moved"
            os.rename(self.path, moved)
            write_file(self.path, b"second")
        return super().open_entry(directory_fd, name)


class ManagedDirectory:
    def __init__(self):
        self.parent = None
        self.path = None

    def __enter__(self):
        self.parent = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.parent.name, "screens")
        os.mkdir(self.path, 0o700)
        return self.path

    def __exit__(self, _type, _value, _traceback):
        self.parent.cleanup()


def write_file(path, value):
    with open(path, "wb") as output:
        output.write(value)


def patch_managed_path(path):
    return mock.patch.multiple(
        evidence,
        SCREENSHOT_DIRECTORY=path,
        SCREENSHOT_PARENT=os.path.dirname(path),
        SCREENSHOT_NAME=os.path.basename(path),
    )


if __name__ == "__main__":
    unittest.main()
