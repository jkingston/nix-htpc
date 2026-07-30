"""Strict content parsing for passive ``cec-ctl --monitor`` output.

This module proves the structure and traffic policy of captured text. The
capture transport must separately prove that ``cec-ctl`` exited successfully
after its requested monitor duration; the text format has no completion
marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


MAX_TRACE_BYTES = 1024 * 1024
MAX_LINE_BYTES = 4096
MAX_FRAMES = 4096
MAX_CEC_MESSAGE_BYTES = 16
TV_LOGICAL_ADDRESS = 0
PI_LOGICAL_ADDRESS = 1
PI_LOGICAL_ADDRESS_MASK = 1 << PI_LOGICAL_ADDRESS
GIVE_DEVICE_POWER_STATUS = 0x8F
PI_LOGICAL_ADDRESS_NAME = "Recording Device 1"
TV_LOGICAL_ADDRESS_NAME = "TV"
GIVE_DEVICE_POWER_STATUS_NAME = "GIVE_DEVICE_POWER_STATUS"

TRANSMITTED = "transmitted"
RECEIVED = "received"

# Exact ``cec_la2s`` output in the pinned v4l-utils decoder.
_LOGICAL_ADDRESS_NAMES = (
    "TV",
    "Recording Device 1",
    "Recording Device 2",
    "Tuner 1",
    "Playback Device 1",
    "Audio System",
    "Tuner 2",
    "Tuner 3",
    "Playback Device 2",
    "Recording Device 3",
    "Tuner 4",
    "Playback Device 3",
    "Backup 1",
    "Backup 2",
    "Specific",
    "Unregistered",
)
_VALIDATED_OPCODE_NAMES = {
    0x82: "ACTIVE_SOURCE",
    GIVE_DEVICE_POWER_STATUS: GIVE_DEVICE_POWER_STATUS_NAME,
    0x90: "REPORT_POWER_STATUS",
}
_INITIAL_EVENT_PATTERN = re.compile(
    r"\AInitial Event: State Change: "
    r"PA: ([0-9a-f](?:\.[0-9a-f]){3}), "
    r"LA mask: 0x([0-9a-f]{4})\Z"
)
_DESCRIPTION_PATTERN = re.compile(
    r"\A(Transmitted by|Received from) "
    r"(.{1,128}?) to (.{1,128}?) "
    r"\(((?:1[0-5]|[0-9])) to ((?:1[0-5]|[0-9]))\): "
    r"([A-Z][A-Z0-9_]*) \(0x([0-9a-f]{2})\)(:?)\Z"
)
_POLL_DESCRIPTION_PATTERN = re.compile(
    r"\A(Transmitted by|Received from) "
    r"(.{1,128}?) to (.{1,128}?) "
    r"\(((?:1[0-5]|[0-9])) to ((?:1[0-5]|[0-9]))\): POLL\Z"
)
_RAW_PATTERN = re.compile(
    r"\A\tRaw: "
    r"((?:0x[0-9a-f]{2})(?: 0x[0-9a-f]{2})*) "
    r"\(([\x20-\x7f]*)\)\Z"
)
_OPCODE_NAME_PATTERN = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")
_LOSS_PATTERN = re.compile(
    r"\b(?:drop|dropped|lost|overflow|overrun)\b",
    re.IGNORECASE,
)


class CecTraceError(Exception):
    """Base class for rejected passive CEC evidence."""


class CecTraceProtocolError(CecTraceError):
    """The monitor text is malformed, structurally incomplete, or lossy."""


class CecTracePolicyError(CecTraceError):
    """A well-formed trace contains forbidden Pi-originated traffic."""


@dataclass(frozen=True)
class _Description:
    direction: str
    source: str
    destination: str
    initiator: int
    destination_address: int
    opcode_name: Optional[str]
    opcode: Optional[int]
    has_details: bool


@dataclass(frozen=True)
class CecInitialEvent:
    """The adapter state reported when passive monitoring starts."""

    physical_address: tuple[int, int, int, int]
    logical_address_mask: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.physical_address, tuple)
            or len(self.physical_address) != 4
            or any(
                isinstance(part, bool)
                or not isinstance(part, int)
                or not 0 <= part <= 0xF
                for part in self.physical_address
            )
        ):
            raise ValueError(
                "physical_address must contain four hexadecimal nibbles"
            )
        _require_integer(
            self.logical_address_mask,
            "logical_address_mask",
            minimum=1,
            maximum=0xFFFF,
        )


@dataclass(frozen=True)
class CecFrame:
    """One normalized CEC message and its decoder evidence."""

    direction: str
    source: str
    destination: str
    initiator: int
    destination_address: int
    opcode_name: Optional[str]
    opcode: Optional[int]
    operands: tuple[int, ...]
    details: tuple[str, ...]
    raw_message: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.direction not in (TRANSMITTED, RECEIVED):
            raise ValueError("direction must be transmitted or received")
        _require_text(self.source, "source")
        _require_text(self.destination, "destination")
        _require_integer(self.initiator, "initiator", minimum=0, maximum=0xF)
        _require_integer(
            self.destination_address,
            "destination_address",
            minimum=0,
            maximum=0xF,
        )
        if self.opcode_name is not None and (
            not isinstance(self.opcode_name, str)
            or not _OPCODE_NAME_PATTERN.fullmatch(self.opcode_name)
        ):
            raise ValueError(
                "opcode_name must be absent or canonical uppercase text"
            )
        if self.opcode is not None:
            _require_integer(self.opcode, "opcode", minimum=0, maximum=0xFF)
        _require_byte_tuple(self.operands, "operands")
        if not isinstance(self.details, tuple):
            raise TypeError("details must be a tuple")
        for detail in self.details:
            _require_text(detail, "detail")
        _require_byte_tuple(self.raw_message, "raw_message")
        if not 1 <= len(self.raw_message) <= MAX_CEC_MESSAGE_BYTES:
            raise ValueError(
                "raw_message must contain between 1 and 16 bytes"
            )

        expected_header = (self.initiator << 4) | self.destination_address
        if self.opcode is None:
            if self.opcode_name is not None or self.operands:
                raise ValueError(
                    "CEC polls must have no opcode name or operands"
                )
            expected_raw = (expected_header,)
        else:
            if self.opcode_name is None:
                raise ValueError(
                    "CEC messages with an opcode require its decoded name"
                )
            expected_raw = (expected_header, self.opcode, *self.operands)
        if self.raw_message != expected_raw:
            raise ValueError(
                "raw_message must match the normalized header and opcode"
            )


@dataclass(frozen=True, init=False)
class CecMonitorContent:
    """Accepted raw monitor content; temporal completion is external."""

    raw: bytes
    initial_event: CecInitialEvent
    frames: tuple[CecFrame, ...]

    def __init__(self, raw: bytes):
        initial_event, frames = _parse_trace(raw)
        object.__setattr__(self, "raw", raw)
        object.__setattr__(self, "initial_event", initial_event)
        object.__setattr__(self, "frames", frames)
        _require_safe_policy(self)


def parse_cec_monitor_content(raw: bytes) -> CecMonitorContent:
    """Parse monitor text and enforce policy on all content it contains."""

    return CecMonitorContent(raw)


def _parse_trace(
    raw: bytes,
) -> tuple[CecInitialEvent, tuple[CecFrame, ...]]:
    lines = _decode_lines(raw)
    if len(lines) < 3 or lines[:2] != ("", ""):
        raise CecTraceProtocolError(
            "CEC trace is missing its exact monitor preamble"
        )
    initial_event = _parse_initial_event(lines[2])
    frames = []
    index = 3

    while index < len(lines):
        if len(frames) >= MAX_FRAMES:
            raise CecTraceProtocolError(
                "CEC trace exceeded its frame-count bound"
            )
        line = lines[index]
        if not line:
            raise CecTraceProtocolError(
                "CEC trace contains an unexpected blank line"
            )
        if line.startswith("Initial Event:"):
            raise CecTraceProtocolError(
                "CEC trace contains duplicate monitor readiness"
            )
        if "State Change:" in line:
            raise CecTraceProtocolError(
                "CEC adapter state changed during the trace"
            )

        description = _parse_description(line)
        index += 1
        details = []
        while index < len(lines) and lines[index].startswith("\t"):
            if lines[index].startswith("\tRaw:"):
                break
            details.append(_parse_detail(lines[index]))
            index += 1

        if index >= len(lines) or not lines[index].startswith("\tRaw:"):
            raise CecTraceProtocolError(
                "CEC message is missing its Raw line"
            )
        raw_message = _parse_raw_message(lines[index])
        index += 1

        if description.has_details != bool(details):
            raise CecTraceProtocolError(
                "CEC description/detail framing is inconsistent"
            )

        frame = _make_frame(
            description,
            tuple(details),
            raw_message,
            initial_event,
        )
        frames.append(frame)

    return initial_event, tuple(frames)


def _decode_lines(raw: bytes) -> tuple[str, ...]:
    if not isinstance(raw, bytes):
        raise TypeError("CEC trace must be bytes")
    if not raw:
        raise CecTraceProtocolError("CEC trace is empty")
    if len(raw) > MAX_TRACE_BYTES:
        raise CecTraceProtocolError("CEC trace exceeded its byte bound")
    if not raw.endswith(b"\n"):
        raise CecTraceProtocolError(
            "CEC trace must end at a complete newline"
        )
    try:
        text = raw.decode("ascii", "strict")
    except UnicodeDecodeError as error:
        raise CecTraceProtocolError(
            "CEC trace is not strict ASCII"
        ) from error
    lines = tuple(text[:-1].split("\n"))
    for line in lines:
        if len(line.encode("ascii")) > MAX_LINE_BYTES:
            raise CecTraceProtocolError(
                "CEC trace line exceeded its byte bound"
            )
        _require_printable_line(line)
        if _LOSS_PATTERN.search(line):
            raise CecTraceProtocolError(
                "CEC monitor reported dropped or lost data"
            )
    return lines


def _parse_initial_event(line: str) -> CecInitialEvent:
    matched = _INITIAL_EVENT_PATTERN.fullmatch(line)
    if matched is None:
        raise CecTraceProtocolError(
            "CEC trace does not begin with exact monitor readiness"
        )
    physical_address = tuple(
        int(part, 16) for part in matched.group(1).split(".")
    )
    logical_address_mask = int(matched.group(2), 16)
    try:
        return CecInitialEvent(
            physical_address=physical_address,
            logical_address_mask=logical_address_mask,
        )
    except (TypeError, ValueError) as error:
        raise CecTraceProtocolError(str(error)) from error


def _parse_description(line: str) -> _Description:
    matched = _DESCRIPTION_PATTERN.fullmatch(line)
    is_poll = matched is None
    if is_poll:
        matched = _POLL_DESCRIPTION_PATTERN.fullmatch(line)
    if matched is None:
        raise CecTraceProtocolError(
            "CEC trace contains an unknown message description"
        )
    direction = (
        TRANSMITTED
        if matched.group(1) == "Transmitted by"
        else RECEIVED
    )
    initiator = int(matched.group(4), 10)
    destination_address = int(matched.group(5), 10)
    if not 0 <= initiator <= 0xF or not 0 <= destination_address <= 0xF:
        raise CecTraceProtocolError(
            "CEC description contains an invalid logical address"
        )
    _require_address_name(
        matched.group(2),
        initiator,
        "source",
    )
    _require_address_name(
        matched.group(3),
        destination_address,
        "destination",
    )
    opcode_name = None
    opcode = None
    has_details = False
    if not is_poll:
        opcode_name = matched.group(6)
        opcode = int(matched.group(7), 16)
        expected_opcode_name = _VALIDATED_OPCODE_NAMES.get(opcode)
        if (
            expected_opcode_name is not None
            and opcode_name != expected_opcode_name
        ):
            raise CecTraceProtocolError(
                "CEC description opcode name contradicts its value"
            )
        has_details = matched.group(8) == ":"
    return _Description(
        direction=direction,
        source=matched.group(2),
        destination=matched.group(3),
        initiator=initiator,
        destination_address=destination_address,
        opcode_name=opcode_name,
        opcode=opcode,
        has_details=has_details,
    )


def _parse_detail(line: str) -> str:
    if not line.startswith("\t") or line.startswith("\tRaw:"):
        raise CecTraceProtocolError("CEC detail line is malformed")
    detail = line[1:]
    if not detail:
        raise CecTraceProtocolError("CEC detail line is empty")
    return detail


def _parse_raw_message(line: str) -> tuple[int, ...]:
    matched = _RAW_PATTERN.fullmatch(line)
    if matched is None:
        raise CecTraceProtocolError("CEC Raw line is malformed")
    raw_message = tuple(
        int(token[2:], 16) for token in matched.group(1).split(" ")
    )
    if not 1 <= len(raw_message) <= MAX_CEC_MESSAGE_BYTES:
        raise CecTraceProtocolError(
            "CEC Raw message must contain between 1 and 16 bytes"
        )
    rendering = "".join(
        chr(value) if 0x20 <= value <= 0x7F else " "
        for value in raw_message
    )
    if matched.group(2) != rendering:
        raise CecTraceProtocolError(
            "CEC Raw rendering does not match its bytes"
        )
    return raw_message


def _make_frame(
    description: _Description,
    details: tuple[str, ...],
    raw_message: tuple[int, ...],
    initial_event: CecInitialEvent,
) -> CecFrame:
    raw_header = raw_message[0]
    raw_initiator = raw_header >> 4
    raw_destination = raw_header & 0xF
    if (
        raw_initiator != description.initiator
        or raw_destination != description.destination_address
    ):
        raise CecTraceProtocolError(
            "CEC Raw header does not match its description"
        )
    if description.opcode is None:
        if len(raw_message) != 1:
            raise CecTraceProtocolError(
                "CEC poll Raw message must contain only its header"
            )
    elif len(raw_message) < 2 or raw_message[1] != description.opcode:
        raise CecTraceProtocolError(
            "CEC Raw opcode does not match its description"
        )

    local_mask = initial_event.logical_address_mask
    if description.direction == TRANSMITTED:
        if not local_mask & (1 << description.initiator):
            raise CecTraceProtocolError(
                "CEC transmitted source is absent from the initial LA mask"
            )
    else:
        if local_mask & (1 << description.initiator):
            raise CecTraceProtocolError(
                "CEC received source is present in the initial LA mask"
            )
        if (
            description.destination_address != 0xF
            and not local_mask
            & (1 << description.destination_address)
        ):
            raise CecTraceProtocolError(
                "CEC received destination is absent from the initial LA mask"
            )

    try:
        return CecFrame(
            direction=description.direction,
            source=description.source,
            destination=description.destination,
            initiator=description.initiator,
            destination_address=description.destination_address,
            opcode_name=description.opcode_name,
            opcode=description.opcode,
            operands=raw_message[2:],
            details=details,
            raw_message=raw_message,
        )
    except (TypeError, ValueError) as error:
        raise CecTraceProtocolError(str(error)) from error


def _require_safe_policy(trace: CecMonitorContent) -> None:
    if (
        trace.initial_event.logical_address_mask
        != PI_LOGICAL_ADDRESS_MASK
    ):
        raise CecTracePolicyError(
            "CEC initial logical-address mask does not match the appliance"
        )
    allowed_poll_seen = False
    for index, frame in enumerate(trace.frames):
        if frame.direction != TRANSMITTED:
            continue
        allowed = (
            frame.source == PI_LOGICAL_ADDRESS_NAME
            and frame.destination == TV_LOGICAL_ADDRESS_NAME
            and frame.initiator == PI_LOGICAL_ADDRESS
            and frame.destination_address == TV_LOGICAL_ADDRESS
            and frame.opcode_name == GIVE_DEVICE_POWER_STATUS_NAME
            and frame.opcode == GIVE_DEVICE_POWER_STATUS
            and not frame.operands
            and not frame.details
            and frame.raw_message
            == (
                (PI_LOGICAL_ADDRESS << 4) | TV_LOGICAL_ADDRESS,
                GIVE_DEVICE_POWER_STATUS,
            )
        )
        if not allowed:
            opcode = (
                "POLL"
                if frame.opcode is None
                else "0x%02x" % frame.opcode
            )
            raise CecTracePolicyError(
                "forbidden Pi CEC transmission at frame %d: "
                "destination %d opcode %s operands %d"
                % (
                    index,
                    frame.destination_address,
                    opcode,
                    len(frame.operands),
                )
            )
        allowed_poll_seen = True
    if not allowed_poll_seen:
        raise CecTracePolicyError(
            "CEC trace contains no allowed Pi power-status poll"
        )


def _require_printable_line(line: str) -> None:
    is_raw_line = line.startswith("\tRaw: ")
    for index, character in enumerate(line):
        codepoint = ord(character)
        if character == "\t" and index == 0:
            continue
        if codepoint == 0x7F and is_raw_line:
            continue
        if not 0x20 <= codepoint <= 0x7E:
            raise CecTraceProtocolError(
                "CEC trace contains a non-printable character"
            )


def _require_address_name(
    actual_name: str,
    address: int,
    role: str,
) -> None:
    expected_name = (
        "all"
        if role == "destination" and address == 0xF
        else _LOGICAL_ADDRESS_NAMES[address]
    )
    if actual_name != expected_name:
        raise CecTraceProtocolError(
            "CEC description %s name contradicts its address" % role
        )


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError("%s must be non-empty text" % name)
    if any(not 0x20 <= ord(character) <= 0x7E for character in value):
        raise ValueError("%s must be printable ASCII" % name)


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
            "%s must be between %d and %d" % (name, minimum, maximum)
        )


def _require_byte_tuple(value: Any, name: str) -> None:
    if not isinstance(value, tuple):
        raise TypeError("%s must be a tuple" % name)
    for item in value:
        _require_integer(item, name, minimum=0, maximum=0xFF)
