from __future__ import annotations

import base64
import inspect
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tools.kodi_capture.passive_evidence import (
    PassiveEvidenceContinuityError,
    PassiveEvidencePolicyError,
    PassiveEvidenceProtocolError,
    ReadyEvidence,
)
from tools.kodi_capture.passive_session import (
    ACTION_COMPLETE,
    COLLECTED,
    HOST_ACTION_TIMEOUT_SECONDS,
    HOST_FINISH_TIMEOUT_SECONDS,
    HOST_SESSION_TIMEOUT_SECONDS,
    POISONED,
    READY,
    REMOTE_PASSIVE_EVIDENCE_PROGRAM,
    SEALED,
    PassiveSessionProtocolError,
    PassiveSessionCleanupError,
    PassiveSessionStateError,
    PassiveSessionTimeout,
    PassiveSessionTransportError,
    RemotePassiveEvidenceSession,
)
from tools.kodi_capture.process import (
    ProcessCleanupError,
    ProcessTimeout,
    ProcessTransportError,
)
from tools.kodi_capture.ssh_policy import (
    SSH_BASE_OPTIONS,
    SSH_OPTION_TERMINATOR,
    SSH_PROGRAM,
)


NONCE = "a" * 32
OTHER_NONCE = "b" * 32
BOOT_ID = "1" * 32
INVOCATION_ID = "2" * 32
START_CURSOR = "fixture-start-cursor"
READY_USEC = 1_000_000

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures"
CEC_TRACE = (
    FIXTURE_DIRECTORY
    / "cec_trace"
    / "normal-recording-device-1.txt"
).read_bytes()
FINAL_JOURNAL = base64.b64decode(
    (
        FIXTURE_DIRECTORY
        / "wake_journal"
        / "benign-global.json-seq.b64"
    ).read_bytes().strip(),
    validate=True,
)


def journal_prefix(raw, record_count):
    lines = raw.splitlines(keepends=True)
    records = lines[:-1]
    terminal = json.loads(records[record_count - 1][1:])
    return (
        b"".join(records[:record_count])
        + b"-- cursor: "
        + terminal["__CURSOR"].encode("ascii")
        + b"\n"
    )


LIVE_JOURNAL = journal_prefix(FINAL_JOURNAL, 2)


def service_document(**changes):
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
    return values


def ready_line(nonce=NONCE):
    service = service_document()
    fields = (
        "KODI-PASSIVE-EVIDENCE/1",
        "READY",
        nonce,
        BOOT_ID,
        service["unit_id"],
        service["load_state"],
        service["active_state"],
        service["sub_state"],
        service["invocation_id"],
        str(service["main_pid"]),
        str(service["n_restarts"]),
        str(service["exec_start_usec"]),
        str(service["active_enter_usec"]),
        str(READY_USEC),
        base64.b64encode(START_CURSOR.encode("ascii")).decode("ascii"),
    )
    return (" ".join(fields) + "\n").encode("ascii")


def evidence_body(**changes):
    service = service_document()
    document = {
        "version": "KODI-PASSIVE-EVIDENCE/1",
        "nonce": NONCE,
        "start_cursor": START_CURSOR,
        "boot_ids": {
            "start": BOOT_ID,
            "live": BOOT_ID,
            "final": BOOT_ID,
        },
        "services": {
            "start": service,
            "live": service,
            "final": service,
        },
        "timing_usec": {
            "ready": READY_USEC,
            "finish": READY_USEC + 1,
            "live_journal": READY_USEC + 8_000_000,
            "monitor_exit": READY_USEC + 20_000_000,
            "final_journal": READY_USEC + 20_000_001,
            "complete": READY_USEC + 20_000_002,
        },
        "cec_trace_b64": base64.b64encode(CEC_TRACE).decode("ascii"),
        "live_journal_b64": base64.b64encode(LIVE_JOURNAL).decode("ascii"),
        "final_journal_b64": base64.b64encode(FINAL_JOURNAL).decode("ascii"),
    }
    for path, value in changes.items():
        document[path] = value
    return json.dumps(
        document,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def result_header(body, nonce=NONCE, length=None):
    if length is None:
        length = len(body)
    return (
        "KODI-PASSIVE-EVIDENCE/1 RESULT %s %d\n"
        % (nonce, length)
    ).encode("ascii")


class ManualClock:
    def __init__(self, value=10.0):
        self.value = value

    def __call__(self):
        return self.value


class ScriptedProcess:
    def __init__(
        self,
        *,
        ready=None,
        body=None,
        header=None,
        trailing=b"",
        stderr=b"",
        fragment_size=65536,
    ):
        self.ready_bytes = ready if ready is not None else ready_line()
        self.body = body if body is not None else evidence_body()
        self.header = (
            header if header is not None
            else result_header(self.body)
        )
        self.trailing = trailing
        self.output = bytearray()
        self.stderr_tail = stderr
        self.fragment_size = fragment_size
        self.events = []
        self.writes = []
        self.write_deadlines = []
        self.read_deadlines = []
        self.read_all_deadlines = []
        self.closed = 0
        self.input_closed = 0
        self.running_checks = 0
        self.failures = {}

    def write(self, data, deadline):
        self.events.append(("write", data))
        self.write_deadlines.append(deadline)
        self._fail("write")
        self.writes.append(data)
        if len(self.writes) == 1:
            self.output.extend(self.ready_bytes)

    def read(self, maximum, deadline):
        self.events.append(("read", maximum))
        self.read_deadlines.append(deadline)
        self._fail("read")
        if not self.output:
            raise ProcessTransportError("unexpected remote EOF")
        count = min(maximum, self.fragment_size, len(self.output))
        chunk = bytes(self.output[:count])
        del self.output[:count]
        return chunk

    def close_input(self):
        self.events.append(("close_input",))
        self._fail("close_input")
        self.input_closed += 1
        self.output.extend(self.header + self.body)

    def require_running_with_empty_stderr(self):
        self.events.append(("require_running",))
        self.running_checks += 1
        self._fail("require_running")
        if self.stderr_tail:
            raise ProcessTransportError("remote wrote to stderr")

    def read_all(self, maximum, deadline):
        self.events.append(("read_all", maximum))
        self.read_all_deadlines.append(deadline)
        self._fail("read_all")
        if self.output:
            count = min(maximum, len(self.output))
            chunk = bytes(self.output[:count])
            del self.output[:count]
            return chunk
        return self.trailing

    def close(self):
        self.events.append(("close",))
        self.closed += 1
        self._fail("close")

    def _fail(self, operation):
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure


class ProcessFactory:
    def __init__(self, process):
        self.process = process
        self.calls = []

    def __call__(self, argv, **keywords):
        self.calls.append((list(argv), keywords))
        return self.process


def open_session(process=None, clock=None, deadline=100.0):
    process = process or ScriptedProcess()
    clock = clock or ManualClock()
    factory = ProcessFactory(process)
    with mock.patch(
        "tools.kodi_capture.passive_session.BoundedProcess",
        factory,
    ):
        session = RemotePassiveEvidenceSession(
            "root@htpc-pi.local",
            deadline,
            clock=clock,
            nonce_factory=lambda: NONCE,
        )
    return session, process, factory, clock


def finish_session(session, clock, action=None, deadline=100.0):
    calls = []

    def default_action(ready, action_deadline):
        calls.append((ready, action_deadline))

    session.perform_action(action or default_action, deadline)
    session.seal_action_window(deadline)
    evidence = session.collect(deadline)
    return evidence, calls


class RemotePassiveEvidenceSessionTest(unittest.TestCase):
    def test_success_uses_exact_protocol_and_returns_decoded_evidence(self):
        session, process, factory, clock = open_session(fragment_size_process(1))
        self.assertEqual(session.state, READY)
        self.assertIsInstance(session.ready, ReadyEvidence)

        evidence, actions = finish_session(session, clock)

        self.assertEqual(session.state, COLLECTED)
        self.assertEqual(evidence.ready, session.ready)
        self.assertEqual(evidence.cec_trace.raw, CEC_TRACE)
        self.assertEqual(evidence.live_journal.raw, LIVE_JOURNAL)
        self.assertEqual(evidence.final_journal.raw, FINAL_JOURNAL)
        self.assertEqual(len(actions), 1)
        self.assertIs(actions[0][0], session.ready)
        self.assertEqual(
            actions[0][1],
            10.0 + HOST_ACTION_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            process.writes,
            [
                (
                    "KODI-PASSIVE-EVIDENCE/1 START %s\n" % NONCE
                ).encode("ascii"),
                (
                    "KODI-PASSIVE-EVIDENCE/1 FINISH %s\n" % NONCE
                ).encode("ascii"),
            ],
        )
        self.assertEqual(process.input_closed, 1)
        self.assertEqual(process.closed, 1)
        self.assertGreaterEqual(process.running_checks, 2)

    def test_ssh_argv_is_fixed_hardened_and_has_no_shell_string(self):
        session, _, factory, _ = open_session()
        argv, keywords = factory.calls[0]
        self.assertEqual(
            argv,
            [
                SSH_PROGRAM,
                "-T",
                "-F",
                "/dev/null",
                *SSH_BASE_OPTIONS,
                "-o",
                "ForwardAgent=no",
                "-o",
                "ForwardX11=no",
                "-o",
                "PermitLocalCommand=no",
                "-o",
                "EscapeChar=none",
                "-o",
                "ControlMaster=no",
                "-o",
                "ControlPath=none",
                SSH_OPTION_TERMINATOR,
                "root@htpc-pi.local",
                REMOTE_PASSIVE_EVIDENCE_PROGRAM,
            ],
        )
        self.assertNotIn("shell", keywords)
        self.assertEqual(keywords["graceful_timeout"], 0.25)
        self.assertEqual(keywords["terminate_timeout"], 1.0)
        session.close()

    def test_open_deadline_is_absolute_and_shared_by_start_and_ready(self):
        session, process, _, _ = open_session(deadline=123.25)
        self.assertEqual(process.write_deadlines, [123.25])
        self.assertTrue(process.read_deadlines)
        self.assertEqual(set(process.read_deadlines), {123.25})
        session.close()

    def test_action_and_finish_have_separate_latency_reserves(self):
        session, process, _, clock = open_session()
        observed = []

        def action(ready, deadline):
            observed.append((ready, deadline))
            clock.value = 13.4

        session.perform_action(action, 99.0)
        self.assertEqual(session.state, ACTION_COMPLETE)
        session.seal_action_window(99.0)
        self.assertEqual(session.state, SEALED)
        self.assertEqual(observed[0][1], 13.5)
        self.assertEqual(process.write_deadlines[-1], 14.0)

    def test_postchecks_fit_between_seal_and_collect(self):
        session, _, _, clock = open_session()
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        clock.value = 25.0
        evidence = session.collect(100.0)
        self.assertEqual(evidence.ready.nonce, NONCE)

    def test_collect_is_capped_by_internal_session_deadline(self):
        session, process, _, clock = open_session()
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        session.collect(1_000.0)
        expected = 10.0 + HOST_SESSION_TIMEOUT_SECONDS
        self.assertEqual(set(process.read_all_deadlines), {expected})
        self.assertIn(expected, process.read_deadlines)

    def test_context_exit_closes_unfinished_session(self):
        session, process, _, _ = open_session()
        with session:
            self.assertEqual(session.state, READY)
        self.assertEqual(process.closed, 1)

    def test_action_is_never_invoked_before_validated_ready(self):
        process = ScriptedProcess(ready=b"malformed READY\n")
        action_calls = []
        factory = ProcessFactory(process)
        with mock.patch(
            "tools.kodi_capture.passive_session.BoundedProcess",
            factory,
        ):
            with self.assertRaises(PassiveEvidenceProtocolError):
                RemotePassiveEvidenceSession(
                    "host",
                    100.0,
                    clock=ManualClock(),
                    nonce_factory=lambda: NONCE,
                )
        self.assertEqual(action_calls, [])
        self.assertEqual(process.closed, 1)

    def test_ready_nonce_mismatch_is_rejected_before_action(self):
        process = ScriptedProcess(ready=ready_line(OTHER_NONCE))
        with self.assertRaisesRegex(
            PassiveSessionProtocolError,
            "nonce",
        ):
            open_session(process)
        self.assertEqual(process.closed, 1)

    def test_ready_framing_size_and_early_eof_fail_closed(self):
        invalid = (
            ready_line()[:-1] + b"\r\n",
            ready_line().replace(b"READY", b"\xffEADY", 1),
            ready_line() + b"coalesced",
            b"x" * 8192 + b"\n",
        )
        for raw in invalid:
            process = ScriptedProcess(ready=raw)
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(
                    (
                        PassiveEvidenceProtocolError,
                        PassiveSessionProtocolError,
                    )
                ):
                    open_session(process)
                self.assertEqual(process.closed, 1)

        process = ScriptedProcess(ready=b"")
        with self.assertRaises(PassiveSessionTransportError):
            open_session(process)
        self.assertEqual(process.closed, 1)

    def test_pending_stderr_or_dead_process_rejects_ready(self):
        for failure in (
            ProcessTransportError("stderr pending"),
            ProcessTransportError("status 0"),
        ):
            process = ScriptedProcess()
            process.failures["require_running"] = failure
            with self.subTest(failure=failure):
                with self.assertRaises(PassiveSessionTransportError):
                    open_session(process)
                self.assertEqual(process.closed, 1)

    def test_action_is_exactly_once_and_failure_poisoned_and_reaped(self):
        session, process, _, _ = open_session()
        calls = []
        primary = RuntimeError("action failed")

        def action(_ready, _deadline):
            calls.append("called")
            raise primary

        with self.assertRaises(RuntimeError) as raised:
            session.perform_action(action, 100.0)
        self.assertIs(raised.exception, primary)
        self.assertEqual(calls, ["called"])
        self.assertEqual(session.state, POISONED)
        self.assertEqual(process.closed, 1)
        with self.assertRaises(PassiveSessionStateError):
            session.perform_action(action, 100.0)
        self.assertEqual(calls, ["called"])

    def test_action_cannot_reenter_and_invoke_a_second_callback(self):
        session, process, _, _ = open_session()
        calls = []

        def nested(_ready, _deadline):
            calls.append("nested")

        def outer(_ready, _deadline):
            calls.append("outer")
            session.perform_action(nested, 100.0)

        with self.assertRaises(PassiveSessionStateError):
            session.perform_action(outer, 100.0)
        self.assertEqual(calls, ["outer"])
        self.assertEqual(session.state, POISONED)
        self.assertEqual(process.closed, 1)

    def test_action_cleanup_failure_is_cause_not_replacement(self):
        session, process, _, _ = open_session()
        primary = KeyboardInterrupt("stop")
        cleanup = ProcessCleanupError("could not reap")
        process.failures["close"] = cleanup

        def action(_ready, _deadline):
            raise primary

        with self.assertRaises(KeyboardInterrupt) as raised:
            session.perform_action(action, 100.0)
        self.assertIs(raised.exception, primary)
        attached = raised.exception.__cause__
        self.assertIsInstance(attached, PassiveSessionCleanupError)
        self.assertIs(attached.__cause__, cleanup)
        with self.assertRaises(PassiveSessionCleanupError) as retained:
            session.close()
        self.assertIs(retained.exception, attached)

    def test_callback_raised_cleanup_type_still_reaps_the_process(self):
        session, process, _, _ = open_session()
        primary = PassiveSessionCleanupError("callback value")

        def action(_ready, _deadline):
            raise primary

        with self.assertRaises(PassiveSessionCleanupError) as raised:
            session.perform_action(action, 100.0)
        self.assertIs(raised.exception, primary)
        self.assertEqual(process.closed, 1)
        self.assertEqual(session.state, POISONED)

    def test_seal_and_collect_base_exceptions_keep_cleanup_as_cause(self):
        for phase in ("seal", "collect"):
            session, process, _, _ = open_session()
            session.perform_action(
                lambda _ready, _deadline: None,
                100.0,
            )
            if phase == "collect":
                session.seal_action_window(100.0)
                process.failures["read"] = KeyboardInterrupt("collect")
            else:
                process.failures["write"] = KeyboardInterrupt("seal")
            cleanup = ProcessCleanupError("could not reap")
            process.failures["close"] = cleanup

            with self.subTest(phase=phase):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    if phase == "seal":
                        session.seal_action_window(100.0)
                    else:
                        session.collect(100.0)
                attached = raised.exception.__cause__
                self.assertIsInstance(
                    attached,
                    PassiveSessionCleanupError,
                )
                self.assertIs(attached.__cause__, cleanup)
                with self.assertRaises(
                    PassiveSessionCleanupError
                ) as retained:
                    session.close()
                self.assertIs(retained.exception, attached)

    def test_action_timeout_never_sends_finish(self):
        session, process, _, clock = open_session()

        def slow(_ready, deadline):
            clock.value = deadline

        with self.assertRaises(PassiveSessionTimeout):
            session.perform_action(slow, 100.0)
        self.assertEqual(len(process.writes), 1)
        self.assertEqual(process.input_closed, 0)
        self.assertEqual(session.state, POISONED)

    def test_finish_requires_completed_action_and_is_one_shot(self):
        session, process, _, _ = open_session()
        with self.assertRaises(PassiveSessionStateError):
            session.seal_action_window(100.0)
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        with self.assertRaises(PassiveSessionStateError):
            session.seal_action_window(100.0)
        self.assertEqual(process.input_closed, 1)

    def test_finish_failure_poisoned_and_reaped(self):
        for operation, failure in (
            ("write", ProcessTimeout("finish timeout")),
            ("close_input", ProcessTransportError("half-close failed")),
        ):
            session, process, _, _ = open_session()
            session.perform_action(
                lambda _ready, _deadline: None,
                100.0,
            )
            process.failures[operation] = failure
            with self.subTest(operation=operation):
                with self.assertRaises(
                    (
                        PassiveSessionTimeout,
                        PassiveSessionTransportError,
                    )
                ):
                    session.seal_action_window(100.0)
                self.assertEqual(session.state, POISONED)
                self.assertEqual(process.closed, 1)

    def test_seal_and_collect_deadlines_are_strict(self):
        session, process, _, clock = open_session()
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        clock.value = 14.0
        with self.assertRaises(PassiveSessionTimeout):
            session.seal_action_window(100.0)
        self.assertEqual(len(process.writes), 1)
        self.assertEqual(session.state, POISONED)

        session, process, _, clock = open_session()
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        clock.value = 10.0 + HOST_SESSION_TIMEOUT_SECONDS
        with self.assertRaises(PassiveSessionTimeout):
            session.collect(100.0)
        self.assertEqual(session.state, POISONED)
        self.assertEqual(process.closed, 1)

    def test_collect_requires_sealed_and_cannot_repeat(self):
        session, _, _, _ = open_session()
        with self.assertRaises(PassiveSessionStateError):
            session.collect(100.0)
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        session.collect(100.0)
        with self.assertRaises(PassiveSessionStateError):
            session.collect(100.0)

    def test_result_header_and_body_are_strictly_bounded(self):
        cases = (
            ScriptedProcess(header=b"bad result\n"),
            ScriptedProcess(
                header=result_header(evidence_body(), length=1),
            ),
            ScriptedProcess(trailing=b"x"),
        )
        for process in cases:
            with self.subTest(process=process):
                session, _, _, _ = open_session(process)
                session.perform_action(
                    lambda _ready, _deadline: None,
                    100.0,
                )
                session.seal_action_window(100.0)
                with self.assertRaises(
                    (
                        PassiveEvidenceProtocolError,
                        PassiveSessionProtocolError,
                    )
                ):
                    session.collect(100.0)
                self.assertEqual(session.state, POISONED)
                self.assertEqual(process.closed, 1)

    def test_result_header_rejects_wrong_fields_and_noncanonical_length(self):
        body = evidence_body()
        valid = result_header(body)
        headers = (
            valid.replace(
                b"KODI-PASSIVE-EVIDENCE/1",
                b"KODI-PASSIVE-EVIDENCE/2",
            ),
            valid.replace(b" RESULT ", b" READY "),
            result_header(body, nonce=OTHER_NONCE),
            valid.replace(
                str(len(body)).encode("ascii"),
                ("0%d" % len(body)).encode("ascii"),
            ),
        )
        for header in headers:
            process = ScriptedProcess(header=header)
            session, _, _, _ = open_session(process)
            session.perform_action(
                lambda _ready, _deadline: None,
                100.0,
            )
            session.seal_action_window(100.0)
            with self.subTest(header=header):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    session.collect(100.0)
                self.assertEqual(session.state, POISONED)

    def test_result_requires_exact_body_length_and_clean_eof(self):
        body = evidence_body()
        scenarios = (
            ScriptedProcess(
                body=body,
                header=result_header(body, length=len(body) + 1),
            ),
            ScriptedProcess(
                body=body,
                header=result_header(body, length=len(body) - 1),
            ),
            ScriptedProcess(trailing=b"xx"),
        )
        for process in scenarios:
            session, _, _, _ = open_session(process)
            session.perform_action(
                lambda _ready, _deadline: None,
                100.0,
            )
            session.seal_action_window(100.0)
            with self.subTest(process=process):
                with self.assertRaises(
                    (
                        PassiveSessionProtocolError,
                        PassiveSessionTransportError,
                    )
                ):
                    session.collect(100.0)
                self.assertEqual(session.state, POISONED)

        process = ScriptedProcess()
        process.failures["read_all"] = ProcessTimeout("EOF timeout")
        session, _, _, _ = open_session(process)
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        with self.assertRaises(PassiveSessionTimeout):
            session.collect(100.0)

    def test_transport_timeout_stderr_and_exit_are_strict(self):
        scenarios = (
            ("read", ProcessTimeout("read timeout"), b""),
            ("read_all", ProcessTransportError("status 17"), b""),
            (None, None, b"ssh warning"),
        )
        for operation, failure, stderr in scenarios:
            process = ScriptedProcess()
            session, _, _, _ = open_session(process)
            session.perform_action(
                lambda _ready, _deadline: None,
                100.0,
            )
            session.seal_action_window(100.0)
            process.stderr_tail = stderr
            if operation is not None:
                process.failures[operation] = failure
            with self.subTest(operation=operation, stderr=stderr):
                with self.assertRaises(
                    (
                        PassiveSessionTimeout,
                        PassiveSessionTransportError,
                    )
                ):
                    session.collect(100.0)
                self.assertEqual(session.state, POISONED)
                self.assertEqual(process.closed, 1)

    def test_context_preserves_body_exception_and_retains_cleanup_error(self):
        session, process, _, _ = open_session()
        cleanup = ProcessCleanupError("could not reap")
        process.failures["close"] = cleanup
        primary = RuntimeError("body failed")

        with self.assertRaises(RuntimeError) as raised:
            with session:
                raise primary
        self.assertIs(raised.exception, primary)
        attached = raised.exception.__cause__
        self.assertIsInstance(attached, PassiveSessionCleanupError)
        self.assertIs(attached.__cause__, cleanup)
        with self.assertRaises(PassiveSessionCleanupError) as retained:
            session.close()
        self.assertIs(retained.exception, attached)

    def test_success_path_cleanup_failure_is_attempted_once_and_retained(self):
        session, process, _, _ = open_session()
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        cleanup = ProcessCleanupError("could not reap")
        process.failures["close"] = cleanup

        with self.assertRaises(PassiveSessionCleanupError) as first:
            session.collect(100.0)
        self.assertIs(first.exception.__cause__, cleanup)
        self.assertEqual(process.closed, 1)
        with self.assertRaises(PassiveSessionCleanupError) as second:
            session.close()
        self.assertIs(second.exception, first.exception)
        self.assertEqual(process.closed, 1)

    def test_context_does_not_self_chain_collect_cleanup_failure(self):
        session, process, _, _ = open_session()
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        cleanup = ProcessCleanupError("could not reap")
        process.failures["close"] = cleanup

        with self.assertRaises(PassiveSessionCleanupError) as raised:
            with session:
                session.collect(100.0)
        self.assertIs(raised.exception.__cause__, cleanup)
        self.assertEqual(process.closed, 1)
        with self.assertRaises(PassiveSessionCleanupError) as retained:
            session.close()
        self.assertIs(retained.exception, raised.exception)

    def test_consumer_continuity_error_is_preserved_unchanged(self):
        document = json.loads(evidence_body().decode("ascii"))
        document["boot_ids"]["final"] = "3" * 32
        body = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        process = ScriptedProcess(body=body, header=result_header(body))
        session, _, _, _ = open_session(process)
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        with self.assertRaises(PassiveEvidenceContinuityError):
            session.collect(100.0)

    def test_consumer_protocol_and_policy_errors_keep_classification(self):
        malformed = json.loads(evidence_body().decode("ascii"))
        malformed["cec_trace_b64"] = base64.b64encode(
            CEC_TRACE[:-1]
        ).decode("ascii")
        malformed_body = json.dumps(
            malformed,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

        forbidden_trace = CEC_TRACE.replace(
            b"GIVE_DEVICE_POWER_STATUS (0x8f)",
            b"ACTIVE_SOURCE (0x82)",
            1,
        ).replace(
            b"0x10 0x8f",
            b"0x10 0x82",
            1,
        )
        forbidden = json.loads(evidence_body().decode("ascii"))
        forbidden["cec_trace_b64"] = base64.b64encode(
            forbidden_trace
        ).decode("ascii")
        forbidden_body = json.dumps(
            forbidden,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

        process = ScriptedProcess(
            body=malformed_body,
            header=result_header(malformed_body),
        )
        session, _, _, _ = open_session(process)
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        with self.assertRaises(PassiveEvidenceProtocolError):
            session.collect(100.0)

        process = ScriptedProcess(
            body=forbidden_body,
            header=result_header(forbidden_body),
        )
        session, _, _, _ = open_session(process)
        session.perform_action(lambda _ready, _deadline: None, 100.0)
        session.seal_action_window(100.0)
        expected_ready = session.ready
        with self.assertRaises(PassiveEvidencePolicyError) as raised:
            session.collect(100.0)
        self.assertEqual(raised.exception.raw, forbidden_body)
        self.assertIs(raised.exception.ready, expected_ready)

    def test_close_is_idempotent_from_every_stable_state(self):
        for target in (READY, ACTION_COMPLETE, SEALED, COLLECTED):
            session, process, _, _ = open_session()
            if target in (ACTION_COMPLETE, SEALED, COLLECTED):
                session.perform_action(
                    lambda _ready, _deadline: None,
                    100.0,
                )
            if target in (SEALED, COLLECTED):
                session.seal_action_window(100.0)
            if target == COLLECTED:
                session.collect(100.0)
            with self.subTest(target=target):
                session.close()
                session.close()
                self.assertEqual(process.closed, 1)

    def test_invalid_deadlines_nonce_callback_and_host_never_spawn_or_act(self):
        invalid_deadlines = (True, float("inf"), float("nan"), "100")
        for deadline in invalid_deadlines:
            with self.subTest(deadline=deadline):
                with self.assertRaises(ValueError):
                    RemotePassiveEvidenceSession(
                        "host",
                        deadline,
                        nonce_factory=lambda: NONCE,
                    )
        for nonce in ("A" * 32, "0" * 32, "a" * 31, 7):
            with self.subTest(nonce=nonce):
                with self.assertRaises(ValueError):
                    RemotePassiveEvidenceSession(
                        "host",
                        100.0,
                        clock=ManualClock(),
                        nonce_factory=lambda nonce=nonce: nonce,
                    )
        with self.assertRaises(ValueError):
            RemotePassiveEvidenceSession(
                "-option",
                100.0,
                clock=ManualClock(),
                nonce_factory=lambda: NONCE,
            )
        for arguments in (
            {"graceful_timeout": -1},
            {"graceful_timeout": float("inf")},
            {"terminate_timeout": 0},
            {"max_stderr_bytes": 0},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    RemotePassiveEvidenceSession(
                        "host",
                        100.0,
                        clock=ManualClock(),
                        nonce_factory=lambda: NONCE,
                        **arguments,
                    )
        session, process, _, _ = open_session()
        with self.assertRaises(ValueError):
            session.perform_action(None, 100.0)
        self.assertEqual(process.closed, 0)
        session.close()

    def test_expired_open_deadline_does_not_spawn(self):
        process = ScriptedProcess()
        factory = ProcessFactory(process)
        with mock.patch(
            "tools.kodi_capture.passive_session.BoundedProcess",
            factory,
        ):
            with self.assertRaises(PassiveSessionTimeout):
                RemotePassiveEvidenceSession(
                    "host",
                    10.0,
                    clock=ManualClock(10.0),
                    nonce_factory=lambda: NONCE,
                )
        self.assertEqual(factory.calls, [])

    def test_spawn_failure_is_typed_and_has_no_action_surface(self):
        with mock.patch(
            "tools.kodi_capture.passive_session.BoundedProcess",
            side_effect=ProcessTransportError("ssh missing"),
        ):
            with self.assertRaisesRegex(
                PassiveSessionTransportError,
                "ssh missing",
            ):
                RemotePassiveEvidenceSession(
                    "host",
                    100.0,
                    clock=ManualClock(),
                    nonce_factory=lambda: NONCE,
                )

        public = {
            name
            for name in dir(RemotePassiveEvidenceSession)
            if not name.startswith("_")
        }
        self.assertEqual(
            public,
            {
                "close",
                "collect",
                "perform_action",
                "ready",
                "seal_action_window",
                "state",
            },
        )
        parameters = inspect.signature(
            RemotePassiveEvidenceSession.__init__
        ).parameters
        for forbidden in ("command", "path", "argv", "run"):
            self.assertNotIn(forbidden, parameters)

        source = (
            Path(__file__).parents[1] / "passive_session.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "shell=True",
            "\"-c\"",
            "import socket",
            "RemoteCaptureLock",
            "CECActivateSource",
            "Player.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


def fragment_size_process(size):
    return ScriptedProcess(fragment_size=size)


LOCAL_PRODUCER_SCRIPT = r"""
import base64
import sys

mode = sys.argv[1]
expected_start = base64.b64decode(sys.argv[2], validate=True)
ready = base64.b64decode(sys.argv[3], validate=True)
expected_finish = base64.b64decode(sys.argv[4], validate=True)
header = base64.b64decode(sys.argv[5], validate=True)
body = base64.b64decode(sys.argv[6], validate=True)

if mode == "small-writes":
    start = bytearray()
    while not start.endswith(b"\n"):
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            sys.stderr.write("START ended early\n")
            raise SystemExit(21)
        start.extend(chunk)
else:
    start = bytearray(sys.stdin.buffer.readline())
if bytes(start) != expected_start:
    sys.stderr.write("START mismatch\n")
    raise SystemExit(22)

if mode == "small-writes":
    for offset in range(0, len(ready), 3):
        sys.stdout.buffer.write(ready[offset:offset + 3])
        sys.stdout.buffer.flush()
else:
    sys.stdout.buffer.write(ready)
    sys.stdout.buffer.flush()

finish = bytearray()
read_size = 2 if mode == "small-writes" else 65536
while True:
    chunk = sys.stdin.buffer.read(read_size)
    if not chunk:
        break
    finish.extend(chunk)
if bytes(finish) != expected_finish:
    sys.stderr.write("FINISH or EOF mismatch\n")
    raise SystemExit(23)

result = header + body
if mode == "truncated-clean":
    sys.stdout.buffer.write(header + body[:7])
    sys.stdout.buffer.flush()
    raise SystemExit(0)
if mode == "stderr-zero":
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()
    sys.stderr.write("local producer stderr\n")
    sys.stderr.flush()
    raise SystemExit(0)
if mode == "nonzero-clean":
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()
    raise SystemExit(19)

if mode == "small-writes":
    for offset in range(0, len(result), 17):
        sys.stdout.buffer.write(result[offset:offset + 17])
        sys.stdout.buffer.flush()
else:
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()
"""


class LocalProducerPopenFactory:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []
        self.processes = []
        self.body = evidence_body()
        self.header = result_header(self.body)
        self.start = (
            "KODI-PASSIVE-EVIDENCE/1 START %s\n" % NONCE
        ).encode("ascii")
        self.finish = (
            "KODI-PASSIVE-EVIDENCE/1 FINISH %s\n" % NONCE
        ).encode("ascii")

    def __call__(self, argv, **keywords):
        self.calls.append((list(argv), keywords))

        def encoded(value):
            return base64.b64encode(value).decode("ascii")

        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                LOCAL_PRODUCER_SCRIPT,
                self.mode,
                encoded(self.start),
                encoded(ready_line()),
                encoded(self.finish),
                encoded(self.header),
                encoded(self.body),
            ],
            **keywords,
        )
        self.processes.append(process)
        return process


class RealPipePassiveSessionTest(unittest.TestCase):
    def test_real_pipes_round_trip_small_and_single_write_output(self):
        # Small producer writes exercise the real selector/pipe path, but the
        # OS may coalesce them. Scripted unit tests own exact byte-boundary
        # permutations; this test owns real subprocess lifecycle integration.
        for mode in ("small-writes", "single-write"):
            factory = LocalProducerPopenFactory(mode)
            open_deadline = time.monotonic() + 5.0
            session = None
            action_calls = []
            try:
                session = RemotePassiveEvidenceSession(
                    "local-test-host",
                    open_deadline,
                    popen_factory=factory,
                    nonce_factory=lambda: NONCE,
                    terminate_timeout=0.2,
                )
                session.perform_action(
                    lambda ready, deadline: action_calls.append(
                        (ready, deadline)
                    ),
                    time.monotonic() + 5.0,
                )
                session.seal_action_window(time.monotonic() + 5.0)
                evidence = session.collect(time.monotonic() + 5.0)
            finally:
                if session is not None:
                    session.close()

            with self.subTest(mode=mode):
                self.assertEqual(session.state, COLLECTED)
                self.assertEqual(len(action_calls), 1)
                self.assertIs(action_calls[0][0], session.ready)
                self.assertEqual(evidence.ready, session.ready)
                self.assertEqual(evidence.cec_trace.raw, CEC_TRACE)
                self.assertEqual(
                    evidence.live_journal.raw,
                    LIVE_JOURNAL,
                )
                self.assertEqual(
                    evidence.final_journal.raw,
                    FINAL_JOURNAL,
                )
                self.assertEqual(len(factory.calls), 1)
                fixed_argv, keywords = factory.calls[0]
                self.assertEqual(
                    fixed_argv[-1],
                    REMOTE_PASSIVE_EVIDENCE_PROGRAM,
                )
                self.assertIs(keywords["shell"], False)
                self.assertEqual(evidence.raw, factory.body)
                self._assert_reaped_process(
                    factory,
                    expected_status=0,
                )

    def test_real_pipe_clean_exit_with_truncated_body_is_transport_failure(self):
        self._assert_real_transport_failure(
            "truncated-clean",
            expected_status=0,
            expected=("closed its output", "status 0"),
            absent=("local producer stderr",),
        )

    def test_real_pipe_status_zero_stderr_is_transport_failure(self):
        self._assert_real_transport_failure(
            "stderr-zero",
            expected_status=0,
            expected=("wrote to stderr", "local producer stderr"),
            absent=("status 19",),
        )

    def test_real_pipe_stderr_free_nonzero_exit_is_transport_failure(self):
        self._assert_real_transport_failure(
            "nonzero-clean",
            expected_status=19,
            expected=("clean status-zero EOF", "status 19"),
            absent=("local producer stderr",),
        )

    def _assert_real_transport_failure(
        self,
        mode,
        *,
        expected_status,
        expected,
        absent,
    ):
        factory = LocalProducerPopenFactory(mode)
        session = None
        try:
            session = RemotePassiveEvidenceSession(
                "local-test-host",
                time.monotonic() + 5.0,
                popen_factory=factory,
                nonce_factory=lambda: NONCE,
                terminate_timeout=0.2,
            )
            session.perform_action(
                lambda _ready, _deadline: None,
                time.monotonic() + 5.0,
            )
            session.seal_action_window(time.monotonic() + 5.0)

            with self.assertRaises(PassiveSessionTransportError) as raised:
                session.collect(time.monotonic() + 5.0)
            message = str(raised.exception)
            for piece in expected:
                self.assertIn(piece, message)
            for piece in absent:
                self.assertNotIn(piece, message)
            self.assertEqual(session.state, POISONED)
        finally:
            if session is not None:
                session.close()
        self._assert_reaped_process(factory, expected_status)

    def _assert_reaped_process(self, factory, expected_status):
        self.assertEqual(len(factory.calls), 1)
        self.assertEqual(len(factory.processes), 1)
        process = factory.processes[0]
        self.assertEqual(process.returncode, expected_status)
        self.assertEqual(process.poll(), expected_status)
        self.assertIsNotNone(process.stdin)
        self.assertIsNotNone(process.stdout)
        self.assertIsNotNone(process.stderr)
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
