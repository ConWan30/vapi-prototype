"""
Phase 28 — CoAP Transport Tests

TestCoapTransport (6):
1.  228-byte payload → render_post returns CHANGED (2.04) + callback invoked
2.  wrong-size payload → render_post returns BAD_REQUEST (4.00) + callback NOT invoked
3.  exception in on_record → render_post returns INTERNAL_SERVER_ERROR, message ≤ 100 chars
4.  source string format is "coap:{hostinfo}"
5.  CoapTransport.run() binds to cfg.coap_bind / cfg.coap_port
6.  CancelledError in run() → context.shutdown() called (graceful)
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# ---------------------------------------------------------------------------
# Stub aiocoap before any imports that trigger coap.py loading
# coap.py uses: aiocoap.CHANGED, aiocoap.BAD_REQUEST, aiocoap.INTERNAL_SERVER_ERROR
#               aiocoap.Message, aiocoap.Context.create_server_context
#               aiocoap.resource.Resource, aiocoap.resource.Site
# ---------------------------------------------------------------------------
_aiocoap = types.ModuleType("aiocoap")
_aiocoap_resource = types.ModuleType("aiocoap.resource")

# Module-level response code sentinels — MUST be on _aiocoap directly
_CHANGED = "2.04_CHANGED"
_BAD_REQUEST = "4.00_BAD_REQUEST"
_INTERNAL_SERVER_ERROR = "5.00_INTERNAL"

_aiocoap.CHANGED = _CHANGED
_aiocoap.BAD_REQUEST = _BAD_REQUEST
_aiocoap.INTERNAL_SERVER_ERROR = _INTERNAL_SERVER_ERROR


class _Message:
    def __init__(self, code=None, payload=b""):
        self.code = code
        self.payload = payload


_aiocoap.Message = _Message


class _Context:
    @staticmethod
    async def create_server_context(site, bind=None, **kwargs):
        ctx = MagicMock()
        ctx.shutdown = AsyncMock()
        return ctx


_aiocoap.Context = _Context


class _Resource:
    def __init__(self):
        pass


_aiocoap_resource.Resource = _Resource
_aiocoap_resource.Site = MagicMock

sys.modules.setdefault("aiocoap", _aiocoap)
sys.modules.setdefault("aiocoap.resource", _aiocoap_resource)

for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

POAC_SIZE = 228


def _make_request(payload: bytes, hostinfo: str = "127.0.0.1:5683") -> MagicMock:
    """Create a mock CoAP request object."""
    req = MagicMock()
    req.payload = payload
    req.remote = MagicMock()
    req.remote.hostinfo = hostinfo
    return req


# ===========================================================================
# TestCoapTransport
# ===========================================================================

class TestCoapTransport(unittest.IsolatedAsyncioTestCase):

    def _get_resource(self, on_record_callback):
        from vapi_bridge.transports.coap import PoACResource
        return PoACResource(on_record_callback)

    async def test_1_228_byte_payload_returns_changed_and_invokes_callback(self):
        """228-byte payload → render_post returns CHANGED (2.04) + callback invoked."""
        received = []

        async def on_record(raw, source):
            received.append((raw, source))

        resource = self._get_resource(on_record)
        request = _make_request(b"\xab" * POAC_SIZE)
        response = await resource.render_post(request)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], b"\xab" * POAC_SIZE)
        self.assertEqual(response.code, _CHANGED)

    async def test_2_wrong_size_returns_bad_request_no_callback(self):
        """Wrong-size payload → BAD_REQUEST (4.00) + callback NOT invoked."""
        called = []

        async def on_record(raw, source):
            called.append(raw)

        resource = self._get_resource(on_record)
        request = _make_request(b"\x00" * 100)
        response = await resource.render_post(request)

        self.assertEqual(len(called), 0)
        self.assertEqual(response.code, _BAD_REQUEST)

    async def test_3_exception_returns_internal_server_error_with_truncated_message(self):
        """Exception in on_record → INTERNAL_SERVER_ERROR, payload message ≤ 100 chars."""
        long_error = "A" * 200

        async def on_record_raises(raw, source):
            raise RuntimeError(long_error)

        resource = self._get_resource(on_record_raises)
        request = _make_request(b"\x00" * POAC_SIZE)
        response = await resource.render_post(request)

        self.assertEqual(response.code, _INTERNAL_SERVER_ERROR)
        if response.payload:
            self.assertLessEqual(len(response.payload.decode("utf-8", errors="replace")), 100)

    async def test_4_source_string_format(self):
        """Source string is 'coap:{hostinfo}'."""
        received_sources = []

        async def on_record(raw, source):
            received_sources.append(source)

        resource = self._get_resource(on_record)
        request = _make_request(b"\x00" * POAC_SIZE, hostinfo="192.168.1.1:5683")
        await resource.render_post(request)

        self.assertEqual(len(received_sources), 1)
        self.assertIn("coap:", received_sources[0])
        self.assertIn("192.168.1.1", received_sources[0])

    async def test_5_coap_transport_run_binds_to_configured_host_port(self):
        """CoapTransport.run() creates a context bound to cfg.coap_bind / cfg.coap_port."""
        from vapi_bridge.transports.coap import CoapTransport

        async def on_record(raw, source):
            pass

        cfg = MagicMock()
        cfg.coap_bind = "0.0.0.0"
        cfg.coap_port = 5683

        transport = CoapTransport(cfg, on_record)
        bind_calls = []

        class _TestContext:
            @staticmethod
            async def create_server_context(site, bind=None, **kwargs):
                bind_calls.append(bind)

                class _FakeCtx:
                    async def shutdown(self):
                        pass

                return _FakeCtx()

        original_ctx = sys.modules["aiocoap"].Context
        sys.modules["aiocoap"].Context = _TestContext
        try:
            task = asyncio.create_task(transport.run())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            sys.modules["aiocoap"].Context = original_ctx

        self.assertEqual(len(bind_calls), 1)
        self.assertEqual(bind_calls[0], ("0.0.0.0", 5683))

    async def test_6_cancelled_error_calls_context_shutdown(self):
        """CancelledError in run() → context.shutdown() called (graceful cleanup)."""
        from vapi_bridge.transports.coap import CoapTransport

        async def on_record(raw, source):
            pass

        cfg = MagicMock()
        cfg.coap_bind = "0.0.0.0"
        cfg.coap_port = 5683
        transport = CoapTransport(cfg, on_record)

        shutdown_called = []

        class _TestContext:
            @staticmethod
            async def create_server_context(site, bind=None, **kwargs):
                class _FakeCtx:
                    async def shutdown(self):
                        shutdown_called.append(True)

                return _FakeCtx()

        original_ctx = sys.modules["aiocoap"].Context
        sys.modules["aiocoap"].Context = _TestContext
        try:
            task = asyncio.create_task(transport.run())
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            sys.modules["aiocoap"].Context = original_ctx

        self.assertEqual(len(shutdown_called), 1)


if __name__ == "__main__":
    unittest.main()
