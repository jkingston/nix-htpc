"""Strict host-side decoding for a passive Kodi/CEC evidence envelope.

This module intentionally has no I/O capability.  A fixed remote producer and
its host-side process gate prove process lifetime, stderr, and exit status;
this module proves the bytes they exchange, the identity fences around the
capture, and the policy of the captured content.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from .cec_trace import (
    MAX_TRACE_BYTES,
    CecMonitorContent,
    CecTracePolicyError,
    CecTraceProtocolError,
    parse_cec_monitor_content as _parse_cec_monitor_content,
)
from .wake_journal import (
    MAX_CONTENT_BYTES,
    MAX_CURSOR_BYTES,
    MAX_PID,
    MAX_UINT64,
    WakeJournalContent,
    WakeJournalPolicyError,
    WakeJournalProtocolError,
    parse_safe_wake_journal as _parse_safe_wake_journal,
)

__all__ = (
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
)


PROTOCOL_VERSION = "KODI-PASSIVE-EVIDENCE/1"
MAX_READY_BYTES = 8192
# Three independently bounded one-MiB sections expand beyond four MiB when
# base64 encoded, before the canonical JSON framing is added.
MAX_ENVELOPE_BYTES = 5 * 1024 * 1024
NONCE_HEX_LENGTH = 32
MIN_OBSERVATION_USEC = 8_000_000
MAX_ACTION_WINDOW_USEC = 5_000_000
MAX_SESSION_USEC = 30_000_000

SERVICE_UNIT = "cec-tv-wake.service"
SERVICE_LOAD_STATE = "loaded"
SERVICE_ACTIVE_STATE = "active"
SERVICE_SUB_STATE = "running"

_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
_DECIMAL_PATTERN = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
_READY_FIELD_COUNT = 15
_READY_KIND = "READY"
_RESULT_KIND = "RESULT"
_TOP_LEVEL_KEYS = frozenset(
    (
        "version",
        "nonce",
        "start_cursor",
        "boot_ids",
        "services",
        "timing_usec",
        "cec_trace_b64",
        "live_journal_b64",
        "final_journal_b64",
    )
)
_FENCE_KEYS = frozenset(("start", "live", "final"))
_SERVICE_KEYS = frozenset(
    (
        "unit_id",
        "load_state",
        "active_state",
        "sub_state",
        "invocation_id",
        "main_pid",
        "n_restarts",
        "exec_start_usec",
        "active_enter_usec",
    )
)
_TIMING_WIRE_KEYS = frozenset(
    (
        "ready",
        "finish",
        "live_journal",
        "monitor_exit",
        "final_journal",
        "complete",
    )
)
_TIMING_FIELDS = (
    ("ready", "ready_usec"),
    ("finish", "finish_usec"),
    ("live_journal", "live_journal_usec"),
    ("monitor_exit", "monitor_exit_usec"),
    ("final_journal", "final_journal_usec"),
    ("complete", "complete_usec"),
)


class PassiveEvidenceError(Exception):
    """Base class for rejected passive evidence."""


class PassiveEvidenceProtocolError(PassiveEvidenceError):
    """The wire representation or one captured section is malformed."""


class PassiveEvidenceContinuityError(PassiveEvidenceError):
    """Well-formed evidence does not describe one continuous capture."""


class PassiveEvidencePolicyError(PassiveEvidenceError):
    """Well-formed continuous evidence contains a forbidden observation."""

    def __init__(self, message: str, raw: bytes, ready: "ReadyEvidence"):
        self.raw = raw
        self.ready = ready
        super().__init__(message)


@dataclass(frozen=True)
class ServiceIdentity:
    """One complete systemd identity and state fence."""

    unit_id: str
    load_state: str
    active_state: str
    sub_state: str
    invocation_id: str
    main_pid: int
    n_restarts: int
    exec_start_usec: int
    active_enter_usec: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.unit_id, "unit_id"),
            (self.load_state, "load_state"),
            (self.active_state, "active_state"),
            (self.sub_state, "sub_state"),
        ):
            _require_token(value, name)
        _require_identifier(self.invocation_id, "invocation_id")
        _require_integer(
            self.main_pid,
            "main_pid",
            minimum=1,
            maximum=MAX_PID,
        )
        _require_integer(
            self.n_restarts,
            "n_restarts",
            minimum=0,
            maximum=MAX_UINT64,
        )
        _require_integer(
            self.exec_start_usec,
            "exec_start_usec",
            minimum=1,
            maximum=MAX_UINT64,
        )
        _require_integer(
            self.active_enter_usec,
            "active_enter_usec",
            minimum=1,
            maximum=MAX_UINT64,
        )
        if self.exec_start_usec > self.active_enter_usec:
            raise ValueError(
                "exec_start_usec must not follow active_enter_usec"
            )


@dataclass(frozen=True, init=False)
class ReadyEvidence:
    """Raw-backed READY message; normalized fields cannot be forged."""

    raw: bytes
    nonce: str
    boot_id: str
    service: ServiceIdentity
    start_cursor: str
    ready_usec: int

    def __init__(self, raw: bytes):
        (
            nonce,
            boot_id,
            service,
            start_cursor,
            ready_usec,
        ) = _parse_ready_line(raw)
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "nonce", nonce)
        object.__setattr__(self, "boot_id", boot_id)
        object.__setattr__(self, "service", service)
        object.__setattr__(self, "start_cursor", start_cursor)
        object.__setattr__(self, "ready_usec", ready_usec)


@dataclass(frozen=True)
class CaptureTiming:
    """Monotonic producer timestamps for the finite capture sequence.

    ``live_journal_usec`` is recorded only after the live journal query has
    succeeded and the producer has proved the monitor is still alive.
    ``monitor_exit_usec`` follows a natural status-zero, fully drained,
    empty-stderr monitor exit.  Those operational facts are enforced by the
    producer/process gate; this value object enforces their temporal bounds.
    """

    ready_usec: int
    finish_usec: int
    live_journal_usec: int
    monitor_exit_usec: int
    final_journal_usec: int
    complete_usec: int

    def __post_init__(self) -> None:
        for _, name in _TIMING_FIELDS:
            _require_integer(
                getattr(self, name),
                name,
                minimum=0,
                maximum=MAX_UINT64,
            )
        if not (
            self.ready_usec
            <= self.finish_usec
            <= self.live_journal_usec
            < self.monitor_exit_usec
            <= self.final_journal_usec
            <= self.complete_usec
        ):
            raise ValueError("capture timestamps are not monotonically ordered")
        if (
            self.finish_usec - self.ready_usec
            > MAX_ACTION_WINDOW_USEC
        ):
            raise ValueError("capture action exceeded its time bound")
        if (
            self.live_journal_usec - self.ready_usec
            < MIN_OBSERVATION_USEC
        ):
            raise ValueError("capture observation was too short")
        if self.complete_usec - self.ready_usec > MAX_SESSION_USEC:
            raise ValueError("capture session exceeded its time bound")


@dataclass(frozen=True, init=False)
class PassiveEvidence:
    """Raw-backed, parsed, continuous, policy-safe passive evidence."""

    raw: bytes
    ready: ReadyEvidence
    timing: CaptureTiming
    live_boot_id: str
    final_boot_id: str
    live_service: ServiceIdentity
    final_service: ServiceIdentity
    cec_trace: CecMonitorContent
    live_journal: WakeJournalContent
    final_journal: WakeJournalContent

    def __init__(self, raw: bytes, ready: ReadyEvidence):
        if type(ready) is not ReadyEvidence:
            raise TypeError("ready must be raw-backed ReadyEvidence")
        try:
            reparsed_ready = ReadyEvidence(ready.raw)
        except (AttributeError, TypeError) as error:
            raise TypeError(
                "ready must be raw-backed ReadyEvidence"
            ) from error
        if reparsed_ready != ready:
            raise TypeError(
                "ready fields do not match its raw READY line"
            )
        (
            timing,
            live_boot_id,
            final_boot_id,
            live_service,
            final_service,
            cec_trace,
            live_journal,
            final_journal,
        ) = _parse_passive_evidence(raw, ready)
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "ready", ready)
        object.__setattr__(self, "timing", timing)
        object.__setattr__(self, "live_boot_id", live_boot_id)
        object.__setattr__(self, "final_boot_id", final_boot_id)
        object.__setattr__(self, "live_service", live_service)
        object.__setattr__(self, "final_service", final_service)
        object.__setattr__(self, "cec_trace", cec_trace)
        object.__setattr__(self, "live_journal", live_journal)
        object.__setattr__(self, "final_journal", final_journal)


def decode_ready_line(raw: bytes) -> ReadyEvidence:
    """Decode one exact producer READY line."""

    return ReadyEvidence(raw)


def decode_result_header(raw: bytes, expected_nonce: str) -> int:
    """Decode one exact RESULT header and return its bounded body length."""

    _require_identifier(expected_nonce, "expected_nonce")
    fields = _decode_space_line(
        raw,
        "RESULT header",
        maximum=MAX_READY_BYTES,
        field_count=4,
    )
    if fields[0] != PROTOCOL_VERSION or fields[1] != _RESULT_KIND:
        raise PassiveEvidenceProtocolError(
            "RESULT header has the wrong protocol marker"
        )
    if fields[2] != expected_nonce:
        raise PassiveEvidenceProtocolError(
            "RESULT header nonce does not match READY"
        )
    return _parse_decimal(
        fields[3],
        "RESULT length",
        minimum=1,
        maximum=MAX_ENVELOPE_BYTES,
    )


def decode_passive_evidence(
    raw: bytes,
    ready: ReadyEvidence,
) -> PassiveEvidence:
    """Decode and validate a canonical passive-evidence JSON envelope."""

    return PassiveEvidence(raw, ready)


def _parse_ready_line(
    raw: bytes,
) -> Tuple[str, str, ServiceIdentity, str, int]:
    fields = _decode_space_line(
        raw,
        "READY line",
        maximum=MAX_READY_BYTES,
        field_count=_READY_FIELD_COUNT,
    )
    if fields[0] != PROTOCOL_VERSION or fields[1] != _READY_KIND:
        raise PassiveEvidenceProtocolError(
            "READY line has the wrong protocol marker"
        )
    nonce = _parse_identifier(fields[2], "nonce")
    boot_id = _parse_identifier(fields[3], "boot_id")
    service = _service_from_ready_fields(fields[4:13])
    ready_usec = _parse_decimal(
        fields[13],
        "ready_usec",
        minimum=0,
        maximum=MAX_UINT64,
    )
    if service.active_enter_usec > ready_usec:
        raise PassiveEvidenceProtocolError(
            "service active_enter_usec follows READY"
        )
    start_cursor = _decode_cursor_base64(fields[14])
    return nonce, boot_id, service, start_cursor, ready_usec


def _service_from_ready_fields(fields: Tuple[str, ...]) -> ServiceIdentity:
    if (
        fields[0] != SERVICE_UNIT
        or fields[1] != SERVICE_LOAD_STATE
        or fields[2] != SERVICE_ACTIVE_STATE
        or fields[3] != SERVICE_SUB_STATE
    ):
        raise PassiveEvidenceProtocolError(
            "READY service is not the fixed loaded active running unit"
        )
    try:
        service = ServiceIdentity(
            unit_id=fields[0],
            load_state=fields[1],
            active_state=fields[2],
            sub_state=fields[3],
            invocation_id=_parse_identifier(
                fields[4],
                "invocation_id",
            ),
            main_pid=_parse_decimal(
                fields[5],
                "main_pid",
                minimum=1,
                maximum=MAX_PID,
            ),
            n_restarts=_parse_decimal(
                fields[6],
                "n_restarts",
                minimum=0,
                maximum=MAX_UINT64,
            ),
            exec_start_usec=_parse_decimal(
                fields[7],
                "exec_start_usec",
                minimum=1,
                maximum=MAX_UINT64,
            ),
            active_enter_usec=_parse_decimal(
                fields[8],
                "active_enter_usec",
                minimum=1,
                maximum=MAX_UINT64,
            ),
        )
        return service
    except (TypeError, ValueError) as error:
        raise PassiveEvidenceProtocolError(
            "READY service identity is invalid"
        ) from error


def _parse_passive_evidence(
    raw: bytes,
    ready: ReadyEvidence,
) -> Tuple[
    CaptureTiming,
    str,
    str,
    ServiceIdentity,
    ServiceIdentity,
    CecMonitorContent,
    WakeJournalContent,
    WakeJournalContent,
]:
    document = _decode_canonical_document(raw)
    _require_exact_keys(document, _TOP_LEVEL_KEYS, "evidence")

    version = _json_text(document["version"], "version")
    nonce = _json_text(document["nonce"], "nonce")
    start_cursor = _json_text(document["start_cursor"], "start_cursor")
    if version != PROTOCOL_VERSION:
        raise PassiveEvidenceProtocolError(
            "evidence has the wrong protocol version"
        )
    _parse_identifier(nonce, "nonce")
    _require_cursor(start_cursor, "start_cursor")
    if nonce != ready.nonce:
        raise PassiveEvidenceContinuityError(
            "evidence nonce does not match READY"
        )
    if start_cursor != ready.start_cursor:
        raise PassiveEvidenceContinuityError(
            "evidence start cursor does not match READY"
        )

    boot_ids = _json_object(document["boot_ids"], "boot_ids")
    _require_exact_keys(boot_ids, _FENCE_KEYS, "boot_ids")
    start_boot_id = _json_identifier(boot_ids["start"], "start boot_id")
    live_boot_id = _json_identifier(boot_ids["live"], "live boot_id")
    final_boot_id = _json_identifier(boot_ids["final"], "final boot_id")

    services = _json_object(document["services"], "services")
    _require_exact_keys(services, _FENCE_KEYS, "services")
    start_service = _service_from_json(services["start"], "start service")
    live_service = _service_from_json(services["live"], "live service")
    final_service = _service_from_json(services["final"], "final service")

    timing_values = _json_object(document["timing_usec"], "timing_usec")
    _require_exact_keys(
        timing_values,
        _TIMING_WIRE_KEYS,
        "timing_usec",
    )
    timing_numbers = {
        field_name: _json_integer(
            timing_values[wire_name],
            wire_name,
            minimum=0,
            maximum=MAX_UINT64,
        )
        for wire_name, field_name in _TIMING_FIELDS
    }
    try:
        timing = CaptureTiming(**timing_numbers)
    except (TypeError, ValueError) as error:
        raise PassiveEvidenceContinuityError(str(error)) from error

    if start_boot_id != ready.boot_id:
        raise PassiveEvidenceContinuityError(
            "evidence start boot ID does not match READY"
        )
    if not (start_boot_id == live_boot_id == final_boot_id):
        raise PassiveEvidenceContinuityError(
            "boot ID changed during passive capture"
        )
    if start_service != ready.service:
        raise PassiveEvidenceContinuityError(
            "evidence start service does not match READY"
        )
    if not (start_service == live_service == final_service):
        raise PassiveEvidenceContinuityError(
            "wake service identity changed during passive capture"
        )
    if timing.ready_usec != ready.ready_usec:
        raise PassiveEvidenceContinuityError(
            "evidence ready timestamp does not match READY"
        )

    cec_raw = _decode_json_base64(
        document["cec_trace_b64"],
        "cec_trace_b64",
        MAX_TRACE_BYTES,
    )
    live_raw = _decode_json_base64(
        document["live_journal_b64"],
        "live_journal_b64",
        MAX_CONTENT_BYTES,
    )
    final_raw = _decode_json_base64(
        document["final_journal_b64"],
        "final_journal_b64",
        MAX_CONTENT_BYTES,
    )

    cec_trace = _parse_cec_section(cec_raw, raw, ready)
    live_journal = _parse_journal_section(
        live_raw,
        "live",
        raw,
        ready,
    )
    final_journal = _parse_journal_section(
        final_raw,
        "final",
        raw,
        ready,
    )
    _require_journal_continuity(
        live_journal,
        final_journal,
        ready.start_cursor,
    )
    return (
        timing,
        live_boot_id,
        final_boot_id,
        live_service,
        final_service,
        cec_trace,
        live_journal,
        final_journal,
    )


def _parse_cec_section(
    section: bytes,
    envelope: bytes,
    ready: ReadyEvidence,
) -> CecMonitorContent:
    try:
        return _parse_cec_monitor_content(section)
    except CecTracePolicyError as error:
        raise PassiveEvidencePolicyError(
            "CEC trace violates passive-capture policy: %s" % error,
            envelope,
            ready,
        ) from error
    except CecTraceProtocolError as error:
        raise PassiveEvidenceProtocolError(
            "CEC trace is malformed: %s" % error
        ) from error


def _parse_journal_section(
    section: bytes,
    label: str,
    envelope: bytes,
    ready: ReadyEvidence,
) -> WakeJournalContent:
    try:
        return _parse_safe_wake_journal(
            section,
            start_cursor=ready.start_cursor,
            boot_id=ready.boot_id,
            invocation_id=ready.service.invocation_id,
            main_pid=ready.service.main_pid,
        )
    except WakeJournalPolicyError as error:
        raise PassiveEvidencePolicyError(
            "%s wake journal violates capture policy: %s" % (label, error),
            envelope,
            ready,
        ) from error
    except WakeJournalProtocolError as error:
        raise PassiveEvidenceProtocolError(
            "%s wake journal is malformed: %s" % (label, error)
        ) from error


def _require_journal_continuity(
    live: WakeJournalContent,
    final: WakeJournalContent,
    start_cursor: str,
) -> None:
    live_count = len(live.records)
    if live_count > len(final.records):
        raise PassiveEvidenceContinuityError(
            "live journal is longer than final journal"
        )
    if final.records[:live_count] != live.records:
        raise PassiveEvidenceContinuityError(
            "live journal is not a prefix of final journal"
        )
    if live_count:
        if final.records[live_count - 1].cursor != live.terminal_cursor:
            raise PassiveEvidenceContinuityError(
                "final journal does not preserve the live terminal cursor"
            )
    elif live.terminal_cursor != start_cursor:
        raise PassiveEvidenceContinuityError(
            "empty live journal did not preserve the READY cursor"
        )
    if (
        live_count == len(final.records)
        and live.terminal_cursor != final.terminal_cursor
    ):
        raise PassiveEvidenceContinuityError(
            "equal journal slices have different terminal cursors"
        )


def _decode_canonical_document(raw: bytes) -> Dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("passive evidence must be bytes")
    if not raw:
        raise PassiveEvidenceProtocolError("passive evidence is empty")
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise PassiveEvidenceProtocolError(
            "passive evidence exceeded its byte bound"
        )
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise PassiveEvidenceProtocolError(
            "passive evidence is not strict UTF-8"
        ) from error
    try:
        document = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
            parse_int=_parse_bounded_json_integer,
        )
    except PassiveEvidenceProtocolError:
        raise
    except (ValueError, RecursionError) as error:
        raise PassiveEvidenceProtocolError(
            "passive evidence is not strict JSON"
        ) from error
    try:
        _reject_surrogates(document)
    except RecursionError as error:
        raise PassiveEvidenceProtocolError(
            "passive evidence exceeded its nesting bound"
        ) from error
    try:
        canonical = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise PassiveEvidenceProtocolError(
            "passive evidence cannot be canonically serialized"
        ) from error
    if raw != canonical:
        raise PassiveEvidenceProtocolError(
            "passive evidence JSON is not canonical"
        )
    if not isinstance(document, dict):
        raise PassiveEvidenceProtocolError(
            "passive evidence JSON must be an object"
        )
    return document


def _object_without_duplicates(
    pairs: list[Tuple[str, Any]],
) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PassiveEvidenceProtocolError(
                "passive evidence contains duplicate JSON keys"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PassiveEvidenceProtocolError(
        "passive evidence contains non-finite JSON number %s" % value
    )


def _parse_bounded_json_integer(value: str) -> int:
    # Every integer in the exact envelope schema is an unsigned 64-bit value.
    # Reject longer tokens before Python 3.9 constructs an arbitrarily large
    # integer; newer interpreters' process-global digit limit is not a protocol
    # guarantee.
    if len(value) > len(str(MAX_UINT64)):
        raise PassiveEvidenceProtocolError(
            "passive evidence contains an oversized JSON integer"
        )
    return int(value, 10)


def _reject_surrogates(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise PassiveEvidenceProtocolError(
                "passive evidence contains a Unicode surrogate"
            )
        return
    if isinstance(value, list):
        for item in value:
            _reject_surrogates(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_surrogates(key)
            _reject_surrogates(item)


def _decode_space_line(
    raw: bytes,
    label: str,
    *,
    maximum: int,
    field_count: int,
) -> Tuple[str, ...]:
    if not isinstance(raw, bytes):
        raise TypeError("%s must be bytes" % label)
    if not raw or len(raw) > maximum:
        raise PassiveEvidenceProtocolError(
            "%s has an invalid byte length" % label
        )
    if b"\r" in raw or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise PassiveEvidenceProtocolError(
            "%s is not one exact LF-terminated line" % label
        )
    line = raw[:-1]
    try:
        decoded = line.decode("ascii", "strict")
    except UnicodeDecodeError as error:
        raise PassiveEvidenceProtocolError(
            "%s is not ASCII" % label
        ) from error
    fields = tuple(decoded.split(" "))
    if len(fields) != field_count or any(not field for field in fields):
        raise PassiveEvidenceProtocolError(
            "%s does not have its exact fields" % label
        )
    return fields


def _parse_identifier(value: str, name: str) -> str:
    try:
        _require_identifier(value, name)
    except (TypeError, ValueError) as error:
        raise PassiveEvidenceProtocolError(
            "%s is not a nonzero lowercase 32-hex identifier" % name
        ) from error
    return value


def _require_identifier(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not _ID_PATTERN.fullmatch(value)
        or value == "0" * NONCE_HEX_LENGTH
    ):
        raise ValueError(
            "%s must be a nonzero lowercase 32-hex identifier" % name
        )


def _parse_decimal(
    value: str,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
        raise PassiveEvidenceProtocolError(
            "%s is not a canonical decimal" % name
        )
    parsed = int(value, 10)
    if not minimum <= parsed <= maximum:
        raise PassiveEvidenceProtocolError("%s is outside its bound" % name)
    return parsed


def _require_integer(
    value: int,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be an integer" % name)
    if not minimum <= value <= maximum:
        raise ValueError("%s is outside its bound" % name)


def _require_token(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError("%s must be bounded visible ASCII" % name)


def _decode_cursor_base64(value: str) -> str:
    cursor_raw = _decode_canonical_base64(
        value,
        "READY cursor",
        MAX_CURSOR_BYTES,
    )
    try:
        cursor = cursor_raw.decode("ascii", "strict")
    except UnicodeDecodeError as error:
        raise PassiveEvidenceProtocolError(
            "READY cursor is not ASCII"
        ) from error
    _require_cursor(cursor, "READY cursor")
    return cursor


def _require_cursor(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8", "surrogatepass")) > MAX_CURSOR_BYTES
        or any(not 0x20 <= ord(character) <= 0x7E for character in value)
    ):
        raise PassiveEvidenceProtocolError(
            "%s is not bounded printable ASCII" % name
        )


def _decode_json_base64(
    value: Any,
    name: str,
    maximum: int,
) -> bytes:
    text = _json_text(value, name)
    return _decode_canonical_base64(text, name, maximum)


def _decode_canonical_base64(
    value: str,
    name: str,
    maximum: int,
) -> bytes:
    if not isinstance(value, str) or not value:
        raise PassiveEvidenceProtocolError(
            "%s must be a nonempty base64 string" % name
        )
    if len(value) > 4 * ((maximum + 2) // 3):
        raise PassiveEvidenceProtocolError(
            "%s exceeds its decoded byte bound" % name
        )
    try:
        encoded = value.encode("ascii", "strict")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise PassiveEvidenceProtocolError(
            "%s is not strict base64" % name
        ) from error
    if len(decoded) > maximum:
        raise PassiveEvidenceProtocolError(
            "%s exceeds its decoded byte bound" % name
        )
    if base64.b64encode(decoded) != encoded:
        raise PassiveEvidenceProtocolError(
            "%s is not canonical base64" % name
        )
    return decoded


def _json_object(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise PassiveEvidenceProtocolError("%s must be an object" % name)
    return value


def _json_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise PassiveEvidenceProtocolError("%s must be a string" % name)
    return value


def _json_identifier(value: Any, name: str) -> str:
    text = _json_text(value, name)
    return _parse_identifier(text, name)


def _json_integer(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        _require_integer(
            value,
            name,
            minimum=minimum,
            maximum=maximum,
        )
    except (TypeError, ValueError) as error:
        raise PassiveEvidenceProtocolError(
            "%s is not a bounded JSON integer" % name
        ) from error
    return value


def _require_exact_keys(
    value: Dict[str, Any],
    expected: frozenset[str],
    name: str,
) -> None:
    if frozenset(value) != expected:
        raise PassiveEvidenceProtocolError(
            "%s does not have its exact keys" % name
        )


def _service_from_json(value: Any, name: str) -> ServiceIdentity:
    service = _json_object(value, name)
    _require_exact_keys(service, _SERVICE_KEYS, name)
    try:
        return ServiceIdentity(
            unit_id=_json_text(service["unit_id"], "%s unit_id" % name),
            load_state=_json_text(
                service["load_state"],
                "%s load_state" % name,
            ),
            active_state=_json_text(
                service["active_state"],
                "%s active_state" % name,
            ),
            sub_state=_json_text(
                service["sub_state"],
                "%s sub_state" % name,
            ),
            invocation_id=_json_identifier(
                service["invocation_id"],
                "%s invocation_id" % name,
            ),
            main_pid=_json_integer(
                service["main_pid"],
                "%s main_pid" % name,
                minimum=1,
                maximum=MAX_PID,
            ),
            n_restarts=_json_integer(
                service["n_restarts"],
                "%s n_restarts" % name,
                minimum=0,
                maximum=MAX_UINT64,
            ),
            exec_start_usec=_json_integer(
                service["exec_start_usec"],
                "%s exec_start_usec" % name,
                minimum=1,
                maximum=MAX_UINT64,
            ),
            active_enter_usec=_json_integer(
                service["active_enter_usec"],
                "%s active_enter_usec" % name,
                minimum=1,
                maximum=MAX_UINT64,
            ),
        )
    except PassiveEvidenceProtocolError:
        raise
    except (TypeError, ValueError) as error:
        raise PassiveEvidenceProtocolError(
            "%s has an invalid identity or state" % name
        ) from error
