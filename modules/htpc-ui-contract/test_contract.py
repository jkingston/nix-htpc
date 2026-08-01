from __future__ import annotations

import copy
import json
import unittest

from contract import ContractError, load_contract, loads_contract, playback_property_names


class ContractValidationTest(unittest.TestCase):
    def test_checked_in_contracts_validate(self):
        self.assertEqual(load_contract("home")["schema_version"], 1)
        self.assertEqual(load_contract("playback")["protocol_version"], "2")

    def test_loader_rejects_ambiguous_json(self):
        with self.assertRaisesRegex(ContractError, "duplicate JSON key"):
            loads_contract("home", '{"schema_version": 1, "schema_version": 1}')
        with self.assertRaisesRegex(ContractError, "non-finite JSON number"):
            loads_contract("home", '{"schema_version": NaN}')

    def test_home_rejects_duplicate_routes(self):
        contract = load_contract("home")
        mutated = copy.deepcopy(contract)
        mutated["rows"][1]["route"] = mutated["rows"][0]["route"]
        with self.assertRaisesRegex(ContractError, "row routes must be unique"):
            loads_contract("home", json.dumps(mutated))

    def test_playback_rejects_non_atomic_slot_contract(self):
        contract = load_contract("playback")
        mutated = copy.deepcopy(contract)
        mutated["seek"]["commit_property"] = "active"
        with self.assertRaisesRegex(ContractError, "viewslot must be the commit"):
            loads_contract("playback", json.dumps(mutated))

    def test_property_inventory_has_no_collisions(self):
        contract = load_contract("playback")
        expected_count = (
            len(contract["service"])
            + 1
            + 1
            + len(contract["chapters"])
            - 1  # The chapter schema is metadata, not a Window property.
            + len(contract["seek"]["controller_fields"])
            + len(contract["seek"]["view_fields"])
            + len(contract["seek"]["slots"]) * len(contract["seek"]["slot_fields"])
        )
        properties = playback_property_names(contract)
        self.assertEqual(len(properties), expected_count)
        self.assertTrue(all(isinstance(name, str) for name in properties))
if __name__ == "__main__":
    unittest.main()
