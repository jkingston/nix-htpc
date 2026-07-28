import importlib.util
import pathlib
import sys
import types
import unittest


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
