import json

import xbmc

PLAYBACK_MODES = [
    "0384002160023.97603pstd",
    "0384002160024.00000pstd",
    "0384002160025.00000pstd",
    "0384002160029.97003pstd",
    "0384002160030.00000pstd",
]


def set_setting(setting, value):
    response = json.loads(xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0",
        "method": "Settings.SetSettingValue",
        "params": {"setting": setting, "value": value},
        "id": setting,
    })))
    if "error" in response:
        xbmc.log(
            f"HTPC settings: failed to set {setting}: {response['error']}",
            xbmc.LOGERROR,
        )


def set_skin_setting(setting, enabled):
    """Apply a BINGIE preference without managing its mutable settings file."""
    command = "Skin.SetBool" if enabled else "Skin.Reset"
    xbmc.executebuiltin(f"{command}({setting})")


set_setting("videoplayer.useprimedecoder", True)
set_setting("videoplayer.useprimerenderer", 0)
set_setting("videoplayer.adjustrefreshrate", 2)
set_setting("videoscreen.whitelist", PLAYBACK_MODES)
set_setting("videoscreen.whitelistpulldown", False)
set_setting("videoscreen.whitelistdoublerefreshrate", False)
# Keep one press predictable while allowing rapid CEC repeats to accumulate
# into one final stream seek. This gives the OSD time to display Kodi's pending
# seek target instead of repeatedly flushing the Jellyfin HTTP stream.
set_setting("videoplayer.seeksteps", [-60, -30, -10, 10, 30, 60])
set_setting("videoplayer.seekdelay", 500)
set_setting("filelists.showparentdiritems", False)
set_setting("input.enablemouse", False)

# The service can start just before the configured skin becomes active. Wait
# briefly, then remove settings that make a TV-first interface feel indirect
# or expose maintenance actions in the normal movie-details flow.
monitor = xbmc.Monitor()
for _ in range(30):
    if xbmc.getSkinDir() == "skin.bingie":
        break
    if monitor.waitForAbort(1):
        raise SystemExit

if xbmc.getSkinDir() == "skin.bingie":
    for setting in [
        "EnableAutoPauseOnOSD",
        "videoinfo_button_trakt",
        "videoinfo_button_plot",
        "videoinfo_button_versions",
        "videoinfo_button_favorites",
        "videoinfo_button_myrating",
        "videoinfo_button_refresh",
        "videoinfo_button_artwork",
        "videoinfo_button_wikipedia",
        "videoinfo_button_moreinfo",
        "videoinfo_button_trailersandmore",
    ]:
        set_skin_setting(setting, False)

xbmc.log("HTPC settings: managed settings applied", xbmc.LOGINFO)
