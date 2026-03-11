# VAPI Project — Claude Code Context

## What This Project Is
VAPI = Verified Autonomous Physical Intelligence. A cryptographic anti-cheat protocol for competitive gaming. Core primitive: Proof of Autonomous Cognition (PoAC) — a 228-byte hash-chained evidence record binding sensor commitments, model attestation, world-model state, and inference outputs, signed with hardware-backed ECDSA-P256.

## Primary Device
DualShock Edge (Sony CFI-ZCP1) — production PHCI-certified gaming controller. Adaptive triggers (motorized L2/R2 resistance) + six-axis IMU + USB 1000 Hz polling create an unforgeable biometric detection surface.

## Secondary Device (extensibility validation only)
IoTeX Pebble Tracker (nRF9160 + CryptoCell-310) — DePIN environmental sensor. Same 228-byte PoAC format, different sensor domain.

## Current Phase: Phase 17 Complete — Master Workflow All Steps Done
- **Bridge: 843 passed** | Hardhat: 354/354 | Hardware: 28 | Total ~1,225 tests
- All thresholds empirically calibrated from N=69 real sessions, 3 distinct players
- Whitepaper v3 complete (`docs/vapi-whitepaper-v2.md`) — nine-level PITL, N=69 data
- No known gaps blocking production; next: IoTeX testnet deployment (needs funded wallet)

## Architecture Layers
1. **Firmware** — C/Zephyr RTOS (nRF9160) + C/ESP-IDF (controller)
2. **Smart Contracts** — Solidity on IoTeX L1 (P256 precompile at 0x0100)
3. **Bridge Service** — Python asyncio (MQTT/CoAP/HTTP ingestion → batch → on-chain relay)
4. **SDK** — Python + C99 header (VAPISession, VAPIVerifier, self_verify loop)
5. **PITL** — Nine-level Physical Input Trust Layer (L0–L6, with L2B/L2C) for bot/cheat detection
6. **Dashboard** — FastAPI + Alpine.js player/operator dashboards

## PITL Stack (Nine-Level)
| Layer | Code | Type | File |
|-------|------|------|------|
| L0 | — | Structural | Physical presence / HID connected |
| L1 | — | Structural | PoAC chain integrity |
| L2 | 0x28 | Hard | `hid_xinput_oracle.py` — HID vs XInput + gravity absent |
| L2B | 0x31 | Advisory | `l2b_imu_press_correlation.py` — IMU-button causal latency |
| L2C | 0x32 | Advisory | `l2c_stick_imu_correlation.py` — stick-IMU cross-correlation |
| L3 | 0x29/0x2A | Hard | `tinyml_backend_cheat.py` — behavioral ML |
| L4 | 0x30 | Advisory | `tinyml_biometric_fusion.py` — 11-feature Mahalanobis |
| L5 | 0x2B | Advisory | `temporal_rhythm_oracle.py` — CV/entropy/quantization |
| L6 | — | Advisory | `l6_trigger_driver.py` — active challenge-response (disabled by default) |

## Core Contract Stack
1. PoACVerifier — signature + chain integrity verification
2. TieredDeviceRegistry — device identity + staking + reputation
3. PHGRegistry — on-chain humanity score checkpoints
4. PHGCredential — soulbound ERC-5192; suspend/reinstate/isActive
5. TournamentGateV3 — PHG-gated + credential active check
6. PITLSessionRegistry — ZK PITL session proof registry; anti-replay nullifiers
7. FederatedThreatRegistry — immutable cross-confirmed cluster hash anchor
8. BountyMarket — DePIN bounty lifecycle
9. SkillOracle / ProgressAttestation / TeamProofAggregator — gaming contracts

## Key Technical Decisions
- ECDSA-P256 (not secp256k1) — matches IoTeX P256 precompile + CryptoCell-310 hardware
- SHA-256 for all commitments (hardware-accelerated on CryptoCell-310)
- Fixed 228-byte wire format — zero-copy deserialization, fits NB-IoT uplink
- Groth16 over BN254 for ZK PITL proofs (~1,820 constraints)
- YubiKey PIV / ATECC608A for hardware-rooted signing (key never leaves hardware)

## Calibrated Thresholds (N=69, 3 Players, 2026-03-07) — PRODUCTION
| Threshold | Value | Derivation |
|-----------|-------|------------|
| L4 anomaly (ANOMALY_THRESHOLD) | **7.019** | mean+3σ, 11-feature Mahalanobis |
| L4 continuity (CONTINUITY_THRESHOLD) | **5.369** | mean+2σ, 11-feature Mahalanobis |
| L5 CV | **0.08** | adversarial — human 10th pct=0.789 (9.9× margin) |
| L5 entropy | **1.0 bits** | human 10th pct=1.231 bits |
| L2B coupled_fraction | **0.55** | human mean=0.786 across 64/69 sessions |
| L2C max_causal_corr | **0.15** | fixed; 0/69 FP (abs() check mandatory) |
| Stick noise floor | 19.28 LSB | mean per-axis std across all sessions |
| IMU gyro noise floor | 332.99 LSB | 95th pct per-axis std |

## Critical Bugs Found and Fixed
1. **L2C sign bug**: `anomaly = max_corr < threshold` fired on anti-correlated signals.
   Fix: `anomaly = abs(max_corr) < threshold`. Anti-correlation is physical coupling.
2. **HID Cross button mapping**: bit5 of `buttons_0` raw HID byte = Cross.
   `snap.buttons` must be: `cross = (buttons_0 >> 5) & 1` (bit0 of processed field).
3. **Batch analysis max_frames**: default of 30k frames missed button presses in 180s sessions.
   Always use `max_frames=0` (no limit) for full-session analysis.

## Key Technical Notes
- `hardhat.config.js`: viaIR=true (stack-too-deep fix for PoACVerifier)
- PoACVerifierTestable.sol: overrides `_requireValidSignature` (virtual) for Hardhat
- conftest.py: autouse event loop fixture prevents Python 3.13 asyncio teardown crash
- Windows SQLite tests: use `tempfile.mkdtemp()` NOT `TemporaryDirectory` (WAL PermissionError on cleanup)
- Web3/eth_account stub pattern: mock `web3`, `web3.exceptions`, `eth_account` before import
- EWCWorldModel INPUT_DIM=30 (tests need 30-dim input, not 10)
- Windows print encoding: use ASCII (PASS: / ->) NOT Unicode (✓ / →) in test print() calls
- IoTeX: chain ID 4689 mainnet, 4690 testnet; P256 precompile at 0x0100
- ZK circuits: `pragma circom 2.0.0;` — requires circom2 Rust binary (not npm circom)
- circom.exe v2.2.3 in `contracts/` — add to PATH when running ceremony
- `hidapi` library: install as `pip install hidapi` (NOT `hid`)
- `docs/vapi-whitepaper-v2.md` is canonical whitepaper; `paper/vapi-whitepaper.md` archived

## Build & Test Commands
```bash
python -m pytest bridge/tests/ --ignore=bridge/tests/test_e2e_simulation.py -q  # 843 passed
python -m pytest sdk/tests/ -v                                                   # 28
cd contracts && npx hardhat test                                                  # 354
pytest tests/hardware/ -v -m hardware -s                                         # 28 (needs controller)
# ZK ceremony (unblocks 5 skips):
cd /c/Users/Contr/vapi-pebble-prototype/contracts && PATH="$(pwd):$PATH" npx hardhat run scripts/run-ceremony.js
# E2E (needs Hardhat node):
HARDHAT_RPC_URL=http://127.0.0.1:8545 python -m pytest bridge/tests/test_e2e_simulation.py -v
```

## Known Remaining Gaps
- L6 human-response baseline not yet hardware-calibrated (§10.6 whitepaper)
- Multi-person Mahalanobis separation not yet validated across 3 players (§10.7)
- Tremor FFT (8-12 Hz) needs ≥1024-frame window at 1000 Hz; current 120-frame window = 8.3 Hz/bin (too coarse)
- Touchpad features zero for all N=69 sessions (touch_active field added in Phase 17; next sessions will populate)
- IoTeX testnet deployment pending (needs funded wallet — see B4 in MEMORY.md)
- L2C coverage limited: right stick rarely used in NCAA Football 26 (68/69 sessions static stick)
