# VAPI Project — Claude Code Context

## What This Project Is
VAPI = Verified Autonomous Physical Intelligence. A cryptographic anti-cheat protocol for competitive gaming. Core primitive: Proof of Autonomous Cognition (PoAC) — a 228-byte hash-chained evidence record binding sensor commitments, model attestation, world-model state, and inference outputs, signed with hardware-backed ECDSA-P256.

## Primary Device
DualShock Edge (Sony CFI-ZCP1) — the production PHCI-certified gaming controller. Its adaptive triggers (motorized L2/R2 resistance) create an unforgeable biometric detection surface.

## Secondary Device (extensibility validation only)
IoTeX Pebble Tracker (nRF9160 + CryptoCell-310) — DePIN environmental sensor. Same 228-byte PoAC format, different sensor domain.

## Architecture Layers
1. **Firmware** — C/Zephyr RTOS (nRF9160) + C/ESP-IDF (controller)
2. **Smart Contracts** — Solidity on IoTeX L1 (P256 precompile at 0x0100)
3. **Bridge Service** — Python asyncio (MQTT/CoAP/HTTP ingestion → batch → on-chain relay)
4. **SDK** — Python + C99 header (VAPISession, VAPIVerifier, self_verify loop)
5. **PITL** — 6-layer Physical Input Trust Layer (L0–L5) for bot/cheat detection
6. **Dashboard** — FastAPI + Alpine.js player/operator dashboards

## Core Contract Stack (priority order for audit)
1. PoACVerifier — signature + chain integrity verification
2. TieredDeviceRegistry — device identity + staking + reputation
3. PHGRegistry — on-chain humanity score checkpoints
4. TournamentGate / TournamentGateV2 — PHG-gated tournament access
5. PHGCredential — soulbound ERC-5192 humanity credential
6. PITLSessionRegistry — ZK PITL session proof registry
7. BountyMarket — DePIN bounty lifecycle
8. SkillOracle / ProgressAttestation / TeamProofAggregator — gaming contracts

## Key Technical Decisions
- ECDSA-P256 (not secp256k1) — matches IoTeX P256 precompile + CryptoCell-310 hardware
- SHA-256 for all commitments (hardware-accelerated on CryptoCell-310)
- Fixed 228-byte wire format — zero-copy deserialization, fits NB-IoT uplink
- Groth16 over BN254 for ZK PITL proofs (~1,820 constraints)
- YubiKey PIV / ATECC608A for hardware-rooted signing (key never leaves hardware)

## Current Project Phase
Pre-hardware-validation. All code is written. Next milestone: connect physical DualShock Edge via USB and run real-world detection tests.

## Known Gaps (from external review)
- Evaluation data is all synthetic/emulated — no real-hardware benchmarks
- Bridge is still effectively trusted (ZK proofs in mock mode)
- Biometric thresholds are magic numbers without empirical calibration data
- Whitepaper §7.5 Phase 18–37 is a changelog, not paper-quality prose
- DePIN/IoT layer dilutes the gaming anti-cheat narrative
- Test suite has no real adversarial gameplay data

## Decisions Made
- Feature branch: `fix/external-review-v1` — all review work happens here
- Whitepaper rewrite target: `docs/vapi-whitepaper-v2.md`
- Hardware tests: `tests/hardware/` directory, `@pytest.mark.hardware` marker, excluded from CI by default
- Synthetic test generators: `tests/data/realistic_generators.py`
- Detection benchmarks: `docs/detection-benchmarks.md`
- All performance figures must include "on synthetic test patterns" caveat every occurrence
- Gas report: `docs/gas-report.md` (already exists in contracts/)
- Session capture output: `sessions/` directory
