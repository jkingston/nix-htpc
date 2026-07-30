from __future__ import annotations

import unittest

from tools.kodi_capture.ssh_policy import validate_ssh_host


class SshPolicyTest(unittest.TestCase):
    def test_valid_destinations_include_the_exact_length_boundary(self):
        for host in ("host", "root@host", "x" * 255):
            with self.subTest(host_length=len(host)):
                validate_ssh_host(host)

    def test_invalid_destinations_fail_before_openssh(self):
        invalid = (
            None,
            "",
            "-option",
            "host name",
            "host\tname",
            "host\nname",
            "host\x7fname",
            "x" * 256,
        )
        for host in invalid:
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    validate_ssh_host(host)


if __name__ == "__main__":
    unittest.main()
