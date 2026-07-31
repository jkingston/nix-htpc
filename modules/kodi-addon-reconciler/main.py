#!/usr/bin/env python3
"""Fixed production entrypoint for Kodi add-on reconciliation."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from reconciler import ReconcileError, load_configuration, reconcile


CONFIGURATION_PATH = Path("@CONFIGURATION_PATH@")


def main() -> int:
    if len(sys.argv) != 1:
        print(
            "kodi-addon-reconciler: command-line arguments are not supported",
            file=sys.stderr,
        )
        return 2
    if os.geteuid() != 0:
        print("kodi-addon-reconciler: must run as root", file=sys.stderr)
        return 2
    try:
        moves = reconcile(load_configuration(CONFIGURATION_PATH))
    except ReconcileError as error:
        print(f"kodi-addon-reconciler: {error}", file=sys.stderr)
        return 1
    for move in moves:
        print(f"kodi-addon-reconciler: {move.operation} {move.addon_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
