from __future__ import annotations

import base64
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from tools.kodi_capture.wake_journal import (
    MAX_CONTENT_BYTES,
    MAX_CURSOR_BYTES,
    MAX_FIELD_VALUES,
    MAX_FIELDS,
    MAX_KEY_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_RECORD_BYTES,
    MAX_RECORDS,
    MAX_UINT64,
    MAX_VALUE_BYTES,
    SERVICE_UNIT,
    JournalExpectation,
    WakeJournalContent,
    WakeJournalPolicyError,
    WakeJournalProtocolError,
    WakeJournalRecord,
    parse_safe_wake_journal,
    decode_untrusted_wake_journal_content,
    require_safe_wake_journal,
)


BOOT_ID = "11111111111111111111111111111111"
INVOCATION_ID = "22222222222222222222222222222222"
OTHER_ID = "33333333333333333333333333333333"
START_CURSOR = "fixture-start-cursor"
MAIN_PID = 4242
FIXTURE_RECORDS = json.loads(
    (
        Path(__file__).parent
        / "fixtures"
        / "wake_journal"
        / "benign-records.json"
    ).read_text(encoding="utf-8")
)
WIRE_FIXTURE = base64.b64decode(
    (
        Path(__file__).parent
        / "fixtures"
        / "wake_journal"
        / "benign-global.json-seq.b64"
    ).read_bytes().strip(),
    validate=True,
)


class WakeJournalContentTest(unittest.TestCase):
    def test_exact_empty_slice_is_safe_and_preserved(self):
        raw = wire((), terminal_cursor=START_CURSOR)
        content = parse_safe(raw)
        self.assertEqual(content.raw, raw)
        self.assertEqual(content.expectation, expectation())
        self.assertEqual(content.terminal_cursor, START_CURSOR)
        self.assertEqual(content.records, ())

    def test_scrubbed_live_shaped_benign_fixture_is_safe(self):
        raw = WIRE_FIXTURE
        content = parse_safe(raw)
        self.assertEqual(
            tuple(
                record.messages[0]
                for record in content.records
                if record.source == "service"
            ),
            ("TV power status: on",),
        )
        self.assertEqual(
            tuple(record.source for record in content.records),
            (
                "unrelated",
                "service",
                "unrelated",
                "unrelated",
                "unrelated",
            ),
        )
        self.assertEqual(
            tuple(record.monotonic_usec for record in content.records),
            (
                240039532090,
                240046318261,
                240076354946,
                240076527489,
                240076552803,
            ),
        )
        self.assertEqual(
            tuple(record.realtime_usec for record in content.records),
            (
                1785412783725935,
                1785412790512107,
                1785412820548792,
                1785412820721335,
                1785412820746649,
            ),
        )
        self.assertEqual(
            content.terminal_cursor,
            content.records[-1].cursor,
        )

    def test_content_construction_reparses_raw_and_models_are_frozen(self):
        raw = wire(FIXTURE_RECORDS[:1])
        expected = expectation()
        content = WakeJournalContent(raw, expected)
        self.assertEqual(content, parse_content(raw))
        with self.assertRaises(WakeJournalProtocolError):
            WakeJournalContent(raw[:-1], expected)
        with self.assertRaises(FrozenInstanceError):
            content.terminal_cursor = "changed"
        with self.assertRaises(FrozenInstanceError):
            content.records[0].messages = ("changed",)

    def test_benign_messages_may_repeat(self):
        messages = tuple(
            record["MESSAGE"]
            for record in FIXTURE_RECORDS
            if record.get("_SYSTEMD_UNIT") == SERVICE_UNIT
        )
        records = []
        for index in range(6):
            records.append(
                service_record(
                    cursor="repeat-%d" % index,
                    monotonic=str(100 + index),
                    message=messages[index % len(messages)],
                )
            )
        content = parse_safe(wire(records))
        self.assertEqual(len(content.records), 6)

    def test_activation_unknown_and_near_match_messages_fail_policy(self):
        messages = (
            "TV wake detected; asking Kodi to become active",
            "Kodi CEC source activation sent",
            "Kodi is unavailable; CEC source activation remains armed",
            "unrecognized service output",
            "TV power status: On",
            "TV power status: on ",
        )
        for index, message in enumerate(messages):
            raw = wire(
                (
                    service_record(
                        cursor="message-%d" % index,
                        message=message,
                    ),
                )
            )
            with self.subTest(message=message):
                content = parse_content(raw)
                with self.assertRaises(WakeJournalPolicyError) as caught:
                    require_safe_wake_journal(content)
                self.assertIs(caught.exception.content, content)

    def test_service_identity_drift_fails_policy(self):
        cases = (
            {"_BOOT_ID": OTHER_ID},
            {"_SYSTEMD_INVOCATION_ID": OTHER_ID},
            {"_PID": "4243"},
            {"_UID": "1000"},
            {"PRIORITY": "5"},
        )
        for index, changes in enumerate(cases):
            record = service_record(cursor="identity-%d" % index)
            record.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(WakeJournalPolicyError):
                    parse_safe(wire((record,)))

    def test_every_manager_or_coredump_target_shape_fails_policy(self):
        for index, target_field in enumerate(
            ("UNIT", "OBJECT_SYSTEMD_UNIT", "COREDUMP_UNIT")
        ):
            record = manager_record(
                cursor="manager-%d" % index,
                target_field=target_field,
            )
            with self.subTest(target_field=target_field):
                content = parse_content(wire((record,)))
                self.assertEqual(content.records[0].source, "manager")
                with self.assertRaises(WakeJournalPolicyError):
                    require_safe_wake_journal(content)

    def test_policy_requires_a_parsed_content_model(self):
        with self.assertRaises(TypeError):
            require_safe_wake_journal("not content")

    def test_whole_content_framing_fails_closed(self):
        valid = wire(FIXTURE_RECORDS[:1])
        malformed = (
            b"",
            valid[:-1],
            valid + b"\n",
            b"\n" + valid,
            valid.replace(b"\n-- cursor:", b"\r\n-- cursor:"),
            valid.replace(b"\x1e", b"", 1),
            valid.replace(b"\x1e", b"x", 1),
            valid.replace(
                b"-- cursor:",
                b"-- cursor: early\n-- cursor:",
            ),
            valid + b"trailing",
        )
        for name, raw in zip(
            (
                "empty",
                "incomplete-footer",
                "trailing-newline",
                "leading-newline",
                "crlf",
                "missing-rs",
                "wrong-rs",
                "early-footer",
                "trailing-bytes",
            ),
            malformed,
        ):
            with self.subTest(name=name, raw=raw[:40]):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(raw)
        with self.assertRaises(TypeError):
            parse_content("not bytes")

    def test_json_framing_and_duplicate_keys_fail_closed(self):
        valid = wire(FIXTURE_RECORDS[:1])
        duplicate = valid.replace(
            b'{"MESSAGE":',
            b'{"MESSAGE":"duplicate","MESSAGE":',
            1,
        )
        nonfinite = valid.replace(
            b'"__MONOTONIC_TIMESTAMP":"99"',
            b'"__MONOTONIC_TIMESTAMP":NaN',
            1,
        )
        scalar = (
            b"\x1e[]\n-- cursor: fixture-cursor-1\n"
        )
        invalid_utf8 = valid.replace(
            b'"Unrelated',
            b'"\xffUnrelated',
            1,
        )
        for name, raw in (
            ("duplicate-key", duplicate),
            ("non-finite-number", nonfinite),
            ("non-object", scalar),
            ("invalid-utf8", invalid_utf8),
        ):
            with self.subTest(name=name, raw=raw[:60]):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(raw)

    def test_lone_json_surrogates_are_typed_failures(self):
        valid = wire(FIXTURE_RECORDS[:1])
        for escaped in (b"\\ud800", b"\\udfff"):
            surrogate = valid.replace(
                b'"Unrelated system activity"',
                b'"' + escaped + b'"',
                1,
            )
            with self.subTest(escaped=escaped):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(surrogate)

        for surrogate in ("\ud800", "\udfff"):
            with self.subTest(model=repr(surrogate)):
                with self.assertRaises(ValueError):
                    WakeJournalRecord(
                        source="unrelated",
                        cursor="surrogate-model",
                        boot_id=BOOT_ID,
                        realtime_usec=0,
                        monotonic_usec=0,
                        messages=(surrogate,),
                        process_invocation_id=None,
                        subject_invocation_ids=(),
                        pid=None,
                        uid=None,
                        priority=None,
                    )

    def test_valid_json_whitespace_is_not_python_encoder_policy(self):
        valid = wire(FIXTURE_RECORDS[:1])
        spaced = valid.replace(b'{"MESSAGE":', b'{ "MESSAGE":', 1)
        content = parse_safe(spaced)
        self.assertEqual(content.records[0].source, "unrelated")

    def test_deep_json_is_a_typed_protocol_failure(self):
        nested = ("[" * 1200 + "0" + "]" * 1200).encode("ascii")
        raw = (
            b"\x1e"
            + nested
            + b"\n-- cursor: nested-terminal\n"
        )
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(raw)

    def test_required_fields_and_string_shapes_are_strict(self):
        required = (
            "MESSAGE",
            "__CURSOR",
            "__MONOTONIC_TIMESTAMP",
            "__REALTIME_TIMESTAMP",
            "_BOOT_ID",
            "_SYSTEMD_INVOCATION_ID",
            "_PID",
            "_UID",
            "PRIORITY",
        )
        for field in required:
            record = service_record(cursor="missing-" + field)
            del record[field]
            with self.subTest(field=field):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(
                        wire(
                            (record,),
                            terminal_cursor=record.get(
                                "__CURSOR",
                                "missing-terminal",
                            ),
                        )
                    )

        for index, value in enumerate((1, {"nested": "value"})):
            record = service_record(cursor="shape-%d" % index)
            record["EXTRA"] = value
            with self.subTest(value=value):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(wire((record,)))

        for index, value in enumerate((None, [], [0, 127, 255])):
            record = service_record(cursor="binary-%d" % index)
            record["EXTRA"] = value
            parse_safe(wire((record,)))

    def test_unrelated_global_records_are_preserved_and_ignored(self):
        record = manager_record(cursor="unrelated")
        record["UNIT"] = "other.service"
        record["INVOCATION_ID"] = OTHER_ID
        content = parse_safe(wire((record,)))
        self.assertEqual(content.records[0].source, "unrelated")

        without_message = dict(record)
        without_message["__CURSOR"] = "unrelated-without-message"
        del without_message["MESSAGE"]
        content = parse_safe(wire((without_message,)))
        self.assertEqual(content.records[0].messages, ())

        for index, message in enumerate(
            (
                "x" * MAX_VALUE_BYTES,
                None,
                [0, 127, 255],
            )
        ):
            tolerant = dict(record)
            tolerant["__CURSOR"] = "unrelated-shape-%d" % index
            tolerant["MESSAGE"] = message
            parse_safe(wire((tolerant,)))

    def test_repeated_unrelated_field_values_follow_journal_json(self):
        record = manager_record(cursor="repeated-values")
        record["UNIT"] = "other.service"
        record["INVOCATION_ID"] = OTHER_ID
        record["MESSAGE"] = [
            "first",
            [0, 127, 255],
            None,
            "second",
        ]
        content = parse_safe(wire((record,)))
        self.assertEqual(
            content.records[0].messages,
            ("first", "second"),
        )

        suspicious = dict(record)
        suspicious["__CURSOR"] = "repeated-wake-message"
        suspicious["MESSAGE"] = [
            "ordinary",
            "Kodi CEC source activation sent",
        ]
        with self.assertRaises(WakeJournalPolicyError):
            parse_safe(wire((suspicious,)))

        service = service_record(cursor="repeated-service-message")
        service["MESSAGE"] = [
            "TV power status: on",
            "TV power status: on",
        ]
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire((service,)))

        exact = dict(record)
        exact["__CURSOR"] = "repeated-value-bound"
        exact["MESSAGE"] = ["x" * 64] * MAX_FIELD_VALUES
        parse_safe(wire((exact,)))

        too_many = dict(exact)
        too_many["__CURSOR"] = "too-many-repeated-values"
        too_many["MESSAGE"] = ["x"] * (MAX_FIELD_VALUES + 1)
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire((too_many,)))

        too_large = dict(exact)
        too_large["__CURSOR"] = "repeated-values-too-large"
        too_large["MESSAGE"] = (
            ["x" * 64] * (MAX_FIELD_VALUES - 1)
            + ["x" * 65]
        )
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire((too_large,)))

        repeated_other_units = dict(record)
        repeated_other_units["__CURSOR"] = "repeated-other-units"
        repeated_other_units["UNIT"] = [
            "first.service",
            "second.service",
        ]
        parse_safe(wire((repeated_other_units,)))

        repeated_target = dict(repeated_other_units)
        repeated_target["__CURSOR"] = "repeated-target-unit"
        repeated_target["UNIT"] = [
            "other.service",
            SERVICE_UNIT,
        ]
        with self.assertRaises(WakeJournalPolicyError):
            parse_safe(wire((repeated_target,)))

        for index, unit_value in enumerate((None, [0, 127, 255])):
            nontext_target = dict(record)
            nontext_target["__CURSOR"] = (
                "nontext-target-%d" % index
            )
            nontext_target["UNIT"] = unit_value
            content = parse_safe(wire((nontext_target,)))
            self.assertEqual(content.records[0].source, "unrelated")

        repeated_subject = dict(record)
        repeated_subject["__CURSOR"] = "repeated-subject-invocation"
        repeated_subject["INVOCATION_ID"] = [
            OTHER_ID,
            INVOCATION_ID,
        ]
        with self.assertRaises(WakeJournalPolicyError):
            parse_safe(wire((repeated_subject,)))

    def test_unrelated_records_may_surround_service_records(self):
        before = manager_record(cursor="unrelated-before")
        before["UNIT"] = "before.service"
        before["INVOCATION_ID"] = OTHER_ID
        between = manager_record(cursor="unrelated-between")
        between["UNIT"] = "between.service"
        between["INVOCATION_ID"] = OTHER_ID
        between["__MONOTONIC_TIMESTAMP"] = "102"
        after = manager_record(cursor="unrelated-after")
        after["UNIT"] = "after.service"
        after["INVOCATION_ID"] = OTHER_ID
        after["__MONOTONIC_TIMESTAMP"] = "104"
        content = parse_safe(
            wire(
                (
                    before,
                    service_record(
                        cursor="service-first",
                        monotonic="101",
                    ),
                    between,
                    service_record(
                        cursor="service-second",
                        monotonic="103",
                    ),
                    after,
                )
            )
        )
        self.assertEqual(
            tuple(record.source for record in content.records),
            (
                "unrelated",
                "service",
                "unrelated",
                "service",
                "unrelated",
            ),
        )
        self.assertEqual(content.terminal_cursor, "unrelated-after")

    def test_unrelated_record_from_another_boot_fails_policy(self):
        record = manager_record(cursor="unrelated-other-boot")
        record["UNIT"] = "other.service"
        record["INVOCATION_ID"] = OTHER_ID
        record["_BOOT_ID"] = OTHER_ID
        with self.assertRaises(WakeJournalPolicyError):
            parse_safe(wire((record,)))

    def test_wake_message_without_service_identity_fails_policy(self):
        record = manager_record(cursor="disguised-wake")
        record["UNIT"] = "other.service"
        record["INVOCATION_ID"] = OTHER_ID
        record["MESSAGE"] = "Kodi CEC source activation sent"
        with self.assertRaises(WakeJournalPolicyError):
            parse_safe(wire((record,)))

    def test_wake_identity_without_unit_metadata_fails_policy(self):
        for index, changes in enumerate(
            (
                {"_SYSTEMD_INVOCATION_ID": INVOCATION_ID},
                {"INVOCATION_ID": INVOCATION_ID},
                {"_PID": str(MAIN_PID)},
            )
        ):
            record = manager_record(cursor="disguised-id-%d" % index)
            record["UNIT"] = "other.service"
            record["INVOCATION_ID"] = OTHER_ID
            if "_SYSTEMD_INVOCATION_ID" in changes:
                del record["INVOCATION_ID"]
            record.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(WakeJournalPolicyError):
                    parse_safe(wire((record,)))

    def test_process_and_subject_invocations_remain_distinct(self):
        record = manager_record(cursor="distinct-invocations")
        record["UNIT"] = "other.service"
        record["_SYSTEMD_INVOCATION_ID"] = OTHER_ID
        record["INVOCATION_ID"] = "44444444444444444444444444444444"
        parse_safe(wire((record,)))

    def test_ambiguous_service_and_manager_identity_is_protocol_drift(self):
        record = service_record(cursor="ambiguous")
        record["UNIT"] = SERVICE_UNIT
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire((record,)))

    def test_cursor_terminal_and_order_invariants_are_exact(self):
        first = service_record(cursor="cursor-one", monotonic="100")
        second = service_record(cursor="cursor-two", monotonic="101")
        cases = (
            wire((first,), terminal_cursor="wrong-terminal"),
            wire((), terminal_cursor="wrong-empty"),
            wire(
                (
                    service_record(cursor=START_CURSOR),
                )
            ),
            wire((first, dict(first))),
            wire(
                (
                    second,
                    service_record(
                        cursor="cursor-three",
                        monotonic="100",
                    ),
                )
            ),
        )
        for raw in cases:
            with self.subTest(raw=raw[-80:]):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(raw)

    def test_cursors_are_bounded_opaque_printable_text(self):
        opaque = "opaque cursor = value;with:punctuation"
        content = parse_safe(
            wire((), terminal_cursor=opaque),
            start_cursor=opaque,
        )
        self.assertEqual(content.terminal_cursor, opaque)

        exact = "c" * MAX_CURSOR_BYTES
        record = service_record(cursor=exact)
        self.assertEqual(
            parse_safe(wire((record,))).terminal_cursor,
            exact,
        )
        for invalid in (
            "",
            "c" * (MAX_CURSOR_BYTES + 1),
            "cursor\ninjection",
            "cursor\x7finjection",
            "snowman-\u2603",
        ):
            with self.subTest(invalid=invalid[:20]):
                with self.assertRaises((TypeError, ValueError)):
                    expectation(start_cursor=invalid)

    def test_ids_and_numeric_fields_are_canonical_and_bounded(self):
        invalid_ids = (
            "0" * 32,
            ("abcdef" * 5 + "ab").upper(),
            BOOT_ID + "0",
            BOOT_ID[:-1],
            "11111111-1111-1111-1111-111111111111",
        )
        for invalid in invalid_ids:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    expectation(boot_id=invalid)

        numeric_cases = (
            ("__MONOTONIC_TIMESTAMP", "-1"),
            ("__MONOTONIC_TIMESTAMP", "01"),
            ("__MONOTONIC_TIMESTAMP", str(MAX_UINT64 + 1)),
            ("__REALTIME_TIMESTAMP", "+1"),
            ("_PID", "0"),
            ("_PID", "01"),
            ("_UID", "-1"),
            ("PRIORITY", "8"),
        )
        for index, (field, value) in enumerate(numeric_cases):
            record = service_record(cursor="numeric-%d" % index)
            record[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(WakeJournalProtocolError):
                    parse_content(wire((record,)))

    def test_field_message_and_record_bounds_are_exact(self):
        record = service_record(cursor="field-limits")
        record["A" * MAX_KEY_BYTES] = "x" * MAX_VALUE_BYTES
        parse_content(wire((record,)))

        invalid_key = service_record(cursor="key-too-long")
        invalid_key["A" * (MAX_KEY_BYTES + 1)] = "x"
        invalid_value = service_record(cursor="value-too-long")
        invalid_value["EXTRA"] = "x" * (MAX_VALUE_BYTES + 1)
        invalid_message = service_record(
            cursor="message-too-long",
            message="x" * (MAX_MESSAGE_BYTES + 1),
        )
        for invalid in (invalid_key, invalid_value, invalid_message):
            with self.assertRaises(WakeJournalProtocolError):
                parse_content(wire((invalid,)))

        exact_record = padded_record(
            MAX_RECORD_BYTES,
            cursor="record-bound",
        )
        parse_safe(wire((exact_record,)))
        oversized_record = padded_record(
            MAX_RECORD_BYTES + 1,
            cursor="record-over",
        )
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire((oversized_record,)))

    def test_field_and_record_count_bounds_are_exact(self):
        record = service_record(cursor="field-count")
        for index in range(MAX_FIELDS - len(record)):
            record["EXTRA_%02d" % index] = "x"
        parse_safe(wire((record,)))
        record["ONE_TOO_MANY"] = "x"
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire((record,)))

        records = tuple(
            service_record(
                cursor="count-%04d" % index,
                monotonic=str(index),
            )
            for index in range(MAX_RECORDS)
        )
        self.assertEqual(len(parse_safe(wire(records)).records), MAX_RECORDS)
        extra = service_record(
            cursor="count-extra",
            monotonic=str(MAX_RECORDS),
        )
        with self.assertRaises(WakeJournalProtocolError):
            parse_content(wire(records + (extra,)))

    def test_whole_content_byte_bound_is_exact(self):
        exact = content_with_size(MAX_CONTENT_BYTES)
        self.assertEqual(len(exact), MAX_CONTENT_BYTES)
        parse_safe(exact)
        oversized = content_with_size(MAX_CONTENT_BYTES + 1)
        self.assertEqual(len(oversized), MAX_CONTENT_BYTES + 1)
        with self.assertRaisesRegex(
            WakeJournalProtocolError,
            "byte bound",
        ):
            parse_content(oversized)


def expectation(
    *,
    start_cursor=START_CURSOR,
    boot_id=BOOT_ID,
    invocation_id=INVOCATION_ID,
    main_pid=MAIN_PID,
):
    return JournalExpectation(
        start_cursor=start_cursor,
        boot_id=boot_id,
        invocation_id=invocation_id,
        main_pid=main_pid,
    )


def parse_content(raw, *, start_cursor=START_CURSOR):
    return decode_untrusted_wake_journal_content(
        raw,
        start_cursor=start_cursor,
        boot_id=BOOT_ID,
        invocation_id=INVOCATION_ID,
        main_pid=MAIN_PID,
    )


def parse_safe(raw, *, start_cursor=START_CURSOR):
    return parse_safe_wake_journal(
        raw,
        start_cursor=start_cursor,
        boot_id=BOOT_ID,
        invocation_id=INVOCATION_ID,
        main_pid=MAIN_PID,
    )


def service_record(
    *,
    cursor="fixture-cursor",
    monotonic="100",
    realtime="200",
    message="TV power status: on",
):
    return {
        "MESSAGE": message,
        "PRIORITY": "6",
        "__CURSOR": cursor,
        "__MONOTONIC_TIMESTAMP": monotonic,
        "__REALTIME_TIMESTAMP": realtime,
        "_BOOT_ID": BOOT_ID,
        "_PID": str(MAIN_PID),
        "_SYSTEMD_INVOCATION_ID": INVOCATION_ID,
        "_SYSTEMD_UNIT": SERVICE_UNIT,
        "_UID": "0",
    }


def manager_record(*, cursor, target_field="UNIT"):
    return {
        "INVOCATION_ID": INVOCATION_ID,
        "MESSAGE": "Started Activate Kodi when the TV wakes.",
        "PRIORITY": "6",
        target_field: SERVICE_UNIT,
        "__CURSOR": cursor,
        "__MONOTONIC_TIMESTAMP": "100",
        "__REALTIME_TIMESTAMP": "200",
        "_BOOT_ID": BOOT_ID,
        "_PID": "1",
        "_SYSTEMD_UNIT": "init.scope",
        "_UID": "0",
    }


def encode_record(record):
    return json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def wire(records, terminal_cursor=None):
    records = tuple(records)
    if terminal_cursor is None:
        terminal_cursor = (
            records[-1]["__CURSOR"] if records else START_CURSOR
        )
    return (
        b"".join(
            b"\x1e" + encode_record(record) + b"\n"
            for record in records
        )
        + b"-- cursor: "
        + terminal_cursor.encode("ascii")
        + b"\n"
    )


def padded_record(target, *, cursor):
    record = service_record(cursor=cursor)
    for index in range(3):
        record["PAD_%d" % index] = "x" * MAX_VALUE_BYTES
    record["PAD_FINAL"] = "x"
    current = len(encode_record(record))
    needed = target - current + 1
    if not 1 <= needed <= MAX_VALUE_BYTES:
        raise AssertionError("target cannot be represented")
    record["PAD_FINAL"] = "x" * needed
    if len(encode_record(record)) != target:
        raise AssertionError("wrong padded record length")
    return record


def content_with_size(target):
    record_wire_size = MAX_RECORD_BYTES + 2
    maximum_records = (target - 64) // record_wire_size
    records = [
        padded_record(
            MAX_RECORD_BYTES,
            cursor="size-cursor-%04d" % index,
        )
        for index in range(maximum_records)
    ]
    final_cursor = "size-cursor-%04d" % maximum_records
    footer_size = len(
        b"-- cursor: " + final_cursor.encode("ascii") + b"\n"
    )
    used = sum(
        1 + len(encode_record(record)) + 1
        for record in records
    )
    final_size = target - used - footer_size - 2
    records.append(
        padded_record(final_size, cursor=final_cursor)
    )
    result = wire(records)
    if len(result) != target:
        raise AssertionError("wrong content length")
    return result


if __name__ == "__main__":
    unittest.main()
