"""
MQTT Transport — Listens for PoAC records on MQTT broker.

Topic scheme:
  vapi/poac/{device_id_hex}  — 228-byte binary PoAC record payload
  vapi/status/{device_id_hex} — JSON status heartbeat (optional)

Compatible with the Pebble's NB-IoT → MQTT bridge (e.g., AWS IoT Core,
HiveMQ, Mosquitto).
"""

import asyncio
import logging

import aiomqtt

from ..codec import PoACRecord, parse_record, POAC_RECORD_SIZE
from ..config import Config

log = logging.getLogger(__name__)


class MqttTransport:
    """Async MQTT listener for PoAC records."""

    def __init__(self, cfg: Config, on_record):
        """
        Args:
            cfg: Bridge configuration.
            on_record: Async callback(raw_data: bytes, source: str) for each record.
        """
        self._cfg = cfg
        self._on_record = on_record

    async def run(self):
        """Connect to MQTT broker and listen for records."""
        topic = f"{self._cfg.mqtt_topic_prefix}/#"
        log.info(
            "MQTT connecting to %s:%d (topic=%s)",
            self._cfg.mqtt_broker, self._cfg.mqtt_port, topic,
        )

        while True:
            try:
                async with aiomqtt.Client(
                    hostname=self._cfg.mqtt_broker,
                    port=self._cfg.mqtt_port,
                    username=self._cfg.mqtt_username or None,
                    password=self._cfg.mqtt_password or None,
                ) as client:
                    await client.subscribe(topic)
                    log.info("MQTT subscribed to %s", topic)

                    async for message in client.messages:
                        await self._handle_message(message)

            except aiomqtt.MqttError as e:
                log.error("MQTT connection lost: %s — reconnecting in 5s", e)
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                log.info("MQTT transport shutting down")
                raise

    async def _handle_message(self, message):
        """Process a single MQTT message."""
        payload = message.payload
        topic = str(message.topic)

        if len(payload) == POAC_RECORD_SIZE:
            source = f"mqtt:{topic}"
            try:
                await self._on_record(bytes(payload), source)
            except Exception as e:
                log.error("Error processing MQTT record: %s", e)
        elif len(payload) > 0:
            log.debug(
                "MQTT: ignoring %d-byte message on %s (expected %d)",
                len(payload), topic, POAC_RECORD_SIZE,
            )
