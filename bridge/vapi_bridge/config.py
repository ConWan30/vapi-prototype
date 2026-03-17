"""
VAPI Bridge Configuration — Environment-based with sensible defaults.

All config is read from environment variables (or .env file via python-dotenv).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int = 0) -> int:
    return int(os.environ.get(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Config:
    """Immutable bridge configuration, loaded once at startup."""

    # --- IoTeX RPC ---
    iotex_rpc_url: str = field(
        default_factory=lambda: _env("IOTEX_RPC_URL", "https://babel-api.testnet.iotex.io")
    )
    chain_id: int = field(default_factory=lambda: _env_int("IOTEX_CHAIN_ID", 4690))

    # --- Contract addresses ---
    verifier_address: str = field(
        default_factory=lambda: _env("POAC_VERIFIER_ADDRESS", "")
    )
    bounty_market_address: str = field(
        default_factory=lambda: _env("BOUNTY_MARKET_ADDRESS", "")
    )
    device_registry_address: str = field(
        default_factory=lambda: _env("DEVICE_REGISTRY_ADDRESS", "")
    )

    # --- Bridge wallet ---
    bridge_private_key: str = field(
        default_factory=lambda: _env("BRIDGE_PRIVATE_KEY", "")
    )

    # --- MQTT ---
    mqtt_enabled: bool = field(
        default_factory=lambda: _env_bool("MQTT_ENABLED", True)
    )
    mqtt_broker: str = field(
        default_factory=lambda: _env("MQTT_BROKER", "localhost")
    )
    mqtt_port: int = field(default_factory=lambda: _env_int("MQTT_PORT", 1883))
    mqtt_topic_prefix: str = field(
        default_factory=lambda: _env("MQTT_TOPIC_PREFIX", "vapi/poac")
    )
    mqtt_username: str = field(default_factory=lambda: _env("MQTT_USERNAME", ""))
    mqtt_password: str = field(default_factory=lambda: _env("MQTT_PASSWORD", ""))

    # --- CoAP ---
    coap_enabled: bool = field(
        default_factory=lambda: _env_bool("COAP_ENABLED", False)
    )
    coap_bind: str = field(
        default_factory=lambda: _env("COAP_BIND", "0.0.0.0")
    )
    coap_port: int = field(default_factory=lambda: _env_int("COAP_PORT", 5683))

    # --- HTTP API / Dashboard ---
    http_enabled: bool = field(
        default_factory=lambda: _env_bool("HTTP_ENABLED", True)
    )
    http_host: str = field(
        default_factory=lambda: _env("HTTP_HOST", "0.0.0.0")
    )
    http_port: int = field(default_factory=lambda: _env_int("HTTP_PORT", 8080))

    # --- Batching ---
    batch_size: int = field(default_factory=lambda: _env_int("BATCH_SIZE", 10))
    batch_timeout_s: int = field(
        default_factory=lambda: _env_int("BATCH_TIMEOUT_S", 30)
    )

    # --- Retry ---
    max_retries: int = field(default_factory=lambda: _env_int("MAX_RETRIES", 5))
    retry_base_delay_s: float = field(
        default_factory=lambda: float(_env("RETRY_BASE_DELAY_S", "2.0"))
    )

    # --- Storage ---
    db_path: str = field(
        default_factory=lambda: _env(
            "DB_PATH",
            str(Path.home() / ".vapi" / "bridge.db"),
        )
    )

    # --- Logging ---
    log_level: str = field(
        default_factory=lambda: _env("LOG_LEVEL", "INFO")
    )

    # --- DualShock Edge transport ---
    dualshock_enabled: bool = field(
        default_factory=lambda: _env_bool("DUALSHOCK_ENABLED", False)
    )
    dualshock_record_interval_s: float = field(
        default_factory=lambda: float(_env("DUALSHOCK_RECORD_INTERVAL_S", "1.0"))
    )
    skill_oracle_address: str = field(
        default_factory=lambda: _env("SKILL_ORACLE_ADDRESS", "")
    )
    # Comma-separated active bounty IDs, e.g. "1001,1002"
    dualshock_active_bounties: str = field(
        default_factory=lambda: _env("DUALSHOCK_ACTIVE_BOUNTIES", "")
    )
    # Directory for persistent device keypair (default: ~/.vapi)
    dualshock_key_dir: str = field(
        default_factory=lambda: _env(
            "DUALSHOCK_KEY_DIR",
            str(Path.home() / ".vapi"),
        )
    )

    # --- Phase 4: ProgressAttestation + TeamProofAggregator ---
    progress_attestation_address: str = field(
        default_factory=lambda: _env("PROGRESS_ATTESTATION_ADDRESS", "")
    )
    team_aggregator_address: str = field(
        default_factory=lambda: _env("TEAM_AGGREGATOR_ADDRESS", "")
    )

    # --- Phase 7: Tiered Registration ---
    device_registration_tier: str = field(
        default_factory=lambda: _env("DEVICE_REGISTRATION_TIER", "Standard")
    )
    attestation_proof_hex: str = field(
        default_factory=lambda: _env("ATTESTATION_PROOF_HEX", "")
    )

    # --- Phase 8: Physical Input Trust Layer ---
    hid_oracle_enabled: bool = field(
        default_factory=lambda: _env_bool("HID_ORACLE_ENABLED", False)
    )
    hid_oracle_threshold: float = field(
        default_factory=lambda: float(_env("HID_ORACLE_THRESHOLD", "0.15"))
    )
    hid_oracle_gamepad_index: int = field(
        default_factory=lambda: _env_int("HID_ORACLE_GAMEPAD_INDEX", 0)
    )
    backend_cheat_enabled: bool = field(
        default_factory=lambda: _env_bool("BACKEND_CHEAT_ENABLED", False)
    )
    backend_cheat_model_path: str = field(
        default_factory=lambda: _env("BACKEND_CHEAT_MODEL_PATH", "")
    )

    # --- Phase 9: Hardware Signing Bridge ---
    identity_backend: str = field(
        default_factory=lambda: _env("IDENTITY_BACKEND", "software")
    )
    yubikey_piv_slot: str = field(
        default_factory=lambda: _env("YUBIKEY_PIV_SLOT", "9c")
    )
    atecc608_i2c_bus: int = field(
        default_factory=lambda: _env_int("ATECC608_I2C_BUS", 1)
    )

    # --- Phase 11: Bridge Key Security ---
    # --- Phase 14B: EWC + Preference model persistence paths ---
    ewc_model_path: str = field(
        default_factory=lambda: str(
            Path(os.getenv("VAPI_EWC_MODEL_PATH",
                           str(Path.home() / ".vapi" / "ewc_model.json")))
        )
    )
    preference_model_path: str = field(
        default_factory=lambda: str(
            Path(os.getenv("VAPI_PREF_MODEL_PATH",
                           str(Path.home() / ".vapi" / "pref_model.bin")))
        )
    )

    # "env"      — read BRIDGE_PRIVATE_KEY plaintext from env (default; dev/testnet only)
    # "keystore" — decrypt an Ethereum keystore JSON file at keystore_path (mainnet)
    bridge_private_key_source: str = field(
        default_factory=lambda: _env("BRIDGE_PRIVATE_KEY_SOURCE", "env")
    )
    # Absolute path to the Ethereum keystore JSON file (required when source="keystore")
    keystore_path: str = field(
        default_factory=lambda: _env("BRIDGE_KEYSTORE_PATH", "")
    )
    # Name of the env var that holds the keystore decryption password
    keystore_password_env: str = field(
        default_factory=lambda: _env("BRIDGE_KEYSTORE_PASSWORD_ENV", "BRIDGE_KEYSTORE_PASSWORD")
    )

    # --- Phase 22: PHG Registry (On-Chain Humanity Credential) ---
    phg_registry_address: str = field(
        default_factory=lambda: _env("PHG_REGISTRY_ADDRESS", "")
    )
    phg_checkpoint_interval: int = field(
        default_factory=lambda: _env_int("PHG_CHECKPOINT_INTERVAL", 10)
    )

    # --- Phase 23: Identity Continuity Registry ---
    identity_registry_address: str = field(
        default_factory=lambda: _env("IDENTITY_REGISTRY_ADDRESS", "")
    )
    continuity_threshold: float = field(
        default_factory=lambda: float(_env("CONTINUITY_THRESHOLD", "2.0"))
    )

    # --- Phase 25: Agent Intelligence & Chain Reconciler ---
    phg_humanity_weighted: bool = field(
        default_factory=lambda: _env_bool("PHG_HUMANITY_WEIGHTED", True)
    )
    reconciler_poll_interval: float = field(
        default_factory=lambda: float(_env("RECONCILER_POLL_INTERVAL", "30.0"))
    )

    # --- Phase 26: ZK PITL Session Proof ---
    pitl_session_registry_address: str = field(
        default_factory=lambda: _env("PITL_SESSION_REGISTRY_ADDRESS", "")
    )

    # --- Phase 62: PITLSessionRegistryV2 (Phase 62 C3 circuit) ---
    # When set, submit_pitl_proof routes to v2 instead of v1.
    pitl_session_registry_v2_address: str = field(
        default_factory=lambda: _env("PITL_SESSION_REGISTRY_V2_ADDRESS", "")
    )

    # --- Phase 28: PHG Credential (Soulbound On-Chain Credential Registry) ---
    phg_credential_address: str = field(
        default_factory=lambda: _env("PHG_CREDENTIAL_ADDRESS", "")
    )

    # --- Phase 29: Tournament Operator Gate API ---
    operator_api_key: str = field(
        default_factory=lambda: _env("OPERATOR_API_KEY", "")
    )
    """
    Shared secret for the /operator/gate API. If empty, operator endpoints
    return HTTP 503. Set to any secure random string (32+ bytes hex recommended).
    """

    # --- Phase 32: ProactiveMonitor poll interval ---
    monitor_poll_interval: float = field(
        default_factory=lambda: float(_env("MONITOR_POLL_INTERVAL", "60.0"))
    )

    # --- Phase 34: Federation Bus ---
    federation_peers: str = field(
        default_factory=lambda: _env("FEDERATION_PEERS", "")
    )
    federation_api_key: str = field(
        default_factory=lambda: _env("FEDERATION_API_KEY", "")
    )
    federation_poll_interval: float = field(
        default_factory=lambda: float(_env("FEDERATION_POLL_INTERVAL", "120.0"))
    )
    federated_threat_registry_address: str = field(
        default_factory=lambda: _env("FEDERATED_THREAT_REGISTRY_ADDRESS", "")
    )

    # --- Phase 35: Longitudinal Insight Synthesis ---
    synthesizer_poll_interval: float = field(
        default_factory=lambda: float(_env("SYNTHESIZER_POLL_INTERVAL", "21600.0"))
    )
    digest_retention_days: float = field(
        default_factory=lambda: float(_env("DIGEST_RETENTION_DAYS", "90.0"))
    )

    # --- Phase 36: Adaptive Adversarial Feedback ---
    adaptive_thresholds_enabled: bool = field(
        default_factory=lambda: _env("ADAPTIVE_THRESHOLDS_ENABLED", "true").lower() == "true"
    )
    policy_multiplier_floor: float = field(
        default_factory=lambda: float(_env("POLICY_MULTIPLIER_FLOOR", "0.5"))
    )
    rate_limit_per_minute: int = field(
        default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "60"))
    )

    # --- L4 Calibration: Hardware-derived Mahalanobis thresholds ---
    # Calibrated from N=69 sessions (2 players, DualShock Edge USB, 2026-03-07).
    # anomaly  = mean+3sigma (99.7th pct) = 6.905  [converging — delta vs N=54 was +0.124]
    # continuity = mean+2sigma (95th pct)
    # Phase 57 recalibration (N=74, HIGH confidence, 12-feature space with jitter_var):
    #   anomaly=7.009, continuity=5.367 (+4.2% / +5.3% vs Phase 46 6.726/5.097)
    # Threshold rise is expected: adding press_timing_jitter_variance (feature 12) expands
    # the Mahalanobis distance distribution; 3σ compensates correctly.
    # Use scripts/threshold_calibrator.py to recalibrate after new sessions.
    l4_anomaly_threshold: float = field(
        default_factory=lambda: float(_env("L4_ANOMALY_THRESHOLD", "7.009"))
    )
    l4_continuity_threshold: float = field(
        default_factory=lambda: float(_env("L4_CONTINUITY_THRESHOLD", "5.367"))
    )

    # --- L5 Calibration: TemporalRhythmOracle thresholds ---
    # CV threshold: bot timing CV < 0.08 (adversarially calibrated; human 10th pct N=54: 0.789 -- 10x margin)
    # Entropy threshold: bot entropy < 1.0 bits (human 10th pct N=54: 1.259 -- safe margin)
    # NOTE: DO NOT raise CV/entropy thresholds to human percentiles -- that creates FP rate.
    # These thresholds are adversarially set far below the human floor, not from human data.
    l5_cv_threshold: float = field(
        default_factory=lambda: float(_env("L5_CV_THRESHOLD", "0.08"))
    )
    l5_entropy_threshold: float = field(
        default_factory=lambda: float(_env("L5_ENTROPY_THRESHOLD", "1.0"))
    )

    # --- Bluetooth transport thresholds ---
    # Defaults mirror USB calibrated values until BT-specific calibration (N>=50 BT sessions).
    # BT polling ~125-250 Hz vs USB 1000 Hz; 50-report windows cover 4x more wall-clock time.
    # Recalibrate: python scripts/threshold_calibrator.py sessions/bt/*.json
    bt_l4_anomaly_threshold: float = field(
        default_factory=lambda: float(
            _env("BT_L4_ANOMALY_THRESHOLD", _env("L4_ANOMALY_THRESHOLD", "6.905"))
        )
    )
    bt_l5_cv_threshold: float = field(
        default_factory=lambda: float(
            _env("BT_L5_CV_THRESHOLD", _env("L5_CV_THRESHOLD", "0.08"))
        )
    )
    bt_polling_rate_hz: float = field(
        default_factory=lambda: float(_env("BT_POLLING_RATE_HZ", "250.0"))
    )

    # --- Phase 37: TournamentGateV3 + Credential Enforcement + AlertRouter ---
    tournament_gate_v3_address: str = field(
        default_factory=lambda: _env("TOURNAMENT_GATE_V3_ADDRESS", "")
    )
    phg_credential_enforcement_enabled: bool = field(
        default_factory=lambda: _env("PHG_CREDENTIAL_ENFORCEMENT_ENABLED", "true").lower() == "true"
    )
    credential_enforcement_min_consecutive: int = field(
        default_factory=lambda: int(_env("CREDENTIAL_ENFORCEMENT_MIN_CONSECUTIVE", "2"))
    )
    credential_suspension_base_days: float = field(
        default_factory=lambda: float(_env("CREDENTIAL_SUSPENSION_BASE_DAYS", "7.0"))
    )
    credential_suspension_max_days: float = field(
        default_factory=lambda: float(_env("CREDENTIAL_SUSPENSION_MAX_DAYS", "28.0"))
    )
    alert_webhook_url: str = field(
        default_factory=lambda: _env("ALERT_WEBHOOK_URL", "")
    )
    alert_webhook_format: str = field(
        default_factory=lambda: _env("ALERT_WEBHOOK_FORMAT", "generic")
    )
    alert_severity_threshold: str = field(
        default_factory=lambda: _env("ALERT_SEVERITY_THRESHOLD", "medium")
    )
    agent_max_history_before_compress: int = field(
        default_factory=lambda: int(_env("AGENT_MAX_HISTORY_BEFORE_COMPRESS", "60"))
    )

    # --- Phase 19: Device Profile (Universal Controller Abstraction) ---
    device_profile_id: str = field(
        default_factory=lambda: _env("DEVICE_PROFILE_ID", "")
    )
    """
    Override controller auto-detection with an explicit profile slug.
    Examples: 'scuf_reflex_pro_v1', 'battle_beaver_dualshock_edge_v1'.
    Empty string (default) = auto-detect from HID VID/PID or fallback to
    'sony_dualshock_edge_v1'.
    """

    auto_detect_device: bool = field(
        default_factory=lambda: _env_bool("AUTO_DETECT_DEVICE", True)
    )
    """
    If True (default), enumerate connected HID devices to find a matching
    DeviceProfile via VID/PID lookup. Disable for headless CI or Docker
    environments where USB enumeration fails or is undesirable.
    """

    # --- Phase C: L6 Active Physical Challenge-Response ---
    l6_challenges_enabled: bool = field(
        default_factory=lambda: _env("L6_CHALLENGES_ENABLED", "false").lower() == "true"
    )
    l6_challenge_interval_ticks: int = field(
        default_factory=lambda: int(_env("L6_CHALLENGE_INTERVAL_TICKS", "300"))
    )
    l6_challenge_timeout_s: float = field(
        default_factory=lambda: float(_env("L6_CHALLENGE_TIMEOUT_S", "3.0"))
    )
    # Phase 42: L6 capture session metadata (set by l6_capture_session.py via PATCH /config)
    l6_capture_player_id: str = field(
        default_factory=lambda: _env("L6_CAPTURE_PLAYER_ID", "")
    )
    l6_capture_game_title: str = field(
        default_factory=lambda: _env("L6_CAPTURE_GAME_TITLE", "")
    )
    l6_capture_hw_session_ref: str = field(
        default_factory=lambda: _env("L6_CAPTURE_HW_SESSION_REF", "")
    )
    l6_capture_notes: str = field(
        default_factory=lambda: _env("L6_CAPTURE_NOTES", "")
    )

    # --- Phase 51: Game-Aware Profiling ---
    game_profile_id: str = field(
        default_factory=lambda: _env("GAME_PROFILE_ID", "")
    )
    """
    Active game profile slug. Overrides L5 button priority and enables L6-Passive.
    Example: 'ncaa_cfb_26'. Empty = default priority, no L6-Passive.
    Set via GAME_PROFILE_ID env var or bridge/.env.
    """

    # --- Phase 55: ioID Device Identity Registry ---
    ioid_registry_address: str = field(
        default_factory=lambda: _env("IOID_REGISTRY_ADDRESS", ""),
    )

    # --- Phase 56: ZK Tournament Passport ---
    tournament_passport_address: str = field(
        default_factory=lambda: _env("TOURNAMENT_PASSPORT_ADDRESS", ""),
    )

    # --- Phase 63: L6b Neuromuscular Reflex Layer ---
    l6b_enabled: bool = field(
        default_factory=lambda: _env("L6B_ENABLED", "false").lower() == "true"
    )
    """False by default — L6b disabled unless L6B_ENABLED=true env var set."""
    l6b_probe_interval_ticks: int = field(
        default_factory=lambda: int(_env("L6B_PROBE_INTERVAL_TICKS", "6750"))
    )
    """Ticks between L6b probe opportunities. Default 6750 ≈ 67.5s at 100Hz session loop."""
    l6b_accel_delta_threshold_lsb: float = field(
        default_factory=lambda: float(_env("L6B_ACCEL_DELTA_THRESHOLD_LSB", "500.0"))
    )
    """Min |accel_mag - pre_mean| (LSB) to count as a reflex impulse above noise floor."""
    l6b_human_min_ms: float = field(
        default_factory=lambda: float(_env("L6B_HUMAN_MIN_MS", "80.0"))
    )
    """Minimum latency (ms) to classify as HUMAN reflex (spinal reflex arc lower bound)."""
    l6b_human_max_ms: float = field(
        default_factory=lambda: float(_env("L6B_HUMAN_MAX_MS", "280.0"))
    )
    """Maximum latency (ms) to classify as HUMAN reflex (cortical loop upper bound)."""

    # --- Phase 62: Player Enrollment Ceremony ---
    enrollment_min_sessions: int = field(
        default_factory=lambda: _env_int("ENROLLMENT_MIN_SESSIONS", 10)
    )
    """Minimum NOMINAL PITL sessions required to qualify for PHGCredential mint."""
    enrollment_humanity_min: float = field(
        default_factory=lambda: float(_env("ENROLLMENT_HUMANITY_MIN", "0.60"))
    )
    """Minimum average humanity probability (across NOMINAL sessions) for enrollment."""

    def validate(self) -> list[str]:
        """Return list of configuration errors (empty = valid)."""
        errors = []
        if not self.verifier_address:
            errors.append("POAC_VERIFIER_ADDRESS is required")
        # Key validation depends on source
        source = getattr(self, "bridge_private_key_source", "env")
        if source == "keystore":
            if not getattr(self, "keystore_path", ""):
                errors.append("BRIDGE_KEYSTORE_PATH is required when BRIDGE_PRIVATE_KEY_SOURCE=keystore")
        else:
            if not self.bridge_private_key:
                errors.append("BRIDGE_PRIVATE_KEY is required")
        if not any([self.mqtt_enabled, self.coap_enabled,
                    self.http_enabled, self.dualshock_enabled]):
            errors.append("At least one transport must be enabled")
        return errors
