#!/usr/bin/env python3
from __future__ import absolute_import, division, print_function

import codecs
import json
import socket
import sys
import time


ADDON_ID = "service.htpc.settings"
EXPECTED_ADDON_VERSION = "@KODI_SETTINGS_ADDON_VERSION@"
READY_LABEL = "Window(Home).Property(htpc.service.ready)"
RPC_HOST = "127.0.0.1"
RPC_PORT = 9090
RPC_TIMEOUT_SECONDS = 7.0
SETTLE_SECONDS = 0.250
STARTUP_GRACE_SECONDS = 10.0
HEALTHY_POLL_SECONDS = 1.0
BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)
MAX_RESPONSE_BYTES = 65536
ALLOWED_METHODS = frozenset(
    (
        "XBMC.GetInfoLabels",
        "Addons.GetAddonDetails",
        "Addons.SetAddonEnabled",
    )
)


class WatchdogError(Exception):
    pass


class JsonRpcError(WatchdogError):
    pass


def _is_notification(value):
    return (
        isinstance(value, dict)
        and value.get("jsonrpc") == "2.0"
        and "id" not in value
        and isinstance(value.get("method"), str)
    )


def socket_transport(payload, timeout, expected_id):
    deadline = time.monotonic() + float(timeout)
    connection = socket.create_connection(
        (RPC_HOST, RPC_PORT),
        timeout=float(timeout),
    )
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise JsonRpcError("JSON-RPC connection deadline expired")
        connection.settimeout(remaining)
        connection.sendall(payload)
        utf8 = codecs.getincrementaldecoder("utf-8")("strict")
        decoder = json.JSONDecoder()
        text = ""
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise JsonRpcError("JSON-RPC response deadline expired")
            connection.settimeout(remaining)
            chunk = connection.recv(4096)
            if not chunk:
                try:
                    text += utf8.decode(b"", final=True)
                except UnicodeDecodeError as error:
                    raise JsonRpcError(
                        "JSON-RPC response ended within UTF-8: %s" % error
                    )
                raise JsonRpcError(
                    "JSON-RPC stream closed before a complete response"
                )
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise JsonRpcError("JSON-RPC response exceeds size limit")
            try:
                text += utf8.decode(chunk, final=False)
            except UnicodeDecodeError as error:
                raise JsonRpcError(
                    "JSON-RPC response is not UTF-8: %s" % error
                )
            while True:
                stripped = text.lstrip()
                if not stripped:
                    text = ""
                    break
                try:
                    value, end = decoder.raw_decode(stripped)
                except ValueError:
                    text = stripped
                    break
                text = stripped[end:]
                if (
                    isinstance(value, dict)
                    and value.get("id") == expected_id
                ):
                    return json.dumps(value, separators=(",", ":"))
                if not _is_notification(value):
                    raise JsonRpcError(
                        "JSON-RPC stream returned an unexpected value"
                    )
    finally:
        connection.close()


class KodiJsonRpcClient(object):
    def __init__(self, transport=None, timeout=RPC_TIMEOUT_SECONDS):
        self.transport = socket_transport if transport is None else transport
        self.timeout = float(timeout)
        self.next_request_id = 1

    def call(self, method, params):
        if method not in ALLOWED_METHODS:
            raise JsonRpcError("forbidden JSON-RPC method: %s" % method)
        request_id = "htpc-settings-watchdog-%d" % self.next_request_id
        self.next_request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }
        payload = (
            json.dumps(request, separators=(",", ":"), sort_keys=True)
            + "\n"
        ).encode("utf-8")
        try:
            raw_response = self.transport(
                payload,
                self.timeout,
                request_id,
            )
            response = json.loads(raw_response)
        except JsonRpcError:
            raise
        except Exception as error:
            raise JsonRpcError("%s transport failed: %s" % (method, error))

        if not isinstance(response, dict):
            raise JsonRpcError("%s returned a non-object response" % method)
        if response.get("jsonrpc") != "2.0":
            raise JsonRpcError("%s returned the wrong JSON-RPC version" % method)
        if response.get("id") != request_id:
            raise JsonRpcError("%s returned the wrong response id" % method)
        if "error" in response:
            raise JsonRpcError("%s returned an error: %r" % (
                method,
                response["error"],
            ))
        if "result" not in response:
            raise JsonRpcError("%s returned no result" % method)
        return response["result"]

    def addon_enabled(self, addon_id, expected_version):
        result = self.call(
            "Addons.GetAddonDetails",
            {
                "addonid": addon_id,
                "properties": ["enabled", "version"],
            },
        )
        if not isinstance(result, dict):
            raise JsonRpcError("Addons.GetAddonDetails result is not an object")
        addon = result.get("addon")
        if not isinstance(addon, dict):
            raise JsonRpcError("Addons.GetAddonDetails returned no addon")
        if addon.get("addonid") != addon_id:
            raise JsonRpcError("Addons.GetAddonDetails returned another addon")
        if addon.get("version") != expected_version:
            raise JsonRpcError(
                "installed %s version %r does not match %r"
                % (addon_id, addon.get("version"), expected_version)
            )
        enabled = addon.get("enabled")
        if not isinstance(enabled, bool):
            raise JsonRpcError("addon enabled state is not boolean")
        return enabled

    def ready(self):
        result = self.call(
            "XBMC.GetInfoLabels",
            {"labels": [READY_LABEL]},
        )
        if not isinstance(result, dict):
            raise JsonRpcError("XBMC.GetInfoLabels result is not an object")
        if READY_LABEL not in result or not isinstance(
            result[READY_LABEL],
            str,
        ):
            raise JsonRpcError("XBMC.GetInfoLabels returned no readiness label")
        return result[READY_LABEL] == "true"

    def set_enabled(self, addon_id, enabled):
        result = self.call(
            "Addons.SetAddonEnabled",
            {
                "addonid": addon_id,
                "enabled": bool(enabled),
            },
        )
        if result != "OK":
            raise JsonRpcError(
                "Addons.SetAddonEnabled returned no exact acknowledgement"
            )


class Watchdog(object):
    def __init__(
        self,
        client,
        expected_version=EXPECTED_ADDON_VERSION,
        clock=None,
        sleeper=None,
        startup_grace=STARTUP_GRACE_SECONDS,
        logger=None,
    ):
        self.client = client
        self.expected_version = expected_version
        self.clock = time.monotonic if clock is None else clock
        self.sleeper = time.sleep if sleeper is None else sleeper
        self.startup_grace = float(startup_grace)
        self.logger = logger or (lambda message: None)
        self.started_at = self.clock()
        self.grace_deadline = self.started_at + self.startup_grace
        self.lease_observed = False
        self.failure_index = 0

    def _backoff(self, error):
        delay = BACKOFF_SECONDS[
            min(self.failure_index, len(BACKOFF_SECONDS) - 1)
        ]
        self.failure_index += 1
        self.logger("probe failed; retrying in %.0fs: %s" % (delay, error))
        return delay, "error"

    def _success(self):
        self.failure_index = 0

    def _begin_grace(self, now):
        self.lease_observed = False
        self.grace_deadline = float(now) + self.startup_grace

    def step(self, now=None):
        now_override = None if now is None else float(now)
        try:
            enabled = self.client.addon_enabled(
                ADDON_ID,
                self.expected_version,
            )
            if not enabled:
                self.client.set_enabled(ADDON_ID, True)
                self._success()
                self._begin_grace(self.clock())
                self.logger("enabled %s" % ADDON_ID)
                return HEALTHY_POLL_SECONDS, "enabled"

            if self.client.ready():
                self._success()
                self.lease_observed = True
                self.grace_deadline = None
                return HEALTHY_POLL_SECONDS, "healthy"

            now = self.clock() if now_override is None else now_override
            if (
                not self.lease_observed
                and self.grace_deadline is not None
                and now < self.grace_deadline
            ):
                self._success()
                return HEALTHY_POLL_SECONDS, "grace"

            self.client.set_enabled(ADDON_ID, False)
            self.sleeper(SETTLE_SECONDS)
            self.client.set_enabled(ADDON_ID, True)
            self._success()
            self._begin_grace(self.clock())
            self.logger("restarted %s after readiness lease expired" % ADDON_ID)
            return HEALTHY_POLL_SECONDS, "restarted"
        except Exception as error:
            return self._backoff(error)

    def run(self):
        while True:
            delay, _action = self.step()
            self.sleeper(delay)


def log(message):
    print("kodi-settings-watchdog: %s" % message, file=sys.stderr, flush=True)


def main():
    Watchdog(KodiJsonRpcClient(), logger=log).run()


if __name__ == "__main__":
    main()
