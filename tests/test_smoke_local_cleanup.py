from __future__ import annotations

import unittest
from unittest.mock import Mock

from scripts.smoke_local import _shutdown_server, _shutdown_server_preserving_error


class SmokeLocalCleanupTests(unittest.TestCase):
    def test_shutdown_is_bounded_for_a_thread_that_does_not_exit(self):
        server = Mock(should_exit=False, force_exit=False)
        thread = Mock()
        thread.is_alive.side_effect = [True, True]
        listener = Mock()

        stopped = _shutdown_server(server, thread, listener, join_timeout=0.25)

        self.assertFalse(stopped)
        self.assertTrue(server.should_exit)
        self.assertTrue(server.force_exit)
        self.assertEqual([call.kwargs["timeout"] for call in thread.join.call_args_list], [0.25, 0.25])
        listener.close.assert_called_once_with()

    def test_listener_closes_when_thread_join_raises(self):
        server = Mock(should_exit=False, force_exit=False)
        thread = Mock()
        thread.join.side_effect = RuntimeError("join failed")
        listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "join failed"):
            _shutdown_server(server, thread, listener, join_timeout=0.25)

        listener.close.assert_called_once_with()

    def test_cleanup_failure_does_not_mask_startup_failure(self):
        server = Mock(should_exit=False, force_exit=False)
        thread = Mock()
        thread.is_alive.side_effect = [True, True]
        listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            try:
                raise RuntimeError("startup failed")
            finally:
                _shutdown_server_preserving_error(server, thread, listener)

        listener.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
