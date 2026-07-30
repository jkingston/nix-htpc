from __future__ import annotations

import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from tools.kodi_capture.process import (
    BoundedProcess,
    ProcessCleanupError,
    ProcessTimeout,
    ProcessTransportError,
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


class BoundedProcessTest(unittest.TestCase):
    def test_constructor_arguments_are_validated(self):
        invalid = [
            {"argv": []},
            {"argv": "program"},
            {"argv": ["valid", 7]},
            {"argv": ["valid"], "graceful_timeout": -1},
            {"argv": ["valid"], "terminate_timeout": 0},
            {"argv": ["valid"], "max_stderr_bytes": 0},
            {"argv": ["valid"], "description": ""},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    BoundedProcess(**arguments)

    def test_start_failure_is_a_transport_error(self):
        def fail_to_start(_argv, **_kwargs):
            raise FileNotFoundError("executable is missing")

        with self.assertRaises(ProcessTransportError) as raised:
            BoundedProcess(
                ["missing"],
                popen_factory=fail_to_start,
            )
        self.assertIn("executable is missing", str(raised.exception))

    def test_spawn_preserves_argv_and_disables_shell(self):
        factory = RecordingPopenFactory(
            "import sys; sys.stdin.buffer.read()"
        )
        process = BoundedProcess(
            ["program", "--literal", "two words"],
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            argv, kwargs = factory.calls[0]
            self.assertEqual(
                argv,
                ["program", "--literal", "two words"],
            )
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["bufsize"], 0)
        finally:
            process.close()

    def test_setup_failure_closes_and_reaps_started_process(self):
        factory = RecordingPopenFactory("import time; time.sleep(60)")
        with mock.patch(
            "tools.kodi_capture.process.os.set_blocking",
            side_effect=OSError("cannot configure pipe"),
        ):
            with self.assertRaises(ProcessTransportError) as raised:
                BoundedProcess(
                    ["program"],
                    popen_factory=factory,
                    terminate_timeout=0.1,
                )
        self.assertIn("cannot configure pipe", str(raised.exception))
        self.assertIsNotNone(factory.processes[0].poll())

    def test_missing_pipe_closes_and_reaps_started_process(self):
        factory = RecordingPopenFactory("import time; time.sleep(60)")

        def omit_stdout(argv, **kwargs):
            process = factory(argv, **kwargs)
            process.stdout.close()
            process.stdout = None
            return process

        with self.assertRaises(ProcessTransportError) as raised:
            BoundedProcess(
                ["program"],
                popen_factory=omit_stdout,
                terminate_timeout=0.1,
            )
        self.assertIn("binary standard streams", str(raised.exception))
        self.assertIsNotNone(factory.processes[0].poll())

    def test_writes_and_reads_binary_stream(self):
        factory = RecordingPopenFactory(
            "import sys; "
            "data = sys.stdin.buffer.read(4); "
            "sys.stdout.buffer.write(data[::-1]); "
            "sys.stdout.buffer.flush(); "
            "sys.stdin.buffer.read()"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            process.write(b"ping", time.monotonic() + 2.0)
            self.assertEqual(
                process.read(4, time.monotonic() + 2.0),
                b"gnip",
            )
        finally:
            process.close()

    def test_expired_absolute_deadline_is_typed(self):
        factory = RecordingPopenFactory("import time; time.sleep(60)")
        process = BoundedProcess(
            ["program"],
            clock=lambda: 2.0,
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            with self.assertRaises(ProcessTimeout):
                process.read(1, deadline=1.0)
        finally:
            process.close()

    def test_early_exit_reports_status_and_stderr(self):
        factory = RecordingPopenFactory(
            "import sys; "
            "sys.stderr.write('process failed\\n'); "
            "sys.stderr.flush(); "
            "raise SystemExit(23)"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            with self.assertRaises(ProcessTransportError) as raised:
                process.read(1024, time.monotonic() + 2.0)
            message = str(raised.exception)
            self.assertIn("23", message)
            self.assertIn("process failed", message)
        finally:
            process.close()

    def test_stderr_retains_exact_configured_tail(self):
        factory = RecordingPopenFactory(
            "import sys; "
            "sys.stderr.write('abcdefgh'); "
            "sys.stderr.flush(); "
            "raise SystemExit(9)"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            terminate_timeout=0.1,
            max_stderr_bytes=4,
        )
        try:
            with self.assertRaises(ProcessTransportError) as raised:
                process.read(1024, time.monotonic() + 2.0)
            self.assertTrue(str(raised.exception).endswith("efgh"))
            self.assertEqual(process.stderr_tail, b"efgh")
        finally:
            process.close()

    def test_close_terminates_and_reaps_process(self):
        factory = RecordingPopenFactory(
            "import time; time.sleep(60)"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        child = factory.processes[0]
        process.close()
        self.assertIsNotNone(child.poll())
        process.close()

    def test_close_allows_bounded_graceful_exit_after_stdin_eof(self):
        factory = RecordingPopenFactory(
            "import sys; sys.stdin.buffer.read()"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            graceful_timeout=0.2,
            terminate_timeout=0.1,
        )
        child = factory.processes[0]
        process.close()
        self.assertEqual(child.returncode, 0)
        self.assertEqual(process.returncode, 0)

    @unittest.skipUnless(
        hasattr(signal, "SIGTERM"),
        "requires POSIX signals",
    )
    def test_close_kills_process_that_ignores_terminate(self):
        factory = RecordingPopenFactory(
            "import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); "
            "time.sleep(60)"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            terminate_timeout=0.05,
        )
        child = factory.processes[0]
        self.assertEqual(
            process.read(1024, time.monotonic() + 2.0),
            b"ready\n",
        )
        process.close()
        self.assertIsNotNone(child.poll())

    def test_cleanup_failure_is_bounded_and_retained(self):
        factory = RecordingPopenFactory(
            "import time; time.sleep(60)"
        )
        process = BoundedProcess(
            ["program"],
            popen_factory=factory,
            terminate_timeout=0.01,
        )
        child = factory.processes[0]
        real_wait = child.wait
        try:
            with mock.patch.object(
                child,
                "wait",
                side_effect=subprocess.TimeoutExpired("program", 0.01),
            ):
                with self.assertRaises(ProcessCleanupError) as first:
                    process.close()
                with self.assertRaises(ProcessCleanupError) as second:
                    process.close()
                self.assertIs(first.exception, second.exception)
        finally:
            real_wait(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
