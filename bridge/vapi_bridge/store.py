"""
SQLite Persistence — Records, devices, and submission tracking.

Zero external dependencies (uses Python stdlib sqlite3).
Thread-safe via WAL mode and connection-per-call pattern.
"""

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .codec import PoACRecord

log = logging.getLogger(__name__)

# Record submission status
STATUS_PENDING = "pending"
STATUS_BATCHED = "batched"
STATUS_SUBMITTED = "submitted"
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
STATUS_DEAD_LETTER = "dead_letter"


class Store:
    """SQLite-backed persistence for the bridge service."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    _PITL_MIGRATION_COLS = [
        "ALTER TABLE records ADD COLUMN pitl_l4_distance REAL",
        "ALTER TABLE records ADD COLUMN pitl_l4_warmed INTEGER",
        "ALTER TABLE records ADD COLUMN pitl_l4_features TEXT",
        "ALTER TABLE records ADD COLUMN pitl_l5_cv REAL",
        "ALTER TABLE records ADD COLUMN pitl_l5_entropy REAL",
        "ALTER TABLE records ADD COLUMN pitl_l5_quant REAL",
        "ALTER TABLE records ADD COLUMN pitl_l5_signals INTEGER",
    ]

    # Phase 23: idempotent schema migrations
    _PHASE23_MIGRATIONS = [
        "ALTER TABLE phg_checkpoints ADD COLUMN last_committed_score INTEGER DEFAULT 0",
    ]

    # Phase 25: idempotent schema migrations
    _PHASE25_MIGRATIONS = [
        "ALTER TABLE records ADD COLUMN pitl_l5_rhythm_humanity REAL",
        "ALTER TABLE records ADD COLUMN pitl_l4_drift_velocity REAL",
        "ALTER TABLE records ADD COLUMN pitl_e4_cognitive_drift REAL",
        "ALTER TABLE records ADD COLUMN pitl_humanity_prob REAL",
        "ALTER TABLE phg_checkpoints ADD COLUMN confirmed INTEGER DEFAULT 0",
    ]

    # Phase 26: idempotent schema migrations
    _PHASE26_MIGRATIONS = [
        "ALTER TABLE records ADD COLUMN pitl_proof_nullifier TEXT DEFAULT NULL",
    ]

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id       TEXT PRIMARY KEY,
                    pubkey_hex      TEXT NOT NULL,
                    first_seen      REAL NOT NULL,
                    last_seen       REAL NOT NULL,
                    last_counter    INTEGER DEFAULT 0,
                    chain_head      TEXT DEFAULT '',
                    last_battery    INTEGER DEFAULT 0,
                    last_latitude   REAL DEFAULT 0.0,
                    last_longitude  REAL DEFAULT 0.0,
                    records_total   INTEGER DEFAULT 0,
                    records_verified INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS records (
                    record_hash     TEXT PRIMARY KEY,
                    device_id       TEXT NOT NULL,
                    counter         INTEGER NOT NULL,
                    timestamp_ms    INTEGER NOT NULL,
                    inference       INTEGER NOT NULL,
                    action_code     INTEGER NOT NULL,
                    confidence      INTEGER NOT NULL,
                    battery_pct     INTEGER NOT NULL,
                    bounty_id       INTEGER DEFAULT 0,
                    latitude        REAL DEFAULT 0.0,
                    longitude       REAL DEFAULT 0.0,
                    status          TEXT DEFAULT 'pending',
                    raw_data        BLOB,
                    created_at      REAL NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES devices(device_id)
                );

                CREATE TABLE IF NOT EXISTS submissions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    tx_hash         TEXT DEFAULT '',
                    record_hashes   TEXT NOT NULL,  -- JSON array
                    status          TEXT DEFAULT 'pending',
                    retries         INTEGER DEFAULT 0,
                    last_error      TEXT DEFAULT '',
                    created_at      REAL NOT NULL,
                    submitted_at    REAL DEFAULT 0,
                    confirmed_at    REAL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_records_status
                    ON records(status);
                CREATE INDEX IF NOT EXISTS idx_records_device
                    ON records(device_id, counter);
                CREATE INDEX IF NOT EXISTS idx_submissions_status
                    ON submissions(status);

                CREATE TABLE IF NOT EXISTS phg_checkpoints (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id       TEXT NOT NULL,
                    phg_score       INTEGER NOT NULL,
                    record_count    INTEGER NOT NULL,
                    bio_hash        TEXT NOT NULL DEFAULT '',
                    tx_hash         TEXT NOT NULL DEFAULT '',
                    committed_at    REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_phg_checkpoints_device
                    ON phg_checkpoints(device_id, committed_at);

                CREATE TABLE IF NOT EXISTS biometric_fingerprint_store (
                    device_id   TEXT PRIMARY KEY,
                    mean_json   TEXT NOT NULL,
                    var_json    TEXT NOT NULL,
                    n_sessions  INTEGER DEFAULT 0,
                    updated_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS continuity_claims (
                    device_id   TEXT PRIMARY KEY,
                    claimed_by  TEXT NOT NULL,
                    claimed_at  REAL NOT NULL
                );
            """)
            # PITL extension columns — idempotent (skip if already exist)
            for sql in self._PITL_MIGRATION_COLS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    log.debug("schema migration already applied: %.80s", sql)  # Phase 54
            # Phase 23 migrations — idempotent
            for sql in self._PHASE23_MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    log.debug("schema migration already applied: %.80s", sql)  # Phase 54
            # Phase 25 migrations — idempotent
            for sql in self._PHASE25_MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    log.debug("schema migration already applied: %.80s", sql)  # Phase 54
            # Phase 25: cognitive trajectory table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cognitive_trajectory (
                    device_id      TEXT PRIMARY KEY,
                    embedding_json TEXT NOT NULL,
                    session_count  INTEGER NOT NULL,
                    updated_at     REAL NOT NULL
                )
            """)
            # Phase 26: PITL session proofs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pitl_session_proofs (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id          TEXT NOT NULL,
                    nullifier_hash     TEXT NOT NULL UNIQUE,
                    feature_commitment TEXT NOT NULL,
                    humanity_prob_int  INTEGER NOT NULL,
                    tx_hash            TEXT DEFAULT '',
                    created_at         REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pitl_proofs_device
                    ON pitl_session_proofs(device_id, created_at)
            """)
            # Phase 26 migrations — idempotent
            for sql in self._PHASE26_MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    log.debug("schema migration already applied: %.80s", sql)  # Phase 54
            # Phase 28: PHG credential mint ledger
            conn.execute("""
                CREATE TABLE IF NOT EXISTS phg_credential_mints (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id     TEXT NOT NULL UNIQUE,
                    credential_id INTEGER NOT NULL,
                    tx_hash       TEXT DEFAULT '',
                    minted_at     REAL NOT NULL
                )
            """)
            # Phase 31: BridgeAgent conversation session persistence
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id   TEXT PRIMARY KEY,
                    history_json TEXT NOT NULL DEFAULT '[]',
                    created_at   REAL NOT NULL,
                    updated_at   REAL NOT NULL
                )
            """)
            # Phase 32: Proactive protocol intelligence audit trail
            conn.execute("""
                CREATE TABLE IF NOT EXISTS protocol_insights (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    insight_type TEXT NOT NULL,
                    device_id    TEXT DEFAULT '',
                    content      TEXT NOT NULL,
                    severity     TEXT DEFAULT 'low',
                    created_at   REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_insights_type
                ON protocol_insights(insight_type, created_at)
            """)
            # Phase 34: Cross-bridge cluster correlation registry
            conn.execute("""
                CREATE TABLE IF NOT EXISTS federation_registry (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    cluster_hash     TEXT NOT NULL,
                    peer_url         TEXT NOT NULL DEFAULT '',
                    device_count     INTEGER NOT NULL DEFAULT 0,
                    suspicion_bucket TEXT NOT NULL DEFAULT 'medium',
                    bridge_id        TEXT NOT NULL DEFAULT '',
                    detected_at      REAL NOT NULL,
                    is_local         INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_federation_hash
                ON federation_registry(cluster_hash, bridge_id)
            """)
            # Phase 35: Longitudinal insight synthesis tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS insight_digests (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    window_label     TEXT NOT NULL,
                    synthesized_at   REAL NOT NULL,
                    bot_farm_count   INTEGER NOT NULL DEFAULT 0,
                    high_risk_count  INTEGER NOT NULL DEFAULT 0,
                    federated_count  INTEGER NOT NULL DEFAULT 0,
                    anomaly_count    INTEGER NOT NULL DEFAULT 0,
                    eligible_count   INTEGER NOT NULL DEFAULT 0,
                    dominant_severity TEXT NOT NULL DEFAULT 'low',
                    top_devices      TEXT NOT NULL DEFAULT '[]',
                    narrative        TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_digests_window
                ON insight_digests(window_label, synthesized_at DESC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_risk_labels (
                    device_id    TEXT PRIMARY KEY,
                    risk_label   TEXT NOT NULL DEFAULT 'stable',
                    label_evidence TEXT NOT NULL DEFAULT '{}',
                    label_set_at REAL NOT NULL,
                    prior_label  TEXT NOT NULL DEFAULT ''
                )
            """)
            # Phase 36: Adaptive detection policies + schema version registry
            conn.execute("""
                CREATE TABLE IF NOT EXISTS detection_policies (
                    device_id    TEXT PRIMARY KEY,
                    multiplier   REAL NOT NULL DEFAULT 1.0,
                    basis_label  TEXT NOT NULL DEFAULT 'stable',
                    set_at       REAL NOT NULL,
                    expires_at   REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    phase          INTEGER PRIMARY KEY,
                    migration_name TEXT NOT NULL,
                    applied_at     REAL NOT NULL
                )
            """)
            # Phase 37: Credential enforcement state
            conn.execute("""
                CREATE TABLE IF NOT EXISTS credential_enforcement (
                    device_id           TEXT PRIMARY KEY,
                    consecutive_critical INT  NOT NULL DEFAULT 0,
                    suspended           INT  NOT NULL DEFAULT 0,
                    suspended_since     REAL,
                    suspended_until     REAL,
                    evidence_hash       TEXT,
                    last_updated        REAL NOT NULL
                )
            """)
            # Phase 38: Per-player living calibration profiles
            conn.execute("""
                CREATE TABLE IF NOT EXISTS player_calibration_profiles (
                    device_id             TEXT PRIMARY KEY,
                    anomaly_threshold     REAL NOT NULL,
                    continuity_threshold  REAL NOT NULL,
                    baseline_mean         REAL NOT NULL,
                    baseline_std          REAL NOT NULL,
                    session_count         INTEGER NOT NULL,
                    updated_at            TEXT NOT NULL
                )
            """)
            # Phase 42: L6 human-response baseline capture
            conn.execute("""
                CREATE TABLE IF NOT EXISTS l6_capture_sessions (
                    session_id       TEXT PRIMARY KEY,
                    profile_id       INTEGER NOT NULL,
                    profile_name     TEXT NOT NULL DEFAULT '',
                    challenge_sent_ts REAL NOT NULL,
                    onset_ms         REAL NOT NULL DEFAULT 0.0,
                    settle_ms        REAL NOT NULL DEFAULT 0.0,
                    peak_delta       REAL NOT NULL DEFAULT 0.0,
                    grip_variance    REAL NOT NULL DEFAULT 0.0,
                    r2_pre_mean      REAL NOT NULL DEFAULT 0.0,
                    accel_variance   REAL NOT NULL DEFAULT 0.0,
                    player_id        TEXT NOT NULL DEFAULT '',
                    game_title       TEXT NOT NULL DEFAULT '',
                    hw_session_ref   TEXT NOT NULL DEFAULT '',
                    notes            TEXT NOT NULL DEFAULT '',
                    created_at       REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_l6_captures_profile
                ON l6_capture_sessions(profile_id, player_id, created_at)
            """)
            # Phase 50: Agent coordination tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type   TEXT NOT NULL,
                    device_id    TEXT,
                    payload_json TEXT NOT NULL,
                    source_agent TEXT NOT NULL,
                    target_agent TEXT,
                    created_at   REAL NOT NULL,
                    consumed_at  REAL,
                    consumed_by  TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_events_target "
                "ON agent_events(target_agent, consumed_at, created_at)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS threshold_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    threshold_type  TEXT NOT NULL,
                    device_id       TEXT,
                    old_value       REAL,
                    new_value       REAL,
                    drift_pct       REAL,
                    sessions_used   INTEGER,
                    phase           TEXT,
                    created_at      REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_threshold_history_type "
                "ON threshold_history(threshold_type, created_at)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calibration_agent_sessions (
                    session_id   TEXT PRIMARY KEY,
                    history_json TEXT NOT NULL,
                    updated_at   REAL NOT NULL
                )
            """)
            # Phase 55: ioID device identity registry
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ioid_devices (
                    device_id      TEXT PRIMARY KEY,
                    device_address TEXT NOT NULL,
                    did            TEXT NOT NULL,
                    tx_hash        TEXT NOT NULL DEFAULT '',
                    registered_at  REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ioid_devices_did "
                "ON ioid_devices(did)"
            )
            # Phase 56: tournament passport registry
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tournament_passports (
                    device_id        TEXT PRIMARY KEY,
                    passport_hash    TEXT NOT NULL,
                    ioid_token_id    INTEGER NOT NULL DEFAULT 0,
                    min_humanity_int INTEGER NOT NULL DEFAULT 0,
                    tx_hash          TEXT NOT NULL DEFAULT '',
                    on_chain         INTEGER NOT NULL DEFAULT 0,
                    issued_at        REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tournament_passports_issued "
                "ON tournament_passports(issued_at DESC)"
            )
            # Phase 58: operator audit log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operator_audit_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint     TEXT NOT NULL,
                    method       TEXT NOT NULL DEFAULT 'POST',
                    device_id    TEXT DEFAULT '',
                    api_key_hash TEXT DEFAULT '',
                    source_ip    TEXT DEFAULT '',
                    status_code  INTEGER NOT NULL,
                    outcome      TEXT NOT NULL,
                    ts           REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_log_device
                ON operator_audit_log(device_id, ts DESC)
            """)
            # Phase 58 migrations — idempotent
            for sql in ["ALTER TABLE pitl_session_proofs ADD COLUMN inference_code INTEGER DEFAULT NULL"]:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    log.debug("schema migration already applied: %.80s", sql)
            # Phase 61: frame replay checkpoints
            conn.execute("""
                CREATE TABLE IF NOT EXISTS frame_checkpoints (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id     TEXT NOT NULL,
                    record_hash   TEXT NOT NULL,
                    frames_json   TEXT NOT NULL,
                    frame_count   INTEGER NOT NULL,
                    checkpoint_ts REAL NOT NULL,
                    created_at    REAL NOT NULL,
                    FOREIGN KEY (record_hash) REFERENCES records(record_hash)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_frame_checkpoints_device
                ON frame_checkpoints(device_id, created_at DESC)
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_frame_checkpoints_record
                ON frame_checkpoints(record_hash)
            """)
            # Phase 62: Player enrollment ceremony state machine
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_enrollments (
                    device_id          TEXT PRIMARY KEY,
                    sessions_nominal   INTEGER NOT NULL DEFAULT 0,
                    sessions_total     INTEGER NOT NULL DEFAULT 0,
                    avg_humanity       REAL NOT NULL DEFAULT 0.0,
                    status             TEXT NOT NULL DEFAULT 'pending',
                    eligible_at        REAL,
                    credentialed_at    REAL,
                    tx_hash            TEXT DEFAULT '',
                    last_updated       REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_enrollments_status
                ON device_enrollments(status, eligible_at)
            """)
            # Phase 63: L6b neuromuscular reflex probe log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS l6b_probe_log (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id        TEXT    NOT NULL,
                    probe_ts_ms      INTEGER NOT NULL,
                    latency_ms       REAL,
                    classification   TEXT    NOT NULL,
                    accel_delta_peak REAL    NOT NULL DEFAULT 0.0,
                    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_l6b_device
                ON l6b_probe_log(device_id)
            """)
            # Bootstrap schema version history (idempotent INSERT OR IGNORE)
            for _ph, _nm in [
                (21, "pitl_sidecar"), (22, "phg_checkpoints"),
                (23, "biometric_continuity"), (24, "phg_delta_fix"),
                (25, "agent_intelligence"), (26, "zk_pitl"),
                (27, "session_proofs"), (28, "phg_credential"),
                (29, "operator_gate"), (30, "bridge_agent"),
                (31, "session_persistence"), (32, "proactive_monitor"),
                (34, "federation_bus"), (35, "insight_synthesizer"),
                (36, "adaptive_feedback"), (37, "credential_enforcement"),
                (38, "living_calibration"), (42, "l6_calibration_capture"),
                (50, "phase50_agent_coordination"),
                (51, "game_aware_profiling"),
                (55, "ioid_device_identity"),
                (56, "tournament_passport"),
                (58, "security_hardening"),
                (59, "controller_twin"),
                (61, "session_replay"),
                (62, "enrollment_ceremony"),
                (63, "l6b_reflex_layer"),
            ]:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_versions (phase, migration_name, applied_at)"
                    " VALUES (?, ?, ?)",
                    (_ph, _nm, time.time()),
                )

    # --- Device operations ---

    def upsert_device(self, device_id: str, pubkey_hex: str):
        now = time.time()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO devices (device_id, pubkey_hex, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET last_seen = ?
            """, (device_id, pubkey_hex, now, now, now))

    def update_device_state(self, device_id: str, record: PoACRecord):
        with self._conn() as conn:
            conn.execute("""
                UPDATE devices SET
                    last_seen = ?,
                    last_counter = ?,
                    chain_head = ?,
                    last_battery = ?,
                    last_latitude = ?,
                    last_longitude = ?,
                    records_total = records_total + 1
                WHERE device_id = ?
            """, (
                time.time(),
                record.monotonic_ctr,
                record.record_hash_hex,
                record.battery_pct,
                record.latitude,
                record.longitude,
                device_id,
            ))

    def get_device(self, device_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_device_pubkey(self, device_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT pubkey_hex FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            return row["pubkey_hex"] if row else None

    def list_devices(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY last_seen DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Record operations ---

    def insert_record(self, record: PoACRecord, raw_data: bytes) -> bool:
        """Insert a record. Returns False if duplicate."""
        try:
            with self._conn() as conn:
                conn.execute("""
                    INSERT INTO records
                        (record_hash, device_id, counter, timestamp_ms,
                         inference, action_code, confidence, battery_pct,
                         bounty_id, latitude, longitude, status, raw_data,
                         created_at,
                         pitl_l4_distance, pitl_l4_warmed, pitl_l4_features,
                         pitl_l5_cv, pitl_l5_entropy, pitl_l5_quant, pitl_l5_signals,
                         pitl_l5_rhythm_humanity, pitl_l4_drift_velocity,
                         pitl_e4_cognitive_drift, pitl_humanity_prob)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.record_hash_hex,
                    record.device_id_hex,
                    record.monotonic_ctr,
                    record.timestamp_ms,
                    record.inference_result,
                    record.action_code,
                    record.confidence,
                    record.battery_pct,
                    record.bounty_id,
                    record.latitude,
                    record.longitude,
                    STATUS_PENDING,
                    raw_data,
                    time.time(),
                    record.pitl_l4_distance,
                    int(record.pitl_l4_warmed_up) if record.pitl_l4_warmed_up is not None else None,
                    record.pitl_l4_features_json,
                    record.pitl_l5_cv,
                    record.pitl_l5_entropy_bits,
                    record.pitl_l5_quant_score,
                    record.pitl_l5_anomaly_signals,
                    getattr(record, "pitl_l5_rhythm_humanity", None),
                    getattr(record, "pitl_l4_drift_velocity", None),
                    getattr(record, "pitl_e4_cognitive_drift", None),
                    getattr(record, "pitl_humanity_prob", None),
                ))
            return True
        except sqlite3.IntegrityError:
            log.debug("Duplicate record: %s", record.record_hash_hex[:16])
            return False

    def get_pending_records(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM records
                WHERE status = ?
                ORDER BY counter ASC
                LIMIT ?
            """, (STATUS_PENDING, limit)).fetchall()
            return [dict(r) for r in rows]

    def update_record_status(self, record_hash: str, status: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE records SET status = ? WHERE record_hash = ?",
                (status, record_hash),
            )

    def batch_update_status(self, record_hashes: list[str], status: str):
        with self._conn() as conn:
            conn.executemany(
                "UPDATE records SET status = ? WHERE record_hash = ?",
                [(status, h) for h in record_hashes],
            )

    def increment_device_verified(self, device_id: str, count: int = 1):
        with self._conn() as conn:
            conn.execute("""
                UPDATE devices SET records_verified = records_verified + ?
                WHERE device_id = ?
            """, (count, device_id))

    # --- Submission tracking ---

    def create_submission(self, record_hashes: list[str]) -> int:
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO submissions (record_hashes, status, created_at)
                VALUES (?, ?, ?)
            """, (json.dumps(record_hashes), STATUS_PENDING, time.time()))
            return cursor.lastrowid

    def update_submission(
        self, sub_id: int, *, status: str = None, tx_hash: str = None,
        error: str = None, retries: int = None,
    ):
        parts, params = [], []
        if status:
            parts.append("status = ?")
            params.append(status)
        if tx_hash:
            parts.append("tx_hash = ?")
            params.append(tx_hash)
            parts.append("submitted_at = ?")
            params.append(time.time())
        if error is not None:
            parts.append("last_error = ?")
            params.append(error)
        if retries is not None:
            parts.append("retries = ?")
            params.append(retries)
        if status == STATUS_VERIFIED:
            parts.append("confirmed_at = ?")
            params.append(time.time())

        if not parts:
            return

        params.append(sub_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE submissions SET {', '.join(parts)} WHERE id = ?",
                params,
            )

    def get_failed_submissions(self, max_retries: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM submissions
                WHERE status = ? AND retries < ?
                ORDER BY created_at ASC
            """, (STATUS_FAILED, max_retries)).fetchall()
            return [dict(r) for r in rows]

    # --- Statistics ---

    def get_stats(self) -> dict:
        with self._conn() as conn:
            devices = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
            records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM records WHERE status = ?",
                (STATUS_PENDING,),
            ).fetchone()[0]
            verified = conn.execute(
                "SELECT COUNT(*) FROM records WHERE status = ?",
                (STATUS_VERIFIED,),
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM records WHERE status = ?",
                (STATUS_FAILED,),
            ).fetchone()[0]
            dead = conn.execute(
                "SELECT COUNT(*) FROM records WHERE status = ?",
                (STATUS_DEAD_LETTER,),
            ).fetchone()[0]
            submissions = conn.execute(
                "SELECT COUNT(*) FROM submissions"
            ).fetchone()[0]

            return {
                "devices_active": devices,
                "records_total": records,
                "records_pending": pending,
                "records_verified": verified,
                "records_failed": failed,
                "records_dead_letter": dead,
                "submissions_total": submissions,
            }

    def get_recent_records(self, limit: int = 50, device_id: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if device_id:
                rows = conn.execute("""
                    SELECT r.*, d.pubkey_hex FROM records r
                    LEFT JOIN devices d ON r.device_id = d.device_id
                    WHERE r.device_id = ?
                    ORDER BY r.created_at DESC
                    LIMIT ?
                """, (device_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT r.*, d.pubkey_hex FROM records r
                    LEFT JOIN devices d ON r.device_id = d.device_id
                    ORDER BY r.created_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_player_profile(self, device_id: str) -> dict | None:
        """PHG Trust Score, record counts, confidence mean, PHCI context."""
        with self._conn() as conn:
            dev = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if not dev:
                return None
            dev = dict(dev)

            # Aggregate stats from records
            agg = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN inference = 32 THEN 1 ELSE 0 END) as nominal_count,
                    AVG(CASE WHEN inference = 32 THEN confidence ELSE NULL END) as conf_mean,
                    SUM(CASE WHEN inference = 32
                             THEN CAST(CAST(confidence AS REAL) / 255 * 10 AS INTEGER)
                             ELSE 0 END) as phg_score_raw,
                    SUM(CASE WHEN inference = 32
                             THEN CAST(
                                 CAST(confidence AS REAL) / 255 * 10
                                 * (1.0 + COALESCE(pitl_humanity_prob, 0.0) * 0.5)
                             AS INTEGER)
                             ELSE 0 END) as phg_score_weighted,
                    AVG(CASE WHEN inference = 32 AND pitl_humanity_prob IS NOT NULL
                             THEN pitl_humanity_prob ELSE NULL END) as humanity_prob_avg,
                    AVG(CASE WHEN inference = 32 AND pitl_l5_rhythm_humanity IS NOT NULL
                             THEN pitl_l5_rhythm_humanity ELSE NULL END) as l5_rhythm_humanity_avg,
                    MIN(created_at) as first_record_at,
                    MAX(created_at) as last_record_at
                FROM records
                WHERE device_id = ?
            """, (device_id,)).fetchone()
            agg = dict(agg)

            phg_score = int(agg["phg_score_raw"] or 0)
            return {
                "device_id":      device_id,
                "phg_score":      phg_score,
                "phg_score_weighted": int(agg["phg_score_weighted"] or 0),
                "humanity_prob_avg": round(agg["humanity_prob_avg"] or 0.0, 4),
                "l5_rhythm_humanity_avg": round(agg["l5_rhythm_humanity_avg"] or 0.0, 4),
                "total_records":  agg["total"] or 0,
                "nominal_records": agg["nominal_count"] or 0,
                "confidence_mean": round(agg["conf_mean"] or 0, 1),
                "first_seen":     dev["first_seen"],
                "last_seen":      dev["last_seen"],
                "records_verified": dev["records_verified"],
                "first_record_at": agg["first_record_at"],
                "last_record_at":  agg["last_record_at"],
            }

    def get_pitl_timeline(self, minutes: int = 10) -> list[dict]:
        """PITL detection events bucketed by 1-minute intervals (non-NOMINAL only)."""
        since = time.time() - minutes * 60
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    CAST(created_at / 60 AS INTEGER) * 60 AS bucket,
                    inference,
                    COUNT(*) as cnt
                FROM records
                WHERE created_at > ? AND inference != 32
                GROUP BY bucket, inference
                ORDER BY bucket
            """, (since,)).fetchall()
            return [dict(r) for r in rows]

    # --- PHG Registry (Phase 22) ---

    def get_verified_nominal_count(self, device_id: str) -> int:
        """Count of verified NOMINAL records for this device (from devices table)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT records_verified FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            return row["records_verified"] if row else 0

    def get_last_phg_checkpoint(self, device_id: str) -> dict | None:
        """Return the most recently *confirmed* PHG checkpoint for a device, or None.

        Phase 25: filters to confirmed=1 only so unconfirmed checkpoints are never
        used as the cumulative-score delta baseline.
        """
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM phg_checkpoints
                WHERE device_id = ? AND confirmed = 1
                ORDER BY id DESC
                LIMIT 1
            """, (device_id,)).fetchone()
            return dict(row) if row else None

    def get_phg_checkpoint_data(self, device_id: str) -> dict | None:
        """Returns PHG score DELTA + biometric hash for the next checkpoint commit.

        Phase 23 fix: returns the delta since the last committed checkpoint, not
        the cumulative score. This prevents the on-chain cumulativeScore from being
        inflated by a factor of checkpoint_count.
        """
        profile = self.get_player_profile(device_id)
        if profile is None:
            return None
        # Phase 25: use weighted score when available for checkpoint deltas
        cumulative_score = profile.get("phg_score_weighted", profile["phg_score"])
        last_row = self.get_last_phg_checkpoint(device_id)
        last_committed = last_row["last_committed_score"] if last_row else 0
        score_delta = max(0, cumulative_score - last_committed)

        fingerprint = self.get_biometric_fingerprint(device_id)
        if fingerprint:
            import json as _json
            fingerprint_json = _json.dumps(fingerprint, sort_keys=True)
            import hashlib as _hashlib
            bio_hash = _hashlib.sha256(fingerprint_json.encode()).digest()
        else:
            bio_hash = bytes(32)
        return {
            "phg_score":       score_delta,
            "biometric_hash":  bio_hash,
            "cumulative_score": cumulative_score,
        }

    def store_phg_checkpoint(
        self,
        device_id: str,
        phg_score: int,
        record_count: int,
        bio_hash_hex: str,
        tx_hash: str,
        cumulative_score: int = 0,
        confirmed: bool = False,
    ):
        """Persist a committed PHG checkpoint for dashboard display.

        cumulative_score is the true cumulative PHG score at the time of commit.
        It is written to last_committed_score so that future delta calculations
        read the correct cumulative baseline (not the previous delta).
        confirmed=True when the transaction receipt status==1 was observed.
        """
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO phg_checkpoints
                    (device_id, phg_score, record_count, bio_hash, tx_hash, committed_at,
                     last_committed_score, confirmed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (device_id, phg_score, record_count, bio_hash_hex, tx_hash, time.time(),
                  cumulative_score, int(confirmed)))

    def get_phg_checkpoints(self, device_id: str, limit: int = 20) -> list[dict]:
        """Return the most recent PHG checkpoints for a device."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM phg_checkpoints
                WHERE device_id = ?
                ORDER BY committed_at DESC
                LIMIT ?
            """, (device_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def mark_checkpoint_confirmed(self, tx_hash: str) -> None:
        """Mark a PHG checkpoint as confirmed by on-chain event (Phase 25)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE phg_checkpoints SET confirmed = 1 WHERE tx_hash = ?",
                (tx_hash,),
            )

    def get_unconfirmed_checkpoints(self, age_s: float = 300.0) -> list[dict]:
        """Return PHG checkpoints that are older than age_s seconds and still unconfirmed (Phase 25)."""
        cutoff = time.time() - age_s
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT * FROM phg_checkpoints
                WHERE confirmed = 0 AND committed_at < ?
                ORDER BY committed_at ASC
            """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    def get_biometric_fingerprint(self, device_id: str) -> dict | None:
        """Average of L4 feature vectors from the 20 most recent NOMINAL records."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT pitl_l4_features FROM records
                WHERE device_id = ? AND inference = 32
                  AND pitl_l4_features IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 20
            """, (device_id,)).fetchall()

        if not rows:
            return None

        import json
        feature_sum: dict[str, float] = {}
        count = 0
        for row in rows:
            try:
                feats = json.loads(row["pitl_l4_features"])
                for k, v in feats.items():
                    feature_sum[k] = feature_sum.get(k, 0.0) + float(v)
                count += 1
            except Exception:
                continue

        if count == 0:
            return None
        return {k: v / count for k, v in feature_sum.items()}

    # --- Phase 23: Biometric Fingerprint State Store ---

    def store_fingerprint_state(
        self,
        device_id: str,
        mean_dict: dict,
        var_dict: dict,
        n_sessions: int,
    ):
        """Persist the classifier's mean and variance arrays for cross-session distance computation."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO biometric_fingerprint_store
                    (device_id, mean_json, var_json, n_sessions, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    mean_json  = excluded.mean_json,
                    var_json   = excluded.var_json,
                    n_sessions = excluded.n_sessions,
                    updated_at = excluded.updated_at
            """, (
                device_id,
                json.dumps(mean_dict, sort_keys=True),
                json.dumps(var_dict, sort_keys=True),
                n_sessions,
                time.time(),
            ))

    def get_fingerprint_variance(self, device_id: str):
        """Return the stored variance vector as a numpy array, or None if not available.

        Returns numpy ndarray of shape (7,) in FEATURE_KEYS canonical order, or None.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT var_json FROM biometric_fingerprint_store WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            import numpy as np
            from .continuity_prover import FEATURE_KEYS
            var_dict = json.loads(row["var_json"])
            # Return values in canonical FEATURE_KEYS order so the vector aligns with
            # the distance computation in ContinuityProver.compute_distance().
            return np.array([var_dict.get(k, 0.0) for k in FEATURE_KEYS], dtype=np.float64)
        except Exception:
            return None

    def mark_device_claimed(self, device_id: str, claimed_by: str):
        """Record that a device has been used in a continuity claim (anti-replay)."""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO continuity_claims (device_id, claimed_by, claimed_at)
                VALUES (?, ?, ?)
            """, (device_id, claimed_by, time.time()))

    def is_device_claimed(self, device_id: str) -> bool:
        """Return True if this device has already been used in a continuity claim."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM continuity_claims WHERE device_id = ?", (device_id,)
            ).fetchone()
            return row is not None

    def get_continuity_chain(self, device_id: str) -> list[dict]:
        """Return all continuity claim records involving this device (as source or destination).

        Each entry: {device_id, claimed_by, claimed_at, direction}
        direction = "source" if this device was the old device; "destination" if the new one.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM continuity_claims WHERE device_id = ? OR claimed_by = ?",
                (device_id, device_id),
            ).fetchall()
            result = []
            for row in rows:
                entry = dict(row)
                entry["direction"] = "source" if entry["claimed_by"] != device_id else "destination"
                result.append(entry)
            return result

    # --- Phase 25: E4 Cognitive Trajectory ---

    def store_cognitive_embedding(
        self, device_id: str, embedding: list, session_count: int
    ):
        """Persist the E4 cognitive embedding for cross-session drift computation."""
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO cognitive_trajectory
                    (device_id, embedding_json, session_count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    embedding_json = excluded.embedding_json,
                    session_count  = excluded.session_count,
                    updated_at     = excluded.updated_at
            """, (device_id, json.dumps(embedding), session_count, time.time()))

    def get_last_cognitive_embedding(self, device_id: str) -> list | None:
        """Return the stored E4 embedding as a list of floats, or None if not available."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT embedding_json FROM cognitive_trajectory WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["embedding_json"])
        except Exception:
            return None

    # --- Phase 26: Behavioral & Network Intelligence ---

    def get_pitl_history(self, device_id: str, limit: int = 100) -> list[dict]:
        """Return PITL sidecar columns from records for longitudinal analysis.

        Filters to records that have at least one non-NULL PITL sidecar to avoid
        empty series in behavioral regression.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT timestamp_ms, inference, confidence,
                       pitl_l4_drift_velocity, pitl_l5_rhythm_humanity,
                       pitl_e4_cognitive_drift, pitl_humanity_prob, pitl_l4_distance
                FROM records
                WHERE device_id = ?
                  AND (pitl_l4_drift_velocity IS NOT NULL OR pitl_humanity_prob IS NOT NULL)
                ORDER BY timestamp_ms DESC
                LIMIT ?
            """, (device_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def get_all_fingerprinted_devices(self) -> list[str]:
        """Return device IDs that have a stored biometric fingerprint."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT device_id FROM biometric_fingerprint_store"
            ).fetchall()
            return [r["device_id"] for r in rows]

    def store_pitl_proof(
        self,
        device_id: str,
        nullifier_hash: str,
        feature_commitment: str,
        humanity_prob_int: int,
        tx_hash: str = "",
    ) -> None:
        """Persist a PITL ZK session proof record (Phase 26)."""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO pitl_session_proofs
                    (device_id, nullifier_hash, feature_commitment,
                     humanity_prob_int, tx_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (device_id, nullifier_hash, feature_commitment,
                  humanity_prob_int, tx_hash, time.time()))

    def get_latest_pitl_proof(self, device_id: str) -> dict | None:
        """Return most recent pitl_session_proofs row for device, or None (Phase 28)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, device_id, nullifier_hash, feature_commitment, "
                "humanity_prob_int, tx_hash, created_at FROM pitl_session_proofs "
                "WHERE device_id=? ORDER BY id DESC LIMIT 1", (device_id,)
            ).fetchone()
        return dict(row) if row else None

    def store_credential_mint(
        self, device_id: str, credential_id: int, tx_hash: str
    ) -> None:
        """Record a successfully minted PHGCredential. INSERT OR IGNORE (idempotent)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO phg_credential_mints "
                "(device_id, credential_id, tx_hash, minted_at) VALUES (?,?,?,?)",
                (device_id, credential_id, tx_hash, time.time()),
            )

    def get_credential_mint(self, device_id: str) -> dict | None:
        """Return credential mint record for device, or None if not minted (Phase 28)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT device_id, credential_id, tx_hash, minted_at "
                "FROM phg_credential_mints WHERE device_id=?", (device_id,)
            ).fetchone()
        return dict(row) if row else None

    # --- Phase 62: Player Enrollment Ceremony ---

    def upsert_enrollment(
        self,
        device_id: str,
        sessions_nominal: int,
        sessions_total: int,
        avg_humanity: float,
        status: str,
        tx_hash: str = "",
    ) -> None:
        """Insert or update enrollment progress for a device. Idempotent."""
        now = time.time()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO device_enrollments
                    (device_id, sessions_nominal, sessions_total, avg_humanity,
                     status, tx_hash, last_updated)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET
                    sessions_nominal=excluded.sessions_nominal,
                    sessions_total=excluded.sessions_total,
                    avg_humanity=excluded.avg_humanity,
                    status=excluded.status,
                    tx_hash=excluded.tx_hash,
                    eligible_at=CASE WHEN excluded.status='eligible' AND status!='eligible'
                                     THEN ? ELSE eligible_at END,
                    credentialed_at=CASE WHEN excluded.status='credentialed'
                                         THEN ? ELSE credentialed_at END,
                    last_updated=?
            """, (device_id, sessions_nominal, sessions_total, avg_humanity,
                  status, tx_hash, now, now, now, now))

    def get_enrollment(self, device_id: str) -> dict | None:
        """Return enrollment row for device, or None if no row exists."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM device_enrollments WHERE device_id=?", (device_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_eligible_unenrolled(self) -> list[dict]:
        """Devices that are eligible but not yet credentialed."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM device_enrollments WHERE status='eligible' "
                "ORDER BY eligible_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_nominal_sessions(self, device_id: str) -> tuple[int, float]:
        """Count PITL session proofs where inference_code is NOMINAL (0x20=32) or NULL.

        Returns (nominal_count, avg_humanity) where avg_humanity is from humanity_prob_int/1000.
        """
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as n, AVG(humanity_prob_int) as avg_hp
                FROM pitl_session_proofs
                WHERE device_id=?
                  AND (inference_code IS NULL OR inference_code = 32)
            """, (device_id,)).fetchone()
        count = int(row["n"]) if row else 0
        avg_hp = float(row["avg_hp"]) / 1000.0 if (row and row["avg_hp"] is not None) else 0.0
        return count, avg_hp

    # --- Phase 63: L6b Neuromuscular Reflex Probe Log ---

    def insert_l6b_probe(
        self,
        device_id: str,
        probe_ts_ms: int,
        latency_ms: float,
        classification: str,
        accel_delta_peak: float,
    ) -> None:
        """Persist one L6b reflex probe result (Phase 63).

        latency_ms=-1.0 indicates NO_RESPONSE (stored as NULL in DB).
        Never raises — caller wraps in try/except.
        """
        _lat = None if latency_ms < 0 else latency_ms
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO l6b_probe_log "
                "(device_id, probe_ts_ms, latency_ms, classification, accel_delta_peak) "
                "VALUES (?, ?, ?, ?, ?)",
                (device_id, probe_ts_ms, _lat, classification, accel_delta_peak),
            )

    def get_l6b_baseline(self, device_id: str) -> dict:
        """Return L6b reflex baseline statistics for a device (Phase 63).

        Returns dict with:
          device_id, probe_count, mean_latency_ms, std_latency_ms,
          classification_distribution (dict[str, int]),
          bot_events (int — count of BOT-classified probes)
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT latency_ms, classification FROM l6b_probe_log WHERE device_id=?",
                (device_id,),
            ).fetchall()
        if not rows:
            return {
                "device_id": device_id,
                "probe_count": 0,
                "mean_latency_ms": None,
                "std_latency_ms": None,
                "classification_distribution": {},
                "bot_events": 0,
            }
        latencies = [float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None]
        dist: dict[str, int] = {}
        for r in rows:
            c = r["classification"]
            dist[c] = dist.get(c, 0) + 1
        mean_lat = sum(latencies) / len(latencies) if latencies else None
        if latencies and len(latencies) > 1:
            var = sum((x - mean_lat) ** 2 for x in latencies) / len(latencies)
            std_lat = var ** 0.5
        else:
            std_lat = None
        return {
            "device_id": device_id,
            "probe_count": len(rows),
            "mean_latency_ms": round(mean_lat, 2) if mean_lat is not None else None,
            "std_latency_ms": round(std_lat, 2) if std_lat is not None else None,
            "classification_distribution": dist,
            "bot_events": dist.get("BOT", 0),
        }

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Return top devices by confirmed cumulative PHG score (Phase 28)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT device_id, MAX(last_committed_score) AS cumulative_score,
                       MAX(record_count) AS record_count
                FROM phg_checkpoints WHERE confirmed = 1
                GROUP BY device_id
                ORDER BY cumulative_score DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_leaderboard_rank(self, device_id: str) -> int | None:
        """Return 1-based rank of device in confirmed PHG leaderboard, or None (Phase 29)."""
        board = self.get_leaderboard(limit=10000)
        for i, entry in enumerate(board, start=1):
            if entry["device_id"] == device_id:
                return i
        return None

    # --- Phase 31: BridgeAgent Session Persistence ---

    def store_agent_session(self, session_id: str, history: list[dict]) -> None:
        """Persist BridgeAgent conversation history (Phase 31)."""
        now = time.time()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO agent_sessions (session_id, history_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    history_json = excluded.history_json,
                    updated_at   = excluded.updated_at
            """, (session_id, json.dumps(history, default=str), now, now))

    def get_agent_session(self, session_id: str) -> list[dict]:
        """Load BridgeAgent conversation history (Phase 31). Returns [] if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT history_json FROM agent_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return []
        try:
            return json.loads(row["history_json"])
        except Exception:
            return []

    def delete_agent_session(self, session_id: str) -> None:
        """Remove an agent session from persistent store (Phase 31)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM agent_sessions WHERE session_id = ?", (session_id,)
            )

    # --- Phase 32: Protocol insights ---

    def store_protocol_insight(self, insight_type: str, content: str,
                                device_id: str = "", severity: str = "low") -> None:
        """Persist a proactive alert or anomaly reaction (Phase 32)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO protocol_insights"
                " (insight_type, device_id, content, severity, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (insight_type, device_id, content, severity, time.time()),
            )

    def get_recent_insights(self, limit: int = 20) -> list:
        """Return most recent protocol insights DESC by created_at (Phase 32)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, insight_type, device_id, content, severity, created_at"
                " FROM protocol_insights ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def prune_old_agent_sessions(self, age_days: float = 30.0) -> int:
        """Delete agent sessions older than age_days. Returns rows deleted (Phase 32)."""
        cutoff = time.time() - age_days * 86400
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM agent_sessions WHERE updated_at < ?", (cutoff,)
            )
        return cur.rowcount

    def prune_old_insights(self, age_days: float = 30.0) -> int:
        """Delete protocol_insights older than age_days. Returns rows deleted (Phase 32)."""
        cutoff = time.time() - age_days * 86400
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM protocol_insights WHERE created_at < ?", (cutoff,)
            )
        return cur.rowcount

    # --- Phase 35: Longitudinal Insight Synthesis ---

    def get_insights_since(self, since: float) -> list:
        """Return all protocol_insights rows created after `since` epoch (Phase 35)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM protocol_insights WHERE created_at >= ? ORDER BY created_at ASC",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]

    def store_insight_digest(self, window_label: str, bot_farm_count: int,
                              high_risk_count: int, federated_count: int,
                              anomaly_count: int, eligible_count: int,
                              dominant_severity: str, top_devices: list,
                              narrative: str) -> None:
        """Persist a longitudinal insight digest for a time window (Phase 35)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO insight_digests"
                " (window_label, synthesized_at, bot_farm_count, high_risk_count,"
                "  federated_count, anomaly_count, eligible_count, dominant_severity,"
                "  top_devices, narrative)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (window_label, time.time(), bot_farm_count, high_risk_count,
                 federated_count, anomaly_count, eligible_count, dominant_severity,
                 json.dumps(top_devices[:5]), narrative),
            )

    def get_latest_digest(self, window_label: str) -> dict | None:
        """Return most recent insight digest for the given window_label (Phase 35)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM insight_digests WHERE window_label=?"
                " ORDER BY synthesized_at DESC LIMIT 1",
                (window_label,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["top_devices"] = json.loads(d.get("top_devices", "[]"))
        return d

    def get_all_latest_digests(self) -> list:
        """Return most recent digest for each window_label (Phase 35)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM insight_digests GROUP BY window_label"
                " HAVING synthesized_at = MAX(synthesized_at)"
                " ORDER BY synthesized_at DESC",
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["top_devices"] = json.loads(d.get("top_devices", "[]"))
            result.append(d)
        return result

    def set_device_risk_label(self, device_id: str, risk_label: str,
                               label_evidence: dict, prior_label: str = "") -> None:
        """Upsert a per-device risk trajectory label (Phase 35)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO device_risk_labels"
                " (device_id, risk_label, label_evidence, label_set_at, prior_label)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET"
                "   risk_label=excluded.risk_label,"
                "   label_evidence=excluded.label_evidence,"
                "   label_set_at=excluded.label_set_at,"
                "   prior_label=excluded.prior_label",
                (device_id, risk_label, json.dumps(label_evidence), time.time(), prior_label),
            )

    def get_device_risk_label(self, device_id: str) -> dict | None:
        """Return the risk trajectory label for a device (Phase 35)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM device_risk_labels WHERE device_id=?", (device_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["label_evidence"] = json.loads(d.get("label_evidence", "{}"))
        return d

    def get_devices_by_risk_label(self, risk_label: str) -> list:
        """Return all devices with the specified risk_label (Phase 35)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM device_risk_labels WHERE risk_label=?"
                " ORDER BY label_set_at DESC",
                (risk_label,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["label_evidence"] = json.loads(d.get("label_evidence", "{}"))
            result.append(d)
        return result

    def prune_old_digests(self, age_days: float = 90.0) -> int:
        """Delete insight_digests older than age_days. Returns rows deleted (Phase 35)."""
        cutoff = time.time() - age_days * 86400
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM insight_digests WHERE synthesized_at < ?", (cutoff,)
            )
        return cur.rowcount

    # --- Phase 36: Adaptive Detection Policies ---

    def store_detection_policy(self, device_id: str, multiplier: float,
                                basis_label: str, expires_at: float) -> None:
        """Upsert an adaptive PITL threshold multiplier for a device (Phase 36)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO detection_policies"
                " (device_id, multiplier, basis_label, set_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET"
                "   multiplier=excluded.multiplier, basis_label=excluded.basis_label,"
                "   set_at=excluded.set_at, expires_at=excluded.expires_at",
                (device_id, multiplier, basis_label, time.time(), expires_at),
            )

    def get_detection_policy(self, device_id: str) -> dict | None:
        """Return active detection policy for device, or None if none/expired (Phase 36)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM detection_policies WHERE device_id=? AND expires_at > ?",
                (device_id, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def get_all_active_policies(self) -> list:
        """Return all non-expired detection policies ordered by set_at DESC (Phase 36)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM detection_policies WHERE expires_at > ?"
                " ORDER BY set_at DESC",
                (time.time(),),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_detection_policy(self, device_id: str) -> None:
        """Remove detection policy for a device (Phase 36)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM detection_policies WHERE device_id=?", (device_id,)
            )

    def record_schema_version(self, phase: int, migration_name: str) -> None:
        """Record a schema migration phase as applied (Phase 36, idempotent)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (phase, migration_name, applied_at)"
                " VALUES (?, ?, ?)",
                (phase, migration_name, time.time()),
            )

    def get_schema_version(self) -> int:
        """Return highest applied phase number from schema_versions (Phase 36)."""
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(phase) FROM schema_versions").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # --- Phase 34: Federation Registry ---

    def store_federation_cluster(self, cluster_hash: str, peer_url: str = "",
                                  device_count: int = 0, suspicion_bucket: str = "medium",
                                  bridge_id: str = "", is_local: bool = False) -> None:
        """Persist a federation cluster record (Phase 34)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO federation_registry"
                " (cluster_hash, peer_url, device_count, suspicion_bucket, bridge_id, detected_at, is_local)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cluster_hash, peer_url, device_count, suspicion_bucket,
                 bridge_id, time.time(), int(is_local)),
            )

    def get_federation_clusters(self, limit: int = 50, is_local=None) -> list:
        """Return federation cluster records, optionally filtered by is_local (Phase 34)."""
        with self._conn() as conn:
            if is_local is None:
                rows = conn.execute(
                    "SELECT * FROM federation_registry ORDER BY detected_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM federation_registry WHERE is_local=?"
                    " ORDER BY detected_at DESC LIMIT ?",
                    (int(is_local), limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_cross_confirmed_hashes(self, min_peers: int = 2) -> list:
        """Return cluster hashes seen by >= min_peers distinct bridges (Phase 34)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT cluster_hash FROM federation_registry"
                " WHERE is_local=0"
                " GROUP BY cluster_hash"
                " HAVING COUNT(DISTINCT bridge_id) >= ?",
                (min_peers,),
            ).fetchall()
        return [r["cluster_hash"] for r in rows]

    def get_latest_world_model_hash(self, device_id: str) -> bytes | None:
        """Return the world_model_hash bytes from the most recent record's raw_data.

        The 164B PoAC body embeds world_model_hash at bytes 96:128.
        raw_data stores the full 228B wire record; body = raw_data[:164].
        """
        with self._conn() as conn:
            row = conn.execute("""
                SELECT raw_data FROM records
                WHERE device_id = ? AND raw_data IS NOT NULL
                ORDER BY timestamp_ms DESC
                LIMIT 1
            """, (device_id,)).fetchone()
        if row is None:
            return None
        raw = bytes(row["raw_data"])
        if len(raw) >= 128:
            return raw[96:128]
        return None

    def get_world_model_hash_chain(self, device_id: str, limit: int = 20) -> list[dict]:
        """Return chronological world_model_hash chain for a device.

        Extracts raw_data[96:128] (world_model_hash field in PoAC body).
        Returns [{timestamp_ms: int, wm_hash_hex: str}] in ascending time order.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT timestamp_ms, raw_data FROM records
                WHERE device_id = ? AND raw_data IS NOT NULL
                  AND length(raw_data) >= 128
                ORDER BY timestamp_ms ASC
                LIMIT ?
            """, (device_id, limit)).fetchall()
        result = []
        for row in rows:
            raw = bytes(row["raw_data"])
            wm_hash = raw[96:128]
            result.append({
                "timestamp_ms": row["timestamp_ms"],
                "wm_hash_hex": wm_hash.hex(),
            })
        return result

    # --- Phase 37: Credential Enforcement ---

    def get_credential_enforcement(self, device_id: str) -> dict | None:
        """Return credential enforcement row for a device, or None (Phase 37)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM credential_enforcement WHERE device_id=?", (device_id,)
            ).fetchone()
        return dict(row) if row else None

    def increment_consecutive_critical(self, device_id: str) -> int:
        """Increment consecutive_critical counter for a device; return new count (Phase 37)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO credential_enforcement (device_id, consecutive_critical, last_updated)"
                " VALUES (?, 1, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET"
                "   consecutive_critical = consecutive_critical + 1,"
                "   last_updated = excluded.last_updated",
                (device_id, time.time()),
            )
            row = conn.execute(
                "SELECT consecutive_critical FROM credential_enforcement WHERE device_id=?",
                (device_id,),
            ).fetchone()
        return int(row[0]) if row else 1

    def reset_consecutive_critical(self, device_id: str) -> None:
        """Reset consecutive_critical to 0 for a device (Phase 37)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO credential_enforcement (device_id, consecutive_critical, last_updated)"
                " VALUES (?, 0, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET"
                "   consecutive_critical = 0, last_updated = excluded.last_updated",
                (device_id, time.time()),
            )

    def store_credential_suspension(self, device_id: str,
                                     evidence_hash: str, until: float) -> None:
        """Record a credential suspension in the DB (Phase 37)."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO credential_enforcement"
                " (device_id, consecutive_critical, suspended, suspended_since,"
                "  suspended_until, evidence_hash, last_updated)"
                " VALUES (?, 0, 1, ?, ?, ?, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET"
                "   suspended=1, suspended_since=excluded.suspended_since,"
                "   suspended_until=excluded.suspended_until,"
                "   evidence_hash=excluded.evidence_hash,"
                "   last_updated=excluded.last_updated",
                (device_id, now, until, evidence_hash, now),
            )

    def is_credential_suspended(self, device_id: str) -> bool:
        """Return True if device has an active credential suspension (Phase 37)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT suspended FROM credential_enforcement WHERE device_id=?",
                (device_id,),
            ).fetchone()
        return bool(row[0]) if row else False

    def clear_credential_suspension(self, device_id: str) -> None:
        """Clear suspension state for a device (Phase 37)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO credential_enforcement"
                " (device_id, consecutive_critical, suspended, last_updated)"
                " VALUES (?, 0, 0, ?)"
                " ON CONFLICT(device_id) DO UPDATE SET"
                "   suspended=0, suspended_since=NULL, suspended_until=NULL,"
                "   evidence_hash=NULL, last_updated=excluded.last_updated",
                (device_id, time.time()),
            )

    def get_all_suspended_credentials(self) -> list:
        """Return all currently suspended credential enforcement rows (Phase 37)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM credential_enforcement WHERE suspended=1"
                " ORDER BY suspended_since DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Phase 38: Living calibration (Mode 6) ---

    def get_nominal_records_for_calibration(self, limit: int = 200) -> list[dict]:
        """Fetch warmed NOMINAL records for living calibration (Phase 38).

        Only includes records where inference=32 (NOMINAL) and the L4 classifier
        had warmed up (pitl_l4_warmed=1), ensuring threshold quality.
        Returns newest-first so exponential decay weights index 0 = most recent.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT device_id, pitl_l4_distance, pitl_l5_cv,
                       pitl_humanity_prob, timestamp_ms
                FROM records
                WHERE inference = 32
                  AND pitl_l4_distance IS NOT NULL
                  AND pitl_l4_warmed = 1
                ORDER BY timestamp_ms DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_player_calibration_profile(
        self,
        device_id: str,
        anomaly_threshold: float,
        continuity_threshold: float,
        baseline_mean: float,
        baseline_std: float,
        session_count: int,
    ) -> None:
        """Insert or replace a per-player calibration profile (Phase 38)."""
        import datetime as _dt
        updated_at = _dt.datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO player_calibration_profiles
                    (device_id, anomaly_threshold, continuity_threshold,
                     baseline_mean, baseline_std, session_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, anomaly_threshold, continuity_threshold,
                 baseline_mean, baseline_std, session_count, updated_at),
            )

    def get_player_calibration_profile(self, device_id: str) -> dict | None:
        """Return the per-player calibration profile for a device, or None (Phase 38)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM player_calibration_profiles WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_player_calibration_profiles(self) -> list[dict]:
        """Return all per-player calibration profiles (Phase 38)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM player_calibration_profiles ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Phase 42: L6 human-response baseline capture ---

    def store_l6_capture(
        self,
        session_id: str,
        profile_id: int,
        profile_name: str,
        challenge_sent_ts: float,
        onset_ms: float,
        settle_ms: float,
        peak_delta: float,
        grip_variance: float,
        r2_pre_mean: float,
        accel_variance: float,
        player_id: str = "",
        game_title: str = "",
        hw_session_ref: str = "",
        notes: str = "",
    ) -> None:
        """Insert one L6 challenge-response record into l6_capture_sessions (Phase 42)."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO l6_capture_sessions
                    (session_id, profile_id, profile_name, challenge_sent_ts,
                     onset_ms, settle_ms, peak_delta, grip_variance,
                     r2_pre_mean, accel_variance,
                     player_id, game_title, hw_session_ref, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, profile_id, profile_name, challenge_sent_ts,
                    onset_ms, settle_ms, peak_delta, grip_variance,
                    r2_pre_mean, accel_variance,
                    player_id, game_title, hw_session_ref, notes, time.time(),
                ),
            )

    def query_l6_captures(
        self,
        player_id: str = "",
        profile_id: int | None = None,
        limit: int = 0,
    ) -> list[dict]:
        """Return l6_capture_sessions rows, optionally filtered (Phase 42).

        Args:
            player_id:  Filter to this player ('' = all players).
            profile_id: Filter to this profile_id (None = all profiles).
            limit:      Max rows to return (0 = no limit).
        """
        clauses, params = [], []
        if player_id:
            clauses.append("player_id = ?")
            params.append(player_id)
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit_clause = f"LIMIT {int(limit)}" if limit > 0 else ""
        sql = f"SELECT * FROM l6_capture_sessions {where} ORDER BY created_at ASC {limit_clause}"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_l6_captures_by_profile(self, player_id: str = "") -> dict[int, int]:
        """Return {profile_id: count} for captured L6 sessions (Phase 42)."""
        params = []
        where = ""
        if player_id:
            where = "WHERE player_id = ?"
            params.append(player_id)
        sql = f"SELECT profile_id, COUNT(*) as n FROM l6_capture_sessions {where} GROUP BY profile_id"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {r["profile_id"]: r["n"] for r in rows}

    # --- Phase 50: Agent coordination methods ---

    def write_agent_event(
        self,
        event_type: str,
        payload: str,
        source: str,
        device_id: str = None,
        target: str = None,
    ) -> int:
        """Insert an agent coordination event (Phase 50). Returns the new event id."""
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO agent_events "
                "(event_type, device_id, payload_json, source_agent, target_agent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_type, device_id, payload, source, target, now),
            )
            return cur.lastrowid

    def read_unconsumed_events(self, target_agent: str, limit: int = 50) -> list:
        """Return unconsumed agent events for target_agent (Phase 50)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_events "
                "WHERE target_agent = ? AND consumed_at IS NULL "
                "ORDER BY created_at ASC LIMIT ?",
                (target_agent, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_event_consumed(self, event_id: int, consumed_by: str) -> None:
        """Mark an agent event as consumed (Phase 50)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE agent_events SET consumed_at = ?, consumed_by = ? WHERE id = ?",
                (time.time(), consumed_by, event_id),
            )

    def write_threshold_history(
        self,
        threshold_type: str,
        old_value: float,
        new_value: float,
        drift_pct: float,
        sessions_used: int,
        phase: str,
        device_id: str = None,
    ) -> None:
        """Record a threshold change in history (Phase 50)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO threshold_history "
                "(threshold_type, device_id, old_value, new_value, drift_pct, "
                "sessions_used, phase, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (threshold_type, device_id, old_value, new_value, drift_pct,
                 sessions_used, phase, time.time()),
            )

    def get_threshold_history(self, limit: int = 20) -> list:
        """Return recent threshold history entries desc by created_at (Phase 50)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM threshold_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_global_recalibration_time(self) -> float:
        """Return epoch of last global agent-triggered recalibration, or 0.0 (Phase 50)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) as ts FROM threshold_history "
                "WHERE threshold_type LIKE 'global%' "
                "AND phase IN ('manual', 'agent_triggered')",
            ).fetchone()
        ts = row["ts"] if (row is not None and row["ts"] is not None) else None
        return float(ts) if ts is not None else 0.0

    def count_records_since_last_calibration(self, device_id: str) -> int:
        """Count records for device_id since last threshold_history entry (Phase 50)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(created_at) as ts FROM threshold_history WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            last_ts = float(row["ts"]) if (row is not None and row["ts"] is not None) else 0.0
            result = conn.execute(
                "SELECT COUNT(*) as n FROM records WHERE device_id = ? AND created_at > ?",
                (device_id, last_ts),
            ).fetchone()
        return int(result["n"]) if result is not None else 0

    def store_calib_agent_session(self, session_id: str, history: list) -> None:
        """Persist CalibrationIntelligenceAgent conversation history (Phase 50)."""
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO calibration_agent_sessions (session_id, history_json, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "history_json = excluded.history_json, updated_at = excluded.updated_at",
                (session_id, json.dumps(history, default=str), now),
            )

    def load_calib_agent_session(self, session_id: str) -> list:
        """Load CalibrationIntelligenceAgent conversation history (Phase 50)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT history_json FROM calibration_agent_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return []
        try:
            return json.loads(row["history_json"])
        except Exception:
            return []

    # --- Phase 58: Operator Audit Log ---

    def log_operator_action(
        self, endpoint: str, device_id: str, api_key_hash: str,
        source_ip: str, status_code: int, outcome: str,
    ) -> None:
        """Append immutable operator audit log entry (Phase 58)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO operator_audit_log "
                "(endpoint, device_id, api_key_hash, source_ip, status_code, outcome, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (endpoint, device_id, api_key_hash, source_ip, status_code, outcome, time.time()),
            )

    def get_operator_audit_log(
        self, limit: int = 100, device_id: str = ""
    ) -> list[dict]:
        """Return recent operator audit entries, optionally filtered by device (Phase 58)."""
        with self._conn() as conn:
            if device_id:
                rows = conn.execute(
                    "SELECT * FROM operator_audit_log WHERE device_id = ? "
                    "ORDER BY ts DESC LIMIT ?", (device_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM operator_audit_log ORDER BY ts DESC LIMIT ?", (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # --- Phase 59: My Controller Digital Twin ---

    def get_controller_twin_snapshot(self, device_id: str) -> dict:
        """Aggregate all data for the My Controller 3D page (Phase 59)."""
        device   = self.get_device(device_id) or {}
        profile  = self.get_player_calibration_profile(device_id) or {}
        ioid     = self.get_ioid_device(device_id) or {}
        passport = self.get_tournament_passport(device_id) or {}
        audit_log = self.get_operator_audit_log(limit=10, device_id=device_id[:16])
        # Query biometric_fingerprint_store directly (Phase 59)
        with self._conn() as conn:
            _fp_row = conn.execute(
                "SELECT * FROM biometric_fingerprint_store WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            biofp = dict(_fp_row) if _fp_row else {}
            recent = conn.execute(
                "SELECT record_hash, inference, pitl_l4_distance, pitl_humanity_prob, "
                "pitl_l4_features, created_at FROM records "
                "WHERE device_id = ? ORDER BY created_at DESC LIMIT 20",
                (device_id,),
            ).fetchall()
            insight_rows = conn.execute(
                "SELECT content, severity, insight_type, created_at "
                "FROM protocol_insights WHERE device_id = ? "
                "ORDER BY created_at DESC LIMIT 5",
                (device_id,),
            ).fetchall()
        dists = [r["pitl_l4_distance"] for r in recent if r["pitl_l4_distance"] is not None]
        trend = "UNKNOWN"
        if len(dists) >= 4:
            mid = len(dists) // 2
            first_h  = sum(dists[mid:]) / max(len(dists) - mid, 1)
            second_h = sum(dists[:mid]) / mid
            trend = ("DEGRADING" if first_h > second_h * 1.1
                     else "IMPROVING" if first_h < second_h * 0.9 else "STABLE")
        return {
            "device":    dict(device) if device else {},
            "calibration": {
                "anomaly_threshold":    profile.get("anomaly_threshold"),
                "continuity_threshold": profile.get("continuity_threshold"),
                "baseline_mean":        profile.get("baseline_mean"),
                "baseline_std":         profile.get("baseline_std"),
                "session_count":        profile.get("session_count", 0),
            },
            "biometric_fingerprint": {
                "mean_json":  biofp.get("mean_json"),
                "var_json":   biofp.get("var_json"),
                "n_sessions": biofp.get("n_sessions", 0),
            },
            "ioid":     {"registered": bool(ioid), "did": ioid.get("did"), "tx_hash": ioid.get("tx_hash")},
            "passport": {
                "issued": bool(passport),
                "passport_hash": passport.get("passport_hash"),
                "min_humanity_int": passport.get("min_humanity_int"),
                "on_chain": bool(passport.get("on_chain")),
                "issued_at": passport.get("issued_at"),
            },
            "audit_log": audit_log,
            "anomaly_trend": trend,
            "recent_records": [dict(r) for r in recent],
            "insights": [dict(r) for r in insight_rows],
        }

    # --- Phase 61: Frame Replay Checkpoints ---

    def store_frame_checkpoint(
        self, device_id: str, record_hash: str, frames: list
    ) -> None:
        """Store a frame replay checkpoint for a PoAC record (Phase 61)."""
        import json as _json
        frames_json = _json.dumps(frames)
        frame_count = len(frames)
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO frame_checkpoints "
                "(device_id, record_hash, frames_json, frame_count, checkpoint_ts, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, record_hash, frames_json, frame_count, now, now),
            )

    def get_frame_checkpoint(
        self, device_id: str, record_hash: str
    ) -> dict | None:
        """Return frame checkpoint for a specific PoAC record (Phase 61)."""
        import json as _json
        with self._conn() as conn:
            row = conn.execute(
                "SELECT frames_json, frame_count, checkpoint_ts FROM frame_checkpoints "
                "WHERE device_id = ? AND record_hash = ?",
                (device_id, record_hash),
            ).fetchone()
        if not row:
            return None
        return {
            "record_hash":   record_hash,
            "frames":        _json.loads(row["frames_json"]),
            "frame_count":   row["frame_count"],
            "checkpoint_ts": row["checkpoint_ts"],
        }

    def list_checkpoints_for_device(
        self, device_id: str, limit: int = 100
    ) -> list[str]:
        """Return record_hash list for all stored checkpoints (Phase 61)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT record_hash FROM frame_checkpoints "
                "WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                (device_id, min(limit, 500)),
            ).fetchall()
        return [r["record_hash"] for r in rows]

    # --- Phase 55: ioID Device Identity Registry ---

    def store_ioid_device(
        self,
        device_id: str,
        device_address: str,
        did: str,
        tx_hash: str = "",
    ) -> None:
        """Persist an ioID device registration record (Phase 55)."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO ioid_devices
                    (device_id, device_address, did, tx_hash, registered_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, device_address, did, tx_hash, time.time()),
            )

    def get_ioid_device(self, device_id: str) -> dict | None:
        """Return the ioID registration record for device_id, or None (Phase 55)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM ioid_devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_all_ioid_devices(self) -> list[dict]:
        """Return all registered ioID devices ordered by registration time (Phase 55)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM ioid_devices ORDER BY registered_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Phase 56: Tournament Passport ---

    def store_tournament_passport(
        self,
        device_id: str,
        passport_hash: str,
        ioid_token_id: int,
        min_humanity_int: int,
        tx_hash: str = "",
        on_chain: bool = False,
    ) -> None:
        """Persist a tournament passport record (Phase 56)."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tournament_passports
                    (device_id, passport_hash, ioid_token_id, min_humanity_int,
                     tx_hash, on_chain, issued_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id, passport_hash, ioid_token_id, min_humanity_int,
                    tx_hash, 1 if on_chain else 0, time.time(),
                ),
            )

    def get_tournament_passport(self, device_id: str) -> dict | None:
        """Return tournament passport for device_id, or None (Phase 56)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tournament_passports WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_passport_eligible_sessions(
        self,
        device_id: str,
        min_humanity: float,
        limit: int = 10,
    ) -> list[dict]:
        """Return NOMINAL sessions with humanity_prob >= min_humanity (Phase 56).

        Used to determine eligibility for tournament passport issuance.
        Returns up to `limit` sessions ordered newest-first.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT record_hash, pitl_humanity_prob, pitl_proof_nullifier,
                       inference, created_at
                FROM records
                WHERE device_id = ?
                  AND inference = 32
                  AND pitl_humanity_prob >= ?
                  AND pitl_proof_nullifier IS NOT NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (device_id, min_humanity, limit),
            ).fetchall()
        return [dict(r) for r in rows]
