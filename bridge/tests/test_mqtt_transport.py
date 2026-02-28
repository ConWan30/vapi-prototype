"""
Phase 28 — MQTT Transport Tests

TestMqttTransport (8):
1.  228-byte payload → on_record callback invoked with correct bytes
2.  source string format is "mqtt:{topic_name}"
3.  100-byte payload → callback NOT invoked (too short)
4.  229-byte payload → callback NOT invoked (too long, boundary)
5.  on_record exception → logged, transport continues (resilient)
6.  MqttError → reconnect attempted after 5s delay
7.  CancelledError → clean shutdown (no exception propagation)
8.  multiple 228-byte messages → callback invoked once per message
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BRIDGE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(BRIDGE_DIR))

# Stub aiomqtt (the actual library used by mqtt.py) before imports
_aiomqtt = types.ModuleType("aiomqtt")

class _MqttError(Exception):
    pass

_aiomqtt.MqttError = _MqttError
_aiomqtt.Client = MagicMock()  # patch() requires attribute to exist before patching
sys.modules.setdefault("aiomqtt", _aiomqtt)

for _mod in ("web3", "web3.exceptions", "eth_account", "hidapi", "hid", "pydualsense"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

POAC_SIZE = 228


def _make_message(payload: bytes, topic: str = "vapi/poac/test") -> MagicMock:
    """Create a mock aiomqtt message."""
    msg = MagicMock()
    msg.payload = payload
    msg.topic = topic
    return msg


# ===========================================================================
# TestMqttTransport
# ===========================================================================

class TestMqttTransport(unittest.IsolatedAsyncioTestCase):

    def _get_transport(self, on_record_callback):
        from vapi_bridge.transports.mqtt import MqttTransport
        cfg = MagicMock()
        cfg.mqtt_broker = "localhost"
        cfg.mqtt_port = 1883
        cfg.mqtt_topic_prefix = "vapi/poac"
        cfg.mqtt_username = ""
        cfg.mqtt_password = ""
        return MqttTransport(cfg, on_record_callback)

    async def test_1_228_byte_payload_invokes_on_record(self):
        """228-byte payload → on_record callback invoked with correct bytes."""
        received = []

        async def on_record(raw, source):
            received.append((raw, source))

        transport = self._get_transport(on_record)
        msg = _make_message(b"\xaa" * POAC_SIZE, topic="vapi/poac/device1")
        await transport._handle_message(msg)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], b"\xaa" * POAC_SIZE)

    async def test_2_source_string_format(self):
        """Source string is 'mqtt:{topic_name}'."""
        received = []

        async def on_record(raw, source):
            received.append(source)

        transport = self._get_transport(on_record)
        msg = _make_message(b"\x00" * POAC_SIZE, topic="vapi/poac/my_device")
        await transport._handle_message(msg)

        self.assertEqual(len(received), 1)
        self.assertIn("mqtt:", received[0])
        self.assertIn("vapi/poac/my_device", received[0])

    async def test_3_100_byte_payload_not_forwarded(self):
        """100-byte payload → callback NOT invoked (too short)."""
        called = []

        async def on_record(raw, source):
            called.append(raw)

        transport = self._get_transport(on_record)
        msg = _make_message(b"\xff" * 100)
        await transport._handle_message(msg)
        self.assertEqual(len(called), 0)

    async def test_4_229_byte_payload_not_forwarded(self):
        """229-byte payload → callback NOT invoked (one byte over, boundary check)."""
        called = []

        async def on_record(raw, source):
            called.append(raw)

        transport = self._get_transport(on_record)
        msg = _make_message(b"\x00" * 229)
        await transport._handle_message(msg)
        self.assertEqual(len(called), 0)

    async def test_5_on_record_exception_does_not_propagate(self):
        """on_record exception is caught and logged; transport continues."""
        async def on_record_raises(raw, source):
            raise ValueError("deliberate test error")

        transport = self._get_transport(on_record_raises)
        msg = _make_message(b"\x01" * POAC_SIZE)
        # Should not raise
        await transport._handle_message(msg)

    async def test_6_mqtt_error_triggers_reconnect(self):
        """MqttError during run() causes a reconnect attempt (sleep + retry loop)."""
        attempt_count = [0]

        async def on_record(raw, source):
            pass

        transport = self._get_transport(on_record)

        # Patch Client to raise MqttError on first connect, then CancelledError
        class _FakeClient:
            async def __aenter__(self):
                attempt_count[0] += 1
                if attempt_count[0] == 1:
                    raise _MqttError("simulated disconnect")
                raise asyncio.CancelledError()

            async def __aexit__(self, *args):
                pass

        with patch("aiomqtt.Client", return_value=_FakeClient()), \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            try:
                await transport.run()
            except asyncio.CancelledError:
                pass

        # After MqttError, sleep(5) was called before retry
        mock_sleep.assert_called_with(5)
        self.assertEqual(attempt_count[0], 2)

    async def test_7_cancelled_error_clean_shutdown(self):
        """CancelledError during run() is re-raised (caller handles it)."""
        async def on_record(raw, source):
            pass

        transport = self._get_transport(on_record)

        class _FakeClientCancel:
            async def __aenter__(self):
                raise asyncio.CancelledError()

            async def __aexit__(self, *args):
                pass

        with patch("aiomqtt.Client", return_value=_FakeClientCancel()):
            # CancelledError propagates (mqtt.py re-raises it)
            with self.assertRaises(asyncio.CancelledError):
                await transport.run()

    async def test_8_multiple_messages_each_invoke_callback_once(self):
        """Multiple 228-byte messages each invoke callback exactly once."""
        received = []

        async def on_record(raw, source):
            received.append(raw)

        transport = self._get_transport(on_record)
        for i in range(5):
            msg = _make_message(bytes([i]) * POAC_SIZE, topic=f"vapi/poac/dev{i}")
            await transport._handle_message(msg)

        self.assertEqual(len(received), 5)
        for i, raw in enumerate(received):
            self.assertEqual(raw, bytes([i]) * POAC_SIZE)


if __name__ == "__main__":
    unittest.main()
