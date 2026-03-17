"""
Phase 54 Hardening — 5 unit tests covering source fixes.

1. test_numpy_fallback_build_distance_matrix
   Monkeypatches HAS_NUMPY=False; verifies build_distance_matrix returns
   a plain list-of-lists (no ImportError, no numpy dependency).

2. test_task_done_handler_logs_critical_on_exception
   Mock Task with exception=RuntimeError("boom"); verifies log.critical fires.

3. test_task_done_handler_ignores_cancelled
   Mock cancelled Task; verifies zero log records emitted.

4. test_send_raw_tx_resets_nonce_on_send_failure
   Mocks send_raw_transaction raising; verifies _nonce is None after
   (confirming _reset_nonce was called — Phase 54 fix).

5. test_migration_logging_on_column_exists
   Creates a real temp DB, calls _init_schema() twice; verifies log.debug
   fires with "schema migration already applied" on duplicate ALTER TABLEs.
"""

import asyncio
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Stub heavy external deps before importing vapi_bridge modules
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parents[1]))

for _mod in ("web3", "web3.exceptions", "eth_account"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

_web3_exc = sys.modules["web3.exceptions"]
for _cls in ("ContractLogicError", "TransactionNotFound"):
    if not hasattr(_web3_exc, _cls):
        setattr(_web3_exc, _cls, type(_cls, (Exception,), {}))
sys.modules["web3.exceptions"] = _web3_exc

_web3 = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3, _attr):
        setattr(_web3, _attr, MagicMock)
sys.modules["web3"] = _web3

_eth_acc = sys.modules["eth_account"]
if not hasattr(_eth_acc, "Account"):
    _mock_acct = MagicMock()
    _mock_acct.from_key.return_value = MagicMock(address="0xBridgeAddr")
    _eth_acc.Account = _mock_acct
sys.modules["eth_account"] = _eth_acc

from vapi_bridge.chain import ChainClient                           # noqa: E402
from vapi_bridge.store import Store                                 # noqa: E402
import vapi_bridge.network_correlation_detector as _ncd_mod         # noqa: E402
from vapi_bridge.network_correlation_detector import (              # noqa: E402
    NetworkCorrelationDetector,
)
from vapi_bridge.main import _task_done_handler                     # noqa: E402


# ---------------------------------------------------------------------------
# Helper — build a minimal ChainClient with all IO mocked
# ---------------------------------------------------------------------------

def _make_chain_client() -> ChainClient:
    client = ChainClient.__new__(ChainClient)
    client._cfg = MagicMock()
    client._cfg.chain_id = 4690
    client._w3 = MagicMock()
    client._account = MagicMock(address="0xBridgeAddr")
    # _nonce_lock must be created inside an active event loop; we set it in _run()
    client._nonce = 42  # pre-seeded so _next_nonce skips the eth call
    client._revoked_manufacturers = set()
    client._verifier = MagicMock()
    client._bounty_market = None
    client._registry = MagicMock()
    client._progress = None
    client._team_agg = None
    return client


# ===========================================================================
# 1. numpy fallback — list-of-lists when HAS_NUMPY=False
# ===========================================================================

class TestNumpyFallback(unittest.TestCase):
    def test_numpy_fallback_build_distance_matrix(self):
        """build_distance_matrix must return [[0.0]*n]*n when HAS_NUMPY=False."""
        store  = MagicMock()
        prover = MagicMock()
        det    = NetworkCorrelationDetector(store, prover)

        original = _ncd_mod.HAS_NUMPY
        try:
            _ncd_mod.HAS_NUMPY = False
            result = det.build_distance_matrix(["a", "b", "c"])
        finally:
            _ncd_mod.HAS_NUMPY = original

        self.assertIsInstance(result, list, "Expected list when HAS_NUMPY=False")
        self.assertEqual(len(result), 3)
        for row in result:
            self.assertIsInstance(row, list)
            self.assertEqual(len(row), 3)
            for val in row:
                self.assertEqual(val, 0.0)


# ===========================================================================
# 2. _task_done_handler logs CRITICAL on exception
# ===========================================================================

class TestTaskDoneHandlerException(unittest.TestCase):
    def test_task_done_handler_logs_critical_on_exception(self):
        """Mock Task with exception=RuntimeError('boom'); expect CRITICAL log."""
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = RuntimeError("boom")
        mock_task.get_name.return_value   = "test-batcher-task"

        with self.assertLogs("vapi_bridge.main", level="CRITICAL") as ctx:
            _task_done_handler(mock_task)

        combined = "\n".join(ctx.output)
        self.assertIn("boom", combined)
        self.assertIn("test-batcher-task", combined)


# ===========================================================================
# 3. _task_done_handler silent on cancelled task
# ===========================================================================

class TestTaskDoneHandlerCancelled(unittest.TestCase):
    def test_task_done_handler_ignores_cancelled(self):
        """Cancelled Task must produce zero log records."""
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.cancelled.return_value = True

        captured = []

        class _Cap(logging.Handler):
            def emit(self, record):
                captured.append(record)

        cap = _Cap()
        logger = logging.getLogger("vapi_bridge.main")
        logger.addHandler(cap)
        try:
            _task_done_handler(mock_task)
        finally:
            logger.removeHandler(cap)

        self.assertEqual(len(captured), 0,
            "No log records expected for a cancelled task")


# ===========================================================================
# 4. send_raw_transaction failure resets nonce
# ===========================================================================

class TestSendRawTxNonceReset(unittest.TestCase):
    def test_send_raw_tx_resets_nonce_on_send_failure(self):
        """_reset_nonce must be called and _nonce=None after send_raw_transaction raises."""
        client = _make_chain_client()
        reset_called = []

        async def _run():
            client._nonce_lock = asyncio.Lock()

            # Patch _reset_nonce to track invocation
            async def _mock_reset():
                reset_called.append(True)
                client._nonce = None  # replicate what the real _reset_nonce does
            client._reset_nonce = _mock_reset

            # Use plain coroutine functions — AsyncMock doesn't work as a bare awaitable
            # (gas_price is accessed as `await eth.gas_price`, not `await eth.gas_price()`)
            async def _gas_price_coro():
                return 1_000_000_000

            async def _estimate_gas(_tx):
                return 80_000

            async def _send_raw(_raw):
                raise Exception("network error")

            client._w3.eth.gas_price            = _gas_price_coro()  # awaitable once
            client._w3.eth.estimate_gas         = _estimate_gas
            client._w3.eth.send_raw_transaction = _send_raw

            mock_built = {
                "from": "0xBridgeAddr", "nonce": 42,
                "gas": 100000, "gasPrice": 1_000_000_000, "chainId": 4690,
            }

            async def _build_tx(_overrides):
                return mock_built

            mock_tx_obj = MagicMock()
            mock_tx_obj.build_transaction = _build_tx

            mock_signed = MagicMock()
            mock_signed.raw_transaction = b"\x00" * 32
            client._account.sign_transaction.return_value = mock_signed

            def _tx_func(*_args):
                return mock_tx_obj

            try:
                await client._send_tx(_tx_func)
            except Exception:
                pass  # expected — send_raw_transaction raised

        asyncio.run(_run())

        self.assertTrue(len(reset_called) > 0,
            "_reset_nonce must be called when send_raw_transaction fails")
        self.assertIsNone(
            client._nonce,
            "_nonce must be None after _reset_nonce is called"
        )


# ===========================================================================
# 5. store.py migration logging
# ===========================================================================

class TestMigrationLogging(unittest.TestCase):
    def test_migration_logging_on_column_exists(self):
        """Second _init_schema() call must log.debug 'schema migration already applied'."""
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, "test_phase54_migration.db")

        captured_debug = []

        class _DebugCap(logging.Handler):
            def emit(self, record):
                if record.levelno == logging.DEBUG:
                    captured_debug.append(record.getMessage())

        cap = _DebugCap()
        store_log = logging.getLogger("vapi_bridge.store")
        prev_level = store_log.level
        store_log.setLevel(logging.DEBUG)
        store_log.addHandler(cap)
        try:
            _store = Store(db_path)
            # Second call triggers OperationalError on all ALTER TABLE stmts
            _store._init_schema()
        finally:
            store_log.setLevel(prev_level)
            store_log.removeHandler(cap)

        self.assertTrue(
            any("schema migration already applied" in m for m in captured_debug),
            f"Expected 'schema migration already applied' in debug log. "
            f"Got: {captured_debug[:5]}"
        )


if __name__ == "__main__":
    unittest.main()
