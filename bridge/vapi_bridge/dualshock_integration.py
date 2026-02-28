"""
VAPI DualShock Edge Integration Transport

Primary certified PHCI device transport. The DualSense Edge (CFI-ZCP1) is the
flagship VAPI node — its adaptive trigger detection surface (L2/R2 resistance
dynamics) provides Proof of Human Gaming signals that software injection cannot
replicate. Bridges a live DualSense Edge controller into the VAPI bridge pipeline
for full on-chain PoAC verification, bounty fulfillment, SkillOracle updates,
ProgressAttestation, and TeamProofAggregator support.

Architecture:
    DualSenseReader (HID, sync thread)
        -> AntiCheatClassifier (6-class inference)
        -> PersistentPoACEngine (228-byte ECDSA-P256, stable device_id)
        -> Bridge.on_record("dualshock")
        -> Batcher -> IoTeX chain:
             PoACVerifier.verifyPoACBatch()
             BountyMarket.submitEvidence()      (if bounty_id > 0)
             SkillOracle.updateRating()         (session end)
             ProgressAttestation.attestProgress() (if improvement detected)

Gaming inference codes (VAPI protocol extension, 0x20-0x2A):
    0x20 NOMINAL        0x21 SKILLED
    0x22 CHEAT:REACTION 0x23 CHEAT:MACRO
    0x24 CHEAT:AIMBOT   0x25 CHEAT:RECOIL
    0x26 CHEAT:IMU_MISS 0x27 CHEAT:INJECTION

Phase 8 codes (Physical Input Trust Layer):
    0x28 DRIVER_INJECT      HID-XInput pipeline injection (Layer 2)
    0x29 WALLHACK_PREAIM    Behavioral wallhack pre-aim (Layer 3)
    0x2A AIMBOT_BEHAVIORAL  Behavioral aimbot lock-on (Layer 3)

SkillOracle ELO logic mirrors SkillOracle.sol exactly:
    NOMINAL gain  = max(1, floor(5  * confidence / 255))
    SKILLED gain  = max(1, floor(12 * confidence / 255))
    CHEAT penalty = -200 (hard)   Rating in [0, 3000]

ProgressAttestation BPS formula (ACCURACY metric):
    baseline_conf = avg confidence of NOMINAL records in first window
    current_conf  = avg confidence of NOMINAL records in last window
    improvement_bps = round((current - baseline) / baseline * 10000)
"""

import asyncio
import hashlib
import json
import logging
import math as _math
import struct
import sys
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .codec import compute_device_id, parse_record
from .config import Config
from .continuity_prover import FEATURE_KEYS
from .store import Store

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gaming inference codes (extension of base VAPI protocol)
# ---------------------------------------------------------------------------
INFER_NOMINAL   = 0x20
INFER_SKILLED   = 0x21
INFER_CHEAT_RXN = 0x22
INFER_CHEAT_MAC = 0x23
INFER_CHEAT_AIM = 0x24
INFER_CHEAT_REC = 0x25
INFER_CHEAT_IMU = 0x26
INFER_CHEAT_INJ = 0x27
# Phase 8: Physical Input Trust Layer
INFER_DRIVER_INJECT     = 0x28  # HID-XInput pipeline injection
INFER_WALLHACK_PREAIM   = 0x29  # Behavioral wallhack pre-aim
INFER_AIMBOT_BEHAVIORAL = 0x2A  # Behavioral aimbot lock-on
CHEAT_CODES = {INFER_CHEAT_RXN, INFER_CHEAT_MAC, INFER_CHEAT_AIM,
               INFER_CHEAT_REC, INFER_CHEAT_IMU, INFER_CHEAT_INJ,
               INFER_DRIVER_INJECT, INFER_WALLHACK_PREAIM, INFER_AIMBOT_BEHAVIORAL}

GAMING_INFERENCE_NAMES = {
    INFER_NOMINAL:   "NOMINAL",        INFER_SKILLED:   "SKILLED",
    INFER_CHEAT_RXN: "CHEAT:REACTION", INFER_CHEAT_MAC: "CHEAT:MACRO",
    INFER_CHEAT_AIM: "CHEAT:AIMBOT",   INFER_CHEAT_REC: "CHEAT:RECOIL",
    INFER_CHEAT_IMU: "CHEAT:IMU_MISS", INFER_CHEAT_INJ: "CHEAT:INJECTION",
    # Phase 8
    INFER_DRIVER_INJECT:     "CHEAT:DRIVER_INJECT",
    INFER_WALLHACK_PREAIM:   "CHEAT:WALLHACK_PREAIM",
    INFER_AIMBOT_BEHAVIORAL: "CHEAT:AIMBOT_BEHAVIORAL",
    # Phase 13 E1: biometric soft anomaly (outside cheat range)
    0x30: "BIOMETRIC_ANOMALY",
    # Phase 16B: temporal rhythm advisory (outside cheat range)
    0x2B: "TEMPORAL_ANOMALY",
}

# PoAC action codes
ACTION_BOOT         = 0x09
ACTION_REPORT       = 0x01
ACTION_BOUNTY_CLAIM = 0x05

# Tier thresholds matching SkillOracle.sol
_TIER_NAMES = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"]
_TIER_THRESHOLDS = [0, 1000, 1500, 2000, 2500]

# Phase 13 E4: EWC session scheduling
_EWC_SESSION_INTERVAL = 30   # Update EWC every 30 loop iterations (~30s at 1s/iter)
_EWC_FISHER_INTERVAL  = 300  # Recompute Fisher every 300 iterations (~5min)

# SkillOracle minimal ABI (matches SkillOracle.sol)
_SKILL_ORACLE_ABI = [
    {
        "name": "updateRating",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_deviceId",   "type": "bytes32"},
            {"name": "_recordHash", "type": "bytes32"},
            {"name": "_inference",  "type": "uint8"},
            {"name": "_confidence", "type": "uint8"},
        ],
        "outputs": [{"name": "newRating", "type": "uint16"}],
    },
    {
        "name": "getRating",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [
            {"name": "rating", "type": "uint16"},
            {"name": "tier",   "type": "uint8"},
        ],
    },
]


def _rating_tier(rating: int) -> str:
    t = 0
    for i, thresh in enumerate(_TIER_THRESHOLDS):
        if rating >= thresh:
            t = i
    return _TIER_NAMES[t]


# ---------------------------------------------------------------------------
# SkillOracle tracker — mirrors contract logic locally, submits on-chain
# ---------------------------------------------------------------------------
class _SkillOracleTracker:
    """
    Local SkillOracle state tracker.

    Applies rating deltas in real-time (same formula as SkillOracle.sol) and
    optionally submits the final session update to chain at session end.
    """

    NOMINAL_GAIN  = 5
    SKILLED_GAIN  = 12
    CHEAT_PENALTY = 200
    INITIAL       = 1000
    MAX           = 3000

    def __init__(self, device_id: bytes, chain_client=None, oracle_address: str = ""):
        self._device_id    = device_id
        self._chain        = chain_client
        self._rating       = self.INITIAL
        self._records      = 0
        self._cheats       = 0
        self._oracle       = None

        if chain_client and oracle_address:
            try:
                w3 = chain_client._w3
                self._oracle = w3.eth.contract(
                    address=w3.to_checksum_address(oracle_address),
                    abi=_SKILL_ORACLE_ABI,
                )
                log.info("SkillOracle contract: %s...", oracle_address[:20])
            except Exception as exc:
                log.warning("SkillOracle init failed: %s", exc)

    def apply(self, inference: int, confidence: int) -> int:
        """Apply inference result locally. Returns updated rating."""
        if inference == INFER_NOMINAL:
            self._rating = min(self.MAX, self._rating + max(1, self.NOMINAL_GAIN * confidence // 255))
        elif inference == INFER_SKILLED:
            self._rating = min(self.MAX, self._rating + max(1, self.SKILLED_GAIN * confidence // 255))
        elif inference in CHEAT_CODES:
            self._rating = max(0, self._rating - self.CHEAT_PENALTY)
            self._cheats += 1
        self._records += 1
        return self._rating

    async def submit_session_update(self, record_hash: bytes, inference: int, confidence: int):
        """Submit final session rating update to SkillOracle contract."""
        if not self._oracle or not self._chain:
            return
        try:
            tx = await self._chain._send_tx(
                self._oracle.functions.updateRating,
                self._device_id,
                record_hash,
                inference,
                confidence,
            )
            log.info(
                "SkillOracle on-chain update: rating=%d tier=%s tx=%s...",
                self._rating, _rating_tier(self._rating), tx[:16],
            )
        except Exception as exc:
            log.warning("SkillOracle chain submit failed: %s", exc)

    @property
    def rating(self) -> int:
        return self._rating

    def summary(self) -> dict:
        return {
            "rating":           self._rating,
            "tier":             _rating_tier(self._rating),
            "records":          self._records,
            "cheats_detected":  self._cheats,
        }


# ---------------------------------------------------------------------------
# ProgressAttestation tracker
# ---------------------------------------------------------------------------

# MetricType enum values matching ProgressAttestation.sol
METRIC_REACTION_TIME   = 0
METRIC_ACCURACY        = 1
METRIC_CONSISTENCY     = 2
METRIC_COMBO_EXECUTION = 3

# Minimum clean records before we'll compute a progress attestation
_PA_MIN_WINDOW = 5


class _ProgressAttestationTracker:
    """
    Tracks session confidence history and detects measurable skill improvement.

    Uses ACCURACY (MetricType=1) as the primary metric: confidence from the
    anti-cheat classifier increases as the player's inputs become cleaner and
    more precise, providing a quantifiable proxy for aim/stick accuracy.

    BPS formula:
        baseline_conf = avg(confidence of first _PA_MIN_WINDOW NOMINAL records)
        current_conf  = avg(confidence of last  _PA_MIN_WINDOW NOMINAL records)
        improvement_bps = round((current_conf - baseline_conf) / baseline_conf * 10000)

    Attestation is submitted only if improvement_bps > 0 and both the
    baseline and current record hashes are verified on-chain.
    """

    def __init__(self, device_id: bytes, chain_client=None, attest_address: str = ""):
        self._device_id       = device_id
        self._chain           = chain_client
        self._attest_address  = attest_address
        self._clean_records   : list[tuple[bytes, int]] = []  # (record_hash, confidence)

    def record(self, record_hash: bytes, inference: int, confidence: int):
        """Register a generated record for progress tracking."""
        if inference in (INFER_NOMINAL, INFER_SKILLED):
            self._clean_records.append((record_hash, confidence))

    def can_attest(self) -> bool:
        return (
            bool(self._chain)
            and bool(self._attest_address)
            and len(self._clean_records) >= _PA_MIN_WINDOW * 2
        )

    def compute_improvement(self) -> tuple[bytes, bytes, int]:
        """
        Compute (baseline_hash, current_hash, improvement_bps).
        Returns (None, None, 0) if not enough data or no improvement.
        """
        if len(self._clean_records) < _PA_MIN_WINDOW * 2:
            return None, None, 0
        baseline_window = self._clean_records[:_PA_MIN_WINDOW]
        current_window  = self._clean_records[-_PA_MIN_WINDOW:]
        baseline_conf = sum(c for _, c in baseline_window) / _PA_MIN_WINDOW
        current_conf  = sum(c for _, c in current_window)  / _PA_MIN_WINDOW
        if baseline_conf <= 0 or current_conf <= baseline_conf:
            return None, None, 0
        bps = round((current_conf - baseline_conf) / baseline_conf * 10000)
        if bps <= 0:
            return None, None, 0
        return baseline_window[0][0], current_window[-1][0], bps

    async def submit(self) -> bool:
        """Submit attestation if improvement is detected. Returns True on success."""
        if not self.can_attest():
            return False
        baseline_hash, current_hash, bps = self.compute_improvement()
        if bps <= 0 or baseline_hash is None:
            log.debug("ProgressAttestation: no measurable improvement this session")
            return False
        try:
            tx = await self._chain.attest_progress(
                self._device_id,
                baseline_hash,
                current_hash,
                METRIC_ACCURACY,
                bps,
            )
            log.info(
                "ProgressAttestation submitted: metric=ACCURACY bps=%d tx=%s...",
                bps, tx[:16],
            )
            return True
        except Exception as exc:
            log.warning("ProgressAttestation failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# DualShock Transport
# ---------------------------------------------------------------------------
class DualShockTransport:
    """
    Full DualShock Edge -> VAPI Bridge transport layer.

    Connects to the DualSense Edge controller, streams PoAC records into the
    bridge's on_record() pipeline, and manages SkillOracle state for the session.

    Configuration (environment variables):
        DUALSHOCK_ENABLED              bool   Enable this transport (default: false)
        DUALSHOCK_RECORD_INTERVAL_S    float  Seconds between PoAC records (default: 1.0)
        SKILL_ORACLE_ADDRESS           str    SkillOracle contract address (optional)
        DUALSHOCK_ACTIVE_BOUNTIES      str    Comma-separated bounty IDs (optional)
    """

    def __init__(
        self,
        cfg: Config,
        store: Store,
        on_record_cb: Callable[[bytes, str], Awaitable[None]],
        chain_client=None,
    ):
        self._cfg         = cfg
        self._store       = store
        self._on_record   = on_record_cb
        self._chain       = chain_client
        self._interval    = float(getattr(cfg, "dualshock_record_interval_s", 1.0))
        self._oracle_addr = getattr(cfg, "skill_oracle_address", "")
        self._bounty_cfg  = getattr(cfg, "dualshock_active_bounties", "")
        self._key_dir     = Path(getattr(cfg, "dualshock_key_dir",
                                         str(Path.home() / ".vapi")))
        self._attest_addr = getattr(cfg, "progress_attestation_address", "")

        # Resolved at run-time
        self._reader      = None
        self._engine      = None
        self._classifier  = None
        self._device_id   : Optional[bytes] = None
        self._pubkey_hex  : Optional[str]   = None
        self._pubkey_bytes: Optional[bytes] = None
        self._identity    = None   # PersistentIdentity, set in _init_hardware
        self._oracle      : Optional[_SkillOracleTracker]        = None
        self._progress    : Optional[_ProgressAttestationTracker] = None
        self._last_raw    : Optional[bytes] = None   # last dispatched record bytes
        # Phase 8: Physical Input Trust Layer
        self._hid_oracle        = None   # HidXInputOracle, set in _init_hardware
        self._backend_classifier = None  # BackendCheatClassifier, set in _init_hardware
        # Phase 9: Hardware Signing Bridge
        self._signing_backend   = None   # SigningBackend, set in _init_hardware
        # Phase 10: Sensor commitment schema v2 — adaptive trigger resistance mode
        self._l2_effect_mode: int = 0   # TriggerMode ordinal; 0 = Off
        self._r2_effect_mode: int = 0
        # Phase 11: TriggerModes enum ref (set in _init_hardware if pydualsense available)
        self._TriggerModes = None
        # Phase 13: Agent capability enhancement modules (wired in _init_hardware)
        self._biometric_classifier = None   # BiometricFusionClassifier (E1)
        self._bio_extractor_cls    = None   # BiometricFeatureExtractor class ref (B3: not re-imported per loop)
        self._ewc_model            = None   # EWCWorldModel (E4)
        self._preference_model     = None   # PreferenceModel (E2)
        self._frame_buffer: list   = []     # Accumulated frames for EWC session update
        self._session_count: int   = 0      # Loop-iteration counter for EWC scheduling
        self._recent_session_vecs: list = []  # Last N session vectors for Fisher
        self._l2_mode_history: list[int] = []  # Last 16 L2 mode values for trigger_mode_hash
        self._r2_mode_history: list[int] = []  # Last 16 R2 mode values for trigger_mode_hash
        # Phase 16B: Layer 5 Temporal Rhythm Oracle
        self._temporal_oracle  = None   # TemporalRhythmOracle, set in _init_hardware
        # Phase 19: Universal Device Abstraction Layer
        self._device_profile   = None   # DeviceProfile, resolved in _init_hardware
        # Phase 21: PITL metadata sidecar — set each loop iteration, read by Bridge.on_record
        self._pending_pitl_meta: dict | None = None
        # Phase 23: Session Continuity
        self._continuity_prover = None  # ContinuityProver, injected by main.py
        self._warmup_attested   = False  # True after first continuity check fires
        # Phase 25: Agent intelligence
        self._drift_history: deque = deque(maxlen=20)  # E4 cognitive drift per EWC update
        self._continuity_lock: asyncio.Lock = asyncio.Lock()
        # Phase 27: ZK PITL session proof — injected by main.py
        self._pitl_prover = None  # PITLProver instance, None = proof generation disabled

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def run(self):
        """Start DualShock transport. Runs until cancelled."""
        log.info("DualShock transport initialising")

        # Import emulator classes (sync, in executor to avoid blocking)
        ok = await asyncio.get_event_loop().run_in_executor(None, self._init_hardware)
        if not ok:
            log.error("DualShock hardware init failed — transport disabled")
            return

        # Pre-register device in the bridge's SQLite store so on_record()
        # can resolve the pubkey for signature verification from the first record.
        self._register_device()

        # On-chain device registration — idempotent, runs once per identity.
        # Skipped on subsequent startups when is_chain_registered is True.
        if self._chain and self._identity and not self._identity.is_chain_registered:
            tier = getattr(self._cfg, "device_registration_tier", "Standard")
            proof_hex = getattr(self._cfg, "attestation_proof_hex", "")
            proof_bytes = bytes.fromhex(proof_hex) if proof_hex else b""
            cert_hash = b""
            if (self._signing_backend
                    and self._signing_backend.attestation_certificate_hash):
                cert_hash = self._signing_backend.attestation_certificate_hash
            registered, tx_hash = await self._chain.ensure_device_registered_tiered(
                self._device_id, self._pubkey_bytes,
                tier=tier, attestation_proof=proof_bytes,
                certificate_hash=cert_hash,
            )
            if registered and tx_hash:
                self._identity.mark_chain_registered(
                    tx_hash, self._cfg.device_registry_address, tier=tier
                )
        elif self._chain and self._identity:
            log.debug("Device already registered on-chain (from local record)")

        # SkillOracle tracker
        self._oracle = _SkillOracleTracker(
            device_id    = self._device_id,
            chain_client = self._chain,
            oracle_address = self._oracle_addr,
        )

        # ProgressAttestation tracker
        self._progress = _ProgressAttestationTracker(
            device_id      = self._device_id,
            chain_client   = self._chain,
            attest_address = self._attest_addr,
        )

        # Parse active bounty IDs from config
        active_bounties: list[int] = []
        for tok in self._bounty_cfg.split(","):
            tok = tok.strip()
            if tok.isdigit():
                active_bounties.append(int(tok))

        log.info(
            "DualShock transport ready | device=%s... | interval=%.1fs | bounties=%s",
            self._device_id.hex()[:16], self._interval,
            active_bounties or "none",
        )

        # Send BOOT record
        boot_raw = self._make_record(INFER_NOMINAL, ACTION_BOOT, 220, 0)
        if boot_raw:
            await self._dispatch(boot_raw)

        # Main session loop
        try:
            await self._session_loop(active_bounties)
        except asyncio.CancelledError:
            log.info("DualShock transport shutdown requested")
            await self._shutdown_cleanup()
            raise

    # ------------------------------------------------------------------
    # Initialisation helpers (sync — run in executor)
    # ------------------------------------------------------------------
    def _init_hardware(self) -> bool:
        """Import emulator module and connect controller. Returns success."""
        controller_dir = Path(__file__).parents[3] / "controller"
        if str(controller_dir) not in sys.path:
            sys.path.insert(0, str(controller_dir))

        try:
            from dualshock_emulator import (
                DualSenseReader,
                PoACEngine,
                AntiCheatClassifier,
            )
        except ImportError as exc:
            log.error("Cannot import dualshock_emulator: %s", exc)
            log.error("Expected path: %s", controller_dir)
            return False

        # Phase 9: Hardware Signing Backend — wire before PersistentIdentity
        backend_type = getattr(self._cfg, "identity_backend", "software")
        signing_backend = None
        try:
            from vapi_bridge.hardware_identity import create_backend
            if backend_type == "software":
                key_path = str(self._key_dir / "dualshock_device_key.json")
                signing_backend = create_backend("software", key_path=key_path)
            elif backend_type == "yubikey":
                signing_backend = create_backend(
                    "yubikey",
                    piv_slot=getattr(self._cfg, "yubikey_piv_slot", "9c"),
                )
            elif backend_type.startswith("atecc608"):
                signing_backend = create_backend(
                    "atecc608",
                    i2c_bus=getattr(self._cfg, "atecc608_i2c_bus", 1),
                )
            else:
                log.warning(
                    "Unknown IDENTITY_BACKEND=%r — falling back to software", backend_type
                )
                key_path = str(self._key_dir / "dualshock_device_key.json")
                signing_backend = create_backend("software", key_path=key_path)
            signing_backend.setup()
            log.info(
                "Signing backend: type=%s hardware=%s",
                signing_backend.backend_type,
                signing_backend.is_hardware_backed,
            )
        except Exception as exc:
            log.warning("Hardware backend init failed (%s) — software fallback", exc)
            try:
                from vapi_bridge.hardware_identity import create_backend
                key_path = str(self._key_dir / "dualshock_device_key.json")
                signing_backend = create_backend("software", key_path=key_path)
                signing_backend.setup()
            except Exception as exc2:
                log.error("Software fallback also failed: %s", exc2)
                signing_backend = None

        self._signing_backend = signing_backend

        # Load or create the persistent device identity.
        # PersistentPoACEngine wraps PoACEngine with a stable key so
        # device_id = keccak256(pubkey) is consistent across restarts.
        try:
            import sys as _sys
            controller_dir_str = str(controller_dir)
            if controller_dir_str not in _sys.path:
                _sys.path.insert(0, controller_dir_str)
            from persistent_identity import PersistentIdentity
            identity = PersistentIdentity(
                key_dir=self._key_dir,
                signing_backend=signing_backend,
            ).load_or_create()
            self._engine       = identity.make_engine()
            self._identity     = identity
            self._pubkey_bytes = identity.public_key_bytes
            self._device_id    = identity.device_id
            log.info("Using persistent device identity: %s...", self._device_id.hex()[:16])
        except Exception as exc:
            log.warning("Persistent identity unavailable (%s) — using ephemeral key", exc)
            self._engine = PoACEngine()
            self._identity = None
            self._pubkey_bytes = getattr(self._engine, "public_key_bytes", None)
            if not self._pubkey_bytes or len(self._pubkey_bytes) != 65:
                log.error("PoACEngine did not expose valid public_key_bytes")
                return False
            self._device_id = compute_device_id(self._pubkey_bytes)

        self._pubkey_hex = self._pubkey_bytes.hex()
        self._classifier = AntiCheatClassifier()
        self._reader     = DualSenseReader()

        # Phase 11: Try to import TriggerModes enum for ordinal mapping in set_trigger_effect
        try:
            from pydualsense import TriggerModes
            self._TriggerModes = TriggerModes
            log.debug("TriggerModes loaded: %d modes available", len(list(TriggerModes)))
        except (ImportError, Exception):
            self._TriggerModes = None
            log.debug("pydualsense TriggerModes unavailable; trigger effect tracking via set_trigger_effect only")

        connected = self._reader.connect()
        if connected:
            log.info("DualSense Edge connected (device_id=%s...)", self._device_id.hex()[:16])
        else:
            log.warning("DualSense Edge not found — running in simulation mode")

        # Phase 8: HID-XInput oracle (Layer 2)
        if getattr(self._cfg, "hid_oracle_enabled", False):
            try:
                from bridge.vapi_bridge.hid_xinput_oracle import HidXInputOracle
            except ImportError:
                try:
                    from vapi_bridge.hid_xinput_oracle import HidXInputOracle
                except ImportError:
                    HidXInputOracle = None  # type: ignore

            if HidXInputOracle is not None:
                try:
                    self._hid_oracle = HidXInputOracle(
                        threshold=getattr(self._cfg, "hid_oracle_threshold", 0.15),
                        gamepad_index=getattr(self._cfg, "hid_oracle_gamepad_index", 0),
                    )
                    log.info(
                        "HidXInputOracle enabled (available=%s)",
                        self._hid_oracle.available,
                    )
                except Exception as exc:
                    log.warning("HidXInputOracle init failed: %s — oracle disabled", exc)

        # Phase 8: Backend behavioral cheat classifier (Layer 3)
        if getattr(self._cfg, "backend_cheat_enabled", False):
            try:
                controller_dir_str = str(Path(__file__).parents[3] / "controller")
                if controller_dir_str not in sys.path:
                    sys.path.insert(0, controller_dir_str)
                from tinyml_backend_cheat import BackendCheatClassifier
                self._backend_classifier = BackendCheatClassifier()
                model_path = getattr(self._cfg, "backend_cheat_model_path", "")
                if model_path:
                    self._backend_classifier.load_model(model_path)
                log.info("BackendCheatClassifier enabled")
            except Exception as exc:
                log.warning("BackendCheatClassifier init failed: %s — disabled", exc)

        # Phase 13: Enhancement modules (E1 biometric, E4 world model, E2 preference)
        try:
            from tinyml_biometric_fusion import (
                BiometricFusionClassifier, BIOMETRIC_MODEL_MANIFEST_HASH,
                BiometricFeatureExtractor,
            )
            from world_model_continual import EWCWorldModel
            from knapsack_personalized import PreferenceModel
            self._biometric_classifier = BiometricFusionClassifier()
            self._bio_extractor_cls    = BiometricFeatureExtractor   # B3: store class ref
            self._ewc_model            = EWCWorldModel()
            self._preference_model     = PreferenceModel()
            # Pin biometric model version into every PoAC record's model_manifest_hash
            if self._engine is not None:
                self._engine.model_hash = BIOMETRIC_MODEL_MANIFEST_HASH

            # Phase 14B: restore persisted model state (non-fatal if files absent)
            try:
                self._ewc_model = EWCWorldModel.load(self._cfg.ewc_model_path)
                log.info("EWC model restored from %s", self._cfg.ewc_model_path)
            except FileNotFoundError:
                log.debug("No EWC model file — starting fresh")
            except Exception as exc:
                log.warning("EWC model load failed (%s) — starting fresh", exc)
            try:
                self._preference_model = PreferenceModel.load(self._cfg.preference_model_path)
                log.info("Preference model restored from %s", self._cfg.preference_model_path)
            except FileNotFoundError:
                log.debug("No preference model file — starting fresh")
            except Exception as exc:
                log.warning("Preference model load failed (%s) — starting fresh", exc)

            log.info("Phase 13 modules loaded: E1 biometric + E4 EWC + E2 preference active")
        except Exception as exc:
            log.warning("Phase 13 modules unavailable (%s) -- legacy hashes active", exc)

        # Phase 16B: Layer 5 — Temporal Rhythm Oracle
        try:
            from temporal_rhythm_oracle import TemporalRhythmOracle  # type: ignore
            self._temporal_oracle = TemporalRhythmOracle()
            log.info("Layer 5 TemporalRhythmOracle initialised")
        except Exception:
            log.warning("TemporalRhythmOracle unavailable — Layer 5 inactive")

        # Phase 19: Resolve device profile (DeviceProfileRegistry)
        try:
            from vapi_bridge.device_registry import DeviceProfileRegistry
            _reg = DeviceProfileRegistry(controller_dir)
            self._device_profile = _reg.resolve(self._cfg)
            log.info(
                "Device profile: %s (PHCI=%s, schema_v=%d, layers=%s)",
                self._device_profile.display_name,
                self._device_profile.phci_tier.name,
                self._device_profile.schema_version,
                self._device_profile.pitl_layers,
            )
        except Exception as exc:
            log.warning(
                "DeviceProfileRegistry unavailable (%s) — defaulting to DualSense Edge",
                exc,
            )

        return True

    def _register_device(self):
        """Pre-register this session's device in the bridge store."""
        did_hex = self._device_id.hex()
        self._store.upsert_device(did_hex, self._pubkey_hex)
        log.info("Device registered: %s... pubkey=%s...", did_hex[:16], self._pubkey_hex[:32])

    # ------------------------------------------------------------------
    # Main session loop
    # ------------------------------------------------------------------
    async def _session_loop(self, active_bounties: list[int]):
        """Continuously poll, classify, generate, and dispatch PoAC records."""
        loop = asyncio.get_event_loop()

        while True:
            t_start = time.monotonic()

            # --- Collect frames for one interval (sync poll in thread) ---
            frames = await loop.run_in_executor(
                None, self._poll_frames, self._interval
            )

            if not frames:
                await asyncio.sleep(self._interval)
                continue

            # --- Anti-cheat classification (Layer 1) ---
            inference, confidence = self._classify(frames)

            # --- Layer 2: HID-XInput pipeline integrity ---
            # Overrides primary result only if injection detected (cheat priority:
            # existing cheat code is NOT overridden — primary cheat wins).
            if self._hid_oracle is not None and inference not in CHEAT_CODES:
                oracle_result = self._hid_oracle.classify()
                if oracle_result is not None:
                    inference, confidence = oracle_result
                    log.warning(
                        "DRIVER_INJECT detected: discrepancy in input pipeline "
                        "(confidence=%d)", confidence
                    )

            # --- Layer 3: Backend behavioral cheat detection ---
            # Overrides only if primary result is clean (NOMINAL/SKILLED).
            if self._backend_classifier is not None and inference not in CHEAT_CODES:
                backend_result = self._backend_classifier.classify_session(frames)
                if backend_result is not None:
                    inference, confidence = backend_result
                    log.warning(
                        "Backend cheat detected: %s (confidence=%d)",
                        GAMING_INFERENCE_NAMES.get(inference, f"0x{inference:02x}"),
                        confidence,
                    )

            # --- Layer 4: Biometric anomaly detection (Phase 13 Enhancement 1) ---
            # Produces BIOMETRIC_ANOMALY (0x30) — intentionally outside [0x28, 0x2A]
            # cheat range; does NOT block team proofs or trigger rating penalty.
            _l4_distance = None
            _l4_warmed = None
            _l4_features_json = None
            _l4_drift_velocity = None
            if self._biometric_classifier is not None and inference not in CHEAT_CODES:
                # B3: use stored class ref — no per-iteration import overhead
                bio_features = self._bio_extractor_cls.extract(frames)
                self._biometric_classifier.update_fingerprint(bio_features)
                bio_result = self._biometric_classifier.classify(bio_features)
                # Phase 36: adaptive policy multiplier feedback from InsightSynthesizer Mode 4
                # Only applies when baseline classify() found no anomaly (bio_result is None).
                # NEVER modifies classifier state. NEVER overrides hard cheat codes.
                if bio_result is None and getattr(self._cfg, "adaptive_thresholds_enabled", True):
                    try:
                        _policy = self._store.get_detection_policy(self._device_id.hex())
                        if _policy:
                            _mult = float(_policy.get("multiplier", 1.0))
                            if _mult < 1.0:
                                _effective_thresh = 3.0 * _mult
                                _d = getattr(self._biometric_classifier, "last_distance", None)
                                if _d is not None and _d > _effective_thresh:
                                    _excess = _d - _effective_thresh
                                    _conf = min(255, 180 + int(_excess * 30.0))
                                    bio_result = (0x30, _conf)  # INFER_BIOMETRIC_ANOMALY
                                    log.debug(
                                        "BIOMETRIC_ANOMALY via policy (d=%.2f, eff=%.2f, mult=%.2f)",
                                        _d, _effective_thresh, _mult,
                                    )
                    except Exception:
                        pass  # policy lookup is always non-fatal
                if bio_result is not None:
                    inference, confidence = bio_result
                    log.debug(
                        "BIOMETRIC_ANOMALY detected (d=%.2f)",
                        self._biometric_classifier.last_distance,
                    )
                # Phase 21: capture L4 metadata for PITL persistence
                _l4_distance = getattr(self._biometric_classifier, "last_distance", None)
                _l4_warmed = (self._biometric_classifier.is_warmed_up()
                              if hasattr(self._biometric_classifier, "is_warmed_up") else None)
                try:
                    import json as _json, dataclasses as _dc
                    _l4_features_json = _json.dumps(
                        {k: float(v) for k, v in _dc.asdict(bio_features).items()
                         if isinstance(v, (int, float))}
                    )
                except Exception:
                    pass
                # Phase 25: two-track EMA — update stable fingerprint only on clean NOMINAL sessions
                if (inference == INFER_NOMINAL
                        and hasattr(self._biometric_classifier, "update_stable_fingerprint")):
                    self._biometric_classifier.update_stable_fingerprint(bio_features)
                # Phase 25: capture drift velocity between candidate and stable fingerprint means
                if hasattr(self._biometric_classifier, "fingerprint_drift_velocity"):
                    _l4_drift_velocity = self._biometric_classifier.fingerprint_drift_velocity

            # --- Layer 5: Temporal Rhythm Oracle (Phase 16B) ---
            # Produces TEMPORAL_ANOMALY (0x2B) — outside [0x28, 0x2A] cheat range.
            # Only fires when ≥2/3 bot-timing signals fire (CV, entropy, quantization).
            # Hard cheat codes from L2/L3 are never overridden.
            _l5_cv = None
            _l5_entropy_bits = None
            _l5_quant_score = None
            _l5_anomaly_signals = None
            _l5_rhythm_humanity = None
            if self._temporal_oracle is not None and inference not in CHEAT_CODES:
                for f in frames:
                    self._temporal_oracle.push_frame(f)
                temporal_result = self._temporal_oracle.classify()
                if temporal_result is not None:
                    inference, confidence = temporal_result
                    log.debug("TEMPORAL_ANOMALY detected (confidence=%d)", confidence)
                # Phase 21: capture L5 temporal features for PITL persistence
                try:
                    _l5_feats = self._temporal_oracle.extract_features()
                    if _l5_feats is not None:
                        _l5_cv = float(_l5_feats.cv)
                        _l5_entropy_bits = float(_l5_feats.entropy_bits)
                        _l5_quant_score = float(_l5_feats.quant_score)
                        _l5_anomaly_signals = int(_l5_feats.anomaly_signals)
                except Exception:
                    pass
                # Phase 25: positive humanity signal from L5 (inverts anomaly into [0,1] score)
                if hasattr(self._temporal_oracle, "rhythm_humanity_score"):
                    try:
                        _l5_rhythm_humanity = self._temporal_oracle.rhythm_humanity_score()
                    except Exception:
                        pass

            # Phase 23: Post-warmup continuity check — fires once when L4 classifier warms up.
            # Persists the classifier's mean/var state and launches async continuity attestation.
            if (not self._warmup_attested
                    and self._biometric_classifier is not None
                    and hasattr(self._biometric_classifier, "is_warmed_up")
                    and self._biometric_classifier.is_warmed_up()):
                self._warmup_attested = True
                # Persist classifier mean/var for cross-device distance computation
                if (hasattr(self._biometric_classifier, "_mean")
                        and hasattr(self._biometric_classifier, "_var")):
                    try:
                        mean_list = self._biometric_classifier._mean.tolist()
                        var_list  = self._biometric_classifier._var.tolist()
                        mean_dict = dict(zip(FEATURE_KEYS, mean_list))
                        var_dict  = dict(zip(FEATURE_KEYS, var_list))
                        self._store.store_fingerprint_state(
                            self._device_id.hex(), mean_dict, var_dict,
                            getattr(self._biometric_classifier, "_n_sessions", 0),
                        )
                        log.info(
                            "Phase 23: fingerprint state persisted for device=%s",
                            self._device_id.hex()[:16],
                        )
                    except Exception as exc:
                        log.warning("Phase 23: fingerprint state persist failed: %s", exc)
                # Fire continuity check (async, non-blocking)
                if self._continuity_prover is not None:
                    asyncio.create_task(self._check_continuity())

            # Phase 25: Bayesian humanity probability fusion — L4 × L5 × E4
            if _l4_warmed and _l4_distance is not None:
                _p_l4 = _math.exp(-max(0.0, _l4_distance - 2.0))
            else:
                _p_l4 = 0.5
            _p_l5 = _l5_rhythm_humanity if _l5_rhythm_humanity is not None else 0.5
            if _e4_cognitive_drift is not None:
                _p_e4 = _math.exp(-_e4_cognitive_drift / 3.0)
            else:
                _p_e4 = 0.5
            _humanity_prob = 0.4 * _p_l4 + 0.4 * _p_l5 + 0.2 * _p_e4

            # Phase 21: store PITL metadata sidecar — read by Bridge.on_record() for persistence
            self._pending_pitl_meta = {
                "l4_distance":        _l4_distance,
                "l4_warmed_up":       _l4_warmed,
                "l4_features_json":   _l4_features_json,
                "l5_cv":              _l5_cv,
                "l5_entropy_bits":    _l5_entropy_bits,
                "l5_quant_score":     _l5_quant_score,
                "l5_anomaly_signals": _l5_anomaly_signals,
                # Phase 25: agent intelligence fields
                "l5_rhythm_humanity": _l5_rhythm_humanity,
                "l4_drift_velocity":  _l4_drift_velocity,
                "e4_cognitive_drift": _e4_cognitive_drift,
                "humanity_prob":      _humanity_prob,
            }

            inf_name = GAMING_INFERENCE_NAMES.get(inference, f"0x{inference:02x}")

            # --- Select bounty (greedy: first active bounty) ---
            bounty_id = active_bounties[0] if active_bounties else 0
            action    = ACTION_BOUNTY_CLAIM if bounty_id else ACTION_REPORT

            # --- Generate PoAC record ---
            raw = self._make_record(inference, action, confidence, bounty_id,
                                    battery_mv=frames[-1].battery_mv)
            if raw:
                await self._dispatch(raw)
                self._last_raw = raw

                # Parse to get canonical record_hash for tracking
                try:
                    parsed = parse_record(raw)
                    record_hash = parsed.record_hash
                except Exception:
                    record_hash = b"\x00" * 32

                # --- Apply SkillOracle delta locally ---
                new_rating = self._oracle.apply(inference, confidence)
                log.debug(
                    "PoAC dispatched | %s conf=%d rating=%d/%s bounty=%d",
                    inf_name, confidence, new_rating,
                    _rating_tier(new_rating), bounty_id,
                )

                # --- Track progress for ProgressAttestation ---
                if self._progress:
                    self._progress.record(record_hash, inference, confidence)

                # --- LED/haptic feedback ---
                await loop.run_in_executor(None, self._apply_feedback, inference)

            # --- Phase 13 E1: maintain trigger mode history for sensor_commitment_v2_bio ---
            if frames:
                self._l2_mode_history = [int(getattr(f, "l2_effect_mode", 0)) for f in frames[-16:]]
                self._r2_mode_history = [int(getattr(f, "r2_effect_mode", 0)) for f in frames[-16:]]

            # --- Phase 13 E4: accumulate frames; update EWC world model every N intervals ---
            _e4_cognitive_drift = None
            if self._ewc_model is not None and frames:
                self._frame_buffer.extend(frames)
                self._session_count += 1
                if self._session_count % _EWC_SESSION_INTERVAL == 0 and self._frame_buffer:
                    session_vec = self._build_ewc_session_vec(self._frame_buffer)
                    session_label = confidence / 255.0
                    self._ewc_model.update(session_vec, session_label)
                    self._recent_session_vecs.append(session_vec)
                    self._frame_buffer = []
                    if (self._session_count % _EWC_FISHER_INTERVAL == 0
                            and len(self._recent_session_vecs) >= 5):
                        self._ewc_model.compute_fisher(self._recent_session_vecs[-50:])
                    log.debug(
                        "EWC update #%d complete",
                        self._session_count // _EWC_SESSION_INTERVAL,
                    )
                    # Phase 25: E4 cognitive drift — compare embedding to previous session
                    if hasattr(self._ewc_model, "get_embedding"):
                        try:
                            import numpy as _np
                            new_emb = self._ewc_model.get_embedding(session_vec)
                            prev_emb_list = self._store.get_last_cognitive_embedding(
                                self._device_id.hex()
                            )
                            if prev_emb_list is not None:
                                prev_arr = _np.array(prev_emb_list, dtype=_np.float32)
                                _e4_cognitive_drift = float(
                                    _np.linalg.norm(new_emb - prev_arr)
                                )
                                self._drift_history.append(_e4_cognitive_drift)
                            self._store.store_cognitive_embedding(
                                self._device_id.hex(),
                                new_emb.tolist(),
                                self._session_count,
                            )
                        except Exception as _exc:
                            log.debug("Phase 25: E4 drift computation failed: %s", _exc)

            # --- Pace to interval ---
            elapsed = time.monotonic() - t_start
            await asyncio.sleep(max(0.0, self._interval - elapsed))

    # ------------------------------------------------------------------
    # Sync helpers (executed in thread pool)
    # ------------------------------------------------------------------
    def _poll_frames(self, duration_s: float) -> list:
        """Synchronously poll controller frames for duration_s seconds."""
        if not self._reader:
            return []
        frames = []
        t_end = time.monotonic() + duration_s
        dt_ms = 8.0   # target ~120 Hz
        while time.monotonic() < t_end:
            snap = self._reader.poll()
            frames.append(snap)
            # Phase 8 Layer 2: update HID-XInput oracle per frame
            if self._hid_oracle is not None:
                self._hid_oracle.update(snap)
            time.sleep(dt_ms / 1000.0)
        return frames

    def _classify(self, frames: list) -> tuple[int, int]:
        """Run anti-cheat classifier over a frame batch. Returns (inference, confidence)."""
        if not self._classifier or not frames:
            return INFER_NOMINAL, 220
        self._classifier.reset()
        dt_ms = 8.0
        for snap in frames:
            self._classifier.extract_features(snap, dt_ms)
        inference, confidence = self._classifier.classify()
        return inference, confidence

    def _build_ewc_session_vec(self, frames: list) -> "np.ndarray":
        """
        Build a 30-dim session feature vector for the EWC world model.

        Phase 14B: uses AntiCheatClassifier.window (FeatureFrame objects with
        FeatureFrame.to_vector()) for exact INPUT_DIM=30 alignment with the MLP.
        Falls back to the Phase 14A InputSnapshot approximation when the
        classifier window is empty or unavailable.
        """
        import numpy as np

        # Phase 14B: prefer the AntiCheatClassifier's FeatureFrame window
        if self._classifier is not None and len(self._classifier.window) > 0:
            feature_frames = list(self._classifier.window)
            try:
                from world_model_continual import EWCWorldModel
                return EWCWorldModel.build_session_vector(feature_frames)
            except Exception:
                pass  # fall through to approximation

        # Phase 14A approximation fallback (InputSnapshot attrs, 12 means + 12 stds + 6)
        _ATTRS = [
            "left_stick_x", "left_stick_y", "right_stick_x", "right_stick_y",
            "l2_trigger", "r2_trigger",
            "accel_x", "accel_y", "accel_z",
            "gyro_x",  "gyro_y",  "gyro_z",
        ]
        if not frames:
            return np.zeros(30, dtype=np.float32)
        mat = np.array(
            [[float(getattr(f, a, 0.0)) for a in _ATTRS] for f in frames],
            dtype=np.float32,
        )
        means = mat.mean(axis=0)   # 12
        stds  = mat.std(axis=0)    # 12
        extras = np.array([
            float(np.mean([int(getattr(f, "buttons", 0)) for f in frames])),
            float(np.mean([int(getattr(f, "battery_mv", 4000)) for f in frames])) / 8400.0,
            float(np.mean([int(getattr(f, "l2_effect_mode", 0)) for f in frames])),
            float(np.mean([int(getattr(f, "r2_effect_mode", 0)) for f in frames])),
            float(len(frames)) / 120.0,
            float(np.mean(mat[:, 6]**2 + mat[:, 7]**2 + mat[:, 8]**2)),  # |accel|^2 mean
        ], dtype=np.float32)
        return np.concatenate([means, stds, extras])

    def _make_record(
        self,
        inference: int,
        action: int,
        confidence: int,
        bounty_id: int,
        battery_mv: int = 4000,
    ) -> Optional[bytes]:
        """
        Generate a 228-byte PoAC record from the current controller state.

        Phase 13 enrichment (when modules are available):
          sensor_commitment = SHA-256 of 56-byte bio-enriched payload (E1)
          world_model_hash  = SHA-256 of EWC weights || preference weights (E4+E2)
        Fallback (Phase 12 legacy, when modules unavailable):
          sensor_commitment = SHA-256 of 48-byte schema v2 payload
          world_model_hash  = SHA-256 of (inference, action, confidence)
        """
        if not self._engine or not self._reader:
            return None
        try:
            snap = self._reader.poll()
            timestamp_ms = int(time.time() * 1000)

            # Phase 11: Sync effect mode state from snap (simulation) or stored state (hardware)
            self._update_trigger_effect_modes(snap)

            # Phase 19: use profile's sensor_commitment_size_bytes to decide path.
            # PHCI_CERTIFIED profiles (56B) use the bio-enriched commitment;
            # PHCI_STANDARD / NONE profiles (48B) use the base v2 commitment.
            # Fall back to the pre-Phase-19 heuristic (biometric_classifier not None)
            # when no profile is resolved.
            _use_bio = (
                self._biometric_classifier is not None
                and (
                    self._device_profile is None
                    or self._device_profile.sensor_commitment_size_bytes >= 56
                )
            )
            if _use_bio:
                from tinyml_biometric_fusion import compute_sensor_commitment_v2_bio
                sensor_hash = compute_sensor_commitment_v2_bio(
                    snap, timestamp_ms,
                    self._l2_effect_mode, self._r2_effect_mode,
                    biometric_classifier=self._biometric_classifier,
                    l2_mode_history=self._l2_mode_history or None,
                    r2_mode_history=self._r2_mode_history or None,
                )
            else:
                # 48-byte schema v2 base commitment (PHCI_STANDARD / NONE / fallback)
                commitment_bytes = struct.pack(
                    ">hhhhBBBBffffffIQ",
                    snap.left_stick_x,    snap.left_stick_y,
                    snap.right_stick_x,   snap.right_stick_y,
                    snap.l2_trigger,      snap.r2_trigger,
                    self._l2_effect_mode, self._r2_effect_mode,
                    snap.accel_x, snap.accel_y, snap.accel_z,
                    snap.gyro_x,  snap.gyro_y,  snap.gyro_z,
                    snap.buttons, timestamp_ms,
                )
                sensor_hash = hashlib.sha256(commitment_bytes).digest()

            # Phase 13 E4+E2: World model hash = SHA-256(EWC_weights || preference_weights)
            if self._ewc_model is not None:
                pref_bytes = (self._preference_model.serialize_weights()
                              if self._preference_model is not None else b"")
                wm_hash = self._ewc_model.compute_hash(preference_weights_bytes=pref_bytes)
            else:
                # Phase 12 fallback: classification epoch hash
                wm_bytes = struct.pack(">BBH", inference, action, confidence)
                wm_hash  = hashlib.sha256(wm_bytes).digest()

            # Battery: convert mV -> pct  (8400 mV = 100%)
            battery_pct = min(100, max(0, round(battery_mv * 100 / 8400)))

            # PoACEngine.generate() returns emulator's PoACRecord dataclass;
            # .serialize_full() produces the canonical 228-byte wire format.
            poac_record = self._engine.generate(
                sensor_hash, wm_hash,
                inference, action, confidence,
                battery_pct, bounty_id=bounty_id,
            )
            return poac_record.serialize_full()

        except Exception as exc:
            log.warning("Record generation error: %s", exc)
            return None

    def _update_trigger_effect_modes(self, snap) -> None:
        """Sync internal trigger effect mode state with the current snapshot.

        In simulation mode (no hardware connected): the emulator's _simulate_input()
        periodically sets non-zero modes on the snapshot, so we pull from snap directly.
        In hardware mode: pydualsense does not expose the current trigger mode back in
        the HID input report (trigger effects are output-only). We keep self._l2_effect_mode
        and self._r2_effect_mode as authoritative state; they are updated by
        set_trigger_effect() when the bridge deliberately sets an effect.
        """
        if self._reader is not None and not self._reader.connected:
            # Simulation mode — emulator snapshot carries simulated effect modes
            self._l2_effect_mode = int(getattr(snap, 'l2_effect_mode', 0) or 0)
            self._r2_effect_mode = int(getattr(snap, 'r2_effect_mode', 0) or 0)
        # Hardware mode: snap.l2_effect_mode is 0 (unreadable from HID report).
        # self._l2_effect_mode / self._r2_effect_mode remain authoritative.

    def set_trigger_effect(self, side: str, mode_ordinal: int) -> None:
        """Set the adaptive trigger resistance mode for L2 or R2.

        Updates the internal state variable immediately so the next sensor commitment
        hash reflects the new mode. Attempts to forward the effect to the physical
        controller if connected and pydualsense TriggerModes are available.

        Args:
            side:         "L2" or "R2"
            mode_ordinal: TriggerMode ordinal (0=Off, 1=Rigid, 2=Pulse, ...)
        """
        if side == "L2":
            self._l2_effect_mode = int(mode_ordinal)
        elif side == "R2":
            self._r2_effect_mode = int(mode_ordinal)
        else:
            log.warning("set_trigger_effect: unknown side %r (expected 'L2' or 'R2')", side)
            return

        # Forward to physical hardware if connected and TriggerModes enum is available
        if self._reader and self._reader.connected and self._TriggerModes:
            try:
                modes = list(self._TriggerModes)
                if 0 <= mode_ordinal < len(modes):
                    mode = modes[mode_ordinal]
                    if side == "L2":
                        self._reader.ds.triggerL.setMode(mode)
                    else:
                        self._reader.ds.triggerR.setMode(mode)
            except Exception as exc:
                log.debug("set_trigger_effect hardware forward failed: %s", exc)

    def _apply_feedback(self, inference: int):
        """Set controller LED and haptics based on anti-cheat result."""
        if not self._reader:
            return
        try:
            if inference == INFER_SKILLED:
                self._reader.set_led(0, 128, 255)      # Cyan — skilled play
            elif inference in CHEAT_CODES:
                self._reader.set_led(255, 0, 0)        # Red  — cheat detected
                self._reader.haptic(200, 200)           # Strong rumble
            else:
                self._reader.set_led(0, 255, 0)        # Green — clean play
        except Exception:
            pass   # Feedback is non-critical

    # ------------------------------------------------------------------
    # Dispatch and shutdown
    # ------------------------------------------------------------------
    async def _dispatch(self, raw: bytes):
        """Push a 228-byte record into the bridge's on_record pipeline."""
        try:
            await self._on_record(raw, "dualshock")
        except Exception as exc:
            log.warning("Bridge on_record error: %s", exc)

    async def _shutdown_cleanup(self):
        """Submit final SkillOracle update and reset controller state."""
        summary = self._oracle.summary()
        log.info(
            "Session complete | records=%d rating=%d tier=%s cheats=%d",
            summary["records"], summary["rating"],
            summary["tier"],    summary["cheats_detected"],
        )
        # Submit final record hash to SkillOracle on-chain
        if self._last_raw and self._oracle:
            try:
                last = parse_record(self._last_raw)
                await self._oracle.submit_session_update(
                    last.record_hash, last.inference_result, last.confidence
                )
            except Exception as exc:
                log.warning("Final SkillOracle update failed: %s", exc)

        # Submit ProgressAttestation if improvement was detected
        if self._progress:
            await self._progress.submit()

        # Phase 14B: persist EWC + preference model state for next session
        if self._ewc_model is not None:
            try:
                from pathlib import Path as _Path
                _Path(self._cfg.ewc_model_path).parent.mkdir(parents=True, exist_ok=True)
                self._ewc_model.save(self._cfg.ewc_model_path)
                log.info("EWC model saved to %s", self._cfg.ewc_model_path)
            except Exception as exc:
                log.warning("EWC model save failed: %s", exc)
        if self._preference_model is not None:
            try:
                from pathlib import Path as _Path
                _Path(self._cfg.preference_model_path).parent.mkdir(parents=True, exist_ok=True)
                self._preference_model.save(self._cfg.preference_model_path)
                log.info("Preference model saved to %s", self._cfg.preference_model_path)
            except Exception as exc:
                log.warning("Preference model save failed: %s", exc)

        # Phase 27: Generate ZK PITL session proof for this session
        if self._pitl_prover is not None and self._pending_pitl_meta:
            try:
                import json as _json
                import time as _time
                _feats_raw = self._pending_pitl_meta.get("l4_features_json") or "{}"
                _feats = _json.loads(_feats_raw) if isinstance(_feats_raw, str) else (_feats_raw or {})
                features = {k: float(_feats.get(k, 0.0)) for k in self._pitl_prover.FEATURE_KEYS}
                l5    = float(self._pending_pitl_meta.get("l5_rhythm_humanity") or 0.5)
                e4    = float(self._pending_pitl_meta.get("e4_cognitive_drift") or 0.0)
                # Inference from last record; fallback to NOMINAL
                infer = 0x20
                if self._last_raw and len(self._last_raw) >= 165:
                    infer = self._last_raw[164]  # body[164] = inference byte
                epoch    = int(_time.time()) // 3600  # hourly epoch (mock-safe)
                dev_hex  = self._device_id.hex()
                proof, fc, hp_int, null = self._pitl_prover.generate_proof(
                    features, dev_hex, l5, e4, infer, epoch
                )
                self._store.store_pitl_proof(dev_hex, hex(null), hex(fc), hp_int)
                if self._chain is not None:
                    asyncio.create_task(
                        self._chain.submit_pitl_proof(dev_hex, proof, fc, hp_int, null, epoch)
                    )
                log.info(
                    "Phase 27: PITL session proof stored: device=%s hp_int=%d fc=%s",
                    dev_hex[:16], hp_int, hex(fc)[:16],
                )
            except Exception as exc:
                log.warning("Phase 27: PITL session proof failed (non-fatal): %s", exc)

        # Reset LED to idle blue
        if self._reader:
            try:
                self._reader.set_led(0, 0, 255)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Phase 23: Biometric Continuity
    # ------------------------------------------------------------------
    async def _check_continuity(self):
        """Check all warmed-up prior devices for biometric continuity.

        Called once when this device's biometric classifier first warms up.
        Iterates all other known devices in the store and, if any are within
        the Mahalanobis threshold, attests continuity on-chain.

        Phase 25: protected by asyncio.Lock to prevent TOCTOU races; awaits
        receipt before marking devices claimed.

        Non-fatal: failures are logged but do not affect the session.
        """
        if self._continuity_prover is None or self._chain is None:
            return
        async with self._continuity_lock:
            new_device_id = self._device_id.hex()
            try:
                devices = self._store.list_devices()
                for dev in devices:
                    old_device_id = dev["device_id"]
                    if old_device_id == new_device_id:
                        continue
                    # Skip if either device is already claimed
                    if (self._store.is_device_claimed(old_device_id)
                            or self._store.is_device_claimed(new_device_id)):
                        continue
                    should, dist = self._continuity_prover.should_attest(
                        old_device_id, new_device_id
                    )
                    if not should:
                        log.debug(
                            "Phase 23: no continuity with %s (dist=%s)",
                            old_device_id[:16], f"{dist:.4f}" if dist is not None else "N/A",
                        )
                        continue
                    log.info(
                        "Phase 23: biometric continuity detected! old=%s new=%s dist=%.4f",
                        old_device_id[:16], new_device_id[:16], dist,
                    )
                    proof_hash = self._continuity_prover.make_proof_hash(
                        old_device_id, new_device_id, dist
                    )
                    tx_hash = await self._chain.attest_continuity(
                        old_device_id, new_device_id, proof_hash
                    )
                    if tx_hash:
                        # Phase 25: await receipt before marking claimed (TOCTOU fix)
                        try:
                            receipt = await asyncio.wait_for(
                                self._chain.wait_for_receipt(tx_hash, timeout=60),
                                timeout=65.0,
                            )
                            if receipt.get("status") == 1:
                                self._store.mark_device_claimed(old_device_id, new_device_id)
                                self._store.mark_device_claimed(new_device_id, old_device_id)
                                log.info(
                                    "Phase 23: ContinuityAttested on-chain. tx=%s", tx_hash[:16]
                                )
                            else:
                                log.warning(
                                    "Phase 23: continuity tx reverted. tx=%s", tx_hash[:16]
                                )
                        except asyncio.TimeoutError:
                            log.warning(
                                "Phase 23: continuity receipt timeout. tx=%s", tx_hash[:16]
                            )
                    break  # Only claim one prior device per session (first match wins)
            except Exception as exc:
                log.warning("Phase 23: _check_continuity failed (non-fatal): %s", exc)
