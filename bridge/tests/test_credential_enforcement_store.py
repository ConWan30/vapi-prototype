"""
Phase 37 — credential_enforcement store tests.

4 tests covering:
1. increment_consecutive_critical() starts at 1 for new device
2. Second call returns 2
3. reset_consecutive_critical() → 0; next increment returns 1
4. store_credential_suspension() + is_credential_suspended() = True;
   clear_credential_suspension() → False
"""
import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vapi_bridge.store import Store


def _fresh_store() -> Store:
    db_dir = tempfile.mkdtemp()
    return Store(os.path.join(db_dir, "test.db"))


class TestCredentialEnforcementStore(unittest.TestCase):

    def test_1_increment_starts_at_1(self):
        """increment_consecutive_critical() returns 1 for a brand-new device."""
        store = _fresh_store()
        result = store.increment_consecutive_critical("aa" * 32)
        self.assertEqual(result, 1)

    def test_2_second_increment_returns_2(self):
        """Calling increment_consecutive_critical() twice returns 2."""
        store = _fresh_store()
        dev = "bb" * 32
        store.increment_consecutive_critical(dev)
        result = store.increment_consecutive_critical(dev)
        self.assertEqual(result, 2)

    def test_3_reset_then_increment(self):
        """reset_consecutive_critical() → 0; next increment returns 1."""
        store = _fresh_store()
        dev = "cc" * 32
        store.increment_consecutive_critical(dev)
        store.increment_consecutive_critical(dev)
        store.reset_consecutive_critical(dev)
        row = store.get_credential_enforcement(dev)
        self.assertIsNotNone(row)
        self.assertEqual(row["consecutive_critical"], 0)
        result = store.increment_consecutive_critical(dev)
        self.assertEqual(result, 1)

    def test_4_suspension_lifecycle(self):
        """store_credential_suspension sets suspended=True; clear sets it to False."""
        store = _fresh_store()
        dev = "dd" * 32
        import time
        store.store_credential_suspension(dev, "aabbccdd" * 8, time.time() + 86400)
        self.assertTrue(store.is_credential_suspended(dev))
        store.clear_credential_suspension(dev)
        self.assertFalse(store.is_credential_suspended(dev))


if __name__ == "__main__":
    unittest.main()
