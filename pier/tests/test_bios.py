"""Tests for BIOS management."""

import tempfile
from pathlib import Path

from pier.core.bios import (
    BIOS_REGISTRY,
    compute_md5,
    get_bios_by_filename,
    get_bios_by_system,
    get_recommended_bios,
    verify_md5,
)


class TestBiosRegistry:
    """Tests for BIOS registry functions."""

    def test_registry_not_empty(self):
        """BIOS registry should contain entries."""
        assert len(BIOS_REGISTRY) > 0

    def test_all_entries_have_required_fields(self):
        """All BIOS entries should have required fields."""
        for bios in BIOS_REGISTRY:
            assert bios.filename
            assert bios.system
            assert bios.md5
            assert len(bios.md5) == 32  # MD5 is 32 hex chars
            assert bios.description
            assert bios.priority in (1, 2)
            assert bios.github_path

    def test_get_bios_by_filename_found(self):
        """get_bios_by_filename should return matching BIOS."""
        bios = get_bios_by_filename("scph5501.bin")
        assert bios is not None
        assert bios.system == "ps1"

    def test_get_bios_by_filename_case_insensitive(self):
        """get_bios_by_filename should be case-insensitive."""
        bios_lower = get_bios_by_filename("scph5501.bin")
        bios_upper = get_bios_by_filename("SCPH5501.BIN")
        assert bios_lower == bios_upper

    def test_get_bios_by_filename_not_found(self):
        """get_bios_by_filename should return None for unknown files."""
        bios = get_bios_by_filename("nonexistent.bin")
        assert bios is None

    def test_get_bios_by_system(self):
        """get_bios_by_system should return BIOS for that system."""
        ps1_bios = get_bios_by_system("ps1")
        assert len(ps1_bios) > 0
        assert all(b.system == "ps1" for b in ps1_bios)

    def test_get_bios_by_system_empty(self):
        """get_bios_by_system should return empty for unknown system."""
        bios = get_bios_by_system("dreamcast")
        assert bios == []

    def test_get_recommended_bios(self):
        """get_recommended_bios should return priority 1 items."""
        recommended = get_recommended_bios()
        assert len(recommended) > 0
        assert all(b.priority == 1 for b in recommended)


class TestHashFunctions:
    """Tests for hash verification functions."""

    def test_compute_md5(self):
        """compute_md5 should compute correct hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)

        try:
            md5 = compute_md5(path)
            assert len(md5) == 32
            assert md5 == "9473fdd0d880a43c21b7778d34872157"  # MD5 of "test content"
        finally:
            path.unlink()

    def test_verify_md5_valid(self):
        """verify_md5 should return True for matching hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)

        try:
            assert verify_md5(path, "9473fdd0d880a43c21b7778d34872157")
        finally:
            path.unlink()

    def test_verify_md5_case_insensitive(self):
        """verify_md5 should be case-insensitive."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)

        try:
            assert verify_md5(path, "9473FDD0D880A43C21B7778D34872157")
        finally:
            path.unlink()

    def test_verify_md5_invalid(self):
        """verify_md5 should return False for non-matching hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            path = Path(f.name)

        try:
            assert not verify_md5(path, "0" * 32)
        finally:
            path.unlink()
