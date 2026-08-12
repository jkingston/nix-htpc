#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Small, Kodi-independent helpers for episode-picker focus state."""


def next_picker_position(position):
    """Return the absolute position to focus after the current episode."""
    try:
        return str(int(position) + 1)
    except (TypeError, ValueError):
        return ""
