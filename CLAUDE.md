# VAPI — Claude Code Project Context

## What This Project Is

VAPI (Verified Autonomous Physical Intelligence) is a cryptographic anti-cheat protocol
for competitive gaming. It produces a 228-byte Proof of Autonomous Cognition (PoAC) record
per cognition cycle, anchored on IoTeX L1. The certified device is a DualShock Edge
(Sony CFI-ZCP1). The primary game corpus is NCAA College Football 26.

## Repository

`C:\Users\Contr\vapi-pebble-prototype`

~220 files, ~1,456 automated tests total (~1,428 CI excluding 28 hardware, 14 E2E).
Bridge: 1032 passing. Contract: 354. SDK: 28. Hardware: 28. E2E: 14.
13 contracts deployed on IoTeX testnet.
Deployer address: `0x0Cf36dB57fc4680bcdfC65D1Aff96993C57a4692`
Chain ID: 4690 (IoTeX Testnet)
Current phase: Phase 61

## Architecture at a Glance

| Layer | Language | Key files |
|-------|----------|-----------|
| Controller anti-cheat | Python | `controller/tinyml_biometric_fusion.py`, `controller/dualshock_integration.py`, `controller/l6_trigger_driver.py`, `controller/l6_response_analyzer.py`, `controller/temporal_rhythm_oracle.py`, `controller/hid_xinput_oracle.py`, `controller/l2b_imu_press_correlation.py`, `controller/l2c_stick_imu_correlation.py` |
| Bridge service | Python asyncio | `bridge/vapi_bridge/` — `insight_synthesizer.py`, `bridge_agent.py`, `behavioral_archaeologist.py`, `network_correlation_detector.py`, `federation_bus.py`, `alert_router.py` |
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
| L4 | 0x30 | Advisory | 11-feature Mahalanobis biometric fingerprint |
| L5 | 0x2B | Advisory | Temporal rhythm (CV, entropy, quantization) |
| L6 | — | Advisory | Active haptic challenge-response (disabled by default) |

Hard codes {0x28, 0x29, 0x2A} block tournament eligibility.
L2C returns None in dead-zone stick games (NCAA CFB 26) — 0.10 weight resolves to 0.5 neutral prior.

## L4 Calibration State (Phase 49, N=74)

- Calibration corpus: hw_005–hw_078 (N=74 including newer tremor/touchpad sessions)
- Feature space: 11 features, 9 active (Phase 46 added accel_magnitude_spectral_entropy)
- Active features (9): trigger_resistance_change_rate(excl), trigger_onset_velocity_L2,
  trigger_onset_velocity_R2, micro_tremor_accel_variance, grip_asymmetry,
  stick_autocorr_lag1, stick_autocorr_lag5, tremor_peak_hz, tremor_band_power,
  accel_magnitude_spectral_entropy, touch_position_variance(excl pending recapture)
- Structurally zero / excluded: trigger_resistance_change_rate, touch_position_variance
  (touchpad_active_fraction replaced by accel_magnitude_spectral_entropy in Phase 46)
- L4 anomaly threshold: 6.726 (mean+3σ, Phase 46, N=74, was 7.019)
- L4 continuity threshold: 5.097 (mean+2σ, Phase 46, N=74, was 5.369)
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
| 61 | Session replay system — `frame_checkpoints` SQLite table (FK→records, maxlen=60 ring, 20 Hz); `/replay` + `/checkpoints` + `/features` REST endpoints; BridgeAgent tool #29 `get_session_replay`; `useReplayMode` + `useFeatureHistory` dashboard hooks; BiometricScatter cyan DB history dots; Chain tile ▶ REPLAYABLE indicator + replay status bar |

## Completed Items — Do Not Re-Open

- Session replay system (Phase 61) — `frame_checkpoints` table (SQLite, FK to records, maxlen=60 ring buffer at 20 Hz capture rate); `/replay`, `/checkpoints`, `/features` REST endpoints; BridgeAgent tool #29 `get_session_replay`; `useReplayMode` + `useFeatureHistory` React hooks; BiometricScatter cyan DB history overlay dots; Chain tile ▶ REPLAYABLE badge + replay status bar in dashboard
- Test coverage expansion (Phase 61 prep) — 4 new test files, 144 tests: `test_hid_report_parser.py` (37 tests, CRITICAL gap), `test_backend_cheat_classifier.py` (40 tests, Layer 3 behavioral classifier), `test_l0_bluetooth_presence.py` (30 tests, L0 BT advisory scoring), `test_threshold_calibrator.py` (37 tests, statistical functions + calibration pipeline)
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

## Remaining Open Gaps (Phase 61, priority order)

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

## BridgeAgent

claude-sonnet-4-6. 29 deterministic read-only tool bindings (tool #29: `get_session_replay`).
GET /operator/agent/stream (SSE, 60 req/min). SQLite session persistence.

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
- Whitepaper test counts: 1032 bridge, ~1,456 total, ~1,428 CI
- frame_checkpoints ring buffer: maxlen=60 (3 seconds at 20 Hz) — do not increase without memory profiling
- /replay endpoint returns checkpoint rows in capture order; BridgeAgent tool #29 is read-only
- L2C phantom weight must be acknowledged in any humanity formula discussion
- accel_magnitude_spectral_entropy is bot-vs-human only — never claim it improves separation ratio

## Build & Test Commands

```bash
python -m pytest bridge/tests/ --ignore=bridge/tests/test_e2e_simulation.py -q  # 1032 passed
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
