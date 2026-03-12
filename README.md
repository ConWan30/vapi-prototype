# VAPI: Verifiable Controller Input Provenance with Physics-Backed Liveness for Competitive Gaming

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.18966169.svg)](https://doi.org/10.5281/zenodo.18966169)

**Authors:** Contravious Battle — Independent Researcher

---

## What Is VAPI

VAPI (Verified Autonomous Physical Intelligence) is a cryptographic anti-cheat and identity protocol for competitive gaming. It attaches a tamper-evident, on-chain-verifiable record — a **Proof of Autonomous Cognition (PoAC)** — to every input event produced by a gaming controller. Each 228-byte PoAC record binds raw sensor commitments (IMU, adaptive trigger dynamics, biometric fingerprint) to a hardware-rooted ECDSA-P256 signature and a hash-chained sequence enforced on the IoTeX blockchain. The result is unforgeable evidence that a human physically operated a certified controller during a session, anchored to a nine-level Physical Input Trust Layer (PITL, L0–L6) that detects software injection, bot behavior, and identity transplants at rates impossible for pure software to forge.

---

## Architecture

| Component | Stack | Role |
|-----------|-------|------|
| **Firmware / C / Zephyr** | C · nRF Connect SDK · Zephyr RTOS | IoTeX Pebble Tracker reference implementation — PoAC chain, PSA crypto, MQTT uplink |
| **Python asyncio Bridge** | Python 3.11 · asyncio · FastAPI · SQLite | DualShock Edge HID capture → PITL oracle pipeline → on-chain relay · BridgeAgent (Claude tool_use) |
| **Solidity Contracts / IoTeX** | Solidity 0.8 · Hardhat · IoTeX L1 (P256 precompile 0x0100) | 13 contracts: PoACVerifier, PITLSessionRegistry, PHGCredential, TournamentGateV3, FederatedThreatRegistry, and more |
| **DualShock Edge Controller Subsystem** | Python · hidapi · pydualsense · NumPy | HID report parser · 11-feature biometric extractor · L4 Mahalanobis · L5 rhythm oracle · L6 active challenge-response |

---

## Current Status — Phase 41 Complete

| Suite | Count | Status |
|-------|-------|--------|
| Bridge tests (pytest) | **874** | All pass |
| Hardhat contract tests | **354** | All pass |
| Hardware integration tests | **28** | Pass (requires DualShock Edge) |
| E2E simulation tests | **14** | Pass |
| Contracts on IoTeX testnet | **13** | Live |

Key Phase 41 deliverables:
- FFT tremor analysis ring buffer (512-frame persistent window)
- Full covariance Mahalanobis L4 (`USE_FULL_COVARIANCE` flag, Tikhonov regularization λ=0.01)
- Synthetic inter-player separation validation (5 players, 20 sessions each — ratio 9.85, LOO 98%)
- ZK inference code binding: `pub[2] = inferenceCode` in `PITLSessionRegistry.sol`
- Game genre certification framework (§7.5.2.1 in whitepaper)
- Whitepaper published to Zenodo: [10.5281/zenodo.18966169](https://doi.org/10.5281/zenodo.18966169)

---

## Hardware Requirements

- **Controller:** Sony DualShock Edge CFI-ZCP1 (primary certified device)
- **OS:** Windows 11 (USB HID polling at 1000 Hz)
- **Cable:** USB-C data cable (not charge-only)
- **Python:** 3.11+ with `hidapi` (`pip install hidapi`, NOT `hid`)
- **Node.js:** 18+ (for Hardhat contract tests)

---

## Live Dashboard

The VAPI dashboard (`frontend/VAPIDashboard.jsx`) auto-detects the bridge and switches between **LIVE** and **DEMO** mode.

### Starting the bridge

```bash
cd bridge
pip install -r requirements.txt
python -m vapi_bridge
# Bridge starts on http://localhost:8080
```

### Starting the frontend

```bash
cd frontend
npm install
npm run dev
# Vite dev server on http://localhost:5173
```

### LIVE / DEMO mode

| Mode | Indicator | Behaviour |
|------|-----------|-----------|
| **LIVE** | Pulsing green dot — `LIVE — BRIDGE CONNECTED` | Fetches `/dashboard/snapshot` every 30 s; subscribes to `WS /ws/records` for real-time PoAC record feed; L6 status, PHG score, Mode 6 calibration chart, and session counters update live |
| **DEMO** | Static orange dot — `DEMO — BRIDGE OFFLINE` | All data falls back to whitepaper-accurate hardcoded constants; visual design is identical |

The live record feed panel (bottom of left column) is only visible in LIVE mode and shows the last 10 incoming PoAC records with colour-coded inference codes (green=NOMINAL, orange=ADVISORY, red=HARD CHEAT).

### Verify the connection

```bash
python scripts/test_dashboard_connection.py
# Checks: /health, /dashboard/snapshot fields, CORS headers, WS /ws/records
```

---

## Running Tests

### Bridge (874 tests)
```bash
cd bridge
pip install -r requirements.txt
python -m pytest tests/ --ignore=tests/test_e2e_simulation.py -q
```

### Contracts (354 tests)
```bash
cd contracts
npm install
npx hardhat test
```

### Hardware integration (28 tests — requires DualShock Edge connected via USB)
```bash
python -m pytest bridge/tests/hardware/ -v -m hardware -s
```

### E2E simulation (14 tests — requires local Hardhat node)
```bash
cd contracts && npx hardhat node &
HARDHAT_RPC_URL=http://127.0.0.1:8545 python -m pytest bridge/tests/test_e2e_simulation.py -v
```

### ZK proofs (5 tests — requires ceremony artifacts)
```bash
# Run ceremony first (one-time, ~10 minutes):
cd contracts && PATH="$(pwd):$PATH" npx hardhat run scripts/run-ceremony.js
# Then:
python -m pytest bridge/tests/test_zk_prover_real.py -v
```

---

## Key Files

```
vapi-pebble-prototype/
├── bridge/
│   ├── vapi_bridge/
│   │   ├── dualshock_integration.py     # Main DualShock HID bridge (PITL L0–L6)
│   │   ├── tinyml_biometric_fusion.py   # L4: 11-feature Mahalanobis biometric
│   │   ├── temporal_rhythm_oracle.py    # L5: IBI CV/entropy/quantization
│   │   ├── pitl_prover.py               # ZK PITL session proof generator
│   │   └── chain.py                     # IoTeX on-chain relay
│   └── tests/                           # 874 test files
├── contracts/
│   ├── contracts/
│   │   ├── PoACVerifier.sol             # ECDSA-P256 + chain integrity
│   │   ├── PITLSessionRegistry.sol      # ZK PITL proof registry + anti-replay
│   │   ├── PHGCredential.sol            # Soulbound humanity credential (ERC-5192)
│   │   └── TournamentGateV3.sol         # PHG-gated + credential-active entry
│   └── circuits/
│       └── PitlSessionProof.circom      # Groth16 ZK circuit (~1,820 constraints)
├── controller/
│   ├── tinyml_biometric_fusion.py       # L4 biometric feature extractor + classifier
│   ├── temporal_rhythm_oracle.py        # L5 temporal rhythm oracle
│   ├── l2b_imu_press_correlation.py     # L2B: IMU-button causal latency
│   └── l2c_stick_imu_correlation.py     # L2C: stick-IMU cross-correlation
├── scripts/
│   ├── capture_session.py               # Live DualShock session capture
│   ├── threshold_calibrator.py          # Recalibrate L4/L5 thresholds from sessions
│   └── generate_synthetic_players.py   # Synthetic inter-player separation validator
├── docs/
│   ├── vapi-whitepaper-v3.md            # Canonical whitepaper (source)
│   └── vapi-whitepaper-v3.pdf           # Compiled PDF
└── calibration_profile.json             # Production L4/L5 thresholds (N=74 sessions)
```

---

## Citation

```bibtex
@software{battle_2026_vapi,
  author    = {Battle, Contravious},
  title     = {VAPI: Verifiable Controller Input Provenance with
               Physics-Backed Liveness for Competitive Gaming},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.18966169},
  url       = {https://doi.org/10.5281/zenodo.18966169}
}
```

---

## License

Copyright (C) 2026 Contravious Battle. All Rights Reserved.
