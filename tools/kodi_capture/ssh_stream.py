"""OpenSSH-backed byte stream for Kodi JSON-RPC."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from typing import Any, Callable, Optional

from .jsonrpc import JsonRpcTimeout, JsonRpcTransportError


class OpenSshByteStream:
    """Byte stream opened with OpenSSH direct TCP forwarding."""

    def __init__(
        self,
        host: str,
        *,
        port: int = 9090,
        ssh_binary: str = "ssh",
        clock: Callable[[], float] = time.monotonic,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        terminate_timeout: float = 1.0,
        max_stderr_bytes: int = 64 * 1024,
    ):
        if (
            not isinstance(host, str)
            or not host
            or host.startswith("-")
        ):
            raise ValueError("host must not be empty or start with '-'")
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if terminate_timeout <= 0:
            raise ValueError("terminate_timeout must be positive")
        if max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")

        self.clock = clock
        self.terminate_timeout = terminate_timeout
        self.max_stderr_bytes = max_stderr_bytes
        self.process = None
        self._stdin = None
        self._stdout = None
        self._stderr = None
        self._stderr_bytes = bytearray()
        self._stderr_eof = False
        self._closed = False
        self._cleanup_error = None
        self.argv = [
            ssh_binary,
            "-o",
            "BatchMode=yes",
            "-o",
            "ClearAllForwardings=yes",
            "-W",
            "127.0.0.1:%d" % port,
            host,
        ]
        try:
            self.process = popen_factory(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
            )
            self._stdin = self.process.stdin
            self._stdout = self.process.stdout
            self._stderr = self.process.stderr
            if (
                self._stdin is None
                or self._stdout is None
                or self._stderr is None
            ):
                raise JsonRpcTransportError(
                    "ssh did not provide binary standard streams"
                )
            os.set_blocking(self._stdin.fileno(), False)
            os.set_blocking(self._stdout.fileno(), False)
            os.set_blocking(self._stderr.fileno(), False)
        except Exception as error:
            try:
                self.close()
            except JsonRpcTransportError as cleanup_error:
                raise JsonRpcTransportError(
                    "ssh setup failed: %s; cleanup failed: %s"
                    % (error, cleanup_error)
                ) from error
            if isinstance(error, JsonRpcTransportError):
                raise
            raise JsonRpcTransportError(
                "ssh setup failed: %s" % error
            ) from error

    def write(self, data: bytes, deadline: float) -> None:
        if self._closed:
            raise JsonRpcTransportError("ssh stream is closed")
        view = memoryview(data)
        while view:
            self._raise_if_exited()
            with selectors.DefaultSelector() as selector:
                selector.register(self._stdin, selectors.EVENT_WRITE, "stdin")
                if not self._stderr_eof:
                    selector.register(
                        self._stderr,
                        selectors.EVENT_READ,
                        "stderr",
                    )
                events = selector.select(self._remaining(deadline))
            if not events:
                raise JsonRpcTimeout("ssh write deadline expired")
            for key, _ in events:
                if key.data == "stderr":
                    self._read_stderr()
                    continue
                try:
                    written = os.write(self._stdin.fileno(), view)
                except (BrokenPipeError, OSError) as error:
                    self._raise_transport("ssh write failed", error)
                if written <= 0:
                    raise JsonRpcTransportError("ssh write made no progress")
                view = view[written:]

    def read(self, max_bytes: int, deadline: float) -> bytes:
        if self._closed:
            raise JsonRpcTransportError("ssh stream is closed")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        while True:
            with selectors.DefaultSelector() as selector:
                selector.register(self._stdout, selectors.EVENT_READ, "stdout")
                if not self._stderr_eof:
                    selector.register(
                        self._stderr,
                        selectors.EVENT_READ,
                        "stderr",
                    )
                events = selector.select(self._remaining(deadline))
            if not events:
                raise JsonRpcTimeout("ssh read deadline expired")
            for key, _ in events:
                if key.data == "stderr":
                    self._read_stderr()
                    continue
                try:
                    chunk = os.read(self._stdout.fileno(), max_bytes)
                except OSError as error:
                    self._raise_transport("ssh read failed", error)
                if chunk:
                    return chunk
                self._wait_briefly_for_exit(deadline)
                self._raise_transport("ssh closed its output")

    def close(self) -> None:
        if self._closed:
            if self._cleanup_error is not None:
                raise self._cleanup_error
            return
        self._closed = True
        process = self.process
        if process is None:
            return
        cleanup_error = None
        stdin = self._stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=self.terminate_timeout)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=self.terminate_timeout)
                except subprocess.TimeoutExpired:
                    cleanup_error = JsonRpcTransportError(
                        "ssh did not exit after bounded terminate/kill waits"
                    )
        for pipe_name in ("_stdout", "_stderr"):
            pipe = getattr(self, pipe_name)
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        if cleanup_error is not None:
            self._cleanup_error = cleanup_error
            raise cleanup_error

    def __enter__(self) -> "OpenSshByteStream":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise JsonRpcTimeout("ssh deadline expired")
        return remaining

    def _read_stderr(self) -> None:
        try:
            chunk = os.read(self._stderr.fileno(), 4096)
        except BlockingIOError:
            return
        except OSError as error:
            self._raise_transport("failed to read ssh stderr", error)
        if not chunk:
            self._stderr_eof = True
            return
        self._append_stderr(chunk)

    def _raise_if_exited(self) -> None:
        returncode = self.process.poll()
        if returncode is None:
            return
        self._drain_stderr()
        raise JsonRpcTransportError(self._exit_message(returncode))

    def _wait_briefly_for_exit(self, deadline: float) -> None:
        if self.process.poll() is not None:
            return
        timeout = min(self.terminate_timeout, self._remaining(deadline))
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def _drain_stderr(self) -> None:
        while True:
            try:
                chunk = os.read(self._stderr.fileno(), 4096)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            self._append_stderr(chunk)

    def _append_stderr(self, chunk: bytes) -> None:
        self._stderr_bytes.extend(chunk)
        overflow = len(self._stderr_bytes) - self.max_stderr_bytes
        if overflow > 0:
            del self._stderr_bytes[:overflow]

    def _raise_transport(
        self,
        message: str,
        error: Optional[BaseException] = None,
    ) -> None:
        self._drain_stderr()
        returncode = self.process.poll()
        detail = self._stderr_text()
        pieces = [message]
        if returncode is not None:
            pieces.append("status %d" % returncode)
        if error is not None:
            pieces.append(str(error))
        if detail:
            pieces.append(detail)
        raise JsonRpcTransportError(": ".join(pieces))

    def _exit_message(self, returncode: int) -> str:
        message = "ssh exited with status %d" % returncode
        detail = self._stderr_text()
        return "%s: %s" % (message, detail) if detail else message

    def _stderr_text(self) -> str:
        return bytes(self._stderr_bytes).decode("utf-8", "replace").strip()
