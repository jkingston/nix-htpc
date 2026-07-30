from __future__ import absolute_import, division, print_function

import json
import unittest
from unittest import mock

import watchdog


class ResponseTransport(object):
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def __call__(self, payload, timeout, expected_id):
        request = json.loads(payload.decode("utf-8"))
        if request["id"] != expected_id:
            raise AssertionError("transport expected the wrong response id")
        self.calls.append((request, timeout))
        return json.dumps(self.responder(request))


def response(request, result):
    return {
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": result,
    }


class JsonRpcClientTest(unittest.TestCase):
    def test_exact_details_label_and_mutation_contracts(self):
        def respond(request):
            if request["method"] == "Addons.GetAddonDetails":
                return response(
                    request,
                    {
                        "addon": {
                            "addonid": watchdog.ADDON_ID,
                            "enabled": True,
                            "version": "9.8.7",
                        }
                    },
                )
            if request["method"] == "XBMC.GetInfoLabels":
                return response(
                    request,
                    {watchdog.READY_LABEL: "true"},
                )
            return response(request, "OK")

        transport = ResponseTransport(respond)
        client = watchdog.KodiJsonRpcClient(transport=transport)

        self.assertTrue(client.addon_enabled(watchdog.ADDON_ID, "9.8.7"))
        self.assertTrue(client.ready())
        client.set_enabled(watchdog.ADDON_ID, False)

        requests = [item[0] for item in transport.calls]
        self.assertEqual(
            requests[0]["params"],
            {
                "addonid": watchdog.ADDON_ID,
                "properties": ["enabled", "version"],
            },
        )
        self.assertEqual(
            requests[1]["params"],
            {"labels": [watchdog.READY_LABEL]},
        )
        self.assertEqual(
            requests[2]["params"],
            {"addonid": watchdog.ADDON_ID, "enabled": False},
        )
        self.assertEqual(
            [item[1] for item in transport.calls],
            [7.0, 7.0, 7.0],
        )
        self.assertEqual(
            set(request["method"] for request in requests),
            {
                "Addons.GetAddonDetails",
                "XBMC.GetInfoLabels",
                "Addons.SetAddonEnabled",
            },
        )

    def test_forbidden_method_is_rejected_before_transport(self):
        transport = ResponseTransport(lambda request: response(request, {}))
        client = watchdog.KodiJsonRpcClient(transport=transport)

        with self.assertRaises(watchdog.JsonRpcError):
            client.call("Player.Stop", {})

        self.assertEqual(transport.calls, [])

    def test_response_envelope_is_strict(self):
        malformed = (
            [],
            {"id": "ignored", "result": {}},
            {"jsonrpc": "1.0", "id": "ignored", "result": {}},
            {"jsonrpc": "2.0", "id": "wrong", "result": {}},
            {"jsonrpc": "2.0", "id": "ignored", "error": None},
            {"jsonrpc": "2.0", "id": "ignored"},
        )
        for template in malformed:
            with self.subTest(template=template):
                def respond(request, template=template):
                    value = dict(template) if isinstance(template, dict) else template
                    if isinstance(value, dict) and value.get("id") == "ignored":
                        value["id"] = request["id"]
                    return value

                client = watchdog.KodiJsonRpcClient(
                    transport=ResponseTransport(respond),
                )
                with self.assertRaises(watchdog.JsonRpcError):
                    client.call("XBMC.GetInfoLabels", {"labels": []})

    def test_details_require_exact_version_and_boolean_enabled(self):
        cases = (
            {
                "addonid": watchdog.ADDON_ID,
                "enabled": True,
                "version": "wrong",
            },
            {
                "addonid": "another.addon",
                "enabled": True,
                "version": "1",
            },
            {
                "addonid": watchdog.ADDON_ID,
                "enabled": 1,
                "version": "1",
            },
        )
        for addon in cases:
            with self.subTest(addon=addon):
                transport = ResponseTransport(
                    lambda request, addon=addon: response(
                        request,
                        {"addon": addon},
                    )
                )
                client = watchdog.KodiJsonRpcClient(transport=transport)
                with self.assertRaises(watchdog.JsonRpcError):
                    client.addon_enabled(watchdog.ADDON_ID, "1")

    def test_mutation_requires_exact_acknowledgement(self):
        for result in (True, None, "ok", {"status": "OK"}):
            with self.subTest(result=result):
                transport = ResponseTransport(
                    lambda request, result=result: response(request, result)
                )
                client = watchdog.KodiJsonRpcClient(transport=transport)
                with self.assertRaises(watchdog.JsonRpcError):
                    client.set_enabled(watchdog.ADDON_ID, True)


class FakeSocket(object):
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []
        self.timeouts = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendall(self, payload):
        self.sent.append(payload)

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""

    def close(self):
        self.closed = True


class SocketTransportTest(unittest.TestCase):
    def invoke(self, chunks):
        connection = FakeSocket(chunks)
        with mock.patch(
            "watchdog.socket.create_connection",
            return_value=connection,
        ):
            result = watchdog.socket_transport(b"request", 7.0, "one")
        return result, connection

    def test_response_is_incremental_json_not_newline_framed(self):
        payload = '{"jsonrpc":"2.0","id":"one","result":"☃"}'.encode("utf-8")
        for split in range(len(payload) + 1):
            with self.subTest(split=split):
                result, connection = self.invoke(
                    tuple(
                        fragment
                        for fragment in (payload[:split], payload[split:])
                        if fragment
                    ),
                )
                self.assertEqual(json.loads(result)["result"], "☃")
                self.assertEqual(connection.sent, [b"request"])
                self.assertTrue(connection.closed)

    def test_incomplete_malformed_and_trailing_values_fail_closed(self):
        cases = (
            (b'{"incomplete":',),
            (b'{"broken":]',),
            (b'{"one":1}{"two":2}',),
            (b'{"bad":"\xff"}',),
        )
        for chunks in cases:
            with self.subTest(chunks=chunks):
                with self.assertRaises(watchdog.JsonRpcError):
                    self.invoke(chunks)

    def test_notification_before_matching_response_is_ignored(self):
        notification = (
            b'{"jsonrpc":"2.0","method":"Player.OnPlay","params":{}}'
        )
        response = b'{"jsonrpc":"2.0","id":"one","result":true}'
        payload = notification + response

        for split in range(len(payload) + 1):
            with self.subTest(split=split):
                result, _connection = self.invoke(
                    tuple(
                        fragment
                        for fragment in (payload[:split], payload[split:])
                        if fragment
                    ),
                )
                self.assertTrue(json.loads(result)["result"])

    def test_wrong_response_id_fails_closed(self):
        with self.assertRaises(watchdog.JsonRpcError):
            self.invoke(
                (b'{"jsonrpc":"2.0","id":"another","result":true}',),
            )

    def test_response_size_is_bounded(self):
        oversized = b" " * (watchdog.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(watchdog.JsonRpcError):
            self.invoke((oversized,))


class FakeClient(object):
    def __init__(self, enabled=True, ready=True):
        self.enabled = enabled
        self.ready_value = ready
        self.calls = []
        self.failures = []

    def _fail(self):
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure

    def addon_enabled(self, addon_id, version):
        self.calls.append(("details", addon_id, version))
        self._fail()
        return self.enabled

    def ready(self):
        self.calls.append(("ready",))
        self._fail()
        return self.ready_value

    def set_enabled(self, addon_id, enabled):
        self.calls.append(("set", addon_id, enabled))
        self._fail()
        self.enabled = enabled


class WatchdogStateTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.sleeps = []
        self.logs = []

    def make_watchdog(self, client, grace=10.0):
        return watchdog.Watchdog(
            client,
            expected_version="2.2.0",
            clock=lambda: self.now[0],
            sleeper=self.sleeps.append,
            startup_grace=grace,
            logger=self.logs.append,
        )

    def test_enabled_missing_lease_gets_startup_grace_then_restarts(self):
        client = FakeClient(enabled=True, ready=False)
        state = self.make_watchdog(client)

        self.assertEqual(state.step(), (1.0, "grace"))
        self.now[0] = 109.999
        self.assertEqual(state.step(), (1.0, "grace"))
        self.now[0] = 110.0
        self.assertEqual(state.step(), (1.0, "restarted"))

        self.assertEqual(
            client.calls[-4:],
            [
                ("details", watchdog.ADDON_ID, "2.2.0"),
                ("ready",),
                ("set", watchdog.ADDON_ID, False),
                ("set", watchdog.ADDON_ID, True),
            ],
        )
        self.assertEqual(self.sleeps, [0.250])
        self.assertEqual(state.grace_deadline, 120.0)

    def test_observed_lease_expiry_restarts_without_startup_grace(self):
        client = FakeClient(enabled=True, ready=True)
        state = self.make_watchdog(client)

        self.assertEqual(state.step(), (1.0, "healthy"))
        client.ready_value = False
        self.now[0] = 101.0
        self.assertEqual(state.step(), (1.0, "restarted"))
        self.assertEqual(self.sleeps, [0.250])

    def test_disabled_addon_is_enabled_and_given_grace(self):
        client = FakeClient(enabled=False, ready=False)
        state = self.make_watchdog(client)
        self.now[0] = 103.0

        self.assertEqual(state.step(), (1.0, "enabled"))
        self.assertEqual(
            client.calls,
            [
                ("details", watchdog.ADDON_ID, "2.2.0"),
                ("set", watchdog.ADDON_ID, True),
            ],
        )
        self.assertEqual(state.grace_deadline, 113.0)

    def test_disable_without_ack_never_issues_blind_enable(self):
        client = FakeClient(enabled=True, ready=False)
        client.failures = [None, None, RuntimeError("no disable ack")]
        state = self.make_watchdog(client, grace=0.0)

        self.assertEqual(state.step(), (1.0, "error"))
        self.assertEqual(
            client.calls,
            [
                ("details", watchdog.ADDON_ID, "2.2.0"),
                ("ready",),
                ("set", watchdog.ADDON_ID, False),
            ],
        )
        self.assertEqual(self.sleeps, [])

    def test_failures_back_off_1_2_4_8_and_success_resets(self):
        client = FakeClient(enabled=True, ready=True)
        client.failures = [RuntimeError("offline")] * 5
        state = self.make_watchdog(client)

        self.assertEqual(
            [state.step()[0] for _item in range(5)],
            [1.0, 2.0, 4.0, 8.0, 8.0],
        )
        self.assertEqual(state.step(), (1.0, "healthy"))
        client.failures = [RuntimeError("offline")]
        self.assertEqual(state.step(), (1.0, "error"))

    def test_grace_uses_time_after_rpc_probes(self):
        client = FakeClient(enabled=True, ready=False)
        state = self.make_watchdog(client)

        original_ready = client.ready

        def delayed_ready():
            result = original_ready()
            self.now[0] = 110.0
            return result

        client.ready = delayed_ready
        self.assertEqual(state.step(), (1.0, "restarted"))


if __name__ == "__main__":
    unittest.main()
