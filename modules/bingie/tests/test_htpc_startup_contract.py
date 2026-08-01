from __future__ import annotations

import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


BINGIE_ROOT = Path(__file__).resolve().parents[1]
SKIN_ROOT = Path(
    os.environ.get("BINGIE_SKIN_ROOT", str(BINGIE_ROOT / "src"))
).resolve()
XML_ROOT = SKIN_ROOT / "1080i"

STARTUP_XML = XML_ROOT / "Startup.xml"
STARTUP_VARIABLES_XML = XML_ROOT / "IncludesVariables.xml"
INCLUDES_XML = XML_ROOT / "Includes.xml"
HOME_XML = XML_ROOT / "Home.xml"
FIRST_RUN_XML = XML_ROOT / "Custom_1101_StartUp.xml"
SECOND_RUN_XML = XML_ROOT / "Custom_1102_StartUp2.xml"
STARTUP_MASK_XML = XML_ROOT / "Custom_1103_StartUpMask.xml"


def _parse(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


def _text(node: ET.Element | None) -> str:
    return "" if node is None else (node.text or "").strip()


def _actions(node: ET.Element, name: str) -> list[ET.Element]:
    return list(node.findall(name))


def _action_texts(node: ET.Element, name: str) -> list[str]:
    return [_text(action) for action in _actions(node, name)]


def _single_named(root: ET.Element, tag: str, name: str) -> ET.Element:
    matches = [node for node in root.findall(tag) if node.get("name") == name]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one direct {tag} named {name!r}, found {len(matches)}"
        )
    return matches[0]


def _button(root: ET.Element, control_id: int) -> ET.Element:
    matches = [
        node
        for node in root.iter("control")
        if node.get("type") == "button" and node.get("id") == str(control_id)
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one button id {control_id}, found {len(matches)}"
        )
    return matches[0]


class StartupLifecycleContractTest(unittest.TestCase):
    def test_startup_clears_mask_then_routes_without_media_side_effects(self):
        root = _parse(STARTUP_XML)
        onloads = _actions(root, "onload")
        action_texts = [_text(action) for action in onloads]

        self.assertEqual(
            action_texts,
            [
                "SetProperty(Random,$INFO[System.Time(ss)],Home)",
                "ClearProperty(StartupMask,Home)",
                "ReplaceWindow($VAR[StartUpWindow])",
                "Skin.SetBool(TMDbBingieHelper.Service)",
                "Skin.SetBool(TMDbBingieHelper.DirectCallAuto)",
                "Skin.SetString(TMDbBingieHelper.MonitorContainer,17195)",
            ],
        )
        self.assertTrue(all(action.get("condition") is None for action in onloads))
        self.assertEqual(_text(root.find("include")), "DefaultSkinSettings")

        startup_source = STARTUP_XML.read_text(encoding="utf-8")
        self.assertNotIn("SetProperty(StartupMask", startup_source)
        self.assertNotIn("PlayMedia(", startup_source)
        self.assertNotIn("SplashScreen", startup_source)
        self.assertNotIn("splash_screen", startup_source)
        self.assertNotIn("PlayerControl(Stop)", startup_source)
        self.assertEqual(_actions(root, "onunload"), [])

    def test_legacy_startup_mask_can_only_clear_itself(self):
        root = _parse(STARTUP_MASK_XML)
        onloads = _actions(root, "onload")

        self.assertEqual(len(onloads), 1)
        self.assertEqual(_text(onloads[0]), "ClearProperty(StartupMask,Home)")
        self.assertIsNone(onloads[0].get("condition"))
        self.assertEqual(
            _text(root.find("visible")),
            "!String.IsEmpty(Window(Home).Property(StartupMask))",
        )

        controls = list(root.iter("control"))
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0].get("type"), "button")
        self.assertEqual(controls[0].get("id"), "10")

        source = STARTUP_MASK_XML.read_text(encoding="utf-8")
        self.assertNotIn("PlayMedia(", source)
        self.assertNotIn("bingie_intro", source)
        self.assertNotIn("Player.HasVideo", source)
        self.assertNotIn("AlarmClock(startup", source)
        self.assertNotIn("videowindow", source)
        self.assertNotIn("Busy_Spinner", source)

    def test_startup_window_keeps_the_ordered_first_run_state_machine(self):
        root = _parse(STARTUP_VARIABLES_XML)
        variable = _single_named(root, "variable", "StartUpWindow")
        values = list(variable.findall("value"))

        self.assertEqual(
            [(value.get("condition"), _text(value)) for value in values],
            [
                ("!Skin.HasSetting(BingieFirstStartupDone)", "1101"),
                ("!Skin.HasSetting(BingieSecondStartupDone)", "1102"),
                (None, "$INFO[System.StartupWindow]"),
            ],
        )

        includes = _parse(INCLUDES_XML)
        expression = _single_named(includes, "expression", "IsFirstRun")
        self.assertEqual(
            _text(expression),
            "!Skin.HasSetting(BingieFirstStartupDone) | "
            "!Skin.HasSetting(BingieSecondStartupDone)",
        )

        home = _parse(HOME_XML)
        first_run_redirects = [
            action
            for action in _actions(home, "onload")
            if _text(action) == "ReplaceWindow($VAR[StartUpWindow])"
        ]
        self.assertEqual(len(first_run_redirects), 1)
        self.assertEqual(first_run_redirects[0].get("condition"), "$EXP[IsFirstRun]")

    def test_first_run_buttons_keep_completion_and_routing_actions(self):
        first_run = _parse(FIRST_RUN_XML)
        for control_id in (101, 102):
            actions = _action_texts(_button(first_run, control_id), "onclick")
            self.assertIn("Skin.SetBool(BingieFirstStartupDone)", actions)
            self.assertIn(
                "AlarmClock(delay_window,ReplaceWindow($VAR[StartUpWindow]),"
                "00:01,silent)",
                actions,
            )

        second_run = _parse(SECOND_RUN_XML)
        source_actions = _action_texts(_button(second_run, 101), "onclick")
        home_actions = _action_texts(_button(second_run, 102), "onclick")
        for actions in (source_actions, home_actions):
            self.assertIn("Skin.SetBool(BingieSecondStartupDone)", actions)
        self.assertIn(
            "AlarmClock(delay_window,ReplaceWindow(Videos,sources://video/),"
            "00:01,silent)",
            source_actions,
        )
        self.assertIn(
            "AlarmClock(delay_window,ReplaceWindow($VAR[StartUpWindow]),"
            "00:01,silent)",
            home_actions,
        )


if __name__ == "__main__":
    unittest.main()
