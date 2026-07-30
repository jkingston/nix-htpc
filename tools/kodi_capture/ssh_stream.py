"""OpenSSH-backed byte stream for Kodi JSON-RPC."""

from __future__ import annotations

import subprocess
import time
from typing import Any, Callable

from .jsonrpc import JsonRpcTimeout, JsonRpcTransportError
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


KODI_JSON_RPC_ENDPOINT = "127.0.0.1:9090"


class OpenSshByteStream:
    """Byte stream opened with OpenSSH direct TCP forwarding."""

    def __init__(
        self,
        host: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        terminate_timeout: float = 1.0,
        max_stderr_bytes: int = 64 * 1024,
    ):
        validate_ssh_host(host)

        self.argv = [
            SSH_PROGRAM,
            *SSH_FIXED_CAPABILITY_OPTIONS,
            "-W",
            KODI_JSON_RPC_ENDPOINT,
            SSH_OPTION_TERMINATOR,
            host,
        ]
        self._cleanup_error = None
        try:
            self._bounded = BoundedProcess(
                self.argv,
                clock=clock,
                popen_factory=popen_factory,
                terminate_timeout=terminate_timeout,
                max_stderr_bytes=max_stderr_bytes,
                description="ssh",
            )
        except ProcessTransportError as error:
            raise JsonRpcTransportError(str(error)) from error
        self.process = self._bounded.process

    @property
    def stderr_tail(self) -> bytes:
        """Return the bounded SSH stderr tail observed so far."""

        return self._bounded.stderr_tail

    def write(self, data: bytes, deadline: float) -> None:
        try:
            self._bounded.write(data, deadline)
        except ProcessTimeout as error:
            raise JsonRpcTimeout(str(error)) from error
        except ProcessTransportError as error:
            raise JsonRpcTransportError(str(error)) from error

    def read(self, max_bytes: int, deadline: float) -> bytes:
        try:
            return self._bounded.read(max_bytes, deadline)
        except ProcessTimeout as error:
            raise JsonRpcTimeout(str(error)) from error
        except ProcessTransportError as error:
            raise JsonRpcTransportError(str(error)) from error

    def close(self) -> None:
        try:
            self._bounded.close()
        except ProcessTransportError as error:
            if self._cleanup_error is None:
                self._cleanup_error = JsonRpcTransportError(str(error))
            raise self._cleanup_error from error
        if self._cleanup_error is not None:
            raise self._cleanup_error

    def __enter__(self) -> "OpenSshByteStream":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()
