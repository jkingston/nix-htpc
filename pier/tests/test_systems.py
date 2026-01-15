"""Tests for ROM systems registry."""

from pier.roms.systems import SYSTEMS, get_system, get_system_for_extension


class TestSystems:
    """Tests for system definitions."""

    def test_systems_defined(self):
        """Key systems should be defined."""
        assert "n64" in SYSTEMS
        assert "snes" in SYSTEMS
        assert "ps2" in SYSTEMS
        assert "gc" in SYSTEMS

    def test_system_has_required_fields(self):
        """Each system should have required fields."""
        for sys_id, system in SYSTEMS.items():
            assert system.id == sys_id
            assert system.name
            assert system.extensions
            assert system.myrient_path
            assert system.emulator

    def test_get_system(self):
        """get_system should return system by ID."""
        n64 = get_system("n64")
        assert n64 is not None
        assert n64.name == "Nintendo 64"

        assert get_system("nonexistent") is None

    def test_get_system_for_extension(self):
        """get_system_for_extension should return matching systems."""
        z64_systems = get_system_for_extension(".z64")
        assert len(z64_systems) == 1
        assert z64_systems[0].id == "n64"

        # .iso is used by multiple systems
        iso_systems = get_system_for_extension(".iso")
        assert len(iso_systems) >= 3  # ps1, ps2, gc, wii, psp

    def test_extensions_are_lowercase(self):
        """All extensions should be lowercase."""
        for system in SYSTEMS.values():
            for ext in system.extensions:
                assert ext == ext.lower()
                assert ext.startswith(".")
