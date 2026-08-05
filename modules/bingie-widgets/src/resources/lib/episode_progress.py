#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Resolve one mutually exclusive home-screen state for each TV series."""

from collections import defaultdict, namedtuple


CONTINUE = "continue"
UP_NEXT = "up_next"

SeriesProgress = namedtuple(
    "SeriesProgress",
    ("state", "tvshowid", "anchor_episodeid", "target_episodeid", "lastplayed"),
)


def resolve_series_progress(episodes, include_specials=False):
    """Return the current Continue Watching or Up Next decision per series.

    A series is anchored to its most recent meaningful playback. An unfinished
    anchor is resumable; a completed anchor advances to the immediate library
    successor even when that successor has previously been watched.
    """
    episodes_by_show = defaultdict(list)
    for episode in episodes:
        tvshow_id = episode.get("tvshowid")
        episode_id = episode.get("episodeid")
        if tvshow_id is None or episode_id is None:
            continue
        if not include_specials and _as_int(episode.get("season")) == 0:
            continue
        episodes_by_show[tvshow_id].append(episode)

    decisions = []
    for tvshow_id, show_episodes in episodes_by_show.items():
        ordered = sorted(show_episodes, key=_episode_order)
        played = [episode for episode in ordered if _is_meaningful(episode)]
        if not played:
            continue

        anchor = max(played, key=lambda episode: episode.get("lastplayed", ""))
        if _resume_position(anchor) > 0:
            decisions.append(_decision(CONTINUE, tvshow_id, anchor, anchor))
            continue

        successor = _successor(ordered, anchor)
        if successor is not None:
            decisions.append(_decision(UP_NEXT, tvshow_id, anchor, successor))

    return sorted(decisions, key=lambda decision: decision.lastplayed, reverse=True)


def _decision(state, tvshow_id, anchor, target):
    return SeriesProgress(
        state=state,
        tvshowid=tvshow_id,
        anchor_episodeid=anchor["episodeid"],
        target_episodeid=target["episodeid"],
        lastplayed=anchor.get("lastplayed", ""),
    )


def _is_meaningful(episode):
    if not episode.get("lastplayed"):
        return False
    return _resume_position(episode) > 0 or _as_int(episode.get("playcount")) > 0


def _resume_position(episode):
    resume = episode.get("resume") or {}
    try:
        return float(resume.get("position") or 0)
    except (TypeError, ValueError):
        return 0


def _episode_order(episode):
    return (
        _as_int(episode.get("season")),
        _as_int(episode.get("episode")),
        _as_int(episode.get("episodeid")),
    )


def _successor(ordered, anchor):
    anchor_id = anchor.get("episodeid")
    for index, episode in enumerate(ordered):
        if episode.get("episodeid") == anchor_id:
            next_index = index + 1
            return ordered[next_index] if next_index < len(ordered) else None
    return None


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
