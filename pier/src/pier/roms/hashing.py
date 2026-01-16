"""ROM hashing and format conversion utilities."""

import hashlib
from enum import Enum
from pathlib import Path


class N64Format(Enum):
    """N64 ROM byte order formats."""

    Z64 = "z64"  # Big-endian (native)
    N64 = "n64"  # Little-endian (byte-swapped)
    V64 = "v64"  # Mixed-endian (word-swapped)
    UNKNOWN = "unknown"


# Magic bytes for each N64 format (first 4 bytes of ROM)
N64_MAGIC = {
    N64Format.Z64: b"\x80\x37\x12\x40",
    N64Format.N64: b"\x40\x12\x37\x80",
    N64Format.V64: b"\x37\x80\x40\x12",
}


def compute_sha1(path: Path) -> str:
    """Compute SHA1 hash of a file.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase hex string of the SHA1 hash.
    """
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha1.update(chunk)
    return sha1.hexdigest().lower()


def compute_md5(path: Path) -> str:
    """Compute MD5 hash of a file.

    Args:
        path: Path to the file to hash.

    Returns:
        Lowercase hex string of the MD5 hash.
    """
    md5 = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            md5.update(chunk)
    return md5.hexdigest().lower()


def compute_crc32(path: Path) -> str:
    """Compute CRC32 of a file.

    Args:
        path: Path to the file to hash.

    Returns:
        Uppercase hex string of the CRC32 (8 characters, zero-padded).
    """
    import binascii

    crc = 0
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            crc = binascii.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def detect_n64_format(path: Path) -> N64Format:
    """Detect the byte order format of an N64 ROM.

    Args:
        path: Path to the N64 ROM file.

    Returns:
        The detected N64Format, or UNKNOWN if not recognized.
    """
    with open(path, "rb") as f:
        magic = f.read(4)

    for fmt, expected_magic in N64_MAGIC.items():
        if magic == expected_magic:
            return fmt
    return N64Format.UNKNOWN


def _byteswap_data(data: bytes) -> bytes:
    """Swap every pair of bytes (for v64 -> z64 conversion).

    V64 has byte pairs swapped: 80 37 12 40 -> 37 80 40 12
    This reverses that operation.
    """
    result = bytearray(len(data))
    for i in range(0, len(data) - 1, 2):
        result[i] = data[i + 1]
        result[i + 1] = data[i]
    return bytes(result)


def _reverse_word_data(data: bytes) -> bytes:
    """Reverse each 4-byte word (for n64 -> z64 conversion).

    N64 is little-endian: 80 37 12 40 -> 40 12 37 80
    This reverses each 4-byte group to get back to big-endian.
    """
    result = bytearray(len(data))
    for i in range(0, len(data) - 3, 4):
        result[i] = data[i + 3]
        result[i + 1] = data[i + 2]
        result[i + 2] = data[i + 1]
        result[i + 3] = data[i]
    return bytes(result)


def convert_to_z64(src: Path, dest: Path | None = None) -> Path:
    """Convert an N64 ROM to z64 (big-endian) format.

    Args:
        src: Path to the source ROM file.
        dest: Optional destination path. If None, overwrites src in-place.

    Returns:
        Path to the converted ROM (dest if provided, otherwise src).

    Raises:
        ValueError: If the ROM format is unknown.
    """
    fmt = detect_n64_format(src)

    if fmt == N64Format.Z64:
        # Already z64, just copy if dest specified
        if dest and dest != src:
            dest.write_bytes(src.read_bytes())
            return dest
        return src

    if fmt == N64Format.UNKNOWN:
        msg = f"Unknown N64 ROM format: {src}"
        raise ValueError(msg)

    # Read the source ROM
    data = src.read_bytes()

    # Convert based on format
    if fmt == N64Format.N64:
        converted = _reverse_word_data(data)
    else:  # V64
        converted = _byteswap_data(data)

    # Write to destination
    output = dest if dest else src
    output.write_bytes(converted)
    return output


def verify_hash(path: Path, expected_sha1: str) -> bool:
    """Verify that a file matches an expected SHA1 hash.

    Args:
        path: Path to the file to verify.
        expected_sha1: Expected SHA1 hash (case-insensitive).

    Returns:
        True if the hash matches, False otherwise.
    """
    actual = compute_sha1(path)
    return actual == expected_sha1.lower()
