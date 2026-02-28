# VAPI Lessons & Memory

## Architecture Decisions
- The gaming anti-cheat (DualShock Edge + PITL + PoAC) is the primary value proposition. DePIN/IoT is extensibility validation only.
- The bridge is the weakest trust link — ZK PITL proofs (Phase 26) are the fix, but currently run in mock mode with dev ceremony keys.
- The adaptive trigger resistance surface is what makes this novel — no other anti-cheat protocol has a hardware-rooted unforgeable biometric signal.
- PHGCredential is now provisional (Phase 37): earned when stable, suspended on-chain when device accumulates ≥2 consecutive critical 7-day windows.
- InsightSynthesizer (Phase 35–37) provides full temporal threat memory: 24h/7d/30d retrospective analysis driving forward detection policy updates.

## Code Quality Notes
- Bridge Python code uses asyncio throughout — never use synchronous blocking calls.
- All Solidity contracts target IoTeX L1 with P256 precompile at address 0x0100.
- Firmware uses PSA Crypto API — never software crypto on the nRF9160.
- The 228-byte PoAC wire format is frozen — do not change field offsets.
- BridgeAgent has 18 tools (Phase 37); uses `inputs` dict not `args` in `_execute_tool()`.
- RateLimiter is instantiated inside `create_operator_app()` factory — NOT global.
- Windows SQLite tests: use `tempfile.mkdtemp()` NOT `TemporaryDirectory` (WAL PermissionError).
- conftest.py: autouse event loop fixture prevents Python 3.13 asyncio teardown crash.

## Testing Notes
- Hardhat tests: ~341, Bridge pytest: ~728, SDK pytest: ~28, Hardware suite: ~72
- Combined bridge+SDK: 728 passed, 7 skipped (Phase 37 baseline)
- The "100% detection / 0% false positive" figure is on SYNTHETIC data only — this must be clearly stated everywhere.
- Real-hardware validation is the #1 priority gap.
- 5 real-ZK tests skip unless `run-ceremony.js` artifacts exist.
- E2E tests need Hardhat node: `HARDHAT_RPC_URL=http://127.0.0.1:8545`

## Whitepaper Notes
- Source: `paper/vapi-whitepaper.md` (CURRENT); `whitepaper/vapi-whitepaper.md` (ARCHIVED)
- Rewrite target: `docs/vapi-whitepaper-v2.md`
- The paper tries to be 3 things (DePIN protocol + gaming anti-cheat + economic agent framework). Lead with gaming.
- §7.5 Phases 18–37 need complete rewrite from changelog format to proper technical exposition.
- BridgeAgent (Claude tool_use) is a UX feature, not a protocol contribution — move to appendix.
- All detection percentages must include "on synthetic test patterns" caveat.
- Remove all phase numbers from main text (internal dev milestones only).

## Threshold Documentation Needs
- L4 Mahalanobis anomaly threshold (3.0): magic number, needs empirical calibration comment
- L4 continuity threshold (2.0): magic number, needs empirical calibration comment
- L5 CV < 0.08, entropy < 1.5 bits, quantization > 0.55: magic numbers
- Behavioral warmup sigmoid scaling factor 20000: magic number needing derivation comment
- Burst farming CV / 2.0 formula: magic number needing derivation comment
- DBSCAN ε=1.0, min_samples=3: magic numbers

## Hardware Testing
- DualShock Edge VID:PID: Sony CFI-ZCP1
- Hardware tests in `tests/hardware/`, marker `@pytest.mark.hardware`
- Excluded from CI by default via `addopts = -m "not hardware"`
- Session captures saved to `sessions/` directory
- Calibration output: `calibration_profile.json`
