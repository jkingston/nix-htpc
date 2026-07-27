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


set_setting("videoplayer.useprimedecoder", True)
set_setting("videoplayer.useprimerenderer", 0)
set_setting("videoplayer.adjustrefreshrate", 2)
set_setting("videoscreen.whitelist", PLAYBACK_MODES)
set_setting("videoscreen.whitelistpulldown", False)
set_setting("videoscreen.whitelistdoublerefreshrate", False)
xbmc.log("HTPC settings: managed settings applied", xbmc.LOGINFO)
