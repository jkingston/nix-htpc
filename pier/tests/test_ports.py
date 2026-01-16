"""Tests for the PC ports module."""

from pathlib import Path

from pier.ports.registry import (
    PORTS,
    Enhancement,
    Port,
    PortType,
    get_port,
    list_ports,
)


class TestPortType:
    """Tests for PortType enum."""

    def test_port_types_defined(self) -> None:
        """All expected port types are defined."""
        assert PortType.HARBOUR_MASTERS.value == "harbour_masters"
        assert PortType.OPENGOAL.value == "opengoal"
        assert PortType.DIRECT_PORT.value == "direct_port"


class TestPortDataclass:
    """Tests for Port dataclass."""

    def test_port_is_frozen(self) -> None:
        """Port is immutable (frozen)."""
        port = Port(
            id="test",
            name="Test Port",
            type=PortType.HARBOUR_MASTERS,
            github_repo="test/repo",
            system="n64",
            rom_search_name="Test Game",
            required_hashes=frozenset({"abc123"}),
            linux_asset_pattern="*Linux*.zip",
            executable_name="test.elf",
        )
        # Port should be hashable (frozen)
        hash(port)

    def test_port_optional_fields(self) -> None:
        """Optional fields have correct defaults."""
        port = Port(
            id="test",
            name="Test Port",
            type=PortType.HARBOUR_MASTERS,
            github_repo="test/repo",
            system="n64",
            rom_search_name="Test Game",
            required_hashes=frozenset(),
            linux_asset_pattern="*Linux*.zip",
            executable_name="test.elf",
        )
        assert port.launch_args == ""
        assert port.steamgriddb_name is None
        assert port.texture_pack_repo is None
        assert port.enhancements == ()

    def test_port_with_enhancements(self) -> None:
        """Port can have enhancements defined."""
        enh = Enhancement(
            id="hd-textures",
            name="HD Texture Pack",
            repo="test/textures",
            asset_pattern="*.o2r",
        )
        port = Port(
            id="test",
            name="Test Port",
            type=PortType.HARBOUR_MASTERS,
            github_repo="test/repo",
            system="n64",
            rom_search_name="Test Game",
            required_hashes=frozenset(),
            linux_asset_pattern="*Linux*.zip",
            executable_name="test.elf",
            enhancements=(enh,),
        )
        assert len(port.enhancements) == 1
        assert port.enhancements[0].id == "hd-textures"


class TestEnhancementDataclass:
    """Tests for Enhancement dataclass."""

    def test_enhancement_is_frozen(self) -> None:
        """Enhancement is immutable (frozen)."""
        enh = Enhancement(
            id="hd-textures",
            name="HD Texture Pack",
            repo="test/textures",
            asset_pattern="*.o2r",
        )
        # Enhancement should be hashable (frozen)
        hash(enh)

    def test_enhancement_default_subdir(self) -> None:
        """Enhancement has default install_subdir."""
        enh = Enhancement(
            id="hd-textures",
            name="HD Texture Pack",
            repo="test/textures",
            asset_pattern="*.o2r",
        )
        assert enh.install_subdir == "mods"

    def test_enhancement_custom_subdir(self) -> None:
        """Enhancement can have custom install_subdir."""
        enh = Enhancement(
            id="hd-textures",
            name="HD Texture Pack",
            repo="test/textures",
            asset_pattern="*.zip",
            install_subdir="texture_replacements",
        )
        assert enh.install_subdir == "texture_replacements"


class TestPortsRegistry:
    """Tests for the ports registry."""

    def test_ports_dict_exists(self) -> None:
        """PORTS dictionary exists and has entries."""
        assert isinstance(PORTS, dict)
        assert len(PORTS) > 0

    def test_expected_ports_defined(self) -> None:
        """Expected ports are defined in registry."""
        expected = [
            "soh",
            "2ship",
            "spaghettikart",
            "starship",
            "opengoal-jak1",
            "opengoal-jak2",
            "perfect-dark",
        ]
        for port_id in expected:
            assert port_id in PORTS, f"Expected port '{port_id}' not found"

    def test_soh_port_details(self) -> None:
        """Ship of Harkinian port has correct details."""
        soh = PORTS["soh"]
        assert soh.name == "Ship of Harkinian"
        assert soh.type == PortType.HARBOUR_MASTERS
        assert soh.github_repo == "HarbourMasters/Shipwright"
        assert soh.system == "n64"
        assert len(soh.required_hashes) > 0  # Has actual hashes
        assert soh.texture_pack_repo == "GhostlyDark/OoT-Reloaded-SoH"

    def test_spaghettikart_port_details(self) -> None:
        """SpaghettiKart port has correct details."""
        port = PORTS["spaghettikart"]
        assert port.name == "SpaghettiKart"
        assert port.type == PortType.HARBOUR_MASTERS
        assert port.github_repo == "HarbourMasters/SpaghettiKart"
        assert port.system == "n64"
        # Has the specific known hash
        assert "579c48e211ae952530ffc8738709f078d5dd215e" in port.required_hashes
        assert port.texture_pack_repo == "GhostlyDark/MK64-Reloaded-SK"

    def test_opengoal_ports(self) -> None:
        """OpenGOAL ports have correct type and launch args."""
        jak1 = PORTS["opengoal-jak1"]
        jak2 = PORTS["opengoal-jak2"]

        assert jak1.type == PortType.OPENGOAL
        assert jak2.type == PortType.OPENGOAL

        assert jak1.launch_args == "-g jak1"
        assert jak2.launch_args == "-g jak2"

        assert jak1.system == "ps2"
        assert jak2.system == "ps2"

    def test_perfect_dark_port(self) -> None:
        """Perfect Dark port has correct details."""
        port = PORTS["perfect-dark"]
        assert port.type == PortType.DIRECT_PORT
        assert port.system == "n64"

    def test_all_ports_have_required_fields(self) -> None:
        """All ports have required fields populated."""
        for port_id, port in PORTS.items():
            assert port.id == port_id, f"Port ID mismatch for {port_id}"
            assert port.name, f"Port {port_id} missing name"
            assert port.github_repo, f"Port {port_id} missing github_repo"
            assert port.system, f"Port {port_id} missing system"
            assert port.linux_asset_pattern, f"Port {port_id} missing linux_asset_pattern"
            assert port.executable_name, f"Port {port_id} missing executable_name"


class TestGetPort:
    """Tests for get_port function."""

    def test_get_existing_port(self) -> None:
        """Get existing port returns Port object."""
        port = get_port("soh")
        assert port is not None
        assert port.id == "soh"

    def test_get_nonexistent_port(self) -> None:
        """Get nonexistent port returns None."""
        port = get_port("nonexistent")
        assert port is None


class TestListPorts:
    """Tests for list_ports function."""

    def test_list_ports_returns_all(self) -> None:
        """List ports returns all ports."""
        ports = list_ports()
        assert len(ports) == len(PORTS)

    def test_list_ports_returns_port_objects(self) -> None:
        """List ports returns Port objects."""
        ports = list_ports()
        for port in ports:
            assert isinstance(port, Port)


class TestPortEnhancements:
    """Tests for port enhancements in registry."""

    def test_soh_has_enhancements(self) -> None:
        """Ship of Harkinian has HD texture enhancement."""
        soh = PORTS["soh"]
        assert len(soh.enhancements) > 0
        enh = soh.enhancements[0]
        assert enh.id == "oot-reloaded"
        assert "GhostlyDark" in enh.repo
        assert enh.asset_pattern == "*.o2r"

    def test_2ship_has_enhancements(self) -> None:
        """2Ship2Harkinian has HD texture enhancement."""
        port = PORTS["2ship"]
        assert len(port.enhancements) > 0
        enh = port.enhancements[0]
        assert enh.id == "mm-reloaded"

    def test_starship_no_enhancements(self) -> None:
        """Starship has no enhancements yet."""
        port = PORTS["starship"]
        assert len(port.enhancements) == 0

    def test_perfect_dark_no_enhancements(self) -> None:
        """Perfect Dark has no enhancements yet."""
        port = PORTS["perfect-dark"]
        assert len(port.enhancements) == 0


class TestPortScanner:
    """Tests for port scanning functionality."""

    def test_scan_installed_ports_empty(self, tmp_path: Path) -> None:
        """Scan empty ports directory returns empty list."""
        from pier.ports.scanner import scan_installed_ports

        result = scan_installed_ports(tmp_path)
        assert result == []

    def test_scan_installed_ports_nonexistent(self, tmp_path: Path) -> None:
        """Scan nonexistent directory returns empty list."""
        from pier.ports.scanner import scan_installed_ports

        result = scan_installed_ports(tmp_path / "nonexistent")
        assert result == []

    def test_get_installed_port_not_found(self, tmp_path: Path) -> None:
        """Get installed port returns None when not found."""
        from pier.ports.scanner import get_installed_port

        result = get_installed_port("soh", tmp_path)
        assert result is None

    def test_get_installed_port_unknown_port(self, tmp_path: Path) -> None:
        """Get installed port returns None for unknown port."""
        from pier.ports.scanner import get_installed_port

        result = get_installed_port("unknown-port-id", tmp_path)
        assert result is None

    def test_get_installed_version_missing(self, tmp_path: Path) -> None:
        """Get installed version returns None when no version file."""
        from pier.ports.scanner import get_installed_version

        result = get_installed_version(tmp_path)
        assert result is None

    def test_get_installed_version_from_pier_version(self, tmp_path: Path) -> None:
        """Get installed version reads .pier-version file."""
        from pier.ports.scanner import get_installed_version

        (tmp_path / ".pier-version").write_text("v1.2.3")
        result = get_installed_version(tmp_path)
        assert result == "v1.2.3"

    def test_save_installed_version(self, tmp_path: Path) -> None:
        """Save installed version writes .pier-version file."""
        from pier.ports.scanner import get_installed_version, save_installed_version

        save_installed_version(tmp_path, "v2.0.0")
        result = get_installed_version(tmp_path)
        assert result == "v2.0.0"


class TestPortSteam:
    """Tests for port Steam shortcut creation."""

    def test_create_port_shortcut(self, tmp_path: Path) -> None:
        """Create port shortcut returns valid shortcut dict."""
        from pier.ports.steam import create_port_shortcut

        port = PORTS["soh"]

        # Create fake executable
        exec_path = tmp_path / port.executable_name
        exec_path.write_text("fake")

        shortcut = create_port_shortcut(port, tmp_path)

        assert shortcut["AppName"] == "Ship of Harkinian"
        assert shortcut["DevkitGameID"] == "port:soh"
        assert shortcut["tags"] == {}  # No tags added

    def test_create_port_shortcut_with_subdirectory(self, tmp_path: Path) -> None:
        """Create port shortcut finds executable in subdirectory."""
        from pier.ports.steam import create_port_shortcut

        port = PORTS["soh"]

        # Create fake executable in subdirectory
        subdir = tmp_path / "SoH-release"
        subdir.mkdir()
        exec_path = subdir / port.executable_name
        exec_path.write_text("fake")

        shortcut = create_port_shortcut(port, tmp_path)

        assert shortcut["AppName"] == "Ship of Harkinian"
        assert str(subdir) in shortcut["StartDir"]


class TestEnhancementFunctions:
    """Tests for enhancement helper functions."""

    def test_get_installed_enhancements_empty(self, tmp_path: Path) -> None:
        """Get installed enhancements returns empty when none installed."""
        from pier.ports.enhancements import get_installed_enhancements

        port = PORTS["soh"]
        result = get_installed_enhancements(port, tmp_path)
        assert result == []

    def test_get_installed_enhancements_found(self, tmp_path: Path) -> None:
        """Get installed enhancements detects installed enhancement."""
        from pier.ports.enhancements import get_installed_enhancements

        port = PORTS["soh"]

        # Create fake enhancement file
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        (mods_dir / "textures.o2r").write_text("fake")

        result = get_installed_enhancements(port, tmp_path)
        assert "oot-reloaded" in result
