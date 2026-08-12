from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKIN_ROOT = REPOSITORY_ROOT / "modules" / "bingie" / "src"
REPORT_PATH = (
    REPOSITORY_ROOT
    / "modules"
    / "bingie"
    / "audit"
    / "dependency-inventory.json"
)
RUNTIME_PACKAGE_PATH = REPOSITORY_ROOT / "modules" / "bingie" / "default.nix"
TOOLS_ROOT = REPOSITORY_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

import bingie_dependency_inventory as inventory  # noqa: E402


EXPECTED_OBSERVATIONS = {
    "plugin.program.autocompletion": (
        "2.1.3",
        "cd9de4ff6d7db2596e3ef629e2092927617d4b2496a7ac55bf8f6d51015da388",
    ),
    "plugin.video.tmdb.bingie.helper": (
        "1.0.3",
        "e8439bceee735b6c26ec75141a6080b06cd3c60da31897d87e2c8cc44fafc653",
    ),
    "resource.images.studios.coloured": (
        "1.0.0012",
        "5021e2c0dfd56186ac04e0d7d8cd0914530b37897fd8f420fe0362b2ee652286",
    ),
    "script.bingie.helper": (
        "1.1.2",
        "79ea0d00b20513105445bf6e16a0424ca816f77cf4cc26822dcd86874d83cdb6",
    ),
    "script.bingie.toolbox": (
        "1.0.0",
        "6d5155d7c6ac758faf82ee27c93406be6026a56285fabe35ecdbf1d83a88d07f",
    ),
    "script.bingie.widgets": (
        "1.0.5",
        "a55099710a32fd99d606ef860a727ac88502860e7db45ea50c6fc8209ac70b9a",
    ),
    "script.skinshortcuts": (
        "2.0.3",
        "1a6ca7fefcbe2550fda02795681537e01931bbd55259999545159ebf1c888141",
    ),
}
NIX_ONLY_IDS = ["script.bingie.helper", "script.bingie.widgets"]
USERDATA_ONLY_IDS = [
    addon_id
    for addon_id in inventory.MANDATORY_IDS
    if addon_id not in NIX_ONLY_IDS
]


def _manifest(addon_id: str, version: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<addon id="{addon_id}" version="{version}" name="test" provider-name="test"/>\n'
    )


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class DependencyInventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.raw = inventory.load_report(REPORT_PATH)

    def test_committed_report_is_canonical_and_matches_skin_source(self):
        self.assertEqual(
            inventory.validate_report(self.report, self.raw, SKIN_ROOT),
            [],
        )

    def test_command_defaults_resolve_from_repository_root(self):
        self.assertEqual(inventory.main(["check"]), 0)

    def test_runtime_skin_package_has_no_dependency_audit_inputs(self):
        source = RUNTIME_PACKAGE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "BINGIE_DEPENDENCY_REPORT",
            "bingie_dependency_inventory",
            "dependency-inventory.json",
            "./audit",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_exact_observations_match_generation_b_provenance(self):
        self.assertEqual(
            [dependency["id"] for dependency in self.report["dependencies"]],
            inventory.MANDATORY_IDS,
        )
        for dependency in self.report["dependencies"]:
            with self.subTest(addon_id=dependency["id"]):
                observation = (
                    dependency["nix_closure"]
                    if dependency["id"] in NIX_ONLY_IDS
                    else dependency["userdata"]
                )
                self.assertEqual(
                    (
                        observation["version"],
                        observation["addon_xml_sha256"],
                    ),
                    EXPECTED_OBSERVATIONS[dependency["id"]],
                )
                self.assertTrue(dependency["runtime_enabled"])
                self.assertNotIn("enabled", observation)
                if dependency["id"] in NIX_ONLY_IDS:
                    self.assertIsNone(dependency["userdata"])
                    self.assertEqual(dependency["classification"], "nix_only")
                    self.assertEqual(
                        dependency["provenance_status"],
                        "nix_pinned",
                    )
                else:
                    self.assertIsNone(dependency["nix_closure"])
                    self.assertEqual(
                        dependency["classification"],
                        "userdata_only",
                    )
                    self.assertEqual(
                        dependency["provenance_status"],
                        "unverified_deployed_userdata",
                    )

    def test_optional_reference_inventory_covers_user_visible_integrations(self):
        references = self.report["referenced_not_imported"]
        required = {
            "plugin.video.imdb.trailers",
            "plugin.video.youtube",
            "resource.images.moviegenreicons.arctic.zephyr",
            "resource.images.moviegenreicons.bingie",
            "resource.images.moviegenreicons.coloured",
            "resource.images.moviegenreicons.filmstrip",
            "resource.images.moviegenreicons.filmstrip-hd.bw",
            "resource.images.moviegenreicons.filmstrip-hd.colour",
            "resource.images.moviegenreicons.grey",
            "resource.images.moviegenreicons.poster",
            "resource.images.moviegenreicons.transparent",
            "resource.images.moviegenreicons.white",
            "resource.images.moviegenreicons.xzener-flat",
            "resource.images.moviegenreicons.xzener-reflection",
            "resource.images.studios.white",
            "script.cu.lrclyrics",
            "script.skin.helper.colorpicker",
            "script.skin.helper.skinbackup",
            "script.tv.show.next.aired",
            "service.upnext",
        }
        self.assertTrue(required.issubset(references["addons"]))
        self.assertNotIn("service.openelec.settings", references["addons"])
        self.assertNotIn("service.libreelec.settings", references["addons"])
        self.assertIn(
            "resource.images.moviegenreicons",
            references["resource_families"],
        )
        manifest = inventory.parse_manifest(SKIN_ROOT / "addon.xml")
        self.assertEqual(
            references,
            inventory.source_references(
                SKIN_ROOT,
                [item["id"] for item in manifest["imports"]],
            ),
        )

    def test_classification_matrix_is_explicit(self):
        old = {"addon_xml_sha256": "a" * 64, "version": "1"}
        exact = {"addon_xml_sha256": "a" * 64, "version": "1"}
        rehashed = {"addon_xml_sha256": "b" * 64, "version": "1"}
        new = {"addon_xml_sha256": "b" * 64, "version": "2"}
        cases = [
            (None, None, "missing"),
            (old, None, "nix_only"),
            (None, old, "userdata_only"),
            (old, exact, "both_exact_match"),
            (old, rehashed, "both_same_version_manifest_mismatch"),
            (old, new, "both_version_mismatch"),
        ]
        for nix_closure, userdata, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    inventory.classify(nix_closure, userdata),
                    expected,
                )

    def test_current_nix_observation_has_complete_sanitized_evidence(self):
        self.assertEqual(
            self.report["nix_closure_evidence"],
            {
                "coverage": "all_addon_manifests_in_recursive_requisites",
                "mandatory_addons_found": NIX_ONLY_IDS,
                "method": "nix-store --query --requisites /run/current-system",
                "system_closure_basename": (
                    "la9bs74dz5q7hkwr7alx8f0h69ac7yhm-"
                    "nixos-system-htpc-pi-sd-card-26.05.20260724.597283a"
                ),
            },
        )
        self.assertEqual(
            [
                dependency["id"]
                for dependency in self.report["dependencies"]
                if dependency["nix_closure"] is not None
            ],
            NIX_ONLY_IDS,
        )
        self.assertEqual(
            [
                dependency["id"]
                for dependency in self.report["dependencies"]
                if dependency["userdata"] is not None
            ],
            USERDATA_ONLY_IDS,
        )

    def test_future_pinned_nix_provenance_states_validate(self):
        cases = [
            (None, "nix_only", "nix_pinned"),
            ("exact", "both_exact_match", "nix_pinned_with_deployed_userdata"),
            (
                "rehash",
                "both_same_version_manifest_mismatch",
                "nix_pinned_with_deployed_userdata",
            ),
            (
                "upgrade",
                "both_version_mismatch",
                "nix_pinned_with_deployed_userdata",
            ),
        ]
        for userdata_mode, classification, provenance in cases:
            with self.subTest(classification=classification):
                report = copy.deepcopy(self.report)
                dependency = report["dependencies"][1]
                report["nix_closure_evidence"]["mandatory_addons_found"] = sorted(
                    NIX_ONLY_IDS + [dependency["id"]]
                )
                dependency["nix_closure"] = copy.deepcopy(dependency["userdata"])
                if userdata_mode is None:
                    dependency["userdata"] = None
                elif userdata_mode == "rehash":
                    dependency["nix_closure"]["addon_xml_sha256"] = "f" * 64
                elif userdata_mode == "upgrade":
                    dependency["nix_closure"]["version"] = "9.0.0"
                dependency["classification"] = classification
                dependency["provenance_status"] = provenance
                self.assertEqual(
                    inventory.validate_report(
                        report,
                        inventory.canonical_json(report),
                        SKIN_ROOT,
                    ),
                    [],
                )

    def test_optional_reference_scanner_includes_xsp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reference.xsp").write_text(
                "<path>plugin://plugin.video.youtube/search/</path>\n",
                encoding="utf-8",
            )
            self.assertEqual(
                inventory.source_references(root, []),
                {
                    "addons": ["plugin.video.youtube"],
                    "resource_families": [],
                },
            )

    def test_capture_filters_immediately_and_does_not_mutate_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            addons_root = root / "addons"
            wanted = addons_root / inventory.MANDATORY_IDS[0]
            unrelated = addons_root / "plugin.video.private"
            wanted.mkdir(parents=True)
            unrelated.mkdir()
            (wanted / "addon.xml").write_text(
                _manifest(inventory.MANDATORY_IDS[0], "1.2.3"),
                encoding="utf-8",
            )
            (unrelated / "addon.xml").write_text(
                _manifest("plugin.video.private", "9.9.9"),
                encoding="utf-8",
            )
            listed = root / inventory.MANDATORY_IDS[1]
            listed.mkdir()
            (listed / "addon.xml").write_text(
                _manifest(inventory.MANDATORY_IDS[1], "2.3.4"),
                encoding="utf-8",
            )
            path_list = root / "paths.txt"
            path_list.write_text(f"{listed}\n", encoding="utf-8")
            before = _tree_hashes(root)

            result = inventory.capture("userdata", [addons_root], [path_list])

            self.assertEqual(_tree_hashes(root), before)
            self.assertEqual(
                [item["id"] for item in result["addons"]],
                inventory.MANDATORY_IDS[:2],
            )
            self.assertNotIn("private", inventory.canonical_json(result))

            empty = root / "empty"
            empty.mkdir()
            self.assertEqual(
                inventory.capture("nix_closure", [empty], [])["addons"],
                [],
            )
            invalid_root = root / "invalid"
            invalid = invalid_root / inventory.MANDATORY_IDS[2]
            invalid.mkdir(parents=True)
            (invalid / "addon.xml").write_text(
                _manifest(inventory.MANDATORY_IDS[2], "../private"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                inventory.InventoryError,
                "invalid version",
            ):
                inventory.capture("userdata", [invalid_root], [])

    def test_capture_rejects_duplicate_mandatory_addons_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for parent in ("first", "second"):
                addon = root / parent / inventory.MANDATORY_IDS[0]
                addon.mkdir(parents=True)
                (addon / "addon.xml").write_text(
                    _manifest(inventory.MANDATORY_IDS[0], parent),
                    encoding="utf-8",
                )
            with self.assertRaisesRegex(
                inventory.InventoryError,
                "duplicate add-on id",
            ):
                inventory.capture(
                    "userdata",
                    [root / "first", root / "second"],
                    [],
                )

    def test_validator_fails_closed_on_drift_types_privacy_and_references(self):
        cases = {}
        drift = copy.deepcopy(self.report)
        drift["dependencies"][0]["minimum_version"] = "0"
        cases["declaration"] = drift
        classification = copy.deepcopy(self.report)
        classification["dependencies"][1]["classification"] = "nix_only"
        cases["classification"] = classification
        resolution = copy.deepcopy(self.report)
        resolution["dependencies"][0]["planned_resolution"] = "later"
        cases["planned_resolution"] = resolution
        version_type = copy.deepcopy(self.report)
        version_type["dependencies"][0]["minimum_version"] = 1
        cases["minimum_version"] = version_type
        optional_type = copy.deepcopy(self.report)
        optional_type["dependencies"][0]["optional"] = "false"
        cases["optional"] = optional_type
        runtime_type = copy.deepcopy(self.report)
        runtime_type["dependencies"][0]["runtime_enabled"] = "true"
        cases["runtime_enabled"] = runtime_type
        privacy = copy.deepcopy(self.report)
        privacy["dependencies"][1]["userdata"]["version"] = "/home/private"
        cases["private filesystem"] = privacy
        references = copy.deepcopy(self.report)
        references["referenced_not_imported"]["addons"].remove(
            "plugin.video.youtube"
        )
        cases["referenced_not_imported"] = references
        closure = copy.deepcopy(self.report)
        closure["nix_closure_evidence"]["mandatory_addons_found"] = []
        cases["contradicts nix_closure_evidence"] = closure

        for expected, report in cases.items():
            with self.subTest(expected=expected):
                errors = inventory.validate_report(
                    report,
                    inventory.canonical_json(report),
                    SKIN_ROOT,
                )
                self.assertTrue(
                    any(expected in error for error in errors),
                    errors,
                )

    def test_json_loader_rejects_duplicates_nonfinite_and_noncanonical_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for invalid in ('{"a": 1, "a": 2}\\n', '{"a": NaN}\\n'):
                with self.subTest(invalid=invalid):
                    path.write_text(invalid, encoding="utf-8")
                    with self.assertRaises(inventory.InventoryError):
                        inventory.load_report(path)
        self.assertNotEqual(self.raw, self.raw.rstrip())
        self.assertTrue(
            any(
                "not canonical JSON" in error
                for error in inventory.validate_report(
                    self.report,
                    self.raw.rstrip(),
                    SKIN_ROOT,
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
