import unittest

from resources.lib.cache_policy import SESSION_PROPERTY, WidgetCachePolicy


class FakeWindow(object):
    def __init__(self, properties=None):
        self.properties = dict(properties or {})

    def getProperty(self, name):
        return self.properties.get(name, "")

    def setProperty(self, name, value):
        self.properties[name] = value


class WidgetCachePolicyTest(unittest.TestCase):
    def test_normal_widgets_follow_media_generation(self):
        window = FakeWindow({"widgetreload-movies": "movies-7"})
        policy = WidgetCachePolicy(window)

        self.assertEqual(
            policy.checksum({"action": "recent", "mediatype": "movies"}),
            "movies-7",
        )

    def test_explicit_reload_takes_precedence(self):
        window = FakeWindow({"widgetreload": "old"})
        policy = WidgetCachePolicy(window)

        self.assertEqual(
            policy.checksum(
                {"action": "recent", "mediatype": "media", "reload": "new"}
            ),
            "new",
        )

    def test_spotlight_is_stable_for_one_kodi_session(self):
        tokens = iter(("session-a", "session-b"))
        window = FakeWindow()
        policy = WidgetCachePolicy(window, token_factory=lambda: next(tokens))
        options = {"action": "spotlight", "mediatype": "media"}

        self.assertEqual(policy.checksum(options), "session-a")
        self.assertEqual(policy.checksum(options), "session-a")
        self.assertEqual(window.properties[SESSION_PROPERTY], "session-a")

    def test_runtime_playlists_are_stable_for_one_kodi_session(self):
        window = FakeWindow()
        policy = WidgetCachePolicy(window, token_factory=lambda: "session-a")

        self.assertEqual(
            policy.checksum(
                {"action": "serverplaylists", "mediatype": "media"}
            ),
            "session-a",
        )

    def test_only_explicit_and_listing_requests_bypass_cache(self):
        policy = WidgetCachePolicy(FakeWindow())

        self.assertTrue(policy.should_read({"action": "recent"}))
        self.assertFalse(policy.should_read({"action": "movieslisting"}))
        self.assertFalse(
            policy.should_read({"action": "recent", "skipcache": "true"})
        )


if __name__ == "__main__":
    unittest.main()
