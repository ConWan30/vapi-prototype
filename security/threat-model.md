# VAPI Threat Model
**Version:** Phase 6 (Security Hardening)
**Date:** 2026-02-21
**Author:** VAPI Security Architecture

---

## 1. System Overview

VAPI (Verified Autonomous Physical Intelligence) is a trustless DePIN protocol where
autonomous devices generate cryptographically signed Proof-of-Autonomous-Cognition (PoAC)
records that are verified on-chain. The threat surface spans:

| Layer | Component | Trust Level |
|---|---|---|
| Firmware | Pebble Tracker / DualShock agent | Semi-trusted (hardware root) |
| Bridge | Python relay (chain.py) | Untrusted relay |
| Contracts | IoTeX EVM (6 contracts) | Trustless |
| IoTeX Chain | IoTeX testnet/mainnet | Trusted infrastructure |

**Critical invariants that must never be violated:**

1. A PoAC record body can only produce rewards/ratings once (anti-replay)
2. Each device_id maps 1-to-1 to a hardware key, with deposit staked (anti-Sybil)
3. Skill ratings reflect long-run play quality, not burst submission (anti-manipulation)
4. The chain of PoAC records per device is monotonically ordered and unbroken

---

## 2. Asset Inventory

| Asset | Value | Confidentiality | Integrity |
|---|---|---|---|
| Device private key (ECDSA-P256) | High | Critical | Critical |
| IOTX deposit in DeviceRegistry | Medium | N/A | High |
| Bounty rewards in BountyMarket | High | N/A | High |
| SkillOracle rating | Medium | Low | High |
| ProgressAttestation record | Low | Low | Medium |
| Bridge private key | High | Critical | N/A |

---

## 3. Threat Actor Profiles

| Actor | Capability | Motivation |
|---|---|---|
| **Malicious bridge operator** | Full control of relay; can inject records, replay, front-run | Steal bounty rewards or inflate ratings |
| **Replay attacker** | Observes verified PoAC records on-chain; controls bridge wallet | Re-submit verified records to claim rewards multiple times |
| **Sybil registrant** | Can register unlimited device identities | Create bot farm to farm rewards or manipulate reputation metrics |
| **Rating manipulator** | Has N verified historical PoAC records for a device | Submit all N in one block to spike SkillOracle rating for tournament eligibility |
| **Compromised device** | Physical access to Pebble Tracker | Extract private key, generate arbitrary PoAC records |
| **Timestamp oracle manipulator** | Block validator (on PoA chains) | Set block.timestamp to accept out-of-window records |
| **Chain linkage forger** | Has device private key | Inject forked chains to confuse PoACVerifier state |

---

## 4. Threat Analysis

### 4.1 Replay Attack

**Threat:** An adversary observes a verified PoAC record (body bytes + signature) from
on-chain event data or the bridge mempool, then re-submits the same bytes to
`verifyPoAC()` or `verifyPoACBatch()` to claim additional bounties or ratings.

**Attack vectors:**

| Vector | Description |
|---|---|
| Single-TX replay | Submit verified body a second time via `verifyPoAC()` |
| Cross-batch replay | Include a previously verified body in a new `verifyPoACBatch()` call |
| Failed-batch retry | Body fails batch verification transiently; attacker retries to get it verified later |
| Cross-chain replay | Submit a body from testnet on mainnet (different chain IDs, no shared state) |

**Mitigations (as of Phase 6):**

| Control | Mechanism | Status |
|---|---|---|
| `verifiedRecords` mapping | Records SHA-256 hashes of all successfully verified bodies | Pre-Phase 6 |
| `submittedHashes` mapping | Records ALL bodies ever submitted to the contract, including those that failed batch verification | **Phase 6 NEW** |
| `RecordAlreadySubmitted` error | Explicit error for pre-submission replay (checked before signature verification — fast path) | **Phase 6 NEW** |
| Monotonic counter | `monotonicCtr` must strictly increase per device; prevents ordered sequence replay | Pre-Phase 6 |
| Chain linkage | `prevPoACHash` must chain correctly; prevents insertion of old records into current chain | Pre-Phase 6 |
| Timestamp window | `maxTimestampSkew` bounds the valid submission window (default: 3600s) | Pre-Phase 6 |

**Phase 6 design detail:**

The `submittedHashes` mapping is set in the OUTER context of `verifyPoACBatch()` before
the child `verifyPoACExternal()` call. This means it persists even when the child reverts:

```
verifyPoACBatch (outer TX context)
  └─ compute h = sha256(body)
  └─ submittedHashes[h] = true   ← SET HERE: persists on child revert
  └─ try verifyPoACExternal(...)
       └─ _verifyInternal(...)   ← may revert; if so, verifiedRecords unchanged
```

For `verifyPoAC` (single TX), if the inner call reverts, the entire TX reverts and
`submittedHashes` stays false — allowing legitimate retry after transient failure
(e.g., clock skew on first submission attempt).

**Residual risk:** Cross-chain replay (testnet body replayed on mainnet) is NOT blocked
by on-chain state (different contracts on different chains have independent storage).
Mitigation: body contains `prevPoACHash` which chains to on-chain state — a testnet
body's chain linkage will not match mainnet state, causing `ChainLinkageBroken`.

---

### 4.2 Sybil Attack

**Threat:** An adversary registers N device identities (N keys, N deposits) to:
(a) farm bounty rewards by submitting N×rewards per bounty
(b) achieve quorum in reputation-weighted governance
(c) inflate corroboration counts to earn reputation bonuses

**Mitigations (as of Phase 6):**

| Control | Mechanism | Effectiveness |
|---|---|---|
| `minimumDeposit` (1 IOTX) | Economic cost per device registration | Medium — low at current IOTX price |
| 7-day deactivation cooldown | Limits deposit cycling speed | Low — capital efficiency still viable |
| Pubkey uniqueness | `deviceId = keccak256(pubkey)`: one identity per keypair | Necessary but not sufficient |
| Hardware attestation (future) | IoTeX ioID + Pebble secure element | High — see sybil-analysis.md |

**Current anti-Sybil posture:** ECONOMIC ONLY. The 1 IOTX deposit requirement
imposes ~$0.04 USD cost per device at current prices, which is insufficient for
high-value bounties. See `security/sybil-analysis.md` for full analysis and
recommendation.

**Recommendation (Phase 7):** Implement stake-proportional reward weighting or
hardware attestation requirement. At minimum, increase `minimumDeposit` to 100 IOTX
for mainnet deployment.

---

### 4.3 Rating Manipulation (SkillOracle)

**Threat:** An adversary accumulates N legitimate PoAC records over time (each verified
by `PoACVerifier`), then submits all N to `SkillOracle.updateSkillRating()` in rapid
succession to spike a device's rating for tournament eligibility or governance weight,
bypassing the intended long-run averaging behavior of the ELO-like formula.

**Example:** Device accumulates 500 NOMINAL records at 5 pts each = +2500 rating
potential. Without rate-limiting, these can all be submitted in one block, jumping from
Bronze (0) to Diamond (2500) instantaneously.

**Mitigations (as of Phase 6):**

| Control | Mechanism | Status |
|---|---|---|
| `processedRecords` | Prevents double-counting a single record hash | Pre-Phase 6 |
| `minUpdateInterval` (1 block, ~5s on IoTeX) | Enforces minimum block gap between successive rating updates per device | **Phase 6 NEW** |
| `RateLimitExceeded` error | Explicit revert when interval not satisfied | **Phase 6 NEW** |
| `setMinUpdateInterval(owner)` | Owner can increase interval for higher-security deployments | **Phase 6 NEW** |

**Phase 6 design detail:**

```solidity
// In updateSkillRating()
uint256 last = _lastUpdateBlock[_deviceId];
if (last != 0 && block.number < last + minUpdateInterval) {
    revert RateLimitExceeded(_deviceId, block.number, last + minUpdateInterval);
}
_lastUpdateBlock[_deviceId] = block.number;
```

With `DEFAULT_MIN_INTERVAL = 1` block (~5 seconds on IoTeX), submitting 500 records
takes at minimum 500 blocks ≈ 41 minutes. This converts burst manipulation into a
sustained operation, allowing governance mechanisms and challenge periods to respond.

**Rate limiting is per-device, not per-caller.** Different devices can update ratings
in the same block without conflict.

**Residual risk:** An attacker with enough records can still achieve the maximum rating
(3000) given sufficient time. Rate-limiting controls the RATE of change, not the ceiling.
Tournament systems should additionally impose challenge periods or snapshot-based eligibility.

---

### 4.4 Timestamp Oracle Manipulation

**Threat:** On IoTeX (a delegated PoS chain), block validators control `block.timestamp`.
A colluding validator could set a favorable timestamp to accept a PoAC record that would
normally be outside the `maxTimestampSkew` window.

**Mitigations:**

| Control | Mechanism |
|---|---|
| `maxTimestampSkew` | Bounds the acceptance window (default 3600s) — validator can shift by at most slot time (~5s) |
| Firmware timestamp (int64 ms) | Signed into the body; cannot be changed post-signing |
| Chain linkage | Sequence of records with increasing timestamps; isolated manipulation is detectable |

**Assessment:** LOW risk given IoTeX's 23 delegated BPs. A single colluding BP can only
shift `block.timestamp` by 1-2 slots (~5-10s), insufficient to exploit the 3600s window.
On mainnet, consider reducing `maxTimestampSkew` to 300s (5 minutes) to tighten this.

---

### 4.5 Chain Linkage Attack

**Threat:** A compromised device or malicious bridge creates a fork in the PoAC chain
by submitting a new record that claims a different `prevPoACHash` than what the contract
expects, allowing the device to present different history to different observers.

**Mitigation:**

The contract stores `_chainState[deviceId].lastRecordHash` and enforces `prevPoACHash`
matches on every non-genesis submission. Any fork attempt causes `ChainLinkageBroken`.

The genesis loophole (prevHash = 0x00 always bypasses chain check) is a known design
decision that supports device reboots and counter resets. This is acceptable because the
monotonic counter check still prevents insertion of old records.

---

### 4.6 Signature Forgery

**Threat:** Attacker submits a body with a forged ECDSA-P256 signature to register
fraudulent PoAC records.

**Mitigation:**

- IoTeX P256 precompile (0x0100) performs ECDSA-P256 verification cryptographically
- 256-bit security: infeasible to forge with current hardware
- `PoACVerifierTestable` for testing overrides this to always-pass — MUST NOT be deployed

**Critical:** Ensure only `PoACVerifier` (not `PoACVerifierTestable`) is deployed to
mainnet/testnet. The deploy script uses `"PoACVerifier"` — verify this before mainnet.

---

### 4.7 Bridge Key Compromise

**Threat:** Attacker obtains the bridge's private key (`BRIDGE_PRIVATE_KEY`).

**Impact:**
- Can submit arbitrary PoAC records on behalf of any device the bridge serves
- Can drain bridge wallet
- Cannot forge signatures (needs device private key)

**Mitigations:**
- `.env.testnet` never committed with real keys (placeholder `0x<your-key>`)
- Bridge key should have minimum balance for gas only
- Future: multi-sig bridge or hardware wallet signing

---

## 5. Attack Surface Summary

| Surface | Exposure | Control |
|---|---|---|
| `verifyPoAC()` | Public, any caller | submittedHashes gate, sig check, rate limit (indirectly) |
| `verifyPoACBatch()` | Public, any caller | submittedHashes gate (outer context), individual record guards |
| `updateSkillRating()` | Public, any caller | processedRecords, rate limit, record verification |
| `registerDevice()` | Public, payable | minimumDeposit anti-Sybil |
| `attestProgress()` | Public | Requires two verified PoAC hashes |
| `submitTeamProof()` | Public | Requires all member hashes verified |
| `postBounty()` | Public, payable | Economic incentive alignment |

---

## 6. Known Issues / Out of Scope

| Issue | Severity | Plan |
|---|---|---|
| `minimumDeposit = 1 IOTX` is economically insufficient for high-value bounties | Medium | Increase to 100+ IOTX for mainnet |
| `_SKILL_ORACLE_ABI` in bridge uses `updateRating` but contract exports `updateSkillRating` | Low (functional bug) | Fix in Phase 7 bridge audit |
| Genesis record (`prevHash = 0x00`) bypasses chain linkage check | Low (by design) | Document; acceptable tradeoff for device reboots |
| `PoACVerifierTestable` must not be deployed to production | Critical | Deploy script uses correct factory name — verify |
| Single-TX path in `verifyPoAC` does NOT permanently blacklist transient failures | Low (by design) | Allows bridge retry; documented tradeoff |

---

## 7. Security Controls Matrix

| Threat | Contract Control | Bridge Control | Operational Control |
|---|---|---|---|
| Replay | submittedHashes, verifiedRecords | chain.ensure_device_registered (idempotent) | Rotate bridge key if compromised |
| Sybil | minimumDeposit, 7-day cooldown | N/A | Increase deposit on mainnet |
| Rating manipulation | processedRecords, minUpdateInterval | Submit one record at a time | Monitor for rapid rating spikes |
| Timestamp gaming | maxTimestampSkew | Bridge timestamp validation | Reduce skew on mainnet |
| Signature forgery | IoTeX P256 precompile | N/A | Secure device key storage |
| Bridge compromise | Device key required for sig | Separate bridge/device keys | Hardware wallet for bridge key |

---

## 8. Appendix: PoAC Record Security Properties

```
Field              Security Role
───────────────────────────────────────────────────────────────────
prev_poac_hash     Chain integrity — links records; forgery causes ChainLinkageBroken
sensor_commitment  Sensor binding — SHA-256 of raw IMU/button state; non-malleable
model_manifest_hash TinyML integrity — prevents model substitution attacks
world_model_hash   Inference binding — ties record to specific classification epoch
inference_result   Anti-cheat signal — determines SkillOracle delta
monotonic_ctr     Replay prevention — strictly increasing per device
timestamp_ms      Temporal binding — limits submission window
bounty_id         Economic binding — ties record to specific bounty
ECDSA-P256 sig    Authentication — proves device generated this body
```

The 228-byte record format provides defense-in-depth: multiple independent fields
must be consistent for a record to pass all validation checks, making forgery
computationally and structurally infeasible without the device private key.
