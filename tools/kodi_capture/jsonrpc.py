"""Strict JSON-RPC codec and client."""

from __future__ import annotations

import codecs
import json
import threading
import time
from typing import Any, Callable, Optional


class JsonRpcError(Exception):
    """Base class for capture JSON-RPC failures."""


class JsonRpcProtocolError(JsonRpcError):
    """The peer returned bytes or JSON that violate the expected protocol."""


class JsonRpcTimeout(JsonRpcError):
    """The absolute operation deadline expired."""


class JsonRpcTransportError(JsonRpcError):
    """The byte stream failed before a valid response arrived."""


class JsonRpcRemoteError(JsonRpcError):
    """Kodi returned a JSON-RPC error response."""

    def __init__(self, request_id: Any, error: Any):
        self.request_id = request_id
        self.error = error
        super().__init__(
            "JSON-RPC request %r failed: %s"
            % (request_id, _describe_remote_error(error))
        )


def _describe_remote_error(error: Any) -> str:
    if not isinstance(error, dict):
        return repr(error)
    code = error.get("code")
    message = error.get("message")
    if code is None and message is None:
        return repr(error)
    return "%s: %s" % (code, message)


class JsonValueDecoder:
    """Incrementally decode concatenated UTF-8 JSON values.

    The input limit bounds the complete stream as well as incomplete buffering.
    """

    def __init__(self, max_input_bytes: int = 1024 * 1024):
        if max_input_bytes <= 0:
            raise ValueError("max_input_bytes must be positive")
        self.max_input_bytes = max_input_bytes
        self._input_bytes = 0
        self._utf8 = codecs.getincrementaldecoder("utf-8")("strict")
        self._json = json.JSONDecoder()
        self._text = ""
        self._finished = False

    def feed(self, data: bytes) -> list[Any]:
        if self._finished:
            raise JsonRpcProtocolError("JSON decoder is already finished")
        if not isinstance(data, bytes):
            raise TypeError("JSON stream chunks must be bytes")
        self._input_bytes += len(data)
        if self._input_bytes > self.max_input_bytes:
            raise JsonRpcProtocolError(
                "JSON input exceeds %d bytes" % self.max_input_bytes
            )
        try:
            self._text += self._utf8.decode(data, final=False)
        except UnicodeDecodeError as error:
            raise JsonRpcProtocolError(
                "JSON stream is not valid UTF-8: %s" % error
            ) from error
        values = self._drain_values()
        return values

    def finish(self) -> list[Any]:
        if self._finished:
            return []
        self._finished = True
        try:
            self._text += self._utf8.decode(b"", final=True)
        except UnicodeDecodeError as error:
            raise JsonRpcProtocolError(
                "JSON stream ended within a UTF-8 sequence"
            ) from error
        values = self._drain_values()
        if self._text.strip():
            raise JsonRpcProtocolError(
                "JSON stream ended with an incomplete or malformed value"
            )
        self._text = ""
        return values

    @property
    def has_pending(self) -> bool:
        buffered_utf8, _ = self._utf8.getstate()
        return bool(self._text.strip() or buffered_utf8)

    def _drain_values(self) -> list[Any]:
        values = []
        while True:
            stripped = self._text.lstrip()
            if not stripped:
                self._text = ""
                break
            try:
                value, end = self._json.raw_decode(stripped)
            except json.JSONDecodeError:
                self._text = stripped
                break
            values.append(value)
            self._text = stripped[end:]
        return values


class JsonRpcClient:
    """Synchronous JSON-RPC client with exactly one outstanding call.

    Any ambiguous failure after writing poisons and closes the client. A valid
    response followed by incomplete data is rejected instead of being carried
    into another call.
    """

    def __init__(
        self,
        stream: Any,
        request_id_factory: Callable[[], Any],
        *,
        clock: Callable[[], float] = time.monotonic,
        max_response_bytes: int = 1024 * 1024,
        read_size: int = 64 * 1024,
        notification_handler: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if read_size <= 0:
            raise ValueError("read_size must be positive")
        self.stream = stream
        self.request_id_factory = request_id_factory
        self.clock = clock
        self.max_response_bytes = max_response_bytes
        self.read_size = read_size
        self.notification_handler = notification_handler
        self._used_request_ids: set[Any] = set()
        self._call_lock = threading.Lock()
        self._poisoned = False
        self._stream_closed = False
        self._cleanup_error = None

    def call(
        self,
        method: str,
        params: Any,
        *,
        deadline: float,
    ) -> Any:
        if not self._call_lock.acquire(blocking=False):
            raise JsonRpcProtocolError(
                "only one JSON-RPC call may be outstanding"
            )
        wrote_request = False
        validated_remote_error = None
        try:
            if self._poisoned:
                raise JsonRpcTransportError(
                    "JSON-RPC client is closed after an ambiguous failure"
                )
            self._require_time(deadline)
            request_id = self._next_request_id()
            request = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": request_id,
            }
            payload = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            wrote_request = True
            self.stream.write(payload, deadline)

            decoder = JsonValueDecoder(self.max_response_bytes)
            while True:
                self._require_time(deadline)
                chunk = self.stream.read(self.read_size, deadline)
                self._require_time(deadline)
                if chunk == b"":
                    values = decoder.finish()
                    outcome = self._inspect_values(values, request_id)
                    if outcome is _NO_RESPONSE:
                        raise JsonRpcTransportError(
                            "JSON-RPC stream closed before response %r"
                            % request_id
                        )
                else:
                    values = decoder.feed(chunk)
                    outcome = self._inspect_values(values, request_id)
                    if outcome is _NO_RESPONSE:
                        continue

                if decoder.has_pending:
                    raise JsonRpcProtocolError(
                        "JSON-RPC response has incomplete trailing data"
                    )
                self._require_time(deadline)
                if isinstance(outcome, _RemoteFailure):
                    validated_remote_error = JsonRpcRemoteError(
                        outcome.request_id,
                        outcome.error,
                    )
                    raise validated_remote_error
                return outcome
        except BaseException as error:
            if error is validated_remote_error:
                raise
            if wrote_request:
                cleanup_error = self._poison()
                if cleanup_error is not None:
                    try:
                        error.cleanup_error = cleanup_error
                    except Exception:
                        pass
                    raise error from cleanup_error
            raise
        finally:
            self._call_lock.release()

    def close(self) -> None:
        self._poisoned = True
        previous_error = self._cleanup_error
        try:
            self._close_stream()
        except BaseException as error:
            if self._cleanup_error is None:
                self._cleanup_error = error
            raise self._cleanup_error
        if previous_error is not None:
            raise previous_error

    def _poison(self) -> Optional[BaseException]:
        self._poisoned = True
        try:
            self._close_stream()
        except BaseException as error:
            if self._cleanup_error is None:
                self._cleanup_error = error
            return self._cleanup_error
        return self._cleanup_error

    def _close_stream(self) -> None:
        if self._stream_closed:
            return
        self.stream.close()
        self._stream_closed = True

    def _next_request_id(self) -> Any:
        request_id = self.request_id_factory()
        if isinstance(request_id, bool) or not isinstance(
            request_id, (int, str)
        ):
            raise JsonRpcProtocolError(
                "request IDs must be non-boolean strings or integers"
            )
        if request_id in self._used_request_ids:
            raise JsonRpcProtocolError(
                "request ID factory reused %r" % request_id
            )
        self._used_request_ids.add(request_id)
        return request_id

    def _require_time(self, deadline: float) -> None:
        if self.clock() >= deadline:
            raise JsonRpcTimeout("JSON-RPC deadline expired")

    def _inspect_values(self, values: list[Any], request_id: Any) -> Any:
        response = _NO_RESPONSE
        remote_error = _NO_RESPONSE
        for value in values:
            if _is_notification(value):
                if self.notification_handler is not None:
                    self.notification_handler(value)
                continue
            if not isinstance(value, dict):
                raise JsonRpcProtocolError(
                    "JSON-RPC peer returned a non-object value"
                )
            if "id" not in value:
                raise JsonRpcProtocolError(
                    "JSON-RPC response has no id"
                )
            if not _same_json_id(value["id"], request_id):
                raise JsonRpcProtocolError(
                    "JSON-RPC response id %r does not match %r"
                    % (value["id"], request_id)
                )
            if (
                response is not _NO_RESPONSE
                or remote_error is not _NO_RESPONSE
            ):
                raise JsonRpcProtocolError(
                    "JSON-RPC peer returned duplicate response %r"
                    % request_id
                )
            if value.get("jsonrpc") != "2.0":
                raise JsonRpcProtocolError(
                    "JSON-RPC response has an invalid version"
                )
            has_result = "result" in value
            has_error = "error" in value
            if has_result == has_error:
                raise JsonRpcProtocolError(
                    "JSON-RPC response must contain exactly one of result/error"
                )
            if has_error:
                _validate_remote_error(value["error"])
                remote_error = value["error"]
            else:
                response = value["result"]

        if remote_error is not _NO_RESPONSE:
            return _RemoteFailure(request_id, remote_error)
        return response


def _is_notification(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("jsonrpc") != "2.0" or "id" in value:
        return False
    if not isinstance(value.get("method"), str):
        return False
    if "result" in value or "error" in value:
        return False
    params = value.get("params", _NO_RESPONSE)
    return params is _NO_RESPONSE or isinstance(params, (dict, list))


def _validate_remote_error(error: Any) -> None:
    if not isinstance(error, dict):
        raise JsonRpcProtocolError("JSON-RPC error must be an object")
    code = error.get("code")
    message = error.get("message")
    if isinstance(code, bool) or not isinstance(code, int):
        raise JsonRpcProtocolError(
            "JSON-RPC error code must be an integer"
        )
    if not isinstance(message, str):
        raise JsonRpcProtocolError(
            "JSON-RPC error message must be a string"
        )


def _same_json_id(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


_NO_RESPONSE = object()


class _RemoteFailure:
    def __init__(self, request_id: Any, error: dict[str, Any]):
        self.request_id = request_id
        self.error = error
