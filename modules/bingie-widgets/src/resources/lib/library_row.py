#!/usr/bin/python
# -*- coding: utf-8 -*-

LIBRARIES = {
    "movies": {
        "destination": "library://video/movies/titles.xml",
        "icon": "DefaultMovies.png",
    },
    "tvshows": {
        "destination": "library://video/tvshows/titles.xml",
        "icon": "DefaultTvShows.png",
    },
}
TITLE_SORT = {"method": "title", "order": "ascending"}


def build_library_row(kodidb, media_type, limit, item_processor=None):
    """Return an alphabetical library preview with View All as its last tile."""
    library = LIBRARIES[media_type]
    preview_limit = max(limit - 1, 0)
    query = getattr(kodidb, media_type)
    items = query(
        sort=TITLE_SORT,
        limits=(0, preview_limit + 1),
    )

    has_more = len(items) > preview_limit
    items = items[:preview_limit] if has_more else items[:limit]
    if item_processor is not None:
        items = [item_processor(item) for item in items]
    if has_more:
        items.append(_view_all_item(library))
    return items


def _view_all_item(library):
    return {
        "label": "View All",
        "title": "View All",
        "file": library["destination"],
        "icon": library["icon"],
        "art": {},
        "extraproperties": {"BingieViewAll": "true"},
        "isFolder": True,
        "type": "files",
        "IsPlayable": "false",
    }
