"""Tests for the ROM hashing module."""

from pathlib import Path

import pytest

from pier.roms.hashing import (
    N64_MAGIC,
    N64Format,
    compute_crc32,
    compute_md5,
    compute_sha1,
    convert_to_z64,
    detect_n64_format,
    verify_hash,
)


class TestComputeHashes:
    """Tests for hash computation functions."""

    def test_compute_sha1(self, tmp_path: Path) -> None:
        """SHA1 hash is computed correctly."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        # Known SHA1 of "hello world"
        expected = "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"
        assert compute_sha1(test_file) == expected

    def test_compute_md5(self, tmp_path: Path) -> None:
        """MD5 hash is computed correctly."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        # Known MD5 of "hello world"
        expected = "5eb63bbbe01eeed093cb22bb8f5acdc3"
        assert compute_md5(test_file) == expected

    def test_compute_crc32(self, tmp_path: Path) -> None:
        """CRC32 is computed correctly."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        # CRC32 should be uppercase hex, 8 chars
        result = compute_crc32(test_file)
        assert len(result) == 8
        assert result.isupper()
        # Known CRC32 of "hello world"
        assert result == "0D4A1185"

    def test_hashes_are_lowercase(self, tmp_path: Path) -> None:
        """SHA1 and MD5 hashes are lowercase."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"test data")

        sha1 = compute_sha1(test_file)
        md5 = compute_md5(test_file)

        assert sha1.islower()
        assert md5.islower()


class TestVerifyHash:
    """Tests for hash verification."""

    def test_verify_hash_matches(self, tmp_path: Path) -> None:
        """Verify returns True for matching hash."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        sha1 = "2aae6c35c94fcfb415dbe95f408b9ce91ee846ed"
        assert verify_hash(test_file, sha1) is True

    def test_verify_hash_case_insensitive(self, tmp_path: Path) -> None:
        """Verify is case-insensitive."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        sha1_upper = "2AAE6C35C94FCFB415DBE95F408B9CE91EE846ED"
        assert verify_hash(test_file, sha1_upper) is True

    def test_verify_hash_no_match(self, tmp_path: Path) -> None:
        """Verify returns False for non-matching hash."""
        test_file = tmp_path / "test.bin"
        test_file.write_bytes(b"hello world")

        wrong_hash = "0000000000000000000000000000000000000000"
        assert verify_hash(test_file, wrong_hash) is False


class TestN64FormatDetection:
    """Tests for N64 ROM format detection."""

    def test_detect_z64_format(self, tmp_path: Path) -> None:
        """Detect z64 (big-endian) format."""
        rom = tmp_path / "test.z64"
        rom.write_bytes(N64_MAGIC[N64Format.Z64] + b"\x00" * 100)

        assert detect_n64_format(rom) == N64Format.Z64

    def test_detect_n64_format(self, tmp_path: Path) -> None:
        """Detect n64 (little-endian) format."""
        rom = tmp_path / "test.n64"
        rom.write_bytes(N64_MAGIC[N64Format.N64] + b"\x00" * 100)

        assert detect_n64_format(rom) == N64Format.N64

    def test_detect_v64_format(self, tmp_path: Path) -> None:
        """Detect v64 (word-swapped) format."""
        rom = tmp_path / "test.v64"
        rom.write_bytes(N64_MAGIC[N64Format.V64] + b"\x00" * 100)

        assert detect_n64_format(rom) == N64Format.V64

    def test_detect_unknown_format(self, tmp_path: Path) -> None:
        """Detect unknown format for non-N64 files."""
        rom = tmp_path / "test.bin"
        rom.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)

        assert detect_n64_format(rom) == N64Format.UNKNOWN


class TestN64Conversion:
    """Tests for N64 ROM format conversion."""

    def test_convert_z64_to_z64(self, tmp_path: Path) -> None:
        """Converting z64 to z64 is a no-op."""
        rom = tmp_path / "test.z64"
        data = N64_MAGIC[N64Format.Z64] + b"\x00" * 100
        rom.write_bytes(data)

        result = convert_to_z64(rom)
        assert result == rom
        assert rom.read_bytes() == data

    def test_convert_z64_to_z64_with_dest(self, tmp_path: Path) -> None:
        """Converting z64 to z64 with dest copies the file."""
        rom = tmp_path / "test.z64"
        dest = tmp_path / "output.z64"
        data = N64_MAGIC[N64Format.Z64] + b"\x00" * 100
        rom.write_bytes(data)

        result = convert_to_z64(rom, dest)
        assert result == dest
        assert dest.read_bytes() == data

    def test_convert_n64_to_z64(self, tmp_path: Path) -> None:
        """Convert n64 (byte-swapped) to z64."""
        rom = tmp_path / "test.n64"
        # Create n64 format ROM: swap bytes to get n64 magic
        n64_data = N64_MAGIC[N64Format.N64] + b"\x01\x02\x03\x04"
        rom.write_bytes(n64_data)

        dest = tmp_path / "output.z64"
        result = convert_to_z64(rom, dest)

        # Check result is z64 format
        assert detect_n64_format(result) == N64Format.Z64
        # Check bytes were swapped
        converted = dest.read_bytes()
        assert converted[:4] == N64_MAGIC[N64Format.Z64]

    def test_convert_v64_to_z64(self, tmp_path: Path) -> None:
        """Convert v64 (word-swapped) to z64."""
        rom = tmp_path / "test.v64"
        v64_data = N64_MAGIC[N64Format.V64] + b"\x00" * 100
        rom.write_bytes(v64_data)

        dest = tmp_path / "output.z64"
        result = convert_to_z64(rom, dest)

        # Check result is z64 format
        assert detect_n64_format(result) == N64Format.Z64

    def test_convert_unknown_raises(self, tmp_path: Path) -> None:
        """Converting unknown format raises ValueError."""
        rom = tmp_path / "test.bin"
        rom.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)

        with pytest.raises(ValueError, match="Unknown N64 ROM format"):
            convert_to_z64(rom)

    def test_convert_inplace(self, tmp_path: Path) -> None:
        """Convert in-place when no dest specified."""
        rom = tmp_path / "test.n64"
        n64_data = N64_MAGIC[N64Format.N64] + b"\x00" * 100
        rom.write_bytes(n64_data)

        result = convert_to_z64(rom)

        assert result == rom
        assert detect_n64_format(rom) == N64Format.Z64
