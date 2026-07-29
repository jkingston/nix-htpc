import importlib.util
import pathlib
import sys
import threading
import types
import unittest
from collections import OrderedDict


def load_module():
    package = types.ModuleType("jellyfin_kodi")
    package.__path__ = []
    helper = types.ModuleType("jellyfin_kodi.helper")
    helper.LazyLogger = lambda _name: types.SimpleNamespace(
        debug=lambda *_args: None,
        warning=lambda *_args: None,
    )
    helper.window = lambda *_args, **_kwargs: None
    utils = types.ModuleType("jellyfin_kodi.helper.utils")
    utils.translate_path = lambda path: path
    xbmc = types.ModuleType("xbmc")
    xbmc.getCondVisibility = lambda _condition: False
    xbmc.getInfoLabel = lambda _label: ""
    requests = types.ModuleType("requests")
    requests.get = lambda *_args, **_kwargs: None
    requests.Session = lambda: types.SimpleNamespace(headers={})

    sys.modules["jellyfin_kodi"] = package
    sys.modules["jellyfin_kodi.helper"] = helper
    sys.modules["jellyfin_kodi.helper.utils"] = utils
    sys.modules["xbmc"] = xbmc
    sys.modules["requests"] = requests

    path = pathlib.Path(__file__).with_name("trickplay.py")
    spec = importlib.util.spec_from_file_location("jellyfin_kodi.trickplay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


trickplay = load_module()

INFO = {
    "Interval": 10000,
    "ThumbnailCount": 140,
    "TileWidth": 10,
    "TileHeight": 10,
    "Width": 320,
    "Height": 240,
}


class TrickplayTest(unittest.TestCase):
    def test_time_labels(self):
        self.assertEqual(trickplay.parse_time_label("01:02:03"), 3723)
        self.assertEqual(trickplay.parse_time_label("12:34"), 754)
        self.assertIsNone(trickplay.parse_time_label(""))
        self.assertIsNone(trickplay.parse_time_label("bad"))
        self.assertEqual(trickplay.format_time(3723), "1:02:03")
        self.assertEqual(trickplay.format_time(754), "12:34")

    def test_tile_coordinates(self):
        self.assertEqual(
            trickplay.tile_for_time(110, INFO),
            (11, 0, (320, 240, 640, 480)),
        )
        self.assertEqual(
            trickplay.tile_for_time(1010, INFO),
            (101, 1, (320, 0, 640, 240)),
        )

    def test_tile_coordinates_clamp_to_last_frame(self):
        self.assertEqual(
            trickplay.tile_for_time(99999, INFO),
            (139, 1, (2880, 720, 3200, 960)),
        )

    def test_frame_coordinates_and_directional_sprite_prefetch(self):
        self.assertEqual(
            trickplay.tile_for_frame(101, INFO),
            (101, 1, (320, 0, 640, 240)),
        )
        self.assertEqual(trickplay.adjacent_sprites(10, INFO, 1), (1,))
        self.assertEqual(trickplay.adjacent_sprites(110, INFO, -1), (0,))

    def test_selects_nearest_resolution(self):
        metadata = {
            "source": {
                "160": dict(INFO, Width=160),
                "320": INFO,
                "640": dict(INFO, Width=640),
            }
        }
        width, selected = trickplay.select_trickplay(metadata, "source")
        self.assertEqual(width, 320)
        self.assertIs(selected, INFO)

    def test_rejects_incomplete_metadata(self):
        self.assertEqual(
            trickplay.select_trickplay({"source": {"320": {}}}, "source"),
            (None, None),
        )
        self.assertEqual(trickplay.select_trickplay({}, "source"), (None, None))

    def test_selects_current_chapter(self):
        chapters = [
            {"Name": "One", "StartPositionTicks": 0},
            {"Name": "Two", "StartPositionTicks": 600000000},
        ]
        self.assertEqual(trickplay.chapter_for_time(chapters, 59)[0], 0)
        self.assertEqual(trickplay.chapter_for_time(chapters, 60)[0], 1)

    def test_resolved_preview_is_tagged_with_exact_controller_target(self):
        events = []
        properties = {
            trickplay.SEEK_ACTIVE: "true",
            trickplay.SEEK_GENERATION: "7",
            trickplay.SEEK_TARGET: "60",
        }
        manager = trickplay.TrickplayPreviewManager(None)
        manager._chapter_frame = lambda _state, index, _abort: (
            events.append(("image", index)) or "/tmp/chapter.jpg"
        )
        original_window = trickplay.window

        def fake_window(key, value=None, clear=False):
            events.append(("window", key, value, clear))
            if clear:
                properties.pop(key, None)
            elif value is not None:
                properties[key] = value
            return properties.get(key, "")

        trickplay.window = fake_window
        try:
            manager._ensure_preview(
                {
                    "chapters": [
                        {"Name": "Chapter two", "StartPositionTicks": 600000000}
                    ],
                    "last_seconds": 0,
                    "info": None,
                    "preview_pending": False,
                },
                60,
                "7",
                threading.Event(),
            )
        finally:
            trickplay.window = original_window

        chapter_event = (
            "window",
            trickplay.PREVIEW_CHAPTER,
            "Chapter two",
            False,
        )
        target_event = (
            "window",
            trickplay.PREVIEW_TARGET,
            "60",
            False,
        )
        self.assertIn(("window", trickplay.PREVIEW_PATH, "/tmp/chapter.jpg", False), events)
        self.assertIn(chapter_event, events)
        self.assertIn(target_event, events)
        self.assertGreater(events.index(target_event), events.index(("image", 0)))

    def test_stale_cold_preview_is_discarded(self):
        events = []
        properties = {
            trickplay.SEEK_ACTIVE: "true",
            trickplay.SEEK_GENERATION: "8",
            trickplay.SEEK_TARGET: "60",
        }
        manager = trickplay.TrickplayPreviewManager(None)

        def stale_chapter(_state, _index, _abort):
            properties[trickplay.SEEK_TARGET] = "70"
            return "/tmp/stale.jpg"

        manager._chapter_frame = stale_chapter
        original_window = trickplay.window

        def fake_window(key, value=None, clear=False):
            if value is not None or clear:
                events.append((key, value, clear))
            return properties.get(key, "")

        trickplay.window = fake_window
        try:
            manager._ensure_preview(
                {
                    "chapters": [
                        {"Name": "Chapter", "StartPositionTicks": 0}
                    ],
                    "last_seconds": 0,
                    "info": None,
                    "preview_pending": False,
                },
                60,
                "8",
                threading.Event(),
            )
        finally:
            trickplay.window = original_window

        self.assertNotIn(
            (trickplay.PREVIEW_PATH, "/tmp/stale.jpg", False),
            events,
        )
        self.assertFalse(
            any(event[0] == trickplay.PREVIEW_TARGET for event in events)
        )

    def test_missing_metadata_publishes_no_empty_image_card(self):
        events = []
        properties = {
            trickplay.SEEK_ACTIVE: "true",
            trickplay.SEEK_GENERATION: "9",
            trickplay.SEEK_TARGET: "90",
        }
        manager = trickplay.TrickplayPreviewManager(None)
        original_window = trickplay.window

        def fake_window(key, value=None, clear=False):
            if value is not None or clear:
                events.append((key, value, clear))
            return properties.get(key, "")

        trickplay.window = fake_window
        try:
            manager._ensure_preview(
                {
                    "chapters": [],
                    "last_seconds": None,
                    "info": None,
                    "preview_pending": False,
                },
                90,
                "9",
                threading.Event(),
            )
        finally:
            trickplay.window = original_window

        self.assertIn((trickplay.PREVIEW_PATH, None, True), events)
        self.assertIn((trickplay.PREVIEW_TARGET, "90", False), events)

    def test_sprite_cache_is_byte_bounded_lru(self):
        state = {
            "sprites": OrderedDict(),
            "sprite_bytes": 0,
            "failed_sprites": {1},
        }
        original_limit = trickplay.SPRITE_CACHE_BYTES
        trickplay.SPRITE_CACHE_BYTES = 5
        try:
            trickplay.TrickplayPreviewManager._remember_sprite_locked(
                state, 0, b"aaa"
            )
            trickplay.TrickplayPreviewManager._remember_sprite_locked(
                state, 1, b"bbb"
            )
        finally:
            trickplay.SPRITE_CACHE_BYTES = original_limit

        self.assertEqual(list(state["sprites"]), [1])
        self.assertEqual(state["sprite_bytes"], 3)
        self.assertNotIn(1, state["failed_sprites"])

    def test_neighbor_warming_prefers_seek_direction(self):
        frames = []
        manager = trickplay.TrickplayPreviewManager(None)
        manager._trickplay_frame_by_index = (
            lambda _state, frame, _abort: frames.append(frame)
        )
        manager._warm_neighbor_frames(
            {"info": INFO},
            50,
            1,
            threading.Event(),
        )
        self.assertEqual(frames, [51, 49, 52, 48, 53, 47])

    def test_persistent_session_carries_token_in_header(self):
        class Session(object):
            def __init__(self):
                self.headers = {}

        original_session = trickplay.requests.Session
        trickplay.requests.Session = Session
        try:
            client = types.SimpleNamespace(
                config=types.SimpleNamespace(
                    data={
                        "auth.token": "secret-token",
                    }
                )
            )
            session = trickplay.TrickplayPreviewManager._new_session(client)
        finally:
            trickplay.requests.Session = original_session

        self.assertEqual(session.headers["X-Emby-Token"], "secret-token")
        self.assertEqual(session.headers["Accept"], "image/jpeg")

    def test_download_does_not_put_token_in_url_or_params(self):
        calls = []

        class Response(object):
            content = b"image"

            @staticmethod
            def raise_for_status():
                return None

        original_get = trickplay.requests.get
        trickplay.requests.get = lambda url, **kwargs: (
            calls.append((url, kwargs)) or Response()
        )
        try:
            client = types.SimpleNamespace(
                config=types.SimpleNamespace(
                    data={
                        "auth.server": "https://media.example",
                        "auth.token": "secret-token",
                        "auth.ssl": True,
                    }
                )
            )
            result = trickplay.TrickplayPreviewManager._download(
                client,
                "Videos/item/Trickplay/320/0.jpg",
                {"MediaSourceId": "source"},
            )
        finally:
            trickplay.requests.get = original_get

        self.assertEqual(result, b"image")
        url, kwargs = calls[0]
        self.assertNotIn("secret-token", url)
        self.assertNotIn("secret-token", repr(kwargs["params"]))
        self.assertEqual(kwargs["headers"]["X-Emby-Token"], "secret-token")


if __name__ == "__main__":
    unittest.main()
