# Pier Test Plan

Comprehensive automated test plan for the pier HTPC game management tool.

## Current Test Coverage

### Existing Tests (need updates)
- `test_bios.py` - BIOS registry, hash functions
- `test_config.py` - Config/Library save/load (outdated: still uses `steam_links`)
- `test_steam.py` - VDF parsing, appid generation, shortcut roundtrip
- `test_tui.py` - Import smoke tests, binding checks (outdated: expects `space` binding)
- `test_registry.py` - Port/system registry

### Known Issues in Existing Tests
1. `test_config.py` references `steam_links` - should use `hidden_from_steam`
2. `test_tui.py` expects `space` binding on SteamScreen - now uses `h` for hide/show

---

## Proposed Test Structure

```
tests/
├── conftest.py                 # Shared fixtures
├── unit/                       # Pure unit tests (no I/O, fast)
│   ├── test_bios.py           # BIOS registry, hash functions
│   ├── test_config.py         # Config/Library dataclasses
│   ├── test_constants.py      # Constants validation
│   ├── test_installer.py      # Installer type detection
│   ├── test_steam_vdf.py      # VDF parsing/writing
│   └── test_registry.py       # Port/system definitions
├── integration/                # Tests with mocked HTTP/filesystem
│   ├── test_bios_download.py  # BIOS download with mocked responses
│   ├── test_myrient.py        # ROM listing/download with mocks
│   ├── test_github.py         # Release fetching with mocks
│   ├── test_artwork.py        # SteamGridDB with mocks
│   └── test_steam_library.py  # Full Steam workflow
└── tui/                        # TUI tests using Textual's Pilot
    ├── test_screens.py        # Screen instantiation, bindings
    ├── test_steam_screen.py   # Steam sync UX flow
    └── test_install_wizard.py # Installer wizard flow
```

---

## Unit Tests

### 1. `test_installer.py` (NEW - HIGH PRIORITY)

Tests for `pier/core/installer.py` installer detection.

```python
class TestInstallerTypeDetection:
    """Tests for detect_installer_type function."""

    def test_detect_innosetup(self, tmp_path):
        """Should detect InnoSetup from 'Inno Setup' in header."""
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"\x00" * 100 + b"Inno Setup" + b"\x00" * 100)
        assert detect_installer_type(installer) == InstallerType.INNOSETUP

    def test_detect_nsis(self, tmp_path):
        """Should detect NSIS from 'Nullsoft' in header."""
        installer = tmp_path / "setup.exe"
        installer.write_bytes(b"\x00" * 100 + b"Nullsoft" + b"\x00" * 100)
        assert detect_installer_type(installer) == InstallerType.NSIS

    def test_detect_msi(self, tmp_path):
        """Should detect MSI from magic bytes."""
        installer = tmp_path / "setup.msi"
        installer.write_bytes(b"\xD0\xCF\x11\xE0" + b"\x00" * 100)
        assert detect_installer_type(installer) == InstallerType.MSI

    def test_detect_mojosetup(self, tmp_path):
        """Should detect MojoSetup from script content."""
        installer = tmp_path / "setup.sh"
        installer.write_text("#!/bin/bash\n# MojoSetup installer\n")
        assert detect_installer_type(installer) == InstallerType.MOJOSETUP

    def test_detect_makeself(self, tmp_path):
        """Should detect makeself from script content."""
        installer = tmp_path / "setup.sh"
        installer.write_text("#!/bin/bash\n# makeself archive\n")
        assert detect_installer_type(installer) == InstallerType.MAKESELF

    def test_detect_unsupported(self, tmp_path):
        """Should return UNSUPPORTED for unknown formats."""
        installer = tmp_path / "mystery.exe"
        installer.write_bytes(b"UNKNOWN FORMAT")
        assert detect_installer_type(installer) == InstallerType.UNSUPPORTED

    def test_detect_nonexistent_file(self, tmp_path):
        """Should return UNSUPPORTED for missing files."""
        assert detect_installer_type(tmp_path / "missing.exe") == InstallerType.UNSUPPORTED


class TestFindExecutables:
    """Tests for find_executables function."""

    def test_find_windows_exe(self, tmp_path):
        """Should find .exe files."""
        (tmp_path / "game.exe").touch()
        (tmp_path / "uninstall.exe").touch()
        exes = find_executables(tmp_path)
        assert len(exes) == 2
        # game.exe should be sorted before uninstall.exe
        assert exes[0].name == "game.exe"

    def test_find_linux_executable(self, tmp_path):
        """Should find files with executable bit."""
        game = tmp_path / "game.x86_64"
        game.touch()
        game.chmod(0o755)
        exes = find_executables(tmp_path)
        assert len(exes) == 1

    def test_prioritize_game_names(self, tmp_path):
        """Should prioritize files with 'game', 'start', 'launch' in name."""
        (tmp_path / "config.exe").touch()
        (tmp_path / "launch_game.exe").touch()
        exes = find_executables(tmp_path)
        assert exes[0].name == "launch_game.exe"

    def test_deprioritize_uninstaller(self, tmp_path):
        """Should deprioritize uninstaller/setup files."""
        (tmp_path / "unins000.exe").touch()
        (tmp_path / "game.exe").touch()
        exes = find_executables(tmp_path)
        assert exes[0].name == "game.exe"


class TestInstallerDescription:
    """Tests for get_installer_description function."""

    def test_all_types_have_descriptions(self):
        """All InstallerType values should have descriptions."""
        for itype in InstallerType:
            desc = get_installer_description(itype)
            assert desc is not None
            assert len(desc) > 0
```

### 2. `test_config.py` (UPDATE - HIGH PRIORITY)

Update for new `hidden_from_steam` and `custom_games` fields.

```python
class TestLibraryHiddenFromSteam:
    """Tests for hidden_from_steam functionality."""

    def test_default_not_hidden(self):
        """Games should not be hidden by default."""
        lib = Library()
        assert lib.is_hidden_from_steam("any_game") is False

    def test_hide_game(self):
        """Should be able to hide a game."""
        lib = Library()
        lib.set_hidden_from_steam("my_game", True)
        assert lib.is_hidden_from_steam("my_game") is True

    def test_unhide_game(self):
        """Should be able to unhide a game."""
        lib = Library()
        lib.set_hidden_from_steam("my_game", True)
        lib.set_hidden_from_steam("my_game", False)
        assert lib.is_hidden_from_steam("my_game") is False

    def test_backwards_compat_is_linked(self):
        """is_linked_to_steam should be inverse of is_hidden_from_steam."""
        lib = Library()
        lib.set_hidden_from_steam("game", True)
        assert lib.is_linked_to_steam("game") is False
        lib.set_hidden_from_steam("game", False)
        assert lib.is_linked_to_steam("game") is True

    def test_migrate_old_steam_links(self, tmp_path):
        """Should migrate old steam_links format on load."""
        pier_dir = tmp_path / ".pier"
        pier_dir.mkdir()
        (pier_dir / "library.json").write_text(json.dumps({
            "installed_ports": {},
            "downloaded_roms": {},
            "steam_links": {"game1": True, "game2": False}
        }))
        lib = Library.load(pier_dir)
        assert lib.is_hidden_from_steam("game1") is False  # was linked
        assert lib.is_hidden_from_steam("game2") is True   # was not linked


class TestLibraryCustomGames:
    """Tests for custom_games functionality."""

    def test_add_custom_game(self):
        """Should be able to add a custom game."""
        lib = Library()
        game = CustomGame(
            name="My Game",
            executable="/path/to/game.exe",
            start_dir="/path/to",
            launch_args="--fullscreen",
            use_steam_run=True,
        )
        lib.add_custom_game("custom:my_game", game)
        assert "custom:my_game" in lib.custom_games

    def test_get_custom_game(self):
        """Should be able to retrieve a custom game."""
        lib = Library()
        game = CustomGame(
            name="My Game",
            executable="/path/to/game.exe",
            start_dir="/path/to",
        )
        lib.add_custom_game("custom:my_game", game)
        retrieved = lib.get_custom_game("custom:my_game")
        assert retrieved.name == "My Game"
        assert retrieved.executable == "/path/to/game.exe"

    def test_remove_custom_game(self):
        """Should be able to remove a custom game."""
        lib = Library()
        game = CustomGame(name="Test", executable="/test", start_dir="/")
        lib.add_custom_game("custom:test", game)
        lib.remove_custom_game("custom:test")
        assert lib.get_custom_game("custom:test") is None

    def test_custom_games_roundtrip(self, tmp_path):
        """Custom games should persist through save/load."""
        pier_dir = tmp_path / ".pier"
        lib = Library()
        game = CustomGame(
            name="Roundtrip Game",
            executable="/game.exe",
            start_dir="/",
            use_steam_run=True,
        )
        lib.add_custom_game("custom:roundtrip", game)
        lib.save(pier_dir)

        loaded = Library.load(pier_dir)
        retrieved = loaded.get_custom_game("custom:roundtrip")
        assert retrieved.name == "Roundtrip Game"
        assert retrieved.use_steam_run is True
```

### 3. `test_bios.py` (UPDATE - MEDIUM PRIORITY)

Add tests for URL encoding and failure reporting.

```python
class TestBiosManagerURLEncoding:
    """Tests for BIOS download URL construction."""

    def test_url_encodes_spaces(self):
        """Should URL-encode spaces in github_path."""
        from urllib.parse import quote
        bios = BiosFile(
            filename="test.bin",
            system="ps1",
            md5="0" * 32,
            description="Test",
            priority=1,
            github_path="Sony - PlayStation/test.bin",
        )
        # The URL should encode the space
        expected_path = quote("Sony - PlayStation/test.bin", safe="/")
        assert "%20" in expected_path or " " not in expected_path


class TestBiosManagerFailureReporting:
    """Tests for download failure reporting."""

    @pytest.mark.asyncio
    async def test_download_recommended_returns_failures(self, mocker):
        """download_recommended should return failed downloads."""
        # Mock HTTP to fail for one file
        manager = BiosManager()
        # ... mock setup ...
        paths, failed = await manager.download_recommended()
        assert isinstance(failed, list)
        # Each failure is (filename, error_message)
        for filename, error in failed:
            assert isinstance(filename, str)
            assert isinstance(error, str)
```

---

## Integration Tests

### 4. `test_bios_download.py` (NEW - MEDIUM PRIORITY)

Integration tests with mocked HTTP responses.

```python
@pytest.fixture
def mock_bios_server(httpx_mock):
    """Mock the GitHub raw content server."""
    # Return valid BIOS content for known files
    def handler(request):
        if "scph5501.bin" in str(request.url):
            # Return content that matches expected MD5
            return httpx.Response(200, content=MOCK_BIOS_CONTENT)
        return httpx.Response(404)
    httpx_mock.add_callback(handler)


class TestBiosDownloadIntegration:
    """Integration tests for BIOS downloads."""

    @pytest.mark.asyncio
    async def test_download_creates_file(self, tmp_path, mock_bios_server):
        """Download should create file in bios directory."""
        config = Config(emulation_dir=tmp_path)
        manager = BiosManager(config)
        # ... test download creates file ...

    @pytest.mark.asyncio
    async def test_download_verifies_hash(self, tmp_path, mock_bios_server):
        """Download should verify MD5 hash."""
        # ... test hash verification ...

    @pytest.mark.asyncio
    async def test_download_reports_http_errors(self, tmp_path, httpx_mock):
        """Should report HTTP errors in failures list."""
        httpx_mock.add_response(status_code=404)
        # ... test 404 handling ...
```

### 5. `test_steam_library.py` (NEW - HIGH PRIORITY)

Full Steam sync workflow tests.

```python
@pytest.fixture
def mock_steam_dir(tmp_path):
    """Create a mock Steam userdata directory."""
    userdata = tmp_path / "userdata" / "12345678" / "config"
    userdata.mkdir(parents=True)
    return userdata


class TestSteamLibraryWorkflow:
    """Integration tests for Steam library management."""

    def test_add_shortcut(self, mock_steam_dir):
        """Should add shortcut to shortcuts.vdf."""
        # ... test adding shortcut ...

    def test_remove_shortcut(self, mock_steam_dir):
        """Should remove shortcut from shortcuts.vdf."""
        # ... test removing shortcut ...

    def test_list_pier_shortcuts(self, mock_steam_dir):
        """Should list only shortcuts with pier tag."""
        # ... test filtering by tag ...

    def test_shortcut_preserves_existing(self, mock_steam_dir):
        """Adding shortcut should preserve existing shortcuts."""
        # ... test non-destructive add ...
```

---

## TUI Tests

### 6. `test_steam_screen.py` (NEW - MEDIUM PRIORITY)

Using Textual's Pilot for async TUI testing.

```python
from textual.pilot import Pilot


class TestSteamScreenActions:
    """Tests for Steam screen user interactions."""

    @pytest.mark.asyncio
    async def test_hide_toggle(self):
        """Pressing 'h' should toggle hide status."""
        async with App().run_test() as pilot:
            # Navigate to steam screen
            # Select a game
            # Press 'h'
            # Verify status changed

    @pytest.mark.asyncio
    async def test_sync_button(self):
        """Sync button should add 'will add' games to Steam."""
        # ... test sync workflow ...

    @pytest.mark.asyncio
    async def test_add_custom_dialog(self):
        """'a' should open Add Custom dialog."""
        # ... test dialog opens and submits ...
```

### 7. `test_install_wizard.py` (NEW - LOW PRIORITY)

```python
class TestInstallWizardScreen:
    """Tests for install wizard TUI."""

    @pytest.mark.asyncio
    async def test_installer_detection_display(self):
        """Should show detected installer type."""
        # ... test status display updates ...

    @pytest.mark.asyncio
    async def test_unsupported_installer_message(self):
        """Should show error for unsupported installers."""
        # ... test error display ...
```

---

## Test Fixtures

### `conftest.py` additions

```python
import pytest
from pathlib import Path
from pier.core.config import Config, Library, CustomGame


@pytest.fixture
def temp_emulation_dir(tmp_path):
    """Create a complete mock emulation directory structure."""
    emu = tmp_path / "Emulation"
    (emu / "roms" / "n64").mkdir(parents=True)
    (emu / "roms" / "ps1").mkdir(parents=True)
    (emu / "ports").mkdir(parents=True)
    (emu / "bios").mkdir(parents=True)
    (emu / ".pier").mkdir(parents=True)
    return emu


@pytest.fixture
def mock_config(temp_emulation_dir):
    """Config pointing to temp directories."""
    return Config(
        emulation_dir=temp_emulation_dir,
        roms_dir=temp_emulation_dir / "roms",
        ports_dir=temp_emulation_dir / "ports",
        pier_dir=temp_emulation_dir / ".pier",
    )


@pytest.fixture
def populated_library():
    """Library with sample data."""
    lib = Library()
    lib.add_port("soh", "1.0.0", executable="/path/to/soh")
    lib.add_rom("n64", "Mario64.z64")
    lib.add_custom_game("custom:test", CustomGame(
        name="Test Game",
        executable="/test.exe",
        start_dir="/",
    ))
    return lib
```

---

## Priority Order

### Phase 1: Fix Broken Tests (immediate)
1. Update `test_config.py` to use `hidden_from_steam` instead of `steam_links`
2. Update `test_tui.py` to expect correct bindings (`h` not `space`)

### Phase 2: Critical New Tests (high priority)
3. Add `test_installer.py` for installer detection
4. Add Steam library integration tests

### Phase 3: Integration Tests (medium priority)
5. Add BIOS download integration tests with mocked HTTP
6. Add myrient/GitHub integration tests

### Phase 4: TUI Tests (lower priority)
7. Add Textual Pilot-based TUI tests

---

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=pier --cov-report=html

# Run specific test file
uv run pytest tests/unit/test_installer.py

# Run tests matching pattern
uv run pytest -k "installer"

# Run with verbose output
uv run pytest -v
```

---

## CI Integration

Add to `.github/workflows/test.yml`:

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pytest --cov=pier
      - run: uv run ruff check
      - run: uv run pyright
```
