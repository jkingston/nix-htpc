#!/usr/bin/env python3
"""Produce finite, passive evidence for headless Kodi/CEC verification.

The installed executable accepts no arguments and exposes no general command
runner. Nix substitutes every executable path with an immutable store path.
The successful path only reads the boot and wake-service identity, observes
CEC traffic, and performs finite global journal queries. ``journalctl --sync``
is the sole stateful operation: it flushes already-submitted journal data and
has no Kodi, HDMI, display, input, or CEC effect.
"""

from __future__ import annotations

import base64
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import BinaryIO, Callable, Dict, Optional, Protocol, Sequence


PROTOCOL_VERSION = "KODI-PASSIVE-EVIDENCE/1"

CEC_CTL = "@CEC_CTL@"
SYSTEMCTL = "@SYSTEMCTL@"
JOURNALCTL = "@JOURNALCTL@"

CEC_DEVICE = "/dev/cec0"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
SERVICE_UNIT = "cec-tv-wake.service"
SERVICE_LOAD_STATE = "loaded"
SERVICE_ACTIVE_STATE = "active"
SERVICE_SUB_STATE = "running"

MAX_READY_BYTES = 8192
MAX_ENVELOPE_BYTES = 5 * 1024 * 1024
MAX_CEC_BYTES = 1024 * 1024
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_COMMAND_BYTES = 8192
MAX_CURSOR_BYTES = 4096
MAX_CONTROL_BYTES = 128
MAX_CHILD_STDERR_BYTES = 4096
MAX_DIAGNOSTIC_BYTES = 512
MAX_UINT64 = (1 << 64) - 1
MAX_PID = (1 << 31) - 1
NONCE_HEX_LENGTH = 32

STARTUP_TIMEOUT_USEC = 5_000_000
READINESS_TIMEOUT_USEC = 2_000_000
COMMAND_TIMEOUT_USEC = 3_000_000
MAX_ACTION_WINDOW_USEC = 5_000_000
MIN_OBSERVATION_USEC = 8_000_000
MAX_SESSION_USEC = 30_000_000
MONITOR_DURATION_SECONDS = 20
CLEANUP_TIMEOUT_SECONDS = 1.0
IO_POLL_USEC = 50_000

_ID_PATTERN = re.compile(r"\A[0-9a-f]{32}\Z")
_UUID_PATTERN = re.compile(
    rb"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    rb"[0-9a-f]{4}-[0-9a-f]{12}\n\Z"
)
_DECIMAL_PATTERN = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
_CEC_READY_PATTERN = re.compile(
    rb"\AInitial Event: State Change: "
    rb"PA: [0-9a-f](?:\.[0-9a-f]){3}, "
    rb"LA mask: 0x[0-9a-f]{4}\n\Z"
)
_CURSOR_PREFIX = b"-- cursor: "

_SERVICE_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "InvocationID",
    "MainPID",
    "NRestarts",
    "ExecMainStartTimestampMonotonic",
    "ActiveEnterTimestampMonotonic",
)
_JOURNAL_FIELDS = (
    "MESSAGE,PRIORITY,_PID,_SYSTEMD_INVOCATION_ID,"
    "_SYSTEMD_UNIT,_UID,INVOCATION_ID,UNIT,"
    "OBJECT_SYSTEMD_UNIT,COREDUMP_UNIT"
)
_CHILD_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PAGER": "cat",
    "PATH": "/nonexistent",
    "SYSTEMD_COLORS": "0",
    "SYSTEMD_PAGER": "cat",
    "SYSTEMD_PAGERSECURE": "1",
    "TERM": "dumb",
}

SERVICE_COMMAND = (
    SYSTEMCTL,
    "show",
    "--no-pager",
    *("--property=" + name for name in _SERVICE_PROPERTIES),
    SERVICE_UNIT,
)
CURSOR_COMMAND = (
    JOURNALCTL,
    "--boot",
    "--lines=0",
    "--output=json-seq",
    "--show-cursor",
    "--no-pager",
    "--quiet",
)
SYNC_COMMAND = (JOURNALCTL, "--sync")
MONITOR_COMMAND = (
    CEC_CTL,
    "-d",
    CEC_DEVICE,
    "--monitor",
    "--show-raw",
    "--skip-info",
    "--monitor-time",
    str(MONITOR_DURATION_SECONDS),
)


class ProducerError(Exception):
    """A bounded producer operation failed."""


@dataclass(frozen=True)
class ServiceIdentity:
    """One complete, healthy systemd service fence."""

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
        if (
            self.unit_id != SERVICE_UNIT
            or self.load_state != SERVICE_LOAD_STATE
            or self.active_state != SERVICE_ACTIVE_STATE
            or self.sub_state != SERVICE_SUB_STATE
        ):
            raise ValueError("wake service is not loaded, active, and running")
        _require_identifier(self.invocation_id, "service invocation ID")
        _require_integer(
            self.main_pid,
            "service main PID",
            minimum=1,
            maximum=MAX_PID,
        )
        _require_integer(
            self.n_restarts,
            "service restart count",
            minimum=0,
            maximum=MAX_UINT64,
        )
        _require_integer(
            self.exec_start_usec,
            "service execution start",
            minimum=1,
            maximum=MAX_UINT64,
        )
        _require_integer(
            self.active_enter_usec,
            "service active-enter time",
            minimum=1,
            maximum=MAX_UINT64,
        )
        if self.exec_start_usec > self.active_enter_usec:
            raise ValueError(
                "service execution start follows active-enter time"
            )


@dataclass(frozen=True)
class CaptureEvidence:
    """Complete normalized values used to build the canonical result body."""

    nonce: str
    start_cursor: str
    start_boot_id: str
    live_boot_id: str
    final_boot_id: str
    start_service: ServiceIdentity
    live_service: ServiceIdentity
    final_service: ServiceIdentity
    ready_usec: int
    finish_usec: int
    live_journal_usec: int
    monitor_exit_usec: int
    final_journal_usec: int
    complete_usec: int
    cec_trace: bytes
    live_journal: bytes
    final_journal: bytes


class Monitor(Protocol):
    def wait_ready(self, deadline: int) -> None: ...
    def wait_until(self, deadline: int) -> None: ...
    def require_alive(self) -> None: ...
    def wait_natural(self, deadline: int) -> bytes: ...
    def pump(self, timeout_usec: int = 0) -> None: ...
    def close(self) -> None: ...


class Runtime(Protocol):
    def monotonic_usec(self) -> int: ...
    def read_boot_id(
        self,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> str: ...
    def read_service(
        self,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> ServiceIdentity: ...
    def read_global_cursor(self, deadline: int) -> str: ...
    def start_monitor(self) -> Monitor: ...
    def sync_journal(self, deadline: int, monitor: Monitor) -> None: ...
    def read_journal(
        self,
        cursor: str,
        deadline: int,
        monitor: Monitor,
    ) -> bytes: ...


class Transport(Protocol):
    def read_start(self, deadline: int) -> str: ...
    def write_ready(
        self,
        line: bytes,
        deadline: int,
        monitor: Monitor,
    ) -> None: ...
    def read_finish_and_eof(
        self,
        nonce: str,
        deadline: int,
        monitor: Monitor,
    ) -> None: ...
    def write_result(
        self,
        header: bytes,
        body: bytes,
        deadline: int,
    ) -> None: ...


def collect_evidence(runtime: Runtime, transport: Transport) -> bytes:
    """Run the fixed passive capture state machine and emit its result."""

    started_usec = _now(runtime)
    startup_deadline = _bounded_add(
        started_usec,
        STARTUP_TIMEOUT_USEC,
    )
    nonce = transport.read_start(startup_deadline)

    start_boot_id = runtime.read_boot_id(startup_deadline)
    start_service = runtime.read_service(startup_deadline)
    start_cursor = runtime.read_global_cursor(startup_deadline)

    monitor = runtime.start_monitor()
    completed_naturally = False
    try:
        ready_deadline = min(
            startup_deadline,
            _bounded_add(
                _now(runtime, started_usec),
                READINESS_TIMEOUT_USEC,
            ),
        )
        monitor.wait_ready(ready_deadline)
        monitor.require_alive()

        ready_boot_id = runtime.read_boot_id(
            startup_deadline,
            monitor,
        )
        ready_service = runtime.read_service(
            startup_deadline,
            monitor,
        )
        _require_same_fence(
            start_boot_id,
            start_service,
            ready_boot_id,
            ready_service,
            "READY",
        )

        ready_usec = _now(runtime, started_usec)
        if start_service.active_enter_usec > ready_usec:
            raise ProducerError(
                "wake service active-enter time follows READY"
            )
        ready_line = _encode_ready_line(
            nonce,
            start_boot_id,
            start_service,
            start_cursor,
            ready_usec,
        )
        action_deadline = _bounded_add(
            ready_usec,
            MAX_ACTION_WINDOW_USEC,
        )
        transport.write_ready(
            ready_line,
            action_deadline,
            monitor,
        )
        transport.read_finish_and_eof(
            nonce,
            action_deadline,
            monitor,
        )
        finish_usec = _now(runtime, ready_usec)
        if finish_usec > action_deadline:
            raise ProducerError("FINISH exceeded the action window")

        observation_deadline = _bounded_add(
            ready_usec,
            MIN_OBSERVATION_USEC,
        )
        session_deadline = _bounded_add(
            ready_usec,
            MAX_SESSION_USEC,
        )
        monitor.wait_until(observation_deadline)
        monitor.require_alive()

        live_pre_boot = runtime.read_boot_id(
            session_deadline,
            monitor,
        )
        live_pre_service = runtime.read_service(
            session_deadline,
            monitor,
        )
        _require_same_fence(
            start_boot_id,
            start_service,
            live_pre_boot,
            live_pre_service,
            "pre-live",
        )
        runtime.sync_journal(session_deadline, monitor)
        live_journal = runtime.read_journal(
            start_cursor,
            session_deadline,
            monitor,
        )
        monitor.require_alive()
        live_boot_id = runtime.read_boot_id(
            session_deadline,
            monitor,
        )
        live_service = runtime.read_service(
            session_deadline,
            monitor,
        )
        monitor.require_alive()
        _require_same_fence(
            start_boot_id,
            start_service,
            live_boot_id,
            live_service,
            "live",
        )
        live_journal_usec = _now(runtime, observation_deadline)

        cec_trace = monitor.wait_natural(session_deadline)
        completed_naturally = True
        monitor_exit_usec = _now(runtime, live_journal_usec)
        if monitor_exit_usec <= live_journal_usec:
            raise ProducerError(
                "monitor exit did not follow the live journal fence"
            )

        final_pre_boot = runtime.read_boot_id(session_deadline)
        final_pre_service = runtime.read_service(session_deadline)
        _require_same_fence(
            start_boot_id,
            start_service,
            final_pre_boot,
            final_pre_service,
            "pre-final",
        )
        runtime.sync_journal(session_deadline, monitor)
        final_journal = runtime.read_journal(
            start_cursor,
            session_deadline,
            monitor,
        )
        final_boot_id = runtime.read_boot_id(session_deadline)
        final_service = runtime.read_service(session_deadline)
        _require_same_fence(
            start_boot_id,
            start_service,
            final_boot_id,
            final_service,
            "final",
        )
        final_journal_usec = _now(runtime, monitor_exit_usec)
        complete_usec = _now(runtime, final_journal_usec)
        if complete_usec > session_deadline:
            raise ProducerError("passive evidence session exceeded its bound")

        evidence = CaptureEvidence(
            nonce=nonce,
            start_cursor=start_cursor,
            start_boot_id=start_boot_id,
            live_boot_id=live_boot_id,
            final_boot_id=final_boot_id,
            start_service=start_service,
            live_service=live_service,
            final_service=final_service,
            ready_usec=ready_usec,
            finish_usec=finish_usec,
            live_journal_usec=live_journal_usec,
            monitor_exit_usec=monitor_exit_usec,
            final_journal_usec=final_journal_usec,
            complete_usec=complete_usec,
            cec_trace=cec_trace,
            live_journal=live_journal,
            final_journal=final_journal,
        )
        body = encode_evidence(evidence)
        header = _encode_result_header(nonce, len(body))
        transport.write_result(header, body, session_deadline)
        return body
    except BaseException as primary:
        try:
            monitor.close()
        except BaseException as cleanup_error:
            raise primary from cleanup_error
        raise
    finally:
        if completed_naturally:
            monitor.close()


def produce(runtime: Runtime, transport: Transport) -> bytes:
    """Compatibility name for the semantic producer operation."""

    return collect_evidence(runtime, transport)


def encode_evidence(evidence: CaptureEvidence) -> bytes:
    """Build the frozen canonical JSON wire body."""

    if not isinstance(evidence, CaptureEvidence):
        raise TypeError("evidence must be CaptureEvidence")
    document = {
        "boot_ids": {
            "final": evidence.final_boot_id,
            "live": evidence.live_boot_id,
            "start": evidence.start_boot_id,
        },
        "cec_trace_b64": _base64(evidence.cec_trace),
        "final_journal_b64": _base64(evidence.final_journal),
        "live_journal_b64": _base64(evidence.live_journal),
        "nonce": evidence.nonce,
        "services": {
            "final": _service_document(evidence.final_service),
            "live": _service_document(evidence.live_service),
            "start": _service_document(evidence.start_service),
        },
        "start_cursor": evidence.start_cursor,
        "timing_usec": {
            "complete": evidence.complete_usec,
            "final_journal": evidence.final_journal_usec,
            "finish": evidence.finish_usec,
            "live_journal": evidence.live_journal_usec,
            "monitor_exit": evidence.monitor_exit_usec,
            "ready": evidence.ready_usec,
        },
        "version": PROTOCOL_VERSION,
    }
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ProducerError("evidence could not be encoded") from error
    if not encoded or len(encoded) > MAX_ENVELOPE_BYTES:
        raise ProducerError("evidence envelope exceeded its byte bound")
    return encoded


def _encode_ready_line(
    nonce: str,
    boot_id: str,
    service: ServiceIdentity,
    start_cursor: str,
    ready_usec: int,
) -> bytes:
    _require_identifier(nonce, "nonce")
    _require_identifier(boot_id, "boot ID")
    _require_cursor(start_cursor)
    _require_integer(
        ready_usec,
        "READY timestamp",
        minimum=0,
        maximum=MAX_UINT64,
    )
    fields = (
        PROTOCOL_VERSION,
        "READY",
        nonce,
        boot_id,
        service.unit_id,
        service.load_state,
        service.active_state,
        service.sub_state,
        service.invocation_id,
        str(service.main_pid),
        str(service.n_restarts),
        str(service.exec_start_usec),
        str(service.active_enter_usec),
        str(ready_usec),
        base64.b64encode(start_cursor.encode("ascii")).decode("ascii"),
    )
    encoded = (" ".join(fields) + "\n").encode("ascii")
    if len(encoded) > MAX_READY_BYTES:
        raise ProducerError("READY line exceeded its byte bound")
    return encoded


def _encode_result_header(nonce: str, length: int) -> bytes:
    _require_identifier(nonce, "nonce")
    _require_integer(
        length,
        "result length",
        minimum=1,
        maximum=MAX_ENVELOPE_BYTES,
    )
    return (
        "%s RESULT %s %d\n"
        % (PROTOCOL_VERSION, nonce, length)
    ).encode("ascii")


def _service_document(service: ServiceIdentity) -> Dict[str, object]:
    if not isinstance(service, ServiceIdentity):
        raise TypeError("service must be ServiceIdentity")
    return {
        "active_enter_usec": service.active_enter_usec,
        "active_state": service.active_state,
        "exec_start_usec": service.exec_start_usec,
        "invocation_id": service.invocation_id,
        "load_state": service.load_state,
        "main_pid": service.main_pid,
        "n_restarts": service.n_restarts,
        "sub_state": service.sub_state,
        "unit_id": service.unit_id,
    }


def _base64(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("captured content must be bytes")
    return base64.b64encode(value).decode("ascii")


def _require_same_fence(
    expected_boot: str,
    expected_service: ServiceIdentity,
    observed_boot: str,
    observed_service: ServiceIdentity,
    label: str,
) -> None:
    if (
        observed_boot != expected_boot
        or observed_service != expected_service
    ):
        raise ProducerError("%s boot/service identity changed" % label)


def _now(runtime: Runtime, previous: Optional[int] = None) -> int:
    value = runtime.monotonic_usec()
    try:
        _require_integer(
            value,
            "monotonic timestamp",
            minimum=0,
            maximum=MAX_UINT64,
        )
    except (TypeError, ValueError) as error:
        raise ProducerError(str(error)) from error
    if previous is not None and value < previous:
        raise ProducerError("monotonic clock moved backwards")
    return value


def _bounded_add(value: int, delta: int) -> int:
    if value > MAX_UINT64 - delta:
        raise ProducerError("monotonic deadline overflowed")
    return value + delta


def _require_identifier(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not _ID_PATTERN.fullmatch(value)
        or value == "0" * NONCE_HEX_LENGTH
    ):
        raise ValueError(
            "%s must be a nonzero lowercase 32-hex identifier" % label
        )


def _require_cursor(cursor: str) -> None:
    if (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > MAX_CURSOR_BYTES
        or any(not 0x20 <= ord(character) <= 0x7E for character in cursor)
    ):
        raise ValueError("journal cursor is not bounded printable ASCII")


def _require_integer(
    value: int,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s must be an integer" % label)
    if not minimum <= value <= maximum:
        raise ValueError("%s is outside its bound" % label)


def _parse_decimal(
    value: str,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
        raise ProducerError("%s is not a canonical decimal" % label)
    parsed = int(value, 10)
    if not minimum <= parsed <= maximum:
        raise ProducerError("%s is outside its bound" % label)
    return parsed


def _parse_control_line(
    raw: bytes,
    operation: str,
    expected_nonce: Optional[str] = None,
) -> str:
    """Parse one exact START or FINISH control line."""

    if not isinstance(raw, bytes):
        raise TypeError("control line must be bytes")
    if (
        not raw
        or len(raw) > MAX_CONTROL_BYTES
        or b"\r" in raw
        or raw.count(b"\n") != 1
        or not raw.endswith(b"\n")
    ):
        raise ProducerError("control line has invalid framing")
    try:
        fields = raw[:-1].decode("ascii", "strict").split(" ")
    except UnicodeDecodeError as error:
        raise ProducerError("control line is not ASCII") from error
    if (
        len(fields) != 3
        or any(not field for field in fields)
        or fields[0] != PROTOCOL_VERSION
        or fields[1] != operation
    ):
        raise ProducerError("control line has the wrong protocol fields")
    nonce = fields[2]
    try:
        _require_identifier(nonce, "control nonce")
    except (TypeError, ValueError) as error:
        raise ProducerError(str(error)) from error
    if expected_nonce is not None and nonce != expected_nonce:
        raise ProducerError("FINISH nonce does not match START")
    return nonce


class _CapturedChild:
    """One fixed child with bounded nonblocking stdout and stderr."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        maximum_stdout: int,
        description: str,
        clock: Callable[[], int],
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        if (
            isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(argument, str) for argument in argv)
        ):
            raise ValueError("child argv must contain strings")
        self.argv = tuple(argv)
        self.maximum_stdout = maximum_stdout
        self.description = description
        self.clock = clock
        self.stdout_bytes = bytearray()
        self.stderr_bytes = bytearray()
        self.stdout_eof = False
        self.stderr_eof = False
        self.closed = False
        self.signalled = False
        self.completed_cleanly = False
        self.fully_reaped = False
        self.process = None
        self.stdout = None
        self.stderr = None
        try:
            self.process = popen_factory(
                list(self.argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=True,
                bufsize=0,
                env=dict(_CHILD_ENV),
            )
            self.stdout = self.process.stdout
            self.stderr = self.process.stderr
            if self.stdout is None or self.stderr is None:
                raise ProducerError(
                    "%s did not expose output pipes" % description
                )
            os.set_blocking(self.stdout.fileno(), False)
            os.set_blocking(self.stderr.fileno(), False)
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise primary from cleanup_error
            if isinstance(primary, ProducerError):
                raise
            if not isinstance(primary, Exception):
                raise
            raise ProducerError(
                "%s could not start: %s" % (description, primary)
            ) from primary

    @property
    def returncode(self) -> Optional[int]:
        if self.process is None:
            return None
        return self.process.poll()

    def pump(self, timeout_usec: int = 0) -> bool:
        if self.closed:
            return False
        timeout_usec = max(0, timeout_usec)
        readable = []
        if not self.stdout_eof and self.stdout is not None:
            readable.append((self.stdout, "stdout"))
        if not self.stderr_eof and self.stderr is not None:
            readable.append((self.stderr, "stderr"))
        if not readable:
            if timeout_usec:
                time.sleep(timeout_usec / 1_000_000)
            return False

        with selectors.DefaultSelector() as selector:
            for pipe, label in readable:
                selector.register(pipe, selectors.EVENT_READ, label)
            events = selector.select(timeout_usec / 1_000_000)
        for key, _ in events:
            self._read_pipe(key.fileobj, key.data)
        return bool(events)

    def require_alive(self) -> None:
        self.pump(0)
        if self.returncode is not None:
            raise ProducerError(
                "%s exited before its required lifetime ended"
                % self.description
            )

    def wait(
        self,
        deadline: int,
        companion: Optional[Monitor] = None,
    ) -> bytes:
        while not self._fully_drained():
            if companion is not None:
                companion.pump(0)
            remaining = _remaining_usec(self.clock, deadline)
            self.pump(min(IO_POLL_USEC, remaining))
        returncode = self.returncode
        self.fully_reaped = True
        if returncode != 0:
            raise ProducerError(
                "%s exited with status %s"
                % (self.description, returncode)
            )
        if self.stderr_bytes:
            raise ProducerError("%s wrote to stderr" % self.description)
        self.completed_cleanly = True
        return bytes(self.stdout_bytes)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        process = self.process
        cleanup_error = None
        if (
            process is not None
            and not self.completed_cleanly
            and not self.fully_reaped
        ):
            try:
                self._signal_group(signal.SIGTERM)
                if process.poll() is None:
                    try:
                        process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired:
                        pass
                if self._group_exists():
                    self._signal_group(signal.SIGKILL)
                if process.poll() is None:
                    process.wait(timeout=CLEANUP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                cleanup_error = ProducerError(
                    "%s could not be reaped" % self.description
                )
            except ProducerError as error:
                cleanup_error = error
        for pipe in (self.stdout, self.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        if cleanup_error is not None:
            raise cleanup_error

    def _signal_group(self, signal_number: int) -> None:
        process = self.process
        process_id = getattr(process, "pid", None)
        if (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
        ):
            raise ProducerError(
                "%s has no safe process-group identity" % self.description
            )
        self.signalled = True
        try:
            os.killpg(process_id, signal_number)
        except ProcessLookupError:
            return
        except OSError as error:
            raise ProducerError(
                "%s process-group signal failed" % self.description
            ) from error

    def _group_exists(self) -> bool:
        process_id = getattr(self.process, "pid", None)
        if (
            isinstance(process_id, bool)
            or not isinstance(process_id, int)
            or process_id <= 0
        ):
            return False
        try:
            os.killpg(process_id, 0)
        except ProcessLookupError:
            return False
        except OSError as error:
            raise ProducerError(
                "%s process-group probe failed" % self.description
            ) from error
        return True

    def _read_pipe(self, pipe: BinaryIO, label: str) -> None:
        if label == "stdout":
            buffer = self.stdout_bytes
            maximum = self.maximum_stdout
        else:
            buffer = self.stderr_bytes
            maximum = MAX_CHILD_STDERR_BYTES
        capacity = maximum + 1 - len(buffer)
        try:
            chunk = os.read(pipe.fileno(), min(65536, max(1, capacity)))
        except BlockingIOError:
            return
        except OSError as error:
            raise ProducerError(
                "%s %s read failed" % (self.description, label)
            ) from error
        if not chunk:
            if label == "stdout":
                self.stdout_eof = True
            else:
                self.stderr_eof = True
            return
        buffer.extend(chunk)
        if len(buffer) > maximum:
            raise ProducerError(
                "%s %s exceeded its byte bound"
                % (self.description, label)
            )
        if label == "stderr":
            raise ProducerError("%s wrote to stderr" % self.description)

    def _fully_drained(self) -> bool:
        return (
            self.returncode is not None
            and self.stdout_eof
            and self.stderr_eof
        )


class _MonitorProcess:
    """Finite passive CEC monitor with explicit readiness and natural exit."""

    def __init__(self, child: _CapturedChild, clock: Callable[[], int]):
        self.child = child
        self.clock = clock
        self.ready = False
        self.natural = False

    def wait_ready(self, deadline: int) -> None:
        while not self.ready:
            self.child.require_alive()
            prefix = bytes(self.child.stdout_bytes)
            if not b"\n\n".startswith(prefix[:2]) and not prefix.startswith(
                b"\n\n"
            ):
                raise ProducerError(
                    "CEC monitor has the wrong readiness preamble"
                )
            if prefix.startswith(b"\n\n"):
                third_end = prefix.find(b"\n", 2)
                if third_end >= 0:
                    ready_line = prefix[2 : third_end + 1]
                    if _CEC_READY_PATTERN.fullmatch(ready_line) is None:
                        raise ProducerError(
                            "CEC monitor readiness is malformed"
                        )
                    self.ready = True
                    break
            remaining = _remaining_usec(self.clock, deadline)
            self.child.pump(min(IO_POLL_USEC, remaining))
        self.require_alive()

    def wait_until(self, deadline: int) -> None:
        while True:
            now = self.clock()
            if now >= deadline:
                self.require_alive()
                return
            self.child.require_alive()
            self.child.pump(min(IO_POLL_USEC, deadline - now))

    def require_alive(self) -> None:
        self.child.require_alive()

    def wait_natural(self, deadline: int) -> bytes:
        output = self.child.wait(deadline)
        if not self.ready:
            raise ProducerError("CEC monitor never became ready")
        if not output.endswith(b"\n"):
            raise ProducerError(
                "CEC monitor ended on an incomplete output line"
            )
        if self.child.signalled:
            raise ProducerError("CEC monitor was signalled")
        self.natural = True
        return output

    def pump(self, timeout_usec: int = 0) -> None:
        self.child.pump(timeout_usec)

    def close(self) -> None:
        self.child.close()


class FixedRuntime:
    """The real, fixed local capabilities used by the installed helper."""

    def __init__(
        self,
        *,
        clock: Callable[[], int] = lambda: (
            time.monotonic_ns() // 1000
        ),
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        self.clock = clock
        self.popen_factory = popen_factory

    def monotonic_usec(self) -> int:
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProducerError("monotonic clock is not an integer")
        return value

    def read_boot_id(
        self,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> str:
        _remaining_usec(self.clock, deadline)
        if monitor is not None:
            monitor.pump(0)
        try:
            with open(BOOT_ID_PATH, "rb") as boot_file:
                raw = boot_file.read(128)
                if boot_file.read(1):
                    raise ProducerError("boot ID exceeded its byte bound")
        except OSError as error:
            raise ProducerError("boot ID could not be read") from error
        if monitor is not None:
            monitor.pump(0)
        _remaining_usec(self.clock, deadline)
        return _parse_boot_id(raw)

    def read_service(
        self,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> ServiceIdentity:
        raw = self._run_command(
            SERVICE_COMMAND,
            maximum_stdout=MAX_COMMAND_BYTES,
            description="systemctl show",
            deadline=deadline,
            monitor=monitor,
        )
        return _parse_service(raw)

    def read_global_cursor(self, deadline: int) -> str:
        raw = self._run_command(
            CURSOR_COMMAND,
            maximum_stdout=MAX_COMMAND_BYTES,
            description="journal cursor query",
            deadline=deadline,
        )
        return _parse_cursor_output(raw)

    def start_monitor(self) -> Monitor:
        child = _CapturedChild(
            MONITOR_COMMAND,
            maximum_stdout=MAX_CEC_BYTES,
            description="passive CEC monitor",
            clock=self.clock,
            popen_factory=self.popen_factory,
        )
        return _MonitorProcess(child, self.clock)

    def sync_journal(self, deadline: int, monitor: Monitor) -> None:
        output = self._run_command(
            SYNC_COMMAND,
            maximum_stdout=1,
            description="journal synchronization",
            deadline=deadline,
            monitor=monitor,
        )
        if output:
            raise ProducerError("journal synchronization wrote output")

    def read_journal(
        self,
        cursor: str,
        deadline: int,
        monitor: Monitor,
    ) -> bytes:
        _require_cursor(cursor)
        command = (
            JOURNALCTL,
            "--boot",
            "--after-cursor=" + cursor,
            "--no-tail",
            "--output=json-seq",
            "--show-cursor",
            "--no-pager",
            "--quiet",
            "--output-fields=" + _JOURNAL_FIELDS,
        )
        return self._run_command(
            command,
            maximum_stdout=MAX_JOURNAL_BYTES,
            description="global journal query",
            deadline=deadline,
            monitor=monitor,
        )

    def _run_command(
        self,
        command: Sequence[str],
        *,
        maximum_stdout: int,
        description: str,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> bytes:
        command_deadline = min(
            deadline,
            _bounded_add(self.monotonic_usec(), COMMAND_TIMEOUT_USEC),
        )
        child = _CapturedChild(
            command,
            maximum_stdout=maximum_stdout,
            description=description,
            clock=self.clock,
            popen_factory=self.popen_factory,
        )
        try:
            output = child.wait(command_deadline, monitor)
        except BaseException as primary:
            try:
                child.close()
            except BaseException as cleanup_error:
                raise primary from cleanup_error
            raise
        else:
            child.close()
            return output


class FixedTransport:
    """Bounded nonblocking START/FINISH and READY/RESULT byte transport."""

    def __init__(
        self,
        stdin: BinaryIO,
        stdout: BinaryIO,
        clock: Callable[[], int],
    ):
        self.stdin = stdin
        self.stdout = stdout
        self.clock = clock
        self.input_buffer = bytearray()
        self.input_eof = False
        try:
            self.stdin_fd = stdin.fileno()
            self.stdout_fd = stdout.fileno()
            os.set_blocking(self.stdin_fd, False)
            os.set_blocking(self.stdout_fd, False)
        except (AttributeError, OSError, ValueError) as error:
            raise ProducerError(
                "protocol transport requires binary file descriptors"
            ) from error

    def read_start(self, deadline: int) -> str:
        raw = self._read_line(deadline)
        if self.input_buffer:
            raise ProducerError("input arrived before READY")
        return _parse_control_line(raw, "START")

    def write_ready(
        self,
        line: bytes,
        deadline: int,
        monitor: Monitor,
    ) -> None:
        if len(line) > MAX_READY_BYTES:
            raise ProducerError("READY line exceeded its byte bound")
        self._write_all(line, deadline, monitor)

    def read_finish_and_eof(
        self,
        nonce: str,
        deadline: int,
        monitor: Monitor,
    ) -> None:
        raw = self._read_line(deadline, monitor)
        _parse_control_line(raw, "FINISH", nonce)
        if self.input_buffer:
            raise ProducerError("input followed FINISH")
        while not self.input_eof:
            monitor.require_alive()
            self._read_input(deadline, monitor)
            if self.input_buffer:
                raise ProducerError("input followed FINISH")

    def write_result(
        self,
        header: bytes,
        body: bytes,
        deadline: int,
    ) -> None:
        self._write_all(header, deadline)
        self._write_all(body, deadline)

    def _read_line(
        self,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> bytes:
        while True:
            newline = self.input_buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self.input_buffer[: newline + 1])
                del self.input_buffer[: newline + 1]
                return raw
            if len(self.input_buffer) >= MAX_CONTROL_BYTES:
                raise ProducerError("control line exceeded its byte bound")
            if self.input_eof:
                raise ProducerError("protocol input ended before a line")
            self._read_input(deadline, monitor)

    def _read_input(
        self,
        deadline: int,
        monitor: Optional[Monitor],
    ) -> None:
        if monitor is not None:
            monitor.pump(0)
        remaining = _remaining_usec(self.clock, deadline)
        with selectors.DefaultSelector() as selector:
            selector.register(self.stdin_fd, selectors.EVENT_READ)
            events = selector.select(
                min(IO_POLL_USEC, remaining) / 1_000_000
            )
        if not events:
            if monitor is not None:
                monitor.pump(0)
            return
        try:
            chunk = os.read(
                self.stdin_fd,
                MAX_CONTROL_BYTES + 1 - len(self.input_buffer),
            )
        except BlockingIOError:
            return
        except OSError as error:
            raise ProducerError("protocol input failed") from error
        if not chunk:
            self.input_eof = True
            return
        self.input_buffer.extend(chunk)
        if len(self.input_buffer) > MAX_CONTROL_BYTES:
            raise ProducerError("protocol input exceeded its byte bound")

    def _write_all(
        self,
        data: bytes,
        deadline: int,
        monitor: Optional[Monitor] = None,
    ) -> None:
        if not isinstance(data, bytes):
            raise TypeError("protocol output must be bytes")
        view = memoryview(data)
        while view:
            if monitor is not None:
                monitor.pump(0)
                monitor.require_alive()
            remaining = _remaining_usec(self.clock, deadline)
            with selectors.DefaultSelector() as selector:
                selector.register(self.stdout_fd, selectors.EVENT_WRITE)
                events = selector.select(
                    min(IO_POLL_USEC, remaining) / 1_000_000
                )
            if not events:
                continue
            try:
                written = os.write(self.stdout_fd, view)
            except (BrokenPipeError, OSError) as error:
                raise ProducerError("protocol output failed") from error
            if written <= 0:
                raise ProducerError("protocol output made no progress")
            view = view[written:]


def _parse_boot_id(raw: bytes) -> str:
    if not isinstance(raw, bytes):
        raise TypeError("boot ID must be bytes")
    if _UUID_PATTERN.fullmatch(raw) is None:
        raise ProducerError("boot ID has invalid framing")
    normalized = raw[:-1].replace(b"-", b"").decode("ascii")
    try:
        _require_identifier(normalized, "boot ID")
    except (TypeError, ValueError) as error:
        raise ProducerError(str(error)) from error
    return normalized


def _parse_service(raw: bytes) -> ServiceIdentity:
    if not isinstance(raw, bytes):
        raise TypeError("service output must be bytes")
    if (
        not raw
        or len(raw) > MAX_COMMAND_BYTES
        or b"\r" in raw
        or not raw.endswith(b"\n")
    ):
        raise ProducerError("service output has invalid framing")
    try:
        lines = raw[:-1].decode("ascii", "strict").split("\n")
    except UnicodeDecodeError as error:
        raise ProducerError("service output is not ASCII") from error
    values = {}
    for line in lines:
        if not line or "=" not in line:
            raise ProducerError("service output contains a malformed field")
        key, value = line.split("=", 1)
        if key in values:
            raise ProducerError("service output contains a duplicate field")
        values[key] = value
    if frozenset(values) != frozenset(_SERVICE_PROPERTIES):
        raise ProducerError("service output has the wrong fields")
    try:
        return ServiceIdentity(
            unit_id=values["Id"],
            load_state=values["LoadState"],
            active_state=values["ActiveState"],
            sub_state=values["SubState"],
            invocation_id=values["InvocationID"],
            main_pid=_parse_decimal(
                values["MainPID"],
                "service main PID",
                minimum=1,
                maximum=MAX_PID,
            ),
            n_restarts=_parse_decimal(
                values["NRestarts"],
                "service restart count",
                minimum=0,
                maximum=MAX_UINT64,
            ),
            exec_start_usec=_parse_decimal(
                values["ExecMainStartTimestampMonotonic"],
                "service execution start",
                minimum=1,
                maximum=MAX_UINT64,
            ),
            active_enter_usec=_parse_decimal(
                values["ActiveEnterTimestampMonotonic"],
                "service active-enter time",
                minimum=1,
                maximum=MAX_UINT64,
            ),
        )
    except ProducerError:
        raise
    except (TypeError, ValueError) as error:
        raise ProducerError("service identity is invalid: %s" % error) from error


def _parse_cursor_output(raw: bytes) -> str:
    if (
        not isinstance(raw, bytes)
        or not raw.startswith(_CURSOR_PREFIX)
        or not raw.endswith(b"\n")
        or raw.count(b"\n") != 1
        or b"\r" in raw
    ):
        raise ProducerError("journal cursor output has invalid framing")
    encoded = raw[len(_CURSOR_PREFIX) : -1]
    try:
        cursor = encoded.decode("ascii", "strict")
        _require_cursor(cursor)
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        raise ProducerError("journal cursor is invalid") from error
    return cursor


def _remaining_usec(clock: Callable[[], int], deadline: int) -> int:
    now = clock()
    if (
        isinstance(now, bool)
        or not isinstance(now, int)
        or isinstance(deadline, bool)
        or not isinstance(deadline, int)
    ):
        raise ProducerError("deadline clock is not an integer")
    remaining = deadline - now
    if remaining <= 0:
        raise ProducerError("operation deadline expired")
    return remaining


def _report_error(stderr: BinaryIO, error: BaseException) -> None:
    text = "%s" % error
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if not text:
        text = type(error).__name__
    encoded = text.encode("ascii", "replace")[: MAX_DIAGNOSTIC_BYTES - 1]
    message = encoded + b"\n"
    try:
        view = memoryview(message)
        while view:
            written = stderr.write(view)
            if written is None:
                break
            if written <= 0:
                return
            view = view[written:]
        stderr.flush()
    except (BrokenPipeError, OSError, ValueError):
        return


def _run(
    arguments: Sequence[str],
    runtime: Optional[Runtime] = None,
    transport: Optional[Transport] = None,
    stderr: Optional[BinaryIO] = None,
) -> int:
    """Run the zero-argument producer with injectable semantic adapters."""

    if stderr is None:
        stderr = sys.stderr.buffer
    if arguments:
        _report_error(stderr, ProducerError("accepts no arguments"))
        return 64
    try:
        if runtime is None:
            runtime = FixedRuntime()
        if transport is None:
            transport = FixedTransport(
                sys.stdin.buffer,
                sys.stdout.buffer,
                runtime.monotonic_usec,
            )
        collect_evidence(runtime, transport)
    except Exception as error:
        _report_error(stderr, error)
        return 70
    return 0


def _raise_on_termination(
    signal_number: int,
    _frame: object,
) -> None:
    try:
        signal_name = signal.Signals(signal_number).name
    except ValueError:
        signal_name = str(signal_number)
    raise ProducerError("interrupted by %s" % signal_name)


def _install_signal_handlers() -> None:
    for signal_number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, _raise_on_termination)


def main() -> int:
    _install_signal_handlers()
    return _run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
