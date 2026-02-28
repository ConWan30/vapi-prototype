# VAPI Lessons Learned

## Phase 15: ZK Trusted Setup + Deployment Tooling

### snarkjs npm API vs subprocess
Using `require("snarkjs")` and `snarkjs.groth16.fullProve()` / `snarkjs.groth16.verify()`
directly in Node.js scripts (test_proof.js, compute_inputs.js) is cleaner than calling
snarkjs via subprocess — avoids PATH resolution, captures errors properly, and works
the same in both `node script.js` and `npx hardhat` contexts.

### Artifacts absent = skip, not fail
Real-path ZK tests must be `@unittest.skipUnless(ZK_ARTIFACTS_AVAILABLE, ...)`, NOT
conditional logic inside the test body. `skipUnless` produces a SKIPPED entry in pytest
output with a clear explanation, which is informative and CI-friendly. Conditional logic
inside tests can produce misleading PASSED results (test body never executes).

### deploy-verifier.js: fail fast on missing TeamProofVerifier.sol
The auto-generated `TeamProofVerifier.sol` only exists after `bash setup.sh`. Without it,
`getContractFactory("TeamProofVerifier")` throws a cryptic Hardhat error. Always check
`fs.existsSync(verifierSolPath)` at the top of the deploy script and emit a clear
instruction pointing to setup.sh before any Hardhat API calls.

### test_proof.js must be in circuits/scripts/ not circuits/
setup.sh calls `node scripts/test_proof.js` from within `contracts/circuits/`. The script
must live at `contracts/circuits/scripts/test_proof.js`, not at `contracts/circuits/test_proof.js`.
The `package.json` `test-proof` npm script (`npm run test-proof`) references the same path.

### Poseidon BigInt handling in circomlibjs
circomlibjs Poseidon inputs must be BigInt or numbers — plain JS integers work for small
values, but identity secrets must be passed as BigInt (e.g., `999n` not `999`) to avoid
silent modular reduction errors when secrets exceed 2^53. `F.toObject()` returns a BigInt
that is safe to pass as a string to snarkjs circuit inputs.

### Whitepaper test counts go stale quickly
The abstract, §7.1 overview table, and any tables in §8 each contain the test count
independently. A single phase can invalidate 3+ separate references. Grep for all
occurrences of the previous count before updating: `grep -n "450\|493\|643\|660"`.

## Phase 9: Hardware Signing Bridge

### Python import path for pytest
Tests in `bridge/tests/` need two path entries:
```python
sys.path.insert(0, str(Path(__file__).parents[1]))        # bridge/
sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))  # controller/
```
Using `parents[3]` goes too far up — results in `ModuleNotFoundError`.
Match the pattern in `bridge/tests/test_hid_xinput_oracle.py` exactly.

### Prehashed location in cryptography 3.13+
`Prehashed` moved from `cryptography.hazmat.primitives.hashes` to
`cryptography.hazmat.primitives.asymmetric.utils`. The old location raises
`AttributeError`. Use try/except import chain to support both versions:
```python
try:
    from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
    prehashed_algo = Prehashed(hashes.SHA256())
except ImportError:
    try:
        prehashed_algo = hashes.Prehashed(hashes.SHA256())
    except AttributeError:
        prehashed_algo = None
```

### DER/raw-RS round-trip is lossless
PoACEngine.generate() calls `private_key.sign()` and expects DER back.
It then calls `decode_dss_signature()` to extract r, s for the 64-byte record field.
`_HardwareKeyProxy` must return DER (not raw r||s) from its `sign()` method.
The encode/decode round-trip is bit-identical for all backends.

### YubiKey PIV sign() pre-hashes
YubiKey PIV `sign()` takes a pre-computed SHA-256 digest, NOT the raw body.
`hashlib.sha256(body).digest()` must be called before passing to PIV.
Same for ATECC608A `atcab_sign()` — 32-byte digest input, not raw bytes.

### `_devices` mapping is private in DeviceRegistry
`DeviceRegistry._devices` is `private`. Inheriting contracts (TieredDeviceRegistry)
cannot access it directly. Use `this.getDeviceInfo()` instead — it is public and
accessible via `this.` from within the inheriting contract, consistent with
the existing `this.isDeviceActive()` pattern.

## Phase 8: Physical Input Trust Layer

### CHEAT_CODES dict must include 0x28/0x29/0x2A
Phase 8 added three PITL codes. Any refactor of `dualshock_integration.py` must
preserve these. Regression check: `assert all(c in CHEAT_CODES for c in (0x28, 0x29, 0x2A))`.

### pydualsense stick range
`pydualsense` remaps sticks from [0,255] hardware range to [-32768,32767].
Gyro: raw value / 1000 = rad/s. Document this in any sensor commitment schema
to avoid confusion when comparing to Pebble's IMU format.

## Phase 10: Full Attestation Enforcement + Manufacturer Registry

### V2 vs V1 registration functions — keep both
Old `registerAttested` / `registerAttestedWithCert` call `_validateAttestation()` which
still reverts `AttestationValidatorNotImplemented` when `attestationEnforced=true`.
New V2 functions call `_validateAttestationV2()` which actually calls the precompile.
Both families coexist — backward-compatible by design. Block 7 test asserts the old
path still reverts; block 16 tests cover the new path.

### IoTeX P256 precompile call signature
Precompile at `0x0100` takes exactly 160 bytes:
    `msgHash(32) || r(32) || s(32) || manuf_x(32) || manuf_y(32)`
Returns 32-byte `uint256`: 1 = valid, 0 = invalid. Check both `ok` flag AND return value.
Use `abi.encodePacked(msgHash, _proof, mk.pubkeyX, mk.pubkeyY)` for assembly-free encoding.

### ManufacturerKey struct with string field requires viaIR=true
The `ManufacturerKey` struct contains a `string` field. Solidity storage with a dynamic
string in a mapping may trigger "stack too deep" on older compilation paths.
`viaIR: true` is already set in `hardhat.config.js` for PoACVerifier — ensure it covers
TieredDeviceRegistry as well (it does since `viaIR` is global in hardhat config).

### Mock precompile bytecode for Hardhat testing
- Accept (returns 1): `"0x600160005260206000f3"` — PUSH1 0x01, MSTORE at 0, RETURN 32 bytes
- Reject (returns 0): `"0x600060005260206000f3"` — PUSH1 0x00, MSTORE at 0, RETURN 32 bytes
Use `hardhat_setCode` to inject before tests requiring precompile behaviour.
The outer `beforeEach` already injects accept bytecode; block 16 inner `beforeEach`
re-injects it (idempotent) before setting manufacturer key.

### OwnableUnauthorizedAccount vs "Ownable: caller is not the owner"
This project uses OpenZeppelin v5 which emits a custom error `OwnableUnauthorizedAccount`.
Always use `revertedWithCustomError(registry, "OwnableUnauthorizedAccount")` — NOT
`revertedWith("Ownable: caller is not the owner")` which is the OZ v4 string revert.

### Sensor commitment schema v2 byte layout
Format string `">hhhhBBBBffffffIQ"` = 48 bytes:
- `h x4` = 8 bytes: left_stick_x, left_stick_y, right_stick_x, right_stick_y (int16)
- `B x4` = 4 bytes: l2_trigger, r2_trigger, l2_effect_mode, r2_effect_mode (uint8)
- `f x6` = 24 bytes: accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z (float32)
- `I`    = 4 bytes: buttons (uint32)
- `Q`    = 8 bytes: timestamp_ms (uint64)
Total = 48 bytes. SHA-256 output = 32 bytes. 228-byte wire format UNCHANGED.

### _l2_effect_mode vs l2_effect_mode naming convention
In `dualshock_integration.py` (class instance vars): use `self._l2_effect_mode` (private).
In `dualshock_emulator.py` (InputSnapshot dataclass fields): use `snapshot.l2_effect_mode`
(no underscore prefix since dataclass fields are public by convention).

## Phase 11: Mainnet Readiness

### Tier enforcement requires virtual function on base class
BountyMarket stores registry as `DeviceRegistry public deviceRegistry`. `canClaimBounty`
was only on `TieredDeviceRegistry`. To call it through the base reference, add
`function canClaimBounty(bytes32) public virtual view returns (bool)` to `DeviceRegistry`
returning `_devices[_deviceId].active` as default (all active base-registry devices are
eligible). TieredDeviceRegistry's version changes visibility to `public view override`.
Solidity virtual dispatch ensures TieredDeviceRegistry's logic runs when called through
the `DeviceRegistry` pointer.

### No BountyMarket.test.js existed before Phase 11
BountyMarket had zero Hardhat tests through Phase 10. Phase 11 created it from scratch
with 8 tests. It uses `TieredDeviceRegistry` (not plain `DeviceRegistry`) to exercise
Emulated/Standard/Attested tiers. Device registration: `registerTieredDevice(pubkey, 0)`
for Emulated, `registerTieredDevice(pubkey, 1)` for Standard, `registerAttested(pubkey, 64byteProof)` for Attested.

### PoAC flow for BountyMarket tests
To mark a record hash as verified for `submitEvidence` to accept:
1. Call `verifier.verifyPoAC(deviceId, body, sig)` — stores `sha256(body)` in submittedHashes
2. Record hash = `ethers.sha256(body)`
3. Pass same hash to `submitEvidence`. PoACVerifierTestable bypasses sig check.
Requires `registry.setReputationUpdater(verifier.address, true)` so verifier can
update device reputation without reverting.

### setApprovedManufacturer is dead state — deprecated in Phase 11
`approvedManufacturers(address)` mapping is set by `setApprovedManufacturer` but never
read by any V2 attestation path. Added @deprecated NatDoc in Phase 11 (non-breaking).
V2 path uses `manufacturerKeys` mapping instead.

## Phase 12: Close All Mainnet Blockers

### PoACVerifier side-channel schema storage — 228-byte wire format is immutable
The 228-byte PoAC wire format (164B body + 64B sig) cannot be changed.
Schema version is stored as a side-channel in PoACVerifier state via a new
`verifyPoACWithSchema(deviceId, rawBody, sig, schemaVersion)` function.
Old `verifyPoAC` callers work unchanged. New callers tag records with schema version
(1=v1 environmental, 2=v2 kinematic). `recordHasSchema[hash]` = false for legacy records.

### Inference result persistence — one SSTORE in _verifyInternal
`_parseBody()` assembly already extracts `inferenceResult` into the struct.
Adding `recordInferences[recordHash] = f.inferenceResult` after `chain.initialized = true`
(step 10 of _verifyInternal) costs one SSTORE per verification but requires zero
changes to callers. The public mapping auto-generates a getter for `TeamProofAggregator`.

### TeamProofAggregator allClean is always true for stored proofs
The `allClean` field documents that all stored proofs were cheat-verified.
If any record has a cheat code, the TX REVERTS before storage — so stored proofs
are tautologically all-clean. Set `allClean = true` at init; never write false.

### ProgressAttestation scoped block for stack-too-deep
The schema check in `attestProgress` (5 parameters) must be inside a scoped block `{}`
to avoid "stack too deep" Solidity errors. The scoped block limits variable lifetime.
Pattern: `{ (uint8 baselineSchema, bool baselineHasSchema) = ... ; ... }`

### asyncio.run() leaves no current event loop in Python 3.12+
`asyncio.run()` calls `events.set_event_loop(None)` on teardown. Subsequent calls to
`asyncio.get_event_loop()` raise RuntimeError in Python 3.13+.
Fix: Add `conftest.py` with `autouse=True, scope="function"` fixture that creates a fresh
event loop via `asyncio.new_event_loop(); asyncio.set_event_loop(loop)` before each test.
This prevents IsolatedAsyncioTestCase and asyncio.run() from breaking other test files.

### Mocking ChainClient._send_tx prevents registerAttestedV2 from being called directly
`_send_tx(tx_func, *args)` receives the contract function BUILDER as `tx_func`, and calls
`tx_func(*args)` internally. If `_send_tx` is mocked with `AsyncMock`, the builder
is never called. Assert on `_send_tx.call_args[0][0] is registry.functions.registerAttestedV2`
instead of `registry.functions.registerAttestedV2.called`.

### Module-level pytestmark affects ALL tests in the file
`pytestmark = pytest.mark.skipif(...)` at module level marks every test class and function
in that module. To skip only specific classes, use a class-level decorator:
`@pytest.mark.skipif(not ENV_VAR, reason="...")` on the class definition.

## General

### Windows terminal: avoid Unicode arrows
Windows terminal (cp1252) cannot encode Unicode characters like arrows (→).
Use ASCII alternatives in any output printed to Windows console.

### Hardhat viaIR=true for PoACVerifier
`PoACVerifier.sol` requires `viaIR: true` in `hardhat.config.js` to avoid
"Stack too deep" compilation errors. Do not remove this flag.

### Record hash is SHA-256, not keccak256
The PoAC record hash used in `submittedHashes` is SHA-256 (matching firmware).
keccak256 is used for deviceId (keccak256 of pubkey). These are different and
must not be swapped.

## Phase 13: Agent Capability Expansion

### external vs public virtual for Solidity inheritance
`external` functions CANNOT be called by derived contracts without `this.function()`.
When a child contract needs to call a parent's function internally (to preserve msg.sender
and avoid an extra external call), change the parent function from `external` to `public virtual`.
This is backward-compatible: all external callers continue to work.
Applied: `TeamProofAggregator.submitTeamProof` changed to `public virtual` for ZK inheritance.

### Testable contract overrides bypass base mock logic
`TeamProofAggregatorZKTestable._verifyZKProof` returns `mockZKResult` regardless of proof length.
Tests for proof-length validation must use the BASE contract (`TeamProofAggregatorZK`),
not the testable override. Deploy the base contract inline in tests that need to exercise
the base mock's specific logic (e.g., `proof.length == MOCK_PROOF_SIZE`).

### Hardhat DeviceAlreadyRegistered in loop tests
Tests that loop `registerDevice()` multiple times for the same pubkey fail after iteration 1.
Fix: Move `registerDevice()` outside the loop; use incrementing monotonic counters inside the
loop to avoid `CounterNotMonotonic` errors from PoACVerifier's replay prevention.

### pytest sys.path for bridge/ root-level modules
`bridge/swarm_zk_aggregator.py` lives in the bridge root (NOT in vapi_bridge/).
Tests import it via `sys.path.insert(0, str(Path(__file__).parents[1]))` which adds bridge/
to the path. This is the same as test_biometric_fusion.py's parent[2]/controller path.
Root-level bridge modules: use `parents[1]` (bridge/ root). Sub-packages: use `parents[1]/vapi_bridge`.

### Pure NumPy EWC backprop is ~40 lines for 3-layer MLP
Manual backprop for a 30→64→32→8 ReLU MLP requires:
forward: x → ReLU(x@W1+b1) → ReLU(h1@W2+b2) → h2@W3+b3
backward: MSE loss → chain rule through W3/b3 → ReLU mask through W2/b2 → W1/b1
EWC penalty: sum_k fisher[k] * (weight[k] - prev_weight[k])^2 * ewc_lambda
The EWC gradient is additive to the MSE gradient: no separate pass needed.

### keccak256 not in Python stdlib
Python `hashlib` includes SHA-3 (sha3_256) but NOT keccak256 (different padding).
For bridge-side Merkle root computation matching on-chain keccak256:
try pysha3 → try pycryptodome → fall back to SHA-256 (acceptable for Phase 13 mock tests).
Phase 14 real ZK path will require py_ecc or snarkjs subprocess for proper BN254 curve ops.

### Gate G6 was already closed
The plan assumed BountyMarket.sol needed zone validation added. Reading lines 357-360
showed it was already there: `if (_zoneLatMin >= _zoneLatMax || _zoneLonMin >= _zoneLonMax)`.
Always read the actual file before assuming a gate fix is needed — don't rely solely on
the assessment's gap analysis. Update gate-check.md accordingly.

---

## Phase 14A Lessons

### Pipeline wiring: try/except guard for optional enhancement modules
Phase 13 modules (E1/E2/E4) are in controller/ which is always added to sys.path in
`_init_hardware()`. Wrapping the import in try/except gives graceful fallback to legacy
hashes — every existing test continues to pass even if numpy is not installed.
Pattern: try import → set self._xxx = Instance() → except Exception → log.warning.

### Lazy import of BiometricFeatureExtractor inside _session_loop
`BiometricFeatureExtractor.extract()` is a static method imported inside the loop body
(not at module or class level). This avoids circular-import issues and keeps the import
localized to the hot path. The class ref is not stored on self since it has no instance state.

### _build_ewc_session_vec: 30-dim approximation from InputSnapshot
EWCWorldModel.build_session_vector() requires objects with to_vector() (AntiCheatClassifier
FeatureFrames). InputSnapshot lacks to_vector(). Phase 14A approximation: build a 30-dim
vector from 12 sensor attribute means + 12 stds + 6 supplementary (buttons, battery,
mode means, frame count, accel mag). Phase 14B should access classifier's internal features.

### Batcher batch routing: schema_version check per-record in batch
The batch path (>1 records) called verify_batch() regardless of schema_version.
Fix: `any(getattr(r, "schema_version", 0) > 0 for r in records)` → split into individual
verify_poac() calls. tx_hash holds the last individual tx hash (acceptable pragmatic solution).

### test_pipeline_integration.py: stub web3 before importing vapi_bridge.batcher
batcher.py imports chain.py which imports `from web3 import AsyncWeb3`. In the test env
web3 is not installed. Pattern from test_chain_v2_methods.py: insert fake module stubs into
sys.modules BEFORE any vapi_bridge imports. Must be at module level (not inside setUp).
Also stub web3.exceptions.ContractLogicError + TransactionNotFound as exception types.

### BiometricFusionClassifier class ref must be stored in setUp for use across test methods
Importing a class inside setUp() makes it local to that method — other test methods cannot
access it by name. Fix: `self.BiometricFusionClassifier = BiometricFusionClassifier` in setUp.

---

## Phase 14B Lessons

### FeatureFrame.to_vector() enables EWCWorldModel.build_session_vector() to work
`EWCWorldModel.build_session_vector(frames)` calls `f.to_vector()` on each frame.
`FeatureFrame` (dualshock_emulator.py) had no such method — adding it (30-field ordered
numpy array) is the correct fix. The order must exactly match the 30 INPUT_DIM fields:
`stick_lx..jerk_r`. Any ordering mistake changes the EWC embedding space silently.

### AntiCheatClassifier.window is the correct source for EWC session vectors
After `_classify(frames)` completes (which calls `reset()` then `extract_features()` per
frame), `self._classifier.window` contains FeatureFrame objects for the current session
window. Pass `list(self._classifier.window)` to `EWCWorldModel.build_session_vector()`.
The fallback (14A InputSnapshot approximation) remains valid when the classifier window
is empty (e.g., first session before any classify() call).

### eth-hash.auto.keccak replaces 3-step import chain cleanly
The old `_keccak256()` had pysha3 → pycryptodome → SHA-256 fallback. Since
`eth-hash[pycryptodome]>=0.5.0` is already in requirements.txt, the SHA-256 fallback
was dead code but could silently produce wrong hashes if pycryptodome import failed.
`from eth_hash.auto import keccak` is simpler: raises ImportError loudly if unavailable
(never reached in practice since eth-hash is a hard dependency of web3/eth-account).

### PreferenceModel.save/load uses 40-byte binary file
`serialize_weights()` returns 5 × float64 = 40 bytes. `save(path)` writes these bytes
directly; `load(path)` calls `from_bytes(Path(path).read_bytes())`. No JSON overhead —
binary roundtrip is byte-identical. Tests validate with `assertEqual(b1, b2)`.

### EWCWorldModel.save/load uses JSON with hex-encoded weight matrices
Weights (float32 arrays) are serialized as `.tobytes().hex()` strings in JSON.
`load()` reconstructs via `np.frombuffer(bytes.fromhex(hex_str), dtype=np.float32)`.
The hash is deterministic after roundtrip because SHA-256 is computed from bytes, not
from the JSON representation itself.

### Config persistence paths use Path.home() / ".vapi"
Consistent with `dualshock_key_dir` pattern. Env vars `VAPI_EWC_MODEL_PATH` and
`VAPI_PREF_MODEL_PATH` allow override without code change. Default dir is `~/.vapi/`
(same as signing keys) — both model files and key files live in the same user directory.

---

## Phase 14C Lessons

### Poseidon tree root is a separate public input from the on-chain keccak256 root
The circuit uses Poseidon(leaf0..leaf7) for its commitment tree — this is a BN254
field element (uint256), not bytes32. The on-chain keccak256 Merkle root (bytes32)
remains for parent cheat detection. Both are passed as separate parameters. The circuit
binds to poseidonMerkleRoot; the contract binds to the keccak256 root. They represent
the same records through different hash algorithms.

### circomlibjs in Node.js is the only correct way to compute Poseidon for circuit inputs
Python Poseidon reimplementations risk parameter mismatch with circomlib's specific BN254
constants (nRoundsF=8, nRoundsP=57, seeded MDS matrix). The safest approach is to call
circomlibjs (the JS reference implementation) via Node.js subprocess. store
compute_inputs.js in bridge/zk_artifacts/ alongside its own package.json/node_modules
so module resolution is self-contained.

### 256B Groth16 proof is ABI-decode compatible with (uint256[2], uint256[2][2], uint256[2])
Fixed-size ABI types are packed sequentially with no offset headers. A:64B + B:128B +
C:64B = 256B exactly, and `abi.decode(proofMem, (uint256[2], uint256[2][2], uint256[2]))`
reads them directly. Python _encode_proof stores fields in natural proof.json order.
The snarkjs-generated verifier internally applies the G2 coordinate swap when building
the Pairing.G2Point, so no pre-swap is needed in Python encoding.

### Solidity function overloading preserves backward compatibility without version bumps
Adding a 7-param submitTeamProofZK() alongside the original 5-param version lets all
Phase 13 tests pass unchanged (they use the 5-param selector). ethers.js calls the
correct overload via `contract["functionName(types)"]()` syntax. The 5-param version
forwards to an internal helper with poseidonMerkleRoot=0, epoch=0 — these zeros trigger
the mock path in _verifyZKProof even when teamProofVerifier is set.

### Virtual ZK verifier override signature must be updated in all subclasses
Changing _verifyZKProof from 4 to 6 params requires updating TeamProofAggregatorZKTestable
immediately. Forgetting this causes a compile error (not a runtime error). The testable
override ignores all params and returns mockZKResult — safe to add ignored params.

### ZK artifacts should be absent in test environments
test_zk_prover.py explicitly asserts ZK_ARTIFACTS_AVAILABLE == False. This prevents
tests from accidentally passing only when circom/snarkjs happen to be installed. The
test documents the expected pre-setup state and fails loudly if env vars are accidentally
set. The mock fallback path is what tests exercise.

### bytes calldata → memory copy required before abi.decode in Solidity
`abi.decode` requires `bytes memory`. Passing `bytes calldata proof` directly may fail
or produce unexpected behavior depending on compiler version. Explicit copy via
`bytes memory proofMem = proof;` before decoding is safe and compiler-version-agnostic.

---

## Phase 16A: Self-Verifying Pipeline Attestation Tests

### sys.path must include bridge/tests/ for cross-module imports
When one test file (`test_e2e_simulation.py`) imports from another file in the
same directory (`test_pipeline_attestation_utils.py`), pytest does NOT
automatically add `tests/` to `sys.path`. The importing file must explicitly:
```python
sys.path.insert(0, str(Path(__file__).parents[0]))   # bridge/tests/
sys.path.insert(0, str(Path(__file__).parents[1]))   # bridge/
```
Without the `parents[0]` insert, `ModuleNotFoundError: No module named 'test_pipeline_attestation_utils'`.

### web3 import must be lazy (inside ContractHarness.create) not at module level
If `from web3 import AsyncWeb3` is at module level in test_pipeline_attestation_utils.py,
the entire module fails to import when web3 is not installed. Putting the import
inside `ContractHarness.create()` makes the module always importable — the 9 E2E
tests that need web3 are already guarded by `@pytest.mark.skipif(not HARDHAT_RPC_URL)`.
This keeps CI import-safe while keeping the import exactly where it is used.

### PoACEngine prints to stdout on keypair generation — acceptable in tests
Each `make_test_vector()` call prints "[CRYPTO] ECDSA-P256 keypair generated".
Tests with multiple vectors (3 devices) will print 3 times. This is cosmetic and
acceptable for E2E tests. Do not suppress it — the output confirms real crypto is running.

### Solidity _computeMerkleRoot does NOT re-sort at each level
The Solidity `_computeMerkleRoot` sorts leaves ONCE at the start, then reduces
pairs in the sorted order: `keccak256(sorted[left] + sorted[right])`.
It does NOT re-sort at each reduction level. The Python replica must match:
sort once → reduce left+right pairs as-is → promote odd leaf unchanged.
Any deviation (e.g., sorting pairs at each level) causes `InvalidMerkleRoot` revert.

### record_hash vs chain_head_hash are different SHA-256 inputs
- `record_hash = SHA-256(raw_body)` [164B body only] — used as on-chain key in
  `submittedHashes`, `recordInferences`, `submitTeamProof`, `submitEvidence`
- `chain_head = SHA-256(raw_body + signature)` [228B full record] — used by
  PoACEngine to update `chain_head` (prev_poac_hash for next record)
Test 9 (genealogy) asserts `v2.raw_body[0:32] == SHA-256(v1.raw_body + v1.signature)`.
Do NOT assert `v2.raw_body[0:32] == SHA-256(v1.raw_body)` — that is incorrect.

### IsolatedAsyncioTestCase + pytest.mark.skipif class decorator works correctly
`@pytest.mark.skipif(not HARDHAT_RPC_URL, reason=...)` on an `IsolatedAsyncioTestCase`
class causes pytest to skip all async test methods in that class without running
`asyncSetUp`. No need for per-test skip decorators. Confirmed working on Python 3.13
with pytest-asyncio in STRICT mode.

### ContractHarness deploys 6 contracts per test — acceptable for Hardhat automine
Hardhat automines each transaction instantly. Six deployments + one `setReputationUpdater`
call = 7 transactions ≈ < 1 second on localhost. No need to share harness across tests;
fresh deployment per test eliminates all state pollution between tests.

### make_chained_vectors() required for genealogy test — make_test_vector() creates new engine
`make_test_vector()` creates a fresh `PoACEngine` per call. Two separate calls produce
two records for TWO DIFFERENT devices. For the genealogy test (two records from the
same device, prev_hash linked), use `make_chained_vectors([0x20, 0x21])` which keeps
a single engine and generates sequential records with correct `prev_poac_hash` links.
