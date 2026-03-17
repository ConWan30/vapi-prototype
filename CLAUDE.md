# VAPI — Claude Code Project Context

## What This Project Is

VAPI (Verified Autonomous Physical Intelligence) is a cryptographic anti-cheat protocol
for competitive gaming. It produces a 228-byte Proof of Autonomous Cognition (PoAC) record
per cognition cycle, anchored on IoTeX L1. The certified device is a DualShock Edge
(Sony CFI-ZCP1). The primary game corpus is NCAA College Football 26.

## Repository

`C:\Users\Contr\vapi-pebble-prototype`

~220 files, ~1,480 automated tests total (~1,452 CI excluding 28 hardware, 14 E2E).
Bridge: 1056 passing. Contract: 354. SDK: 28. Hardware: 28. E2E: 14.
20 contracts deployed on IoTeX testnet (all LIVE, 2026-03-16). See `contracts/deployed-addresses.json`.
Active wallet (bridge + deployer): `0x0Cf36dB57fc4680bcdfC65D1Aff96993C57a4692` (~6.1 IOTX remaining)
Previous bridge wallet (no longer accessible): `0xfCF4681e57C8de9650c3Eb4dA8e26dC9441A5EF1` (deployed original 14 contracts — addresses unchanged, still valid on-chain)
Chain ID: 4690 (IoTeX Testnet)
Current phase: Phase 63

## Architecture at a Glance

| Layer | Language | Key files |
|-------|----------|-----------|
| Controller anti-cheat | Python | `controller/tinyml_biometric_fusion.py`, `controller/dualshock_integration.py`, `controller/l6_trigger_driver.py`, `controller/l6_response_analyzer.py`, `controller/temporal_rhythm_oracle.py`, `controller/hid_xinput_oracle.py`, `controller/l2b_imu_press_correlation.py`, `controller/l2c_stick_imu_correlation.py` |
| Bridge service | Python asyncio | `bridge/vapi_bridge/` — `insight_synthesizer.py`, `bridge_agent.py`, `calibration_intelligence_agent.py`, `behavioral_archaeologist.py`, `network_correlation_detector.py`, `federation_bus.py`, `alert_router.py` |
| Smart contracts | Solidity | `contracts/` — `PoACVerifier.sol`, `PHGRegistry.sol`, `PHGCredential.sol`, `TournamentGateV3.sol`, `PITLSessionRegistry.sol`, `SkillOracle.sol`, `FederatedThreatRegistry.sol` |
| Scripts | Python | `scripts/threshold_calibrator.py`, `scripts/run_adversarial_validation.py` (9-feature proxy Phase 49), `scripts/interperson_separation_analyzer.py`, `scripts/l6_threshold_calibrator.py`, `scripts/phase_coherence_calibration.py` (negative result, keep), `scripts/generate_professional_adversarial.py` (Phase 48) |
| Calibration data | JSON | `sessions/human/hw_005` through `sessions/hw_078` (N=74, 3 players) |
| Frontend dashboard | React JSX | `frontend/VAPIDashboard.jsx` — 850+ lines, void-black + electric orange + cyan |
| Whitepaper | Markdown | `docs/vapi-whitepaper-v3.md` |

## PoAC Wire Format — FROZEN, DO NOT MODIFY

228 bytes total: 164-byte signed body + 64-byte ECDSA-P256 signature.
Chain link hash = SHA-256(raw[0:164]) — 164-byte body only, NOT full 228 bytes.

## PITL Nine-Level Stack

| Layer | Code | Type | Signal |
|-------|------|------|--------|
| L0 | — | Structural | HID presence |
| L1 | — | Structural | PoAC chain integrity |
| L2 | 0x28 | Hard cheat | IMU gravity + HID/XInput discrepancy |
| L3 | 0x29/0x2A | Hard cheat | TinyML behavioral classifier |
| L2B | 0x31 | Advisory | IMU-button causal latency |
| L2C | 0x32 | Advisory | Stick-IMU cross-correlation (inactive in dead-zone stick games) |
| L4 | 0x30 | Advisory | 12-feature Mahalanobis biometric fingerprint |
| L5 | 0x2B | Advisory | Temporal rhythm (CV, entropy, quantization) |
| L6 | — | Advisory | Active haptic challenge-response (disabled by default) |

Hard codes {0x28, 0x29, 0x2A} block tournament eligibility.
L2C returns None in dead-zone stick games (NCAA CFB 26) — 0.10 weight resolves to 0.5 neutral prior.

## L4 Calibration State (Phase 57, N=74)

- Calibration corpus: hw_005–hw_078 (N=74 including newer tremor/touchpad sessions)
- Feature space: 12 features, 10 active (Phase 46 added accel_magnitude_spectral_entropy; Phase 57 added press_timing_jitter_variance)
- Active features (10): trigger_resistance_change_rate(excl), trigger_onset_velocity_L2,
  trigger_onset_velocity_R2, micro_tremor_accel_variance, grip_asymmetry,
  stick_autocorr_lag1, stick_autocorr_lag5, tremor_peak_hz, tremor_band_power,
  accel_magnitude_spectral_entropy, touch_position_variance(excl pending recapture),
  press_timing_jitter_variance (index 11 — normalised IBI variance; human 0.001–0.05; bot macro <0.00005)
- Structurally zero / excluded: trigger_resistance_change_rate, touch_position_variance
  (touchpad_active_fraction replaced by accel_magnitude_spectral_entropy in Phase 46)
- L4 anomaly threshold: **7.009** (mean+3σ, Phase 57, N=74, 12-feature space — was 6.726 Phase 46)
- L4 continuity threshold: **5.367** (mean+2σ, Phase 57, N=74, 12-feature space — was 5.097 Phase 46)
- Threshold rise (+4.2%/+5.3%): expected — press_timing_jitter_variance adds real variance, expands Mahalanobis distribution
- Inter-person separation ratio: 0.362 — L4 is intra-player anomaly detector only
- Human false positive rate: ~2.9% (expected at 3σ)

## accel_magnitude_spectral_entropy (Phase 46, index 9)

Replaces structurally-zero touchpad_active_fraction.
Physics: Shannon entropy of the 0–500 Hz power spectrum of DC-removed ||accel||.
Requires 1000 Hz polling — cannot be computed on standard HID (125–250 Hz) devices.
Ring buffer: 1024 frames, follows Phase 41 pattern (returns 0.0 until filled).
Human range: 3–8.6 bits, tightly centered at 4.8–4.9 bits (std 1.303).
Static injection: 0.0 (variance guard). Random noise: ~9.0 bits (detectable).
Player means nearly identical (P1: 4.878, P2: 4.882, P3: 4.767) — bot-vs-human
discriminator only, NOT inter-player identifier. Does not improve separation ratio.
Negative result documented: docs/phase-coherence-calibration.md (accel_phase_coherence
ruled out — gravity dominates accel during still frames in handheld gaming grip).

## Humanity Probability Formula (Phase 46)

Without L6 (default):
  humanity_probability = 0.28·p_L4 + 0.27·p_L5 + 0.20·p_E4 + 0.15·p_L2B + 0.10·p_L2C
  NOTE: p_L2C resolves to 0.5 neutral prior in dead-zone stick games (NCAA CFB 26).
  Formula runs as effective 4-signal in practice for this game corpus.

With L6 active:
  p_human = 0.23·p_L4 + 0.22·p_L5 + 0.15·p_E4 + 0.15·p_L6 + 0.15·p_L2B + 0.10·p_L2C

## Phase Summary

| Phase | Key milestone |
|-------|---------------|
| 17 | L4 feature space 7→11; L2B/L2C oracles added |
| 38 | Mode 6 living calibration active (α=0.95, ±15%/cycle, 6h) |
| 41 | Full covariance L4; ZK inference code binding |
| 43 | L6 human response baseline; bridge 865→877 |
| 45 | accel_phase_coherence NEGATIVE RESULT — gravity dominates; reverted; documented |
| 46 | accel_magnitude_spectral_entropy shipped; thresholds 7.019→6.726 / 5.369→5.097; bridge 880 |
| 47 | L2C phantom weight closed — PITL layer table live INACTIVE indicator; stale threshold labels fixed |
| 48 | Professional adversarial data — 3 white-box attack classes (G/H/I), 15 sessions, 4 bridge tests; bridge 884 |
| 49 | Tremor FFT window 513→1025 positions (512→1024 velocity samples); 1.95→0.977 Hz/bin; 4 bins across 8–12 Hz band; batch validator 7→9 features; bridge 888 |
| 50 | CalibrationIntelligenceAgent peer (6 tools, 30-min event consumer, min() enforcement); BridgeAgent +3 tools +2 behaviors; InsightSynthesizer Mode 6 callback; agent_events/threshold_history/calibration_agent_sessions tables; /calibration/agent + /calibration/stream endpoints; bridge 902 |
| 51 | Game-Aware Profiling — NCAA CFB 26 profile (R2-first L5 priority, 11-entry button map); L6-Passive passive R2 onset tracking (no PS5 conflict); GameProfile registry; get_game_profile BridgeAgent tool #21; CORS/port/batcher/MQTT dev fixes; calibration_agent timeout 180→600s; bridge 915 |
| 52 | Runtime hardening — `_run_ds_with_restart()` (3 auto-restarts); `pitl_meta=None` NameError fix; WS broadcast `except pass` → `log.error`; CORS `:5174` fallback; `hardware_block.controller_connected` init False; CalibIntelAgent failure counter; batcher gas dead-letter + `retry_task.add_done_callback`; ProactiveMonitor decoupled from agent instance; bridge 915 (pure hardening, no new tests) |
| 53 | Serialization hardening — `_safe_val()` NaN/Inf→None wrapper on all WS float fields; `_pending_pitl_meta` reset per loop; `controller_registered` WS event on device connect (frontend triggers fetchSnapshot); chain gas/revert permanent vs transient log discrimination; schema_versions Phase 51; `retry_task.add_done_callback`; 21 new tests; bridge 936 |
| 54 | Runtime hardening — numpy fallback ImportError fix (NCD `build_distance_matrix`); `_task_done_handler` CRITICAL log on all 11 managed tasks; `send_raw_transaction` nonce reset on send failure; WS receive 60s timeout (`ws_records`/`ws_frames`); store migration `log.debug`; fetchSnapshot abort dedup; WS reconnect exponential backoff 5→60s; bridge 941 |
| 55 | ioID Device Identity — `VAPIioIDRegistry.sol`; `ioid_devices` table; DID `did:io:0x<addr>` in PITL metadata + WS; `ensure_ioid_registered()` + `ioid_increment_session()` chain calls; `get_ioid_status` BridgeAgent tool #22; 5 tests; bridge 946 |
| 56 | ZK Tournament Passport — `TournamentPassport.circom` (5 public signals); `PITLTournamentPassport.sol` (mock mode, SESSION_COUNT=5); `tournament_passports` table; `generate_tournament_passport` tool #23; `POST /operator/passport`; 5 tests; bridge 951 |
| 57 | Jitter Variance Feature — `press_timing_jitter_variance` index 11; `_BIO_FEATURE_DIM` 11→12; IBI deques (Cross/L2/R2/Triangle, maxlen=50); `_press_timing_jitter_variance()` static method; behavioral_archaeologist FEATURE_KEYS updated; threshold_calibrator `_extract_jitter_variance()`; 5 tests; bridge 956 |
| 58 | Security Hardening + BridgeAgent Expansion — operator endpoint auth (x-api-key → 401/503); sliding-window rate limiter; operator_audit_log table + log_operator_action/get_operator_audit_log; inference_code column in pitl_session_proofs; BridgeAgent tools #24–27 (analyze_threshold_impact, predict_evasion_cost, get_anomaly_trend, generate_incident_report); 16 tests; bridge 972 |
| 59 | My Controller 3D Digital Twin — physics-driven DualShock Edge CFI-ZCP1 twin page; get_ibi_snapshot() on BiometricFeatureExtractor; ibi_snapshot in pitl_meta + /ws/records; /ws/twin/{device_id} device-scoped fusion WS; GET /controller/twin/{id} + /chain REST; BridgeAgent tool #28 get_controller_twin_data; React Three Fiber + Rapier + Drei frontend (controller-twin.html); IBI Biometric Heartbeat canvas; PoAC DNA Helix; ProofAnchorPanel (ioID DID + ZK passport + separation ratio disclaimer); chain timeline scrubber; 16 tests; bridge 988 |
| 60 | My Controller Enhanced Visualization (Phase 60A) — 4-tab left panel: HEARTBEAT / RADAR / L5 RHYTHM / BIOM MAP; BiometricRadar (12-spoke canvas, mean_json, BIO_NORM[12]); L5RhythmOverlay (per-button CV bars + entropy gauge + quant flag, pitl_l5_cv/pitl_l5_entropy); BiometricScatter (2D tremor×jitter cross-section, bot zone, human 2σ ellipse, N=74); ProofShareQR (QRCode npm, IoTeX explorer deeplink, copy URL); qrcode dep; zero backend changes; bridge 988 unchanged |
| 61 | Session Replay + Feature History Scatter — frame_checkpoints table (deque maxlen=60, 20 Hz); store_frame_checkpoint/get_frame_checkpoint/list_checkpoints_for_device; _replay_ring deque + checkpoint storage in _dispatch; /replay + /checkpoints + /features endpoints; BridgeAgent tool #29 get_session_replay; useReplayMode + useFeatureHistory hooks; BiometricScatter history dots (cyan, DB feature vectors); chain tile ▶ indicator + replay status bar; Track C deployments blocked (wallet 0.43 IOTX); +12 tests; bridge 1000 |
| 62 | Player Enrollment + ZK Inference Code Binding — EnrollmentManager (auto PHGCredential mint after enrollment_min_sessions=10 NOMINAL sessions); device_enrollments table + 4 store methods; config enrollment_min/humanity_min; GET /enrollment/status/{device_id}; BridgeAgent tool #30 get_enrollment_status; PitlSessionProof.circom C3 constraint (inferenceResult === inferenceCodeFromBody); C1 Poseidon 7→8 inputs (adds inferenceCodeFromBody); mock proof commitment includes inference_result; PITLSessionRegistryV2.sol + deploy script; Phase 62 ceremony re-run; artifacts updated; +26 tests; bridge 1026 |
| 63 | L6b Neuromuscular Reflex Layer — first reactive involuntary probe; L6B_PROBE profile (id=8, amplitude 60/255, sub-perceptual); L6bReflexAnalyzer (accel-mag delta, BOT<15ms/HUMAN 80-280ms); l6b_probe_log SQLite table + insert_l6b_probe/get_l6b_baseline; 5 new config fields (l6b_enabled/probe_interval/accel_threshold/human_min_ms/human_max_ms); 4-way humanity formula (baseline/L6/L6b/both); pitl_meta l6b_* fields; BridgeAgent tool #31 get_reflex_baseline; profile 8 excluded from L6 active rotation; L6B_ENABLED=false default; +26 tests; bridge 1056 |

## Completed Items — Do Not Re-Open

- Phase 63 L6b Neuromuscular Reflex — L6B_PROBE profile (id=8, sub-perceptual); L6bReflexAnalyzer; l6b_probe_log table; 5 config fields; 4-way humanity formula; pitl_meta l6b_* fields; BridgeAgent tool #31 get_reflex_baseline; 26 tests; bridge 1056
- Phase 62 Player Enrollment + ZK C3 — EnrollmentManager; device_enrollments table; GET /enrollment/status; BridgeAgent tool #30; PitlSessionProof.circom C3 + Poseidon(8) C1; mock proof inference binding; PITLSessionRegistryV2.sol; ceremony re-run; nPublic=5 preserved; +26 tests; bridge 1026
- Phase 61 Session Replay + Feature History Scatter — frame_checkpoints (SQLite, FK to records, maxlen=60 ring, INSERT OR IGNORE idempotent); _replay_ring deque 20 Hz; /replay + /checkpoints + /features; BridgeAgent tool #29; useReplayMode + useFeatureHistory; BiometricScatter cyan DB dots; chain tile ▶ indicator; replay status bar; 12 tests; bridge 1000
- Phase 60 My Controller Enhanced Visualization (60A) — BiometricRadar 12-spoke; L5RhythmOverlay CV+entropy+quant; BiometricScatter tremor×jitter 2D cross-section; ProofShareQR modal with IoTeX explorer deeplink; 4-tab left panel; qrcode npm dep; zero backend; bridge 988 unchanged
- Phase 59 My Controller 3D Digital Twin — physics-driven controller twin; get_ibi_snapshot(); /ws/twin/{device_id} fusion WS; /controller/twin/{id} REST; BridgeAgent tool #28; ControllerTwin.jsx (R3F + Rapier + Drei); IBI Biometric Heartbeat; PoAC DNA Helix; chain timeline scrubber; 16 tests; bridge 988
- Phase 58 Security Hardening — operator endpoint auth; sliding-window rate limiter; operator_audit_log; inference_code; BridgeAgent tools #24–27; 16 tests; bridge 972
- Phase 57 jitter variance — `press_timing_jitter_variance` (index 11) added to BiometricFeatureFrame; `_BIO_FEATURE_DIM` 11→12; IBI deque tracking (Cross/L2/R2/Triangle); static `_press_timing_jitter_variance()`; 5 new tests; bridge 956
- Phase 56 ZK Tournament Passport — TournamentPassport.circom + PITLTournamentPassport.sol + deploy script; tournament_passports table + 3 store methods; generate_tournament_passport BridgeAgent tool #23; POST /operator/passport; 5 new tests; bridge 951
- Phase 55 ioID Device Identity — VAPIioIDRegistry.sol + deploy script; ioid_devices table; DID in pitl_meta + WS; chain methods ensure_ioid_registered/ioid_increment_session; get_ioid_status tool #22; 5 new tests; bridge 946
- Phase 54 runtime hardening — numpy fallback ImportError fix; `_task_done_handler` CRITICAL on 11 tasks; chain nonce reset on send failure; WS 60s receive timeout; store migration log.debug; fetchSnapshot abort dedup; WS reconnect 5→60s backoff; 5 new tests; bridge 941
- Phase 53 serialization hardening — `_safe_val()` NaN/Inf→None on all WS float fields; `controller_registered` WS event; `_pending_pitl_meta` reset per loop; gas/revert error discrimination; 21 new tests; bridge 936
- Phase 52 runtime hardening — `_run_ds_with_restart()`; `pitl_meta=None` fix; WS broadcast logging; CORS `:5174`; `hardware_block` init False; CalibIntelAgent failure counter; batcher gas dead-letter extended; bridge 915
- Tremor FFT window widening (Phase 49) — 513→1025 ring buffer, 0.977 Hz/bin, 4 Phase 49 bridge tests, batch validator 7→9 features, Attack G batch still 0% (right_stick_x preserved), whitepaper §8.5 + feature table updated, bridge 888
- Professional bot adversarial data (Phase 48) — 3 white-box attack classes G/H/I, 15 sessions, 4 unit tests, validation script updated, analysis doc, whitepaper §9.5 added
- L2C phantom weight formula integrity fix (Phase 47) — PITL layer live status, log.debug, WS flag, §7.5.4, test_9, HUMANITY tile
- accel_magnitude_spectral_entropy as active feature at index 9 (Phase 46)
- L4 thresholds recalibrated N=74 (Phase 46)
- L6 human response baseline calibration (Phase 43)
- Full covariance L4 (Phase 41)
- ZK inference code binding / pub[2]=0 gap (Phase 41)
- IoTeX testnet deployment (13 contracts live)
- PoAC chain hash bug fix
- PHGCredential auto-expiry fix

## Remaining Open Gaps (Phase 48, priority order)

1. **L2C phantom weight** — CLOSED (Phase 47)
   `l2c_inactive` flag in pitl_meta + WS stream; log.debug per dead-zone cycle; §7.5.4 footnote;
   test_9 formula validity; HUMANITY tile "4-signal (L2C: dead zone)" in orange; PITL layer table
   L2C row shows "INACTIVE (dead zone)" live when l2c_inactive=true; L4 thresholds updated; bridge 880.

2. **Inter-person separation ratio 0.362** — OPEN/HIGH
   Neither phase coherence nor spectral entropy improved it (both are bot-vs-human).
   True fix requires: post-Phase-17 touchpad recapture (hardware + gameplay) AND
   widening tremor FFT window beyond 120 frames.

3. **Post-Phase-17 touchpad recapture** — OPEN/HIGH (requires controller + gameplay)
   touch_position_variance structurally zero across all calibration sessions.

4. **Professional bot adversarial data** — CLOSED (Phase 48)
   3 white-box attack classes (G: randomized_bot, H: threshold_aware, I: spectral_mimicry), 15 sessions.
   H: 100% L4 detection. G/I: batch 0% (live L4+tremor and L2B respectively). Analysis: `docs/professional-adversarial-analysis.md`.
   Remaining gap: real hardware bot software (aimbot, ML-driven inputs) still untested.

5. **Multi-party ZK ceremony** — PLANNED (no hardware)

6. **PHGCredential multi-sig/timelock governance** — PLANNED (no hardware)

## ZK Circuit

Groth16, BN254, ~1,820 constraints, 2^11 powers-of-tau.
PITLSessionRegistry: `0x8da0A497234C57914a46279A8F938C07D3Eb5f12`
PitlSessionProofVerifier: `0x07D3ca1548678410edC505406f022399920d4072`

## BridgeAgent + CalibrationIntelligenceAgent (Phase 50) + Game Profile (Phase 51)

BridgeAgent: claude-sonnet-4-6. 28 deterministic tool bindings (17 original + 3 Phase 50 + 1 Phase 51 + 4 Phase 58 + 1 Phase 59).
GET /operator/agent/stream (SSE, 60 req/min). SQLite session persistence.
Phase 50: check_threshold_drift() wired to InsightSynthesizer Mode 6 callback.
Phase 50: react() emits recalibration_needed agent_events when drift_velocity > 0.6.
Phase 51: get_game_profile() tool — returns active game profile, L5 priority, L6-Passive stats.

CalibrationIntelligenceAgent: claude-sonnet-4-6. 6 calibration specialist tools.
GET /operator/calibration/stream + POST /operator/calibration/agent.
run_event_consumer() polls agent_events table every 30 min.
Enforces min() unconditionally on trigger_recalibration — thresholds can only tighten.

## Game-Aware Profiling (Phase 51)

Active profile: ncaa_cfb_26 (set via GAME_PROFILE_ID=ncaa_cfb_26 in bridge/.env).
L5 button priority overridden: R2 (sprint) > Cross > L2_dig > Triangle — football-specific.
L6-Passive: per-press R2 onset tracking (no controller writes, no PS5 conflict). Bootstrap N=20,
EMA α=0.15, flag_ratio=1.5 (50% slower than personal mean = PS5 haptic resistance event).
game_profile.py: GameProfile frozen dataclass + registry; ncaa_cfb_26 registered at import.
rhythm_hash() canonical order UNCHANGED — sensor commitment invariant preserved.

## Hardware

DualShock Edge CFI-ZCP1, USB-C, Windows 11, hidapi VID=0x054C PID=0x0DF2 interface 3.
USB polling: 1002 Hz. Injection margin: 14,000× (accel), 10,000× (gyro).
Micro-tremor variance: 278,239 LSB².

## Hard Rules

- Never modify the 228-byte PoAC wire format
- Never change chain link hash from SHA-256(164B body)
- Hardware tests gated @pytest.mark.hardware, excluded from CI
- E2E tests require running Hardhat node
- L6_CHALLENGES_ENABLED=false is the correct default
- Per-player L4 thresholds can only tighten, never loosen (enforced by min())
- Stable EMA track updates on NOMINAL sessions only
- Whitepaper test counts: 1056 bridge, ~1,480 total, ~1,452 CI
- Operator endpoints (/operator/passport, /operator/passport/issue) require valid x-api-key header matching cfg.operator_api_key; return 503 if key unconfigured, 401 if wrong key
- L2C phantom weight must be acknowledged in any humanity formula discussion
- accel_magnitude_spectral_entropy is bot-vs-human only — never claim it improves separation ratio

## Build & Test Commands

```bash
python -m pytest bridge/tests/ --ignore=bridge/tests/test_e2e_simulation.py -q  # 1056 passed
python -m pytest sdk/tests/ -v                                                   # 28
cd contracts && npx hardhat test                                                  # 354
pytest tests/hardware/ -v -m hardware -s                                         # 28 (needs controller)
# ZK ceremony (unblocks 5 skips):
cd /c/Users/Contr/vapi-pebble-prototype/contracts && PATH="$(pwd):$PATH" npx hardhat run scripts/run-ceremony.js
# E2E (needs Hardhat node):
HARDHAT_RPC_URL=http://127.0.0.1:8545 python -m pytest bridge/tests/test_e2e_simulation.py -v
# L6 capture workflow:
python scripts/l6_hardware_check.py
python scripts/l6_capture_session.py --player P1 --game "NCAA Football 26" --target 50
python scripts/l6_threshold_calibrator.py --from-db
```

## Key Gotchas (Windows / HID)

- `hidapi` library: `pip install hidapi` (NOT `hid`)
- HID Cross button: bit5 of `buttons_0` raw HID byte; `cross = (buttons_0 >> 5) & 1`
- L2C sign bug: use `abs(max_causal_corr) < threshold` — anti-correlation is physical coupling
- Windows SQLite tests: use `tempfile.mkdtemp()` NOT `TemporaryDirectory` (WAL PermissionError)
- Windows print encoding: ASCII (PASS: / ->) NOT Unicode (✓ / →) in test print() calls
- Web3/eth_account stub: mock `web3`, `web3.exceptions`, `eth_account` before import
- EWCWorldModel INPUT_DIM=30 (tests need 30-dim input, not 10)
- ZK circuits: `pragma circom 2.0.0;` — requires circom2 Rust binary; circom.exe v2.2.3 in `contracts/`
- IoTeX: chain ID 4689 mainnet, 4690 testnet; P256 precompile at 0x0100
- hardhat.config.js: viaIR=true (stack-too-deep fix for PoACVerifier)
- conftest.py: autouse event loop fixture prevents Python 3.13 asyncio teardown crash
- Batch analysis: always use max_frames=0 — default 30k limit misses presses in 180s sessions
