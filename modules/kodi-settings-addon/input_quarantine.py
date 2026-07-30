from __future__ import absolute_import, division, print_function

from seek_controller import HOLD_ONSET_MAX, HOLD_RELEASE_IDLE


INPUT_WATERMARK_PAYLOAD_KEY = "_htpc_physical_input_watermark"
DIRECTION_KEYS = frozenset(("left", "right"))
CANONICAL_KEYS = frozenset(("left", "right", "select", "back"))
PHYSICAL_KEYS = {
    "left": "left",
    "timeline-left": "left",
    "right": "right",
    "timeline-right": "right",
    "primary": "select",
    "osd-primary": "select",
    "chapter-select": "select",
    "fullscreen-back": "back",
    "osd-back": "back",
}


def canonical_physical_key(action, payload=None):
    if action == "chapter-focus":
        direction = (payload or {}).get("physical_direction")
        return direction if direction in DIRECTION_KEYS else None
    if action == "chapter-exit":
        if (payload or {}).get("arm_back"):
            return "back"
        return None
    return PHYSICAL_KEYS.get(action)


class InputQuarantine(object):
    """Suppress only continuations of physical trains crossing media."""

    def __init__(self):
        self.last_seen = {}
        self.deadlines = {}
        self.latest_direction = None
        self.latest_direction_seen = None

    def should_suppress(self, key, timestamp):
        if key is None:
            return False
        timestamp = float(timestamp)
        self._record(key, timestamp)
        deadline = self.deadlines.get(key)
        if deadline is None:
            return False
        if timestamp > deadline:
            self.deadlines.pop(key, None)
            return False
        self.deadlines[key] = max(
            deadline,
            timestamp + HOLD_RELEASE_IDLE,
        )
        return True

    def _record(self, key, timestamp):
        self.last_seen[key] = max(
            timestamp,
            self.last_seen.get(key, timestamp),
        )
        if (
            key in DIRECTION_KEYS
            and (
                self.latest_direction_seen is None
                or timestamp >= self.latest_direction_seen
            )
        ):
            self.latest_direction = key
            self.latest_direction_seen = timestamp

    def merge_watermark(self, watermark):
        if not isinstance(watermark, dict):
            return
        last_seen = watermark.get("last_seen")
        if isinstance(last_seen, dict):
            for key, timestamp in last_seen.items():
                if key not in CANONICAL_KEYS:
                    continue
                try:
                    timestamp = float(timestamp)
                except (TypeError, ValueError):
                    continue
                self.last_seen[key] = max(
                    timestamp,
                    self.last_seen.get(key, timestamp),
                )
        latest = watermark.get("latest_direction")
        if not isinstance(latest, dict):
            return
        key = latest.get("key")
        if key not in DIRECTION_KEYS:
            return
        try:
            timestamp = float(latest.get("timestamp"))
        except (TypeError, ValueError):
            return
        if (
            self.latest_direction_seen is None
            or timestamp >= self.latest_direction_seen
        ):
            self.latest_direction = key
            self.latest_direction_seen = timestamp

    def on_playback_boundary(self, timestamp, watermark=None):
        timestamp = float(timestamp)
        self.merge_watermark(watermark)
        for key, last_seen in self.last_seen.items():
            if (
                key in DIRECTION_KEYS
                and key != self.latest_direction
            ):
                self.deadlines.pop(key, None)
                continue
            if last_seen < timestamp - HOLD_ONSET_MAX:
                continue
            self.deadlines[key] = max(
                self.deadlines.get(key, float("-inf")),
                last_seen + HOLD_ONSET_MAX,
                timestamp + HOLD_RELEASE_IDLE,
            )

    def clear(self):
        self.last_seen = {}
        self.deadlines = {}
        self.latest_direction = None
        self.latest_direction_seen = None
