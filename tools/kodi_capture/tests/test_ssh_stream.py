from __future__ import annotations

import inspect
import signal
import subprocess
import sys
import unittest
from unittest import mock

from tools.kodi_capture.jsonrpc import JsonRpcTimeout, JsonRpcTransportError
from tools.kodi_capture.ssh_policy import (
    SSH_FIXED_CAPABILITY_OPTIONS,
    SSH_OPTION_TERMINATOR,
    SSH_PROGRAM,
)
from tools.kodi_capture.ssh_stream import (
    KODI_JSON_RPC_ENDPOINT,
    OpenSshByteStream,
)


class RecordingPopenFactory(object):
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


class OpenSshByteStreamTest(unittest.TestCase):
    def test_constructor_arguments_are_validated(self):
        invalid = [
            {"host": None},
            {"host": ""},
            {"host": "-option"},
            {"host": "host name"},
            {"host": "host\nname"},
            {"host": "x" * 256},
            {"host": "host", "terminate_timeout": 0},
            {"host": "host", "max_stderr_bytes": 0},
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    OpenSshByteStream(**arguments)

    def test_constructor_exposes_no_transport_configuration(self):
        parameters = inspect.signature(OpenSshByteStream).parameters
        self.assertNotIn("ssh_binary", parameters)
        self.assertNotIn("port", parameters)

    def test_start_failure_is_a_transport_error(self):
        def fail_to_start(_argv, **_kwargs):
            raise FileNotFoundError("ssh is missing")

        with self.assertRaises(JsonRpcTransportError) as raised:
            OpenSshByteStream(
                "htpc-pi.local",
                popen_factory=fail_to_start,
            )
        self.assertIn("ssh is missing", str(raised.exception))

    def test_setup_failure_closes_and_reaps_started_process(self):
        factory = RecordingPopenFactory("import time; time.sleep(60)")
        with mock.patch(
            "tools.kodi_capture.process.os.set_blocking",
            side_effect=OSError("cannot configure pipe"),
        ):
            with self.assertRaises(JsonRpcTransportError) as raised:
                OpenSshByteStream(
                    "htpc-pi.local",
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

        with self.assertRaises(JsonRpcTransportError) as raised:
            OpenSshByteStream(
                "htpc-pi.local",
                popen_factory=omit_stdout,
                terminate_timeout=0.1,
            )
        self.assertIn("binary standard streams", str(raised.exception))
        self.assertIsNotNone(factory.processes[0].poll())

    def test_argv_uses_direct_loopback_stream_and_no_shell(self):
        factory = RecordingPopenFactory(
            "import sys; sys.stdin.buffer.read()"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            argv, kwargs = factory.calls[0]
            self.assertEqual(
                argv,
                [
                    SSH_PROGRAM,
                    *SSH_FIXED_CAPABILITY_OPTIONS,
                    "-W",
                    KODI_JSON_RPC_ENDPOINT,
                    SSH_OPTION_TERMINATOR,
                    "htpc-pi.local",
                ],
            )
            self.assertIs(kwargs["shell"], False)
        finally:
            stream.close()

    def test_writes_and_reads_binary_stream(self):
        factory = RecordingPopenFactory(
            "import sys; "
            "data = sys.stdin.buffer.read(4); "
            "sys.stdout.buffer.write(data[::-1]); "
            "sys.stdout.buffer.flush(); "
            "sys.stdin.buffer.read()"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            stream.write(b"ping", time_deadline())
            self.assertEqual(stream.read(4, time_deadline()), b"gnip")
        finally:
            stream.close()

    def test_process_timeout_maps_to_json_rpc_timeout(self):
        factory = RecordingPopenFactory(
            "import time; time.sleep(60)"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            clock=lambda: 2.0,
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            with self.assertRaises(JsonRpcTimeout):
                stream.read(1, deadline=1.0)
        finally:
            stream.close()

    def test_early_exit_reports_status_and_stderr(self):
        factory = RecordingPopenFactory(
            "import sys; "
            "sys.stderr.write('connection refused\\n'); "
            "sys.stderr.flush(); "
            "raise SystemExit(23)"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        try:
            with self.assertRaises(JsonRpcTransportError) as raised:
                stream.read(1024, time_deadline())
            message = str(raised.exception)
            self.assertIn("23", message)
            self.assertIn("connection refused", message)
        finally:
            stream.close()

    def test_stderr_retains_exact_configured_tail(self):
        factory = RecordingPopenFactory(
            "import sys; "
            "sys.stderr.write('abcdefgh'); "
            "sys.stderr.flush(); "
            "raise SystemExit(9)"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.1,
            max_stderr_bytes=4,
        )
        try:
            with self.assertRaises(JsonRpcTransportError) as raised:
                stream.read(1024, time_deadline())
            self.assertTrue(str(raised.exception).endswith("efgh"))
            self.assertEqual(stream.stderr_tail, b"efgh")
        finally:
            stream.close()

    def test_close_terminates_and_reaps_process(self):
        factory = RecordingPopenFactory(
            "import time; time.sleep(60)"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.1,
        )
        process = factory.processes[0]
        stream.close()
        self.assertIsNotNone(process.poll())
        stream.close()

    def test_cleanup_failure_maps_to_retained_transport_error(self):
        factory = RecordingPopenFactory(
            "import time; time.sleep(60)"
        )
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.01,
        )
        process = factory.processes[0]
        real_wait = process.wait
        try:
            with mock.patch.object(
                process,
                "wait",
                side_effect=subprocess.TimeoutExpired("ssh", 0.01),
            ):
                with self.assertRaises(JsonRpcTransportError) as first:
                    stream.close()
                with self.assertRaises(JsonRpcTransportError) as second:
                    stream.close()
                self.assertIs(first.exception, second.exception)
        finally:
            real_wait(timeout=2.0)

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
        stream = OpenSshByteStream(
            "htpc-pi.local",
            popen_factory=factory,
            terminate_timeout=0.05,
        )
        process = factory.processes[0]
        self.assertEqual(
            stream.read(1024, time_deadline()),
            b"ready\n",
        )
        stream.close()
        self.assertIsNotNone(process.poll())


def time_deadline():
    import time

    return time.monotonic() + 2.0


if __name__ == "__main__":
    unittest.main()
