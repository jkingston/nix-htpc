from __future__ import absolute_import, division, print_function

import json
import math
import time
import uuid

import xbmc
import xbmcgui

from media_contract import (
    CURRENT_SEEK_CONTROLLER_PROPERTY_KEYS,
    CURRENT_SEEK_PROPERTY_KEYS,
    CURRENT_VIEW_SLOT_FIELDS,
    HOME_WINDOW_ID,
    SEEK_PREFIX,
    SERVICE_PROTOCOL,
    SERVICE_PROTOCOL_VERSION,
    SERVICE_READY,
    PREVIEW_CONTRACT,
    SEEK_REQUEST,
    preview_validation,
    preview_status,
)


LEASE_REFRESH_SECONDS = 0.75
TIMELINE_RAIL_WIDTH = 1152.0
TIMELINE_MARKER_HALF_WIDTH = 10.0
TIMELINE_MARKER_CONTROL_WIDTH = (
    TIMELINE_RAIL_WIDTH + (2.0 * TIMELINE_MARKER_HALF_WIDTH)
)


class KodiPropertyPublisher(object):
    def __init__(self, window=None, logger=None, consumer_nonce=None):
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)
        self.logger = logger
        self.last = {}
        self.view_slot = None
        self.view_signature = None
        self.preview_diagnostic_generation = None
        self.preview_diagnostics_seen = set()
        self.texture_handoff_generation = None
        self.last_preview_status = "none"
        self.consumer_nonce = consumer_nonce or uuid.uuid4().hex

    def _diagnose(self, message):
        """Emit best-effort diagnostics without changing playback behavior."""
        if self.logger is None:
            return
        try:
            self.logger(message)
        except Exception:
            pass

    def publish(self, snapshot):
        values = {
            "active": "true" if snapshot["active"] else "",
            "generation": str(snapshot["generation"]),
            "targetseconds": str(snapshot["target_seconds"]),
            "modal": "true" if snapshot.get("modal") else "",
        }
        for key, value in values.items():
            if self.last.get(key) == value:
                continue
            if value:
                self.window.setProperty(SEEK_PREFIX + key, value)
            else:
                self.window.clearProperty(SEEK_PREFIX + key)
        self.last.update(values)
        request = ""
        if snapshot.get("active"):
            request = json.dumps(
                {
                    "schema": 1,
                    "active": True,
                    "generation": int(snapshot["generation"]),
                    "target_seconds": int(snapshot["target_seconds"]),
                    "playback_epoch": snapshot.get("playback_epoch"),
                    "consumer_nonce": self.consumer_nonce,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        if self.last.get("seekrequest") != request:
            if request:
                self.window.setProperty(SEEK_REQUEST, request)
            else:
                self.window.clearProperty(SEEK_REQUEST)
            self.last["seekrequest"] = request

    def refresh_preview(self, snapshot):
        keys = (PREVIEW_CONTRACT,)
        properties = dict(
            (key, self.window.getProperty(key))
            for key in keys
        )
        validation_snapshot = dict(snapshot)
        validation_snapshot["consumer_nonce"] = self.consumer_nonce
        path, reason = preview_validation(properties, validation_snapshot)
        self.last_preview_status = preview_status(
            properties,
            validation_snapshot,
        )
        generation = snapshot.get("generation") if snapshot.get("active") else None
        if generation != self.preview_diagnostic_generation:
            self.preview_diagnostic_generation = generation
            self.preview_diagnostics_seen = set()
            self.texture_handoff_generation = None
        outcome = "ready" if path else "rejected"
        diagnostic = (outcome, reason)
        if (
            generation is not None
            and diagnostic not in self.preview_diagnostics_seen
        ):
            self.preview_diagnostics_seen.add(diagnostic)
            self._diagnose(
                "trickplay stage=validator outcome=%s reason=%s"
                % diagnostic
            )
        return path

    @staticmethod
    def _safe_percent(value):
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            numeric = 0.0
        if not math.isfinite(numeric):
            numeric = 0.0
        return max(0.0, min(100.0, numeric))

    @staticmethod
    def _marker_percent(percent):
        """Map rail progress into a radius-padded ranges control.

        Kodi halves a ranges texture at exactly 0% and clamps near-zero
        markers to the control's left edge. Padding the coordinate space by
        one texture half-width on each side keeps the marker on the interior
        path while its centre still lands on the true timeline position.
        """
        return 100.0 * (
            TIMELINE_MARKER_HALF_WIDTH
            + percent * TIMELINE_RAIL_WIDTH / 100.0
        ) / TIMELINE_MARKER_CONTROL_WIDTH

    def publish_view(self, view):
        """Publish one coherent semantic frame through a double buffer.

        Kodi does not offer a batch Window-property mutation. The inactive
        slot is therefore populated completely and ``viewslot`` is flipped
        only after every component is ready. The skin never observes fields
        from different revisions if it binds its two groups to slots a/b.
        """
        active = bool(view.get("active"))
        playback_epoch = view.get("playback_epoch")
        actual_valid = bool(view.get("identity")) and playback_epoch not in (
            None,
            "",
        )
        if actual_valid:
            actual_percent = self._safe_percent(
                view.get("actual_percent", 0.0)
            )
            formatted_actual = "%.2f" % self._marker_percent(actual_percent)
            actual_marker = "%s,%s" % (
                formatted_actual,
                formatted_actual,
            )
        else:
            actual_marker = ""
        if self.last.get("actualmarker") != actual_marker:
            if actual_marker:
                self.window.setProperty(
                    SEEK_PREFIX + "actualmarker",
                    actual_marker,
                )
            else:
                self.window.clearProperty(
                    SEEK_PREFIX + "actualmarker"
                )
            self.last["actualmarker"] = actual_marker

        percent = self._safe_percent(view.get("target_percent", 0.0))
        formatted = "%.4f" % percent
        formatted_marker = "%.4f" % self._marker_percent(percent)
        if not active and self.last.get("viewactive"):
            self.window.clearProperty(SEEK_PREFIX + "viewactive")
            self.last["viewactive"] = ""

        values = (
            ("targetvalid", "true" if view.get("target_valid") else ""),
            ("targetfill", "0.0000,%s" % formatted),
            (
                "targetmarker",
                "%s,%s" % (formatted_marker, formatted_marker),
            ),
            ("time", str(view.get("time", ""))),
            ("delta", str(view.get("delta", ""))),
            ("prompt", str(view.get("prompt", ""))),
            (
                "previewstatus",
                str(view.get("preview_status", "none")),
            ),
            (
                "previewpath",
                str(view.get("preview_path", ""))
                if view.get("preview_status") in ("ready", "loading")
                else "",
            ),
            (
                "previewanchor",
                str(int(math.floor(percent + 0.5))),
            ),
        )
        if tuple(field for field, _value in values) != CURRENT_VIEW_SLOT_FIELDS:
            raise RuntimeError("incomplete playback view slot")
        signature = (active, values)
        if signature == self.view_signature:
            return

        next_slot = "b" if self.view_slot == "a" else "a"
        for field, value in values:
            key = "%s.%s" % (next_slot, field)
            if self.last.get(key) == value:
                continue
            if value:
                self.window.setProperty(SEEK_PREFIX + key, value)
            else:
                self.window.clearProperty(SEEK_PREFIX + key)
            self.last[key] = value

        # This is the atomic commit point consumed by the two skin groups.
        self.window.setProperty(SEEK_PREFIX + "viewslot", next_slot)
        self.last["viewslot"] = next_slot
        self.view_slot = next_slot
        self.view_signature = signature

        generation = view.get("controller_generation")
        if (
            self.logger is not None
            and view.get("preview_status") == "ready"
            and view.get("preview_path")
            and generation is not None
            and generation != self.texture_handoff_generation
        ):
            # Kodi offers no texture-loaded callback. This records the final
            # property handoff to the skin; a screenshot verifies rendering.
            self._diagnose(
                "trickplay stage=texture-handoff outcome=ready "
                "reason=property-commit"
            )
            self.texture_handoff_generation = generation

        value = "true" if active else ""
        if self.last.get("viewactive") != value:
            if value:
                self.window.setProperty(SEEK_PREFIX + "viewactive", value)
            else:
                self.window.clearProperty(SEEK_PREFIX + "viewactive")
            self.last["viewactive"] = value

    def clear(self):
        for key in CURRENT_SEEK_PROPERTY_KEYS:
            self.window.clearProperty(SEEK_PREFIX + key)
        self.last = {}
        self.view_slot = None
        self.view_signature = None
        self.preview_diagnostic_generation = None
        self.preview_diagnostics_seen = set()
        self.texture_handoff_generation = None
        self.last_preview_status = "none"
        self.window.clearProperty(SEEK_REQUEST)

    def clear_controller(self):
        """Clear the transaction contract without tearing the latched view."""
        for key in CURRENT_SEEK_CONTROLLER_PROPERTY_KEYS:
            self.window.clearProperty(SEEK_PREFIX + key)
            self.last.pop(key, None)
        self.window.clearProperty(SEEK_REQUEST)
        self.last.pop("seekrequest", None)


class ServiceLease(object):
    """A crash-expiring property used by conditional skin fallbacks."""

    def __init__(self, window=None, clock=None, builtin=None):
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)
        self.clock = clock or time.monotonic
        self.builtin = builtin or xbmc.executebuiltin
        self.next_refresh = 0.0

    def refresh(self, force=False):
        now = self.clock()
        if not force and now < self.next_refresh:
            return
        self.window.setProperty(SERVICE_READY, "true")
        self.window.setProperty(SERVICE_PROTOCOL, SERVICE_PROTOCOL_VERSION)
        self.builtin("CancelAlarm(HTPCServiceLease,silent)")
        self.builtin(
            "AlarmClock(HTPCServiceLease,"
            "ClearProperty(%s,Home),00:02,silent)" % SERVICE_READY
        )
        self.next_refresh = now + LEASE_REFRESH_SECONDS

    def stop(self):
        self.builtin("CancelAlarm(HTPCServiceLease,silent)")
        self.window.clearProperty(SERVICE_READY)
        self.window.clearProperty(SERVICE_PROTOCOL)
        self.next_refresh = 0.0


class HtpcPresenter(object):
    PENDING_FOCUS_TARGETS = frozenset(("timeline", "transport"))
    TOP_BAR_CONTROL_ID = 9102
    TIMELINE_CONTROL_ID = 9300
    TRANSPORT_CONTROL_ID = 9201

    def __init__(self, logger=None, clock=None):
        self.logger = logger
        self.clock = clock or time.monotonic
        self.reset()

    def reset(self):
        """Discard focus delivery and generation state at service boundaries."""
        self.last_generation = None
        self.last_active = False
        self.pending_focus_target = None

    @staticmethod
    def osd_active():
        return xbmc.getCondVisibility("Window.IsActive(videoosd)")

    def show_osd(self):
        if not self.osd_active():
            xbmc.executebuiltin("ActivateWindow(videoosd)")

    def emphasize_timeline(self):
        self._request_focus("timeline")

    def show_transport(self):
        self._request_focus("transport")

    def _request_focus(self, target):
        if target not in self.PENDING_FOCUS_TARGETS:
            raise ValueError("unsupported pending focus target: %s" % target)
        self.pending_focus_target = target
        self.show_osd()
        self._deliver_pending_focus()

    def _deliver_pending_focus(self):
        if not self.pending_focus_target or not self.osd_active():
            return
        target = self.pending_focus_target
        self.pending_focus_target = None
        if target == "timeline":
            self.focus_timeline()
        else:
            self.focus_transport()

    def close_osd(self):
        if self.osd_active():
            xbmc.executebuiltin("Dialog.Close(videoosd)")

    @staticmethod
    def focus_top_bar():
        xbmc.executebuiltin(
            "SetFocus(%d)" % HtpcPresenter.TOP_BAR_CONTROL_ID
        )

    @staticmethod
    def focus_timeline():
        xbmc.executebuiltin(
            "SetFocus(%d)" % HtpcPresenter.TIMELINE_CONTROL_ID
        )

    @staticmethod
    def focus_transport():
        xbmc.executebuiltin(
            "SetFocus(%d)" % HtpcPresenter.TRANSPORT_CONTROL_ID
        )

    @staticmethod
    def osd_action(action):
        xbmc.executebuiltin("Action(%s,videoosd)" % action)

    def update(self, snapshot):
        active = bool(snapshot["active"])
        osd_active = self.osd_active()

        if osd_active:
            self._deliver_pending_focus()

        if not active:
            self.last_generation = None
            self.last_active = False
            return

        if snapshot["generation"] != self.last_generation:
            xbmc.executebuiltin("CancelAlarm(CloseVideoOSD,silent)")
            self.last_generation = snapshot["generation"]
        self.last_active = True
