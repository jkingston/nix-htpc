from __future__ import annotations

import ast
import importlib.util
import json
import re
import unittest
from pathlib import Path

from contract import load_contract


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[1]


def _load_python(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _class_integer_constants(path: Path, class_name: str) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            constants = {}
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1
                    and isinstance(statement.targets[0], ast.Name)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, int)
                ):
                    constants[statement.targets[0].id] = statement.value.value
            return constants
    raise AssertionError("class %s not found in %s" % (class_name, path))


class ExistingConsumerCompatibilityTest(unittest.TestCase):
    def test_settings_service_constants_match_playback_contract(self):
        contract = load_contract("playback")
        media = _load_python(
            REPOSITORY_ROOT
            / "modules"
            / "kodi-settings-addon"
            / "media_contract.py",
            "existing_media_contract",
        )
        self.assertEqual(media.HOME_WINDOW_ID, contract["property_window"])
        self.assertEqual(media.SERVICE_READY, contract["service"]["ready"])
        self.assertEqual(media.SERVICE_PROTOCOL, contract["service"]["protocol"])
        self.assertEqual(
            media.SERVICE_PROTOCOL_VERSION,
            contract["protocol_version"],
        )
        self.assertEqual(media.SEEK_PREFIX, contract["seek"]["prefix"])
        self.assertEqual(media.SEEK_REQUEST, contract["seek"]["request"])
        self.assertEqual(
            list(media.CURRENT_VIEW_SLOT_FIELDS),
            contract["seek"]["slot_fields"],
        )
        self.assertEqual(
            media.PREVIEW_CONTRACT,
            contract["preview"]["property"],
        )
        self.assertEqual(
            media.PREVIEW_CONTRACT_VERSION,
            contract["preview"]["schema"],
        )
        self.assertEqual(
            media.CHAPTER_CONTRACT_VERSION,
            contract["chapters"]["schema"],
        )
        for constant, key in (
            (media.CHAPTERS_AVAILABLE, "available"),
            (media.CHAPTERS_MANIFEST, "manifest"),
            (media.CHAPTERS_TOKEN, "token"),
            (media.CHAPTERS_PLAYBACK, "playback"),
            (media.CHAPTERS_REVISION, "revision"),
            (media.CHAPTER_AVAILABLE, "ui_available"),
            (media.CHAPTER_OPEN, "ui_open"),
        ):
            self.assertEqual(constant, contract["chapters"][key])

    def test_presenter_focus_targets_match_playback_contract(self):
        contract = load_contract("playback")
        constants = _class_integer_constants(
            REPOSITORY_ROOT
            / "modules"
            / "kodi-settings-addon"
            / "presenter.py",
            "HtpcPresenter",
        )
        self.assertEqual(
            constants["TOP_BAR_CONTROL_ID"],
            contract["controls"]["top_bar"],
        )
        self.assertEqual(
            constants["TIMELINE_CONTROL_ID"],
            contract["controls"]["timeline"],
        )
        self.assertEqual(
            constants["TRANSPORT_CONTROL_ID"],
            contract["controls"]["play_pause"],
        )

    def test_skin_controls_and_events_match_playback_contract(self):
        contract = load_contract("playback")
        expected_osd_events = {
            event
            for event in contract["seek"]["events"]
            if event.startswith(("timeline-", "transport-"))
        }
        skin_roots = [REPOSITORY_ROOT / "modules" / "bingie" / "src"]
        new_skin = REPOSITORY_ROOT / "modules" / "htpc-skin" / "src"
        if new_skin.exists():
            skin_roots.append(new_skin)

        for skin_root in skin_roots:
            with self.subTest(skin=skin_root.parent.name):
                xml_path = skin_root / "1080i" / "IncludesHTPCVideoOSD.xml"
                xml = xml_path.read_text(encoding="utf-8")
                controls = {
                    int(control_id): control_type
                    for control_type, control_id in re.findall(
                        r'<control\s+type="([^"]+)"\s+id="([0-9]+)"',
                        xml,
                    )
                }
                self.assertEqual(
                    controls[contract["controls"]["top_bar_group"]],
                    "grouplist",
                )
                for semantic_control in ("top_bar", "play_pause", "timeline"):
                    self.assertIn(contract["controls"][semantic_control], controls)

                emitted = set(
                    re.findall(
                        r"NotifyAll\(htpc\.seek,([a-z-]+)\)",
                        xml,
                    )
                )
                self.assertEqual(emitted, expected_osd_events)

    def test_home_contract_preserves_the_recorded_intent(self):
        home = load_contract("home")
        legacy_path = (
            REPOSITORY_ROOT
            / "modules"
            / "bingie"
            / "tests"
            / "fixtures"
            / "home_contract.json"
        )
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))[
            "intended_declarative_contract"
        ]
        expected_controls = dict(legacy["controls"])
        expected_controls["bootstrap"] = legacy["home"]["bootstrap_control"]
        self.assertEqual(home["window"]["controls"], expected_controls)
        self.assertEqual(
            [row["key"] for row in home["rows"]],
            [row["key"] for row in legacy["home"]["rows"]],
        )
        self.assertEqual(
            [row["label"] for row in home["rows"]],
            [row["label"] for row in legacy["home"]["rows"]],
        )
        self.assertEqual(
            [entry["label"] for entry in home["sidebar"]],
            [entry["label"] for entry in legacy["navigation"]["sidebar"]],
        )


if __name__ == "__main__":
    unittest.main()
