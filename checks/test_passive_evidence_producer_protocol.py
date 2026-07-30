from __future__ import annotations

import base64
import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.kodi_capture import passive_evidence as consumer


PRODUCER_PATH = (
    REPOSITORY_ROOT
    / "modules"
    / "kodi-passive-evidence"
    / "kodi_passive_evidence.py"
)
PRODUCER_MODULE_NAME = "_kodi_passive_evidence_protocol_producer"
FIXTURE_DIRECTORY = (
    REPOSITORY_ROOT / "tools" / "kodi_capture" / "tests" / "fixtures"
)

NONCE = "a" * consumer.NONCE_HEX_LENGTH
BOOT_ID = "11111111111111111111111111111111"
INVOCATION_ID = "22222222222222222222222222222222"
START_CURSOR = "fixture-start-cursor"


def load_producer():
    specification = importlib.util.spec_from_file_location(
        PRODUCER_MODULE_NAME,
        PRODUCER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load passive-evidence producer")
    module = importlib.util.module_from_spec(specification)
    sys.modules[PRODUCER_MODULE_NAME] = module
    specification.loader.exec_module(module)
    return module


producer = load_producer()


def journal_prefix(raw: bytes, record_count: int) -> bytes:
    lines = raw.splitlines(keepends=True)
    records = lines[:-1]
    if not 1 <= record_count <= len(records):
        raise AssertionError("record_count is outside fixture")
    terminal_record = json.loads(records[record_count - 1][1:])
    return (
        b"".join(records[:record_count])
        + b"-- cursor: "
        + terminal_record["__CURSOR"].encode("ascii")
        + b"\n"
    )


CEC_TRACE = (
    FIXTURE_DIRECTORY
    / "cec_trace"
    / "normal-recording-device-1.txt"
).read_bytes()
FINAL_JOURNAL = base64.b64decode(
    (
        FIXTURE_DIRECTORY
        / "wake_journal"
        / "benign-global.json-seq.b64"
    ).read_bytes().strip(),
    validate=True,
)
LIVE_JOURNAL = journal_prefix(FINAL_JOURNAL, 2)


class FixtureMonitor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.closed = False

    def wait_ready(self, deadline):
        self.runtime.events.append(("monitor.wait_ready", deadline))

    def wait_until(self, deadline):
        self.runtime.events.append(("monitor.wait_until", deadline))
        self.runtime.now_usec = max(self.runtime.now_usec, deadline)

    def require_alive(self):
        self.runtime.events.append(("monitor.require_alive",))

    def wait_natural(self, deadline):
        self.runtime.events.append(("monitor.wait_natural", deadline))
        self.runtime.now_usec += 1
        return CEC_TRACE

    def close(self):
        self.runtime.events.append(("monitor.close",))
        self.closed = True


class FixtureRuntime:
    def __init__(self):
        self.now_usec = 1_000_000
        self.events = []
        self.monitor = FixtureMonitor(self)
        self.journals = [LIVE_JOURNAL, FINAL_JOURNAL]
        self.service = producer.ServiceIdentity(
            unit_id="cec-tv-wake.service",
            load_state="loaded",
            active_state="active",
            sub_state="running",
            invocation_id=INVOCATION_ID,
            main_pid=4242,
            n_restarts=0,
            exec_start_usec=900_000,
            active_enter_usec=900_100,
        )

    def monotonic_usec(self):
        return self.now_usec

    def read_boot_id(self, deadline, monitor=None):
        self.events.append(("runtime.read_boot_id", monitor is self.monitor))
        return BOOT_ID

    def read_service(self, deadline, monitor=None):
        self.events.append(("runtime.read_service", monitor is self.monitor))
        return self.service

    def read_global_cursor(self, deadline):
        self.events.append(("runtime.read_global_cursor",))
        return START_CURSOR

    def start_monitor(self):
        self.events.append(("runtime.start_monitor",))
        return self.monitor

    def sync_journal(self, deadline, monitor):
        self.events.append(("runtime.sync_journal", monitor is self.monitor))

    def read_journal(self, cursor, deadline, monitor):
        self.events.append(
            ("runtime.read_journal", cursor, monitor is self.monitor)
        )
        return self.journals.pop(0)


class FixtureTransport:
    def __init__(self, runtime):
        self.runtime = runtime
        self.ready = None
        self.header = None
        self.body = None

    def read_start(self, deadline):
        self.runtime.events.append(("transport.read_start",))
        return NONCE

    def write_ready(self, line, deadline, monitor):
        self.runtime.events.append(
            ("transport.write_ready", monitor is self.runtime.monitor)
        )
        self.ready = line

    def read_finish_and_eof(self, nonce, deadline, monitor):
        self.runtime.events.append(
            ("transport.read_finish_and_eof", nonce, monitor is self.runtime.monitor)
        )
        self.runtime.now_usec += 1

    def write_result(self, header, body, deadline):
        self.runtime.events.append(("transport.write_result",))
        self.header = header
        self.body = body


class PassiveEvidenceProducerProtocolTest(unittest.TestCase):
    def test_real_producer_round_trips_real_capture_fixtures(self):
        runtime = FixtureRuntime()
        transport = FixtureTransport(runtime)

        returned = producer.collect_evidence(runtime, transport)

        self.assertTrue(runtime.monitor.closed)
        self.assertEqual(runtime.journals, [])
        self.assertEqual(returned, transport.body)
        ready = consumer.decode_ready_line(transport.ready)
        length = consumer.decode_result_header(transport.header, ready.nonce)
        self.assertEqual(length, len(transport.body))
        evidence = consumer.decode_passive_evidence(transport.body, ready)

        self.assertEqual(evidence.raw, transport.body)
        self.assertEqual(evidence.ready.raw, transport.ready)
        self.assertEqual(evidence.ready.nonce, NONCE)
        self.assertEqual(evidence.ready.boot_id, BOOT_ID)
        self.assertEqual(evidence.ready.start_cursor, START_CURSOR)
        self.assertEqual(
            evidence.ready.service.invocation_id,
            INVOCATION_ID,
        )
        self.assertEqual(evidence.cec_trace.raw, CEC_TRACE)
        self.assertEqual(evidence.live_journal.raw, LIVE_JOURNAL)
        self.assertEqual(evidence.final_journal.raw, FINAL_JOURNAL)
        self.assertGreaterEqual(
            evidence.timing.live_journal_usec
            - evidence.timing.ready_usec,
            consumer.MIN_OBSERVATION_USEC,
        )
        self.assertLessEqual(
            evidence.timing.finish_usec - evidence.timing.ready_usec,
            consumer.MAX_ACTION_WINDOW_USEC,
        )

    def test_real_producer_wire_output_is_canonical_and_exactly_framed(self):
        runtime = FixtureRuntime()
        transport = FixtureTransport(runtime)
        producer.collect_evidence(runtime, transport)

        document = json.loads(transport.body.decode("ascii"))
        canonical = json.dumps(
            document,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self.assertEqual(transport.body, canonical)
        self.assertEqual(
            transport.header,
            (
                "%s RESULT %s %d\n"
                % (
                    consumer.PROTOCOL_VERSION,
                    NONCE,
                    len(transport.body),
                )
            ).encode("ascii"),
        )

    def test_producer_and_consumer_protocol_constants_match(self):
        names = (
            "PROTOCOL_VERSION",
            "MAX_READY_BYTES",
            "MAX_ENVELOPE_BYTES",
            "NONCE_HEX_LENGTH",
            "MIN_OBSERVATION_USEC",
            "MAX_ACTION_WINDOW_USEC",
            "MAX_SESSION_USEC",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(
                    getattr(producer, name),
                    getattr(consumer, name),
                )


if __name__ == "__main__":
    unittest.main()
