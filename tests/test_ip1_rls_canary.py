import unittest

from scripts.verify_ip1_rls_canary import visible_markers_match_tenant


class IP1RlsCanaryTests(unittest.TestCase):
    def test_tenant_can_only_see_its_own_marker(self):
        self.assertTrue(visible_markers_match_tenant("tenant-a", ["canary-a"]))
        self.assertTrue(visible_markers_match_tenant("tenant-b", ["canary-b"]))
        self.assertFalse(visible_markers_match_tenant("tenant-a", ["canary-a", "canary-b"]))
        self.assertFalse(visible_markers_match_tenant("tenant-a", ["canary-b"]))

    def test_missing_or_unknown_context_sees_no_canary_rows(self):
        self.assertTrue(visible_markers_match_tenant(None, []))
        self.assertTrue(visible_markers_match_tenant("tenant-unknown", []))
        self.assertFalse(visible_markers_match_tenant(None, ["canary-a"]))


if __name__ == "__main__":
    unittest.main()
