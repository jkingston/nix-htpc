"""Tests for Steam shortcut manager."""

from pier.steam.manager import (
    Shortcut,
    get_all_shortcuts,
    find_shortcut,
    parse_shortcut,
    get_shortcut_details,
)


class TestParseShortcut:
    """Tests for shortcut parsing."""

    def test_parse_shortcut_basic(self):
        """parse_shortcut should create Shortcut from dict."""
        entry = {
            "appid": 12345,
            "AppName": "Test Game",
            "Exe": '"/usr/bin/test"',
            "StartDir": '"/home/user"',
            "LaunchOptions": "--fullscreen",
            "tags": {"0": "pier", "1": "N64"},
        }

        shortcut = parse_shortcut("0", entry)

        assert shortcut.index == "0"
        assert shortcut.app_id == 12345
        assert shortcut.name == "Test Game"
        assert shortcut.exe == "/usr/bin/test"
        assert shortcut.start_dir == "/home/user"
        assert shortcut.launch_options == "--fullscreen"
        assert shortcut.tags == ["pier", "N64"]
        assert shortcut.is_pier is True

    def test_parse_shortcut_no_pier_tag(self):
        """parse_shortcut should detect non-pier shortcuts."""
        entry = {
            "appid": 12345,
            "AppName": "Manual Game",
            "tags": {"0": "custom"},
        }

        shortcut = parse_shortcut("1", entry)

        assert shortcut.is_pier is False
        assert shortcut.tags == ["custom"]


class TestGetAllShortcuts:
    """Tests for getting all shortcuts."""

    def test_get_all_shortcuts(self):
        """get_all_shortcuts should return all shortcuts."""
        data = {
            "shortcuts": {
                "0": {"appid": 1, "AppName": "Game A", "tags": {"0": "pier"}},
                "1": {"appid": 2, "AppName": "Game B", "tags": {"0": "other"}},
                "2": {"appid": 3, "AppName": "Game C", "tags": {"0": "pier"}},
            }
        }

        shortcuts = get_all_shortcuts(data)

        assert len(shortcuts) == 3
        assert shortcuts[0].name == "Game A"
        assert shortcuts[1].name == "Game B"
        assert shortcuts[2].name == "Game C"

    def test_get_all_shortcuts_sorted_by_index(self):
        """get_all_shortcuts should return shortcuts sorted by index."""
        data = {
            "shortcuts": {
                "2": {"appid": 3, "AppName": "Third"},
                "0": {"appid": 1, "AppName": "First"},
                "1": {"appid": 2, "AppName": "Second"},
            }
        }

        shortcuts = get_all_shortcuts(data)

        assert [s.index for s in shortcuts] == ["0", "1", "2"]
        assert [s.name for s in shortcuts] == ["First", "Second", "Third"]


class TestFindShortcut:
    """Tests for finding shortcuts."""

    def test_find_by_index(self):
        """find_shortcut should find by index."""
        data = {
            "shortcuts": {
                "0": {"appid": 1, "AppName": "Game A", "tags": {}},
                "1": {"appid": 2, "AppName": "Game B", "tags": {}},
            }
        }

        result = find_shortcut("1", data)

        assert result is not None
        assert result.name == "Game B"

    def test_find_by_exact_name(self):
        """find_shortcut should find by exact name."""
        data = {
            "shortcuts": {
                "0": {"appid": 1, "AppName": "Mario Kart", "tags": {}},
                "1": {"appid": 2, "AppName": "Super Mario", "tags": {}},
            }
        }

        result = find_shortcut("Mario Kart", data)

        assert result is not None
        assert result.name == "Mario Kart"

    def test_find_by_partial_name(self):
        """find_shortcut should find by partial name match."""
        data = {
            "shortcuts": {
                "0": {"appid": 1, "AppName": "Super Mario 64", "tags": {}},
            }
        }

        result = find_shortcut("mario", data)

        assert result is not None
        assert result.name == "Super Mario 64"

    def test_find_not_found(self):
        """find_shortcut should return None when not found."""
        data = {"shortcuts": {"0": {"appid": 1, "AppName": "Test", "tags": {}}}}

        result = find_shortcut("nonexistent", data)

        assert result is None


class TestShortcutDetails:
    """Tests for shortcut details."""

    def test_get_shortcut_details(self):
        """get_shortcut_details should return formatted info."""
        shortcut = Shortcut(
            index="0",
            app_id=12345,
            name="Test Game",
            exe="/usr/bin/test",
            start_dir="/home/user",
            launch_options="--opt",
            tags=["pier", "N64"],
            is_pier=True,
        )

        details = get_shortcut_details(shortcut)

        assert details["Name"] == "Test Game"
        assert details["Index"] == "0"
        assert details["Pier Managed"] == "Yes"
        assert details["Tags"] == "pier, N64"

    def test_display_tags_empty(self):
        """Shortcut should display '-' for empty tags."""
        shortcut = Shortcut(
            index="0",
            app_id=1,
            name="Test",
            exe="",
            start_dir="",
            launch_options="",
            tags=[],
            is_pier=False,
        )

        assert shortcut.display_tags == "-"
