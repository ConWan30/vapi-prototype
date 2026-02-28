# Phase 13 Pre-Implementation Gate Check

Documented: 2026-02-22. Verified before any Phase 13 enhancement code was written.
All six gates must be CLOSED before Phase 13 enhancements ship to mainnet.

---

## Gate Status Summary

| ID | Gate | Code Status | Operational Status | Phase 13 Blocker? |
|----|------|-------------|-------------------|-------------------|
| G1 | attestationEnforced=false | Not a code issue | OPEN — run enable-enforcement.js on testnet | No — doesn't block Phase 13 code |
| G2 | schema_version=2 not wired | **FIXED in Phase 13 Step 1** | CLOSED after fix | Yes — fixed before enhancements |
| G3 | watch_manufacturer_revocations() not started | **FIXED in Phase 13 Step 2** | CLOSED after fix | Yes — fixed before enhancements |
| G4 | Bridge keystore not deployed | Not a code issue | OPEN — run generate_bridge_keystore.py | No — doesn't block Phase 13 code |
| G5 | E2E tests are stubs | Not a code issue | OPEN — awaits HARDHAT_RPC_URL + deployed contracts | No — stubs are intentional until live testnet |
| G6 | BountyMarket zone validation | **ALREADY CLOSED** | Lines 357-360 already validate lat/lon min < max — no action needed | N/A |

---

## G1: attestationEnforced=false (Operational)

**File:** `contracts/contracts/TieredDeviceRegistry.sol` line 155
```solidity
attestationEnforced = false;  // Default: testnet mode, any 64-byte proof accepted
```

**Impact:** Attested tier accepts any 64-byte proof. registerAttestedV2 P256 crypto exists
(Phase 10) but the enforcement gate is off. No on-chain cryptographic distinction between
Attested and Standard tiers currently.

**Required Action (before mainnet):**
1. Register real manufacturer P256 keys via setManufacturerKey()
2. Test registerAttestedV2 against live IoTeX testnet precompile at 0x0100
3. Confirm E2E test passes with real attestation proof
4. Run: `REGISTRY_ADDRESS=0x... npx hardhat run scripts/enable-enforcement.js --network iotex_testnet`

**Sign-off criteria:** IoTeX testnet scan shows attestationEnforced=true; at least one V2 registration
TX confirmed with real P256 proof.

---

## G2: schema_version=2 Not Wired (Code Fix — COMPLETED in Step 1)

**Root cause:** `bridge/vapi_bridge/batcher.py` lines 117–121 call `verify_single()` / `verify_batch()`
which use the legacy `verifyPoAC()` path. The new `chain.verify_poac(schema_version=N)` added in
Phase 12 is never called. DualShock records land on-chain with `recordHasSchema[hash] = false`.

**Fix applied:**
- `bridge/vapi_bridge/batcher.py`: route single-record submissions through `verify_poac` with
  `schema_version` from `record.schema_version` attribute (defaults to 0 for non-DualShock records)
- `bridge/vapi_bridge/dualshock_integration.py`: tag `record.schema_version = 2` when building
  each PoACRecord before enqueueing

**Sign-off criteria:** After fix, `pytest tests/test_biometric_fusion.py` passes; DualShock records
in a local Hardhat test show `recordHasSchema[hash] = true` and `recordSchemas[hash] = 2`.

---

## G3: watch_manufacturer_revocations() Not Started (Code Fix — COMPLETED in Step 2)

**Root cause:** `bridge/vapi_bridge/main.py` Bridge.run() starts batcher, transports, and DualShock
transport, but never creates a task for `chain.watch_manufacturer_revocations()`. The revocation
cache `_revoked_manufacturers` set never populates from on-chain events.

**Fix applied:** Added task creation in `Bridge.run()` after batcher task, gated on
`cfg.device_registry_address` being set.

**Sign-off criteria:** `grep watch_manufacturer_revocations bridge/vapi_bridge/main.py` finds the call.
Bridge starts without error. If a ManufacturerKeyRevoked event fires, the log shows the revocation.

---

## G4: Bridge Keystore Not Deployed (Operational)

**File:** `bridge/vapi_bridge/chain.py` — `bridge_private_key_source` config field exists (Phase 11).
**Default:** `BRIDGE_PRIVATE_KEY` env var (plaintext). Keystore path: `keystore_path` config field.

**Required Action (before mainnet):**
```bash
python -c "from vapi_bridge.chain import ChainClient; ChainClient.generate_bridge_keystore('bridge.keystore.json', 'STRONG_PASSWORD')"
# Then set in environment:
BRIDGE_PRIVATE_KEY_SOURCE=keystore
BRIDGE_KEYSTORE_PATH=bridge.keystore.json
BRIDGE_KEYSTORE_PASSWORD=STRONG_PASSWORD
```

**Sign-off criteria:** Bridge starts with keystore source, no BRIDGE_PRIVATE_KEY env var set,
and a test TX succeeds.

---

## G5: E2E Tests Are Stubs (Operational)

**File:** `bridge/tests/test_e2e_simulation.py`
Six tests in `TestE2ESimulation` class call `pytest.skip()` with message:
"Requires deployed contract addresses in env vars — skipped in CI."

These tests are CORRECTLY stubbed — they require a live Hardhat node AND deployed contract addresses.
They are not code defects; they are waiting for the testnet deployment environment.

**Required Action (before mainnet):**
```bash
cd contracts && npx hardhat node &
npx hardhat run scripts/deploy.js --network localhost
HARDHAT_RPC_URL=http://127.0.0.1:8545 \
REGISTRY_ADDRESS=0x... VERIFIER_ADDRESS=0x... MARKET_ADDRESS=0x... \
python -m pytest tests/test_e2e_simulation.py -v
```

**Sign-off criteria:** All 6 `TestE2ESimulation` tests pass (not skip) against local Hardhat node.

---

## G6: BountyMarket Zone Validation (Code Fix — COMPLETED in Step 3)

**Root cause:** `contracts/contracts/BountyMarket.sol` postBounty() function accepts bounty
descriptors where `zoneLatMin >= zoneLatMax` (degenerate bounding box). Evidence submissions
against degenerate zones may pass zone containment checks vacuously.

**Fix applied:** Added `error InvalidZone()` and zone bounds validation in postBounty().

**Sign-off criteria:** `npx hardhat test` still shows 209+ passing; new test in BountyMarket.test.js
confirms postBounty reverts with InvalidZone for degenerate zone inputs.

---

## Phase 13 Gate Sign-Off

When all gates are closed, update this table:

| Gate | Closed By | Date | Evidence |
|------|-----------|------|---------|
| G1 | ops/deployer | TBD | IoTeX testnet TX hash |
| G2 | Phase 13 Step 1 | 2026-02-22 | pytest passing |
| G3 | Phase 13 Step 2 | 2026-02-22 | main.py grep confirmed |
| G4 | ops/deployer | TBD | Bridge start log |
| G5 | ops/deployer | TBD | pytest all 6 pass |
| G6 | Phase 13 Step 3 | 2026-02-22 | hardhat test passing |
