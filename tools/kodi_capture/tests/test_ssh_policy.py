from __future__ import annotations

import subprocess
import unittest

from tools.kodi_capture.ssh_policy import (
    SSH_FIXED_CAPABILITY_OPTIONS,
    SSH_OPTION_TERMINATOR,
    SSH_PROGRAM,
    validate_ssh_host,
)
from tools.kodi_capture.ssh_stream import KODI_JSON_RPC_ENDPOINT


class SshPolicyTest(unittest.TestCase):
    def _effective_settings(self, *capability_args):
        completed = subprocess.run(
            [SSH_PROGRAM, "-G", *capability_args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg="ssh -G failed: %s" % completed.stderr,
        )
        return set(completed.stdout.splitlines())

    def test_fixed_capability_options_are_exact_and_ordered(self):
        self.assertEqual(
            SSH_FIXED_CAPABILITY_OPTIONS,
            (
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
            ),
        )

    def test_openssh_accepts_effective_fixed_capability_policy(self):
        common_settings = {
            "batchmode yes",
            "clearallforwardings yes",
            "forwardagent no",
            "forwardx11 no",
            "permitlocalcommand no",
            "controlmaster false",
            "requesttty false",
            "escapechar none",
        }
        capability_shapes = {
            "fixed-command": (
                "-T",
                *SSH_FIXED_CAPABILITY_OPTIONS,
                SSH_OPTION_TERMINATOR,
                "root@example.invalid",
                "/run/current-system/sw/bin/kodi-passive-evidence",
            ),
            "direct-stream": (
                *SSH_FIXED_CAPABILITY_OPTIONS,
                "-W",
                KODI_JSON_RPC_ENDPOINT,
                SSH_OPTION_TERMINATOR,
                "root@example.invalid",
            ),
        }

        for shape, capability_args in capability_shapes.items():
            with self.subTest(shape=shape):
                settings = self._effective_settings(*capability_args)
                self.assertTrue(
                    common_settings.issubset(settings),
                    msg="missing settings: %r"
                    % sorted(common_settings - settings),
                )
                if shape == "direct-stream":
                    self.assertIn("sessiontype none", settings)
                    self.assertIn("exitonforwardfailure yes", settings)

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
