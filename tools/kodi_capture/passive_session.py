"""One-shot fixed SSH session for passive Kodi/CEC evidence.

The remote command is an immutable, zero-argument NixOS executable.  A caller
opens the session, performs a bounded authorized action phase after READY has
been validated, seals that window with FINISH plus stdin EOF, performs any
postchecks over separate transports, then collects one finite RESULT.

No background stdout reader is needed: the producer emits nothing after READY
until its finite monitor and final identity fences complete.  Calling
``collect`` promptly after bounded postchecks drains that finite output before
the session's internal deadline.
"""

from __future__ import annotations

import math
import re
import secrets
import subprocess
import time
from typing import Any, Callable, Optional

from .passive_evidence import (
    MAX_ENVELOPE_BYTES,
    MAX_READY_BYTES,
    NONCE_HEX_LENGTH,
    PROTOCOL_VERSION,
    PassiveEvidence,
    ReadyEvidence,
    decode_passive_evidence,
    decode_ready_line,
    decode_result_header,
)
from .process import (
    BoundedProcess,
    ProcessTimeout,
    ProcessTransportError,
)
from .ssh_policy import (
    SSH_BASE_OPTIONS,
    SSH_OPTION_TERMINATOR,
    SSH_PROGRAM,
    validate_ssh_host,
)


REMOTE_PASSIVE_EVIDENCE_PROGRAM = (
    "/run/current-system/sw/bin/kodi-passive-evidence"
)
# The producer permits FINISH through remote READY + 5 seconds.  A 3.5-second
# action budget and 4-second FINISH cap leave one second for SSH latency and
# clock-boundary jitter.  Its whole remote session is capped at 30 seconds;
# the host's 31-second cap leaves the corresponding result-delivery reserve.
HOST_ACTION_TIMEOUT_SECONDS = 3.5
HOST_FINISH_TIMEOUT_SECONDS = 4.0
HOST_SESSION_TIMEOUT_SECONDS = 31.0
MAX_SSH_STDERR_BYTES = 64 * 1024

READY = "ready"
ACTION_RUNNING = "action-running"
ACTION_COMPLETE = "action-complete"
SEALED = "sealed"
COLLECTED = "collected"
CLOSED = "closed"
POISONED = "poisoned"

_NONCE_PATTERN = re.compile(
    r"\A[0-9a-f]{%d}\Z" % NONCE_HEX_LENGTH
)
_SESSION_SSH_OPTIONS = (
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
)


class PassiveSessionError(Exception):
    """Base class for passive evidence session failures."""


class PassiveSessionStateError(PassiveSessionError):
    """An operation is invalid in the session's current one-shot state."""


class PassiveSessionTimeout(PassiveSessionError):
    """An absolute host-side session or action deadline expired."""


class PassiveSessionTransportError(PassiveSessionError):
    """SSH or the fixed remote producer failed."""


class PassiveSessionCleanupError(PassiveSessionTransportError):
    """The SSH process could not be reaped unambiguously."""


class PassiveSessionProtocolError(PassiveSessionError):
    """Session framing outside the evidence decoder was invalid."""


class RemotePassiveEvidenceSession:
    """One READY/action/FINISH/RESULT transaction over fixed OpenSSH argv.

    Construction opens SSH, writes START, validates READY, and proves that the
    SSH process is live with no pending stderr.  The object is intentionally
    one-shot: any in-flight transport or action failure poisons it, and every
    successful protocol operation advances exactly one state.
    """

    def __init__(
        self,
        host: str,
        open_deadline: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = (
            subprocess.Popen
        ),
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        graceful_timeout: float = 0.25,
        terminate_timeout: float = 1.0,
        max_stderr_bytes: int = MAX_SSH_STDERR_BYTES,
    ):
        validate_ssh_host(host)
        _validate_deadline(open_deadline)
        if not callable(clock):
            raise ValueError("clock must be callable")
        if not callable(popen_factory):
            raise ValueError("popen_factory must be callable")
        if not callable(nonce_factory):
            raise ValueError("nonce_factory must be callable")
        if (
            isinstance(graceful_timeout, bool)
            or not isinstance(graceful_timeout, (int, float))
            or not math.isfinite(float(graceful_timeout))
            or graceful_timeout < 0
        ):
            raise ValueError("graceful_timeout must not be negative")
        if (
            isinstance(terminate_timeout, bool)
            or not isinstance(terminate_timeout, (int, float))
            or not math.isfinite(float(terminate_timeout))
            or terminate_timeout <= 0
        ):
            raise ValueError("terminate_timeout must be positive")
        if (
            isinstance(max_stderr_bytes, bool)
            or not isinstance(max_stderr_bytes, int)
            or max_stderr_bytes <= 0
        ):
            raise ValueError("max_stderr_bytes must be positive")

        self._clock = clock
        self._argv = [
            SSH_PROGRAM,
            "-T",
            *_SESSION_SSH_OPTIONS,
            SSH_OPTION_TERMINATOR,
            host,
            REMOTE_PASSIVE_EVIDENCE_PROGRAM,
        ]
        self._process: Optional[BoundedProcess] = None
        self._cleanup_error: Optional[PassiveSessionCleanupError] = None
        self._buffer = bytearray()
        self._state = POISONED
        self._ready: Optional[ReadyEvidence] = None
        self._action_deadline = 0.0
        self._finish_deadline = 0.0
        self._session_deadline = 0.0

        _require_before(clock, float(open_deadline), "session open")
        self._nonce = _fresh_nonce(nonce_factory)
        try:
            try:
                self._process = BoundedProcess(
                    self._argv,
                    clock=clock,
                    popen_factory=popen_factory,
                    graceful_timeout=float(graceful_timeout),
                    terminate_timeout=float(terminate_timeout),
                    max_stderr_bytes=max_stderr_bytes,
                    description="remote passive evidence",
                )
            except ProcessTransportError as error:
                raise PassiveSessionTransportError(str(error)) from error
            start = (
                "%s START %s\n" % (PROTOCOL_VERSION, self._nonce)
            ).encode("ascii")
            self._write(start, float(open_deadline))
            ready_raw = self._read_line(
                MAX_READY_BYTES,
                float(open_deadline),
                "READY",
            )
            if self._buffer:
                raise PassiveSessionProtocolError(
                    "remote output followed READY before the action"
                )
            ready = decode_ready_line(ready_raw)
            if ready.nonce != self._nonce:
                raise PassiveSessionProtocolError(
                    "READY nonce does not match START"
                )
            self._require_running_stderr_clean()
            ready_received = _now(clock)
            _require_before(
                clock,
                float(open_deadline),
                "READY completion",
            )
            self._ready = ready
            self._action_deadline = (
                ready_received + HOST_ACTION_TIMEOUT_SECONDS
            )
            self._finish_deadline = (
                ready_received + HOST_FINISH_TIMEOUT_SECONDS
            )
            self._session_deadline = (
                ready_received + HOST_SESSION_TIMEOUT_SECONDS
            )
            self._state = READY
        except BaseException as primary:
            self._poison_and_close(primary)

    @property
    def ready(self) -> ReadyEvidence:
        """Return the validated READY evidence."""

        if self._ready is None:
            raise PassiveSessionStateError("session did not reach READY")
        return self._ready

    @property
    def state(self) -> str:
        return self._state

    def perform_action(
        self,
        action: Callable[[ReadyEvidence, float], None],
        deadline: float,
    ) -> None:
        """Invoke the supplied action exactly once after validated READY."""

        if not callable(action):
            raise ValueError("action must be callable")
        self._require_state(READY, "perform action")
        _validate_deadline(deadline)
        action_deadline = min(
            float(deadline),
            self._action_deadline,
        )
        try:
            self._state = ACTION_RUNNING
            _require_before(
                self._clock,
                action_deadline,
                "action start",
            )
            action(self.ready, action_deadline)
            _require_before(
                self._clock,
                action_deadline,
                "action completion",
            )
            self._require_running_stderr_clean()
            self._state = ACTION_COMPLETE
        except BaseException as primary:
            self._poison_and_close(primary)

    def seal_action_window(self, deadline: float) -> None:
        """Send exact FINISH and stdin EOF within the reserved host window."""

        self._require_state(ACTION_COMPLETE, "seal action window")
        _validate_deadline(deadline)
        finish_deadline = min(
            float(deadline),
            self._finish_deadline,
        )
        try:
            _require_before(
                self._clock,
                finish_deadline,
                "FINISH start",
            )
            finish = (
                "%s FINISH %s\n"
                % (PROTOCOL_VERSION, self._nonce)
            ).encode("ascii")
            self._write(finish, finish_deadline)
            self._close_input()
            _require_before(
                self._clock,
                finish_deadline,
                "FINISH completion",
            )
            self._state = SEALED
        except BaseException as primary:
            self._poison_and_close(primary)

    def collect(self, deadline: float) -> PassiveEvidence:
        """Collect one exact clean RESULT after caller-owned postchecks."""

        self._require_state(SEALED, "collect evidence")
        _validate_deadline(deadline)
        collect_deadline = min(
            float(deadline),
            self._session_deadline,
        )
        try:
            _require_before(
                self._clock,
                collect_deadline,
                "RESULT collection start",
            )
            result_header = self._read_line(
                MAX_READY_BYTES,
                collect_deadline,
                "RESULT",
            )
            body_length = decode_result_header(
                result_header,
                self._nonce,
            )
            body = self._read_exact(body_length, collect_deadline)
            if self._buffer:
                raise PassiveSessionProtocolError(
                    "remote output exceeds the declared RESULT length"
                )
            trailing = self._read_all(1, collect_deadline)
            if trailing:
                raise PassiveSessionProtocolError(
                    "remote output follows the declared RESULT body"
                )
            if self._process is None:
                raise PassiveSessionStateError(
                    "session process is unavailable"
                )
            if self._process.stderr_tail:
                detail = self._process.stderr_tail.decode(
                    "utf-8",
                    "replace",
                ).strip()
                raise PassiveSessionTransportError(
                    "remote passive evidence wrote to stderr"
                    + (": " + detail if detail else "")
                )

            _require_before(
                self._clock,
                collect_deadline,
                "evidence decoding",
            )
            # Preserve the consumer's protocol, continuity, and policy error
            # classes so callers retain raw evidence and exact classification.
            evidence = decode_passive_evidence(body, self.ready)
            _require_before(
                self._clock,
                collect_deadline,
                "session completion",
            )
            self._close_process()
            self._state = COLLECTED
            return evidence
        except BaseException as primary:
            self._poison_and_close(primary)

    def close(self) -> None:
        """Close and reap without advancing an unfinished transaction."""

        if self._cleanup_error is not None:
            raise self._cleanup_error
        if self._process is None:
            return
        try:
            self._close_process()
        except BaseException:
            self._state = POISONED
            raise
        if self._state != COLLECTED:
            self._state = CLOSED

    def __enter__(self) -> "RemotePassiveEvidenceSession":
        return self

    def __exit__(
        self,
        _type: Any,
        _value: Any,
        _traceback: Any,
    ) -> None:
        if _value is None:
            self.close()
            return
        try:
            self.close()
        except PassiveSessionCleanupError as cleanup_error:
            if cleanup_error is _value:
                return
            raise _value.with_traceback(_traceback) from cleanup_error

    def _require_state(self, expected: str, operation: str) -> None:
        if self._state != expected:
            raise PassiveSessionStateError(
                "%s requires %s state, found %s"
                % (operation, expected, self._state)
            )

    def _write(self, data: bytes, deadline: float) -> None:
        process = self._require_process()
        try:
            process.write(data, deadline)
        except ProcessTimeout as error:
            raise PassiveSessionTimeout(str(error)) from error
        except ProcessTransportError as error:
            raise PassiveSessionTransportError(str(error)) from error

    def _close_input(self) -> None:
        process = self._require_process()
        try:
            process.close_input()
        except ProcessTransportError as error:
            raise PassiveSessionTransportError(str(error)) from error

    def _read(self, maximum: int, deadline: float) -> bytes:
        process = self._require_process()
        try:
            return process.read(maximum, deadline)
        except ProcessTimeout as error:
            raise PassiveSessionTimeout(str(error)) from error
        except ProcessTransportError as error:
            raise PassiveSessionTransportError(str(error)) from error

    def _read_all(self, maximum: int, deadline: float) -> bytes:
        process = self._require_process()
        try:
            return process.read_all(maximum, deadline)
        except ProcessTimeout as error:
            raise PassiveSessionTimeout(str(error)) from error
        except ProcessTransportError as error:
            raise PassiveSessionTransportError(str(error)) from error

    def _require_running_stderr_clean(self) -> None:
        process = self._require_process()
        try:
            process.require_running_with_empty_stderr()
        except ProcessTransportError as error:
            raise PassiveSessionTransportError(str(error)) from error

    def _read_line(
        self,
        maximum: int,
        deadline: float,
        label: str,
    ) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line_length = newline + 1
                if line_length > maximum:
                    raise PassiveSessionProtocolError(
                        "%s line exceeded its byte bound" % label
                    )
                line = bytes(self._buffer[:line_length])
                del self._buffer[:line_length]
                return line
            if len(self._buffer) > maximum:
                raise PassiveSessionProtocolError(
                    "%s line exceeded its byte bound" % label
                )
            self._buffer.extend(
                self._read(
                    maximum + 1 - len(self._buffer),
                    deadline,
                )
            )

    def _read_exact(self, length: int, deadline: float) -> bytes:
        if (
            isinstance(length, bool)
            or not isinstance(length, int)
            or not 1 <= length <= MAX_ENVELOPE_BYTES
        ):
            raise PassiveSessionProtocolError(
                "RESULT length is outside its bound"
            )
        body = bytearray()
        if self._buffer:
            consumed = min(length, len(self._buffer))
            body.extend(self._buffer[:consumed])
            del self._buffer[:consumed]
        while len(body) < length:
            body.extend(
                self._read(
                    min(64 * 1024, length - len(body)),
                    deadline,
                )
            )
        return bytes(body)

    def _require_process(self) -> BoundedProcess:
        if self._process is None:
            raise PassiveSessionStateError(
                "session process is unavailable"
            )
        return self._process

    def _close_process(self) -> None:
        if self._cleanup_error is not None:
            raise self._cleanup_error
        process = self._process
        if process is None:
            return
        try:
            process.close()
        except BaseException as error:
            cleanup_error = PassiveSessionCleanupError(str(error))
            self._cleanup_error = cleanup_error
            raise cleanup_error from error
        self._process = None

    def _poison_and_close(self, primary: BaseException) -> None:
        self._state = POISONED
        if (
            self._cleanup_error is not None
            and primary is self._cleanup_error
        ):
            raise primary
        try:
            self._close_process()
        except BaseException as cleanup_error:
            raise primary from cleanup_error
        raise primary


def _fresh_nonce(nonce_factory: Callable[[], str]) -> str:
    nonce = nonce_factory()
    if (
        not isinstance(nonce, str)
        or _NONCE_PATTERN.fullmatch(nonce) is None
        or nonce == "0" * NONCE_HEX_LENGTH
    ):
        raise ValueError(
            "nonce factory must return nonzero lowercase 32-hex text"
        )
    return nonce


def _validate_deadline(deadline: float) -> None:
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("deadline must be a finite absolute timestamp")


def _now(clock: Callable[[], float]) -> float:
    value = clock()
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise PassiveSessionTimeout("monotonic clock is not finite")
    return float(value)


def _require_before(
    clock: Callable[[], float],
    deadline: float,
    phase: str,
) -> None:
    if _now(clock) >= deadline:
        raise PassiveSessionTimeout("%s missed its deadline" % phase)


__all__ = (
    "REMOTE_PASSIVE_EVIDENCE_PROGRAM",
    "HOST_ACTION_TIMEOUT_SECONDS",
    "HOST_FINISH_TIMEOUT_SECONDS",
    "HOST_SESSION_TIMEOUT_SECONDS",
    "PassiveSessionError",
    "PassiveSessionStateError",
    "PassiveSessionTimeout",
    "PassiveSessionTransportError",
    "PassiveSessionCleanupError",
    "PassiveSessionProtocolError",
    "RemotePassiveEvidenceSession",
)
