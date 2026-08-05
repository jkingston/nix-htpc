from pathlib import Path
import tempfile
import unittest

import snapshot


class SnapshotParserTest(unittest.TestCase):
    def test_parses_kib_fields_and_ignores_non_numeric_values(self):
        self.assertEqual(
            snapshot.parse_kib_fields(
                "VmRSS:\t345680 kB\nThreads:\t42\nState:\tS sleeping\n"
            ),
            {"VmRSS": 345680, "Threads": 42},
        )

    def test_task_stat_handles_spaces_and_parentheses_in_name(self):
        payload = "77 (Language Invoker) S 1 2 3 4 5 6 7 8 9 10 11 12 13"
        self.assertEqual(snapshot.parse_task_stat(payload), ("Language Invoker", 23))

    def test_cpu_percentages_are_sorted_and_normalized_per_core(self):
        before = (1000, {1: ("Kodi", 10), 2: ("worker", 20)})
        after = (1200, {1: ("Kodi", 110), 2: ("worker", 40)})
        rows = snapshot.cpu_percentages(before, after, 4)
        self.assertEqual([row["name"] for row in rows], ["Kodi", "worker"])
        self.assertEqual(rows[0]["cpu_percent"], 200.0)

    def test_process_discovery_requires_one_exact_kodi_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            for pid, name in (("1", "init"), ("20", "kodi.bin")):
                directory = proc / pid
                directory.mkdir()
                (directory / "comm").write_text(name + "\n")
            self.assertEqual(snapshot.find_kodi_pid(proc), 20)


if __name__ == "__main__":
    unittest.main()
