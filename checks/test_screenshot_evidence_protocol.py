from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import asdict
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.kodi_capture import remote_directory as consumer


PRODUCER_PATH = (
    REPOSITORY_ROOT
    / "modules"
    / "kodi-screenshot-evidence"
    / "kodi_screenshot_evidence.py"
)
PRODUCER_MODULE_NAME = "_kodi_screenshot_evidence_protocol_producer"


def load_producer():
    specification = importlib.util.spec_from_file_location(
        PRODUCER_MODULE_NAME,
        PRODUCER_PATH,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load screenshot-evidence producer")
    module = importlib.util.module_from_spec(specification)
    sys.modules[PRODUCER_MODULE_NAME] = module
    specification.loader.exec_module(module)
    return module


producer = load_producer()


class ScreenshotEvidenceProtocolTest(unittest.TestCase):
    def test_real_producer_output_has_exact_consumer_semantics(self):
        encoded = producer.encode_evidence(producer_evidence())
        snapshot = consumer._parse_snapshot(encoded)
        self.maxDiff = None
        self.assertEqual(asdict(snapshot), expected_semantics())

    def test_producer_and_consumer_protocol_limits_match(self):
        names = (
            "PROTOCOL_VERSION",
            "MAX_ENTRIES",
            "MAX_OUTPUT_BYTES",
            "MAX_FIELD_BYTES",
            "MAX_FILENAME_BYTES",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(
                    getattr(producer, name),
                    getattr(consumer, name),
                )


def producer_evidence():
    return producer.DirectoryEvidence(
        stat=producer.StatEvidence(
            file_type="d",
            device=11,
            inode=12,
            mode=0o700,
            uid=1000,
            gid=100,
            link_count=2,
            size=13,
            mtime_ns=14,
            ctime_ns=15,
        ),
        owner="htpc",
        group="users",
        files=(
            producer.FileEvidence(
                name="screenshot00001.png",
                stat=producer.StatEvidence(
                    file_type="f",
                    device=11,
                    inode=21,
                    mode=0o640,
                    uid=1000,
                    gid=100,
                    link_count=1,
                    size=22,
                    mtime_ns=23,
                    ctime_ns=24,
                ),
            ),
            producer.FileEvidence(
                name="screenshot00002.png",
                stat=producer.StatEvidence(
                    file_type="f",
                    device=11,
                    inode=31,
                    mode=0o600,
                    uid=1000,
                    gid=100,
                    link_count=1,
                    size=32,
                    mtime_ns=33,
                    ctime_ns=34,
                ),
            ),
        ),
    )


def expected_semantics():
    return {
        "directory": {
            "stat": {
                "file_type": "d",
                "device": 11,
                "inode": 12,
                "mode": 0o700,
                "uid": 1000,
                "gid": 100,
                "link_count": 2,
                "size": 13,
                "mtime_ns": 14,
                "ctime_ns": 15,
            },
            "owner": "htpc",
            "group": "users",
        },
        "files": (
            {
                "name": "screenshot00001.png",
                "stat": {
                    "file_type": "f",
                    "device": 11,
                    "inode": 21,
                    "mode": 0o640,
                    "uid": 1000,
                    "gid": 100,
                    "link_count": 1,
                    "size": 22,
                    "mtime_ns": 23,
                    "ctime_ns": 24,
                },
            },
            {
                "name": "screenshot00002.png",
                "stat": {
                    "file_type": "f",
                    "device": 11,
                    "inode": 31,
                    "mode": 0o600,
                    "uid": 1000,
                    "gid": 100,
                    "link_count": 1,
                    "size": 32,
                    "mtime_ns": 33,
                    "ctime_ns": 34,
                },
            },
        ),
    }


if __name__ == "__main__":
    unittest.main()
