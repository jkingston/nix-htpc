from __future__ import annotations

import base64
import inspect
import json
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

from tools.kodi_capture import passive_evidence as protocol
from tools.kodi_capture.passive_evidence import (
    MAX_ACTION_WINDOW_USEC,
    MAX_ENVELOPE_BYTES,
    MAX_READY_BYTES,
    MAX_SESSION_USEC,
    MIN_OBSERVATION_USEC,
    NONCE_HEX_LENGTH,
    PROTOCOL_VERSION,
    CaptureTiming,
    PassiveEvidence,
    PassiveEvidenceContinuityError,
    PassiveEvidencePolicyError,
    PassiveEvidenceProtocolError,
    ReadyEvidence,
    ServiceIdentity,
    decode_passive_evidence,
    decode_ready_line,
    decode_result_header,
)
from tools.kodi_capture.wake_journal import (
    MAX_CURSOR_BYTES,
    MAX_PID,
    MAX_UINT64,
)


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

VERSION = "KODI-PASSIVE-EVIDENCE/1"
NONCE = "a" * NONCE_HEX_LENGTH
OTHER_NONCE = "b" * NONCE_HEX_LENGTH
BOOT_ID = "11111111111111111111111111111111"
OTHER_BOOT_ID = "33333333333333333333333333333333"
INVOCATION_ID = "22222222222222222222222222222222"
OTHER_INVOCATION_ID = "44444444444444444444444444444444"
UNIT_ID = "cec-tv-wake.service"
LOAD_STATE = "loaded"
ACTIVE_STATE = "active"
SUB_STATE = "running"
MAIN_PID = 4242
START_CURSOR = "fixture-start-cursor"
N_RESTARTS = 0
EXEC_START_USEC = 900_000
ACTIVE_ENTER_USEC = 900_100
READY_USEC = 1_000_000
FINISH_USEC = READY_USEC + 1
LIVE_JOURNAL_USEC = READY_USEC + MIN_OBSERVATION_USEC
MONITOR_EXIT_USEC = LIVE_JOURNAL_USEC + 1
FINAL_JOURNAL_USEC = MONITOR_EXIT_USEC + 1
COMPLETE_USEC = FINAL_JOURNAL_USEC + 1


def journal_prefix(raw, record_count):
    lines = raw.splitlines(keepends=True)
    records = lines[:-1]
    if not 1 <= record_count <= len(records):
        raise AssertionError("record_count is outside fixture")
    last = json.loads(records[record_count - 1][1:])
    cursor = last["__CURSOR"].encode("ascii")
    return (
        b"".join(records[:record_count])
        + b"-- cursor: "
        + cursor
        + b"\n"
    )


LIVE_JOURNAL = journal_prefix(FINAL_JOURNAL, 2)


def empty_journal(cursor=START_CURSOR):
    return b"-- cursor: " + cursor.encode("ascii") + b"\n"


def canonical_base64(value):
    return base64.b64encode(value).decode("ascii")


def service_dict(**changes):
    result = {
        "active_enter_usec": ACTIVE_ENTER_USEC,
        "active_state": ACTIVE_STATE,
        "exec_start_usec": EXEC_START_USEC,
        "invocation_id": INVOCATION_ID,
        "load_state": LOAD_STATE,
        "main_pid": MAIN_PID,
        "n_restarts": N_RESTARTS,
        "sub_state": SUB_STATE,
        "unit_id": UNIT_ID,
    }
    result.update(changes)
    return result


def timing_dict(**changes):
    result = {
        "complete": COMPLETE_USEC,
        "final_journal": FINAL_JOURNAL_USEC,
        "finish": FINISH_USEC,
        "live_journal": LIVE_JOURNAL_USEC,
        "monitor_exit": MONITOR_EXIT_USEC,
        "ready": READY_USEC,
    }
    result.update(changes)
    return result


def envelope_dict(**changes):
    result = {
        "boot_ids": {
            "final": BOOT_ID,
            "live": BOOT_ID,
            "start": BOOT_ID,
        },
        "cec_trace_b64": canonical_base64(CEC_TRACE),
        "final_journal_b64": canonical_base64(FINAL_JOURNAL),
        "live_journal_b64": canonical_base64(LIVE_JOURNAL),
        "nonce": NONCE,
        "services": {
            "final": service_dict(),
            "live": service_dict(),
            "start": service_dict(),
        },
        "start_cursor": START_CURSOR,
        "timing_usec": timing_dict(),
        "version": VERSION,
    }
    result.update(changes)
    return result


def encode_envelope(value=None):
    if value is None:
        value = envelope_dict()
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def ready_line(
    *,
    version=VERSION,
    operation="READY",
    nonce=NONCE,
    boot_id=BOOT_ID,
    unit_id=UNIT_ID,
    load_state=LOAD_STATE,
    active_state=ACTIVE_STATE,
    sub_state=SUB_STATE,
    invocation_id=INVOCATION_ID,
    main_pid=MAIN_PID,
    n_restarts=N_RESTARTS,
    exec_start_usec=EXEC_START_USEC,
    active_enter_usec=ACTIVE_ENTER_USEC,
    ready_usec=READY_USEC,
    cursor=START_CURSOR,
):
    fields = (
        version,
        operation,
        nonce,
        boot_id,
        unit_id,
        load_state,
        active_state,
        sub_state,
        invocation_id,
        str(main_pid),
        str(n_restarts),
        str(exec_start_usec),
        str(active_enter_usec),
        str(ready_usec),
        canonical_base64(cursor.encode("ascii")),
    )
    return (" ".join(fields) + "\n").encode("ascii")


def result_header(
    *,
    version=VERSION,
    operation="RESULT",
    nonce=NONCE,
    length=None,
):
    if length is None:
        length = len(encode_envelope())
    return (
        "%s %s %s %s\n"
        % (version, operation, nonce, length)
    ).encode("ascii")


def decoded_ready():
    return decode_ready_line(ready_line())


def decode_envelope(value=None, ready=None):
    if ready is None:
        ready = decoded_ready()
    return decode_passive_evidence(
        encode_envelope(value),
        ready,
    )


def replace_nested(value, path, replacement):
    result = deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return result


def replace_benign_service_message(raw, replacement):
    return raw.replace(
        b"TV power status: on",
        replacement,
        1,
    )


def append_service_record(raw, message):
    lines = raw.splitlines(keepends=True)
    service = json.loads(lines[1][1:])
    service["MESSAGE"] = message
    service["__CURSOR"] = "appended-service-cursor"
    service["__MONOTONIC_TIMESTAMP"] = "240076552804"
    service["__REALTIME_TIMESTAMP"] = "1785412820746650"
    encoded = json.dumps(
        service,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        b"".join(lines[:-1])
        + b"\x1e"
        + encoded
        + b"\n-- cursor: appended-service-cursor\n"
    )


class ReadyLineTest(unittest.TestCase):
    def test_exact_ready_line_is_normalized_and_raw_backed(self):
        raw = ready_line()
        ready = decode_ready_line(raw)
        self.assertEqual(PROTOCOL_VERSION, VERSION)
        self.assertEqual(ready.raw, raw)
        self.assertEqual(ready.nonce, NONCE)
        self.assertEqual(ready.boot_id, BOOT_ID)
        self.assertEqual(ready.start_cursor, START_CURSOR)
        self.assertEqual(ready.ready_usec, READY_USEC)
        self.assertEqual(
            ready.service,
            ServiceIdentity(
                unit_id=UNIT_ID,
                load_state=LOAD_STATE,
                active_state=ACTIVE_STATE,
                sub_state=SUB_STATE,
                invocation_id=INVOCATION_ID,
                main_pid=MAIN_PID,
                n_restarts=N_RESTARTS,
                exec_start_usec=EXEC_START_USEC,
                active_enter_usec=ACTIVE_ENTER_USEC,
            ),
        )
        self.assertEqual(ReadyEvidence(raw), ready)

    def test_ready_requires_bytes_exact_ascii_and_one_complete_line(self):
        valid = ready_line()
        malformed = (
            b"",
            valid[:-1],
            valid + b"\n",
            b"\n" + valid,
            b" " + valid,
            valid[:-1] + b" \n",
            valid.replace(b"\n", b"\r\n"),
            valid.replace(b" READY ", b"\xffREADY ", 1),
            valid.replace(b" READY ", b"\0READY ", 1),
        )
        for raw in malformed:
            with self.subTest(raw=raw[:60]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(raw)
        for value in (None, "ready", bytearray(valid), memoryview(valid)):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    decode_ready_line(value)

    def test_ready_version_operation_and_field_count_are_exact(self):
        valid = ready_line()
        malformed = (
            ready_line(version="KODI-PASSIVE-EVIDENCE/0"),
            ready_line(version=VERSION.lower()),
            ready_line(operation="RESULT"),
            ready_line(operation="ready"),
            valid.replace(b" READY ", b" READY EXTRA ", 1),
            valid.replace(b" READY ", b" ", 1),
        )
        for raw in malformed:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(raw)

    def test_nonce_is_exact_bounded_lowercase_hex(self):
        invalid = (
            "",
            "a" * (NONCE_HEX_LENGTH - 1),
            "a" * (NONCE_HEX_LENGTH + 1),
            "A" * NONCE_HEX_LENGTH,
            "g" * NONCE_HEX_LENGTH,
            ("a" * (NONCE_HEX_LENGTH - 1)) + "-",
        )
        for nonce in invalid:
            with self.subTest(nonce=nonce):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(ready_line(nonce=nonce))

    def test_boot_and_invocation_ids_are_exact_nonzero_lowercase_hex(self):
        invalid = (
            "",
            "0" * 32,
            "1" * 31,
            "1" * 33,
            "A" * 32,
            "g" * 32,
            "11111111-1111-1111-1111-111111111111",
        )
        for field in ("boot_id", "invocation_id"):
            for value in invalid:
                arguments = {field: value}
                with self.subTest(field=field, value=value):
                    with self.assertRaises(PassiveEvidenceProtocolError):
                        decode_ready_line(ready_line(**arguments))

    def test_unit_and_service_states_are_exact(self):
        invalid = (
            {"unit_id": "other.service"},
            {"unit_id": "CEC-TV-WAKE.SERVICE"},
            {"load_state": "not-found"},
            {"load_state": "Loaded"},
            {"active_state": "inactive"},
            {"active_state": "Active"},
            {"sub_state": "failed"},
            {"sub_state": "Running"},
            {"unit_id": ""},
            {"load_state": ""},
            {"active_state": ""},
            {"sub_state": ""},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(ready_line(**changes))

    def test_ready_decimal_fields_are_canonical_and_bounded(self):
        fields = (
            ("main_pid", 1, MAX_PID),
            ("n_restarts", 0, MAX_UINT64),
            ("exec_start_usec", 1, MAX_UINT64),
            ("active_enter_usec", 1, MAX_UINT64),
            ("ready_usec", 0, MAX_UINT64),
        )
        for field, minimum, maximum in fields:
            invalid = (
                "",
                "-1",
                "+1",
                "01",
                "1.0",
                " 1",
                "1 ",
                str(maximum + 1),
            )
            if minimum == 1:
                invalid = invalid + ("0",)
            for value in invalid:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(PassiveEvidenceProtocolError):
                        decode_ready_line(ready_line(**{field: value}))

        maximum = decode_ready_line(
            ready_line(
                main_pid=MAX_PID,
                n_restarts=MAX_UINT64,
                exec_start_usec=MAX_UINT64,
                active_enter_usec=MAX_UINT64,
                ready_usec=MAX_UINT64,
            )
        )
        self.assertEqual(maximum.service.main_pid, MAX_PID)
        self.assertEqual(maximum.ready_usec, MAX_UINT64)

    def test_service_timestamps_must_be_internally_ordered(self):
        malformed = (
            ready_line(
                exec_start_usec=ACTIVE_ENTER_USEC + 1,
                active_enter_usec=ACTIVE_ENTER_USEC,
            ),
            ready_line(
                active_enter_usec=READY_USEC + 1,
                ready_usec=READY_USEC,
            ),
        )
        for raw in malformed:
            with self.subTest(raw=raw):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(raw)

    def test_cursor_base64_is_canonical_and_cursor_is_bounded_printable_ascii(self):
        exact_cursor = "c" * MAX_CURSOR_BYTES
        self.assertEqual(
            decode_ready_line(
                ready_line(cursor=exact_cursor)
            ).start_cursor,
            exact_cursor,
        )
        invalid_cursors = (
            "",
            "c" * (MAX_CURSOR_BYTES + 1),
            "line\nbreak",
            "control\x1f",
            "delete\x7f",
        )
        for cursor in invalid_cursors:
            with self.subTest(cursor=repr(cursor[:20])):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(ready_line(cursor=cursor))

        valid = ready_line(cursor="A")
        canonical = canonical_base64(b"A").encode("ascii")
        malformed = (
            valid.replace(canonical, b"QR=="),
            valid.replace(canonical, b"QQ="),
            valid.replace(canonical, b"QQ==="),
            valid.replace(canonical, b"Q Q=="),
            valid.replace(canonical, b"QQ**"),
            valid.replace(canonical, b"/w=="),
        )
        for raw in malformed:
            with self.subTest(raw=raw[-20:]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_ready_line(raw)

    def test_ready_whole_line_bound_is_enforced_before_decoding(self):
        self.assertLessEqual(len(ready_line()), MAX_READY_BYTES)
        with self.assertRaises(PassiveEvidenceProtocolError):
            decode_ready_line(b"x" * (MAX_READY_BYTES + 1))


class ResultHeaderTest(unittest.TestCase):
    def test_exact_result_header_returns_declared_length(self):
        length = len(encode_envelope())
        self.assertEqual(MAX_ENVELOPE_BYTES, 5 * 1024 * 1024)
        self.assertEqual(
            decode_result_header(
                result_header(length=length),
                NONCE,
            ),
            length,
        )

    def test_result_header_framing_version_operation_and_nonce_are_exact(self):
        valid = result_header()
        malformed = (
            b"",
            valid[:-1],
            valid + b"\n",
            b"\n" + valid,
            valid.replace(b"\n", b"\r\n"),
            result_header(version="KODI-PASSIVE-EVIDENCE/0"),
            result_header(operation="READY"),
            result_header(operation="result"),
            result_header(nonce=OTHER_NONCE),
            valid.replace(b" RESULT ", b" RESULT EXTRA ", 1),
            valid.replace(b" RESULT ", b" ", 1),
            valid.replace(b" RESULT ", b"\xffRESULT ", 1),
        )
        for raw in malformed:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_result_header(raw, NONCE)

    def test_result_header_length_is_canonical_positive_and_bounded(self):
        self.assertEqual(
            decode_result_header(
                result_header(length=MAX_ENVELOPE_BYTES),
                NONCE,
            ),
            MAX_ENVELOPE_BYTES,
        )
        invalid = (
            "",
            "0",
            "-1",
            "+1",
            "01",
            "1.0",
            " 1",
            "1 ",
            str(MAX_ENVELOPE_BYTES + 1),
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_result_header(
                        result_header(length=value),
                        NONCE,
                    )

    def test_result_header_argument_types_and_expected_nonce_are_strict(self):
        valid = result_header()
        for raw in (None, "result", bytearray(valid)):
            with self.subTest(raw=type(raw).__name__):
                with self.assertRaises(TypeError):
                    decode_result_header(raw, NONCE)
        for nonce in (
            None,
            b"a" * NONCE_HEX_LENGTH,
            "",
            "A" * NONCE_HEX_LENGTH,
            "a" * (NONCE_HEX_LENGTH - 1),
        ):
            with self.subTest(nonce=nonce):
                with self.assertRaises(
                    (TypeError, ValueError, PassiveEvidenceProtocolError)
                ):
                    decode_result_header(valid, nonce)


class EnvelopeProtocolTest(unittest.TestCase):
    def test_real_fixtures_decode_to_normalized_immutable_evidence(self):
        raw = encode_envelope()
        ready = decoded_ready()
        evidence = decode_passive_evidence(raw, ready)

        self.assertEqual(evidence.raw, raw)
        self.assertIs(evidence.ready, ready)
        self.assertEqual(evidence.live_boot_id, BOOT_ID)
        self.assertEqual(evidence.final_boot_id, BOOT_ID)
        self.assertEqual(evidence.live_service, ready.service)
        self.assertEqual(evidence.final_service, ready.service)
        self.assertEqual(evidence.cec_trace.raw, CEC_TRACE)
        self.assertEqual(evidence.live_journal.raw, LIVE_JOURNAL)
        self.assertEqual(evidence.final_journal.raw, FINAL_JOURNAL)
        self.assertEqual(
            evidence.timing,
            CaptureTiming(
                ready_usec=READY_USEC,
                finish_usec=FINISH_USEC,
                live_journal_usec=LIVE_JOURNAL_USEC,
                monitor_exit_usec=MONITOR_EXIT_USEC,
                final_journal_usec=FINAL_JOURNAL_USEC,
                complete_usec=COMPLETE_USEC,
            ),
        )
        self.assertEqual(PassiveEvidence(raw, ready), evidence)

    def test_envelope_requires_bytes_bounded_strict_utf8_json_object(self):
        malformed = (
            b"",
            b"\xff",
            b"null",
            b"[]",
            b'"text"',
            b"{} trailing",
            b'{"unterminated":',
            b"x" * (MAX_ENVELOPE_BYTES + 1),
        )
        for raw in malformed:
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_passive_evidence(raw, decoded_ready())
        for raw in (None, "{}", bytearray(b"{}"), memoryview(b"{}")):
            with self.subTest(raw=type(raw).__name__):
                with self.assertRaises(TypeError):
                    decode_passive_evidence(raw, decoded_ready())
        with self.assertRaises(TypeError):
            decode_passive_evidence(encode_envelope(), "not ready")

    def test_envelope_rejects_duplicate_and_noncanonical_json(self):
        canonical = encode_envelope()
        duplicate = canonical.replace(
            b'"nonce":',
            (b'"nonce":"' + NONCE.encode("ascii") + b'","nonce":'),
            1,
        )
        decoded = envelope_dict()
        reversed_top_level = dict(reversed(tuple(decoded.items())))
        noncanonical = (
            json.dumps(decoded, sort_keys=True).encode("ascii"),
            json.dumps(
                reversed_top_level,
                sort_keys=False,
                separators=(",", ":"),
            ).encode("ascii"),
            b"\n" + canonical,
            canonical + b"\n",
        )
        for raw in (duplicate,) + noncanonical:
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_passive_evidence(raw, decoded_ready())

        nonfinite = canonical.replace(
            b'"ready":1000000',
            b'"ready":NaN',
            1,
        )
        with self.assertRaises(PassiveEvidenceProtocolError):
            decode_passive_evidence(nonfinite, decoded_ready())

        hostile_json = (
            b'{"number":' + (b"9" * 5000) + b"}",
            b'{"\\ud800":null}',
            (b"[" * 2000) + b"0" + (b"]" * 2000),
        )
        for raw in hostile_json:
            with self.subTest(raw=raw[:40]):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_passive_evidence(raw, decoded_ready())

    def test_every_object_requires_its_exact_key_set(self):
        samples = (
            ((), "unexpected"),
            (("boot_ids",), "unexpected"),
            (("services",), "unexpected"),
            (("services", "start"), "unexpected"),
            (("services", "live"), "unexpected"),
            (("services", "final"), "unexpected"),
            (("timing_usec",), "unexpected"),
        )
        for path, key in samples:
            value = envelope_dict()
            target = value
            for part in path:
                target = target[part]
            target[key] = 1
            with self.subTest(kind="unknown", path=path):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_envelope(value)

        required = (
            ((), "version"),
            ((), "nonce"),
            ((), "start_cursor"),
            ((), "boot_ids"),
            ((), "services"),
            ((), "timing_usec"),
            ((), "cec_trace_b64"),
            ((), "live_journal_b64"),
            ((), "final_journal_b64"),
            (("boot_ids",), "start"),
            (("boot_ids",), "live"),
            (("boot_ids",), "final"),
            (("services",), "start"),
            (("services",), "live"),
            (("services",), "final"),
            (("services", "start"), "unit_id"),
            (("services", "start"), "load_state"),
            (("services", "start"), "active_state"),
            (("services", "start"), "sub_state"),
            (("services", "start"), "invocation_id"),
            (("services", "start"), "main_pid"),
            (("services", "start"), "n_restarts"),
            (("services", "start"), "exec_start_usec"),
            (("services", "start"), "active_enter_usec"),
            (("timing_usec",), "ready"),
            (("timing_usec",), "finish"),
            (("timing_usec",), "live_journal"),
            (("timing_usec",), "monitor_exit"),
            (("timing_usec",), "final_journal"),
            (("timing_usec",), "complete"),
        )
        for path, key in required:
            value = envelope_dict()
            target = value
            for part in path:
                target = target[part]
            del target[key]
            with self.subTest(kind="missing", path=path, key=key):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_envelope(value)

        service_fields = tuple(service_dict())
        for fence in ("start", "live", "final"):
            for key in service_fields:
                value = envelope_dict()
                del value["services"][fence][key]
                with self.subTest(
                    kind="missing-service-field",
                    fence=fence,
                    key=key,
                ):
                    with self.assertRaises(PassiveEvidenceProtocolError):
                        decode_envelope(value)

        for fence in ("start", "live", "final"):
            value = envelope_dict()
            del value["boot_ids"][fence]
            with self.subTest(kind="missing-boot-fence", fence=fence):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_envelope(value)

    def test_envelope_scalar_shapes_are_strict(self):
        cases = (
            (("version",), 1),
            (("nonce",), None),
            (("start_cursor",), []),
            (("boot_ids", "start"), {}),
            (("services", "start", "unit_id"), None),
            (("services", "start", "load_state"), []),
            (("services", "start", "active_state"), {}),
            (("services", "start", "sub_state"), 1),
            (("services", "start", "invocation_id"), []),
            (("services", "start", "main_pid"), True),
            (("services", "start", "n_restarts"), "0"),
            (("services", "start", "exec_start_usec"), None),
            (("services", "start", "active_enter_usec"), 1.0),
            (("timing_usec", "ready"), True),
            (("timing_usec", "finish"), "1"),
            (("timing_usec", "live_journal"), []),
            (("timing_usec", "monitor_exit"), {}),
            (("timing_usec", "final_journal"), None),
            (("timing_usec", "complete"), 1.0),
            (("cec_trace_b64",), []),
            (("live_journal_b64",), None),
            (("final_journal_b64",), 1),
        )
        for path, replacement in cases:
            with self.subTest(path=path, replacement=replacement):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_envelope(
                        replace_nested(
                            envelope_dict(),
                            path,
                            replacement,
                        )
                    )

    def test_envelope_version_nonce_cursor_and_ids_are_strict(self):
        cases = (
            (("version",), "KODI-PASSIVE-EVIDENCE/0"),
            (("nonce",), OTHER_NONCE),
            (("start_cursor",), "other-cursor"),
            (("boot_ids", "start"), OTHER_BOOT_ID),
            (
                ("services", "start", "invocation_id"),
                OTHER_INVOCATION_ID,
            ),
        )
        for path, replacement in cases:
            with self.subTest(path=path):
                with self.assertRaises(
                    (
                        PassiveEvidenceProtocolError,
                        PassiveEvidenceContinuityError,
                    )
                ):
                    decode_envelope(
                        replace_nested(
                            envelope_dict(),
                            path,
                            replacement,
                        )
                    )

    def test_base64_fields_require_the_unique_canonical_spelling(self):
        for field in (
            "cec_trace_b64",
            "live_journal_b64",
            "final_journal_b64",
        ):
            value = envelope_dict()
            encoded = value[field]
            malformed = (
                encoded[:-1],
                encoded + "=",
                encoded[:4] + " " + encoded[4:],
                "*" + encoded[1:],
            )
            for replacement in malformed:
                candidate = dict(value)
                candidate[field] = replacement
                with self.subTest(field=field, replacement=replacement[:20]):
                    with self.assertRaises(PassiveEvidenceProtocolError):
                        decode_envelope(candidate)

        noncanonical = envelope_dict()
        noncanonical["cec_trace_b64"] = "QR=="
        with self.assertRaises(PassiveEvidenceProtocolError):
            decode_envelope(noncanonical)

    def test_decoded_evidence_bounds_are_enforced(self):
        cases = (
            ("cec_trace_b64", b"x" * (1024 * 1024 + 1)),
            ("live_journal_b64", b"x" * (1024 * 1024 + 1)),
            ("final_journal_b64", b"x" * (1024 * 1024 + 1)),
        )
        for field, decoded in cases:
            value = envelope_dict()
            value[field] = canonical_base64(decoded)
            with self.subTest(field=field):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_envelope(value)


class ContinuityAndTimingTest(unittest.TestCase):
    def test_every_boot_fence_must_match_ready(self):
        for fence in ("start", "live", "final"):
            value = envelope_dict()
            value["boot_ids"][fence] = OTHER_BOOT_ID
            with self.subTest(fence=fence):
                with self.assertRaises(PassiveEvidenceContinuityError):
                    decode_envelope(value)

    def test_every_service_field_at_every_fence_must_match_ready(self):
        replacements = {
            "unit_id": "other.service",
            "load_state": "not-found",
            "active_state": "inactive",
            "sub_state": "failed",
            "invocation_id": OTHER_INVOCATION_ID,
            "main_pid": MAIN_PID + 1,
            "n_restarts": N_RESTARTS + 1,
            "exec_start_usec": EXEC_START_USEC + 1,
            "active_enter_usec": ACTIVE_ENTER_USEC + 1,
        }
        for fence in ("start", "live", "final"):
            for field, replacement in replacements.items():
                value = envelope_dict()
                value["services"][fence][field] = replacement
                with self.subTest(fence=fence, field=field):
                    with self.assertRaises(
                        PassiveEvidenceContinuityError
                    ):
                        decode_envelope(value)

    def test_ready_timestamp_must_match_the_ready_frame(self):
        value = envelope_dict()
        value["timing_usec"]["ready"] = READY_USEC + 1
        with self.assertRaises(PassiveEvidenceContinuityError):
            decode_envelope(value)

    def test_each_timing_order_edge_is_enforced(self):
        cases = (
            {"finish": READY_USEC - 1},
            {"live_journal": FINISH_USEC - 1},
            {"monitor_exit": LIVE_JOURNAL_USEC - 1},
            {"monitor_exit": LIVE_JOURNAL_USEC},
            {"final_journal": MONITOR_EXIT_USEC - 1},
            {"complete": FINAL_JOURNAL_USEC - 1},
        )
        for changes in cases:
            value = envelope_dict()
            value["timing_usec"].update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(PassiveEvidenceContinuityError):
                    decode_envelope(value)

    def test_minimum_observation_action_and_session_bounds_are_exact(self):
        exact_minimum = envelope_dict()
        exact_minimum["timing_usec"].update(
            {
                "finish": READY_USEC,
                "live_journal": READY_USEC + MIN_OBSERVATION_USEC,
                "monitor_exit": READY_USEC + MIN_OBSERVATION_USEC + 1,
                "final_journal": READY_USEC + MIN_OBSERVATION_USEC + 2,
                "complete": READY_USEC + MIN_OBSERVATION_USEC + 3,
            }
        )
        decode_envelope(exact_minimum)

        below_minimum = deepcopy(exact_minimum)
        below_minimum["timing_usec"].update(
            {
                "live_journal": (
                    READY_USEC + MIN_OBSERVATION_USEC - 1
                ),
                "monitor_exit": (
                    READY_USEC + MIN_OBSERVATION_USEC
                ),
                "final_journal": (
                    READY_USEC + MIN_OBSERVATION_USEC + 1
                ),
                "complete": (
                    READY_USEC + MIN_OBSERVATION_USEC + 2
                ),
            }
        )
        with self.assertRaises(PassiveEvidenceContinuityError):
            decode_envelope(below_minimum)

        exact_action = envelope_dict()
        exact_action["timing_usec"].update(
            {
                "finish": READY_USEC + MAX_ACTION_WINDOW_USEC,
                "live_journal": READY_USEC + MIN_OBSERVATION_USEC,
                "monitor_exit": READY_USEC + MIN_OBSERVATION_USEC + 1,
                "final_journal": READY_USEC + MIN_OBSERVATION_USEC + 2,
                "complete": READY_USEC + MIN_OBSERVATION_USEC + 3,
            }
        )
        decode_envelope(exact_action)

        over_action = deepcopy(exact_action)
        over_action["timing_usec"].update(
            {
                "finish": READY_USEC + MAX_ACTION_WINDOW_USEC + 1,
                "live_journal": READY_USEC + MIN_OBSERVATION_USEC,
                "monitor_exit": READY_USEC + MIN_OBSERVATION_USEC + 1,
                "final_journal": READY_USEC + MIN_OBSERVATION_USEC + 2,
                "complete": READY_USEC + MIN_OBSERVATION_USEC + 3,
            }
        )
        with self.assertRaises(PassiveEvidenceContinuityError):
            decode_envelope(over_action)

        exact_session = envelope_dict()
        exact_session["timing_usec"].update(
            {
                "complete": READY_USEC + MAX_SESSION_USEC,
            }
        )
        decode_envelope(exact_session)

        over_session = deepcopy(exact_session)
        over_session["timing_usec"]["complete"] += 1
        with self.assertRaises(PassiveEvidenceContinuityError):
            decode_envelope(over_session)

    def test_timestamps_are_nonnegative_bounded_integers(self):
        for field in timing_dict():
            for replacement in (-1, MAX_UINT64 + 1, True, 1.5):
                value = envelope_dict()
                value["timing_usec"][field] = replacement
                with self.subTest(field=field, replacement=replacement):
                    with self.assertRaises(PassiveEvidenceProtocolError):
                        decode_envelope(value)

    def test_live_journal_must_be_an_exact_record_prefix_of_final(self):
        service_only = journal_prefix(FINAL_JOURNAL, 2)
        service_line = service_only.splitlines(keepends=True)[1]
        service_cursor = json.loads(service_line[1:])["__CURSOR"]
        nonprefix_live = (
            service_line
            + b"-- cursor: "
            + service_cursor.encode("ascii")
            + b"\n"
        )
        value = envelope_dict()
        value["live_journal_b64"] = canonical_base64(nonprefix_live)
        with self.assertRaises(PassiveEvidenceContinuityError):
            decode_envelope(value)

    def test_empty_and_complete_cursor_boundaries_are_explicit(self):
        empty_then_later = envelope_dict()
        empty_then_later["live_journal_b64"] = canonical_base64(
            empty_journal()
        )
        evidence = decode_envelope(empty_then_later)
        self.assertEqual(
            evidence.live_journal.terminal_cursor,
            START_CURSOR,
        )
        self.assertGreater(len(evidence.final_journal.records), 0)

        no_later_records = envelope_dict()
        no_later_records["live_journal_b64"] = canonical_base64(
            empty_journal()
        )
        no_later_records["final_journal_b64"] = canonical_base64(
            empty_journal()
        )
        evidence = decode_envelope(no_later_records)
        self.assertEqual(evidence.live_journal.records, ())
        self.assertEqual(evidence.final_journal.records, ())
        self.assertEqual(
            evidence.live_journal.terminal_cursor,
            evidence.final_journal.terminal_cursor,
        )

        same_complete_slice = envelope_dict()
        same_complete_slice["live_journal_b64"] = canonical_base64(
            FINAL_JOURNAL
        )
        evidence = decode_envelope(same_complete_slice)
        self.assertEqual(
            evidence.live_journal.terminal_cursor,
            evidence.final_journal.terminal_cursor,
        )

    def test_live_terminal_cursor_must_identify_its_prefix_boundary(self):
        lines = LIVE_JOURNAL.splitlines(keepends=True)
        first_cursor = json.loads(lines[0][1:])["__CURSOR"]
        wrong_boundary = (
            b"".join(lines[:-1])
            + b"-- cursor: "
            + first_cursor.encode("ascii")
            + b"\n"
        )
        value = envelope_dict()
        value["live_journal_b64"] = canonical_base64(wrong_boundary)
        with self.assertRaises(PassiveEvidenceProtocolError):
            decode_envelope(value)

        shorter_final = journal_prefix(FINAL_JOURNAL, 1)
        value = envelope_dict()
        value["final_journal_b64"] = canonical_base64(shorter_final)
        with self.assertRaises(PassiveEvidenceContinuityError):
            decode_envelope(value)


class NestedEvidencePolicyTest(unittest.TestCase):
    def test_cec_protocol_and_policy_failures_are_typed(self):
        malformed = envelope_dict()
        malformed["cec_trace_b64"] = canonical_base64(CEC_TRACE[:-1])
        with self.assertRaises(PassiveEvidenceProtocolError):
            decode_envelope(malformed)

        forbidden_trace = CEC_TRACE.replace(
            b"GIVE_DEVICE_POWER_STATUS (0x8f)",
            b"ACTIVE_SOURCE (0x82)",
            1,
        ).replace(
            b"0x10 0x8f",
            b"0x10 0x82",
            1,
        )
        forbidden = envelope_dict()
        forbidden["cec_trace_b64"] = canonical_base64(forbidden_trace)
        with self.assertRaises(PassiveEvidencePolicyError):
            decode_envelope(forbidden)

    def test_each_journal_protocol_failure_is_typed(self):
        for field, raw in (
            ("live_journal_b64", LIVE_JOURNAL[:-1]),
            ("final_journal_b64", FINAL_JOURNAL[:-1]),
        ):
            value = envelope_dict()
            value[field] = canonical_base64(raw)
            with self.subTest(field=field):
                with self.assertRaises(PassiveEvidenceProtocolError):
                    decode_envelope(value)

    def test_each_journal_policy_failure_is_typed(self):
        activation = b"TV wake detected; asking Kodi to become active"
        bad_live = replace_benign_service_message(
            LIVE_JOURNAL,
            activation,
        )
        bad_live_final = replace_benign_service_message(
            FINAL_JOURNAL,
            activation,
        )
        bad_final = append_service_record(
            FINAL_JOURNAL,
            activation.decode("ascii"),
        )
        for field, live_raw, final_raw in (
            (
                "live_journal_b64",
                bad_live,
                bad_live_final,
            ),
            (
                "final_journal_b64",
                LIVE_JOURNAL,
                bad_final,
            ),
        ):
            value = envelope_dict()
            value["live_journal_b64"] = canonical_base64(live_raw)
            value["final_journal_b64"] = canonical_base64(final_raw)
            with self.subTest(field=field):
                with self.assertRaises(PassiveEvidencePolicyError):
                    decode_envelope(value)


class ModelAndPublicApiTest(unittest.TestCase):
    def test_raw_backed_models_cannot_accept_normalized_values(self):
        ready_signature = inspect.signature(ReadyEvidence)
        passive_signature = inspect.signature(PassiveEvidence)
        self.assertEqual(tuple(ready_signature.parameters), ("raw",))
        self.assertEqual(
            tuple(passive_signature.parameters),
            ("raw", "ready"),
        )

        ready = decoded_ready()
        evidence = decode_envelope(ready=ready)
        with self.assertRaises(TypeError):
            ReadyEvidence(
                ready.raw,
                OTHER_NONCE,
                OTHER_BOOT_ID,
                ServiceIdentity(
                    unit_id="other.service",
                    load_state="not-found",
                    active_state="inactive",
                    sub_state="failed",
                    invocation_id=OTHER_INVOCATION_ID,
                    main_pid=MAIN_PID + 1,
                    n_restarts=1,
                    exec_start_usec=EXEC_START_USEC + 1,
                    active_enter_usec=ACTIVE_ENTER_USEC + 1,
                ),
                "other-cursor",
                READY_USEC + 1,
            )
        with self.assertRaises(TypeError):
            PassiveEvidence(
                evidence.raw,
                evidence.ready,
                CaptureTiming(
                    READY_USEC,
                    FINISH_USEC,
                    LIVE_JOURNAL_USEC,
                    MONITOR_EXIT_USEC,
                    FINAL_JOURNAL_USEC,
                    COMPLETE_USEC,
                ),
                OTHER_BOOT_ID,
                OTHER_BOOT_ID,
                evidence.live_service,
                evidence.final_service,
                evidence.cec_trace,
                evidence.live_journal,
                evidence.final_journal,
            )

        class ForgedReady(ReadyEvidence):
            pass

        forged_subclass = object.__new__(ForgedReady)
        for field, value in vars(ready).items():
            object.__setattr__(forged_subclass, field, value)
        with self.assertRaises(TypeError):
            decode_passive_evidence(
                encode_envelope(),
                forged_subclass,
            )

        forged_exact = object.__new__(ReadyEvidence)
        for field, value in vars(ready).items():
            object.__setattr__(forged_exact, field, value)
        object.__setattr__(forged_exact, "nonce", OTHER_NONCE)
        with self.assertRaises(TypeError):
            decode_passive_evidence(
                encode_envelope(),
                forged_exact,
            )

    def test_all_models_are_frozen(self):
        ready = decoded_ready()
        evidence = decode_envelope(ready=ready)
        targets = (
            (ready, "nonce", OTHER_NONCE),
            (ready.service, "main_pid", MAIN_PID + 1),
            (evidence, "live_boot_id", OTHER_BOOT_ID),
            (evidence.timing, "complete_usec", COMPLETE_USEC + 1),
            (evidence.live_service, "n_restarts", 1),
            (evidence.final_service, "invocation_id", OTHER_INVOCATION_ID),
            (evidence.cec_trace, "raw", b"changed"),
            (evidence.live_journal, "raw", b"changed"),
            (evidence.final_journal, "raw", b"changed"),
        )
        for target, attribute, replacement in targets:
            with self.subTest(
                target=type(target).__name__,
                attribute=attribute,
            ):
                with self.assertRaises(FrozenInstanceError):
                    setattr(target, attribute, replacement)

    def test_public_functions_are_decoders_not_encoders_or_io(self):
        self.assertEqual(
            set(protocol.__all__),
            {
                "PROTOCOL_VERSION",
                "MAX_READY_BYTES",
                "MAX_ENVELOPE_BYTES",
                "NONCE_HEX_LENGTH",
                "MIN_OBSERVATION_USEC",
                "MAX_ACTION_WINDOW_USEC",
                "MAX_SESSION_USEC",
                "PassiveEvidenceError",
                "PassiveEvidenceProtocolError",
                "PassiveEvidenceContinuityError",
                "PassiveEvidencePolicyError",
                "ServiceIdentity",
                "ReadyEvidence",
                "CaptureTiming",
                "PassiveEvidence",
                "decode_ready_line",
                "decode_result_header",
                "decode_passive_evidence",
            },
        )
        self.assertNotIn(
            "parse_cec_monitor_content",
            protocol.__all__,
        )
        self.assertNotIn(
            "parse_safe_wake_journal",
            protocol.__all__,
        )
        public_functions = {
            name
            for name, value in vars(protocol).items()
            if (
                not name.startswith("_")
                and inspect.isfunction(value)
                and value.__module__ == protocol.__name__
            )
        }
        self.assertEqual(
            public_functions,
            {
                "decode_passive_evidence",
                "decode_ready_line",
                "decode_result_header",
            },
        )
        forbidden_fragments = (
            "encode",
            "connect",
            "open",
            "write",
            "send",
            "receive",
            "spawn",
        )
        for name in public_functions:
            self.assertFalse(
                any(fragment in name for fragment in forbidden_fragments),
                name,
            )

    def test_error_hierarchy_is_stable(self):
        for error in (
            PassiveEvidenceProtocolError,
            PassiveEvidenceContinuityError,
            PassiveEvidencePolicyError,
        ):
            self.assertTrue(
                issubclass(error, protocol.PassiveEvidenceError)
            )


if __name__ == "__main__":
    unittest.main()
