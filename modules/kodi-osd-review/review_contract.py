"""Finite, side-effect-free contract for the headless OSD review driver."""

from __future__ import annotations


HOME_WINDOW_ID = 10000
REVIEW_WINDOW_ID = 1192
PROPERTY_PREFIX = "htpc.review."

PROPERTY_NAMES = (
    "ready",
    "scenario",
    "revision",
    "paused",
    "title",
    "subtitle",
    "elapsed",
    "remaining",
    "focuscue",
    "seek.viewactive",
    "seek.viewslot",
    "seek.a.revision",
    "seek.a.phase",
    "seek.a.targetvalid",
    "seek.a.targetfill",
    "seek.a.targetmarker",
    "seek.a.time",
    "seek.a.delta",
    "seek.a.prompt",
    "seek.a.previewstatus",
    "seek.a.previewpath",
    "seek.a.previewanchor",
    "seek.b.revision",
    "seek.b.phase",
    "seek.b.targetvalid",
    "seek.b.targetfill",
    "seek.b.targetmarker",
    "seek.b.time",
    "seek.b.delta",
    "seek.b.prompt",
    "seek.b.previewstatus",
    "seek.b.previewpath",
    "seek.b.previewanchor",
)
PROPERTY_KEYS = tuple(PROPERTY_PREFIX + name for name in PROPERTY_NAMES)

SCENARIOS = (
    "transport-playing",
    "transport-paused",
    "timeline-playing",
    "timeline-idle",
    "timeline-chapters",
    "seek-backward",
    "seek-forward",
    "seek-forward-slot-b",
    "top-stop",
)
EXPECTED_FOCUS = {
    "transport-playing": "9201",
    "transport-paused": "9201",
    "timeline-playing": "9300",
    "timeline-idle": "9300",
    "timeline-chapters": "9300",
    "seek-backward": "9300",
    "seek-forward": "9300",
    "seek-forward-slot-b": "9300",
    "top-stop": "9101",
}

_COMMON = {
    "revision": "1",
    "paused": "true",
    "title": "Headless OSD Review",
    "subtitle": "Deterministic fixture",
    "elapsed": "40:00",
    "remaining": "\N{MINUS SIGN}1:00:00",
    "focuscue": (
        "\N{LEFTWARDS ARROW}  10s   \N{BULLET}   Hold to scrub"
        "   \N{BULLET}   10s  \N{RIGHTWARDS ARROW}"
    ),
}

_FOCUS_CUE = {
    "timeline-chapters": {
        "focuscue": (
            "\N{LEFTWARDS ARROW}  10s   \N{BULLET}   Hold to scrub"
            "   \N{BULLET}   10s  \N{RIGHTWARDS ARROW}"
            "    \N{UPWARDS ARROW}  Chapters"
        ),
    },
}

_PLAYBACK_STATE = {
    "transport-playing": {
        "paused": "",
    },
    "timeline-playing": {
        "paused": "",
    },
}

_SEEK = {
    "seek-backward": {
        "seek.viewactive": "true",
        "seek.viewslot": "a",
        "seek.a.revision": "1",
        "seek.a.phase": "ready",
        "seek.a.targetvalid": "true",
        "seek.a.targetfill": "0.0000,25.0000",
        "seek.a.targetmarker": "25.0000,25.0000",
        "seek.a.time": "25:00",
        "seek.a.delta": "\N{MINUS SIGN}15:00",
        "seek.a.prompt": "OK  Seek   \N{BULLET}   Back  Cancel",
        "seek.a.previewstatus": "ready",
        "seek.a.previewpath": (
            "special://skin/resources/review/seek-25.png"
        ),
        "seek.a.previewanchor": "25",
    },
    "seek-forward": {
        "seek.viewactive": "true",
        "seek.viewslot": "a",
        "seek.a.revision": "1",
        "seek.a.phase": "ready",
        "seek.a.targetvalid": "true",
        "seek.a.targetfill": "0.0000,75.0000",
        "seek.a.targetmarker": "75.0000,75.0000",
        "seek.a.time": "1:15:00",
        "seek.a.delta": "+35:00",
        "seek.a.prompt": "OK  Seek   \N{BULLET}   Back  Cancel",
        "seek.a.previewstatus": "ready",
        "seek.a.previewpath": (
            "special://skin/resources/review/seek-75.png"
        ),
        "seek.a.previewanchor": "75",
    },
    "seek-forward-slot-b": {
        "seek.viewactive": "true",
        "seek.viewslot": "b",
        "seek.b.revision": "1",
        "seek.b.phase": "ready",
        "seek.b.targetvalid": "true",
        "seek.b.targetfill": "0.0000,75.0000",
        "seek.b.targetmarker": "75.0000,75.0000",
        "seek.b.time": "1:15:00",
        "seek.b.delta": "+35:00",
        "seek.b.prompt": "OK  Seek   \N{BULLET}   Back  Cancel",
        "seek.b.previewstatus": "ready",
        "seek.b.previewpath": (
            "special://skin/resources/review/seek-75.png"
        ),
        "seek.b.previewanchor": "75",
    },
}


class RequestError(ValueError):
    """The invocation is outside the finite review contract."""


def parse_request(arguments):
    """Return ``("state", name)`` or ``("command", "close")``."""
    if len(arguments) != 1:
        raise RequestError("expected exactly one argument")

    argument = str(arguments[0])
    if argument == "command=close":
        return ("command", "close")
    if argument.startswith("state="):
        scenario = argument[len("state=") :]
        if scenario in SCENARIOS:
            return ("state", scenario)
    raise RequestError("unknown review request")


def scenario_properties(scenario):
    """Return one complete immutable-style fixture frame, excluding ready."""
    if scenario not in SCENARIOS:
        raise RequestError("unknown review scenario")

    values = dict(_COMMON)
    values["scenario"] = scenario
    values.update(_PLAYBACK_STATE.get(scenario, {}))
    values.update(_FOCUS_CUE.get(scenario, {}))
    values.update(_SEEK.get(scenario, {}))
    return {
        PROPERTY_PREFIX + name: value
        for name, value in values.items()
    }
