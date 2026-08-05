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

# keep the kodi monitor alive which processes database updates to refresh widgets
while not MONITOR.waitForAbort(60):
    pass

del MONITOR
del WIN
del ADDON
log_msg('Backgroundservice stopped', xbmc.LOGINFO)
