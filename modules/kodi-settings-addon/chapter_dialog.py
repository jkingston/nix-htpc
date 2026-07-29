from __future__ import absolute_import, division, print_function

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
                    {
                        "thumb": chapter["image_path"],
                        "icon": chapter["image_path"],
                    }
                )
            item.setProperty("chapter.index", str(chapter["index"]))
            item.setProperty(
                "chapter.startseconds",
                str(chapter["start_seconds"]),
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
        self.close_without_event()

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT):
            control = self.getControl(CHAPTER_LIST_ID)
            current = control.getSelectedPosition()
            delta = -1 if action_id == ACTION_MOVE_LEFT else 1
            selected = max(0, min(len(self.chapters) - 1, current + delta))
            try:
                control.selectItem(selected)
            except AttributeError:
                pass
            if self.focus_callback and selected != current:
                self.focus_callback(dict(self.chapters[selected]))
        elif action_id == ACTION_MOVE_UP:
            self._exit("top")
        elif action_id == ACTION_MOVE_DOWN:
            self._exit("timeline")
        elif action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self._exit("back")

    def _exit(self, destination):
        if self._closing:
            return
        self._closing = True
        if self.exit_callback:
            self.exit_callback(destination)
        self.close()

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
        self.dialog = self.dialog_class(
            "ChapterRail.xml",
            self.addon_path,
            "Default",
            "1080i",
            chapters=chapters,
            current_seconds=current_seconds,
            select_callback=self._selected,
            focus_callback=self._focused,
            exit_callback=self._exit,
        )
        self.dialog.show()
        self.window.setProperty(CHAPTER_OPEN, "true")
        return True

    def _selected(self, chapter):
        chapter["playback_token"] = self.token
        self.event_sink("chapter-select", chapter)
        self.dialog = None
        self.token = None
        self.window.clearProperty(CHAPTER_OPEN)

    def _focused(self, chapter):
        chapter["playback_token"] = self.token
        self.event_sink("chapter-focus", chapter)

    def _exit(self, destination):
        self.event_sink(
            "chapter-exit",
            {
                "destination": destination,
                # Only a physical Back leaving the dialog should suppress the
                # rest of that repeat train on the newly exposed OSD.
                "arm_back": destination == "back",
            },
        )
        self.dialog = None
        self.token = None
        self.window.clearProperty(CHAPTER_OPEN)

    def close(self, notify=False, destination="back"):
        dialog = self.dialog
        self.dialog = None
        self.token = None
        self.window.clearProperty(CHAPTER_OPEN)
        if dialog is not None:
            dialog.close_without_event()
            if notify:
                # Contract loss is an involuntary exit from a pause-owned
                # chapter transaction. Route it through the same cancel path
                # as Back so playback cannot remain stranded while paused.
                self.event_sink(
                    "chapter-exit",
                    {
                        "destination": destination,
                        "arm_back": False,
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
