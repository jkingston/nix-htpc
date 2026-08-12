#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Small, Kodi-independent helpers for episode-picker focus state."""


def _episode_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def season_anchor_positions(episodes):
    """Return the first absolute episode position for every season.

    The skin's all-episodes container is ordered by season and episode.  Keep
    this calculation independent of Kodi so it can be tested without a Kodi
    runtime and so the service only needs to fetch the two numeric fields.
    """
    ordered = []
    for item in episodes or []:
        season = _episode_number(item.get("season"))
        episode = _episode_number(item.get("episode"))
        if season is None or episode is None:
            continue
        episode_id = _episode_number(item.get("episodeid"))
        ordered.append((season, episode, episode_id or 0, item))

    ordered.sort(key=lambda value: value[:3])
    anchors = {}
    for position, (season, _episode, _episode_id, _item) in enumerate(ordered, 1):
        anchors.setdefault(str(season), str(position))
    return anchors


def next_picker_position(position):
    """Return the absolute position to focus after the current episode."""
    try:
        return str(int(position) + 1)
    except (TypeError, ValueError):
        return ""
