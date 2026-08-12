#!/usr/bin/python
# -*- coding: utf-8 -*-

from resources.lib.utils import log_msg, ADDON_ID
from resources.lib.kodi_monitor import KodiMonitor
import xbmc
import xbmcgui
import xbmcaddon

WIN = xbmcgui.Window(10000)
ADDON = xbmcaddon.Addon(ADDON_ID)
MONITOR = KodiMonitor(win=WIN, addon=ADDON)
log_msg('Backgroundservice started', xbmc.LOGINFO)

# Keep the Kodi monitor alive. Picker anchor requests are polled frequently so
# the initial index build runs in the service worker without blocking input.
while not MONITOR.waitForAbort(0.25):
    MONITOR.tick()

MONITOR.stop()
del MONITOR
del WIN
del ADDON
log_msg('Backgroundservice stopped', xbmc.LOGINFO)
