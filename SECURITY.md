# Security Policy

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately by emailing the repository owner (contact via GitHub profile) with:
- Description of the vulnerability
- Steps to reproduce
- Affected VAPI component (bridge, SDK, contracts, wire format)
- Potential impact

You will receive a response within 72 hours. If the vulnerability is confirmed, we will:
1. Work with you on a fix (crediting you unless you prefer anonymity)
2. Publish a security advisory after the fix is merged
3. Tag a new release

---

## Scope

| In scope | Out of scope |
|----------|-------------|
| Wire format parsing bugs in `sdk/vapi_sdk.py` or `bridge/vapi_bridge/` | Threshold calibration drift (this is a known calibration issue, not a vulnerability) |
| Chain hash verification bypass | L6_CHALLENGES_ENABLED=false (disabled by design) |
| Solidity contract vulnerabilities | Simulation attacks requiring physical hardware |
| Inference code injection (forging a NOMINAL code on a cheat session) | Inter-person confusion (documented limitation below) |
| Operator API auth bypass | Smart contract gas optimization |
| Rate limiter evasion on `/operator/` endpoints | Test suite failures on unsupported Python versions |

---

## Known Limitations (Not Vulnerabilities)

These are documented, empirically characterized limitations — not security bugs. They are
disclosed here in full rather than hidden in appendices.

### 1. Inter-Person Separation Ratio: 0.362

**What it means:** L4 (Mahalanobis biometric) uses a single population-level calibration.
The mean inter-person separation ratio across our N=74 calibration corpus (3 players) is
0.362 — well below the target of >2.0 required for reliable identity discrimination.

**Implication:** L4 is an intra-player anomaly detector. It reliably flags when a session
diverges from a player's own baseline. It does NOT reliably distinguish Player A from
Player B. A motivated attacker who has studied Player A's biometric profile could potentially
submit sessions that pass L4 while playing as Player B.

**Mitigation:** The ZK Tournament Passport enforces per-device session counts on-chain,
bounding the number of sessions per device that can be submitted to any tournament.
The ioID DID binds a device to a player identity. These are complementary controls.

**Status:** Active research gap. Fix requires post-Phase-17 touchpad recapture (hardware)
and expanded calibration corpus (N >> 74, more players). Tracked in whitepaper §8.6.

### 2. Biometric Transplant Attack: 0% Detection

**What it means:** If an attacker captures a legitimate player's raw PoAC chain (228-byte
records) and replays them verbatim, the chain hash verification passes and the biometric
fingerprint looks valid (it IS the real player's fingerprint). VAPI has no mechanism to
detect replay of valid records from a different session.

**Mitigations in place:**
- `monotonic_ctr` (uint32) must increase monotonically — pure replay of an old chain fails
- `timestamp_ms` is embedded and verified by smart contracts
- The ZK Tournament Passport commits to a session-specific nullifier

**Remaining gap:** A "fresh replay" attack — where the attacker re-signs records with the
legitimate device key (physical access required) — is not detected by any current layer.
This requires physical possession of the hardware device.

### 3. L2C Phantom Weight in Dead-Zone Stick Games

**What it means:** In games like NCAA College Football 26 that use extreme dead zones on
analog sticks, the L2C (Stick-IMU Cross-Correlation) oracle returns `None`. This resolves
to a 0.5 neutral prior in the humanity formula, effectively reducing the formula to 4 active
signals with weights renormalized implicitly.

**Formula without L6 (effective in NCAA CFB 26):**
```
humanity_probability = 0.28·p_L4 + 0.27·p_L5 + 0.20·p_E4 + 0.15·p_L2B + (0.10·0.5)
```
The L2C contribution is a fixed 0.05 addend, not a real signal.

**Implication:** An attacker targeting NCAA CFB 26 specifically gets a 0.10-weight free pass.
This reduces the effective detection coverage but does not eliminate it — L4, L5, E4, and L2B
remain active.

### 4. L6 Active Challenge-Response: Disabled by Default

`L6_CHALLENGES_ENABLED=false` is the correct production default. The current L6 calibration
corpus has N=43 samples. The RIGID_MAX threshold is uncalibrated (insufficient data for
reliable gating). Enabling L6 as a primary tournament gate would produce false positives
at an unacceptable rate.

**This is not a vulnerability.** Do not report it as one.

### 5. Feature Index Exclusions (L4)

Two of the 12 L4 biometric features are structurally zero in the current calibration corpus
and are excluded from the Mahalanobis computation:
- `trigger_resistance_change_rate` (index 0) — requires adaptive trigger hardware signal not
  yet captured at the required resolution
- `touch_position_variance` (index 10) — touchpad inactive in most competitive game sessions

These exclusions are correct. Including zero-variance features would inflate the Mahalanobis
distance and produce false positives.

---

## What Is NOT a Vulnerability

- Threshold calibration: recalibrating L4/L5 thresholds as the corpus grows is expected behavior
- L6_CHALLENGES_ENABLED=false: this is the correct default (see above)
- Human false positive rate ~2.9%: expected at 3σ, documented in whitepaper §7.3
- Separation ratio 0.362: documented limitation, not a bypass
- Advisory inference codes (0x2B, 0x30, 0x31, 0x32): these accumulate evidence but do not
  block tournament eligibility on their own. That is by design.

---

## Supported Versions

| Component | Supported |
|-----------|-----------|
| Bridge (Phase 61) | Yes |
| SDK (1.0.0) | Yes |
| Contracts (testnet) | Yes (testnet only) |
| Mainnet contracts | Not yet deployed |
| Firmware (Pebble Tracker reference) | No (pre-hardware validation) |
