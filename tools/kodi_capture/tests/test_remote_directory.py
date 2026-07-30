from __future__ import annotations

import inspect
import subprocess
import sys
import time
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest import mock

from tools.kodi_capture.remote_directory import (
    MAX_ENTRIES,
    MAX_FIELD_BYTES,
    MAX_FILENAME_BYTES,
    MAX_OUTPUT_BYTES,
    PROTOCOL_VERSION,
    REMOTE_EVIDENCE_PROGRAM,
    DirectoryNotQuiescent,
    DirectorySnapshot,
    DirectoryStamp,
    RemoteDirectoryProtocolError,
    RemoteDirectoryTimeout,
    RemoteDirectoryTransportError,
    RemoteFileStamp,
    RemoteScreenshotDirectory,
    StatStamp,
    _parse_snapshot,
)
from tools.kodi_capture.ssh_policy import SSH_FIXED_CAPABILITY_OPTIONS


HEADER = [
    "D",
    "d",
    "45826",
    "561945",
    "700",
    "1000",
    "100",
    "2",
    "4096",
    "1785408067088154106",
    "1785408067088154106",
    "htpc",
    "users",
]
FIRST_FILE = [
    "F",
    "screenshot00000.png",
    "f",
    "45826",
    "561937",
    "644",
    "1000",
    "100",
    "1",
    "4813225",
    "1785408067996144302",
    "1785408067996144302",
]
SECOND_FILE = [
    "F",
    "screenshot00001.png",
    "f",
    "45826",
    "561938",
    "600",
    "1000",
    "100",
    "1",
    "1234",
    "1785408068996144302",
    "1785408068996144303",
]


class RecordingPopenFactory:
    def __init__(self, *payloads):
        self.payloads = iter(payloads)
        self.calls = []
        self.processes = []

    def __call__(self, argv, **kwargs):
        payload = next(self.payloads)
        script = (
            "import sys; "
            "sys.stdout.buffer.write(%r); "
            "sys.stdout.buffer.flush()" % payload
        )
        self.calls.append((argv, kwargs))
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            **kwargs,
        )
        self.processes.append(process)
        return process


class ScriptPopenFactory:
    def __init__(self, *scripts):
        self.scripts = iter(scripts)
        self.calls = []
        self.processes = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        process = subprocess.Popen(
            [sys.executable, "-c", next(self.scripts)],
            **kwargs,
        )
        self.processes.append(process)
        return process


class CompletedBoundedProcess:
    def __init__(self, output, *, after_read=None):
        self.output = output
        self.after_read = after_read
        self.closed = False
        self.read_deadlines = []

    def read_all(self, max_bytes, deadline):
        self.read_deadlines.append(deadline)
        if len(self.output) > max_bytes:
            raise AssertionError("fixture exceeded bound")
        if self.after_read is not None:
            self.after_read()
        return self.output

    def close(self):
        self.closed = True


class MutableClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class ModelTest(unittest.TestCase):
    def test_models_are_frozen_and_retain_full_stat_identity(self):
        snapshot = model_snapshot()
        file = snapshot.files[0]
        changed = replace(
            file,
            stat=replace(
                file.stat,
                inode=file.stat.inode + 1,
                ctime_ns=file.stat.ctime_ns + 1,
            ),
        )
        self.assertNotEqual(file, changed)
        with self.assertRaises(FrozenInstanceError):
            file.name = "screenshot9.png"

    def test_models_reject_unsafe_directory_file_and_collection_shapes(self):
        directory = model_directory()
        file = model_file()
        for changes in (
            {"stat": replace(directory.stat, file_type="l")},
            {"stat": replace(directory.stat, mode=0o755)},
            {"owner": "root"},
            {"group": "root"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(directory, **changes)

        for changes in (
            {"name": "../screenshot1.png"},
            {"name": "screenshot.png"},
            {"stat": replace(file.stat, file_type="l")},
            {"stat": replace(file.stat, link_count=2)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(file, **changes)

        with self.assertRaises(TypeError):
            DirectorySnapshot(directory, [file])
        with self.assertRaises(ValueError):
            DirectorySnapshot(
                directory,
                (replace(file, name="screenshot00001.png"), file),
            )
        with self.assertRaises(ValueError):
            DirectorySnapshot(directory, (file, file))
        with self.assertRaises(ValueError):
            DirectorySnapshot(
                directory,
                (replace(file, stat=replace(file.stat, device=99)),),
            )

    def test_snapshot_equality_includes_directory_and_file_metadata(self):
        snapshot = model_snapshot()
        changed = (
            replace(
                snapshot,
                directory=replace(
                    snapshot.directory,
                    stat=replace(
                        snapshot.directory.stat,
                        ctime_ns=snapshot.directory.stat.ctime_ns + 1,
                    ),
                ),
            ),
            replace(
                snapshot,
                files=(
                    replace(
                        snapshot.files[0],
                        stat=replace(
                            snapshot.files[0].stat,
                            inode=snapshot.files[0].stat.inode + 1,
                        ),
                    ),
                ),
            ),
        )
        for candidate in changed:
            with self.subTest(candidate=candidate):
                self.assertNotEqual(snapshot, candidate)


class ParserTest(unittest.TestCase):
    def test_live_shaped_golden_wire_is_parsed_without_normalization(self):
        snapshot = _parse_snapshot(
            wire([FIRST_FILE, SECOND_FILE])
        )
        self.assertEqual(snapshot, model_snapshot(two_files=True))
        self.assertEqual(
            tuple(file.name for file in snapshot.files),
            ("screenshot00000.png", "screenshot00001.png"),
        )
        self.assertEqual(snapshot.directory.stat.link_count, 2)
        self.assertEqual(snapshot.directory.stat.size, 4096)
        self.assertEqual(
            snapshot.files[0].stat.mtime_ns,
            1785408067996144302,
        )

    def test_version_terminal_nul_ascii_and_empty_fields_are_exact(self):
        valid = wire([])
        malformed = (
            b"",
            valid[:-1],
            valid + b"\0",
            valid + b"trailing",
            valid.replace(PROTOCOL_VERSION.encode(), b"OTHER/1", 1),
            valid.replace(b"htpc", b"h\xfftpc", 1),
            valid.replace(b"htpc\0", b"\0", 1),
        )
        for output in malformed:
            with self.subTest(output=output[:40]):
                with self.assertRaises(RemoteDirectoryProtocolError):
                    _parse_snapshot(output)

    def test_record_counts_tags_and_directory_fence_are_exact(self):
        malformed = (
            encode_fields([PROTOCOL_VERSION, *HEADER]),
            encode_fields(
                [PROTOCOL_VERSION, *HEADER, "F", *HEADER]
            ),
            wire([], header=["F", *HEADER[1:]]),
            wire([], footer=["F", *HEADER[1:]]),
            wire(
                [],
                footer=[
                    *HEADER[:9],
                    str(int(HEADER[9]) + 1),
                    *HEADER[10:],
                ],
            ),
        )
        for output in malformed:
            with self.subTest(output=output[:60]):
                with self.assertRaises(RemoteDirectoryProtocolError):
                    _parse_snapshot(output)

    def test_canonical_integer_and_octal_fields_fail_closed(self):
        directory_mutations = (
            (2, "+45826"),
            (2, "045826"),
            (3, "0"),
            (4, "0700"),
            (4, "0o700"),
            (4, "888"),
            (5, "-1"),
            (7, "0"),
            (9, "1.0"),
        )
        for index, value in directory_mutations:
            with self.subTest(record="directory", index=index, value=value):
                changed = list(HEADER)
                changed[index] = value
                with self.assertRaises(RemoteDirectoryProtocolError):
                    _parse_snapshot(wire([], header=changed, footer=changed))

        file_mutations = (
            (3, "+45826"),
            (4, "0"),
            (5, "0644"),
            (6, "-1"),
            (8, "0"),
            (9, "01"),
            (10, "1.5"),
        )
        for index, value in file_mutations:
            with self.subTest(record="file", index=index, value=value):
                changed = list(FIRST_FILE)
                changed[index] = value
                with self.assertRaises(RemoteDirectoryProtocolError):
                    _parse_snapshot(wire([changed]))

    def test_directory_and_file_policy_is_duplicated_fail_closed(self):
        directory_mutations = (
            (1, "l"),
            (4, "755"),
            (11, "root"),
            (12, "root"),
        )
        for index, value in directory_mutations:
            changed = list(HEADER)
            changed[index] = value
            with self.subTest(record="directory", index=index):
                with self.assertRaises(RemoteDirectoryProtocolError):
                    _parse_snapshot(wire([], header=changed, footer=changed))

        file_mutations = (
            (1, "other.png"),
            (1, "screenshot1.png\n"),
            (2, "l"),
            (3, "999"),
            (6, "1001"),
            (7, "101"),
            (8, "2"),
        )
        for index, value in file_mutations:
            changed = list(FIRST_FILE)
            changed[index] = value
            with self.subTest(record="file", index=index):
                with self.assertRaises(RemoteDirectoryProtocolError):
                    _parse_snapshot(wire([changed]))

    def test_wire_order_and_names_must_already_be_strict(self):
        with self.assertRaises(RemoteDirectoryProtocolError):
            _parse_snapshot(wire([SECOND_FILE, FIRST_FILE]))
        with self.assertRaises(RemoteDirectoryProtocolError):
            _parse_snapshot(wire([FIRST_FILE, FIRST_FILE]))

    def test_all_parser_caps_are_enforced(self):
        oversized_field = list(FIRST_FILE)
        oversized_field[1] = "x" * (MAX_FIELD_BYTES + 1)
        oversized_name = list(FIRST_FILE)
        oversized_name[1] = (
            "screenshot"
            + ("1" * (MAX_FILENAME_BYTES - len("screenshot.png") + 1))
            + ".png"
        )
        self.assertEqual(
            len(oversized_name[1].encode("ascii")),
            MAX_FILENAME_BYTES + 1,
        )
        malformed = (
            b"x" * (MAX_OUTPUT_BYTES + 1),
            wire([oversized_field]),
            wire([oversized_name]),
        )
        for output in malformed:
            with self.assertRaises(RemoteDirectoryProtocolError):
                _parse_snapshot(output)

        entries = []
        for index in range(MAX_ENTRIES + 1):
            entry = list(FIRST_FILE)
            entry[1] = "screenshot%05d.png" % index
            entry[4] = str(600000 + index)
            entries.append(entry)
        with self.assertRaises(RemoteDirectoryProtocolError):
            _parse_snapshot(wire(entries))


class StableObservationTest(unittest.TestCase):
    def test_public_operation_spawns_exactly_two_fixed_capabilities(self):
        self.assertEqual(
            REMOTE_EVIDENCE_PROGRAM,
            "/run/current-system/sw/bin/kodi-screenshot-evidence",
        )
        payload = wire([FIRST_FILE])
        factory = RecordingPopenFactory(payload, payload)
        reader = RemoteScreenshotDirectory(
            "root@htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        snapshot = reader.observe_stable(time.monotonic() + 5.0)
        self.assertEqual(snapshot, model_snapshot())
        self.assertEqual(len(factory.calls), 2)
        expected_argv = [
            "ssh",
            "-T",
            *SSH_FIXED_CAPABILITY_OPTIONS,
            "--",
            "root@htpc-pi.local",
            REMOTE_EVIDENCE_PROGRAM,
        ]
        for argv, kwargs in factory.calls:
            self.assertEqual(argv, expected_argv)
            self.assertIs(kwargs["shell"], False)
        self.assertTrue(all(process.poll() == 0 for process in factory.processes))

    def test_one_deadline_covers_both_processes_and_final_comparison(self):
        payload = wire([FIRST_FILE])
        processes = [
            CompletedBoundedProcess(payload),
            CompletedBoundedProcess(payload),
        ]
        deadline = 10.0
        with mock.patch(
            "tools.kodi_capture.remote_directory.BoundedProcess",
            side_effect=processes,
        ) as bounded:
            reader = RemoteScreenshotDirectory(
                "host",
                clock=lambda: 0.0,
            )
            snapshot = reader.observe_stable(deadline)
        self.assertEqual(snapshot, model_snapshot())
        self.assertEqual(bounded.call_count, 2)
        self.assertTrue(all(process.closed for process in processes))
        self.assertEqual(
            [process.read_deadlines for process in processes],
            [[deadline], [deadline]],
        )

    def test_expiry_during_first_observation_prevents_second_spawn(self):
        payload = wire([])
        clock = MutableClock()
        process = CompletedBoundedProcess(
            payload,
            after_read=lambda: setattr(clock, "value", 10.0),
        )
        with mock.patch(
            "tools.kodi_capture.remote_directory.BoundedProcess",
            return_value=process,
        ) as bounded:
            reader = RemoteScreenshotDirectory(
                "host",
                clock=clock,
            )
            with self.assertRaises(RemoteDirectoryTimeout):
                reader.observe_stable(10.0)
        self.assertEqual(bounded.call_count, 1)
        self.assertTrue(process.closed)

    def test_full_snapshot_drift_is_not_stable(self):
        first_payload = wire([FIRST_FILE])
        changed = list(FIRST_FILE)
        changed[4] = str(int(changed[4]) + 1)
        changed[11] = str(int(changed[11]) + 1)
        second_payload = wire([changed])
        processes = [
            CompletedBoundedProcess(first_payload),
            CompletedBoundedProcess(second_payload),
        ]
        with mock.patch(
            "tools.kodi_capture.remote_directory.BoundedProcess",
            side_effect=processes,
        ):
            reader = RemoteScreenshotDirectory(
                "host",
                clock=lambda: 0.0,
            )
            with self.assertRaises(DirectoryNotQuiescent):
                reader.observe_stable(10.0)

    def test_constructor_deadline_and_public_surface_are_strict(self):
        invalid_hosts = (
            None,
            "",
            "-option",
            "host name",
            "host\nname",
            "x" * 256,
        )
        for host in invalid_hosts:
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    RemoteScreenshotDirectory(host)
        invalid_settings = (
            {"clock": None},
            {"popen_factory": None},
            {"terminate_timeout": True},
            {"terminate_timeout": 0},
            {"terminate_timeout": float("inf")},
            {"terminate_timeout": "1"},
            {"max_stderr_bytes": True},
            {"max_stderr_bytes": 0},
            {"max_stderr_bytes": 1.0},
        )
        for settings in invalid_settings:
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    RemoteScreenshotDirectory(
                        "host",
                        **settings,
                    )
        for deadline in (True, float("inf"), float("nan"), "1"):
            reader = RemoteScreenshotDirectory("host")
            with self.assertRaises(ValueError):
                reader.observe_stable(deadline)

        public = {
            name
            for name in dir(RemoteScreenshotDirectory)
            if not name.startswith("_")
        }
        self.assertEqual(public, {"observe_stable"})
        parameters = inspect.signature(
            RemoteScreenshotDirectory.__init__
        ).parameters
        for forbidden in ("path", "command", "program", "argv", "run"):
            self.assertNotIn(forbidden, parameters)

    def test_nonzero_timeout_overflow_and_malformed_output_are_typed(self):
        valid = wire([])
        cases = (
            (
                "import sys; "
                "sys.stdout.buffer.write(%r); "
                "sys.stdout.buffer.flush(); "
                "sys.stderr.write('helper failed\\n'); "
                "sys.stderr.flush(); "
                "raise SystemExit(7)" % valid,
                2.0,
                RemoteDirectoryTransportError,
            ),
            (
                "import time; time.sleep(60)",
                0.2,
                RemoteDirectoryTimeout,
            ),
            (
                "import sys; "
                "sys.stdout.buffer.write(b'x' * %d); "
                "sys.stdout.buffer.flush()" % (MAX_OUTPUT_BYTES + 1),
                2.0,
                RemoteDirectoryTransportError,
            ),
            (
                "import sys; "
                "sys.stdout.buffer.write(b'bad\\0'); "
                "sys.stdout.buffer.flush()",
                2.0,
                RemoteDirectoryProtocolError,
            ),
        )
        for script, duration, expected in cases:
            with self.subTest(expected=expected):
                factory = ScriptPopenFactory(script)
                reader = RemoteScreenshotDirectory(
                    "host",
                    popen_factory=factory,
                    terminate_timeout=0.05,
                )
                with self.assertRaises(expected):
                    reader.observe_stable(time.monotonic() + duration)
                self.assertEqual(len(factory.calls), 1)
                self.assertIsNotNone(factory.processes[0].poll())

    def test_second_process_failure_closes_both_and_never_spawns_a_third(self):
        factory = RecordingPopenFactory(wire([]), b"bad\0")
        reader = RemoteScreenshotDirectory(
            "host",
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        with self.assertRaises(RemoteDirectoryProtocolError):
            reader.observe_stable(time.monotonic() + 5.0)
        self.assertEqual(len(factory.calls), 2)
        self.assertTrue(all(process.poll() == 0 for process in factory.processes))

    def test_already_expired_deadline_never_spawns(self):
        popen_factory = mock.Mock(
            side_effect=AssertionError("must not spawn")
        )
        reader = RemoteScreenshotDirectory(
            "host",
            clock=lambda: 10.0,
            popen_factory=popen_factory,
        )
        with self.assertRaises(RemoteDirectoryTimeout):
            reader.observe_stable(10.0)
        popen_factory.assert_not_called()


def encode_fields(fields):
    return ("\0".join(fields) + "\0").encode("ascii")


def wire(files, *, header=HEADER, footer=HEADER):
    fields = [PROTOCOL_VERSION, *header]
    for file in files:
        fields.extend(file)
    fields.extend(footer)
    return encode_fields(fields)


def model_stat(
    *,
    file_type="f",
    inode=561937,
    mode=0o644,
    link_count=1,
    size=4813225,
    mtime_ns=1785408067996144302,
    ctime_ns=1785408067996144302,
):
    return StatStamp(
        file_type=file_type,
        device=45826,
        inode=inode,
        mode=mode,
        uid=1000,
        gid=100,
        link_count=link_count,
        size=size,
        mtime_ns=mtime_ns,
        ctime_ns=ctime_ns,
    )


def model_directory():
    return DirectoryStamp(
        stat=model_stat(
            file_type="d",
            inode=561945,
            mode=0o700,
            link_count=2,
            size=4096,
            mtime_ns=1785408067088154106,
            ctime_ns=1785408067088154106,
        ),
        owner="htpc",
        group="users",
    )


def model_file():
    return RemoteFileStamp(
        "screenshot00000.png",
        model_stat(),
    )


def model_snapshot(*, two_files=False):
    files = [model_file()]
    if two_files:
        files.append(
            RemoteFileStamp(
                "screenshot00001.png",
                model_stat(
                    inode=561938,
                    mode=0o600,
                    size=1234,
                    mtime_ns=1785408068996144302,
                    ctime_ns=1785408068996144303,
                ),
            )
        )
    return DirectorySnapshot(model_directory(), tuple(files))


if __name__ == "__main__":
    unittest.main()
