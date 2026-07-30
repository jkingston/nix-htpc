"""Shared fixed OpenSSH policy for the Kodi capture helpers."""

from __future__ import annotations


SSH_PROGRAM = "ssh"
SSH_FIXED_CAPABILITY_OPTIONS = (
    "-F",
    "/dev/null",
    "-o",
    "BatchMode=yes",
    "-o",
    "ClearAllForwardings=yes",
    "-o",
    "ForwardAgent=no",
    "-o",
    "ForwardX11=no",
    "-o",
    "PermitLocalCommand=no",
    "-o",
    "EscapeChar=none",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
)
SSH_OPTION_TERMINATOR = "--"


def validate_ssh_host(host: str) -> None:
    """Require one bounded, non-option OpenSSH destination argument."""

    if (
        not isinstance(host, str)
        or not host
        or len(host) > 255
        or host.startswith("-")
        or any(
            ord(character) <= 32 or ord(character) == 127
            for character in host
        )
    ):
        raise ValueError("host must be a bounded non-option SSH destination")
