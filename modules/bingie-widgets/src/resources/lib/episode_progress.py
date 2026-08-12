#!/usr/bin/python
# -*- coding: utf-8 -*-

"""Resolve one mutually exclusive home-screen state for each TV series."""

from collections import defaultdict, namedtuple


CONTINUE = "continue"
UP_NEXT = "up_next"

SeriesProgress = namedtuple(
    "SeriesProgress",
    (
        "state", "tvshowid", "anchor_episodeid", "target_episodeid",
        "target_index", "lastplayed",
    ),
)


def resolve_series_progress(episodes, include_specials=False):
    """Return the current Continue Watching or Up Next decision per series.

    A series is anchored to its most recent meaningful playback. An unfinished
    anchor is resumable; a completed anchor advances to the immediate library
    successor even when that successor has previously been watched.
    """
    decisions = []
    for tvshow_id, show_episodes in _group_by_show(episodes).items():
        library_order = sorted(show_episodes, key=_episode_order)
        ordered = library_order
        if not include_specials:
            ordered = [
                episode for episode in library_order
                if _as_int(episode.get("season")) > 0
            ]
        played = [episode for episode in ordered if _is_meaningful(episode)]
        if not played:
            continue

        anchor = max(played, key=lambda episode: episode.get("lastplayed", ""))
        if _resume_position(anchor) > 0:
            decisions.append(
                _decision(CONTINUE, tvshow_id, anchor, anchor, library_order)
            )
            continue

        successor = _successor(ordered, anchor)
        if successor is not None:
            decisions.append(
                _decision(UP_NEXT, tvshow_id, anchor, successor, library_order)
            )

    return sorted(decisions, key=lambda decision: decision.lastplayed, reverse=True)


def resolve_series_primary(episodes, include_specials=False):
    """Resolve a show's play target, falling back to its first episode."""
    decisions = resolve_series_progress(episodes, include_specials)
    if decisions:
        return decisions[0]

    return _first_episode_decision(episodes, include_specials)


def resolve_series_primaries(
        episodes, include_specials=False, progress_decisions=None):
    """Return the playable target for every series in a library snapshot."""
    if progress_decisions is None:
        progress_decisions = resolve_series_progress(
            episodes, include_specials
        )
    progress_by_show = {
        decision.tvshowid: decision for decision in progress_decisions
    }
    primaries = []
    for tvshow_id, show_episodes in _group_by_show(episodes).items():
        primary = progress_by_show.get(tvshow_id)
        if primary is None:
            primary = _first_episode_decision(
                show_episodes, include_specials
            )
        if primary is not None:
            primaries.append(primary)
    return primaries


def _first_episode_decision(episodes, include_specials):
    """Return a first-episode fallback without re-running progress resolution."""

    library_order = sorted(
        [episode for episode in episodes if episode.get("episodeid") is not None],
        key=_episode_order,
    )
    playable = library_order
    if not include_specials:
        playable = [
            episode for episode in library_order
            if _as_int(episode.get("season")) > 0
        ]
    if not playable:
        return None

    target = playable[0]
    return _decision(
        UP_NEXT,
        target.get("tvshowid"),
        target,
        target,
        library_order,
    )


def _group_by_show(episodes):
    episodes_by_show = defaultdict(list)
    for episode in episodes:
        tvshow_id = episode.get("tvshowid")
        episode_id = episode.get("episodeid")
        if tvshow_id is None or episode_id is None:
            continue
        episodes_by_show[tvshow_id].append(episode)
    return episodes_by_show


def _decision(state, tvshow_id, anchor, target, library_order):
    return SeriesProgress(
        state=state,
        tvshowid=tvshow_id,
        anchor_episodeid=anchor["episodeid"],
        target_episodeid=target["episodeid"],
        target_index=_episode_index(library_order, target["episodeid"]),
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


def _episode_index(ordered, episode_id):
    for index, episode in enumerate(ordered):
        if episode.get("episodeid") == episode_id:
            return index
    return 0


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
