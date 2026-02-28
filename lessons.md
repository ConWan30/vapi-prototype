# Lessons Learned

- Always run the full test suite (`pytest` for bridge, `npx hardhat test` for contracts) before committing changes.
- The PoAC wire format is 228 bytes EXACTLY. Any change breaks firmware↔contract↔bridge compatibility.
- Biometric thresholds (Mahalanobis 3.0 anomaly, 2.0 continuity) are calibration-dependent — never hardcode without documenting the source.
- The bridge SQLite schema uses migrations tracked in `schema_versions` table — always add a migration, never alter tables directly.
- Groth16 proofs require a trusted setup ceremony — `run-ceremony.js` handles dev setup, but production requires multi-party MPC.
- When editing Solidity contracts, check gas costs with `hardhat test --gas-reporter`. The P256 precompile staticcall pattern must stay under 100K gas per individual verification.
- DualShock Edge sensor commitment schema v2 is DIFFERENT from Pebble Tracker schema v1 — the hash includes stick axes, trigger resistance state, gyro, accelerometer.
- The `OPERATOR_API_KEY` env var must be set for the gate API to function — it returns 503 otherwise (intentional graceful degradation).
- BridgeAgent `_execute_tool()` uses `inputs` dict (not `args`) — consistent across all 18 tools.
- `create_operator_app()` is a factory — rate limiter, agent, all state lives in the closure, not globally.
- InsightSynthesizer Mode 5 (Phase 37): evidence hash is `SHA-256(f"{device_id}:{digest_id}")` referencing immutable `insight_digests` row, not ephemeral `detection_policies`.
- PHGCredential suspension is exponential: `base_s * 2^(consecutive - min)`, capped at max_s (28d). Duration is consequence-graduated.
- AlertRouter polls every 30s, tracks `_last_id`, dispatches via `urllib.request.urlopen` in executor — zero new dependencies.
- FederationBus privacy: cluster fingerprint is `SHA-256("|".join(sorted(device_ids)))[:16]` — 16-char hex prefix. If device population is small (<10K), brute-force is feasible. Document as known limitation.
- Batcher bounded queue maxsize=1000: overflow raises `asyncio.QueueFull` — currently not caught, records are dropped. Add counter metric.
- Synthetic test data produces 100% detection / 0% false positives — meaningless without real-world calibration. Never report this without the "synthetic" caveat.
- Windows: always use `tempfile.mkdtemp()` for SQLite test fixtures (not `TemporaryDirectory`) due to WAL PermissionError on cleanup.
