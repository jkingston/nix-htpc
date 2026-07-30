"""Cooperating-client capture lock held by one fixed remote process."""

from __future__ import annotations

import math
import re
import secrets
import subprocess
import time
from typing import Any, Callable, Optional

from .process import (
    BoundedProcess,
    ProcessTimeout,
    ProcessTransportError,
)
from .ssh_policy import (
    SSH_FIXED_CAPABILITY_OPTIONS,
    SSH_OPTION_TERMINATOR,
    SSH_PROGRAM,
    validate_ssh_host,
)
REMOTE_FLOCK_PROGRAM = "/run/current-system/sw/bin/flock"
REMOTE_CAT_PROGRAM = "/run/current-system/sw/bin/cat"
REMOTE_LOCK_FILE = "/run/lock/kodi-capture.lock"
LOCK_CONFLICT_STATUS = 75
PROTOCOL = "KODI-CAPTURE-LOCK/1"
ACQUIRE_OPERATION = "ACQUIRE"
PING_OPERATION = "PING"
PROTOCOL_OPERATIONS = (ACQUIRE_OPERATION, PING_OPERATION)
NONCE_HEX_LENGTH = 32
MAX_ECHO_BYTES = 96
PRE_ACQUIRE = "pre-acquire"
HELD = "held"
CLOSED = "closed"
POISONED = "poisoned"
_LINE_PATTERN = re.compile(
    rb"\A"
    + re.escape(PROTOCOL.encode("ascii"))
    + rb" (?:"
    + rb"|".join(
        re.escape(operation.encode("ascii"))
        for operation in PROTOCOL_OPERATIONS
    )
    + rb") [0-9a-f]{"
    + str(NONCE_HEX_LENGTH).encode("ascii")
    + rb"}\n\Z"
)


class RemoteLockError(Exception):
    """Base class for remote capture-lock failures."""


class RemoteLockTimeout(RemoteLockError):
    """The absolute lock-protocol deadline expired."""


class RemoteLockConflict(RemoteLockError):
    """Another cooperating capture client holds the remote lock."""


class RemoteLockProtocolError(RemoteLockError):
    """The remote lock echo did not exactly match the request."""


class RemoteLockTransportError(RemoteLockError):
    """SSH or the remote lock-holder process failed."""


class RemoteLockCleanupError(RemoteLockTransportError):
    """The remote lock-holder process could not be reaped."""


class RemoteLockPoisoned(RemoteLockError):
    """A prior ambiguous exchange made the lock unusable."""


class RemoteCaptureLock:
    """Hold an exclusive advisory lock through an SSH-attached remote ``cat``.

    The fixed remote program contains no interpreter. ``flock`` starts ``cat``
    only after acquiring the lock, so an exact nonce-bearing echo from a process
    that is still live is both protocol readiness and proof that the
    cooperating lock is held. :meth:`assert_held` is an active write/read ping,
    not a passive local process-status observation.
    """

    def __init__(
        self,
        host: str,
        acquire_deadline: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        graceful_timeout: float = 0.25,
        terminate_timeout: float = 1.0,
        max_stderr_bytes: int = 64 * 1024,
    ):
        validate_ssh_host(host)
        self._validate_deadline(acquire_deadline)
        if not callable(nonce_factory):
            raise ValueError("nonce_factory must be callable")
        if graceful_timeout < 0:
            raise ValueError("graceful_timeout must not be negative")
        if terminate_timeout <= 0:
            raise ValueError("terminate_timeout must be positive")
        if max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")

        self._argv = [
            SSH_PROGRAM,
            "-T",
            *SSH_FIXED_CAPABILITY_OPTIONS,
            SSH_OPTION_TERMINATOR,
            host,
            REMOTE_FLOCK_PROGRAM,
            "-n",
            "-E",
            str(LOCK_CONFLICT_STATUS),
            REMOTE_LOCK_FILE,
            REMOTE_CAT_PROGRAM,
        ]
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._used_nonces = set()
        self._state = PRE_ACQUIRE
        self._transport_closed = False
        self._acquire_acknowledged = False
        self._poison_reason: Optional[str] = None
        self._cleanup_error: Optional[RemoteLockCleanupError] = None
        try:
            self._bounded = BoundedProcess(
                self._argv,
                clock=clock,
                popen_factory=popen_factory,
                graceful_timeout=graceful_timeout,
                terminate_timeout=terminate_timeout,
                max_stderr_bytes=max_stderr_bytes,
                description="remote capture lock",
            )
        except ProcessTransportError as error:
            raise RemoteLockTransportError(str(error)) from error

        try:
            self._exchange(ACQUIRE_OPERATION, acquire_deadline)
            self._state = HELD
        except BaseException as primary:
            self._poison_and_raise(primary)

    @property
    def stderr_tail(self) -> bytes:
        """Return the bounded SSH stderr tail observed so far."""

        return self._bounded.stderr_tail

    def assert_held(self, deadline: float) -> None:
        """Actively ping the remote guardian to prove the lock remains held."""

        self._validate_deadline(deadline)
        if self._state == POISONED:
            raise RemoteLockPoisoned(
                "remote capture lock is poisoned: %s" % self._poison_reason
            )
        if self._state != HELD:
            raise RemoteLockPoisoned("remote capture lock is closed")
        try:
            self._exchange(PING_OPERATION, deadline)
        except BaseException as primary:
            self._poison_and_raise(primary)

    def close(self) -> None:
        """Release the remote lock and reap its SSH process."""

        if self._cleanup_error is not None:
            raise self._cleanup_error
        if self._transport_closed:
            return
        self._transport_closed = True
        try:
            self._bounded.close()
        except BaseException as error:
            cleanup_error = RemoteLockCleanupError(str(error))
            self._cleanup_error = cleanup_error
            raise cleanup_error from error
        if self._state != POISONED:
            self._state = CLOSED

    def __enter__(self) -> "RemoteCaptureLock":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if _value is None:
            self.close()
            return
        try:
            self.close()
        except RemoteLockCleanupError as cleanup_error:
            raise _value.with_traceback(_traceback) from cleanup_error

    def _exchange(self, operation: str, deadline: float) -> None:
        nonce = self._new_nonce()
        expected = ("%s %s %s\n" % (PROTOCOL, operation, nonce)).encode(
            "ascii"
        )
        try:
            self._bounded.write(expected, deadline)
            received = self._read_line(deadline)
        except ProcessTimeout as error:
            raise RemoteLockTimeout(str(error)) from error
        except ProcessTransportError as error:
            self._raise_process_failure(error)

        if not _LINE_PATTERN.fullmatch(received):
            raise RemoteLockProtocolError(
                "remote capture lock returned a malformed echo"
            )
        if received != expected:
            raise RemoteLockProtocolError(
                "remote capture lock returned the wrong echo"
            )
        if operation == ACQUIRE_OPERATION:
            self._acquire_acknowledged = True
        self._require_live_process()
        if self._clock() >= deadline:
            raise RemoteLockTimeout(
                "remote capture lock acknowledgement missed its deadline"
            )

    def _read_line(self, deadline: float) -> bytes:
        received = bytearray()
        while True:
            remaining = (MAX_ECHO_BYTES + 1) - len(received)
            if remaining <= 0:
                raise RemoteLockProtocolError(
                    "remote capture lock echo exceeded its size bound"
                )
            chunk = self._bounded.read(remaining, deadline)
            received.extend(chunk)
            if len(received) > MAX_ECHO_BYTES:
                raise RemoteLockProtocolError(
                    "remote capture lock echo exceeded its size bound"
                )
            newline = received.find(b"\n")
            if newline < 0:
                continue
            if newline != len(received) - 1:
                raise RemoteLockProtocolError(
                    "remote capture lock returned trailing bytes"
                )
            return bytes(received)

    def _new_nonce(self) -> str:
        try:
            nonce = self._nonce_factory()
        except Exception as error:
            raise RemoteLockProtocolError(
                "remote capture lock nonce generation failed"
            ) from error
        if (
            not isinstance(nonce, str)
            or len(nonce) != NONCE_HEX_LENGTH
            or any(character not in "0123456789abcdef" for character in nonce)
        ):
            raise RemoteLockProtocolError(
                "remote capture lock nonce must be 32 lowercase hex characters"
            )
        if nonce in self._used_nonces:
            raise RemoteLockProtocolError(
                "remote capture lock nonce was reused"
            )
        self._used_nonces.add(nonce)
        return nonce

    def _raise_process_failure(self, error: ProcessTransportError) -> None:
        returncode = self._bounded.returncode
        if (
            returncode == LOCK_CONFLICT_STATUS
            and self._state == PRE_ACQUIRE
            and not self._acquire_acknowledged
        ):
            raise RemoteLockConflict(str(error)) from error
        raise RemoteLockTransportError(str(error)) from error

    def _require_live_process(self) -> None:
        returncode = self._bounded.returncode
        if returncode is None:
            return
        message = "remote capture lock exited with status %d" % returncode
        if (
            returncode == LOCK_CONFLICT_STATUS
            and self._state == PRE_ACQUIRE
            and not self._acquire_acknowledged
        ):
            raise RemoteLockConflict(message)
        raise RemoteLockTransportError(message)

    def _poison_and_raise(self, primary: BaseException) -> None:
        reason = str(primary) or type(primary).__name__
        self._poison_reason = reason
        self._state = POISONED
        try:
            self.close()
        except RemoteLockCleanupError as cleanup_error:
            raise primary from cleanup_error
        raise primary

    @staticmethod
    def _validate_deadline(deadline: float) -> None:
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(float(deadline))
        ):
            raise ValueError("deadline must be a finite absolute timestamp")
