from __future__ import annotations

import json
import unittest
from collections import deque

from tools.kodi_capture.jsonrpc import (
    JsonRpcClient,
    JsonRpcProtocolError,
    JsonRpcRemoteError,
    JsonRpcTimeout,
    JsonRpcTransportError,
    JsonValueDecoder,
)


class JsonValueDecoderTest(unittest.TestCase):
    def test_fragmentation_at_every_byte_including_multibyte_utf8(self):
        value = {"title": "Snowman ☃", "items": [1, 2, 3]}
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")

        for split in range(len(payload) + 1):
            with self.subTest(split=split):
                decoder = JsonValueDecoder()
                values = decoder.feed(payload[:split])
                values += decoder.feed(payload[split:])
                values += decoder.finish()
                self.assertEqual(values, [value])

        decoder = JsonValueDecoder()
        values = []
        for byte in payload:
            values.extend(decoder.feed(bytes([byte])))
        values.extend(decoder.finish())
        self.assertEqual(values, [value])

    def test_concatenated_values_and_whitespace(self):
        decoder = JsonValueDecoder()
        values = decoder.feed(b' \n{"one":1}\t[2,3]  "four" ')
        values += decoder.finish()
        self.assertEqual(values, [{"one": 1}, [2, 3], "four"])

    def test_malformed_or_incomplete_eof_is_rejected(self):
        for payload in (b'{"broken":]', b'{"incomplete":'):
            with self.subTest(payload=payload):
                decoder = JsonValueDecoder()
                self.assertEqual(decoder.feed(payload), [])
                with self.assertRaises(JsonRpcProtocolError):
                    decoder.finish()

    def test_invalid_utf8_is_rejected(self):
        decoder = JsonValueDecoder()
        with self.assertRaises(JsonRpcProtocolError):
            decoder.feed(b'{"bad":"\xff"}')

    def test_incomplete_utf8_at_eof_is_rejected(self):
        decoder = JsonValueDecoder()
        decoder.feed(b'{"bad":"\xe2\x98')
        with self.assertRaises(JsonRpcProtocolError):
            decoder.finish()

    def test_input_is_bounded_for_incomplete_and_complete_values(self):
        decoder = JsonValueDecoder(max_input_bytes=8)
        with self.assertRaises(JsonRpcProtocolError):
            decoder.feed(b'{"unfinished')

        decoder = JsonValueDecoder(max_input_bytes=8)
        with self.assertRaises(JsonRpcProtocolError):
            decoder.feed(b'{"complete":true}')


class FakeByteStream(object):
    def __init__(self, chunks=()):
        self.chunks = deque(chunks)
        self.writes = []
        self.closed = False

    def write(self, data, deadline):
        self.writes.append((data, deadline))

    def read(self, _max_bytes, _deadline):
        return self.chunks.popleft() if self.chunks else b""

    def close(self):
        self.closed = True


class FailingCloseStream(FakeByteStream):
    def __init__(self, chunks=()):
        super().__init__(chunks)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        raise JsonRpcTransportError("simulated reap failure")


class JsonRpcClientTest(unittest.TestCase):
    def test_request_and_exact_response_id(self):
        stream = FakeByteStream(
            [b'{"jsonrpc":"2.0","id":"request-1","result":{"ok":true}}']
        )
        client = JsonRpcClient(
            stream,
            lambda: "request-1",
            clock=lambda: 1.0,
        )

        self.assertEqual(
            client.call("Test.Method", {"value": "☃"}, deadline=2.0),
            {"ok": True},
        )
        request = json.loads(stream.writes[0][0].decode("utf-8"))
        self.assertEqual(
            request,
            {
                "jsonrpc": "2.0",
                "method": "Test.Method",
                "params": {"value": "☃"},
                "id": "request-1",
            },
        )
        self.assertEqual(stream.writes[0][1], 2.0)

    def test_notifications_are_tolerated_around_response(self):
        notifications = []
        stream = FakeByteStream(
            [
                (
                    b'{"jsonrpc":"2.0","method":"Player.OnPlay","params":{}}'
                    b'{"jsonrpc":"2.0","id":7,"result":"ok"}'
                    b'{"jsonrpc":"2.0","method":"Player.OnAVStart"}'
                )
            ]
        )
        client = JsonRpcClient(
            stream,
            lambda: 7,
            clock=lambda: 1.0,
            notification_handler=notifications.append,
        )
        self.assertEqual(client.call("Test", {}, deadline=2.0), "ok")
        self.assertEqual(
            [item["method"] for item in notifications],
            ["Player.OnPlay", "Player.OnAVStart"],
        )

    def test_wrong_missing_and_boolean_ids_are_rejected(self):
        responses = [
            b'{"jsonrpc":"2.0","id":"other","result":true}',
            b'{"jsonrpc":"2.0","result":true}',
            b'{"jsonrpc":"2.0","id":true,"result":true}',
        ]
        for response in responses:
            with self.subTest(response=response):
                client = JsonRpcClient(
                    FakeByteStream([response]),
                    lambda: 1,
                    clock=lambda: 1.0,
                )
                with self.assertRaises(JsonRpcProtocolError):
                    client.call("Test", {}, deadline=2.0)

    def test_duplicate_or_trailing_response_is_rejected(self):
        responses = [
            (
                b'{"jsonrpc":"2.0","id":1,"result":"first"}'
                b'{"jsonrpc":"2.0","id":1,"result":"second"}'
            ),
            (
                b'{"jsonrpc":"2.0","id":1,"result":"first"}'
                b'{"jsonrpc":"2.0","id":2,"result":"trailing"}'
            ),
            (
                b'{"jsonrpc":"2.0","id":1,"result":"first"}'
                b'{"jsonrpc":'
            ),
        ]
        for response in responses:
            with self.subTest(response=response):
                client = JsonRpcClient(
                    FakeByteStream([response]),
                    lambda: 1,
                    clock=lambda: 1.0,
                )
                with self.assertRaises(JsonRpcProtocolError):
                    client.call("Test", {}, deadline=2.0)
                self.assertTrue(client.stream.closed)
                with self.assertRaises(JsonRpcTransportError):
                    client.call("Again", {}, deadline=2.0)
                self.assertEqual(len(client.stream.writes), 1)

    def test_remote_error_is_typed(self):
        error = {"code": -32602, "message": "Invalid params"}
        stream = FakeByteStream(
            [
                json.dumps(
                    {"jsonrpc": "2.0", "id": "request", "error": error}
                ).encode("utf-8")
            ]
        )
        client = JsonRpcClient(
            stream,
            lambda: "request",
            clock=lambda: 1.0,
        )
        with self.assertRaises(JsonRpcRemoteError) as raised:
            client.call("Test", {}, deadline=2.0)
        self.assertEqual(raised.exception.request_id, "request")
        self.assertEqual(raised.exception.error, error)

    def test_malformed_responses_and_notifications_are_protocol_errors(self):
        responses = [
            b'{"jsonrpc":"1.0","id":1,"result":true}',
            (
                b'{"jsonrpc":"2.0","id":1,"result":true,'
                b'"error":{"code":1,"message":"bad"}}'
            ),
            b'{"jsonrpc":"2.0","id":1,"error":null}',
            b'{"jsonrpc":"2.0","id":1,"error":{"code":true,"message":"bad"}}',
            b'{"jsonrpc":"2.0","id":1,"error":{"code":1,"message":7}}',
            b'{"jsonrpc":"2.0","method":"Notice","result":true}',
            b'{"jsonrpc":"2.0","method":"Notice","params":"bad"}',
        ]
        for response in responses:
            with self.subTest(response=response):
                stream = FakeByteStream([response])
                client = JsonRpcClient(stream, lambda: 1, clock=lambda: 1.0)
                with self.assertRaises(JsonRpcProtocolError):
                    client.call("Test", {}, deadline=2.0)
                self.assertTrue(stream.closed)

    def test_complete_oversized_response_poisons_client(self):
        stream = FakeByteStream(
            [b'{"jsonrpc":"2.0","id":1,"result":"oversized"}']
        )
        client = JsonRpcClient(
            stream,
            lambda: 1,
            clock=lambda: 1.0,
            max_response_bytes=16,
        )
        with self.assertRaises(JsonRpcProtocolError):
            client.call("Test", {}, deadline=2.0)
        self.assertTrue(stream.closed)

    def test_exact_remote_error_does_not_poison_client(self):
        request_ids = iter([1, 2])
        stream = FakeByteStream(
            [
                (
                    b'{"jsonrpc":"2.0","id":1,'
                    b'"error":{"code":-1,"message":"retry"}}'
                ),
                b'{"jsonrpc":"2.0","id":2,"result":"ok"}',
            ]
        )
        client = JsonRpcClient(
            stream,
            lambda: next(request_ids),
            clock=lambda: 1.0,
        )
        with self.assertRaises(JsonRpcRemoteError):
            client.call("First", {}, deadline=2.0)
        self.assertFalse(stream.closed)
        self.assertEqual(client.call("Second", {}, deadline=2.0), "ok")
        self.assertEqual(len(stream.writes), 2)

    def test_absolute_deadline_expires_between_notifications(self):
        now = [1.0]

        class AdvancingStream(FakeByteStream):
            def read(self, max_bytes, deadline):
                chunk = super().read(max_bytes, deadline)
                now[0] = deadline
                return chunk

        stream = AdvancingStream(
            [b'{"jsonrpc":"2.0","method":"Player.OnPlay"}']
        )
        client = JsonRpcClient(stream, lambda: 1, clock=lambda: now[0])
        with self.assertRaises(JsonRpcTimeout):
            client.call("Test", {}, deadline=2.0)
        self.assertTrue(stream.closed)

    def test_deadline_is_rechecked_after_reading_valid_response(self):
        now = [1.0]

        class AdvancingStream(FakeByteStream):
            def read(self, max_bytes, deadline):
                chunk = super().read(max_bytes, deadline)
                now[0] = deadline
                return chunk

        stream = AdvancingStream(
            [b'{"jsonrpc":"2.0","id":1,"result":"too-late"}']
        )
        client = JsonRpcClient(stream, lambda: 1, clock=lambda: now[0])
        with self.assertRaises(JsonRpcTimeout):
            client.call("Test", {}, deadline=2.0)
        self.assertTrue(stream.closed)
        with self.assertRaises(JsonRpcTransportError):
            client.call("Again", {}, deadline=3.0)
        self.assertEqual(len(stream.writes), 1)

    def test_request_ids_may_not_be_reused(self):
        stream = FakeByteStream(
            [
                b'{"jsonrpc":"2.0","id":"same","result":1}',
                b'{"jsonrpc":"2.0","id":"same","result":2}',
            ]
        )
        client = JsonRpcClient(stream, lambda: "same", clock=lambda: 1.0)
        self.assertEqual(client.call("One", {}, deadline=2.0), 1)
        with self.assertRaises(JsonRpcProtocolError):
            client.call("Two", {}, deadline=2.0)
        self.assertEqual(len(stream.writes), 1)

    def test_notification_handler_cannot_start_a_second_call(self):
        nested_errors = []
        stream = FakeByteStream(
            [
                (
                    b'{"jsonrpc":"2.0","method":"Player.OnPlay"}'
                    b'{"jsonrpc":"2.0","id":1,"result":"ok"}'
                )
            ]
        )
        client = None

        def handle_notification(_notification):
            try:
                client.call("Nested", {}, deadline=2.0)
            except JsonRpcProtocolError as error:
                nested_errors.append(str(error))

        client = JsonRpcClient(
            stream,
            lambda: 1,
            clock=lambda: 1.0,
            notification_handler=handle_notification,
        )
        self.assertEqual(client.call("Outer", {}, deadline=2.0), "ok")
        self.assertEqual(
            nested_errors,
            ["only one JSON-RPC call may be outstanding"],
        )

    def test_notification_handler_failure_poisons_matching_response(self):
        stream = FakeByteStream(
            [
                (
                    b'{"jsonrpc":"2.0","method":"Player.OnPlay"}'
                    b'{"jsonrpc":"2.0","id":1,"result":"unused"}'
                )
            ]
        )

        def fail_handler(_notification):
            raise RuntimeError("handler failed")

        client = JsonRpcClient(
            stream,
            lambda: 1,
            clock=lambda: 1.0,
            notification_handler=fail_handler,
        )
        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            client.call("First", {}, deadline=2.0)
        self.assertTrue(stream.closed)
        with self.assertRaises(JsonRpcTransportError):
            client.call("Second", {}, deadline=2.0)
        self.assertEqual(len(stream.writes), 1)

    def test_unexpected_partial_write_failure_poisons_client(self):
        class PartialWriteStream(FakeByteStream):
            def write(self, data, deadline):
                self.writes.append((data[:1], deadline))
                raise RuntimeError("partial write failed")

        stream = PartialWriteStream()
        client = JsonRpcClient(stream, lambda: 1, clock=lambda: 1.0)
        with self.assertRaisesRegex(RuntimeError, "partial write failed"):
            client.call("First", {}, deadline=2.0)
        self.assertTrue(stream.closed)
        with self.assertRaises(JsonRpcTransportError):
            client.call("Second", {}, deadline=2.0)
        self.assertEqual(len(stream.writes), 1)

    def test_poison_retains_and_reports_close_failure(self):
        stream = FailingCloseStream(
            [b'{"jsonrpc":"2.0","id":"wrong","result":true}']
        )
        client = JsonRpcClient(stream, lambda: 1, clock=lambda: 1.0)
        with self.assertRaises(JsonRpcProtocolError) as raised:
            client.call("First", {}, deadline=2.0)
        self.assertIsInstance(
            raised.exception.cleanup_error,
            JsonRpcTransportError,
        )
        self.assertIs(
            raised.exception.__cause__,
            raised.exception.cleanup_error,
        )
        with self.assertRaisesRegex(
            JsonRpcTransportError,
            "simulated reap failure",
        ):
            client.close()
        self.assertEqual(stream.close_calls, 2)
        self.assertEqual(len(stream.writes), 1)

    def test_stream_close_before_response_is_transport_error(self):
        stream = FakeByteStream()
        client = JsonRpcClient(
            stream,
            lambda: 1,
            clock=lambda: 1.0,
        )
        with self.assertRaises(JsonRpcTransportError):
            client.call("Test", {}, deadline=2.0)
        self.assertTrue(stream.closed)
        with self.assertRaises(JsonRpcTransportError):
            client.call("Again", {}, deadline=2.0)
        self.assertEqual(len(stream.writes), 1)


if __name__ == "__main__":
    unittest.main()
