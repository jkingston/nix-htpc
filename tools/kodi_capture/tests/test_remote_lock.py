from __future__ import annotations

import inspect
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.kodi_capture.process import (
    ProcessCleanupError,
    ProcessTransportError,
)
from tools.kodi_capture.remote_lock import (
    ACQUIRE_OPERATION,
    PROTOCOL,
    RemoteCaptureLock,
    RemoteLockCleanupError,
    RemoteLockConflict,
    RemoteLockPoisoned,
    RemoteLockProtocolError,
    RemoteLockTimeout,
    RemoteLockTransportError,
)


ECHO_SCRIPT = (
    "import sys\n"
    "for line in sys.stdin.buffer:\n"
    " sys.stdout.buffer.write(line)\n"
    " sys.stdout.buffer.flush()\n"
)


class RecordingPopenFactory:
    def __init__(self, script):
        self.script = script
        self.calls = []
        self.processes = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        process = subprocess.Popen(
            [sys.executable, "-c", self.script],
            **kwargs,
        )
        self.processes.append(process)
        return process


class Nonces:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class EchoBoundedProcess:
    def __init__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        self.writes = []
        self.read_deadlines = []
        self.pending = b""
        self.stderr_tail = b""
        self.returncode = None
        self.closed = False

    def write(self, data, deadline):
        self.writes.append((data, deadline))
        self.pending = data

    def read(self, _max_bytes, deadline):
        self.read_deadlines.append(deadline)
        pending = self.pending
        self.pending = b""
        return pending

    def close(self):
        self.closed = True


class FragmentEchoBoundedProcess(EchoBoundedProcess):
    def __init__(self, argv, split, **kwargs):
        super().__init__(argv, **kwargs)
        self.split = split
        self.fragments = []

    def write(self, data, deadline):
        self.writes.append((data, deadline))
        self.fragments = [data[: self.split], data[self.split :]]

    def read(self, _max_bytes, deadline):
        self.read_deadlines.append(deadline)
        return self.fragments.pop(0)


class ExitAfterEchoBoundedProcess(EchoBoundedProcess):
    def __init__(self, argv, status, **kwargs):
        super().__init__(argv, **kwargs)
        self.status = status

    def read(self, max_bytes, deadline):
        reply = super().read(max_bytes, deadline)
        self.returncode = self.status
        return reply


class PostAcquireConflictBoundedProcess(EchoBoundedProcess):
    def write(self, data, deadline):
        if self.writes:
            self.returncode = 75
            raise ProcessTransportError("guardian exited with status 75")
        super().write(data, deadline)


class SharedGuardian:
    def __init__(self):
        self.owner = None
        self.instances = []

    def create(self, argv, **kwargs):
        instance = GuardianBoundedProcess(self, argv, **kwargs)
        self.instances.append(instance)
        return instance


class GuardianBoundedProcess(EchoBoundedProcess):
    def __init__(self, guardian, argv, **kwargs):
        super().__init__(argv, **kwargs)
        self.guardian = guardian
        self.conflict = guardian.owner is not None
        if self.conflict:
            self.returncode = 75
        else:
            guardian.owner = self

    def write(self, data, deadline):
        if self.conflict:
            raise ProcessTransportError("guardian exited with status 75")
        super().write(data, deadline)

    def close(self):
        super().close()
        if self.guardian.owner is self:
            self.guardian.owner = None
        if self.returncode is None:
            self.returncode = 0


class RaisingNonceSequence:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value


class RemoteCaptureLockTest(unittest.TestCase):
    def test_constructor_arguments_are_validated(self):
        invalid = [
            {"host": None},
            {"host": ""},
            {"host": "-option"},
            {"host": "host name"},
            {"host": "host\nname"},
            {"host": "x" * 256},
            {"host": "host", "acquire_deadline": True},
            {"host": "host", "acquire_deadline": float("inf")},
            {"host": "host", "acquire_deadline": 1, "nonce_factory": None},
            {"host": "host", "acquire_deadline": 1, "graceful_timeout": -1},
            {"host": "host", "acquire_deadline": 1, "terminate_timeout": 0},
            {"host": "host", "acquire_deadline": 1, "max_stderr_bytes": 0},
        ]
        for arguments in invalid:
            arguments.setdefault("acquire_deadline", time_deadline())
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    RemoteCaptureLock(**arguments)

    def test_fixed_argv_exact_echo_and_shell_false(self):
        factory = RecordingPopenFactory(ECHO_SCRIPT)
        nonces = Nonces("0" * 32, "1" * 32)
        lock = RemoteCaptureLock(
            "htpc-pi.local",
            time_deadline(),
            popen_factory=factory,
            nonce_factory=nonces,
            terminate_timeout=0.1,
        )
        try:
            lock.assert_held(time_deadline())
            argv, kwargs = factory.calls[0]
            self.assertEqual(
                argv,
                [
                    "ssh",
                    "-T",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ClearAllForwardings=yes",
                    "--",
                    "htpc-pi.local",
                    "/run/current-system/sw/bin/flock",
                    "-n",
                    "-E",
                    "75",
                    "/run/lock/kodi-capture.lock",
                    "/run/current-system/sw/bin/cat",
                ],
            )
            self.assertIs(kwargs["shell"], False)
        finally:
            lock.close()

    def test_acquire_and_ping_share_the_exact_absolute_deadline(self):
        instances = []

        def create(argv, **kwargs):
            instance = EchoBoundedProcess(argv, **kwargs)
            instances.append(instance)
            return instance

        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            side_effect=create,
        ):
            lock = RemoteCaptureLock(
                "host",
                123.25,
                clock=lambda: 0.0,
                nonce_factory=Nonces("a" * 32, "b" * 32),
            )
            lock.assert_held(456.75)
            lock.close()

        bounded = instances[0]
        self.assertEqual(bounded.writes[0][1], 123.25)
        self.assertEqual(bounded.read_deadlines[0], 123.25)
        self.assertEqual(bounded.writes[1][1], 456.75)
        self.assertEqual(bounded.read_deadlines[1], 456.75)

    def test_exact_ack_at_deadline_is_a_timeout_and_closes(self):
        bounded = EchoBoundedProcess([])
        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            return_value=bounded,
        ):
            with self.assertRaises(RemoteLockTimeout):
                RemoteCaptureLock(
                    "host",
                    10.0,
                    clock=lambda: 10.0,
                    nonce_factory=Nonces("0" * 32),
                )
        self.assertTrue(bounded.closed)

    def test_nonce_must_be_bounded_lowercase_hex_and_unique(self):
        invalid = ["", "a" * 31, "a" * 33, "G" * 32, b"a" * 32]
        for nonce in invalid:
            with self.subTest(nonce=nonce):
                factory = RecordingPopenFactory(ECHO_SCRIPT)
                with self.assertRaises(RemoteLockProtocolError):
                    RemoteCaptureLock(
                        "host",
                        time_deadline(),
                        popen_factory=factory,
                        nonce_factory=lambda: nonce,
                        terminate_timeout=0.1,
                    )
                self.assertIsNotNone(factory.processes[0].poll())

        factory = RecordingPopenFactory(ECHO_SCRIPT)
        lock = RemoteCaptureLock(
            "host",
            time_deadline(),
            popen_factory=factory,
            nonce_factory=lambda: "a" * 32,
            terminate_timeout=0.1,
        )
        try:
            with self.assertRaises(RemoteLockProtocolError):
                lock.assert_held(time_deadline())
            with self.assertRaises(RemoteLockPoisoned):
                lock.assert_held(time_deadline())
        finally:
            lock.close()

    def test_status_75_is_a_typed_lock_conflict(self):
        factory = RecordingPopenFactory("raise SystemExit(75)")
        with self.assertRaises(RemoteLockConflict):
            RemoteCaptureLock(
                "host",
                time_deadline(),
                popen_factory=factory,
                nonce_factory=Nonces("0" * 32),
                terminate_timeout=0.1,
            )
        self.assertEqual(factory.processes[0].returncode, 75)

    def test_echo_from_exited_process_never_acquires(self):
        for status in (0, 75):
            with self.subTest(status=status):
                bounded = ExitAfterEchoBoundedProcess([], status)
                with mock.patch(
                    "tools.kodi_capture.remote_lock.BoundedProcess",
                    return_value=bounded,
                ):
                    with self.assertRaises(RemoteLockTransportError):
                        RemoteCaptureLock(
                            "host",
                            time_deadline(),
                            nonce_factory=Nonces("0" * 32),
                        )
                self.assertTrue(bounded.closed)

    def test_status_75_after_acquire_is_lost_lock_transport(self):
        bounded = PostAcquireConflictBoundedProcess([])
        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            return_value=bounded,
        ):
            lock = RemoteCaptureLock(
                "host",
                time_deadline(),
                nonce_factory=Nonces("0" * 32, "1" * 32),
            )
        with self.assertRaises(RemoteLockTransportError):
            lock.assert_held(time_deadline())
        self.assertTrue(bounded.closed)
        with self.assertRaises(RemoteLockPoisoned):
            lock.assert_held(time_deadline())

    def test_other_early_exit_is_a_transport_error_with_stderr(self):
        factory = RecordingPopenFactory(
            "import sys\n"
            "sys.stderr.write('ssh failed\\n')\n"
            "sys.stderr.flush()\n"
            "raise SystemExit(23)\n"
        )
        with self.assertRaises(RemoteLockTransportError) as raised:
            RemoteCaptureLock(
                "host",
                time_deadline(),
                popen_factory=factory,
                nonce_factory=Nonces("0" * 32),
                terminate_timeout=0.1,
            )
        self.assertIn("23", str(raised.exception))
        self.assertIn("ssh failed", str(raised.exception))

    def test_deadline_is_typed_and_closes_the_process(self):
        factory = RecordingPopenFactory(
            "import sys, time\n"
            "sys.stdin.buffer.readline()\n"
            "time.sleep(60)\n"
        )
        with self.assertRaises(RemoteLockTimeout):
            RemoteCaptureLock(
                "host",
                time.monotonic() + 0.05,
                popen_factory=factory,
                nonce_factory=Nonces("0" * 32),
                terminate_timeout=0.05,
            )
        self.assertIsNotNone(factory.processes[0].poll())

    def test_wrong_echo_poisons_and_reaps_before_later_ping(self):
        factory = RecordingPopenFactory(
            "import sys\n"
            "first = sys.stdin.buffer.readline()\n"
            "sys.stdout.buffer.write(first)\n"
            "sys.stdout.buffer.flush()\n"
            "second = sys.stdin.buffer.readline()\n"
            "sys.stdout.buffer.write("
            "b'KODI-CAPTURE-LOCK/1 PING ' + b'f' * 32 + b'\\n')\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stdin.buffer.read()\n"
        )
        lock = RemoteCaptureLock(
            "host",
            time_deadline(),
            popen_factory=factory,
            nonce_factory=Nonces("0" * 32, "1" * 32),
            terminate_timeout=0.1,
        )
        with self.assertRaises(RemoteLockProtocolError):
            lock.assert_held(time_deadline())
        self.assertIsNotNone(factory.processes[0].poll())
        with self.assertRaises(RemoteLockPoisoned):
            lock.assert_held(time_deadline())
        lock.close()

    def test_protocol_line_can_fragment_at_every_byte_boundary(self):
        wire = (
            "%s %s %s\n" % (PROTOCOL, ACQUIRE_OPERATION, "0" * 32)
        ).encode("ascii")
        for split in range(1, len(wire)):
            with self.subTest(split=split):
                bounded = FragmentEchoBoundedProcess([], split)
                with mock.patch(
                    "tools.kodi_capture.remote_lock.BoundedProcess",
                    return_value=bounded,
                ):
                    lock = RemoteCaptureLock(
                        "host",
                        time_deadline(),
                        nonce_factory=Nonces("0" * 32),
                    )
                lock.close()

    def test_malformed_trailing_and_oversize_echoes_are_rejected(self):
        replies = [
            b"not-the-protocol\n",
            (
                b"KODI-CAPTURE-LOCK/1 ACQUIRE "
                + b"0" * 32
                + b"\ntrailing"
            ),
            b"x" * 97,
        ]
        for reply in replies:
            with self.subTest(reply=reply[:20]):
                encoded = repr(reply)
                factory = RecordingPopenFactory(
                    "import sys\n"
                    "sys.stdin.buffer.readline()\n"
                    "sys.stdout.buffer.write(%s)\n"
                    "sys.stdout.buffer.flush()\n"
                    "sys.stdin.buffer.read()\n" % encoded
                )
                with self.assertRaises(RemoteLockProtocolError):
                    RemoteCaptureLock(
                        "host",
                        time_deadline(),
                        popen_factory=factory,
                        nonce_factory=Nonces("0" * 32),
                        terminate_timeout=0.1,
                    )
                self.assertIsNotNone(factory.processes[0].poll())

    def test_context_manager_and_repeated_close_release_and_reap(self):
        factory = RecordingPopenFactory(ECHO_SCRIPT)
        with RemoteCaptureLock(
            "host",
            time_deadline(),
            popen_factory=factory,
            nonce_factory=Nonces("0" * 32),
            terminate_timeout=0.1,
        ) as lock:
            process = factory.processes[0]
            self.assertIsNone(process.poll())
        self.assertEqual(process.returncode, 0)
        lock.close()

    def test_acquisition_keyboard_interrupt_is_primary_over_cleanup(self):
        bounded = EchoBoundedProcess([])
        bounded.close = mock.Mock(
            side_effect=ProcessCleanupError("could not reap")
        )
        primary = KeyboardInterrupt("stop acquisition")
        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            return_value=bounded,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                RemoteCaptureLock(
                    "host",
                    time_deadline(),
                    nonce_factory=RaisingNonceSequence(primary),
                )
        self.assertIs(raised.exception, primary)
        self.assertIsInstance(
            raised.exception.__cause__,
            RemoteLockCleanupError,
        )

    def test_assert_keyboard_interrupt_poisons_and_remains_primary(self):
        factory = RecordingPopenFactory(ECHO_SCRIPT)
        primary = KeyboardInterrupt("stop ping")
        lock = RemoteCaptureLock(
            "host",
            time_deadline(),
            popen_factory=factory,
            nonce_factory=RaisingNonceSequence("0" * 32, primary),
            terminate_timeout=0.1,
        )
        with self.assertRaises(KeyboardInterrupt) as raised:
            lock.assert_held(time_deadline())
        self.assertIs(raised.exception, primary)
        self.assertIsNotNone(factory.processes[0].poll())
        with self.assertRaises(RemoteLockPoisoned):
            lock.assert_held(time_deadline())
        lock.close()

    def test_assert_primary_attaches_and_retains_cleanup_failure(self):
        bounded = EchoBoundedProcess([])
        cleanup = ProcessCleanupError("could not reap")
        bounded.close = mock.Mock(side_effect=cleanup)
        primary = KeyboardInterrupt("stop ping")
        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            return_value=bounded,
        ):
            lock = RemoteCaptureLock(
                "host",
                time_deadline(),
                nonce_factory=RaisingNonceSequence("0" * 32, primary),
            )
        with self.assertRaises(KeyboardInterrupt) as raised:
            lock.assert_held(time_deadline())
        self.assertIs(raised.exception, primary)
        attached = raised.exception.__cause__
        self.assertIsInstance(attached, RemoteLockCleanupError)
        with self.assertRaises(RemoteLockCleanupError) as retained:
            lock.close()
        self.assertIs(retained.exception, attached)

    def test_context_body_exception_remains_primary_on_cleanup_failure(self):
        bounded = EchoBoundedProcess([])
        bounded.close = mock.Mock(
            side_effect=ProcessCleanupError("could not reap")
        )
        primary = RuntimeError("body failed")
        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            return_value=bounded,
        ):
            lock = RemoteCaptureLock(
                "host",
                time_deadline(),
                nonce_factory=Nonces("0" * 32),
            )
        with self.assertRaises(RuntimeError) as raised:
            with lock:
                raise primary
        self.assertIs(raised.exception, primary)
        attached = raised.exception.__cause__
        self.assertIsInstance(attached, RemoteLockCleanupError)
        with self.assertRaises(RemoteLockCleanupError) as retained:
            lock.close()
        self.assertIs(retained.exception, attached)

    def test_cleanup_failure_is_retained(self):
        bounded = EchoBoundedProcess([], description="test")
        cleanup = ProcessCleanupError("could not reap")
        bounded.close = mock.Mock(side_effect=cleanup)

        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            return_value=bounded,
        ):
            lock = RemoteCaptureLock(
                "host",
                10.0,
                clock=lambda: 0.0,
                nonce_factory=Nonces("0" * 32),
            )
        with self.assertRaises(RemoteLockCleanupError) as first:
            lock.close()
        with self.assertRaises(RemoteLockCleanupError) as second:
            lock.close()
        self.assertIs(first.exception, second.exception)

    def test_two_instances_serialize_through_shared_guardian(self):
        guardian = SharedGuardian()
        with mock.patch(
            "tools.kodi_capture.remote_lock.BoundedProcess",
            side_effect=guardian.create,
        ):
            first = RemoteCaptureLock(
                "host",
                time_deadline(),
                nonce_factory=Nonces("0" * 32),
            )
            with self.assertRaises(RemoteLockConflict):
                RemoteCaptureLock(
                    "host",
                    time_deadline(),
                    nonce_factory=Nonces("1" * 32),
                )
            self.assertIs(guardian.owner, guardian.instances[0])
            first.close()
            self.assertIsNone(guardian.owner)
            third = RemoteCaptureLock(
                "host",
                time_deadline(),
                nonce_factory=Nonces("2" * 32),
            )
            self.assertIs(guardian.owner, guardian.instances[2])
            third.close()
            self.assertIsNone(guardian.owner)

    def test_public_api_exposes_no_generic_execution_surface(self):
        public = {
            name
            for name in dir(RemoteCaptureLock)
            if not name.startswith("_")
        }
        self.assertEqual(public, {"assert_held", "close", "stderr_tail"})
        parameters = inspect.signature(
            RemoteCaptureLock.__init__
        ).parameters
        for forbidden in ("run", "command", "path", "process", "argv"):
            self.assertNotIn(forbidden, parameters)

    def test_source_contains_no_interpreter_or_mutating_remote_command(self):
        source = (
            Path(__file__).parents[1] / "remote_lock.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "/bin/sh",
            "\"sh\"",
            "\"-c\"",
            "Input.",
            "screenshot",
            "cec-client",
            "Action(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


def time_deadline():
    return time.monotonic() + 2.0


if __name__ == "__main__":
    unittest.main()
