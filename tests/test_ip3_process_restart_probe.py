from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts import verify_ip3_process_restart as probe


class _PendingFuture:
    def done(self) -> bool:
        return False


class IP3ProcessRestartProbeTests(unittest.TestCase):
    def test_waits_for_planner_start_before_replay_phase(self):
        counts = iter((0, 1))
        with patch.object(probe, "api", side_effect=lambda *_args, **_kwargs:
                          (200, {"count": next(counts)}, {})), \
             patch.object(probe.time, "sleep"):
            probe._wait_planner_start(0, _PendingFuture(), timeout=1)

    def test_timeout_report_names_phase_and_redacts_secret(self):
        with patch.object(probe, "_image_id", side_effect=TimeoutError("token=hidden")):
            report = probe.verify()

        self.assertFalse(report["passed"])
        self.assertEqual(report["failure_phase"], "inspect_local_image")
        self.assertEqual(report["failure_class"], "TimeoutError")
        self.assertEqual(report["failure_message"], "token=[REDACTED]")
        self.assertTrue(report["compose_cleanup"])


if __name__ == "__main__":
    unittest.main()
