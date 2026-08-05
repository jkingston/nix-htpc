#!/usr/bin/env python3
"""Reconcile Kodi's persisted production skin while Kodi is stopped."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import tempfile
import xml.etree.ElementTree as ET


MAX_SETTINGS_BYTES = 4 * 1024 * 1024
SETTING_ID = "lookandfeel.skin"


class ReconcileError(RuntimeError):
    """The production profile could not be reconciled safely."""


def _load(path: Path, uid: int) -> ET.Element:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ET.Element("settings", {"version": "2"})
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_size > MAX_SETTINGS_BYTES
    ):
        raise ReconcileError("Kodi GUI settings metadata is unsafe")
    try:
        root = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError) as error:
        raise ReconcileError("Kodi GUI settings are unreadable") from error
    if root.tag != "settings":
        raise ReconcileError("Kodi GUI settings have an unexpected root")
    return root


def reconcile(path: Path, skin: str, uid: int, gid: int) -> bool:
    """Persist ``skin`` atomically. Return whether the file changed."""

    root = _load(path, uid)
    matches = root.findall("./setting[@id='%s']" % SETTING_ID)
    if len(matches) > 1:
        raise ReconcileError("Kodi GUI settings contain duplicate skin settings")
    if matches and matches[0].text == skin:
        return False
    setting = matches[0] if matches else ET.SubElement(
        root, "setting", {"id": SETTING_ID}
    )
    setting.text = skin
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".guisettings.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return True


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--skin", required=True)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--gid", required=True, type=int)
    options = parser.parse_args(arguments)
    if not options.skin.startswith("skin."):
        raise ReconcileError("production skin ID is invalid")
    reconcile(options.path, options.skin, options.uid, options.gid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
