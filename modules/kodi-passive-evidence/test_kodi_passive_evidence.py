from __future__ import annotations

import ast
import base64
import io
import json
import os
import re
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

import kodi_passive_evidence as evidence


NONCE = "a" * 32
OTHER_NONCE = "b" * 32
BOOT_ID = "1" * 32
OTHER_BOOT_ID = "3" * 32
INVOCATION_ID = "2" * 32
START_CURSOR = "fixture-start-cursor"
CEC_TRACE = b"syntactically valid CEC monitor output\n"
LIVE_JOURNAL = b"live journal bytes\n"
FINAL_JOURNAL = b"final journal bytes\n"


def service(**changes):
    values = {
        "unit_id": "cec-tv-wake.service",
        "load_state": "loaded",
        "active_state": "active",
        "sub_state": "running",
        "invocation_id": INVOCATION_ID,
        "main_pid": 4242,
        "n_restarts": 0,
        "exec_start_usec": 900_000,
        "active_enter_usec": 900_100,
    }
    values.update(changes)
    return evidence.ServiceIdentity(**values)


class FakeMonitor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.closed = False
        self.close_error = None
        self.natural_error = None
        self.natural_delay_usec = 1

    def wait_ready(self, deadline):
        self.runtime.events.append(("monitor.wait_ready", deadline))

    def wait_until(self, deadline):
        self.runtime.events.append(("monitor.wait_until", deadline))
        self.runtime.now_usec = max(self.runtime.now_usec, deadline)

    def require_alive(self):
        self.runtime.events.append(("monitor.require_alive",))

    def wait_natural(self, deadline):
        self.runtime.events.append(("monitor.wait_natural", deadline))
        if self.natural_error is not None:
            raise self.natural_error
        self.runtime.now_usec += self.natural_delay_usec
        return CEC_TRACE

    def close(self):
        self.runtime.events.append(("monitor.close",))
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class ScriptedRuntime:
    def __init__(self):
        self.now_usec = 1_000_000
        self.events = []
        self.boot_ids = [BOOT_ID] * 6
        self.services = [service()] * 6
        self.journals = [LIVE_JOURNAL, FINAL_JOURNAL]
        self.monitor = FakeMonitor(self)
        self.failures = {}

    def _fail(self, operation):
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def monotonic_usec(self):
        return self.now_usec

    def read_boot_id(self, deadline, monitor=None):
        self.events.append(("runtime.read_boot_id", monitor is self.monitor))
        self._fail("read_boot_id")
        return self.boot_ids.pop(0)

    def read_service(self, deadline, monitor=None):
        self.events.append(("runtime.read_service", monitor is self.monitor))
        self._fail("read_service")
        return self.services.pop(0)

    def read_global_cursor(self, deadline):
        self.events.append(("runtime.read_global_cursor",))
        self._fail("read_global_cursor")
        return START_CURSOR

    def start_monitor(self):
        self.events.append(("runtime.start_monitor",))
        self._fail("start_monitor")
        return self.monitor

    def sync_journal(self, deadline, monitor):
        self.events.append(("runtime.sync_journal", monitor is self.monitor))
        self._fail("sync_journal")

    def read_journal(self, cursor, deadline, monitor):
        self.events.append(
            ("runtime.read_journal", cursor, monitor is self.monitor)
        )
        self._fail("read_journal")
        return self.journals.pop(0)


class SemanticTransport:
    def __init__(self, runtime):
        self.runtime = runtime
        self.events = runtime.events
        self.ready = None
        self.result_header = None
        self.result_body = None
        self.finish_delay_usec = 1
        self.failures = {}

    def _fail(self, operation):
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def read_start(self, deadline):
        self.events.append(("transport.read_start",))
        self._fail("read_start")
        return NONCE

    def write_ready(self, line, deadline, monitor):
        self.events.append(
            ("transport.write_ready", monitor is self.runtime.monitor)
        )
        self._fail("write_ready")
        self.ready = line

    def read_finish_and_eof(self, nonce, deadline, monitor):
        self.events.append(
            (
                "transport.read_finish_and_eof",
                nonce,
                monitor is self.runtime.monitor,
            )
        )
        self._fail("read_finish_and_eof")
        self.runtime.now_usec += self.finish_delay_usec

    def write_result(self, header, body, deadline):
        self.events.append(("transport.write_result",))
        self._fail("write_result")
        self.result_header = header
        self.result_body = body


def collect(runtime=None, transport=None):
    runtime = runtime or ScriptedRuntime()
    transport = transport or SemanticTransport(runtime)
    result = evidence.collect_evidence(runtime, transport)
    return runtime, transport, result


def decoded_document(transport):
    return json.loads(transport.result_body.decode("ascii"))


def ready_fields(transport):
    return transport.ready.decode("ascii").rstrip("\n").split(" ")


def event_names(events):
    return [event[0] for event in events]


class ServiceIdentityTest(unittest.TestCase):
    def test_service_identity_is_frozen(self):
        identity = service()
        with self.assertRaises(FrozenInstanceError):
            identity.main_pid = 99


class CollectionTest(unittest.TestCase):
    def test_success_is_canonical_exactly_framed_and_returns_the_body(self):
        runtime, transport, result = collect()

        self.assertEqual(result, transport.result_body)
        self.assertTrue(runtime.monitor.closed)
        self.assertEqual(
            transport.result_body,
            json.dumps(
                decoded_document(transport),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        self.assertEqual(
            transport.result_header,
            (
                "%s RESULT %s %d\n"
                % (
                    evidence.PROTOCOL_VERSION,
                    NONCE,
                    len(transport.result_body),
                )
            ).encode("ascii"),
        )

        document = decoded_document(transport)
        self.assertEqual(
            set(document),
            {
                "version",
                "nonce",
                "start_cursor",
                "boot_ids",
                "services",
                "timing_usec",
                "cec_trace_b64",
                "live_journal_b64",
                "final_journal_b64",
            },
        )
        self.assertEqual(document["version"], evidence.PROTOCOL_VERSION)
        self.assertEqual(document["nonce"], NONCE)
        self.assertEqual(document["start_cursor"], START_CURSOR)
        self.assertEqual(
            document["boot_ids"],
            {"start": BOOT_ID, "live": BOOT_ID, "final": BOOT_ID},
        )
        self.assertEqual(
            base64.b64decode(document["cec_trace_b64"], validate=True),
            CEC_TRACE,
        )
        self.assertEqual(
            base64.b64decode(document["live_journal_b64"], validate=True),
            LIVE_JOURNAL,
        )
        self.assertEqual(
            base64.b64decode(document["final_journal_b64"], validate=True),
            FINAL_JOURNAL,
        )

    def test_ready_line_has_all_fifteen_fields_and_canonical_cursor(self):
        _, transport, _ = collect()
        fields = ready_fields(transport)

        self.assertEqual(len(fields), 15)
        self.assertEqual(fields[0], evidence.PROTOCOL_VERSION)
        self.assertEqual(fields[1], "READY")
        self.assertEqual(fields[2], NONCE)
        self.assertEqual(fields[3], BOOT_ID)
        self.assertEqual(
            fields[4:13],
            [
                "cec-tv-wake.service",
                "loaded",
                "active",
                "running",
                INVOCATION_ID,
                "4242",
                "0",
                "900000",
                "900100",
            ],
        )
        self.assertEqual(
            base64.b64decode(fields[14], validate=True),
            START_CURSOR.encode("ascii"),
        )
        self.assertEqual(
            base64.b64encode(START_CURSOR.encode("ascii")).decode("ascii"),
            fields[14],
        )
        self.assertTrue(transport.ready.endswith(b"\n"))
        self.assertEqual(transport.ready.count(b"\n"), 1)

    def test_capture_has_strict_order_monitor_pumping_and_both_fences(self):
        runtime, _, _ = collect()
        self.assertEqual(
            event_names(runtime.events),
            [
                "transport.read_start",
                "runtime.read_boot_id",
                "runtime.read_service",
                "runtime.read_global_cursor",
                "runtime.start_monitor",
                "monitor.wait_ready",
                "monitor.require_alive",
                "runtime.read_boot_id",
                "runtime.read_service",
                "transport.write_ready",
                "transport.read_finish_and_eof",
                "monitor.wait_until",
                "monitor.require_alive",
                "runtime.read_boot_id",
                "runtime.read_service",
                "runtime.sync_journal",
                "runtime.read_journal",
                "monitor.require_alive",
                "runtime.read_boot_id",
                "runtime.read_service",
                "monitor.require_alive",
                "monitor.wait_natural",
                "runtime.read_boot_id",
                "runtime.read_service",
                "runtime.sync_journal",
                "runtime.read_journal",
                "runtime.read_boot_id",
                "runtime.read_service",
                "transport.write_result",
                "monitor.close",
            ],
        )
        boot_events = [
            event for event in runtime.events
            if event[0] == "runtime.read_boot_id"
        ]
        service_events = [
            event for event in runtime.events
            if event[0] == "runtime.read_service"
        ]
        self.assertEqual(
            boot_events,
            [
                ("runtime.read_boot_id", False),
                ("runtime.read_boot_id", True),
                ("runtime.read_boot_id", True),
                ("runtime.read_boot_id", True),
                ("runtime.read_boot_id", False),
                ("runtime.read_boot_id", False),
            ],
        )
        self.assertEqual(
            service_events,
            [
                ("runtime.read_service", False),
                ("runtime.read_service", True),
                ("runtime.read_service", True),
                ("runtime.read_service", True),
                ("runtime.read_service", False),
                ("runtime.read_service", False),
            ],
        )

    def test_timing_waits_for_observation_and_records_each_boundary(self):
        runtime, transport, _ = collect()
        timing = decoded_document(transport)["timing_usec"]

        self.assertEqual(timing["ready"], int(ready_fields(transport)[13]))
        self.assertLessEqual(timing["ready"], timing["finish"])
        self.assertLessEqual(timing["finish"], timing["live_journal"])
        self.assertLess(timing["live_journal"], timing["monitor_exit"])
        self.assertLessEqual(timing["monitor_exit"], timing["final_journal"])
        self.assertLessEqual(timing["final_journal"], timing["complete"])
        self.assertGreaterEqual(
            timing["live_journal"] - timing["ready"],
            evidence.MIN_OBSERVATION_USEC,
        )
        self.assertLessEqual(
            timing["finish"] - timing["ready"],
            evidence.MAX_ACTION_WINDOW_USEC,
        )
        self.assertLessEqual(
            timing["complete"] - timing["ready"],
            evidence.MAX_SESSION_USEC,
        )
        waited_deadline = next(
            event[1]
            for event in runtime.events
            if event[0] == "monitor.wait_until"
        )
        self.assertGreaterEqual(
            waited_deadline,
            timing["ready"] + evidence.MIN_OBSERVATION_USEC,
        )

    def test_monitor_must_exit_naturally_before_final_journal_query(self):
        runtime, _, _ = collect()
        names = event_names(runtime.events)
        natural = names.index("monitor.wait_natural")
        syncs = [
            index
            for index, name in enumerate(names)
            if name == "runtime.sync_journal"
        ]
        journals = [
            index
            for index, name in enumerate(names)
            if name == "runtime.read_journal"
        ]
        self.assertLess(journals[0], natural)
        self.assertLess(natural, syncs[1])
        self.assertLess(syncs[1], journals[1])

    def test_initial_to_ready_live_and_final_boot_fences_are_enforced(self):
        for changed_index in (1, 2, 3, 4, 5):
            with self.subTest(changed_index=changed_index):
                runtime = ScriptedRuntime()
                runtime.boot_ids[changed_index] = OTHER_BOOT_ID
                transport = SemanticTransport(runtime)
                with self.assertRaises(evidence.ProducerError):
                    evidence.collect_evidence(runtime, transport)
                self.assertTrue(runtime.monitor.closed)
                self.assertIsNone(transport.result_body)

    def test_initial_to_ready_live_and_final_service_fences_are_enforced(self):
        for changed_index in (1, 2, 3, 4, 5):
            with self.subTest(changed_index=changed_index):
                runtime = ScriptedRuntime()
                runtime.services[changed_index] = replace(
                    runtime.services[changed_index],
                    invocation_id="4" * 32,
                )
                transport = SemanticTransport(runtime)
                with self.assertRaises(evidence.ProducerError):
                    evidence.collect_evidence(runtime, transport)
                self.assertTrue(runtime.monitor.closed)
                self.assertIsNone(transport.result_body)

    def test_finish_action_window_is_bounded(self):
        runtime = ScriptedRuntime()
        transport = SemanticTransport(runtime)
        transport.finish_delay_usec = evidence.MAX_ACTION_WINDOW_USEC + 1
        with self.assertRaises(evidence.ProducerError):
            evidence.collect_evidence(runtime, transport)
        self.assertTrue(runtime.monitor.closed)
        self.assertIsNone(transport.result_body)

    def test_session_window_is_bounded(self):
        runtime = ScriptedRuntime()
        runtime.monitor.natural_delay_usec = evidence.MAX_SESSION_USEC
        transport = SemanticTransport(runtime)
        with self.assertRaises(evidence.ProducerError):
            evidence.collect_evidence(runtime, transport)
        self.assertTrue(runtime.monitor.closed)
        self.assertIsNone(transport.result_body)

    def test_failure_after_monitor_start_always_closes_it(self):
        failure_points = (
            ("write_ready", "transport"),
            ("read_finish_and_eof", "transport"),
            ("sync_journal", "runtime"),
            ("read_journal", "runtime"),
        )
        for operation, owner in failure_points:
            with self.subTest(operation=operation):
                runtime = ScriptedRuntime()
                transport = SemanticTransport(runtime)
                target = transport if owner == "transport" else runtime
                target.failures[operation] = RuntimeError(operation)
                with self.assertRaisesRegex(RuntimeError, operation):
                    evidence.collect_evidence(runtime, transport)
                self.assertTrue(runtime.monitor.closed)

    def test_base_exception_is_not_swallowed_and_cleanup_preserves_primary(self):
        runtime = ScriptedRuntime()
        transport = SemanticTransport(runtime)
        primary = KeyboardInterrupt("primary")
        cleanup = RuntimeError("cleanup")
        transport.failures["read_finish_and_eof"] = primary
        runtime.monitor.close_error = cleanup

        with self.assertRaises(KeyboardInterrupt) as raised:
            evidence.collect_evidence(runtime, transport)

        self.assertIs(raised.exception, primary)
        self.assertTrue(runtime.monitor.closed)

    def test_pre_monitor_failure_does_not_attempt_monitor_cleanup(self):
        runtime = ScriptedRuntime()
        transport = SemanticTransport(runtime)
        runtime.failures["read_global_cursor"] = RuntimeError("cursor")
        with self.assertRaisesRegex(RuntimeError, "cursor"):
            evidence.collect_evidence(runtime, transport)
        self.assertNotIn("runtime.start_monitor", event_names(runtime.events))
        self.assertFalse(runtime.monitor.closed)


class EntrypointTest(unittest.TestCase):
    def test_arguments_are_rejected_before_runtime_or_transport_is_used(self):
        class Unexpected:
            def __getattribute__(self, name):
                raise AssertionError("adapter was touched: %s" % name)

        stderr = io.BytesIO()
        self.assertEqual(
            evidence._run(
                ["unexpected"],
                runtime=Unexpected(),
                transport=Unexpected(),
                stderr=stderr,
            ),
            64,
        )
        self.assertNotEqual(stderr.getvalue(), b"")

    def test_successful_run_returns_zero(self):
        runtime = ScriptedRuntime()
        transport = SemanticTransport(runtime)
        self.assertEqual(
            evidence._run(
                [],
                runtime=runtime,
                transport=transport,
                stderr=io.BytesIO(),
            ),
            0,
        )
        self.assertIsNotNone(transport.result_body)

    def test_producer_error_is_reported_without_traceback(self):
        runtime = ScriptedRuntime()
        transport = SemanticTransport(runtime)
        transport.failures["read_start"] = evidence.ProducerError("bad start")
        stderr = io.BytesIO()
        self.assertNotEqual(
            evidence._run(
                [],
                runtime=runtime,
                transport=transport,
                stderr=stderr,
            ),
            0,
        )
        self.assertIn(b"bad start", stderr.getvalue())
        self.assertNotIn(b"Traceback", stderr.getvalue())

    def test_diagnostic_is_single_line_ascii_bounded_and_sanitized(self):
        stderr = io.BytesIO()
        evidence._report_error(
            stderr,
            evidence.ProducerError("bad\r\n  thing\t\u00e9"),
        )
        self.assertEqual(stderr.getvalue(), b"bad thing ?\n")

        stderr = io.BytesIO()
        evidence._report_error(
            stderr,
            evidence.ProducerError("x" * evidence.MAX_DIAGNOSTIC_BYTES * 2),
        )
        self.assertLessEqual(
            len(stderr.getvalue()),
            evidence.MAX_DIAGNOSTIC_BYTES,
        )
        self.assertEqual(stderr.getvalue().count(b"\n"), 1)

    def test_diagnostic_partial_write_and_broken_sink_are_nonfatal(self):
        class PartialWriter:
            def __init__(self):
                self.flushes = 0

            def write(self, value):
                return max(0, len(value) - 1)

            def flush(self):
                self.flushes += 1

        partial = PartialWriter()
        evidence._report_error(partial, RuntimeError("failure"))
        self.assertEqual(partial.flushes, 0)

        class BrokenWriter:
            def write(self, value):
                raise BrokenPipeError()

        evidence._report_error(BrokenWriter(), RuntimeError("failure"))

    def test_termination_signals_enter_the_controlled_cleanup_path(self):
        for signal_number in (
            evidence.signal.SIGHUP,
            evidence.signal.SIGINT,
            evidence.signal.SIGTERM,
        ):
            with self.subTest(signal_number=signal_number):
                with self.assertRaisesRegex(
                    evidence.ProducerError,
                    evidence.signal.Signals(signal_number).name,
                ):
                    evidence._raise_on_termination(signal_number, None)

        with mock.patch.object(evidence.signal, "signal") as installed:
            evidence._install_signal_handlers()
        self.assertEqual(
            installed.call_args_list,
            [
                mock.call(
                    evidence.signal.SIGHUP,
                    evidence._raise_on_termination,
                ),
                mock.call(
                    evidence.signal.SIGINT,
                    evidence._raise_on_termination,
                ),
                mock.call(
                    evidence.signal.SIGTERM,
                    evidence._raise_on_termination,
                ),
            ],
        )


class ControlParsingTest(unittest.TestCase):
    def test_start_and_finish_are_exact_versioned_nonce_lines(self):
        start = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        finish = (
            "%s FINISH %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        self.assertEqual(
            evidence._parse_control_line(start, "START"),
            NONCE,
        )
        self.assertEqual(
            evidence._parse_control_line(finish, "FINISH", NONCE),
            NONCE,
        )

    def test_control_line_rejects_every_non_exact_framing_variant(self):
        valid = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        invalid = (
            b"",
            valid[:-1],
            valid[:-1] + b"\r\n",
            valid + b"\n",
            valid + b"trailing",
            valid.replace(b" START ", b"  START "),
            valid.replace(b" START ", b"\tSTART "),
            valid.replace(b"START", b"FINISH"),
            valid.replace(
                evidence.PROTOCOL_VERSION.encode("ascii"),
                b"KODI-PASSIVE-EVIDENCE/2",
            ),
            valid.replace(NONCE.encode("ascii"), b"A" * 32),
            valid.replace(NONCE.encode("ascii"), b"0" * 32),
            valid.replace(NONCE.encode("ascii"), b"a" * 31),
            valid.replace(NONCE.encode("ascii"), b"\xff" * 32),
            b"x" * evidence.MAX_CONTROL_BYTES + b"\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(evidence.ProducerError):
                    evidence._parse_control_line(raw, "START")

    def test_finish_nonce_must_match_start_nonce(self):
        finish = (
            "%s FINISH %s\n"
            % (evidence.PROTOCOL_VERSION, OTHER_NONCE)
        ).encode("ascii")
        with self.assertRaisesRegex(evidence.ProducerError, "nonce"):
            evidence._parse_control_line(
                finish,
                "FINISH",
                NONCE,
            )


class AdapterParserBoundaryTest(unittest.TestCase):
    def test_boot_id_parser_accepts_only_one_lowercase_uuid_line(self):
        self.assertEqual(
            evidence._parse_boot_id(
                b"11111111-1111-1111-1111-111111111111\n"
            ),
            BOOT_ID,
        )
        invalid = (
            b"",
            b"11111111-1111-1111-1111-111111111111",
            b"11111111-1111-1111-1111-111111111111\r\n",
            b"11111111-1111-1111-1111-111111111111\nextra",
            b"11111111-1111-1111-1111-11111111111g\n",
            b"AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA\n",
            b"00000000-0000-0000-0000-000000000000\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(evidence.ProducerError):
                    evidence._parse_boot_id(raw)
        with self.assertRaises(TypeError):
            evidence._parse_boot_id("not bytes")

    def test_service_parser_requires_all_nine_fields_exactly_once(self):
        self.assertEqual(evidence._parse_service(SERVICE_OUTPUT), service())
        lines = SERVICE_OUTPUT.splitlines(keepends=True)
        invalid = {
            "missing": b"".join(lines[:-1]),
            "duplicate": SERVICE_OUTPUT + lines[0],
            "unknown": SERVICE_OUTPUT.replace(b"Id=", b"Other=", 1),
            "no newline": SERVICE_OUTPUT[:-1],
            "carriage return": SERVICE_OUTPUT.replace(b"\n", b"\r\n", 1),
            "non-ASCII": SERVICE_OUTPUT.replace(b"loaded", b"\xff"),
            "noncanonical PID": SERVICE_OUTPUT.replace(
                b"MainPID=4242",
                b"MainPID=04242",
            ),
            "zero PID": SERVICE_OUTPUT.replace(
                b"MainPID=4242",
                b"MainPID=0",
            ),
            "negative restarts": SERVICE_OUTPUT.replace(
                b"NRestarts=0",
                b"NRestarts=-1",
            ),
            "zero exec timestamp": SERVICE_OUTPUT.replace(
                b"ExecMainStartTimestampMonotonic=900000",
                b"ExecMainStartTimestampMonotonic=0",
            ),
            "zero active timestamp": SERVICE_OUTPUT.replace(
                b"ActiveEnterTimestampMonotonic=900100",
                b"ActiveEnterTimestampMonotonic=0",
            ),
            "timestamps reversed": SERVICE_OUTPUT.replace(
                b"ExecMainStartTimestampMonotonic=900000",
                b"ExecMainStartTimestampMonotonic=900200",
            ),
            "inactive": SERVICE_OUTPUT.replace(
                b"ActiveState=active",
                b"ActiveState=inactive",
            ),
            "zero invocation": SERVICE_OUTPUT.replace(
                INVOCATION_ID.encode("ascii"),
                b"0" * 32,
            ),
        }
        for label, raw in invalid.items():
            with self.subTest(label=label):
                with self.assertRaises(evidence.ProducerError):
                    evidence._parse_service(raw)
        with self.assertRaises(TypeError):
            evidence._parse_service("not bytes")

    def test_cursor_parser_accepts_only_one_bounded_printable_ascii_line(self):
        maximum = "c" * evidence.MAX_CURSOR_BYTES
        self.assertEqual(
            evidence._parse_cursor_output(
                b"-- cursor: " + maximum.encode("ascii") + b"\n"
            ),
            maximum,
        )
        invalid = (
            b"",
            b"-- cursor: \n",
            b"-- cursor: value",
            b"-- cursor: value\r\n",
            b"-- cursor: value\nextra",
            b"-- cursor: value\n-- cursor: other\n",
            b"-- cursor: \xff\n",
            b"cursor: value\n",
            b"-- cursor: " + b"c" * (evidence.MAX_CURSOR_BYTES + 1) + b"\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(evidence.ProducerError):
                    evidence._parse_cursor_output(raw)
        with self.assertRaises(evidence.ProducerError):
            evidence._parse_cursor_output("not bytes")


class TransportMonitor:
    def __init__(self):
        self.pumps = 0
        self.alive_checks = 0

    def pump(self, timeout_usec=0):
        self.pumps += 1

    def require_alive(self):
        self.alive_checks += 1


class StepClock:
    def __init__(self, start=0, step=0):
        self.value = start
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


class FixedTransportTest(unittest.TestCase):
    def setUp(self):
        self.stdin_read_fd, self.stdin_write_fd = os.pipe()
        self.stdout_read_fd, self.stdout_write_fd = os.pipe()
        self.stdin = os.fdopen(self.stdin_read_fd, "rb", buffering=0)
        self.stdout = os.fdopen(self.stdout_write_fd, "wb", buffering=0)
        self.clock = StepClock()
        self.transport = evidence.FixedTransport(
            self.stdin,
            self.stdout,
            self.clock,
        )
        self.real_read = os.read
        self.real_write = os.write

    def tearDown(self):
        for stream in (self.stdin, self.stdout):
            try:
                stream.close()
            except OSError:
                pass
        for descriptor in (self.stdin_write_fd, self.stdout_read_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _write_input(self, value):
        self.real_write(self.stdin_write_fd, value)

    def _close_input(self):
        try:
            os.close(self.stdin_write_fd)
        except OSError:
            pass
        self.stdin_write_fd = -1

    def test_start_and_finish_survive_fragmentation_at_every_byte_boundary(self):
        start = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        finish = (
            "%s FINISH %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")

        def one_byte_read(descriptor, count):
            return self.real_read(descriptor, min(1, count))

        self._write_input(start)
        with mock.patch.object(
            evidence.os,
            "read",
            side_effect=one_byte_read,
        ):
            self.assertEqual(self.transport.read_start(1_000_000), NONCE)

        self._write_input(finish)
        self._close_input()
        monitor = TransportMonitor()
        with mock.patch.object(
            evidence.os,
            "read",
            side_effect=one_byte_read,
        ):
            self.transport.read_finish_and_eof(
                NONCE,
                1_000_000,
                monitor,
            )
        self.assertGreater(monitor.pumps, len(finish))
        self.assertGreater(monitor.alive_checks, 0)

    def test_start_rejects_pipelined_input_before_ready(self):
        start = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        finish = (
            "%s FINISH %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        self._write_input(start + finish)
        with self.assertRaisesRegex(evidence.ProducerError, "before READY"):
            self.transport.read_start(1_000_000)

    def test_finish_requires_exact_matching_line_followed_by_eof(self):
        start = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        wrong_finish = (
            "%s FINISH %s\n"
            % (evidence.PROTOCOL_VERSION, OTHER_NONCE)
        ).encode("ascii")
        self._write_input(start)
        self.assertEqual(self.transport.read_start(1_000_000), NONCE)
        self._write_input(wrong_finish)
        self._close_input()
        with self.assertRaisesRegex(evidence.ProducerError, "nonce"):
            self.transport.read_finish_and_eof(
                NONCE,
                1_000_000,
                TransportMonitor(),
            )

    def test_finish_rejects_any_bytes_after_its_line(self):
        start = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        finish = (
            "%s FINISH %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        self._write_input(start)
        self.assertEqual(self.transport.read_start(1_000_000), NONCE)
        self._write_input(finish + b"x")
        self._close_input()
        with self.assertRaisesRegex(evidence.ProducerError, "followed"):
            self.transport.read_finish_and_eof(
                NONCE,
                1_000_000,
                TransportMonitor(),
            )

    def test_finish_waits_for_eof_but_remains_deadline_bounded(self):
        start = (
            "%s START %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        finish = (
            "%s FINISH %s\n" % (evidence.PROTOCOL_VERSION, NONCE)
        ).encode("ascii")
        self._write_input(start)
        self.assertEqual(self.transport.read_start(1_000_000), NONCE)
        self._write_input(finish)
        self.transport.clock = StepClock(start=0, step=2)
        with self.assertRaisesRegex(evidence.ProducerError, "deadline"):
            self.transport.read_finish_and_eof(
                NONCE,
                5,
                TransportMonitor(),
            )

    def test_input_eof_before_a_complete_line_is_rejected(self):
        self._write_input(b"incomplete")
        self._close_input()
        with self.assertRaisesRegex(evidence.ProducerError, "before a line"):
            self.transport.read_start(1_000_000)

    def test_ready_and_result_tolerate_partial_writes_without_reframing(self):
        ready = b"ready bytes\n"
        header = b"result header\n"
        body = b'{"body":"bytes"}'
        monitor = TransportMonitor()

        def one_byte_write(descriptor, value):
            return self.real_write(descriptor, value[:1])

        with mock.patch.object(
            evidence.os,
            "write",
            side_effect=one_byte_write,
        ):
            self.transport.write_ready(
                ready,
                1_000_000,
                monitor,
            )
            self.transport.write_result(
                header,
                body,
                1_000_000,
            )

        self.assertEqual(
            self.real_read(
                self.stdout_read_fd,
                len(ready) + len(header) + len(body),
            ),
            ready + header + body,
        )
        self.assertGreaterEqual(monitor.pumps, len(ready))
        self.assertGreaterEqual(monitor.alive_checks, len(ready))

    def test_output_is_deadline_bounded(self):
        self.transport.clock = StepClock(start=0, step=2)
        with self.assertRaisesRegex(evidence.ProducerError, "deadline"):
            self.transport.write_result(b"header", b"body", 1)


SERVICE_OUTPUT = (
    "Id=cec-tv-wake.service\n"
    "LoadState=loaded\n"
    "ActiveState=active\n"
    "SubState=running\n"
    "InvocationID=%s\n"
    "MainPID=4242\n"
    "NRestarts=0\n"
    "ExecMainStartTimestampMonotonic=900000\n"
    "ActiveEnterTimestampMonotonic=900100\n"
    % INVOCATION_ID
).encode("ascii")


class RecordingChild:
    instances = []
    outputs = {
        "systemctl show": SERVICE_OUTPUT,
        "journal cursor query": (
            b"-- cursor: " + START_CURSOR.encode("ascii") + b"\n"
        ),
        "journal synchronization": b"",
        "global journal query": FINAL_JOURNAL,
    }

    def __init__(self, argv, **keywords):
        self.argv = tuple(argv)
        self.keywords = keywords
        self.closed = False
        self.signalled = False
        RecordingChild.instances.append(self)

    def wait(self, deadline, companion=None):
        self.deadline = deadline
        self.companion = companion
        return self.outputs[self.keywords["description"]]

    def close(self):
        self.closed = True

    def pump(self, timeout_usec=0):
        return None

    def require_alive(self):
        return None


class FixedRuntimeTest(unittest.TestCase):
    def setUp(self):
        RecordingChild.instances = []
        self.monitor = TransportMonitor()
        self.runtime = evidence.FixedRuntime(clock=lambda: 1_000)

    def test_all_runtime_commands_are_fixed_exact_argv(self):
        with mock.patch.object(
            evidence,
            "_CapturedChild",
            RecordingChild,
        ):
            parsed_service = self.runtime.read_service(
                1_000_000,
                self.monitor,
            )
            cursor = self.runtime.read_global_cursor(1_000_000)
            monitor = self.runtime.start_monitor()
            self.runtime.sync_journal(1_000_000, self.monitor)
            journal = self.runtime.read_journal(
                START_CURSOR,
                1_000_000,
                self.monitor,
            )

        self.assertEqual(parsed_service, service())
        self.assertEqual(cursor, START_CURSOR)
        self.assertIsInstance(monitor, evidence._MonitorProcess)
        self.assertEqual(journal, FINAL_JOURNAL)
        commands = {
            instance.keywords["description"]: instance.argv
            for instance in RecordingChild.instances
        }
        self.assertEqual(commands["systemctl show"], evidence.SERVICE_COMMAND)
        self.assertEqual(
            commands["journal cursor query"],
            evidence.CURSOR_COMMAND,
        )
        self.assertEqual(
            commands["passive CEC monitor"],
            (
                evidence.CEC_CTL,
                "-d",
                "/dev/cec0",
                "--monitor",
                "--show-raw",
                "--skip-info",
                "--monitor-time",
                "20",
            ),
        )
        self.assertEqual(
            commands["journal synchronization"],
            (evidence.JOURNALCTL, "--sync"),
        )
        self.assertEqual(
            commands["global journal query"],
            (
                evidence.JOURNALCTL,
                "--boot",
                "--after-cursor=" + START_CURSOR,
                "--no-tail",
                "--output=json-seq",
                "--show-cursor",
                "--no-pager",
                "--quiet",
                "--output-fields=" + evidence._JOURNAL_FIELDS,
            ),
        )

    def test_capture_journal_is_global_finite_and_has_no_line_or_unit_limit(self):
        with mock.patch.object(
            evidence,
            "_CapturedChild",
            RecordingChild,
        ):
            self.runtime.read_journal(
                START_CURSOR,
                1_000_000,
                self.monitor,
            )
        command = RecordingChild.instances[0].argv
        self.assertIn("--no-tail", command)
        self.assertIn("--after-cursor=" + START_CURSOR, command)
        for argument in command:
            with self.subTest(argument=argument):
                self.assertNotEqual(argument, "-n")
                self.assertNotEqual(argument, "-u")
                self.assertFalse(argument.startswith("--lines"))
                self.assertFalse(argument.startswith("--unit"))
        self.assertNotIn(evidence.SERVICE_UNIT, command)

    def test_monitor_duration_is_exactly_twenty_seconds(self):
        self.assertEqual(evidence.MONITOR_DURATION_SECONDS, 20)
        self.assertEqual(
            evidence.MONITOR_COMMAND.count("--monitor-time"),
            1,
        )
        duration_index = evidence.MONITOR_COMMAND.index("--monitor-time") + 1
        self.assertEqual(evidence.MONITOR_COMMAND[duration_index], "20")

    def test_child_process_disables_shell_and_inherits_no_ambient_path(self):
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        os.close(stdout_write)
        os.close(stderr_write)

        class Process:
            def __init__(self):
                self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
                self.stderr = os.fdopen(stderr_read, "rb", buffering=0)

            def poll(self):
                return 0

        calls = []

        def popen_factory(argv, **keywords):
            calls.append((argv, keywords))
            return Process()

        child = evidence._CapturedChild(
            evidence.MONITOR_COMMAND,
            maximum_stdout=100,
            description="fixed child",
            clock=lambda: 0,
            popen_factory=popen_factory,
        )
        child.completed_cleanly = True
        child.fully_reaped = True
        child.close()

        self.assertEqual(len(calls), 1)
        argv, keywords = calls[0]
        self.assertEqual(argv, list(evidence.MONITOR_COMMAND))
        self.assertIs(keywords["shell"], False)
        self.assertIs(keywords["close_fds"], True)
        self.assertIs(keywords["start_new_session"], True)
        self.assertEqual(keywords["env"], evidence._CHILD_ENV)
        self.assertEqual(keywords["env"]["PATH"], "/nonexistent")
        self.assertNotIn("HOME", keywords["env"])

    def test_command_cleanup_preserves_base_exception_as_primary(self):
        primary = KeyboardInterrupt("primary")
        cleanup = RuntimeError("cleanup")

        class FailingChild:
            def __init__(self, *arguments, **keywords):
                pass

            def wait(self, deadline, companion=None):
                raise primary

            def close(self):
                raise cleanup

        with mock.patch.object(evidence, "_CapturedChild", FailingChild):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.runtime._run_command(
                    evidence.SYNC_COMMAND,
                    maximum_stdout=1,
                    description="failure",
                    deadline=1_000_000,
                    monitor=self.monitor,
                )
        self.assertIs(raised.exception, primary)
        self.assertIs(raised.exception.__cause__, cleanup)


class CompletedProcess:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def completed_child(
    *,
    stdout=b"",
    stderr=b"",
    returncode=0,
    signalled=False,
):
    child = object.__new__(evidence._CapturedChild)
    child.description = "test child"
    child.clock = lambda: 0
    child.stdout_bytes = bytearray(stdout)
    child.stderr_bytes = bytearray(stderr)
    child.stdout_eof = True
    child.stderr_eof = True
    child.closed = False
    child.signalled = signalled
    child.completed_cleanly = False
    child.fully_reaped = False
    child.process = CompletedProcess(returncode)
    child.stdout = None
    child.stderr = None
    return child


class CapturedChildTest(unittest.TestCase):
    def test_wait_requires_status_zero_empty_stderr_and_returns_full_stdout(self):
        child = completed_child(stdout=b"complete stdout")
        self.assertEqual(child.wait(1_000_000), b"complete stdout")

        failed = completed_child(returncode=9)
        with self.assertRaisesRegex(evidence.ProducerError, "status 9"):
            failed.wait(1_000_000)
        self.assertTrue(failed.fully_reaped)
        with mock.patch.object(evidence.os, "killpg") as kill_group:
            failed.close()
        kill_group.assert_not_called()

        with self.assertRaisesRegex(evidence.ProducerError, "stderr"):
            completed_child(stderr=b"diagnostic").wait(1_000_000)

    def test_stdout_and_stderr_are_independently_bounded(self):
        child = completed_child()
        child.stdout_eof = False
        child.stderr_eof = False
        child.maximum_stdout = 3

        class Pipe:
            def fileno(self):
                return 99

        with mock.patch.object(evidence.os, "read", return_value=b"xxxx"):
            with self.assertRaisesRegex(evidence.ProducerError, "stdout"):
                child._read_pipe(Pipe(), "stdout")
        with mock.patch.object(evidence.os, "read", return_value=b"x"):
            with self.assertRaisesRegex(evidence.ProducerError, "stderr"):
                child._read_pipe(Pipe(), "stderr")

    def test_close_terminates_reaps_closes_pipes_and_is_idempotent(self):
        process = CleanupProcess(wait_effects=[0])
        stdout = CleanupPipe()
        stderr = CleanupPipe()
        child = cleanup_child(process, stdout, stderr)

        def signal_group(process_id, signal_number):
            self.assertEqual(process_id, process.pid)
            process.signals.append(signal_number)
            if signal_number == 0:
                raise ProcessLookupError

        with mock.patch.object(
            evidence.os,
            "killpg",
            side_effect=signal_group,
        ):
            child.close()
            child.close()

        self.assertTrue(child.closed)
        self.assertTrue(child.signalled)
        self.assertEqual(
            process.signals,
            [evidence.signal.SIGTERM, 0],
        )
        self.assertEqual(
            process.wait_timeouts,
            [evidence.CLEANUP_TIMEOUT_SECONDS],
        )
        self.assertEqual(stdout.closes, 1)
        self.assertEqual(stderr.closes, 1)

    def test_close_escalates_from_terminate_to_kill_with_bounded_waits(self):
        timeout = evidence.subprocess.TimeoutExpired(
            "test child",
            evidence.CLEANUP_TIMEOUT_SECONDS,
        )
        process = CleanupProcess(wait_effects=[timeout, 0])
        child = cleanup_child(process, CleanupPipe(), CleanupPipe())

        def signal_group(process_id, signal_number):
            self.assertEqual(process_id, process.pid)
            process.signals.append(signal_number)

        with mock.patch.object(
            evidence.os,
            "killpg",
            side_effect=signal_group,
        ):
            child.close()

        self.assertEqual(
            process.signals,
            [
                evidence.signal.SIGTERM,
                0,
                evidence.signal.SIGKILL,
            ],
        )
        self.assertEqual(
            process.wait_timeouts,
            [
                evidence.CLEANUP_TIMEOUT_SECONDS,
                evidence.CLEANUP_TIMEOUT_SECONDS,
            ],
        )

    def test_close_reports_bounded_reap_failure_after_closing_pipes(self):
        first = evidence.subprocess.TimeoutExpired(
            "test child",
            evidence.CLEANUP_TIMEOUT_SECONDS,
        )
        second = evidence.subprocess.TimeoutExpired(
            "test child",
            evidence.CLEANUP_TIMEOUT_SECONDS,
        )
        process = CleanupProcess(wait_effects=[first, second])
        stdout = CleanupPipe()
        stderr = CleanupPipe()
        child = cleanup_child(process, stdout, stderr)

        def signal_group(process_id, signal_number):
            self.assertEqual(process_id, process.pid)
            process.signals.append(signal_number)

        with mock.patch.object(
            evidence.os,
            "killpg",
            side_effect=signal_group,
        ):
            with self.assertRaisesRegex(evidence.ProducerError, "reaped"):
                child.close()

        self.assertEqual(
            process.signals,
            [
                evidence.signal.SIGTERM,
                0,
                evidence.signal.SIGKILL,
            ],
        )
        self.assertEqual(stdout.closes, 1)
        self.assertEqual(stderr.closes, 1)


class CleanupPipe:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


class CleanupProcess:
    def __init__(self, wait_effects):
        self.wait_effects = list(wait_effects)
        self.pid = 424242
        self.returncode = None
        self.signals = []
        self.wait_timeouts = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.wait_timeouts.append(timeout)
        result = self.wait_effects.pop(0)
        if isinstance(result, BaseException):
            raise result
        self.returncode = result
        return result


def cleanup_child(process, stdout, stderr):
    child = object.__new__(evidence._CapturedChild)
    child.description = "test child"
    child.closed = False
    child.signalled = False
    child.completed_cleanly = False
    child.fully_reaped = False
    child.process = process
    child.stdout = stdout
    child.stderr = stderr
    return child


class FragmentedMonitorChild:
    def __init__(self, chunks, final):
        self.chunks = list(chunks)
        self.final = final
        self.stdout_bytes = bytearray()
        self.signalled = False
        self.closed = False

    def require_alive(self):
        return None

    def pump(self, timeout_usec=0):
        if self.chunks:
            self.stdout_bytes.extend(self.chunks.pop(0))

    def wait(self, deadline, companion=None):
        return self.final

    def close(self):
        self.closed = True


class FixedMonitorTest(unittest.TestCase):
    def test_readiness_preamble_can_arrive_at_arbitrary_boundaries(self):
        ready_line = (
            b"Initial Event: State Change: PA: a.b.c.d, "
            b"LA mask: 0x1234\n"
        )
        chunks = [bytes((byte,)) for byte in b"\n\n" + ready_line]
        child = FragmentedMonitorChild(
            chunks,
            b"\n\n" + ready_line + b"event\n",
        )
        clock = StepClock(start=0, step=1)
        monitor = evidence._MonitorProcess(child, clock)

        monitor.wait_ready(100_000)

        self.assertTrue(monitor.ready)
        self.assertEqual(child.chunks, [])

    def test_natural_exit_requires_ready_complete_output_and_no_signal(self):
        trace = (
            b"\n\nInitial Event: State Change: PA: 0.0.0.0, "
            b"LA mask: 0x0002\n"
        )
        child = FragmentedMonitorChild([], trace)
        monitor = evidence._MonitorProcess(child, lambda: 0)
        monitor.ready = True
        self.assertEqual(monitor.wait_natural(1_000_000), trace)
        self.assertTrue(monitor.natural)

        for final, signalled, message in (
            (trace[:-1], False, "incomplete"),
            (trace, True, "signalled"),
        ):
            with self.subTest(message=message):
                child = FragmentedMonitorChild([], final)
                child.signalled = signalled
                monitor = evidence._MonitorProcess(child, lambda: 0)
                monitor.ready = True
                with self.assertRaisesRegex(evidence.ProducerError, message):
                    monitor.wait_natural(1_000_000)

    def test_natural_exit_cannot_be_claimed_without_readiness(self):
        child = FragmentedMonitorChild([], b"event\n")
        monitor = evidence._MonitorProcess(child, lambda: 0)
        with self.assertRaisesRegex(evidence.ProducerError, "never became ready"):
            monitor.wait_natural(1_000_000)


class CecReadinessTest(unittest.TestCase):
    def test_readiness_is_syntactic_and_not_adapter_specific(self):
        valid = (
            b"Initial Event: State Change: PA: 0.0.0.0, "
            b"LA mask: 0x0000\n",
            b"Initial Event: State Change: PA: 1.a.f.0, "
            b"LA mask: 0x0002\n",
            b"Initial Event: State Change: PA: f.f.f.f, "
            b"LA mask: 0xffff\n",
        )
        for line in valid:
            with self.subTest(line=line):
                self.assertIsNotNone(
                    evidence._CEC_READY_PATTERN.fullmatch(line)
                )
        self.assertNotIn(b"recording", evidence._CEC_READY_PATTERN.pattern)
        self.assertNotIn(b"adapter", evidence._CEC_READY_PATTERN.pattern)


class PassiveCapabilitySourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(evidence.__file__)
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_only_declared_source_placeholders_exist(self):
        shebang = self.source.splitlines()[0]
        placeholders = set(
            re.findall(r"@[A-Z][A-Z0-9_]*@", self.source)
        )
        declared = {"@CEC_CTL@", "@SYSTEMCTL@", "@JOURNALCTL@"}
        if shebang == "#!/usr/bin/env python3":
            self.assertEqual(placeholders, declared)
        else:
            self.assertRegex(
                shebang,
                r"\A#!/nix/store/[^ ]+/bin/python3 -I\Z",
            )
            self.assertEqual(placeholders, set())

    def test_source_has_no_network_kodi_ui_or_cec_transmit_capability(self):
        lowered = self.source.lower()
        forbidden = (
            "import socket",
            "from socket",
            "urllib",
            "requests",
            "http.client",
            "import xbmc",
            "from xbmc",
            "jsonrpc",
            "--to ",
            "--to=",
            "--image-view-on",
            "--active-source",
            "--standby",
            "--deck-control",
            "--user-control-pressed",
            "--user-control-released",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, lowered)
        string_literals = {
            node.value.lower()
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
        }
        for flag in (
            "--to",
            "--image-view-on",
            "--active-source",
            "--standby",
            "--deck-control",
            "--user-control-pressed",
            "--user-control-released",
        ):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, string_literals)

    def test_source_never_enables_a_shell_or_generic_execution(self):
        forbidden_import_roots = {
            "http",
            "requests",
            "socket",
            "urllib",
            "websocket",
            "xbmc",
            "xbmcgui",
            "xbmcplugin",
        }
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for imported in node.names:
                    self.assertNotIn(
                        imported.name.split(".", 1)[0],
                        forbidden_import_roots,
                    )
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                self.assertNotIn(
                    node.module.split(".", 1)[0],
                    forbidden_import_roots,
                )
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    self.assertIsInstance(keyword.value, ast.Constant)
                    self.assertIs(keyword.value.value, False)
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    self.assertFalse(
                        node.func.attr in {"system", "popen"}
                        or node.func.attr.startswith("exec")
                        or node.func.attr.startswith("spawn"),
                    )
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                ):
                    self.assertEqual(node.func.attr, "Popen")


if __name__ == "__main__":
    unittest.main()
