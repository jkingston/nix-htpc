#!/usr/bin/python
# -*- coding: utf-8 -*-

import os, sys
from datetime import datetime, timedelta
from operator import itemgetter
import xbmc
from widgetshelper import kodi_constants
from resources.lib.episode_progress import (
    CONTINUE,
    UP_NEXT,
    resolve_series_primary,
    resolve_series_progress,
)
from resources.lib.utils import create_main_entry,log_msg

class Episodes(object):
    ''' all episode widgets provided by the script '''
    options = {}
    kodidb = None
    addon = None

    def __init__(self, addon, widgetshelper, options):
        ''' Initialization '''
        self.addon = addon
        self.widgetshelper = widgetshelper
        options["next_inprogress_only"] = self.addon.getSetting("nextup_inprogressonly") == "true"
        options["episodes_enable_specials"] = self.addon.getSetting("episodes_enable_specials") == "true"
        options["group_episodes"] = self.addon.getSetting("episodes_grouping") == "true"
        self.options = options

    def listing(self):
        ''' main listing with all our episode nodes '''
        icon = "DefaultTvShows.png"
        all_items = [
            (self.addon.getLocalizedString(32013), "inprogress&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32000), "nextepisode&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32020), "recent&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32007), "recommended&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32008), "inprogressandrecommended&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32027), "inprogressandrandom&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32006), "random&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32022), "unaired&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32023), "nextaired&mediatype=episodes", icon),
            (self.addon.getLocalizedString(32035), "airingtoday&mediatype=episodes", icon)
        ]
        return self.widgetshelper.process_method_on_list(create_main_entry, all_items)

    def recommended(self):
        ''' get recommended episodes - library episodes with score higher than 7 '''
        filters = [kodi_constants.FILTER_RATING]
        if self.options["hide_watched"]:
            filters.append(kodi_constants.FILTER_UNWATCHED)
        return self.widgetshelper.kodidb.episodes(sort=kodi_constants.SORT_RATING, filters=filters,
                                                  limits=(0, self.options["limit"]))

    def recent(self):
        ''' get recently added episodes '''
        log_msg("recent widget", xbmc.LOGINFO)
        tvshow_episodes = {}
        total_count = 0
        unique_count = 0
        filters = []
        if self.options["hide_watched"]:
            filters.append(kodi_constants.FILTER_UNWATCHED)
        if self.options.get("tag"):
            filters.append({"operator": "contains", "field": "tag", "value": self.options["tag"]})
        if self.options.get("path"):
            filters.append({"operator": "startswith", "field": "path", "value": self.options["path"]})
        while unique_count < self.options["limit"]:
            recent_episodes = self.widgetshelper.kodidb.episodes(
                sort=kodi_constants.SORT_DATEADDED, filters=filters, limits=(
                    total_count, self.options["limit"] + total_count))
            log_msg("Check grouping setting", xbmc.LOGINFO)
            if not self.options["group_episodes"]:
                # grouping is not enabled, just return the result
                log_msg("Grouping not enabled, return normal result", xbmc.LOGINFO)
                return recent_episodes

            if len(recent_episodes) < self.options["limit"]:
                # break the loop if there are no more episodes
                unique_count = self.options["limit"]

            # if multiple episodes for the same show with same addition date, we combine them into one
            # to do that we build a dict with recent episodes for all episodes of the same season added on the same date
            for episode in recent_episodes:
                total_count += 1
                unique_key = "%s-%s-%s" % (episode["tvshowid"], episode["dateadded"].split(" ")[0], episode["season"])
                log_msg("Unique %s" % unique_key, xbmc.LOGINFO)
                if unique_key not in tvshow_episodes:
                    tvshow_episodes[unique_key] = []
                    unique_count += 1
                tvshow_episodes[unique_key].append(episode)

        log_msg("Return entries sorted by dateadded", xbmc.LOGINFO)
        # create our entries and return the result sorted by dateadded
        all_items = self.widgetshelper.process_method_on_list(self.create_grouped_entry, tvshow_episodes.values())
        return sorted(all_items, key=itemgetter("dateadded"), reverse=True)[:self.options["limit"]]

    def random(self):
        ''' get random episodes '''
        filters = []
        if self.options["hide_watched"]:
            filters.append(kodi_constants.FILTER_UNWATCHED)
        if self.options.get("tag"):
            filters.append({"operator": "contains", "field": "tag", "value": self.options["tag"]})
        if self.options.get("path"):
            filters.append({"operator": "startswith", "field": "path", "value": self.options["path"]})
        return self.widgetshelper.kodidb.episodes(sort=kodi_constants.SORT_RANDOM, filters=filters,
                                                  limits=(0, self.options["limit"]))

    def inprogress(self):
        ''' get in progress episodes '''
        filters = [kodi_constants.FILTER_INPROGRESS]
        if self.options.get("tag"):
            filters.append({"operator": "contains", "field": "tag", "value": self.options["tag"]})
        if self.options.get("path"):
            filters.append({"operator": "startswith", "field": "path", "value": self.options["path"]})
        return self.widgetshelper.kodidb.episodes(sort=kodi_constants.SORT_LASTPLAYED, filters=filters,
                                                  limits=(0, self.options["limit"]))

    def inprogressandrecommended(self):
        ''' get recommended AND in progress episodes '''
        all_items = self.inprogress()
        all_titles = [item["title"] for item in all_items]
        for item in self.recommended():
            if item["title"] not in all_titles:
                all_items.append(item)
        return all_items[:self.options["limit"]]

    def inprogressandrandom(self):
        ''' get recommended AND random episodes '''
        all_items = self.inprogress()
        all_ids = [item["episodeid"] for item in all_items]
        for item in self.random():
            if item["episodeid"] not in all_ids:
                all_items.append(item)
        return all_items[:self.options["limit"]]

    def nextepisode(self):
        ''' compatibility alias for the sequence-based Up Next listing '''
        return self._progress_items(UP_NEXT)

    def continuewatching(self):
        ''' get the most recently played unfinished episode for each show '''
        return self._progress_items(CONTINUE)

    def seriesprogress(self):
        ''' get the resolved resume/next item for one TV show '''
        return self._progress_items(None)

    def nextinprogress(self):
        """Get the immediate successor of each show's latest completed play."""
        return self._progress_items(UP_NEXT)

    def _progress_items(self, state):
        filters = []
        if self.options.get("tag"):
            filters.append({
                "operator": "contains",
                "field": "tag",
                "value": self.options["tag"],
            })
        if self.options.get("path"):
            filters.append({
                "operator": "startswith",
                "field": "path",
                "value": self.options["path"],
            })

        episode_state = self.widgetshelper.kodidb.episodes(
            filters=filters,
            tvshowid=self.options.get("tvshowid"),
            fields=[
                "tvshowid", "season", "episode", "playcount",
                "lastplayed", "resume",
            ],
        )
        include_specials = (
            self.options["episodes_enable_specials"]
            and not self.options.get("jellyfin_ignore_specials")
        )
        decisions = resolve_series_progress(
            episode_state,
            include_specials=include_specials,
        )
        if state is None:
            primary = resolve_series_primary(
                episode_state,
                include_specials=include_specials,
            )
            decisions = [primary] if primary is not None else []
        else:
            decisions = [
                decision for decision in decisions if decision.state == state
            ]
        if state == UP_NEXT:
            decisions = self._within_next_episode_age(decisions)
        decisions = decisions[:self.options["limit"]]
        items = self.widgetshelper.kodidb.episode_details(
            [decision.target_episodeid for decision in decisions]
        )
        decisions_by_episode = {
            decision.target_episodeid: decision for decision in decisions
        }
        for item in items:
            decision = decisions_by_episode.get(item.get("episodeid"))
            if decision is None:
                continue
            properties = item.setdefault("extraproperties", {})
            properties["BingieEpisodeIndex"] = str(decision.target_index)
            properties["BingieSeriesProgress"] = decision.state
        return items

    def _within_next_episode_age(self, decisions):
        max_days = self.options.get("jellyfin_max_days", 0)
        if not max_days:
            return decisions
        cutoff = datetime.now() - timedelta(days=max_days)
        recent = []
        for decision in decisions:
            try:
                played_at = datetime.strptime(
                    decision.lastplayed,
                    "%Y-%m-%d %H:%M:%S",
                )
            except (TypeError, ValueError):
                continue
            if played_at >= cutoff:
                recent.append(decision)
        return recent

    @staticmethod
    def create_grouped_entry(tvshow_episodes):
        ''' helper for grouped episodes '''
        firstepisode = tvshow_episodes[0]
        if len(tvshow_episodes) > 2:
            # add as season entry if there were multiple episodes for the same show
            # use first episode as reference to keep the correct sorting order
            item = firstepisode
            item["type"] = "season"
            item["label"] = "%s %s" % (xbmc.getLocalizedString(20373), firstepisode["season"])
            item["plot"] = u"[B]%s[/B] • %s %s[CR]%s: %s"\
                % (item["label"], len(tvshow_episodes), xbmc.getLocalizedString(20387),
                   xbmc.getLocalizedString(570), firstepisode["dateadded"].split(" ")[0])
            item["extraproperties"] = {"UnWatchedEpisodes": "%s" % len(tvshow_episodes)}
            return item
        # just add the single item
        return firstepisode

    @staticmethod
    def map_episode_props(episode_details):
        ''' adds some of the optional fields as extra properties for the listitem '''
        extraprops = {}
        for item in ["network", "airdate", "airdate.label", "airtime", "airdatetime", "airdatetime.label", "airday"]:
            extraprops[item] = episode_details[item]
        extraprops["DBTYPE"] = "episode"
        episode_details["extraproperties"] = extraprops
        return episode_details
