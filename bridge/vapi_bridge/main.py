"""
VAPI Bridge — Main entry point and orchestration.

Starts all enabled transports, the batcher, and the HTTP dashboard
as concurrent asyncio tasks. Handles graceful shutdown on SIGINT/SIGTERM.
"""

import asyncio
import logging
import signal
import sys

from .batcher import Batcher
from .chain import ChainClient
from .codec import (
    PoACRecord,
    compute_device_id,
    parse_record,
    verify_signature,
)
from .config import Config
from .store import Store

log = logging.getLogger(__name__)


def _log_startup_diagnostics(cfg):
    """Log readiness status for Phase 28/29 features (Phase 29).

    Purely informational — never raises, never blocks startup.
    """
    from pathlib import Path
    _dlog = logging.getLogger("vapi_bridge.startup")
    circuits_dir = Path(__file__).parents[2] / "contracts" / "circuits"
    for circuit in ("TeamProof", "PitlSessionProof"):
        zkey = circuits_dir / f"{circuit}_final.zkey"
        _dlog.info("ZK %s: %s", circuit, "READY" if zkey.exists() else "MISSING (.zkey not found — run contracts/scripts/run-ceremony.js)")
    _dlog.info("PHGCredential: %s", getattr(cfg, "phg_credential_address", "") or "NOT SET")
    _dlog.info("OperatorAPI: %s", "ENABLED" if getattr(cfg, "operator_api_key", "") else "DISABLED (set OPERATOR_API_KEY to enable)")


class Bridge:
    """Top-level orchestrator for the VAPI bridge service."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.chain = ChainClient(cfg)
        self.batcher = Batcher(cfg, self.store, self.chain)
        self._tasks: list[asyncio.Task] = []
        self._ds_transport = None  # DualShockTransport, set in run() if dualshock_enabled

    async def on_record(self, raw_data: bytes, source: str):
        """
        Unified callback for all transports.

        Parses the record, validates it, persists it, and enqueues for batching.
        """
        # 1. Parse
        try:
            record = parse_record(raw_data)
        except ValueError as e:
            log.warning("Invalid record from %s: %s", source, e)
            raise

        # 2. Look up device public key
        # Try local store first, then on-chain registry
        device_id_hex = record.record_hash_hex  # placeholder until we have pubkey
        pubkey_hex = None

        # If we know this device, use cached pubkey
        # For now, try to fetch from on-chain registry
        # The device_id is keccak256(pubkey) but we need the pubkey to verify the sig
        # Chicken-and-egg: we need a device registration step or the pubkey in the payload

        # Strategy: devices must be pre-registered via the dashboard or auto-discovered
        # from on-chain DeviceRegistry. For first contact, we accept unverified records
        # and flag them for manual review.

        # Check if we can find the pubkey from any known device
        # whose chain state matches this record's prev_hash
        pubkey_bytes = await self._resolve_pubkey(record, source)

        # Tag schema version for known transports (Gate Fix A, Phase 19: from profile)
        if source == "dualshock":
            if (self._ds_transport is not None
                    and getattr(self._ds_transport, "_device_profile", None) is not None):
                record.schema_version = self._ds_transport._device_profile.schema_version
            else:
                record.schema_version = 2  # backward-compat fallback

            # Phase 21: apply PITL sidecar metadata from DualShockTransport
            pitl_meta = getattr(self._ds_transport, "_pending_pitl_meta", None) \
                if self._ds_transport is not None else None
            if pitl_meta:
                record.pitl_l4_distance        = pitl_meta.get("l4_distance")
                record.pitl_l4_warmed_up       = pitl_meta.get("l4_warmed_up")
                record.pitl_l4_features_json   = pitl_meta.get("l4_features_json")
                record.pitl_l5_cv              = pitl_meta.get("l5_cv")
                record.pitl_l5_entropy_bits    = pitl_meta.get("l5_entropy_bits")
                record.pitl_l5_quant_score     = pitl_meta.get("l5_quant_score")
                record.pitl_l5_anomaly_signals = pitl_meta.get("l5_anomaly_signals")
                # Phase 25: agent intelligence sidecar fields
                record.pitl_l5_rhythm_humanity = pitl_meta.get("l5_rhythm_humanity")
                record.pitl_l4_drift_velocity  = pitl_meta.get("l4_drift_velocity")
                record.pitl_e4_cognitive_drift = pitl_meta.get("e4_cognitive_drift")
                record.pitl_humanity_prob      = pitl_meta.get("humanity_prob")

        if pubkey_bytes:
            device_id = compute_device_id(pubkey_bytes)
            record.device_id = device_id

            # 3. Verify signature
            if not verify_signature(record, pubkey_bytes):
                log.warning(
                    "Signature verification FAILED: device=%s counter=%d source=%s",
                    device_id.hex()[:16], record.monotonic_ctr, source,
                )
                raise ValueError("Invalid signature")

            log.info(
                "Record verified: device=%s counter=%d action=%s conf=%d src=%s",
                device_id.hex()[:16], record.monotonic_ctr,
                record.action_name, record.confidence, source,
            )

            # Update device state
            self.store.upsert_device(device_id.hex(), pubkey_bytes.hex())
            self.store.update_device_state(device_id.hex(), record)
        else:
            # No pubkey available — accept but log warning
            # Use record_hash as a temporary device_id for tracking
            record.device_id = record.record_hash
            log.warning(
                "No pubkey for record counter=%d — accepted unverified from %s",
                record.monotonic_ctr, source,
            )
            self.store.upsert_device(record.record_hash_hex, "unknown")
            self.store.update_device_state(record.record_hash_hex, record)

        # 4. Persist
        is_new = self.store.insert_record(record, raw_data)
        if not is_new:
            return  # Duplicate, skip

        # 5. Broadcast to WebSocket clients (Phase 21 — non-blocking, best-effort)
        try:
            from .transports.http import ws_broadcast, _record_to_ws_msg
            asyncio.create_task(ws_broadcast(_record_to_ws_msg(record)))
        except Exception:
            pass

        # 6. Enqueue for batching
        await self.batcher.enqueue(record, raw_data)

    async def _resolve_pubkey(
        self, record: PoACRecord, source: str
    ) -> bytes | None:
        """
        Attempt to find the public key for a record's signing device.

        Resolution order:
        1. Local store (device whose chain_head matches record.prev_poac_hash)
        2. On-chain DeviceRegistry
        """
        # Check all known devices for chain continuity
        devices = self.store.list_devices()
        for dev in devices:
            if dev["pubkey_hex"] == "unknown":
                continue
            if dev["chain_head"] == record.prev_poac_hash.hex():
                return bytes.fromhex(dev["pubkey_hex"])

        # For genesis records (prev_hash all zeros), check all registered devices
        if record.prev_poac_hash == b"\x00" * 32:
            for dev in devices:
                if dev["pubkey_hex"] != "unknown":
                    return bytes.fromhex(dev["pubkey_hex"])

        # Try on-chain registry (brute-force check is impractical; need device_id)
        # In production, the uplink message should include the device_id as a header
        return None

    async def run(self):
        """Start all services and run until shutdown."""
        # Validate configuration
        errors = self.cfg.validate()
        if errors:
            for err in errors:
                log.error("Config error: %s", err)
            sys.exit(1)

        log.info("=" * 60)
        log.info("VAPI Bridge v0.2.0-rc1 starting")
        log.info("=" * 60)
        _log_startup_diagnostics(self.cfg)
        log.info("IoTeX RPC: %s (chain_id=%d)", self.cfg.iotex_rpc_url, self.cfg.chain_id)
        log.info("Bridge wallet: %s", self.chain.bridge_address)
        log.info("Verifier: %s", self.cfg.verifier_address)
        if self.cfg.bounty_market_address:
            log.info("BountyMarket: %s", self.cfg.bounty_market_address)
        if self.cfg.device_registry_address:
            log.info("DeviceRegistry: %s", self.cfg.device_registry_address)
        log.info("Database: %s", self.cfg.db_path)

        try:
            balance = await self.chain.get_balance()
            log.info("Bridge balance: %.4f IOTX", balance)
            if balance < 1.0:
                log.warning("Low balance — bridge may fail to submit transactions")
        except Exception as e:
            log.warning("Could not fetch balance: %s", e)

        # Start batcher
        self._tasks.append(asyncio.create_task(self.batcher.run()))

        # Start manufacturer revocation listener (Gate Fix G3)
        if self.cfg.device_registry_address:
            self._tasks.append(asyncio.create_task(
                self.chain.watch_manufacturer_revocations()
            ))
            log.info("Manufacturer revocation listener started")

        # Start enabled transports
        if self.cfg.mqtt_enabled:
            from .transports.mqtt import MqttTransport
            mqtt = MqttTransport(self.cfg, self.on_record)
            self._tasks.append(asyncio.create_task(mqtt.run()))

        if self.cfg.coap_enabled:
            from .transports.coap import CoapTransport
            coap = CoapTransport(self.cfg, self.on_record)
            self._tasks.append(asyncio.create_task(coap.run()))

        # Phase 32: Hoist intelligence modules so ProactiveMonitor and HTTP dashboard share instances
        from .behavioral_archaeologist import BehavioralArchaeologist
        from .continuity_prover import ContinuityProver
        from .network_correlation_detector import NetworkCorrelationDetector
        _arch = BehavioralArchaeologist(self.store)
        _prover = ContinuityProver(self.store)
        _net_det = NetworkCorrelationDetector(self.store, _prover)

        # Phase 32: Eagerly create BridgeAgent with cross-device intelligence injection
        _agent_instance = None
        if getattr(self.cfg, "operator_api_key", ""):
            try:
                from .bridge_agent import BridgeAgent
                _agent_instance = BridgeAgent(
                    self.cfg, self.store,
                    behavioral_arch=_arch,
                    network_detector=_net_det,
                )
                log.info("BridgeAgent initialized eagerly with cross-device intelligence (Phase 32)")
            except ImportError:
                log.warning("anthropic not installed — BridgeAgent disabled")

        if self.cfg.http_enabled:
            from .transports.http import create_app
            from .monitoring import create_monitoring_app, state as monitor_state
            from .dashboard_api import create_dashboard_app
            from .operator_api import create_operator_app
            import uvicorn

            app = create_app(self.cfg, self.store, self.on_record)
            mon_app = create_monitoring_app(cfg=self.cfg, state=monitor_state, store=self.store)
            app.mount("/monitor", mon_app)
            app.mount("/dash", create_dashboard_app(self.store, _arch, _net_det))
            app.mount("/operator", create_operator_app(self.cfg, self.store, _agent=_agent_instance))
            config = uvicorn.Config(
                app,
                host=self.cfg.http_host,
                port=self.cfg.http_port,
                log_level=self.cfg.log_level.lower(),
                access_log=False,
            )
            server = uvicorn.Server(config)
            self._tasks.append(asyncio.create_task(server.serve()))

        if self.cfg.dualshock_enabled:
            from .dualshock_integration import DualShockTransport
            from .continuity_prover import ContinuityProver
            from .pitl_prover import PITLProver, PITL_ZK_ARTIFACTS_AVAILABLE
            ds = DualShockTransport(self.cfg, self.store, self.on_record, self.chain)
            # Phase 23: inject continuity prover when Identity Registry is configured
            if getattr(self.cfg, "identity_registry_address", ""):
                ds._continuity_prover = ContinuityProver(
                    self.store,
                    threshold=getattr(self.cfg, "continuity_threshold", 2.0),
                )
                log.info(
                    "Phase 23: ContinuityProver active (threshold=%.1f)",
                    self.cfg.continuity_threshold,
                )
            # Phase 27: inject PITLProver for session-end ZK proof generation (always active)
            ds._pitl_prover = PITLProver()
            log.info("Phase 27: PITLProver injected (zk_artifacts=%s)", PITL_ZK_ARTIFACTS_AVAILABLE)
            self._ds_transport = ds
            self._tasks.append(asyncio.create_task(ds.run()))
            log.info("DualShock Edge transport enabled (interval=%.1fs)",
                     self.cfg.dualshock_record_interval_s)

            # Phase 26: WorldModelAttestation startup check (fix: _device_id_hex → _device_id.hex())
            _ewc = getattr(ds, "_ewc_model", None)
            _dev_id_hex = ds._device_id.hex() if hasattr(ds, "_device_id") and ds._device_id else None
            if _ewc is not None and _dev_id_hex:
                from .world_model_attestation import WorldModelAttestation
                _attest = WorldModelAttestation(self.store, _ewc)
                _ok, _reason = _attest.verify_current_weights(_dev_id_hex)
                if not _ok:
                    log.critical(
                        "EWC WEIGHT MISMATCH: %s — possible model poisoning attack!",
                        _reason,
                    )
                else:
                    log.info("WorldModelAttestation: %s", _reason)

        # Phase 25: Start chain reconciler for PHG checkpoint confirmation
        if getattr(self.cfg, "phg_registry_address", ""):
            from .chain_reconciler import ChainReconciler
            reconciler = ChainReconciler(
                self.store,
                self.chain,
                poll_interval=getattr(self.cfg, "reconciler_poll_interval", 30.0),
            )
            self._tasks.append(asyncio.create_task(reconciler.run()))
            log.info(
                "Phase 25: ChainReconciler started (interval=%.0fs)",
                getattr(self.cfg, "reconciler_poll_interval", 30.0),
            )

        # Phase 32: Start ProactiveMonitor — autonomous protocol surveillance
        if getattr(self.cfg, "operator_api_key", "") and _agent_instance is not None:
            from .proactive_monitor import ProactiveMonitor
            # Phase 17: Auto-calibration agent (4th ProactiveMonitor surveillance check)
            _calibration_agent = None
            try:
                from .calibration_agent import CalibrationAgent
                _calibration_agent = CalibrationAgent(store=self.store, cfg=self.cfg)
                log.info("Phase 17: CalibrationAgent attached to ProactiveMonitor")
            except Exception as _cal_exc:
                log.warning("CalibrationAgent unavailable: %s", _cal_exc)
            monitor = ProactiveMonitor(
                self.store, _arch, _net_det, _agent_instance, self.cfg,
                poll_interval=getattr(self.cfg, "monitor_poll_interval", 60.0),
                calibration_agent=_calibration_agent,
            )
            self._tasks.append(asyncio.create_task(monitor.run()))
            log.info(
                "Phase 32: ProactiveMonitor started (interval=%.0fs)",
                getattr(self.cfg, "monitor_poll_interval", 60.0),
            )

        # Phase 34: Start FederationBus — cross-bridge cluster correlation
        if getattr(self.cfg, "federation_peers", ""):
            try:
                import httpx  # validate httpx available before creating task
                from .federation_bus import FederationBus
                _fed_interval = getattr(self.cfg, "federation_poll_interval", 120.0)
                fed_bus = FederationBus(
                    self.store, _net_det, self.chain, self.cfg,
                    poll_interval=_fed_interval,
                )
                self._tasks.append(asyncio.create_task(fed_bus.run()))
                log.info(
                    "Phase 34: FederationBus started (interval=%.0fs)",
                    _fed_interval,
                )
            except ImportError:
                log.warning("Phase 34: httpx not installed — FederationBus disabled")

        # Phase 35: InsightSynthesizer — longitudinal synthesis, always starts (no guard)
        from .insight_synthesizer import InsightSynthesizer
        _synth_interval = getattr(self.cfg, "synthesizer_poll_interval", 21600.0)
        synth = InsightSynthesizer(self.store, self.cfg, poll_interval=_synth_interval,
                                   chain=self.chain)
        self._tasks.append(asyncio.create_task(synth.run()))
        log.info("Phase 35: InsightSynthesizer started (interval=%.0fs)", _synth_interval)
        log.info(
            "Phase 36: Adaptive feedback loop active (floor=%.2f)",
            getattr(self.cfg, "policy_multiplier_floor", 0.5),
        )

        # Phase 37: AlertRouter — webhook dispatch for enforcement events (always starts)
        from .alert_router import AlertRouter
        _alert_router = AlertRouter(self.cfg, self.store)
        self._tasks.append(asyncio.create_task(_alert_router.run()))
        log.info(
            "Phase 37: AlertRouter started (threshold=%s)",
            getattr(self.cfg, "alert_severity_threshold", "medium"),
        )
        log.info(
            "Phase 37: Credential enforcement active (min_consecutive=%d, base=%.0fd)",
            getattr(self.cfg, "credential_enforcement_min_consecutive", 2),
            getattr(self.cfg, "credential_suspension_base_days", 7.0),
        )

        log.info("All services started — bridge is operational")

        # Wait for shutdown
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    def shutdown(self):
        """Cancel all running tasks."""
        log.info("Shutdown requested")
        for task in self._tasks:
            task.cancel()


def main():
    """CLI entry point."""
    cfg = Config()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    bridge = Bridge(cfg)

    # Handle signals for graceful shutdown
    loop = asyncio.new_event_loop()
    for sig_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig_name, bridge.shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        loop.run_until_complete(bridge.run())
    except KeyboardInterrupt:
        bridge.shutdown()
        loop.run_until_complete(asyncio.sleep(1))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
