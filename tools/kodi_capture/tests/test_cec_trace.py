from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from tools.kodi_capture.cec_trace import (
    MAX_CEC_MESSAGE_BYTES,
    MAX_FRAMES,
    MAX_LINE_BYTES,
    MAX_TRACE_BYTES,
    RECEIVED,
    TRANSMITTED,
    CecMonitorContent,
    CecTracePolicyError,
    CecTraceProtocolError,
    parse_cec_monitor_content,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "cec_trace"
    / "normal-recording-device-1.txt"
).read_bytes()
INITIAL = (
    b"\n\nInitial Event: State Change: PA: 1.0.0.0, "
    b"LA mask: 0x0002\n"
)
POLL = (
    b"Transmitted by Recording Device 1 to TV (1 to 0): "
    b"GIVE_DEVICE_POWER_STATUS (0x8f)\n"
    b"\tRaw: 0x10 0x8f (  )\n"
)
RESPONSE = (
    b"Received from TV to Recording Device 1 (0 to 1): "
    b"REPORT_POWER_STATUS (0x90):\n"
    b"\tpwr-state: on (0x00)\n"
    b"\tRaw: 0x01 0x90 0x00 (   )\n"
)
INBOUND_POLL = (
    b"Received from TV to Recording Device 1 (0 to 1): POLL\n"
    b"\tRaw: 0x01 ( )\n"
)
OUTBOUND_POLL = (
    b"Transmitted by Recording Device 1 to TV (1 to 0): POLL\n"
    b"\tRaw: 0x10 ( )\n"
)


class CecMonitorContentTest(unittest.TestCase):
    def test_real_normal_fixture_is_preserved_and_normalized(self):
        trace = parse_cec_monitor_content(FIXTURE)
        self.assertEqual(trace.raw, FIXTURE)
        self.assertEqual(trace.initial_event.physical_address, (1, 0, 0, 0))
        self.assertEqual(trace.initial_event.logical_address_mask, 0x0002)
        self.assertEqual(len(trace.frames), 2)

        poll = trace.frames[0]
        self.assertEqual(poll.direction, TRANSMITTED)
        self.assertEqual(poll.source, "Recording Device 1")
        self.assertEqual(poll.destination, "TV")
        self.assertEqual((poll.initiator, poll.destination_address), (1, 0))
        self.assertEqual(poll.opcode_name, "GIVE_DEVICE_POWER_STATUS")
        self.assertEqual(poll.opcode, 0x8F)
        self.assertEqual(poll.operands, ())
        self.assertEqual(poll.details, ())
        self.assertEqual(poll.raw_message, (0x10, 0x8F))

        response = trace.frames[1]
        self.assertEqual(response.direction, RECEIVED)
        self.assertEqual(
            (response.initiator, response.destination_address),
            (0, 1),
        )
        self.assertEqual(response.opcode, 0x90)
        self.assertEqual(response.operands, (0x00,))
        self.assertEqual(response.details, ("pwr-state: on (0x00)",))
        self.assertEqual(response.raw_message, (0x01, 0x90, 0x00))

        with self.assertRaises(FrozenInstanceError):
            poll.opcode = 0x82

    def test_well_formed_inbound_frames_are_preserved(self):
        inbound = (
            b"Received from Playback Device 1 to all (4 to 15): "
            b"ACTIVE_SOURCE (0x82):\n"
            b"\tphys-addr: 1.0.0.0\n"
            b"\tRaw: 0x4f 0x82 0x10 0x00 (O   )\n"
        )
        trace = parse_cec_monitor_content(INITIAL + POLL + inbound)
        frame = trace.frames[1]
        self.assertEqual(frame.direction, RECEIVED)
        self.assertEqual((frame.initiator, frame.destination_address), (4, 15))
        self.assertEqual(frame.opcode, 0x82)
        self.assertEqual(frame.operands, (0x10, 0x00))
        self.assertEqual(frame.details, ("phys-addr: 1.0.0.0",))

    def test_inbound_poll_and_unregistered_source_are_preserved(self):
        unregistered_poll = (
            b"Received from Unregistered to Recording Device 1 "
            b"(15 to 1): POLL\n"
            b"\tRaw: 0xf1 ( )\n"
        )
        trace = parse_cec_monitor_content(
            INITIAL + POLL + INBOUND_POLL + unregistered_poll
        )
        for frame in trace.frames[-2:]:
            self.assertIsNone(frame.opcode_name)
            self.assertIsNone(frame.opcode)
            self.assertEqual(frame.operands, ())
            self.assertEqual(len(frame.raw_message), 1)
        self.assertEqual(trace.frames[-1].source, "Unregistered")

    def test_raw_rendering_preserves_upstream_del_byte(self):
        inbound = (
            b"Received from Playback Device 1 to all (4 to 15): "
            b"VENDOR_COMMAND (0x89)\n"
            b"\tRaw: 0x4f 0x89 0x7f (O \x7f)\n"
        )
        trace = parse_cec_monitor_content(INITIAL + POLL + inbound)
        self.assertEqual(trace.frames[-1].operands, (0x7F,))

    def test_whole_input_framing_ascii_and_bounds_fail_closed(self):
        malformed = (
            b"",
            FIXTURE[:-1],
            FIXTURE[:1] + FIXTURE[2:],
            b"\n" + FIXTURE,
            FIXTURE + b"\n",
            FIXTURE.replace(b"Recording", b"Record\xffing", 1),
            FIXTURE.replace(b"\nTransmitted", b"\r\nTransmitted", 1),
            INITIAL + (b"x" * (MAX_LINE_BYTES + 1)) + b"\n",
            b"x" * (MAX_TRACE_BYTES + 1),
        )
        for raw in malformed:
            with self.subTest(length=len(raw)):
                with self.assertRaises(CecTraceProtocolError):
                    parse_cec_monitor_content(raw)
        with self.assertRaises(TypeError):
            parse_cec_monitor_content("not bytes")

    def test_readiness_is_exact_unique_and_stable(self):
        malformed = (
            POLL,
            INITIAL.replace(b"0x0002", b"0x0000") + POLL,
            INITIAL + INITIAL + POLL,
            INITIAL
            + POLL
            + (
                b"Event: State Change: PA: 1.0.0.0, "
                b"LA mask: 0x0002\n"
            ),
            INITIAL.replace(b"Initial Event", b"Initial event") + POLL,
        )
        for raw in malformed:
            with self.subTest(raw=raw[:70]):
                with self.assertRaises(CecTraceProtocolError):
                    parse_cec_monitor_content(raw)

    def test_description_raw_state_machine_rejects_unknown_and_truncated_input(self):
        description = POLL.splitlines(keepends=True)[0]
        raw_line = POLL.splitlines(keepends=True)[1]
        malformed = (
            INITIAL + b"unknown monitor output\n" + POLL,
            INITIAL + raw_line + description,
            INITIAL + description,
            INITIAL + description + b"\tunexpected detail\n" + raw_line,
            INITIAL + RESPONSE.replace(b"(0x90):\n", b"(0x90):\n\n"),
            INITIAL
            + RESPONSE.replace(
                b"REPORT_POWER_STATUS (0x90):",
                b"REPORT_POWER_STATUS (0x90)",
            )
            + POLL,
            INITIAL
            + POLL.replace(
                b"GIVE_DEVICE_POWER_STATUS (0x8f)",
                b"GIVE_DEVICE_POWER_STATUS (0x8f):",
            ),
        )
        for raw in malformed:
            with self.subTest(raw=raw[:100]):
                with self.assertRaises(CecTraceProtocolError):
                    parse_cec_monitor_content(raw)

    def test_header_opcode_and_direction_must_match_the_description(self):
        malformed = (
            INITIAL + POLL.replace(b"0x10 0x8f", b"0x20 0x8f"),
            INITIAL + POLL.replace(b"0x10 0x8f", b"0x10 0x82"),
            INITIAL
            + POLL.replace(
                b"Transmitted by Recording Device 1 to TV",
                b"Received from Recording Device 1 to TV",
            ),
            INITIAL.replace(b"LA mask: 0x0002", b"LA mask: 0x0004") + POLL,
            INITIAL
            + RESPONSE.replace(
                b"Received from TV to Recording Device 1",
                b"Received from TV to Recording Device 2",
            ).replace(b"(0 to 1)", b"(0 to 2)"),
            INITIAL
            + POLL
            + (
                b"Received from Recording Device 1 to all (1 to 15): "
                b"ACTIVE_SOURCE (0x82):\n"
                b"\tphys-addr: 1.0.0.0\n"
                b"\tRaw: 0x1f 0x82 0x10 0x00 (    )\n"
            ),
        )
        for raw in malformed:
            with self.subTest(raw=raw[:110]):
                with self.assertRaises(CecTraceProtocolError):
                    parse_cec_monitor_content(raw)

    def test_drop_and_loss_diagnostics_fail_closed_in_every_state(self):
        diagnostics = (
            b"Dropped 1 CEC event\n",
            b"monitor overflow detected\n",
            b"\tlost messages: 1\n",
        )
        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaises(CecTraceProtocolError):
                    parse_cec_monitor_content(INITIAL + diagnostic + POLL)

    def test_only_exact_pi_power_status_poll_is_allowed(self):
        forbidden = (
            POLL.replace(
                b"GIVE_DEVICE_POWER_STATUS (0x8f)",
                b"ACTIVE_SOURCE (0x82)",
            ).replace(b"0x10 0x8f", b"0x10 0x82"),
            POLL.replace(b"to TV (1 to 0)", b"to all (1 to 15)")
            .replace(b"0x10 0x8f", b"0x1f 0x8f"),
            (
                b"Transmitted by Recording Device 1 to TV (1 to 0): "
                b"GIVE_DEVICE_POWER_STATUS (0x8f):\n"
                b"\toperand: 0x00\n"
                b"\tRaw: 0x10 0x8f 0x00 (   )\n"
            ),
        )
        for frame in forbidden:
            with self.subTest(frame=frame[:90]):
                with self.assertRaises(CecTracePolicyError):
                    parse_cec_monitor_content(INITIAL + frame)

    def test_allowed_poll_is_bound_to_the_exact_appliance_identity(self):
        cases = (
            (
                INITIAL.replace(b"0x0002", b"0x0006") + POLL,
                CecTracePolicyError,
            ),
            (
                INITIAL
                + POLL.replace(
                    b"Recording Device 1",
                    b"Playback Device 1",
                ),
                CecTraceProtocolError,
            ),
            (
                INITIAL + POLL.replace(b"to TV", b"to Audio System"),
                CecTraceProtocolError,
            ),
            (
                INITIAL
                + POLL.replace(
                    b"GIVE_DEVICE_POWER_STATUS",
                    b"ACTIVE_SOURCE",
                ),
                CecTraceProtocolError,
            ),
            (
                INITIAL + POLL.replace(b"(  )", b"(bogus)"),
                CecTraceProtocolError,
            ),
            (
                INITIAL + POLL.replace(b"(  )", b"(xx)"),
                CecTraceProtocolError,
            ),
            (
                INITIAL + POLL.replace(b"(1 to 0)", b"(01 to 0)"),
                CecTraceProtocolError,
            ),
            (
                INITIAL
                + POLL
                + RESPONSE.replace(
                    b"REPORT_POWER_STATUS",
                    b"ACTIVE_SOURCE",
                ),
                CecTraceProtocolError,
            ),
            (
                INITIAL
                + POLL
                + RESPONSE.replace(b"from TV", b"from Playback Device 1"),
                CecTraceProtocolError,
            ),
            (
                INITIAL
                + POLL
                + (
                    b"Received from Recording Device 1 "
                    b"to Recording Device 1 (2 to 1): POLL\n"
                    b"\tRaw: 0x21 (!)\n"
                ),
                CecTraceProtocolError,
            ),
        )
        for raw, error in cases:
            with self.subTest(error=error):
                with self.assertRaises(error):
                    parse_cec_monitor_content(raw)

    def test_allowed_poll_is_required_even_with_valid_inbound_traffic(self):
        with self.assertRaises(CecTracePolicyError):
            parse_cec_monitor_content(INITIAL + RESPONSE)

    def test_header_only_pi_poll_is_forbidden_by_policy(self):
        with self.assertRaises(CecTracePolicyError):
            parse_cec_monitor_content(INITIAL + POLL + OUTBOUND_POLL)

    def test_frame_count_and_message_length_bounds_are_exact(self):
        trace = parse_cec_monitor_content(INITIAL + (POLL * MAX_FRAMES))
        self.assertEqual(len(trace.frames), MAX_FRAMES)
        with self.assertRaises(CecTraceProtocolError):
            parse_cec_monitor_content(INITIAL + (POLL * (MAX_FRAMES + 1)))

        maximum = inbound_frame(MAX_CEC_MESSAGE_BYTES - 2)
        trace = parse_cec_monitor_content(INITIAL + POLL + maximum)
        self.assertEqual(
            len(trace.frames[-1].raw_message),
            MAX_CEC_MESSAGE_BYTES,
        )
        with self.assertRaises(CecTraceProtocolError):
            parse_cec_monitor_content(
                INITIAL
                + POLL
                + inbound_frame(MAX_CEC_MESSAGE_BYTES - 1)
            )

    def test_content_construction_always_reparses_raw_evidence(self):
        self.assertEqual(
            CecMonitorContent(FIXTURE),
            parse_cec_monitor_content(FIXTURE),
        )
        with self.assertRaises(CecTraceProtocolError):
            CecMonitorContent(FIXTURE.replace(b"0x10 0x8f", b"0x10 0x82"))

    def test_frame_model_rejects_raw_message_drift_and_excess_length(self):
        trace = parse_cec_monitor_content(INITIAL + POLL)
        with self.assertRaises(ValueError):
            replace(trace.frames[0], raw_message=(0x10, 0x82))
        with self.assertRaises(ValueError):
            replace(
                trace.frames[0],
                operands=(0,) * 15,
                raw_message=(0x10, 0x8F) + ((0,) * 15),
            )


def inbound_frame(operand_count):
    raw_message = (0x4F, 0x89) + ((0,) * operand_count)
    raw_fields = " ".join(
        "0x%02x" % value for value in raw_message
    ).encode("ascii")
    rendering = "".join(
        chr(value) if 0x20 <= value <= 0x7F else " "
        for value in raw_message
    ).encode("ascii")
    return (
        b"Received from Playback Device 1 to all (4 to 15): "
        b"VENDOR_COMMAND (0x89)\n"
        b"\tRaw: "
        + raw_fields
        + b" ("
        + rendering
        + b")\n"
    )


if __name__ == "__main__":
    unittest.main()
