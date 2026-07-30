"""Strict runtime adapter for the inert BINGIE OSD review window."""

from __future__ import annotations

import sys

import xbmc
import xbmcgui

from review_contract import (
    EXPECTED_FOCUS,
    HOME_WINDOW_ID,
    PROPERTY_KEYS,
    PROPERTY_PREFIX,
    RequestError,
    parse_request,
    scenario_properties,
)


ACTIVE_REVIEW = "Window.IsActive(1192)"
CLOSE_REVIEW = "Dialog.Close(1192,true)"
OPEN_REVIEW = "ActivateWindow(1192)"
LOG_PREFIX = "HTPC OSD Review: "
REVIEW_TTL_TICKS = 160
TICK_SECONDS = 0.05
NO_DIALOG_IDS = frozenset((0, 9999))
REVIEW_DIALOG_IDS = frozenset((1192, 11192))


def _is_bingie():
    return xbmc.getSkinDir() == "skin.bingie"


def _clear(window):
    failures = []
    for key in PROPERTY_KEYS:
        try:
            window.clearProperty(key)
        except Exception as error:
            failures.append("%s: %s" % (key, error))
    if failures:
        raise RuntimeError(
            "review property cleanup failed: " + "; ".join(failures)
        )


def _close_owned_window():
    failures = []
    for _attempt in range(3):
        if not xbmc.getCondVisibility(ACTIVE_REVIEW):
            return
        try:
            xbmc.executebuiltin(CLOSE_REVIEW)
        except Exception as error:
            failures.append(str(error))
        for _poll in range(10):
            if not xbmc.getCondVisibility(ACTIVE_REVIEW):
                return
            xbmc.sleep(25)
    detail = ": " + "; ".join(failures) if failures else ""
    raise RuntimeError("review window did not close" + detail)


def _properties_are_empty(window):
    return all(not window.getProperty(key) for key in PROPERTY_KEYS)


def _stage(window, scenario):
    values = scenario_properties(scenario)
    for key in PROPERTY_KEYS:
        if key in (
            PROPERTY_PREFIX + "ready",
            PROPERTY_PREFIX + "seek.viewslot",
            PROPERTY_PREFIX + "seek.viewactive",
        ):
            continue
        value = values.get(key)
        if value:
            window.setProperty(key, value)
    # Match production's atomic view publication: complete the inactive slot,
    # select it, then expose the view.
    for name in ("seek.viewslot", "seek.viewactive"):
        key = PROPERTY_PREFIX + name
        value = values.get(key)
        if value:
            window.setProperty(key, value)


def _safe_to_open():
    return (
        _is_bingie()
        and xbmcgui.getCurrentWindowId() == HOME_WINDOW_ID
        and xbmcgui.getCurrentWindowDialogId() in NO_DIALOG_IDS
        and not xbmc.getCondVisibility("Player.HasMedia")
        and not xbmc.getCondVisibility("System.ScreenSaverActive")
        and not xbmc.getCondVisibility("System.DPMSActive")
        and not xbmc.getCondVisibility("Window.IsActive(busydialog)")
        and not xbmc.getCondVisibility("Window.IsActive(busydialognocancel)")
    )


def _review_is_stable(expected_focus):
    return (
        _is_bingie()
        and xbmcgui.getCurrentWindowId() == HOME_WINDOW_ID
        and xbmcgui.getCurrentWindowDialogId() in REVIEW_DIALOG_IDS
        and xbmc.getCondVisibility(ACTIVE_REVIEW)
        and xbmc.getInfoLabel("System.CurrentControlId") == expected_focus
        and not xbmc.getCondVisibility("Player.HasMedia")
        and not xbmc.getCondVisibility("System.ScreenSaverActive")
        and not xbmc.getCondVisibility("System.DPMSActive")
        and not xbmc.getCondVisibility("Window.IsActive(busydialog)")
        and not xbmc.getCondVisibility("Window.IsActive(busydialognocancel)")
    )


def _await_review_focus(monitor, expected_focus):
    for _attempt in range(40):
        if _review_is_stable(expected_focus):
            return
        if monitor.waitForAbort(TICK_SECONDS):
            raise RuntimeError("Kodi stopped while opening review")
    raise RuntimeError("review window did not reach expected focus")


def _hold_review(monitor, expected_focus):
    for _tick in range(REVIEW_TTL_TICKS):
        if monitor.waitForAbort(TICK_SECONDS):
            return
        if not _review_is_stable(expected_focus):
            raise RuntimeError("review state drifted after readiness")


def _cleanup(window):
    failures = []
    try:
        window.clearProperty(PROPERTY_PREFIX + "ready")
    except Exception as error:
        failures.append("ready: %s" % error)
    try:
        _close_owned_window()
    except Exception as error:
        failures.append("window: %s" % error)
    try:
        _clear(window)
    except Exception as error:
        failures.append("properties: %s" % error)
    try:
        if xbmc.getCondVisibility(ACTIVE_REVIEW):
            failures.append("window remains active")
        if not _properties_are_empty(window):
            failures.append("properties remain set")
    except Exception as error:
        failures.append("verification: %s" % error)
    if failures:
        raise RuntimeError("review cleanup failed: " + "; ".join(failures))


def _execute(arguments):
    request, value = parse_request(arguments)
    if not _is_bingie():
        raise RequestError("active skin is not BINGIE")

    window = xbmcgui.Window(HOME_WINDOW_ID)
    if request == "command":
        if (
            not xbmc.getCondVisibility(ACTIVE_REVIEW)
            or not window.getProperty(PROPERTY_PREFIX + "ready")
        ):
            raise RequestError("no ready review is active")
        _cleanup(window)
        return True

    if not _safe_to_open():
        raise RequestError("review requires an idle BINGIE Home window")
    if xbmc.getCondVisibility(ACTIVE_REVIEW):
        raise RequestError("review window is already active")
    if not _properties_are_empty(window):
        raise RequestError("stale review properties require cleanup")

    expected_focus = EXPECTED_FOCUS[value]
    expected_properties = scenario_properties(value)
    try:
        monitor = xbmc.Monitor()
        _stage(window, value)
        if expected_properties != {
            key: window.getProperty(key)
            for key in expected_properties
        }:
            raise RuntimeError("staged review fixture did not verify")
        if not _safe_to_open():
            raise RuntimeError("Kodi state changed while staging review")
        xbmc.executebuiltin(OPEN_REVIEW)
        _await_review_focus(monitor, expected_focus)
        # This is an observation fence, published only after focus is proven.
        window.setProperty(PROPERTY_PREFIX + "ready", "true")
        _hold_review(monitor, expected_focus)
        return True
    finally:
        _cleanup(window)


def run(arguments):
    """Execute one finite request with bounded, unconditional cleanup."""
    try:
        return _execute(arguments)
    except (RequestError, RuntimeError) as error:
        xbmc.log(LOG_PREFIX + str(error), xbmc.LOGERROR)
        return False


if __name__ == "__main__":
    run(sys.argv[1:])
