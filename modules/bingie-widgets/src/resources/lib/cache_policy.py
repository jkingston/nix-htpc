#!/usr/bin/python
# -*- coding: utf-8 -*-

import uuid


SESSION_PROPERTY = "Bingie.Widgets.Session"
SESSION_SCOPED_ACTIONS = frozenset(("serverplaylists", "spotlight"))


class WidgetCachePolicy(object):
    """Define cache identity separately from widget query implementation."""

    def __init__(self, window, token_factory=None):
        self.window = window
        self.token_factory = token_factory or (lambda: uuid.uuid4().hex)

    def should_read(self, options):
        return not (
            options.get("skipcache") == "true"
            or "listing" in options.get("action", "")
        )

    def checksum(self, options):
        if options.get("action") in SESSION_SCOPED_ACTIONS:
            return self._session_token()
        return options.get("reload") or self._media_generation(options)

    def _session_token(self):
        token = self.window.getProperty(SESSION_PROPERTY)
        if token:
            return token
        token = self.token_factory()
        self.window.setProperty(SESSION_PROPERTY, token)
        return token

    def _media_generation(self, options):
        media_type = options.get("mediatype", "")
        if media_type == "media":
            return self.window.getProperty("widgetreload") or None
        return self.window.getProperty("widgetreload-%s" % media_type) or None
