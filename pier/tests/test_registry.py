"""Tests for port and system registry."""


from pier.core.registry import (
    PORTS,
    SYSTEMS,
    AssetGenerator,
    get_port,
    get_system,
    list_ports,
    list_systems,
)


class TestSystemRegistry:
    """Tests for system registry."""

    def test_systems_not_empty(self):
        """SYSTEMS should contain entries."""
        assert len(SYSTEMS) > 0

    def test_all_systems_have_required_fields(self):
        """All systems should have required fields."""
        for system_id, system in SYSTEMS.items():
            assert system.id == system_id
            assert system.name
            assert system.myrient_path
            assert len(system.extensions) > 0
            assert system.libretro_name

    def test_get_system_found(self):
        """get_system should return matching system."""
        system = get_system("n64")
        assert system is not None
        assert system.name == "Nintendo 64"

    def test_get_system_not_found(self):
        """get_system should return None for unknown system."""
        system = get_system("commodore64")
        assert system is None

    def test_list_systems(self):
        """list_systems should return all systems."""
        systems = list_systems()
        assert len(systems) == len(SYSTEMS)
        assert all(s.id in SYSTEMS for s in systems)


class TestPortRegistry:
    """Tests for port registry."""

    def test_ports_not_empty(self):
        """PORTS should contain entries."""
        assert len(PORTS) > 0

    def test_all_ports_have_required_fields(self):
        """All ports should have required fields."""
        for port_id, port in PORTS.items():
            assert port.id == port_id
            assert port.name
            assert port.game
            assert port.repo
            assert port.asset_pattern
            assert port.executable
            assert port.rom is not None
            assert isinstance(port.asset_generator, AssetGenerator)

    def test_all_ports_have_valid_rom_requirements(self):
        """All ports should have valid ROM requirements."""
        for port in PORTS.values():
            rom = port.rom
            assert rom.name
            assert rom.filename
            assert rom.system in SYSTEMS
            assert rom.myrient_path
            assert rom.hash_type in ("sha1", "md5")
            # hash_value can be empty for ports with multiple valid dumps

    def test_get_port_found(self):
        """get_port should return matching port."""
        port = get_port("soh")
        assert port is not None
        assert port.name == "Ship of Harkinian"
        assert port.game == "Ocarina of Time"

    def test_get_port_not_found(self):
        """get_port should return None for unknown port."""
        port = get_port("halflife3")
        assert port is None

    def test_list_ports(self):
        """list_ports should return all ports."""
        ports = list_ports()
        assert len(ports) == len(PORTS)
        assert all(p.id in PORTS for p in ports)

    def test_port_mods(self):
        """Ports with mods should have valid mod definitions."""
        for port in PORTS.values():
            for mod in port.mods:
                assert mod.id
                assert mod.name
                assert mod.repo
                assert mod.asset_pattern
                assert mod.description
