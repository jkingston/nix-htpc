from __future__ import absolute_import, division, print_function

from functools import partial

import xbmcgui

from media_contract import (
    CHAPTERS_AVAILABLE,
    CHAPTERS_MANIFEST,
    CHAPTERS_PLAYBACK,
    CHAPTERS_REVISION,
    CHAPTERS_TOKEN,
    CHAPTER_AVAILABLE,
    CHAPTER_OPEN,
    HOME_WINDOW_ID,
    chapter_contract_available,
    parse_chapter_payload,
)
from seek_controller import format_time


CHAPTER_LIST_ID = 11
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4


class ChapterPropertyProvider(object):
    def __init__(self, window=None):
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)

    def _properties(self):
        keys = (
            CHAPTERS_AVAILABLE,
            CHAPTERS_MANIFEST,
            CHAPTERS_PLAYBACK,
            CHAPTERS_TOKEN,
            CHAPTERS_REVISION,
        )
        return dict((key, self.window.getProperty(key)) for key in keys)

    def load(self):
        properties = self._properties()
        if not chapter_contract_available(properties):
            return None, []
        # Include lifecycle and manifest revisions in the dialog snapshot
        # identity. Playback alone stays constant while chapter frames arrive.
        token = properties[CHAPTERS_TOKEN]
        chapters = parse_chapter_payload(
            properties[CHAPTERS_MANIFEST],
            expected_token=properties[CHAPTERS_PLAYBACK],
        )
        return token, chapters

    def available(self):
        _token, chapters = self.load()
        return len(chapters) >= 2


class ChapterRail(xbmcgui.WindowXMLDialog):
    """Temporary, Jellyfin chapter-only rail."""

    def __init__(self, *args, **kwargs):
        self.chapters = kwargs.pop("chapters", [])
        self.current_seconds = float(kwargs.pop("current_seconds", 0.0))
        self.select_callback = kwargs.pop("select_callback", None)
        self.focus_callback = kwargs.pop("focus_callback", None)
        self.exit_callback = kwargs.pop("exit_callback", None)
        self._closing = False
        super(ChapterRail, self).__init__(*args, **kwargs)

    def onInit(self):
        control = self.getControl(CHAPTER_LIST_ID)
        items = []
        focus_position = 0
        for position, chapter in enumerate(self.chapters):
            item = xbmcgui.ListItem(
                label=chapter["label"],
                label2=format_time(chapter["start_seconds"]),
            )
            if chapter["image_path"]:
                item.setArt(
                    {"thumb": chapter["image_path"]}
                )
            items.append(item)
            if chapter["start_seconds"] <= self.current_seconds:
                focus_position = position
        control.addItems(items)
        try:
            control.selectItem(focus_position)
        except AttributeError:
            pass
        self.setFocusId(CHAPTER_LIST_ID)

    def onClick(self, control_id):
        if control_id != CHAPTER_LIST_ID or self._closing:
            return
        control = self.getControl(CHAPTER_LIST_ID)
        position = control.getSelectedPosition()
        if not 0 <= position < len(self.chapters):
            return
        chapter = dict(self.chapters[position])
        if self.select_callback:
            self.select_callback(chapter)

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT):
            control = self.getControl(CHAPTER_LIST_ID)
            selected = control.getSelectedPosition()
            if not 0 <= selected < len(self.chapters):
                return
            if self.focus_callback:
                chapter = dict(self.chapters[selected])
                chapter["physical_direction"] = (
                    "left" if action_id == ACTION_MOVE_LEFT else "right"
                )
                self.focus_callback(chapter)
        elif action_id == ACTION_MOVE_UP:
            self._request_exit("top", "up")
        elif action_id == ACTION_MOVE_DOWN:
            self._request_exit("timeline", "down")
        elif action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self._request_exit("back")

    def _request_exit(self, destination, physical_direction=None):
        if self._closing:
            return
        if self.exit_callback:
            self.exit_callback(destination, physical_direction)

    def close_without_event(self):
        if self._closing:
            return
        self._closing = True
        self.close()


class ChapterDialogManager(object):
    def __init__(
        self,
        addon_path,
        event_sink,
        provider=None,
        dialog_class=None,
        window=None,
    ):
        self.addon_path = addon_path
        self.event_sink = event_sink
        self.provider = provider or ChapterPropertyProvider()
        self.dialog_class = dialog_class or ChapterRail
        self.window = window or xbmcgui.Window(HOME_WINDOW_ID)
        self.dialog = None
        self.token = None
        self.dialog_generation = 0
        self.active_dialog_generation = None
        self.pending_synthetic_generation = None

    @property
    def is_open(self):
        return self.dialog is not None

    def available(self):
        return self.provider.available()

    def open(self, current_seconds=0.0):
        if self.dialog is not None:
            return True
        token, chapters = self.provider.load()
        if len(chapters) < 2:
            return False
        self.token = token
        self.dialog_generation += 1
        generation = self.dialog_generation
        self.active_dialog_generation = generation
        self.pending_synthetic_generation = None
        self.dialog = self.dialog_class(
            "ChapterRail.xml",
            self.addon_path,
            "Default",
            "1080i",
            chapters=chapters,
            current_seconds=current_seconds,
            select_callback=partial(self._selected, generation),
            focus_callback=partial(self._focused, generation),
            exit_callback=partial(self._exit, generation),
        )
        self.dialog.show()
        self.window.setProperty(CHAPTER_OPEN, "true")
        return True

    def _selected(self, generation, chapter):
        chapter["playback_token"] = self.token
        chapter["dialog_generation"] = generation
        self.event_sink("chapter-select", chapter)

    def _focused(self, generation, chapter):
        chapter["playback_token"] = self.token
        chapter["dialog_generation"] = generation
        self.event_sink("chapter-focus", chapter)

    def _exit(self, generation, destination, physical_direction=None):
        payload = {
            "destination": destination,
            "dialog_generation": generation,
            # Only a physical Back leaving the dialog should suppress the
            # rest of that repeat train on the newly exposed OSD.
            "arm_back": destination == "back",
        }
        if physical_direction is not None:
            payload["physical_direction"] = physical_direction
        self.event_sink("chapter-exit", payload)

    def accepts_event(self, payload):
        if (payload or {}).get("synthetic"):
            generation = (payload or {}).get("dialog_generation")
            if generation != self.pending_synthetic_generation:
                return False
            self.pending_synthetic_generation = None
            return True
        return (
            self.active_dialog_generation is not None
            and (payload or {}).get("dialog_generation")
            == self.active_dialog_generation
        )

    def close(self, notify=False, destination="back"):
        dialog = self.dialog
        generation = self.active_dialog_generation
        self.dialog = None
        self.token = None
        self.active_dialog_generation = None
        self.pending_synthetic_generation = None
        self.window.clearProperty(CHAPTER_OPEN)
        if dialog is not None:
            dialog.close_without_event()
            if notify:
                # Contract loss is an involuntary exit from a pause-owned
                # chapter transaction. Route it through the same cancel path
                # as Back so playback cannot remain stranded while paused.
                self.pending_synthetic_generation = generation
                self.event_sink(
                    "chapter-exit",
                    {
                        "destination": destination,
                        "arm_back": False,
                        "dialog_generation": generation,
                        "synthetic": True,
                    },
                )

    def validate(self):
        if self.dialog is None:
            return
        token, chapters = self.provider.load()
        if token != self.token or len(chapters) < 2:
            self.close(notify=True)

    def sync_properties(self):
        if self.available():
            self.window.setProperty(CHAPTER_AVAILABLE, "true")
        else:
            self.window.clearProperty(CHAPTER_AVAILABLE)
            if self.dialog is not None:
                self.close(notify=True)

    def clear_properties(self):
        self.window.clearProperty(CHAPTER_AVAILABLE)
        self.window.clearProperty(CHAPTER_OPEN)
