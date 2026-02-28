# VAPI Task Tracker

## Active: DualShock-Primary Documentation Pivot

> Goal: Invert the VAPI narrative from "DePIN protocol with gaming extension" to
> "verifiable gaming intelligence protocol with DePIN extensibility."
> Zero code changes. Zero test changes. Documentation + docstrings only.

### Checklist

- [x] Plan mode exploration — identified all files needing change
- [x] Plan approved by user
- [x] `tasks/todo.md` — this file
- [x] `tasks/lessons.md` — lessons tracker
- [x] `overview/vapi-dualshock-primary.md` — flagship positioning doc (NEW)
- [x] `README.md` — rewrite opening (title, subtitle, device table)
- [x] `paper/vapi-whitepaper.md` — abstract, §7.5, §8.5, conclusion
- [x] `positioning/physical-input-trust-layer.md` — §4.3 reframe
- [x] `controller/dualshock_emulator.py` — rename "Pebble VAPI alignment" section
- [x] `bridge/vapi_bridge/dualshock_integration.py` — add primary device note
- [x] Regression verification: 205 pytest + 173 Hardhat + 72 HW

### Novel angles to land in all docs
1. Adaptive trigger detection surface (L2/R2 resistance curves vs software injection)
2. "Proof of Human Gaming" framing
3. Device capability taxonomy: {sensor_stream, signing_key, monotonic_counter, network_bridge}
4. Sensor commitment schema v2 (kinematic/haptic) vs v1 (environmental)

---

---

## Phase 10: Full Attestation Enforcement + Manufacturer Registry

> Goal: Close the root trust gap — real ECDSA-P256 manufacturer verification via IoTeX
> precompile 0x0100; sensor commitment upgrade to schema v2 (kinematic/haptic).
> Backward-compatible: 173 existing Hardhat tests unchanged. +12 new = 185 total.

### Checklist

- [x] Plan mode exploration — assessed all gaps from Phase 9 assessment
- [x] Plan approved by user
- [x] `contracts/contracts/TieredDeviceRegistry.sol` — ManufacturerKey struct, manufacturerKeys state, 3 new errors, 3 new events, _validateAttestationV2, registerAttestedV2, registerAttestedWithCertV2, getManufacturerKey, setManufacturerKey, revokeManufacturerKey
- [x] `contracts/test/TieredDeviceRegistry.test.js` — constants + describe blocks 15 (5 tests) + 16 (7 tests) = 12 new tests
- [x] `bridge/vapi_bridge/dualshock_integration.py` — _l2_effect_mode/_r2_effect_mode instance vars; sensor commitment schema v2 (48 bytes)
- [x] `controller/dualshock_emulator.py` — l2_effect_mode/r2_effect_mode dataclass fields; sensor commitment schema v2 in VAPIAgent.l1_cycle()
- [x] `bridge/tests/test_sensor_commitment_v2.py` — NEW; 10 pytest tests for schema v2
- [x] `bridge/vapi_bridge/chain.py` — 5 new ABI entries (registerAttestedV2, registerAttestedWithCertV2, setManufacturerKey, revokeManufacturerKey, manufacturerKeys)
- [x] `bridge/attestation-enforcement-guide.md` — NEW; 8-section operator guide
- [x] `tasks/todo.md` — this file
- [x] `tasks/lessons.md` — Phase 10 lessons
- [x] Verification: Hardhat 185 passing
- [x] Verification: Bridge pytest 215 passing

### Target test counts
- Hardhat: 173 + 12 = **185**
- Bridge pytest: 205 + 10 = **215**
- HW tests: 72 (unchanged)

### Key constraints
- Old `_validateAttestation()` body unchanged; old registerAttested/registerAttestedWithCert unchanged
- Block 7 test (AttestationValidatorNotImplemented when enforced=true via old functions) still passes
- 228-byte PoAC wire format immutable; schema v2 changes 48-byte hash input, not 32-byte hash output
- Windows terminal: no Unicode chars in new print statements

---

---

## Phase 11: Mainnet Readiness — Tier Enforcement + Key Security + Schema v2 Integrity

> Goal: Turn the tier system from decorative into economically enforceable; make schema v2
> carry real adaptive trigger data; secure the bridge key; validate the precompile path.
> Target: ~510 tests (+38 from current 472).

### Checklist

- [x] Priority 4: `setApprovedManufacturer` @deprecated NatDoc in TieredDeviceRegistry.sol
- [x] Priority 1: `canClaimBounty` virtual on DeviceRegistry; BountyMarket IneligibleTier error+check; BountyMarket.test.js (8 tests) — **Hardhat: 193 passing**
- [x] Priority 2: Adaptive trigger effect mode wired to hardware — `set_trigger_effect()`, `_update_trigger_effect_modes()`, test_trigger_effect_modes.py (6 tests)
- [x] Priority 3: Bridge key security — `bridge_private_key_source`/`keystore_path` config fields; ChainClient keystore path; generate_bridge_keystore.py; test_chain_keystore.py (8 tests) — **pytest: 229 passing**
- [x] Priority 5: `contracts/scripts/test_p256_precompile.js` — IoTeX testnet precompile validator
- [x] Priority 6: `contracts/scripts/deploy-mainnet.js` + `contracts/scripts/enable-enforcement.js`
- [x] Priority 7: Whitepaper §7.1 updated to 493 tests

---

### Priority 1 — Tier Enforcement in Downstream Contracts (PARAMOUNT) [DONE]
**Problem:** `canClaimBounties=false` for Emulated tier is stored but never checked.
An Emulated-tier device can call BountyMarket.submitEvidence() and earn full rewards today.
The tier system has zero economic teeth without this.

**Files to change:**
- `contracts/contracts/BountyMarket.sol`
  - Add `DeviceRegistry` interface import (already has registry address)
  - Add check in `submitEvidence()`: call `registry.canClaimBounty(deviceId)`, revert `IneligibleTier(deviceId)` if false
  - New custom error: `error IneligibleTier(bytes32 deviceId)`
- `contracts/contracts/SkillOracle.sol`
  - Add check in `updateRating()`: call `registry.canUseSkillOracle(deviceId)` — but note: Emulated CAN use SkillOracle (canUseSkillOracle=true). Standard and Attested can claim bounties. Verify TierConfig values first before adding check.
  - Actually: SkillOracle check may not be needed (all tiers have canUseSkillOracle=true). Focus on BountyMarket.
- `contracts/test/BountyMarket.test.js`
  - Add tests: Emulated-tier device submitEvidence() reverts IneligibleTier
  - Add tests: Standard-tier and Attested-tier devices can submit evidence
  - Target: +8 tests

**Key constraint:** BountyMarket already has a registry reference. Check how it's stored
(likely `DeviceRegistry` interface). Add `canClaimBounty` to the interface or call via
TieredDeviceRegistry — use `IDeviceRegistry` pattern or cast.

---

### Priority 2 — Adaptive Trigger Effect Mode Wired to Hardware (SCHEMA v2 INTEGRITY)
**Problem:** `_l2_effect_mode` and `_r2_effect_mode` are always 0. Schema v2 was built to
include adaptive trigger resistance state as a hardware-unforgeable signal. With both always
0, schema v2 is functionally identical to v1 + sticks + timestamp. The core novel claim
("software injection cannot reproduce L2/R2 resistance dynamics") is not yet in the hash.

**Files to change:**
- `bridge/vapi_bridge/dualshock_integration.py`
  - In `_init_hardware()`: after `self._reader = DualSenseReader()`, try to import
    `TriggerModes` from pydualsense. Store reference: `self._TriggerModes = TriggerModes`
  - Add method `_update_trigger_effect_modes(snap)`: reads current trigger mode from
    `self._reader.ds` if connected (e.g. `getattr(ds, 'trigger_L', None)`) and maps
    to TriggerMode ordinal. Falls back to 0 if not available.
  - In `_make_record()`: call `self._update_trigger_effect_modes(snap)` before building
    `commitment_bytes` so `_l2_effect_mode`/`_r2_effect_mode` reflect actual hardware state.
  - Add public setter `set_trigger_effect(side, mode_ordinal)` for when the transport
    deliberately sets a trigger effect (e.g. cheat-alert haptic feedback): update
    `_l2_effect_mode` or `_r2_effect_mode` immediately.
- `controller/dualshock_emulator.py`
  - In `DualSenseReader.poll()`: when `HAS_DUALSENSE` and connected, attempt to read
    active trigger mode from `ds` state and set `snap.l2_effect_mode`/`snap.r2_effect_mode`.
    Use `getattr(self.ds, 'triggerL', None)` with safe fallback = 0.
  - In `DualSenseReader._simulate_input()`: simulate occasional mode changes
    (e.g. every 500 frames, randomly pick mode 0/1/2) so simulation exercises non-zero modes.
- `bridge/tests/test_dualshock_integration.py` (or new file)
  - Add tests: effect mode changes before record generation produce different sensor_hash
  - Target: +6 tests

---

### Priority 3 — Bridge Key Security (DEPLOYMENT BLOCKER)
**Problem:** `BRIDGE_PRIVATE_KEY` is plaintext in env var. Key compromise = ability to
register arbitrary devices and submit fraudulent bounty evidence on-chain.

**Files to change:**
- `bridge/vapi_bridge/config.py`
  - Add `keystore_path: str` and `keystore_password_env: str` config fields
  - Add `bridge_private_key_source: str` = "env" | "keystore" (default "env")
- `bridge/vapi_bridge/chain.py`
  - In `ChainClient.__init__()`: check `cfg.bridge_private_key_source`
    - "env": existing behavior (plaintext key from env, emit WARNING log)
    - "keystore": load via `Account.decrypt(json.load(keystore_path), password)`
      where password comes from env var named by `keystore_password_env`
  - Add `ChainClient.generate_keystore(output_path, password)` classmethod for setup
- `scripts/generate_bridge_keystore.py` (NEW)
  - CLI tool: reads `BRIDGE_PRIVATE_KEY` env var, encrypts to keystore JSON, writes file
  - Prints: "Delete BRIDGE_PRIVATE_KEY from env after confirming keystore works"
- `bridge/tests/test_chain_keystore.py` (NEW)
  - Tests: keystore round-trip (generate → load → same address)
  - Tests: wrong password raises ValueError
  - Tests: "env" source still works (backward compat)
  - Target: +8 tests

---

### Priority 4 — Dead State Cleanup: `approvedManufacturers`
**Problem:** `mapping(address => bool) public approvedManufacturers` is set by
`setApprovedManufacturer()` but never read anywhere. An operator calling it thinks
they're enabling attestation; they're doing nothing. Protocol confusion risk.

**Options (choose one):**
A. Wire it into `_validateAttestation()` (V1 path): add check `if (!approvedManufacturers[someAddress])` — but V1 path uses no manufacturer address, so this doesn't naturally fit.
B. Deprecate: add NatDoc `@deprecated Use setManufacturerKey instead.` to `setApprovedManufacturer`. Add `emit` warning. Keep for backward compat storage reads.
C. Remove (breaking): delete mapping + function. No existing tests depend on the mapping being writable in a meaningful way (block 11 tests only check the bool storage value).

**Recommended: Option B** — deprecate in-place (non-breaking), add NatDoc, zero code risk.
- `contracts/contracts/TieredDeviceRegistry.sol`: add `@deprecated` NatDoc to `setApprovedManufacturer`
- `contracts/test/TieredDeviceRegistry.test.js`: update block 11 comment to note deprecated

---

### Priority 5 — IoTeX Testnet Precompile Integration Test
**Problem:** `_p256Verify` has never been called against the real IoTeX precompile.
The 160-byte input layout is per IoTeX docs but untested. One byte-order error = silent
failure on mainnet.

**Files to change:**
- `scripts/test_p256_precompile.js` (NEW — runs against iotex_testnet, not Hardhat)
  - Generates a real P256 keypair (via ethers or noble-curves)
  - Computes `msgHash = keccak256(pubkey)`
  - Signs with private key → extract r, s
  - Encodes 160-byte input: `msgHash || r || s || x || y`
  - Calls precompile via `provider.call({to: "0x0100", data: input})`
  - Asserts decoded result == 1
  - Also tests invalid signature → result == 0
- This is a script, not a Hardhat test (needs real IoTeX testnet RPC)
- Document in attestation-enforcement-guide.md §6 under "Before enabling enforcement"

---

### Priority 6 — Mainnet Deployment Script
**Files to change:**
- `scripts/deploy-mainnet.js` (NEW)
  - Deploys TieredDeviceRegistry with mainnet deposits (10/100/1 IOTX)
  - Calls `setManufacturerKey(manufacturer, x, y, name)` from env vars
  - Does NOT call `setAttestationEnforced(true)` — that's a manual step after E2E test
  - Does NOT transfer ownership — that's a manual step to Gnosis Safe
  - Prints full post-deploy checklist with tx hashes
- `scripts/enable-enforcement.js` (NEW)
  - Standalone: calls `setAttestationEnforced(true)` on an already-deployed registry
  - Requires `REGISTRY_ADDRESS` env var
  - Prompts "Are you sure? This enables strict P256 verification." before sending

---

### Priority 7 — Whitepaper / Paper Test Count Update (QUICK)
- `paper/vapi-whitepaper.md`: grep "450 tests" → update to "472 tests" (Phase 10 complete)
- Add one sentence in §8.5 mentioning ManufacturerKey registry and V2 attestation path

---

### Execution Order
1. Priority 4 (dead state NatDoc) — 5 min, zero risk, sets clean baseline
2. Priority 1 (tier enforcement) — highest functional impact, ~2hr
3. Priority 2 (adaptive trigger wiring) — schema v2 integrity, ~1.5hr
4. Priority 3 (bridge keystore) — security, ~1hr
5. Priority 6 (deploy scripts) — ~45min
6. Priority 5 (testnet precompile test) — requires IoTeX testnet RPC + IOTX balance
7. Priority 7 (whitepaper) — 5 min

### Test Count Target
- Hardhat: 185 + 8 (tier enforcement) = **193**
- Bridge pytest: 215 + 6 (effect mode) + 8 (keystore) = **229**
- HW: 72 unchanged
- **Total: 494** (conservative; may reach 510 with additional edge cases)

### To resume next session, say:
> "Continue VAPI Phase 16 — execute todo.md plan. See MEMORY.md for full context."

---

---

## Phase 12: Close All Mainnet Blockers — Programmatic Gap Remediation

> Goal: Close three programmatically-fixable blockers blocking mainnet claims:
> (1) TeamProofAggregator.allClean hardcoded true, (2) ProgressAttestation schema mixing,
> (3) Bridge lacks V2 wrappers and revocation listener.
> Foundation: PoACVerifier persists inferenceResult + schema side-channel.
> Zero breaking changes. 209 Hardhat + 244 pytest passing (6 e2e skipped without live node).

### Checklist

- [x] Plan mode exploration — assessed all gaps
- [x] Plan approved by user
- [x] `contracts/contracts/PoACVerifier.sol` — recordInferences, recordSchemas, recordHasSchema, InvalidSchemaVersion, verifyPoACWithSchema, getRecordSchema, inference persistence in _verifyInternal
- [x] `contracts/contracts/TeamProofAggregator.sol` — CHEAT_INFERENCE_MIN/MAX constants, CheatFlagDetected error, cheat check loop in submitTeamProof
- [x] `contracts/contracts/ProgressAttestation.sol` — IncompatibleSchema error, schema check in attestProgress (scoped block)
- [x] `contracts/test/PoACVerifier.test.js` — describe block 18 "Record Inference and Schema Storage" (6 tests)
- [x] `contracts/test/TeamProofAggregator.test.js` — describe block 10 "Cheat Flag Enforcement" (5 tests)
- [x] `contracts/test/ProgressAttestation.test.js` — describe block 12 "Schema Version Validation" (5 tests)
- [x] Hardhat: 209 passing
- [x] `bridge/vapi_bridge/chain.py` — VERIFIER_ABI (verifyPoACWithSchema, getRecordSchema, recordInferences), REGISTRY_ABI (getManufacturerKey, ManufacturerKeyRevoked event), _revoked_manufacturers, verify_poac, register_device_attested_v2, get_manufacturer_key, is_manufacturer_revoked, watch_manufacturer_revocations
- [x] `bridge/tests/test_chain_v2_methods.py` — NEW; 6 tests
- [x] `bridge/tests/test_event_listener.py` — NEW; 4 tests
- [x] `bridge/tests/conftest.py` — NEW; autouse event_loop fixture (Python 3.12+ asyncio compat)
- [x] `bridge/tests/test_e2e_simulation.py` — NEW; 5 pure-Python smoke tests + 6 skippable e2e stubs
- [x] pytest: 244 passing + 6 skipped (e2e stubs, need HARDHAT_RPC_URL)
- [x] `contracts/scripts/transfer-ownership.js` — NEW; dry-run + live ownership transfer
- [x] `tasks/todo.md` — this file
- [x] `tasks/lessons.md` — Phase 12 lessons

### Test Count
- Hardhat: 193 + 16 = **209**
- Bridge pytest: 229 + 15 = **244** (+ 6 skipped e2e stubs)
- HW tests: 72 (unchanged)
- **Total passing: 525**

---

---

## Phase 13: Agent Capability Expansion — COMPLETE

> Goal: Four novel enhancements making VAPI irreplaceable. Enrich the three underutilized
> fields in the immutable 228-byte PoAC body without changing wire format.
> Gate fixes first; Block 1 (E1+E4) → Block 2 (E2) → Block 3 (E3 design+mock).
> Final: 630 total tests (72 HW + 225 Hardhat + 333 pytest).

### Checklist

- [x] Gate Fix G2: `codec.py` schema_version field; `batcher.py` verify_poac routing; `main.py` DualShock schema_version=2 tagging
- [x] Gate Fix G3: `main.py` revocation listener task start
- [x] Gate Fix G6: Already closed (BountyMarket zone validation at lines 357-360) — documented in gate-check.md
- [x] E1 Biometric Fusion: `controller/tinyml_biometric_fusion.py` + `bridge/tests/test_biometric_fusion.py` (20 tests)
- [x] E4 EWC World Model: `controller/world_model_continual.py` + `bridge/tests/test_world_model_continual.py` (25 tests)
- [x] ProgressAttestation.sol: WORLD_MODEL_EVOLUTION (MetricType 4)
- [x] ProgressAttestation.test.js: describe "13. World Model Evolution Metric" (4 tests)
- [x] E2 Personalized Optimizer: `controller/knapsack_personalized.py` + `bridge/tests/test_knapsack_personalized.py` (23 tests)
- [x] E3 ZK Swarm: `contracts/contracts/TeamProofAggregatorZK.sol` + `TeamProofAggregatorZKTestable.sol` + `contracts/test/TeamProofAggregatorZK.test.js` (12 Hardhat tests)
- [x] E3 Python: `bridge/swarm_zk_aggregator.py` + `bridge/tests/test_swarm_zk_aggregator.py` (21 tests)
- [x] `overview/agent-enhancements.md` — full enhancement docs + synergy matrix
- [x] `tasks/gate-check.md` — gate status documentation
- [x] `tasks/todo.md` + `tasks/lessons.md` + `MEMORY.md` updated
- [x] TeamProofAggregator.sol: `submitTeamProof` changed to `public virtual` for ZK inheritance

### Test Count
- Hardhat: 213 + 12 (ZK) = **225**
- Bridge pytest: 289 + 23 (knapsack) + 21 (zk) = **333** (+ 6 skipped)
- HW tests: 72 (unchanged)
- **Total passing: 630**

---

## Phase 14A: Pipeline Integration — COMPLETE

> Goal: Wire all Phase 13 modules into the live PoAC pipeline so every DualShockTransport
> record uses E1 biometric sensor commitment (56B), E4 EWC world model hash, and E2
> preference weights. No wire format change. No new Solidity.

### Checklist

- [x] `bridge/vapi_bridge/dualshock_integration.py`: 7 changes (instance vars, Phase 13 module init, Layer 4 biometric in _session_loop, mode history + EWC scheduling, _make_record sensor_hash via compute_sensor_commitment_v2_bio, _make_record wm_hash via ewc_model.compute_hash, _build_ewc_session_vec helper)
- [x] `bridge/vapi_bridge/batcher.py`: batch path checks schema_version > 0; splits into individual verify_poac() calls per record
- [x] `bridge/tests/test_pipeline_integration.py`: 8 new integration tests (sensor commitment, world model hash, Layer 4, batch routing)
- [x] `MEMORY.md` + `tasks/todo.md` + `tasks/lessons.md` updated

### Test Count
- Hardhat: 225 (unchanged)
- Bridge pytest: 333 + 8 = **341** (+ 6 skipped)
- HW tests: 72 (unchanged)
- **Total passing: 638**

---

---

## Phase 14B: Production Hardening — COMPLETE

> Goal: Close the 5 highest-severity post-14A gaps: EWC session vector fidelity,
> model persistence, hot-loop import, inference name registry, keccak fallback.
> No wire format change. No new Solidity. +5 new tests.

### Checklist

- [x] **B4**: `GAMING_INFERENCE_NAMES[0x30] = "BIOMETRIC_ANOMALY"` in dualshock_integration.py
- [x] **B3**: `self._bio_extractor_cls = BiometricFeatureExtractor` stored in `_init_hardware()`; in-loop import removed
- [x] **B1a**: `FeatureFrame.to_vector()` added to dualshock_emulator.py (30-field float32 ndarray)
- [x] **B1b**: `_build_ewc_session_vec()` uses `self._classifier.window` → `EWCWorldModel.build_session_vector()` (exact 30-dim); 14A approximation as fallback
- [x] **B2a**: `PreferenceModel.save(path)` + `PreferenceModel.load(path)` added to knapsack_personalized.py
- [x] **B2b**: `ewc_model_path` + `preference_model_path` config fields in config.py (env: VAPI_EWC_MODEL_PATH, VAPI_PREF_MODEL_PATH; defaults `~/.vapi/`)
- [x] **B2c**: Load-on-init in `_init_hardware()` Phase 13 block; save-on-shutdown in `_shutdown_cleanup()`
- [x] **B5**: `_keccak256()` in swarm_zk_aggregator.py → `from eth_hash.auto import keccak` (no SHA-256 fallback)
- [x] 5 new tests: TestFeatureFrameToVector + TestEWCSessionVecFidelity + TestModelPersistence(×2) + TestKeccakNeverFallsBack
- [x] `MEMORY.md` + `tasks/todo.md` + `tasks/lessons.md` updated

### Test Count
- Hardhat: 225 (unchanged)
- Bridge pytest: 341 + 5 = **346** (+ 6 skipped)
- HW tests: 72 (unchanged)
- **Total passing: 643**

---

## Phase 14C: Real ZK Circuit — IN PROGRESS

> Goal: Replace mock 256-byte proof with real Groth16/BN254 proof from Circom circuit.
> Wire-format invariant holds (256 bytes). No change to PoAC 228-byte record.
> Tests: 643 → 643 (no new tests yet; Artifact 4 will add them).

### Checklist

- [x] **Artifact 1**: `contracts/circuits/TeamProof.circom` — Groth16 circuit (MAX_MEMBERS=6, ~3700 constraints, Poseidon Merkle tree height-3, 5 constraint groups)
- [x] **Artifact 1**: `contracts/circuits/setup.sh` — 7-step trusted setup script
- [x] **Artifact 1**: `contracts/circuits/package.json` — circomlib + snarkjs deps
- [x] **Artifact 2**: `bridge/zk_prover.py` — ZKProver class (generate_proof + verify_proof); 256B ABI-packed proof; mock fallback when artifacts absent; ZK_ARTIFACTS_AVAILABLE bool
- [x] **Artifact 2**: `bridge/zk_artifacts/compute_inputs.js` — circomlibjs Poseidon helper (computes poseidonMerkleRoot + nullifierHash matching circuit exactly)
- [x] **Artifact 2**: `bridge/zk_artifacts/package.json` — circomlibjs ^0.1.7
- [x] **Artifact 2**: `bridge/swarm_zk_aggregator.py` — submit_team_proof_zk() routes through ZKProver when artifacts present
- [x] **Artifact 3**: Update `contracts/contracts/TeamProofAggregatorZK.sol` — add poseidonMerkleRoot + epoch params to submitTeamProofZK/_verifyZKProof; wire to auto-generated TeamProofVerifier.sol
- [x] **Artifact 3**: `contracts/contracts/ITeamProofVerifier.sol` — NEW interface (verifyProof with 4 public inputs)
- [x] **Artifact 3**: `contracts/contracts/TeamProofAggregatorZKTestable.sol` — updated _verifyZKProof signature (6 params); 225 Hardhat passing
- [x] **Artifact 4**: `contracts/contracts/test/MockTeamProofVerifier.sol` — configurable mock ITeamProofVerifier
- [x] **Artifact 4**: TeamProofAggregatorZK.test.js describe "5. Phase 14C" — 7 new Hardhat tests (232 total)
- [x] **Artifact 4**: `bridge/tests/test_zk_prover.py` — NEW; 10 Python tests (356 total)
- [x] **Artifact 5**: `tasks/lessons.md` updated + MEMORY.md Phase 14C COMPLETE

### Setup (operator, one-time)
```bash
# 1. Build the circuit
cd contracts/circuits && npm install && bash setup.sh
# 2. Copy artifacts to bridge
cp TeamProof_js/TeamProof.wasm   bridge/zk_artifacts/
cp TeamProof_final.zkey          bridge/zk_artifacts/   # KEEP SECRET
cp verification_key.json         bridge/zk_artifacts/
# 3. Install circomlibjs for Python-side Poseidon computation
cd bridge/zk_artifacts && npm install
# 4. Set env vars
export VAPI_ZK_WASM_PATH=bridge/zk_artifacts/TeamProof.wasm
export VAPI_ZK_ZKEY_PATH=bridge/zk_artifacts/TeamProof_final.zkey
export VAPI_ZK_VKEY_PATH=bridge/zk_artifacts/verification_key.json
```

### Key technical decisions
- MOCK_PROOF_SIZE = 256 aligns with Groth16 uncompressed: A(64B) + B(128B) + C(64B)
- 256B encoding is ABI-compatible: `abi.decode(proof, (uint256[2], uint256[2][2], uint256[2]))`
- poseidonMerkleRoot (Poseidon tree) is a new public input separate from on-chain keccak256 root
- circomlibjs (JS) computes Poseidon hashes matching circomlib Circom templates exactly
- nullifier = Poseidon(poseidonMerkleRoot, identitySecrets[0], epoch) — batch+leader+time binding
- ZK_ARTIFACTS_AVAILABLE = False when WASM/zkey/circomlibjs not installed → mock proof used

---

---

## Phase 15: ZK Trusted Setup + Deployment Tooling — COMPLETE

> Goal: Make ZK_ARTIFACTS_AVAILABLE=True achievable by any developer with 30 minutes.
> Add the missing smoke test, deployment script, real-path integration tests, and docs sync.
> Zero new Solidity. Zero new Python modules. Pure tooling + hardening.

### Checklist

- [x] **15A**: `contracts/circuits/scripts/test_proof.js` — smoke test referenced by setup.sh (was missing); uses circomlibjs + snarkjs npm API; exits 0/1; called by `npm run test-proof`
- [x] **15B**: `contracts/scripts/deploy-verifier.js` — deploy snarkjs-generated TeamProofVerifier.sol; wire to TeamProofAggregatorZK via setTeamProofVerifier(); write deployment JSON; append ZK vars to .env.testnet
- [x] **15C**: `bridge/tests/test_zk_prover_real.py` — 5 tests skipped unless ZK_ARTIFACTS_AVAILABLE=True; roundtrip, tamper, epoch binding, root uniqueness
- [x] **15D**: `paper/vapi-whitepaper.md` — test counts updated (450→660, 493→660, ~90 files→~149 files)
- [x] **15D**: `README.md` — "ZK Proof Setup (Optional)" section added (circuit build, artifact copy, verifier deploy, env vars, test verification)
- [x] **15D**: `tasks/todo.md` + `tasks/lessons.md` + `MEMORY.md` updated

### New files
| File | Purpose |
|------|---------|
| `contracts/circuits/scripts/test_proof.js` | Post-setup smoke test: build→prove→verify cycle |
| `contracts/scripts/deploy-verifier.js` | Deploy TeamProofVerifier + wire to aggregator |
| `bridge/tests/test_zk_prover_real.py` | Skip-unless-artifacts integration tests (5 tests) |

### Test Count
- Hardhat: 232 (unchanged — no new Solidity tests)
- Bridge pytest: 356 + 0 active = **356** (5 new tests are skip-unless-artifacts)
- HW tests: 72 (unchanged)
- **Total active: 660**
- **Total including skip-unless: 665** (5 new real-path tests dormant until setup.sh run)

### Key design decisions
- `test_proof.js` uses snarkjs npm module directly (not subprocess) — cleaner API, no PATH issues
- `deploy-verifier.js` validates TeamProofVerifier.sol presence before deploying — fails fast with clear error if setup.sh was not run
- `test_zk_prover_real.py` stubs web3/eth_account at module level (same pattern as all other bridge tests) — no special import magic needed
- 5 real-path tests are `@unittest.skipUnless(ZK_ARTIFACTS_AVAILABLE, ...)` — they appear in pytest output as SKIPPED with explanation, not as missing/unknown

---

---

## Phase 16A: Self-Verifying Pipeline Attestation Tests — COMPLETE

> Goal: Replace all 6 pytest.skip() stubs in test_e2e_simulation.py with real
> IsolatedAsyncioTestCase async tests + add 3 novel "Self-Verifying Pipeline
> Attestation" tests. Zero code changes to contracts or bridge logic.

### Checklist

- [x] `bridge/tests/test_pipeline_attestation_utils.py` — NEW; VAPITestVector,
      make_test_vector(), make_chained_vectors(), compute_merkle_root(),
      ContractHarness (create, _deploy, register_device, submit_record,
      read_inference, post_test_bounty, create_team, submit_team_proof)
- [x] `bridge/tests/test_e2e_simulation.py` — MODIFIED; TestE2ESimulation
      (IsolatedAsyncioTestCase, 6 real async tests replacing stubs);
      TestSelfVerifyingPipelineAttestation (3 novel self-verifying tests);
      TestE2EPipelineLogic (5 pure-Python tests, unchanged)
- [x] Regression verification: 356 active pytest passing, 14 skipped (9 E2E + 5 ZK)
- [x] `tasks/todo.md` + `tasks/lessons.md` + `MEMORY.md` updated

### New files
| File | Purpose |
|------|---------|
| `bridge/tests/test_pipeline_attestation_utils.py` | Shared harness: VAPITestVector, ContractHarness, compute_merkle_root |

### Test Count
- Hardhat:      232 (unchanged)
- Bridge pytest: 356 active + **9 new E2E** (skip-unless HARDHAT_RPC_URL) = **365 when Hardhat running**
- Bridge skipped (no Hardhat): 9 + 5 ZK = **14 skipped in CI**
- HW tests: 72 (unchanged)
- **CI active total: 356 (unchanged)**
- **With Hardhat node: 356 + 9 = 365 active, 5 skipped**

### Key design
- Uses `PoACVerifierTestable` — overrides `_requireValidSignature()` as no-op;
  no IoTeX P256 precompile needed on Hardhat
- `ContractHarness.create()` deploys all 6 contracts in order per test (fresh state)
- `compute_merkle_root()` is an exact Python replica of Solidity `_computeMerkleRoot`;
  cross-validated by test_python_solidity_merkle_cross_validation (TX reverts if mismatch)
- Self-verifying novel tests use the chain as oracle:
  - Test 7: `recordInferences[hash] == inference` — chain stores PITL byte
  - Test 8: Python root == Solidity root — cross-validated by TX revert behavior
  - Test 9: Chain accepts sequential records → genealogy sealed immutably
- `sys.path` fix: test_e2e_simulation.py inserts both `bridge/` AND `bridge/tests/`
  so `test_pipeline_attestation_utils` is always importable

---

## Backlog (Phase 16B+)

- Phase 16B+: ioID + Quicksilver selective IoTeX synergy (defer W3bstream + Realms)
- Remaining GAP-3: `attestationEnforced=true` on testnet (requires real P256 cert vector)
- Remaining GAP-4: Bridge wallet → Gnosis Safe 2-of-3 (needs deployed contracts)
- Remaining GAP-5: live E2E tests (requires HARDHAT_RPC_URL + deployed addresses)
- Biometric model training: real labeled session data for production thresholds
- Multi-party ZK ceremony: replace single-contributor Phase 2 setup with MPC for mainnet
