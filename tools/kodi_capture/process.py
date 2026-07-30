"""Bounded POSIX subprocess byte streams."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from typing import Any, Callable, Optional, Sequence


class ProcessError(Exception):
    """Base class for subprocess lifecycle failures."""


class ProcessTimeout(ProcessError):
    """An absolute subprocess I/O deadline expired."""


class ProcessTransportError(ProcessError):
    """A subprocess byte stream failed."""


class ProcessCleanupError(ProcessTransportError):
    """A subprocess could not be reaped within bounded waits."""


class BoundedProcess:
    """Nonblocking byte streams around one bounded POSIX subprocess.

    The process always receives binary stdin, stdout, and stderr pipes and is
    spawned without a local shell. Callers own an absolute monotonic deadline
    for each read or write.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        clock: Callable[[], float] = time.monotonic,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        graceful_timeout: float = 0.0,
        terminate_timeout: float = 1.0,
        max_stderr_bytes: int = 64 * 1024,
        description: str = "process",
    ):
        if (
            isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(argument, str) for argument in argv)
        ):
            raise ValueError("argv must contain strings")
        if graceful_timeout < 0:
            raise ValueError("graceful_timeout must not be negative")
        if terminate_timeout <= 0:
            raise ValueError("terminate_timeout must be positive")
        if max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")
        if not isinstance(description, str) or not description:
            raise ValueError("description must not be empty")

        self.argv = list(argv)
        self.clock = clock
        self.graceful_timeout = graceful_timeout
        self.terminate_timeout = terminate_timeout
        self.max_stderr_bytes = max_stderr_bytes
        self.description = description
        self.process = None
        self._stdin = None
        self._stdout = None
        self._stderr = None
        self._stderr_bytes = bytearray()
        self._stderr_eof = False
        self._input_closed = False
        self._closed = False
        self._cleanup_error: Optional[ProcessCleanupError] = None

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
                raise ProcessTransportError(
                    "%s did not provide binary standard streams"
                    % self.description
                )
            os.set_blocking(self._stdin.fileno(), False)
            os.set_blocking(self._stdout.fileno(), False)
            os.set_blocking(self._stderr.fileno(), False)
        except BaseException as error:
            try:
                self.close()
            except ProcessCleanupError as cleanup_error:
                if not isinstance(error, Exception):
                    raise error from cleanup_error
                raise ProcessTransportError(
                    "%s setup failed: %s; cleanup failed: %s"
                    % (self.description, error, cleanup_error)
                ) from error
            if not isinstance(error, Exception):
                raise
            if isinstance(error, ProcessTransportError):
                raise
            raise ProcessTransportError(
                "%s setup failed: %s" % (self.description, error)
            ) from error

    @property
    def stderr_tail(self) -> bytes:
        """Return the bounded stderr tail observed so far."""

        return bytes(self._stderr_bytes)

    @property
    def returncode(self) -> Optional[int]:
        """Return the current process status without waiting."""

        if self.process is None:
            return None
        return self.process.poll()

    def require_running_with_empty_stderr(self) -> None:
        """Require a live child and no stderr observed through this instant.

        The stderr drain is nonblocking.  This is intended for interactive
        protocol readiness gates that must not authorize their next action
        based on stdout while a simultaneous diagnostic is already pending.
        """

        if self._closed:
            raise ProcessTransportError(
                "%s stream is closed" % self.description
            )
        self._read_stderr_once_checked()
        if self._stderr_bytes:
            raise ProcessTransportError(
                "%s wrote to stderr: %s"
                % (self.description, self._stderr_text())
            )
        returncode = self.process.poll()
        if returncode is not None:
            raise ProcessTransportError(
                self._exit_message(returncode)
            )

    def write(self, data: bytes, deadline: float) -> None:
        if self._closed:
            raise ProcessTransportError(
                "%s stream is closed" % self.description
            )
        if self._input_closed:
            raise ProcessTransportError(
                "%s input is closed" % self.description
            )
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
                raise ProcessTimeout(
                    "%s write deadline expired" % self.description
                )
            for key, _ in events:
                if key.data == "stderr":
                    self._read_stderr()
                    continue
                try:
                    written = os.write(self._stdin.fileno(), view)
                except (BrokenPipeError, OSError) as error:
                    self._raise_transport(
                        "%s write failed" % self.description,
                        error,
                    )
                if written <= 0:
                    raise ProcessTransportError(
                        "%s write made no progress" % self.description
                    )
                view = view[written:]

    def close_input(self) -> None:
        """Send EOF to the child while retaining its output streams.

        Interactive finite protocols commonly require the child to observe
        stdin EOF before it emits its final response.  This half-close is
        idempotent; process completion and stderr validation remain the
        caller's responsibility through :meth:`read_all`.
        """

        if self._closed:
            raise ProcessTransportError(
                "%s stream is closed" % self.description
            )
        if self._input_closed:
            return
        try:
            self._stdin.close()
        except OSError as error:
            self._raise_transport(
                "%s input close failed" % self.description,
                error,
            )
        self._input_closed = True

    def read(self, max_bytes: int, deadline: float) -> bytes:
        if self._closed:
            raise ProcessTransportError(
                "%s stream is closed" % self.description
            )
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
                raise ProcessTimeout(
                    "%s read deadline expired" % self.description
                )
            for key, _ in events:
                if key.data == "stderr":
                    self._read_stderr()
                    continue
                try:
                    chunk = os.read(self._stdout.fileno(), max_bytes)
                except OSError as error:
                    self._raise_transport(
                        "%s read failed" % self.description,
                        error,
                    )
                if chunk:
                    return chunk
                self._wait_briefly_for_exit(deadline)
                self._raise_transport(
                    "%s closed its output" % self.description
                )

    def read_all(self, max_bytes: int, deadline: float) -> bytes:
        """Read bounded output through a clean status-zero process exit."""

        if self._closed:
            raise ProcessTransportError(
                "%s stream is closed" % self.description
            )
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be a positive integer")

        output = bytearray()
        while True:
            remaining_capacity = (max_bytes + 1) - len(output)
            chunk = self._read_for_completion(
                remaining_capacity,
                deadline,
            )
            if not chunk:
                self._remaining(deadline)
                return bytes(output)
            output.extend(chunk)
            if len(output) > max_bytes:
                raise ProcessTransportError(
                    "%s output exceeded %d bytes"
                    % (self.description, max_bytes)
                )

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
        if stdin is not None and not self._input_closed:
            try:
                stdin.close()
            except OSError:
                pass
            self._input_closed = True
        if process.poll() is None and self.graceful_timeout > 0:
            try:
                process.wait(timeout=self.graceful_timeout)
            except subprocess.TimeoutExpired:
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
                    cleanup_error = ProcessCleanupError(
                        "%s did not exit after bounded terminate/kill waits"
                        % self.description
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

    def __enter__(self) -> "BoundedProcess":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ProcessTimeout(
                "%s deadline expired" % self.description
            )
        return remaining

    def _read_stderr(self) -> None:
        try:
            chunk = os.read(self._stderr.fileno(), 4096)
        except BlockingIOError:
            return
        except OSError as error:
            self._raise_transport(
                "failed to read %s stderr" % self.description,
                error,
            )
        if not chunk:
            self._stderr_eof = True
            return
        self._append_stderr(chunk)

    def _read_for_completion(
        self,
        max_bytes: int,
        deadline: float,
    ) -> bytes:
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
                raise ProcessTimeout(
                    "%s read deadline expired" % self.description
                )
            for key, _ in events:
                if key.data == "stderr":
                    self._read_stderr()
                    continue
                try:
                    chunk = os.read(self._stdout.fileno(), max_bytes)
                except OSError as error:
                    self._raise_transport(
                        "%s read failed" % self.description,
                        error,
                    )
                if chunk:
                    return chunk
                self._require_clean_exit(deadline)
                return b""

    def _require_clean_exit(self, deadline: float) -> None:
        while self.process.poll() is None:
            if self._stderr_eof:
                try:
                    self.process.wait(timeout=self._remaining(deadline))
                except subprocess.TimeoutExpired as error:
                    raise ProcessTimeout(
                        "%s read deadline expired" % self.description
                    ) from error
                break
            with selectors.DefaultSelector() as selector:
                selector.register(
                    self._stderr,
                    selectors.EVENT_READ,
                    "stderr",
                )
                events = selector.select(self._remaining(deadline))
            if not events:
                raise ProcessTimeout(
                    "%s read deadline expired" % self.description
                )
            self._read_stderr()

        self._drain_stderr()
        returncode = self.process.poll()
        if returncode != 0:
            self._raise_transport(
                "%s exited without a clean status-zero EOF"
                % self.description
            )

    def _raise_if_exited(self) -> None:
        returncode = self.process.poll()
        if returncode is None:
            return
        self._drain_stderr()
        raise ProcessTransportError(self._exit_message(returncode))

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

    def _read_stderr_once_checked(self) -> None:
        if self._stderr_eof:
            return
        try:
            chunk = os.read(self._stderr.fileno(), 4096)
        except BlockingIOError:
            return
        except OSError as error:
            self._raise_transport(
                "failed to read %s stderr" % self.description,
                error,
            )
        if not chunk:
            self._stderr_eof = True
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
        raise ProcessTransportError(": ".join(pieces))

    def _exit_message(self, returncode: int) -> str:
        message = "%s exited with status %d" % (
            self.description,
            returncode,
        )
        detail = self._stderr_text()
        return "%s: %s" % (message, detail) if detail else message

    def _stderr_text(self) -> str:
        return bytes(self._stderr_bytes).decode("utf-8", "replace").strip()
