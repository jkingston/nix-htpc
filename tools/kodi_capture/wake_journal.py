"""Strict content parsing for finite ``cec-tv-wake`` journal slices.

The fixed Pi helper will record a global journal cursor before any capture
action, then run one finite global ``journalctl --after-cursor`` JSON-sequence
query while the passive CEC monitor remains active. A global query is
intentional: systemd emits no terminal cursor for an empty unit-filtered query
when its start cursor came from the global journal. This module validates the
complete global content and applies policy only to wake-service records.
Monitor completion, process exit, stderr, duration, boot continuity, and
start/end service identity remain transport and composition invariants.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional


SERVICE_UNIT = "cec-tv-wake.service"
BENIGN_MESSAGES = frozenset(
    (
        "TV is in standby; CEC source activation armed",
        "TV power status: standby",
        "TV power status: on",
    )
)
ACTIVATION_MESSAGES = frozenset(
    (
        "TV wake detected; asking Kodi to become active",
        "Kodi CEC source activation sent",
        "Kodi is unavailable; CEC source activation remains armed",
    )
)
RESERVED_MESSAGES = BENIGN_MESSAGES | ACTIVATION_MESSAGES

# A short headless capture should be far smaller; these whole-stream caps
# tolerate unrelated global-journal bursts while keeping hostile input bounded.
MAX_CONTENT_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024
MAX_RECORDS = 2048
MAX_FIELDS = 64
MAX_FIELD_VALUES = 64
MAX_KEY_BYTES = 128
MAX_VALUE_BYTES = 4096
MAX_MESSAGE_BYTES = 256
MAX_CURSOR_BYTES = 4096
MAX_UINT64 = (1 << 64) - 1
MAX_PID = (1 << 31) - 1

_FOOTER_PREFIX = b"-- cursor: "
_RECORD_PREFIX = b"\x1e"
_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
_KEY_PATTERN = re.compile(r"\A[A-Z_][A-Z0-9_]*\Z")
_DECIMAL_PATTERN = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
_TARGET_FIELDS = (
    "UNIT",
    "OBJECT_SYSTEMD_UNIT",
    "COREDUMP_UNIT",
)


class WakeJournalError(Exception):
    """Base class for rejected wake-service journal evidence."""


class WakeJournalProtocolError(WakeJournalError):
    """The journal text is malformed or internally inconsistent."""


class WakeJournalPolicyError(WakeJournalError):
    """Well-formed journal content violates the capture policy."""

    def __init__(
        self,
        violations: tuple[str, ...],
        content: "WakeJournalContent",
    ):
        self.violations = violations
        self.content = content
        super().__init__("; ".join(violations))


@dataclass(frozen=True)
class JournalExpectation:
    """Trusted anchors collected before the first capture action."""

    start_cursor: str
    boot_id: str
    invocation_id: str
    main_pid: int

    def __post_init__(self) -> None:
        _require_cursor(self.start_cursor, "start_cursor")
        _require_id(self.boot_id, "boot_id")
        _require_id(self.invocation_id, "invocation_id")
        _require_integer(
            self.main_pid,
            "main_pid",
            minimum=1,
            maximum=MAX_PID,
        )


@dataclass(frozen=True)
class WakeJournalRecord:
    """One normalized record returned by the fixed global query."""

    source: str
    cursor: str
    boot_id: str
    realtime_usec: int
    monotonic_usec: int
    messages: tuple[str, ...]
    process_invocation_id: Optional[str]
    subject_invocation_ids: tuple[str, ...]
    pid: Optional[int]
    uid: Optional[int]
    priority: Optional[int]

    def __post_init__(self) -> None:
        if self.source not in ("service", "manager", "unrelated"):
            raise ValueError(
                "source must be service, manager, or unrelated"
            )
        _require_cursor(self.cursor, "cursor")
        _require_id(self.boot_id, "record boot_id")
        _require_integer(
            self.realtime_usec,
            "realtime_usec",
            minimum=0,
            maximum=MAX_UINT64,
        )
        _require_integer(
            self.monotonic_usec,
            "monotonic_usec",
            minimum=0,
            maximum=MAX_UINT64,
        )
        if not isinstance(self.messages, tuple):
            raise TypeError("messages must be a tuple")
        for message in self.messages:
            _require_text(
                message,
                "message",
                maximum=MAX_VALUE_BYTES,
            )
        if self.source == "service" and len(self.messages) != 1:
            raise ValueError(
                "service records must contain exactly one message"
            )
        if self.process_invocation_id is not None:
            _require_id(
                self.process_invocation_id,
                "record process_invocation_id",
            )
        if not isinstance(self.subject_invocation_ids, tuple):
            raise TypeError("subject_invocation_ids must be a tuple")
        for subject_invocation_id in self.subject_invocation_ids:
            _require_text(
                subject_invocation_id,
                "subject_invocation_id",
                maximum=MAX_VALUE_BYTES,
            )
        for value, name, maximum in (
            (self.pid, "record pid", MAX_PID),
            (self.uid, "record uid", MAX_UINT64),
            (self.priority, "record priority", 7),
        ):
            if value is not None:
                _require_integer(
                    value,
                    name,
                    minimum=0,
                    maximum=maximum,
                )


@dataclass(frozen=True, init=False)
class WakeJournalContent:
    """Raw-backed finite journal content before wake policy is applied."""

    raw: bytes
    expectation: JournalExpectation
    terminal_cursor: str
    records: tuple[WakeJournalRecord, ...]

    def __init__(self, raw: bytes, expectation: JournalExpectation):
        if not isinstance(expectation, JournalExpectation):
            raise TypeError("expectation must be a JournalExpectation")
        terminal_cursor, records = _parse_content(
            raw,
            expectation.start_cursor,
        )
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "expectation", expectation)
        object.__setattr__(self, "terminal_cursor", terminal_cursor)
        object.__setattr__(self, "records", records)


def decode_untrusted_wake_journal_content(
    raw: bytes,
    *,
    start_cursor: str,
    boot_id: str,
    invocation_id: str,
    main_pid: int,
) -> WakeJournalContent:
    """Decode content for diagnostics without applying wake policy."""

    expectation = JournalExpectation(
        start_cursor=start_cursor,
        boot_id=boot_id,
        invocation_id=invocation_id,
        main_pid=main_pid,
    )
    return WakeJournalContent(raw, expectation)


def require_safe_wake_journal(content: WakeJournalContent) -> None:
    """Require every wake-service record to be known benign output."""

    if not isinstance(content, WakeJournalContent):
        raise TypeError("content must be WakeJournalContent")
    expected = content.expectation
    violations = []
    for index, record in enumerate(content.records):
        reason = _policy_violation(record, expected)
        if reason is not None:
            violations.append(
                "record %d: %s: %r"
                % (
                    index,
                    reason,
                    record.messages,
                )
            )
    if violations:
        raise WakeJournalPolicyError(tuple(violations), content)


def parse_safe_wake_journal(
    raw: bytes,
    *,
    start_cursor: str,
    boot_id: str,
    invocation_id: str,
    main_pid: int,
) -> WakeJournalContent:
    """Parse a finite slice and enforce the wake-service policy."""

    content = decode_untrusted_wake_journal_content(
        raw,
        start_cursor=start_cursor,
        boot_id=boot_id,
        invocation_id=invocation_id,
        main_pid=main_pid,
    )
    require_safe_wake_journal(content)
    return content


def _parse_content(
    raw: bytes,
    start_cursor: str,
) -> tuple[str, tuple[WakeJournalRecord, ...]]:
    lines = _content_lines(raw)
    if not lines[-1].startswith(_FOOTER_PREFIX):
        raise WakeJournalProtocolError(
            "wake journal content is missing its terminal cursor"
        )
    terminal_cursor = _decode_cursor(
        lines[-1][len(_FOOTER_PREFIX) :],
        "terminal cursor",
    )
    record_lines = lines[:-1]
    if len(record_lines) > MAX_RECORDS:
        raise WakeJournalProtocolError(
            "wake journal content exceeded its record-count bound"
        )

    records = []
    seen_cursors = set()
    previous_monotonic = None
    for index, line in enumerate(record_lines):
        record = _parse_record(line, index)
        if record.cursor == start_cursor:
            raise WakeJournalProtocolError(
                "journal record repeats the start cursor"
            )
        if record.cursor in seen_cursors:
            raise WakeJournalProtocolError(
                "journal records contain a duplicate cursor"
            )
        if (
            previous_monotonic is not None
            and record.monotonic_usec < previous_monotonic
        ):
            raise WakeJournalProtocolError(
                "journal monotonic timestamps moved backwards"
            )
        seen_cursors.add(record.cursor)
        previous_monotonic = record.monotonic_usec
        records.append(record)

    if records:
        if terminal_cursor != records[-1].cursor:
            raise WakeJournalProtocolError(
                "terminal cursor does not match the last record"
            )
    elif terminal_cursor != start_cursor:
        raise WakeJournalProtocolError(
            "empty journal content did not preserve the start cursor"
        )
    return terminal_cursor, tuple(records)


def _content_lines(raw: bytes) -> tuple[bytes, ...]:
    if not isinstance(raw, bytes):
        raise TypeError("wake journal content must be bytes")
    if not raw:
        raise WakeJournalProtocolError("wake journal content is empty")
    if len(raw) > MAX_CONTENT_BYTES:
        raise WakeJournalProtocolError(
            "wake journal content exceeded its byte bound"
        )
    if b"\r" in raw:
        raise WakeJournalProtocolError(
            "wake journal content contains carriage-return framing"
        )
    if not raw.endswith(b"\n"):
        raise WakeJournalProtocolError(
            "wake journal content must end at a complete newline"
        )
    lines = tuple(raw[:-1].split(b"\n"))
    if not lines or any(not line for line in lines):
        raise WakeJournalProtocolError(
            "wake journal content contains an empty line"
        )
    if any(
        line.startswith(_FOOTER_PREFIX)
        for line in lines[:-1]
    ):
        raise WakeJournalProtocolError(
            "wake journal content contains a non-terminal cursor"
        )
    return lines


def _parse_record(line: bytes, index: int) -> WakeJournalRecord:
    if not line.startswith(_RECORD_PREFIX):
        raise WakeJournalProtocolError(
            "journal record %d lacks its JSON-sequence prefix" % index
        )
    encoded = line[1:]
    if not encoded or len(encoded) > MAX_RECORD_BYTES:
        raise WakeJournalProtocolError(
            "journal record %d has an invalid byte length" % index
        )
    fields = _decode_fields(encoded, index)
    return _record_from_fields(fields, index)


def _decode_fields(
    encoded: bytes,
    index: int,
) -> dict[str, Any]:
    try:
        text = encoded.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise WakeJournalProtocolError(
            "journal record %d is not strict UTF-8" % index
        ) from error

    def reject_constant(value: str) -> Any:
        raise ValueError("non-finite JSON constant %s" % value)

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise WakeJournalProtocolError(
            "journal record %d is not strict JSON" % index
        ) from error
    if not isinstance(decoded, dict):
        raise WakeJournalProtocolError(
            "journal record %d must be a JSON object" % index
        )
    if len(decoded) > MAX_FIELDS:
        raise WakeJournalProtocolError(
            "journal record %d exceeded its field-count bound" % index
        )

    # The Pi's journalctl emits additional automatic metadata even with a
    # fixed --output-fields list. Validate bounded journal field grammar here;
    # _record_from_fields consumes only the security-relevant fields.
    for key, value in decoded.items():
        if (
            not isinstance(key, str)
            or _KEY_PATTERN.fullmatch(key) is None
            or len(key.encode("ascii")) > MAX_KEY_BYTES
        ):
            raise WakeJournalProtocolError(
                "journal record %d contains an invalid field name"
                % index
            )
        _validate_field_value(value, key, index)
    return decoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_field_value(value: Any, key: str, index: int) -> None:
    try:
        size = _field_value_size(value)
    except (TypeError, UnicodeEncodeError) as error:
        raise WakeJournalProtocolError(
            "journal record %d field %s has an unsupported JSON shape"
            % (index, key)
        ) from error
    if size > MAX_VALUE_BYTES:
        raise WakeJournalProtocolError(
            "journal record %d field %s exceeded its byte bound"
            % (index, key)
        )


def _field_value_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8", "strict"))
    if value is None:
        return 0
    if _is_byte_array(value):
        return len(value)
    if isinstance(value, list):
        if len(value) > MAX_FIELD_VALUES:
            raise TypeError("too many repeated field values")
        return sum(_single_field_value_size(item) for item in value)
    raise TypeError("unsupported journal JSON field value")


def _single_field_value_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8", "strict"))
    if value is None:
        return 0
    if _is_byte_array(value):
        return len(value)
    raise TypeError("unsupported repeated journal field value")


def _is_byte_array(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, int)
        and not isinstance(item, bool)
        and 0 <= item <= 0xFF
        for item in value
    )


def _text_field_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and not _is_byte_array(value):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _record_from_fields(
    values: dict[str, Any],
    index: int,
) -> WakeJournalRecord:
    required = (
        "__CURSOR",
        "__MONOTONIC_TIMESTAMP",
        "__REALTIME_TIMESTAMP",
        "_BOOT_ID",
    )
    missing = tuple(key for key in required if key not in values)
    if missing:
        raise WakeJournalProtocolError(
            "journal record %d is missing fields: %s"
            % (index, ", ".join(missing))
        )

    cursor = _content_cursor(values["__CURSOR"], index)
    boot_id = _content_id(values["_BOOT_ID"], "_BOOT_ID", index)
    realtime_usec = _content_decimal(
        values["__REALTIME_TIMESTAMP"],
        "__REALTIME_TIMESTAMP",
        index,
        maximum=MAX_UINT64,
    )
    monotonic_usec = _content_decimal(
        values["__MONOTONIC_TIMESTAMP"],
        "__MONOTONIC_TIMESTAMP",
        index,
        maximum=MAX_UINT64,
    )
    messages = _text_field_values(values.get("MESSAGE"))
    if (
        "_SYSTEMD_UNIT" in values
        and not isinstance(values["_SYSTEMD_UNIT"], str)
    ):
        raise WakeJournalProtocolError(
            "journal record %d field _SYSTEMD_UNIT must be text"
            % index
        )
    direct_service = values.get("_SYSTEMD_UNIT") == SERVICE_UNIT
    manager_target = any(
        SERVICE_UNIT in _text_field_values(values.get(key))
        for key in _TARGET_FIELDS
    )
    if direct_service and manager_target:
        raise WakeJournalProtocolError(
            "journal record %d has ambiguous wake-unit identity" % index
        )

    process_invocation_id = _optional_id_field(
        values,
        "_SYSTEMD_INVOCATION_ID",
        index,
    )
    subject_invocation_ids = _text_field_values(
        values.get("INVOCATION_ID")
    )

    if direct_service:
        source = "service"
        service_required = (
            "MESSAGE",
            "_SYSTEMD_INVOCATION_ID",
            "_PID",
            "_UID",
            "PRIORITY",
        )
        missing = tuple(
            key for key in service_required if key not in values
        )
        if missing:
            raise WakeJournalProtocolError(
                "service record %d is missing fields: %s"
                % (index, ", ".join(missing))
            )
        for key in service_required:
            if not isinstance(values[key], str):
                raise WakeJournalProtocolError(
                    "service record %d field %s must be text"
                    % (index, key)
                )
        if len(values["MESSAGE"].encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise WakeJournalProtocolError(
                "service record %d MESSAGE exceeded its byte bound"
                % index
            )
        pid = _content_decimal(
            values["_PID"],
            "_PID",
            index,
            maximum=MAX_PID,
            minimum=1,
        )
        uid = _content_decimal(
            values["_UID"],
            "_UID",
            index,
            maximum=MAX_UINT64,
        )
        priority = _content_decimal(
            values["PRIORITY"],
            "PRIORITY",
            index,
            maximum=7,
        )
    elif manager_target:
        source = "manager"
        pid = _optional_content_decimal(
            values.get("_PID"),
            "_PID",
            index,
            maximum=MAX_PID,
            minimum=1,
        )
        uid = _optional_content_decimal(
            values.get("_UID"),
            "_UID",
            index,
            maximum=MAX_UINT64,
        )
        priority = _optional_content_decimal(
            values.get("PRIORITY"),
            "PRIORITY",
            index,
            maximum=7,
        )
    else:
        source = "unrelated"
        pid = _optional_content_decimal(
            values.get("_PID"),
            "_PID",
            index,
            maximum=MAX_PID,
            minimum=1,
        )
        uid = _optional_content_decimal(
            values.get("_UID"),
            "_UID",
            index,
            maximum=MAX_UINT64,
        )
        priority = _optional_content_decimal(
            values.get("PRIORITY"),
            "PRIORITY",
            index,
            maximum=7,
        )

    return WakeJournalRecord(
        source=source,
        cursor=cursor,
        boot_id=boot_id,
        realtime_usec=realtime_usec,
        monotonic_usec=monotonic_usec,
        messages=messages,
        process_invocation_id=process_invocation_id,
        subject_invocation_ids=subject_invocation_ids,
        pid=pid,
        uid=uid,
        priority=priority,
    )


def _policy_violation(
    record: WakeJournalRecord,
    expected: JournalExpectation,
) -> Optional[str]:
    if record.boot_id != expected.boot_id:
        return "changed boot identity"
    if record.source == "unrelated":
        if any(
            message in RESERVED_MESSAGES
            for message in record.messages
        ):
            return "wake-service message lacks service identity"
        if (
            record.process_invocation_id == expected.invocation_id
            or expected.invocation_id in record.subject_invocation_ids
            or record.pid == expected.main_pid
        ):
            return "wake-service identity lacks wake-unit metadata"
        return None
    if record.source != "service":
        return "systemd lifecycle or coredump activity"
    if record.process_invocation_id != expected.invocation_id:
        return "changed service invocation"
    if record.pid != expected.main_pid:
        return "changed service process"
    if record.uid != 0:
        return "unexpected service user"
    if record.priority != 6:
        return "unexpected service priority"
    if record.messages[0] not in BENIGN_MESSAGES:
        return "wake/source activation or unknown service activity"
    return None


def _decode_cursor(value: bytes, name: str) -> str:
    try:
        decoded = value.decode("ascii", "strict")
    except UnicodeDecodeError as error:
        raise WakeJournalProtocolError(
            "%s is not strict ASCII" % name
        ) from error
    try:
        _require_cursor(decoded, name)
    except (TypeError, ValueError) as error:
        raise WakeJournalProtocolError(str(error)) from error
    return decoded


def _content_cursor(value: str, index: int) -> str:
    try:
        _require_cursor(value, "record cursor")
    except (TypeError, ValueError) as error:
        raise WakeJournalProtocolError(
            "journal record %d has an invalid cursor" % index
        ) from error
    return value


def _content_id(
    value: str,
    name: str,
    index: int,
) -> str:
    try:
        _require_id(value, name)
    except (TypeError, ValueError) as error:
        raise WakeJournalProtocolError(
            "journal record %d has an invalid %s" % (index, name)
        ) from error
    return value


def _optional_id_field(
    values: dict[str, Any],
    name: str,
    index: int,
) -> Optional[str]:
    if name not in values:
        return None
    return _content_id(values[name], name, index)


def _content_decimal(
    value: str,
    name: str,
    index: int,
    *,
    maximum: int,
    minimum: int = 0,
) -> int:
    if (
        not isinstance(value, str)
        or _DECIMAL_PATTERN.fullmatch(value) is None
    ):
        raise WakeJournalProtocolError(
            "journal record %d field %s is not canonical decimal text"
            % (index, name)
        )
    parsed = int(value, 10)
    if not minimum <= parsed <= maximum:
        raise WakeJournalProtocolError(
            "journal record %d field %s is outside its bound"
            % (index, name)
        )
    return parsed


def _optional_content_decimal(
    value: Optional[str],
    name: str,
    index: int,
    *,
    maximum: int,
    minimum: int = 0,
) -> Optional[int]:
    if value is None:
        return None
    return _content_decimal(
        value,
        name,
        index,
        maximum=maximum,
        minimum=minimum,
    )


def _require_cursor(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError("%s must be non-empty text" % name)
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeEncodeError as error:
        raise ValueError("%s must be printable ASCII" % name) from error
    if len(encoded) > MAX_CURSOR_BYTES:
        raise ValueError("%s exceeded its byte bound" % name)
    if any(not 0x20 <= byte <= 0x7E for byte in encoded):
        raise ValueError("%s must be printable ASCII" % name)


def _require_id(value: Any, name: str) -> None:
    if (
        not isinstance(value, str)
        or _ID_PATTERN.fullmatch(value) is None
        or value == ("0" * 32)
    ):
        raise ValueError(
            "%s must be a nonzero 32-digit lowercase hexadecimal ID"
            % name
        )


def _require_text(value: Any, name: str, *, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError("%s must be text" % name)
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as error:
        raise ValueError(
            "%s must contain only Unicode scalar values" % name
        ) from error
    if len(encoded) > maximum:
        raise ValueError("%s exceeded its byte bound" % name)


def _require_integer(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be an integer" % name)
    if not minimum <= value <= maximum:
        raise ValueError(
            "%s must be between %d and %d"
            % (name, minimum, maximum)
        )
