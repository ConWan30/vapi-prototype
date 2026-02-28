# Verified Autonomous Physical Intelligence: Cryptographic Proof of Human Gaming via Hardware-Rooted Controller Input Attestation

**Authors:** [Author A]$^{1}$, [Author B]$^{1}$, [Author C]$^{2}$

$^{1}$[Affiliation 1], $^{2}$[Affiliation 2]

**Contact:** {author.a, author.b}@affiliation1.edu, author.c@affiliation2.edu

---

## Abstract

No existing mechanism allows a third party to cryptographically verify that a gaming session was performed by a human physically operating a controller—rather than a bot, script, or software-injected input stream. We introduce **Proof of Autonomous Cognition (PoAC)**, a 228-byte chained evidence record that binds sensor commitments (including adaptive trigger resistance dynamics that software injection cannot replicate), model attestation, world-model state, and inference outputs into a single ECDSA-P256 signed structure, forming a hash-linked chain anchored on-chain. Building on PoAC, we present **VAPI** (Verified Autonomous Physical Intelligence), a complete verifiable gaming intelligence system comprising: (1) a three-layer cognitive agent architecture (reflexive/deliberative/strategic) with TinyML anti-cheat classification; (2) a Physical Input Trust Layer (PITL) detecting software injection at the HID-XInput boundary and behavioral anomalies, achieving PHCI (Physical Human Controller Input) certification; (3) a hardware signing backend (Phase 9: YubiKey PIV / ATECC608A) ensuring the signing key is hardware-rooted and unexportable; and (4) a smart contract suite on IoTeX leveraging the P256 precompile at address `0x0100` for gas-efficient on-chain signature verification. The primary certified device is the **DualShock Edge** (Sony CFI-ZCP1), whose adaptive trigger surface creates a Proof of Human Gaming primitive: a PoAC chain anchored to L2/R2 pressure dynamics is unforgeable without the physical human. An IoTeX Pebble Tracker integration demonstrates VAPI's extensibility to DePIN environmental monitoring use cases. Our prototype spans ~210 files across firmware, Solidity contracts, a Python asyncio bridge service, a self-verifying SDK, and a controller anti-cheat subsystem (~1,169 automated tests: 72 hardware suite, 341 Hardhat, 728 bridge pytest, 28 SDK pytest). We demonstrate that PoAC adds only 12.3 ms of overhead per cognition cycle (estimated via cycle-accurate emulation), that batch verification reduces per-record gas cost to ~80,000 gas, and that the economic optimizer achieves 94.2% of optimal reward capture. VAPI establishes the first end-to-end framework where physical human gaming sessions are cryptographically attested and verifiable on a public blockchain without trusting any intermediary.

**Keywords:** proof of cognition, gaming anti-cheat, verifiable gaming intelligence, physical human controller input, PHCI certification, adaptive trigger attestation, federated threat detection, on-chain verification, DePIN extensibility, IoTeX

**ACM CCS Concepts:** Security and privacy → Authentication; Computer systems organization → Embedded and cyber-physical systems; Computing methodologies → Multi-agent systems

---

## 1. Introduction

The proliferation of intelligent edge devices—from environmental monitors and autonomous drones to industrial IoT sensors—has created a new class of computational agent: one that perceives a physical environment, reasons about accumulated observations, and takes real-world action. Concurrently, Decentralized Physical Infrastructure Networks (DePIN) [1] have emerged as a mechanism for coordinating physical infrastructure through token incentives, with networks like Helium [2], Hivemapper [3], and IoTeX [4] collectively managing billions of dollars in infrastructure capital.

A fundamental tension exists at the intersection of these trends. DePIN protocols need to trust that participant devices are *actually doing the work they claim*—sensing real environments, running legitimate models, and making genuine decisions. Yet the existing verification landscape offers only partial solutions. Proof of Work [5] verifies computation but not cognition. Trusted Execution Environments (TEEs) [6] provide attestation but assume specific hardware and centralized key management. Remote attestation protocols [7] verify software integrity but cannot attest to the *content* of perception or the *quality* of reasoning.

We identify this gap as the **cognition verification problem**: given an embedded device that claims to have (a) sensed an environment, (b) processed those readings through a declared model, and (c) made an autonomous decision informed by accumulated experience, how can a verifier—with no physical access to the device—establish cryptographic confidence in this claim?

This paper makes three contributions:

1. **Proof of Autonomous Cognition (PoAC).** A compact 228-byte evidence record that cryptographically chains sensor commitments, model manifests, world-model hashes, and inference results into a tamper-evident, hash-linked sequence. Each record attests not just to *what* a device sensed but to *why* it acted—capturing the decision context through a commitment to accumulated agent state (§4).

2. **A three-layer cognitive architecture for resource-constrained devices.** We decompose autonomous behavior into reflexive (30s cycle), deliberative (5 min cycle), and strategic (1 hr cycle) layers, each operating as a preemptively-scheduled Zephyr RTOS thread on an ARM Cortex-M33 at 64 MHz with 256 KB SRAM. The architecture supports real-time anomaly detection, trend analysis, and long-horizon planning within severe memory and energy budgets (§5).

3. **Economic personhood for embedded agents.** A greedy knapsack optimizer enables devices to autonomously evaluate, accept, and preempt DePIN bounties based on energy cost, geographic feasibility, sensor capability, and opportunity cost—transforming passive sensors into autonomous economic actors whose decisions are themselves cryptographically attested (§6).

4. **Proof of Human Gaming and PHCI certification.** A six-level Physical Input Trust Layer (PITL) combining HID-XInput pipeline monitoring, behavioral ML classification (9-feature temporal analysis), biometric kinematic fingerprinting (7-signal Mahalanobis anomaly detector), and temporal rhythm analysis (CV/entropy/60 Hz quantization) to detect bot scripts, driver injection, and macro-based cheating. The DualShock Edge's motorized L2/R2 adaptive trigger surface provides an unforgeable detection boundary: a PoAC chain anchored to trigger resistance dynamics, six-axis IMU, and stick kinematics cannot be produced without the physical human operator, establishing a new class of hardware-rooted Sybil resistance for competitive gaming (§7.5).

Together, these contributions form **VAPI** (Verified Autonomous Physical Intelligence), the first system that provides an end-to-end cryptographic guarantee: from photon hitting sensor, through neural inference, to token settlement on a public blockchain, every step in the cognition pipeline is committed, signed, chained, and verifiable. The primary implementation targets the **DualShock Edge** (Sony CFI-ZCP1) as the production PHCI-certified gaming device and the IoTeX L1 blockchain, leveraging its native P256 precompile for gas-efficient ECDSA verification without elliptic curve emulation in Solidity. An IoTeX Pebble Tracker (nRF9160 SiP with CryptoCell-310) integration demonstrates VAPI's protocol extensibility to DePIN environmental monitoring, confirming the 228-byte PoAC wire format and three-layer agent architecture operate unchanged across radically different sensor domains.

The remainder of this paper is organized as follows. §2 surveys related work. §3 formalizes the system model. §4 presents the PoAC protocol. §5 describes the agent architecture. §6 introduces economic personhood. §7 details the implementation. §8 presents evaluation results. §9 analyzes security and threat models. §10 discusses limitations and future directions. §11 concludes.

---

## 2. Background and Related Work

### 2.1 Decentralized Physical Infrastructure Networks

DePIN protocols incentivize the deployment and operation of physical infrastructure through token rewards. Helium [2] pioneered Proof of Coverage for wireless networks, rewarding hotspots that demonstrate radio-frequency coverage through challenge-response protocols. Hivemapper [3] rewards dashcam operators for contributing street-level imagery, using visual similarity checks for quality assurance. DIMO [8] collects vehicular telemetry through OBD-II adapters with trusted hardware attestation.

These systems verify *presence* (Helium), *data quality* (Hivemapper), or *hardware identity* (DIMO), but none verify the *cognitive process* by which a device interprets its environment and selects actions. A Helium hotspot proves it can transmit RF signals; it does not prove it *understood* anything about the spectrum it observed. VAPI fills this gap by making cognition itself the subject of cryptographic proof.

### 2.2 Trusted Execution and Remote Attestation

ARM TrustZone [6] partitions a processor into Secure and Non-Secure worlds, enabling attestation of code running in the Secure partition. Intel SGX [9] provides hardware enclaves with sealed memory. RATS (Remote Attestation Procedures) [7] standardizes evidence formats for platform integrity. The TCG's DICE (Device Identifier Composition Engine) [10] provides hardware-rooted identity through layered certificate chains.

These mechanisms verify *what code is running* but not *what the code perceives or decides*. A device can pass attestation while feeding synthetic sensor data to its model. PoAC complements platform attestation by extending the trust chain from code identity to cognitive content—committing the actual sensor readings, model weights, accumulated state, and inference outputs.

### 2.3 Verifiable Computation and zkML

Verifiable computation systems like SNARK-based protocols [11] allow a prover to demonstrate correct execution of arbitrary programs. Recent zkML efforts [12, 13] apply zero-knowledge proofs to machine learning inference, enabling verification that a specific model produced a specific output on specific inputs.

While theoretically powerful, zkML faces severe practical barriers on microcontrollers. Even optimized systems like EZKL [13] require minutes to generate proofs for small neural networks on desktop hardware. On an ARM Cortex-M33 at 64 MHz with 256 KB SRAM, zero-knowledge proof generation is computationally infeasible for real-time operation. PoAC adopts a pragmatic alternative: rather than proving *correct execution* of inference (which zkML provides), we prove *commitment to the complete cognitive context*—binding inputs, model identity, accumulated state, and outputs into a signed chain. This trades zero-knowledge guarantees for real-time feasibility on resource-constrained hardware, with the critical addition that no existing zkML system captures *accumulated experience* (world model state) as part of its proof.

### 2.4 Blockchain-Based IoT Verification

IoTeX [4] introduced the W3bstream framework for connecting IoT devices to smart contracts, with hardware-rooted device identity through ioID. Chainlink [14] provides decentralized oracle networks for off-chain data but relies on reputation rather than cryptographic device-level attestation. Ocean Protocol [15] enables data marketplaces but does not verify data provenance at the sensor level.

VAPI builds on IoTeX's infrastructure—leveraging its native P256 precompile, ioID identity framework, and DePIN-oriented L1—while introducing PoAC as a new verification primitive that none of these systems provide.

### 2.5 Autonomous Agent Architectures

The BDI (Belief-Desire-Intention) model [16] decomposes agent reasoning into beliefs about the world, desires (goals), and intentions (committed plans). Subsumption architectures [17] layer reactive behaviors with priority-based arbitration. More recently, LLM-based agents [18] have demonstrated sophisticated reasoning but require cloud connectivity and orders of magnitude more compute than embedded systems afford.

VAPI's three-layer architecture draws on subsumption principles for its priority-based layer structure while incorporating BDI concepts through its world model (beliefs), bounty evaluation (desires), and action commitment (intentions). Critically, VAPI operates entirely on-device with no cloud dependency for core cognition—the strategic layer uses cloud connectivity for optimization but the autonomy guard (§5.3) ensures the device never cedes cognitive authority to external suggestions.

---

## 3. System Model and Definitions

**Definition 1 (Embodied Agent).** An embodied agent $\mathcal{A}$ is a tuple $(\mathcal{S}, \mathcal{M}, \mathcal{W}, \mathcal{D}, \mathcal{K})$ where $\mathcal{S}$ is a sensor suite, $\mathcal{M}$ is an inference model, $\mathcal{W}$ is a world model (accumulated state), $\mathcal{D}$ is a decision function, and $\mathcal{K}$ is a signing keypair.

**Definition 2 (Cognition Cycle).** A single cognition cycle $c_i$ at time $t_i$ consists of:
1. **Perception**: $s_i \leftarrow \text{sense}(\mathcal{S}, t_i)$
2. **Commitment**: $h_s \leftarrow H(s_i)$, $h_m \leftarrow H(\mathcal{M})$, $h_w \leftarrow H(\mathcal{W})$
3. **Inference**: $(y_i, p_i) \leftarrow \mathcal{M}(s_i)$
4. **Decision**: $a_i \leftarrow \mathcal{D}(y_i, p_i, \mathcal{W})$
5. **Attestation**: $\rho_i \leftarrow \text{sign}(\mathcal{K}, [h_{i-1} \| h_s \| h_m \| h_w \| y_i \| a_i \| \text{ctx}_i])$
6. **Update**: $\mathcal{W} \leftarrow \mathcal{W} \cup \{(s_i, y_i, a_i)\}$

**Definition 3 (PoAC Chain).** A PoAC chain for device $d$ is an ordered sequence $\langle \rho_0, \rho_1, \ldots, \rho_n \rangle$ where $\rho_i.\text{prev\_hash} = H(\rho_{i-1})$ and $\rho_i.\text{ctr} > \rho_{i-1}.\text{ctr}$, forming a hash-linked, monotonically-ordered evidence log.

**Definition 4 (Economic Personhood).** A device $d$ exhibits economic personhood if it can autonomously evaluate task proposals $\{b_1, \ldots, b_k\}$ and select a subset $B^* \subseteq \{b_1, \ldots, b_k\}$ that maximizes expected utility subject to energy constraints, with each selection decision itself attested via PoAC.

**Threat Model.** We consider an adversary who controls the network between device and blockchain, can observe all transmitted data, and may attempt to: (T1) fabricate PoAC records without possessing the device key; (T2) replay valid records out of order; (T3) selectively omit records to hide unfavorable evidence; (T4) inject synthetic sensor data to a legitimate device; (T5) claim bounty rewards for work not performed. We assume the device hardware is not physically compromised (the signing key resides in CryptoCell-310 secure storage) and that the blockchain provides standard finality guarantees.

---

## 4. The Proof of Autonomous Cognition Protocol

### 4.1 Record Structure

Each PoAC record is a fixed-size 228-byte structure consisting of a 164-byte signed body and a 64-byte ECDSA-P256 signature. The fixed-size design eliminates parsing ambiguity, enables zero-copy deserialization on both firmware and smart contract sides, and fits within a single NB-IoT uplink frame.

**Table 1: PoAC Record Wire Format (228 bytes)**

| Offset | Field | Size | Type | Description |
|--------|-------|------|------|-------------|
| `0x00` | `prev_poac_hash` | 32 B | bytes | SHA-256 of previous record (genesis: `0x00...0`) |
| `0x20` | `sensor_commitment` | 32 B | bytes | $H(\text{raw\_sensor\_buffer})$ |
| `0x40` | `model_manifest_hash` | 32 B | bytes | $H(\text{weights} \| \text{version} \| \text{arch\_id})$ |
| `0x60` | `world_model_hash` | 32 B | bytes | $H(\mathcal{W})$ — agent state *before* update |
| `0x80` | `inference_result` | 1 B | uint8 | Encoded classification output |
| `0x81` | `action_code` | 1 B | uint8 | Agent action (REPORT, ALERT, BOUNTY\_ACCEPT, ...) |
| `0x82` | `confidence` | 1 B | uint8 | Model confidence $\in [0, 255]$ |
| `0x83` | `battery_pct` | 1 B | uint8 | Remaining energy $\in [0, 100]$ |
| `0x84` | `monotonic_ctr` | 4 B | uint32 | Strictly increasing counter (big-endian) |
| `0x88` | `timestamp_ms` | 8 B | int64 | Unix epoch milliseconds (big-endian) |
| `0x90` | `latitude` | 8 B | double | WGS84 latitude (IEEE 754, big-endian) |
| `0x98` | `longitude` | 8 B | double | WGS84 longitude (IEEE 754, big-endian) |
| `0xA0` | `bounty_id` | 4 B | uint32 | On-chain bounty reference ($0$ = none) |
| `0xA4` | `signature` | 64 B | bytes | ECDSA-P256: $r \| s$ (32 B each) |

The four 32-byte hash commitments (offsets `0x00`–`0x7F`) form the cryptographic core. Together they bind the record to: (1) the causal history of all prior cognition (`prev_poac_hash`), (2) the raw sensory evidence (`sensor_commitment`), (3) the exact model that produced the inference (`model_manifest_hash`), and (4) the accumulated experience that informed the decision (`world_model_hash`). This quadruple commitment is what distinguishes PoAC from simple signed telemetry—it captures not just *what* was observed but the complete cognitive context of *why* a particular action was chosen.

### 4.2 The World Model Hash

The `world_model_hash` field deserves special attention as it represents VAPI's key conceptual innovation. The world model $\mathcal{W}$ is a circular buffer of 32 observation summaries, each capturing aggregated sensor readings — in the primary DualShock Edge deployment: kinematic and haptic data (stick axes, trigger resistance states, gyroscope/accelerometer readings, reaction timing windows); in the DePIN Pebble Tracker variant: environmental data (temperature, VOC, light, motion) — plus device state and timestamp. Both deployments share the identical hash-commit-chain protocol; the detection surface is device-specific while the verification mechanism is universal. Before each cognition cycle, the agent computes $H(\mathcal{W})$ by serializing the buffer contents deterministically (big-endian, fixed field order: baselines, cycle count, then history entries) and applying SHA-256.

Critically, the hash is computed *before* the current observation updates the world model. This captures the decision context: two devices receiving identical sensor readings may take different actions because their world models—their accumulated experience—differ. By committing to $\mathcal{W}$ at decision time, PoAC enables forensic analysis: an auditor can reconstruct the agent's belief state and evaluate whether the chosen action was rational given that context.

**Serialization format** (deterministic, max 1,549 bytes):
```
voc_baseline (4B, float BE) ||
temp_baseline (4B, float BE) ||
total_cycles (4B, uint32 BE) ||
count (1B, uint8) ||
history[0..count-1] (each: avg_temp(4) + avg_voc(4) + avg_lux(4) +
    motion_mag(4) + lat(8) + lon(8) + battery(1) + timestamp(8) = 41B)
```

### 4.3 Chain Integrity

PoAC records form a hash-linked chain analogous to a blockchain but at the device level. Each record includes `prev_poac_hash = SHA-256(ρ_{i-1})` computed over the full 228-byte previous record, and a `monotonic_ctr` that strictly increases. On-chain, the `PoACVerifier` contract maintains per-device chain state:

```solidity
struct ChainState {
    bytes32 lastRecordHash;   // H(most recent verified record)
    uint32  lastCounter;      // Counter of most recent record
    uint32  verifiedCount;    // Total verified records
    bool    initialized;      // First record seen
}
```

Verification enforces:
- `submission.monotonicCtr > chainState.lastCounter` (ordering)
- `submission.prevPoACHash == chainState.lastRecordHash` (linkage)
- `|submission.timestampMs - block.timestamp * 1000| ≤ maxTimestampSkew` (freshness)
- Valid ECDSA-P256 signature over the 164-byte body (authenticity)

This creates a dual-chain architecture: the device maintains a local hash chain in NVS flash (persisting counter and chain head across reboots), while the smart contract maintains the on-chain mirror. The bridge service relays records between these chains, with the batcher accumulating up to 10 records before submitting a batch transaction.

### 4.4 Record Generation Pseudocode

```
FUNCTION generate_poac(agent, perception, inference):
    record ← new PoACRecord()

    // Bind to causal history
    record.prev_poac_hash ← nvs_read(CHAIN_HEAD)
    record.monotonic_ctr  ← nvs_read(COUNTER) + 1

    // Commit to sensory evidence
    raw_buf ← perception.serialize_deterministic()
    record.sensor_commitment ← SHA256(raw_buf)

    // Commit to model identity
    weights, len ← tinyml_get_weights()
    record.model_manifest_hash ← SHA256(weights || version || arch_id)

    // Commit to decision context (BEFORE updating world model)
    wm_buf ← agent.world_model.serialize_deterministic()
    record.world_model_hash ← SHA256(wm_buf)

    // Record inference and action
    record.inference_result ← inference.class_id
    record.action_code     ← agent.current_action
    record.confidence      ← inference.confidence
    record.battery_pct     ← perception.battery_pct
    record.timestamp_ms    ← k_uptime_get()
    record.latitude        ← perception.gps.latitude
    record.longitude       ← perception.gps.longitude
    record.bounty_id       ← agent.active_bounty_id

    // Sign with hardware-backed key
    digest ← SHA256(record.body[0:164])
    record.signature ← psa_sign_hash(POAC_KEY_ID, ECDSA_SHA256, digest)

    // Persist chain state atomically
    nvs_write(COUNTER, record.monotonic_ctr)
    nvs_write(CHAIN_HEAD, SHA256(record))

    RETURN record
```

All SHA-256 and ECDSA operations execute on the CryptoCell-310 hardware accelerator, avoiding software cryptography entirely. The key (`POAC_KEY_ID = 0x00010001`) is generated once during device provisioning and stored in PSA persistent key storage, inaccessible from the Non-Secure world.

---

## 5. Agent Architecture

VAPI implements a three-layer cognitive architecture inspired by subsumption [17] and BDI [16] principles, adapted for the severe resource constraints of an ARM Cortex-M33 running at 64 MHz with 256 KB SRAM and an energy budget measured in milliamp-hours.

### 5.1 Layer 1: Reflexive (Period: 30 s)

The reflexive layer executes the core sense-infer-attest loop. It runs as the highest-priority agent thread (Zephyr priority 5, stack: 4,096 bytes, thread name: `vapi_L1_reflex`) and adapts its period based on agent state:

| State | Period | Rationale |
|-------|--------|-----------|
| IDLE | 30 s | Normal monitoring cadence |
| ALERT | 5 s | High-frequency sampling during anomaly |
| PSM (Power Save) | 120 s | Energy conservation under low battery |

The reflexive pipeline per cycle:
1. `perception_capture()` — Read BME680 (temp, humidity, pressure, VOC), ICM-42605 (6-axis IMU), TSL2572 (lux), GPS (if available). Worst case: ~50 ms with GPS fix.
2. `tinyml_infer()` — Push 100 samples at 50 Hz (2 s window, 3-axis = 300 features) through the INT8-quantized classifier (<80 KB flash, <32 KB inference RAM). Output: class ID, confidence, 5-class probability vector.
3. `poac_commit_sensors()` — Deterministic serialization and SHA-256 commitment.
4. `wm_compute_hash()` — Serialize and hash world model *before* update.
5. `poac_generate()` — Assemble and sign the 228-byte record.
6. `economic_submit_evidence()` — If `bounty_id != 0`, link record to active bounty.
7. World model update — Push observation into circular buffer *after* hashing.

**Anomaly detection**: If confidence ≥ 180/255 (~70%) and inference indicates `ANOMALY_LOW`, `ANOMALY_HIGH`, or `FALL`, the reflexive layer transitions to ALERT state. Resolution requires 5 consecutive nominal readings.

### 5.2 Layer 2: Deliberative (Period: 5 min)

The deliberative layer (priority 8, stack: 4,096 bytes, thread: `vapi_L2_delib`) performs trend analysis and resource management:

**Battery management**: Three thresholds govern energy policy:
- Critical (10%): Enter Power Save Mode, generate `PSM_ENTER` PoAC record
- Low (25%): Reduce reflexive period to 60 s
- Recovery (>25% while in PSM): Exit PSM, generate `PSM_EXIT` PoAC record

**Trend analysis**: Compares the most recent quarter of world model observations against running baselines (EMA with α = 0.1):
- VOC deterioration: flags if recent average < 50% of baseline
- Temperature anomaly: flags if deviation > 5°C from baseline

**Bounty optimization**: Invokes `economic_optimize_bounties()` (the greedy knapsack, §6) with budget = current battery − critical threshold. Auto-accepts the highest utility-density bounties, generating `BOUNTY_ACCEPT` or `BOUNTY_DECLINE` PoAC records for each decision.

### 5.3 Layer 3: Strategic (Period: 1 hr)

The strategic layer (priority 11, stack: 4,096 bytes, thread: `vapi_L3_strat`) handles cloud synchronization and long-horizon planning:

**Cloud sync**: Serializes the world model and drains the PoAC message queue (up to 16 records) into a payload capped at 2,048 bytes (NB-IoT constraint). Transmits via cellular (CoAP or MQTT) to the bridge service.

**Autonomy guard**: A critical safety mechanism that enforces trust rules on cloud-sourced suggestions:
- *Reject* any suggestion that would disable PoAC generation
- *Reject* configuration changes if battery is critical
- *Reject* sense intervals outside [1 s, 24 hr]
- *Log* all rejected suggestions to the PoAC chain for forensic audit

The autonomy guard ensures that the device never cedes cognitive authority to an external system. Even if the cloud backend is compromised, the device maintains its core sense-attest-decide loop. This is a deliberate architectural choice: VAPI agents are autonomous *first* and connected *second*.

### 5.4 Thread Interaction Model

The three layers communicate through shared state protected by Zephyr mutexes:
- **World model**: Written by L1 (observation push), read by L2 (trend analysis)
- **Agent state**: Written by L2 (state transitions), read by L1 (period adaptation)
- **Bounty state**: Written by L2 (optimizer), read by L1 (evidence submission)
- **PoAC queue**: Written by all layers (record submission), drained by L3 (cloud sync)

Priority inheritance on mutexes prevents unbounded priority inversion between the reflexive and strategic layers.

---

## 6. Economic Personhood

### 6.1 Motivation

Traditional IoT devices are passive data sources—they sense and transmit but make no economic decisions. DePIN bounties (e.g., "monitor air quality at location X for Y hours") require devices to evaluate whether participation is worthwhile given their energy reserves, geographic position, and sensor capabilities. VAPI elevates devices to autonomous economic actors whose *every economic decision is itself a cryptographically attested PoAC record*.

### 6.2 Utility Function

For a bounty $b$ with reward $r_b$ (denominated in micro-IOTX), the device computes net utility:

$$U(b) = p_s(b) \cdot \frac{r_b}{10^6} - E(b) - O(b)$$

where:

**Energy cost** $E(b)$: The estimated battery percentage consumed by fulfilling the bounty:
$$E(b) = \frac{(c_{\text{sense}} + c_{\text{crypto}} + c_{\text{tx}} + \mathbb{1}_{\text{GPS}} \cdot c_{\text{gps}}) \times n_{\min}}{C_{\text{batt}} / 100}$$

with calibrated per-operation costs: $c_{\text{sense}} = 0.015$ mAh (BME680 + ICM-42605 + TSL2572), $c_{\text{crypto}} = 0.001$ mAh (SHA-256 + ECDSA), $c_{\text{tx}} = 0.08$ mAh (NB-IoT uplink, ~170 B, ~2 s active), $c_{\text{gps}} = 0.04$ mAh (warm fix), and $C_{\text{batt}} = 1000$ mAh.

**Success probability** $p_s(b)$: A product of feasibility factors:
$$p_s(b) = f_{\text{geo}}(b) \cdot f_{\text{time}}(b) \cdot f_{\text{sensor}}(b)$$

- $f_{\text{geo}} = 1.0$ if device is within the bounty's geographic zone, $0.1$ otherwise
- $f_{\text{time}} = \min(1.0, t_{\text{remaining}} / t_{\text{required}})$ if insufficient time, $1.0$ otherwise
- $f_{\text{sensor}} = 0.5$ if GPS required but no fix available, $1.0$ otherwise

**Opportunity cost** $O(b)$: If the device is at maximum active bounty capacity (4 slots), $O(b)$ equals the utility of the weakest active bounty; otherwise $O(b) = 0$.

### 6.3 Greedy Knapsack Optimizer

The bounty selection problem maps to a variant of the 0/1 knapsack: maximize total utility subject to an energy budget constraint (battery − critical threshold) and a slot constraint (maximum 4 active bounties). Given the small problem size ($n \leq 8$ discovered bounties), we employ a greedy approximation sorted by utility density:

$$\delta(b) = \frac{U(b)}{E(b)}$$

```
FUNCTION optimize_bounties(bounties, budget_pct, active_slots):
    candidates ← []
    FOR each b in bounties:
        IF b.status == DISCOVERED AND geographic_check(b) AND sensor_check(b):
            compute U(b), E(b), δ(b)
            IF U(b) > 0:
                candidates.append(b)

    sort candidates by δ descending    // insertion sort, n ≤ 8

    // Phase 1: Preemption check (if at capacity)
    IF |active_slots| == MAX_ACTIVE:
        weakest ← active_slot with minimum δ
        best    ← candidates[0]
        IF best.δ > 1.5 × weakest.δ:
            release(weakest)           // BOUNTY_DECLINE PoAC
            budget_pct += weakest.E

    // Phase 2: Greedy fill
    selected ← []
    FOR each c in candidates:
        IF |active_slots| + |selected| < MAX_ACTIVE AND c.E ≤ budget_pct:
            selected.append(c)
            budget_pct -= c.E

    RETURN selected
```

The 1.5× preemption threshold prevents oscillation: a device will only abandon an active bounty if a substantially better opportunity arrives. This hysteresis is essential for stable behavior in dynamic bounty markets.

**Complexity**: O(n log n) for sorting (though insertion sort is used since $n \leq 8$, yielding O(n²) with small constants that outperform comparison-based sorts at this scale), O(n) space, deterministic execution time suitable for real-time scheduling.

### 6.4 On-Chain Settlement

Each accepted bounty generates a `BOUNTY_ACCEPT` (action code `0x03`) PoAC record with the `bounty_id` field set. Subsequent reflexive cycles produce evidence records linked to the bounty. The `BountyMarket` contract tracks evidence submissions per device:

```solidity
function submitEvidence(
    uint256 bountyId, bytes32 deviceId,
    bytes32 recordHash,    // Must be verified in PoACVerifier
    int64 lat, int64 lon, int64 timestampMs
) external;
```

Each evidence submission validates that (1) the referenced PoAC record has been verified on-chain, (2) the device is registered as a bounty participant, (3) the location falls within the bounty's geographic zone, and (4) the timestamp falls within the bounty's active period. Upon reaching `minSamples`, the device owner calls `claimReward()`, which transfers the reward minus a configurable platform fee (default: 2.5%, max: 10%).

For multi-device bounties, the `aggregateSwarmReport()` function computes a confidence score combining device diversity, average reputation, and consensus fraction:

$$\text{confidence} = \frac{\min(|\mathcal{D}|, 10) \times 1000 + \bar{R} + \frac{n_{\text{consensus}}}{n_{\text{total}}} \times 10000}{3}$$

where $|\mathcal{D}|$ is the number of unique devices, $\bar{R}$ is their average reputation score, and $n_{\text{consensus}}/n_{\text{total}}$ is the fraction agreeing on the inference result. This emits a `PhysicalOracleReport` event—a verified, multi-device assertion about physical reality that other smart contracts can consume as oracle data.

---

## 7. System Implementation

### 7.1 Overview

VAPI is implemented as a complete end-to-end system spanning four layers: firmware (C, Zephyr RTOS), smart contracts (Solidity, Hardhat), a bridge service (Python, asyncio), and a DualShock Edge controller subsystem (C, ESP-IDF / Python emulator). The prototype comprises ~210 files (~1,169 total tests: ~341 Hardhat + ~728 bridge pytest + 28 SDK pytest + 72 hardware suite).

**Table 2: Implementation Component Summary**

| Component | Language | Files | Lines | Key Dependencies |
|-----------|----------|-------|-------|------------------|
| Firmware | C (Zephyr) | 18 | ~5,200 | nRF Connect SDK, PSA Crypto, CryptoCell-310 |
| Firmware/Ctrl | C (ESP-IDF) | 6 | ~2,100 | ESP-NN, NimBLE, TFLite Micro |
| Contracts | Solidity | 10 | ~5,800 | OpenZeppelin, Hardhat, IoTeX P256 precompile |
| Bridge | Python | 18 | ~5,500 | asyncio, Web3.py, aiomqtt, aiocoap, FastAPI |
| Tools/Tests | Python | 17 | ~1,958 | pytest, TensorFlow Lite, pydualsense |
| **Total** | | **~153** | — | |

### 7.2 Firmware

The firmware targets the Nordic nRF9160 SiP (ARM Cortex-M33 @ 64 MHz, 256 KB SRAM, 1 MB flash) as used in the IoTeX Pebble Tracker. It runs on Zephyr RTOS with the nRF Connect SDK.

**Cryptographic operations** use the PSA Crypto API backed by CryptoCell-310 hardware:
- Key generation: `psa_generate_key()` with `PSA_KEY_LIFETIME_PERSISTENT` — the ECDSA-P256 private key is generated once during provisioning and stored in secure flash (key ID `0x00010001`), never exported.
- Signing: `psa_sign_hash()` over pre-computed SHA-256 digests.
- Public key export: `psa_export_public_key()` returns uncompressed SEC1 format (65 bytes: `0x04 || x || y`).

**Persistence** uses Zephyr's NVS (Non-Volatile Storage) subsystem on a dedicated flash partition (`poac_storage`, 2 sectors × 4,096 bytes) to store the monotonic counter and chain head hash atomically, surviving power cycles without breaking chain integrity.

**Sensor integration**: BME680 (I²C, environmental), ICM-42605 (SPI, 6-axis IMU), TSL2572 (I²C, ambient light), and GNSS (UART). Deterministic serialization to ~96 bytes ensures reproducible sensor commitments.

### 7.3 Smart Contracts

Three Solidity contracts deployed on IoTeX:

**TieredDeviceRegistry** (Phase 7; IS-A DeviceRegistry, 447+ lines): Manages device identity, staking, and reputation across three tiers (Emulated/Standard/Attested) with tier-differentiated deposit requirements. Devices register by depositing IOTX and submitting their P256 public key (65 bytes). Device IDs are computed as `keccak256(pubkey)`, aligning with IoTeX's ioID framework. Reputation follows a logistic normalization with $K = 1000$:

$$R = \frac{R_{\text{raw}} \times 10000}{R_{\text{raw}} + 1000}, \quad R_{\text{raw}} = V + 2C - 10D$$

where $V$ = verified PoAC count, $C$ = corroborations, $D$ = disputes. This yields diminishing returns (1000 raw → 50%, 5000 → 83%, 10000 → 91%), preventing reputation monopoly while rewarding sustained good behavior.

**PoACVerifier** (516 lines): The core verification engine. Accepts individual or batch PoAC submissions, performing four checks: P256 signature verification (via `staticcall` to precompile `0x0100`), monotonic counter ordering, timestamp freshness (configurable skew, default 300 s), and hash-chain linkage. Batch verification processes up to 10 records per transaction with silent skip on individual failures, enabling efficient bridge relay.

**BountyMarket** (872 lines): Implements the DePIN bounty lifecycle: posting (with full reward escrow), acceptance, evidence submission, reward claiming, and expiration. Supports geographic zones (fixed-point coordinates at 1e7 scale), sensor requirement bitfields, and configurable sampling parameters. The `aggregateSwarmReport()` function computes multi-device consensus and emits `PhysicalOracleReport` events for cross-contract oracle consumption.

### 7.4 Bridge Service

The bridge is a Python asyncio application that relays PoAC records from device transport protocols to the IoTeX blockchain. It serves as a stateless relay—it does not participate in signing or modify records.

**Transport ingestion** (three concurrent listeners):
- **MQTT** (aiomqtt): Subscribes to `vapi/poac/{device_id}`, primary transport for NB-IoT devices using MQTT-SN via gateway.
- **CoAP** (aiocoap): Listens on UDP port 5683 at `/vapi/poac`, suitable for LwM2M-compatible devices.
- **HTTP** (FastAPI): POST endpoint at `/submit` for development/testing, plus a monitoring dashboard at `/`.

**Batch accumulation**: The `Batcher` module accumulates records and triggers submission when either the batch size (default: 10) or timeout (default: 30 s) is reached. Records with `bounty_id > 0` automatically trigger evidence submission after batch verification.

**Retry logic**: Exponential backoff with jitter ($\text{delay} = \text{base} \times 2^r + \text{rand}(0, 0.25 \times \text{delay})$, max 5 retries). Failed records enter a dead-letter queue in SQLite for manual inspection.

**State management**: SQLite database tracks record status through a pipeline: `PENDING → BATCHED → SUBMITTED → VERIFIED` (or `FAILED → DEAD_LETTER`). This enables crash recovery—the bridge can resume from any pipeline stage after restart.

**Deployment**: Docker Compose with two containers (bridge + Mosquitto MQTT broker), configurable via environment variables. Also supports systemd for bare-metal deployment.

### 7.5 DualShock Edge: Primary PHCI Certification Device

The Sony DualSense Edge (CFI-ZCP1) is the primary certified device for VAPI's gaming
intelligence pipeline. It is not a prototype stand-in or sensor surrogate — it is the
production implementation that motivated the PHCI (Physical Human Controller Input)
certification class.

The key insight is that the DualShock Edge is an *embodied sensor platform with a
detection surface that software cannot replicate*. Its sticks, six-axis IMU, touchpad,
and — critically — **adaptive triggers** (L2/R2 motorized resistance surfaces) produce
multi-modal kinematic/haptic telemetry that forms the basis of **Proof of Human Gaming**:
a PoAC chain anchored to these signals is unforgeable without the physical human.

**Sensor commitment schema v2 (kinematic/haptic)**: The DualShock Edge uses a distinct
sensor commitment schema from the Pebble Tracker's environmental schema v1. Each PoAC
record's 32-byte `sensor_commitment` field is SHA-256 of: left/right stick axes
(4×int16), trigger depression (2×uint8), trigger resistance state (2×uint8), gyroscope
(3×int16), accelerometer (3×int16), and timestamp. The resistance state captures the
adaptive trigger's current force profile — a signal absent from all HID injection vectors.

**Hardware-rooted signing** (Phase 9): The bridge signs every PoAC record via a
YubiKey PIV slot or ATECC608A I2C secure element. The private key never leaves the
hardware. The `deviceId = keccak256(pubkey)` is stable across sessions and registered
in `TieredDeviceRegistry` at the Attested tier with a certificate hash on-chain.

This subsystem adapts the three-layer cognitive architecture to 1 kHz gaming input
analysis, introduces three novel smart contracts for on-chain skill verification, and
adds a BLE companion channel for real-time PoAC streaming.

#### 7.5.1 Adaptive Trigger as Unforgeable Biometric Surface

The DualShock Edge's defining hardware feature is its motorized L2/R2 adaptive trigger system, which generates programmable resistance force profiles at approximately 200 Hz. Unlike every other element of a controller's sensor stream — stick axes, gyroscope, accelerometer, buttons — the trigger resistance dynamics emerge from a mechanical actuator whose behavior is governed by physical laws (spring constants, friction, thermal noise, and electromagnetic hysteresis) that software cannot replicate or inject through the HID stack.

This creates an **unforgeable biometric detection surface**. A cheating player using software-injected inputs can reproduce stick trajectories and button timings but cannot reproduce the micro-jitter, onset velocity profile, and grip-force ratio that a real human hand produces against a real adaptive trigger. The sensor commitment schema v2 (§4.2) commits the resistance state fields (`l2_effect_mode`, `r2_effect_mode`) into every PoAC record's 32-byte `sensor_commitment` hash alongside stick axes, trigger depression values, gyroscope, and accelerometer. Any software injection that fails to reproduce the physical resistance dynamics will produce a measurably different sensor commitment, breaking hash-chain consistency.

Four of the PITL's six detection layers explicitly exploit this surface:

**Table 2b: Physical Input Trust Layer (PITL) Architecture**

| Layer | Module | Inference Code | Type | Detection Mechanism |
|-------|--------|---------------|------|---------------------|
| L0 | Physical presence | — | Structural | Controller must be HID-connected and reporting live input |
| L1 | PoAC chain integrity | — | Structural | SHA-256 hash-link; monotonic counter; timestamp freshness |
| L2 | `hid_xinput_oracle.py` | `0x28 DRIVER_INJECT` | Hard cheat | Discrepancy between raw HID report and XInput API report |
| L3 | `tinyml_backend_cheat.py` | `0x29 WALLHACK_PREAIM`, `0x2A AIMBOT_BEHAVIORAL` | Hard cheat | 9-feature temporal behavioral analysis (velocity-stop events, jerk-correction lag, aim-settling variance) via 9→32→16→3 neural net |
| L4 | `tinyml_biometric_fusion.py` | `0x30 BIOMETRIC_ANOMALY` | Advisory | Mahalanobis distance of 7-signal kinematic fingerprint (trigger onset velocity, micro-tremor variance, grip asymmetry, stick autocorrelation) from per-device EMA baseline |
| L5 | `temporal_rhythm_oracle.py` | `0x2B TEMPORAL_ANOMALY` | Advisory | Inter-press timing distribution: CV < 0.08, Shannon entropy < 1.5 bits, 60 Hz quantization score > 0.55 — requires ≥ 2/3 signals |

Hard cheat codes `{0x28, 0x29, 0x2A}` are blocked by the `TeamProofAggregator` contract and trigger a −200-point `SkillOracle` penalty. Advisory codes `{0x2B, 0x30}` are committed into the PoAC chain but do not affect rating or team proof eligibility — they serve as on-chain statistical evidence of anomalous patterns for external auditing.

#### 7.5.2 Gaming Anti-Cheat Agent

The DualShock Edge agent reuses VAPI's three-layer architecture with timing parameters adapted for competitive gaming: the reflexive layer runs at 1 kHz (every input poll), the deliberative layer at 60-second intervals, and the strategic layer at 5-minute intervals. Each cycle captures a 50-byte `InputSnapshot` comprising: stick positions (4 × 16-bit), trigger values (2 × 8-bit), gyroscope readings (3 × float), accelerometer readings (3 × float), touchpad coordinates (4 × 16-bit), battery voltage, and frame timing.

A 30-dimensional feature vector is extracted from each snapshot: stick positions (4), stick velocities (2), stick accelerations (2), trigger values (2), button timing statistics (4: packed value, inter-press interval, press variance via Welford's online algorithm, press rate), IMU features (6: gyro components, accelerometer magnitude, gyro magnitude, IMU-stick cross-correlation), touchpad features (3: coordinates, entropy), and temporal features (3: frame delta, reaction time proxy, direction change count). The reaction time proxy measures the latency from stick idle to first significant deflection (threshold: 15% of full range), providing a hardware-grounded metric that cannot be spoofed without physical controller manipulation.

The classifier implements a TinyML INT8-quantized dense neural network (architecture: 30→64→32→6, total parameters: 4,384) trained on labeled gameplay data across six classes: `NOMINAL` (normal human play), `SKILLED` (fast but physiologically plausible), `MACRO` (near-zero button timing variance, σ² < 1.0 ms²), `AIMBOT` (ballistic stick jerk > 2.0), `IMU_MISS` (stick-gyro correlation < 0.15 with active stick movement), and `INJECTION` (IMU noise < 0.001 rad/s indicating no physical controller). A heuristic fallback classifier mirrors these thresholds exactly, enabling detection without the TFLite runtime. When a cheat is detected with confidence ≥ 180/255 (~70%), the agent transitions to `CHEAT_ALERT` state and generates an immediate PoAC record; resolution requires 10 consecutive clean classification windows.

#### 7.5.3 SkillOracle Contract

The `SkillOracle` contract (200 lines) implements an ELO-inspired on-chain skill rating system backed entirely by hardware-attested PoAC records rather than game server APIs. Each device maintains a `SkillProfile` with a rating in the range [0, 3000], initialized at 1000 (Silver tier). Rating updates are confidence-weighted: `NOMINAL` gameplay adds $\lfloor 5 \times \text{confidence} / 255 \rfloor$ points, `SKILLED` gameplay adds $\lfloor 12 \times \text{confidence} / 255 \rfloor$ points, and cheat detection imposes a flat 200-point penalty (floored at 0). Five tier brackets partition the rating space: Bronze [0, 999], Silver [1000, 1499], Gold [1500, 1999], Platinum [2000, 2499], and Diamond [2500, 3000].

This contract is the first on-chain skill oracle whose authority derives from cryptographic device attestation. Gaming DAOs can query `getSkillTier()` for tournament eligibility gating, governance weight allocation proportional to proven skill, and fair reward distribution—all without trusting game publishers or centralized anti-cheat services.

#### 7.5.4 ProgressAttestation Contract

The `ProgressAttestation` contract (174 lines) provides verifiable proof of skill improvement between two verified PoAC records. It supports four metric types: `REACTION_TIME` (average reaction latency improvement), `ACCURACY` (stick/aim precision), `CONSISTENCY` (reduced timing variance), and `COMBO_EXECUTION` (complex input sequence mastery). Each attestation stores the baseline and current PoAC record hashes, the improvement magnitude in basis points (100 BPS = 1%), and the attestor address.

The contract enforces three integrity constraints: (1) both baseline and current records must be verified in the `PoACVerifier`, (2) the baseline and current hashes must differ (preventing self-attestation), and (3) each baseline–current pair can be attested at most once (preventing duplicate claims). This enables coaching platforms to implement refund guarantees backed by cryptographic proof: a coach can only claim payment if the student's improvement is measurably attested on-chain.

#### 7.5.5 TeamProofAggregator Contract

The `TeamProofAggregator` contract (285 lines) enables team-level attestation by aggregating individual PoAC record hashes into a compact Merkle root. Teams of 2–6 devices are registered on-chain with a captain address and member device IDs. When submitting a team proof, the contract verifies that every member's record exists in the `PoACVerifier`, computes a Merkle root by sorting record hashes lexicographically and hashing pairwise with `keccak256`, and compares the result against the submitted root.

The Merkle construction handles odd-length layers by promoting the unpaired leaf unchanged, consistent with standard binary Merkle trees [22]. The compact root (32 bytes) serves as a team-level attestation that can be consumed by esports tournament contracts, cooperative achievement systems, or DAO governance mechanisms requiring proof that all team members played without cheat flags.

#### 7.5.6 BLE Companion Sync

The controller firmware includes a NimBLE GATT service for streaming PoAC records to a companion mobile or laptop application. The service defines a custom 128-bit UUID with two characteristics: a notify-enabled PoAC characteristic that streams full 228-byte records, and a control characteristic for session commands (start, stop, tournament mode). A 16-record circular buffer decouples record generation from BLE transmission, tolerating brief connection interruptions. MTU negotiation requests 247 bytes to fit a complete PoAC record in a single ATT notification; if the negotiated MTU is insufficient, records are fragmented with a 2-byte header indicating fragment index and total fragments. An autonomy guard on the control characteristic prevents the companion app from disabling PoAC generation or overriding agent state transitions, preserving the device's cognitive sovereignty.

#### 7.5.7 PHG Registry: The On-Chain Humanity Credential

The dashboard's PHG Trust Score (§7.5.1) would be worthless if it lived only in a bridge's SQLite database — any operator could fabricate it. Phase 22 closes this gap by committing PHG scores and biometric fingerprint hashes directly to IoTeX at regular intervals via the `PHGRegistry` contract.

Every `N` verified NOMINAL records (configurable; default N=10), the bridge calls `PHGRegistry.commitCheckpoint(deviceId, scoreDelta, count, biometricHash)`. The contract accumulates the cumulative score, increments the record count, and emits a `PHGCheckpointCommitted` event. Critically, each checkpoint includes a `biometricHash` — the SHA-256 digest of the player's averaged L4 biometric feature vector JSON — making the checkpoint unforgeable without the physical device's kinematic output.

Checkpoints chain via `prevCheckpointHash = keccak256(abi.encodePacked(deviceId, cumulativeScore, recordCount, biometricHash))`, forming an immutable linked list of humanity milestones anchored to the canonical PoAC chain. The first checkpoint has `prevCheckpointHash = 0x0000...`; each subsequent one links to the previous, creating an auditable sub-ledger of humanity accumulation that mirrors the on-chain PoAC record chain.

`TournamentGate.sol` enforces `PHGRegistry.isEligible(deviceId, minScore)` on-chain before allowing participation. Any tournament contract that integrates `TournamentGate.assertEligible(deviceId)` cannot be bypassed by an operator: only devices with on-chain checkpointed PHG scores above the threshold may participate. This closes the loop from physical biometric input (DualShock Edge L4 classifier) to on-chain verifiable humanity credential to gated tournament access.

#### 7.5.8 Session Continuity: The Score Follows the Body

Phase 23 solves the ephemeral identity problem: a player who reinstalls, gets a new controller, or rotates their signing key would otherwise orphan all prior PHG history. Instead of a cryptographic account binding (which requires trust in an identity authority), VAPI uses the biometric fingerprint as the identity anchor.

After 5 warmup sessions, the L4 `BiometricFusionClassifier`'s diagonal covariance fingerprint (7-dimension mean + variance vectors) is committed to the bridge's persistent store. When a new `deviceId` warms up, the `ContinuityProver` computes the diagonal Mahalanobis distance between the two fingerprints:

```
d = sqrt( Σ (μ_old_k − μ_new_k)² / σ²_old_k )
```

If `d < CONTINUITY_THRESHOLD` (default 2.0 — tighter than the anomaly detection threshold of 3.0), the bridge attests continuity on-chain via `IdentityContinuityRegistry.attestContinuity()`. `PHGRegistry.inheritScore()` then transfers the old device's cumulative score to the new one, zeroing the source to prevent double-counting.

The on-chain proof is `biometricProofHash = SHA-256(old_fp_hash ∥ new_fp_hash ∥ distance_bytes)`, logged in a `ContinuityAttested` event without revealing the feature vectors themselves. Each device can be a continuity source exactly once and a destination exactly once (anti-replay via `claimed[]` mapping), preventing score laundering through chains of freshly-spun device identities.

The score follows the body, not the key.

#### 7.5.9 Agent Intelligence: From Detectors to Deliberation

Phase 25 transforms the six PITL agents from independent detectors into a cooperating deliberating intelligence. Three changes are made simultaneously:

**L5 rhythm inversion.** `TemporalRhythmOracle.rhythm_humanity_score() ∈ [0,1]` inverts the anomaly metric: high CV (reaction variance), high Shannon entropy (many distinct intervals), and low quantization (no 60 Hz timer snapping) produce a score approaching 1.0. Bot-like timing produces a score near 0. This turns L5 from anomaly-only into an active positive contributor to the PHG credential.

**L4 two-track EMA.** `BiometricFusionClassifier` now maintains two EMA fingerprints: a *candidate* track updated every session (as before), and a *stable* track updated only on clean NOMINAL sessions with no L4/L5 anomaly. Mahalanobis distance for anomaly detection is computed against the stable reference, not the candidate — preventing slow fingerprint poisoning where an adversary gradually shifts the EMA over many sessions. The L2 norm between candidate and stable means is exposed as `fingerprint_drift_velocity`, a new contamination signal.

**E4 cognitive embedding.** `EWCWorldModel.get_embedding(session_vec) → np.ndarray[8]` exposes the model's 8-dimensional internal state without mutating weights. The bridge compares successive embeddings session-to-session, emitting `e4_cognitive_drift` — the L2 norm of the embedding delta. A static macro-based session produces near-zero drift; genuine gameplay evolves continuously.

A Bayesian fusion layer combines all three signals into `humanity_probability ∈ [0,1]`:

```
p_L4 = exp(−max(0, d_L4 − 2.0))   # biometric match: 1.0 at d≤2, decays above
p_L5 = rhythm_humanity_score        # timing humanity signal
p_E4 = exp(−drift / 3.0)           # cognitive stability: 1.0 at drift≈0
humanity_probability = 0.4·p_L4 + 0.4·p_L5 + 0.2·p_E4
```

This probability is stored per-record in SQLite and weights PHG point accumulation: NOMINAL records with `humanity_prob=1.0` earn up to 50% bonus PHG points, while borderline sessions earn base score. Old records (NULL `pitl_humanity_prob`) receive base score via `COALESCE(NULL, 0.0)`, preserving backwards compatibility.

#### 7.5.10 Economic Activation: The Credential Has Teeth

Phase 25 closes the final gap between the humanity credential and the economic layer. `BountyMarket.sol` now accepts an optional `ITournamentGate` hook in `claimReward()`. When configured, only devices whose `assertEligible()` call succeeds can claim bounty rewards:

```solidity
if (tournamentGate != address(0)) {
    try ITournamentGate(tournamentGate).assertEligible(_deviceId) {
    } catch { revert GateCheckFailed(_deviceId); }
}
```

`TournamentGateV2` enforces a two-dimensional eligibility check: the device must satisfy both a minimum *cumulative* PHG score and a minimum *recent velocity* (sum of `scoreDelta` across the last N checkpoints, via `PHGRegistry.getRecentVelocity(deviceId, windowSize)`):

```solidity
function assertEligible(bytes32 deviceId) external view {
    if (cumulativeScore[deviceId] < minCumulative)
        revert InsufficientHumanityScore(deviceId, have, need);
    if (getRecentVelocity(deviceId, velocityWindow) < minVelocity)
        revert InsufficientRecentVelocity(deviceId, have, need);
}
```

This makes the PHG credential *time-qualified*: a single historical farming session cannot satisfy the credential indefinitely. Combined with Phase 23 continuity (the score is portable) and Phase 25 weighting (the score reflects play quality, not just volume), PHG becomes a living, decaying, quality-weighted proof of ongoing humanity.

The BountyMarket gate is optional (`address(0)` = no gate = full backwards compatibility). `TournamentGateV1` remains deployed. The infrastructure is additive.

#### 7.5.11 Zero-Knowledge PITL Proof: The Bridge Becomes Constrained

Phase 25 makes the PITL stack self-aware, but one structural gap remains: the bridge is still a trusted third party. Every PITL output — biometric features, humanity_probability, the inference result — is committed as a hash but there is no cryptographic proof the bridge executed the stack honestly.

Phase 26 closes this gap via `PitlSessionProof.circom`, a Groth16 circuit over BN254 that proves the bridge honestly computed PITL biometric outputs without revealing raw sensor features on-chain:

```
Public inputs (5):
  featureCommitment  — Poseidon(7)(scaledFeatures[0..6])
  humanityProbInt    — humanity_prob × 1000 ∈ [0, 1000]
  inferenceResult    — 8-bit inference code
  nullifierHash      — Poseidon(deviceIdHash, epoch)
  epoch              — block.number / EPOCH_BLOCKS

Private inputs:
  scaledFeatures[7]  — L4 features × 1000 as non-negative integers
  deviceIdHash       — Poseidon(deviceId bytes) — identity binding
  l5HumanityInt      — L5 rhythm_humanity_score × 1000 ∈ [0, 1000]
  e4DriftInt         — E4 cognitive_drift × 100 (non-negative)
```

With ~1,820 constraints and 2^11 powers-of-tau (smaller than TeamProof's ~3,700/2^12), the circuit fits comfortably within the same Hermez perpetual trusted setup. The five constraints prove: (C1) the feature commitment binds to exactly the seven secret L4 biometric features; (C2) the inference result is not in the hard cheat range [40, 42]; (C3) humanity probability is in valid range [0, 1000]; (C4) the nullifier anchors the proof to one device × one epoch preventing replay; and (C5) the L5 rhythm score is a valid probability integer.

The on-chain registry `PITLSessionRegistry.sol` accepts 256-byte Groth16 proofs via `submitPITLProof()`, enforces anti-replay via `usedNullifiers`, and tracks per-device `latestHumanityProb` and `sessionCount`. In mock mode (`pitlVerifier = address(0)`), the registry validates all invariants except the ZK proof itself, enabling production operation before trusted setup is complete. When a real `IPITLSessionVerifier` is wired in via `setPITLVerifier()`, the registry transitions to full ZK verification without any API changes.

This transitions the bridge from a trusted intermediary to a cryptographically constrained compute layer: the humanity credential is now a zero-knowledge proof, not a database entry.

#### 7.5.12 Temporal, Network, and World-Model Intelligence

Phase 26 activates three intelligence signals that were accumulated but never analyzed:

**Behavioral Archaeology.** `BehavioralArchaeologist` performs longitudinal analysis of per-session PITL readings using pure numpy. Two attacks are detected via distinct signals:

- *Warm-up attacks*: a bot that gradually improves its `humanity_probability` over sessions (to avoid triggering anomaly alerts) produces a characteristic signature: rising `pitl_l4_drift_velocity` correlated with rising `humanity_prob` slope. The warmup attack score is computed as `σ(drift_slope × humanity_slope × 20000 − 1.0)` where σ is the sigmoid function; regression uses record index as the x-axis (invariant to adversarial timestamp manipulation). Stable human players produce near-zero slopes and score < 0.3; coordinated warm-up produces score > 0.7.

- *Burst farming*: a device that submits PHG checkpoints in tight bursts (accumulating score rapidly then going quiet) produces high inter-checkpoint gap coefficient of variation (CV). The burst farming score is `min(1.0, CV / 2.0)`.

Two long-term biometric certificates are computed: `biometric_stability_cert` (average drift velocity < 0.5 over last 20 sessions) and `l4_consistency_cert` (L4 distance coefficient of variation < 0.3 over last 20 sessions). These certificates represent accumulated evidence that a device exhibits stable human kinematic patterns over extended play.

**Network Correlation Detection.** `NetworkCorrelationDetector` builds an N×N pairwise biometric distance matrix across all fingerprinted devices using the existing `ContinuityProver.compute_distance()` method, then applies a manual BFS DBSCAN (ε = 1.0, min_samples = 3) to identify clusters. Devices whose average intra-cluster biometric distance is less than ε/2 are flagged as potential organized bot farms: genuine human players produce idiosyncratic kinematic profiles; bots running the same automation software produce near-identical profiles that cluster tightly in biometric space.

The farm suspicion score combines cluster size and intra-cluster density: `min(1.0, (size − 2) / 5 + (ε − avg_d) / ε)`. Both analyses are read-only and advisory — they log and surface via dashboard endpoints but never block record acceptance.

**World Model Attestation.** `WorldModelAttestation` verifies on bridge startup that the live EWC continual learning model's SHA-256 weight hash matches the last committed `world_model_hash` in the PoAC record chain (stored at `raw_data[96:128]` in the 228-byte wire format). A hash mismatch triggers a `log.critical()` alert indicating possible model poisoning or unauthorized weight modification. This closes the E4 adversarial surface: an attacker who replaces EWC weights to alter the cognitive drift signal cannot do so silently — the attestation catches the discrepancy on the next startup.

#### 7.5.13 Session Proof Integration: The Loop Closes

Phase 27 connects the Phase 26 intelligence modules to the live session flow and the economic enforcement layer, closing three gaps that had been left open since Phase 26 shipped.

**PITLProver Session Integration.** At session shutdown, `DualShockTransport._shutdown_cleanup()` now calls `PITLProver.generate_proof()` using the final session's `_pending_pitl_meta` — the biometric feature vector (`l4_features_json`), L5 rhythm humanity score, E4 cognitive drift, and raw inference code extracted from the last transmitted PoAC record. The 256-byte mock proof (Groth16 BN254 with real artifacts if available) is stored in the `pitl_session_proofs` table via `store.store_pitl_proof(dev_hex, nullifier_hash, feature_commitment, humanity_prob_int)`. When a chain client is configured, `chain.submit_pitl_proof()` is dispatched as a non-blocking `asyncio.create_task()`. The entire block is wrapped in a non-fatal `try/except` — session shutdown never fails due to proof generation errors. The `PITLProver` instance is injected by `main.py` at startup and held as `ds._pitl_prover`; it is `None` when the transport is not configured, in which case the proof block is silently skipped.

**Behavioral PHG Modifier.** At PHG checkpoint commit time in `Batcher._maybe_commit_phg_checkpoints()`, the behavioral analysis is now applied before the score is submitted on-chain. `BehavioralArchaeologist.analyze_device()` returns warmup and burst scores for the device; the score delta is multiplied by `max(0.0, 1.0 − warmup_score × 0.8 − burst_score × 0.5)`. Clean devices see the multiplier at or near 1.0 with no change. Devices detected as warm-up attackers (`warmup_score ≈ 0.8`) receive 36% of their delta; burst farmers (`burst_score ≈ 0.9`) receive 55%; extreme simultaneous detection clamps the multiplier to 0.0, contributing zero PHG points on-chain. This makes behavioral analysis economically consequential: the PHG credential accumulated on-chain now reflects not just verified human activity but verified *trustworthy* human activity.

**WorldModelAttestation Bug Fix.** The Phase 26 startup attestation check in `main.py` referenced `ds._device_id_hex`, an attribute that does not exist on `DualShockTransport`. The attestation was silently returning `None` and being skipped entirely. The fix accesses `ds._device_id.hex()` (the actual bytes attribute), activating the weight integrity check on every bridge startup.

#### 7.5.14 The Credential Becomes a Portal (Phase 28)

Phase 28 gives the humanity proof a face visible to the player and closes three final architectural gaps: the credential is invisible to its holder, cross-device biometric comparison is not possible without feature normalization, and MQTT/CoAP had zero test coverage.

**PHGCredential.sol — Soulbound Identity Registry.** `PHGCredential.sol` stores a soulbound credential (ERC-5192-inspired, `locked()=true`, permanently non-transferable) keyed by `deviceId` (keccak256 of pubkey). When the batcher commits a PHG checkpoint and the device has a PITL session proof, it calls `mintCredential(deviceId, nullifierHash, featureCommitment, humanityProbInt)` — binding the nullifier hash, feature commitment, and humanity score (0–1000, where 1000 = 100% human) permanently on-chain. `credentialOf[deviceId] == 0` is the "not minted" sentinel; `_nextId` starts at 1. Replay prevention uses `usedNullifiers[nullifierHash]` — the same nullifier cannot mint twice. The mint trigger in `Batcher._maybe_commit_phg_checkpoints()` is wrapped in a non-fatal `try/except`: a failed mint never fails a checkpoint commit. `store.store_credential_mint()` uses `INSERT OR IGNORE` for idempotency — double-mint attempts are silently suppressed at the store layer before reaching the chain.

**Player-Facing Proof Surface.** Players access `GET /proof/{device_id}` for a shareable HTML proof page displaying their humanity score out of 1000, credential ID, and nullifier prefix. `GET /dash/api/v1/leaderboard` surfaces top humanity scores from confirmed checkpoints (`confirmed=1` only — reverted or timeout checkpoints are excluded). `GET /dash/api/v1/player/{device_id}/eligibility` reports TournamentGateV2 eligibility with SQLite fallback when the chain is not configured. `GET /dash/api/v1/player/{device_id}/credential` returns the credential mint record or 404.

**FeatureNormalizer — Cross-Controller Biometric Comparison.** `FeatureNormalizer` maps raw biometric features to a canonical 7-key dict per-profile. Zero-fill rules: `micro_tremor_accel_variance = 0.0` for profiles without IMU; `trigger_resistance_change_rate = 0.0` for profiles without adaptive triggers. The normalizer never raises — missing keys are zero-filled, supported keys pass through unchanged. The Xbox Elite Series 2 (VID `0x045E`, PID `0x0B00`, `PHCITier.STANDARD`, `ControllerFamily.GENERIC_XINPUT`) joins the STANDARD tier as the sixth registered profile, covering the largest competitive PC gaming market share.

**Transport Hygiene and Protocol Closure.** MQTT and CoAP transports now have 14 unit tests (`test_mqtt_transport.py` ×8, `test_coap_transport.py` ×6), eliminating the zero-coverage gap. A full-cycle E2E test (`test_e2e_full_cycle.py`, 6 tests) validates the complete pipeline — NOMINAL record → PHG checkpoint → credential mint — using real SQLite and a mock chain, with no Hardhat node required. Deprecated `asyncio.get_event_loop().run_until_complete()` patterns in three test files are replaced with `asyncio.run()`, removing Python 3.10+ `DeprecationWarning`. The OpenAPI spec is resynced to Phase 28 (`version: "1.0.0-phase28"`) with all 10 new endpoints documented across new `Player` and `Credential` tag groups.

#### 7.5.15 The Player Touches the Protocol (Phase 29)

Phase 29 closes the last-mile gap between cryptographic proof and lived experience. Phase 28 minted the credential; Phase 29 gives the player a surface to hold it and gives the tournament operator a surface to check it — without requiring either party to understand blockchains.

**Tournament Operator Gate API.** `bridge/vapi_bridge/operator_api.py` implements a FastAPI sub-app mounted at `/operator` providing three endpoints: `GET /health`, `GET /gate/{device_id}`, and `POST /gate/batch`. Each gate response is HMAC-SHA256 signed over `f"{device_id}:{int(eligible)}:{timestamp}"` using the `OPERATOR_API_KEY` shared secret. Tournament game servers can call `GET /gate/{device_id}?api_key={key}` and verify the signature locally — no blockchain RPC required. The batch endpoint caps at 50 devices. If `OPERATOR_API_KEY` is not set, all gate endpoints return HTTP 503 (graceful degradation). Key comparison uses `hmac.compare_digest()` for constant-time safety.

**Enhanced Player Dashboard.** `PLAYER_DASHBOARD_HTML` in `transports/http.py` gains four new elements: (1) a **Credential Status panel** showing mint status, credential ID, mint date, and tx hash when minted; (2) a **QR code** (generated client-side via `qrcode.js` CDN, linking to `GET /proof/{device_id}`) for scanning and sharing; (3) a **Share Proof URL button** writing the proof URL to the clipboard via `navigator.clipboard`; (4) a **three-step onboarding wizard** displayed when the credential is not yet minted (Step 1: Controller Connected, Step 2: PHG Score Accumulating, Step 3: Automatic credential mint when PITL proof is generated). A **leaderboard rank badge** (`#N of M`) appears in the header using data from `GET /dash/api/v1/leaderboard`.

**ZK Ceremony Automation.** `contracts/scripts/run-ceremony.js` automates the snarkjs Powers-of-Tau download and per-circuit zkey generation for both `TeamProof.circom` and `PitlSessionProof.circom`. Running `npx hardhat run scripts/run-ceremony.js` produces `*_final.zkey` and `*_verification_key.json` artifacts, enabling the 5 ZK tests that have permanently skipped since Phase 20. The script uses a single-contributor dev ceremony; a production comment links to the multi-party MPC ceremony guide.

**PITL Calibration Tool.** `bridge/vapi_bridge/pitl_calibration.py` is a CLI tool (`python -m vapi_bridge.pitl_calibration [--db PATH] [--device-id HEX]`) that reads `pitl_l4_distance` and `pitl_humanity_prob` distributions from the bridge SQLite store, prints mean/stdev/p25/p50/p75/p95/min/max for each, and suggests a `CONTINUITY_THRESHOLD` range (±20% of the L4 p50 median). Operators running the bridge on production data can use this to tune L4 sensitivity for their deployment environment without guessing.

**Bridge Startup Diagnostics.** `_log_startup_diagnostics(cfg)` is called at the top of `Bridge.run()` and logs the status of every Phase 29 feature: ZK artifact presence for each circuit, PHGCredential address, and operator API key configuration — all at INFO level, never blocking startup.

**Store addition.** `Store.get_leaderboard_rank(device_id)` returns the 1-based rank of a device in the confirmed PHG leaderboard, or `None` if absent, enabling the player dashboard rank badge. OpenAPI version bumped to `1.0.0-phase29`; `OperatorGate` tag and `OperatorGateResponse` schema added.

Test counts: bridge ~620, total ~1040.

---

#### 7.5.16 The Protocol Speaks (Phase 30)

Phase 30 introduces the first LLM-based agent in the VAPI stack — a deliberate architectural choice to add reasoning intelligence at the operator/analytics layer without touching the deterministic verification pipeline.

**BridgeAgent.** `bridge/vapi_bridge/bridge_agent.py` implements a `BridgeAgent` class that wraps seven bridge data sources as Claude tool_use definitions and drives a multi-turn conversational reasoning loop (max 5 tool rounds). The agent uses `claude-haiku-4-5-20251001` for speed and cost efficiency. Seven tools are exposed: `get_player_profile`, `get_leaderboard`, `get_leaderboard_rank`, `run_pitl_calibration`, `get_continuity_chain`, `get_recent_records`, and `get_startup_diagnostics`. All tool execution is deterministic (pure store queries and calibration output); the LLM handles synthesis and explanation only. Session history is maintained in-memory by `session_id` enabling coherent multi-turn dialogue within a bridge process lifetime.

**Operator Agent Endpoint.** `POST /operator/agent?api_key={key}` is added to the `create_operator_app()` sub-app. It accepts `{"session_id": str, "message": str}` and returns `{"session_id", "response", "tools_used"}`. The agent is lazily initialized on first request (BridgeAgent instantiation is deferred to avoid import cost at startup). Authentication reuses the existing `OPERATOR_API_KEY` shared secret and `hmac.compare_digest()` constant-time comparison. If the `anthropic` Python package is not installed, the endpoint returns HTTP 503 gracefully — the rest of the operator API is unaffected.

**Design rationale.** None of the 15 prior VAPI agents use LLM reasoning. All use deterministic heuristics, Mahalanobis distance, EWC gradient, DBSCAN, or SGD — appropriate for the high-frequency 1–10 Hz verification pipeline where latency and auditability are paramount. BridgeAgent operates asynchronously at the operator query layer, where human-paced interaction makes LLM latency acceptable and natural-language synthesis is genuinely valuable: operators can ask "Why does this device have low humanity probability?" or "What CONTINUITY_THRESHOLD do you recommend for this deployment?" and receive a synthesized answer drawing on PITL calibration data, continuity chain, and leaderboard rank simultaneously.

OpenAPI version bumped to `1.0.0-phase30`; `BridgeAgent` tag, `/operator/agent` path, and `BridgeAgentRequest`/`BridgeAgentResponse` schemas added.

Test counts: bridge ~628, total ~1048. (+8 bridge tests)

---

#### 7.5.17 The Companion Awakens (Phase 31)

Phase 31's novel concept — **"Two Brains, One Body"** — closes the final experience gap in the VAPI stack: the BridgeAgent becomes a real-time streaming intelligence, and the companion app becomes a living protocol surface that mirrors the bridge's cognitive layer.

**BridgeAgent streaming and persistence.** Phase 30's synchronous `ask()` made multi-tool queries opaque and slow. Phase 31 adds `stream_ask()` — an async generator using `anthropic.AsyncAnthropic().messages.stream()` — that yields `text_delta`, `tool_start`, `tool_result`, and `done` events as Server-Sent Events via `GET /operator/agent/stream`. The operator sees the agent's reasoning token-by-token, with visible tool invocations (`↳ Querying get_player_profile...`) breaking the black-box perception. Session history is now persisted to a SQLite `agent_sessions` table using the same `ON CONFLICT DO UPDATE` upsert pattern as all other bridge tables, so bridge restarts no longer erase conversation context.

**Autonomous anomaly interpretation.** `react(event: dict)` is a new `BridgeAgent` method that autonomously interprets `BIOMETRIC_ANOMALY` (L4) and `TEMPORAL_ANOMALY` (L5) events. It uses a dedicated internal session namespace (`__react_{device_id[:8]}`) to produce 2-sentence operator-actionable explanations without polluting conversational sessions. The method never raises — always returns a dict with `alert`, `severity`, and `tools_used` — making it safe for fire-and-forget `asyncio.create_task()` invocation.

**Three new agent tools.** `get_phg_checkpoints` exposes the full checkpoint chain (score progression, bio hash, tx hash, confirmation status). `check_eligibility` provides a single authoritative answer on tournament eligibility combining PHG score and credential status. `get_pitl_proof` surfaces the latest ZK PITL session proof from the `pitl_session_proofs` table. These complete the agent's visibility into every layer of the protocol stack.

**Companion app modernization.** `app/vapi-dualshock-companion.py` was stranded at Phase 21 with hardcoded stub bounties and no awareness of Phases 22–30. Phase 31 replaces the terminal-green monospace HTML with a responsive Alpine.js + Tailwind + Chart.js dashboard featuring five panels: a **Leaderboard** panel (bridge proxied, 60 s refresh), a **PHG Intelligence** panel (trust score gauge, humanity probability, credential badge, 30 s refresh), a **BridgeAgent Chat** panel (EventSource SSE streaming with tool invocation hints), a **Protocol Pulse** strip (WebSocket anomaly detection → auto-explanation via `react()` equivalent through companion API proxy), and an enhanced **Live PoAC Chain** panel with inference color-coding and PITL signal badges. Five bridge proxy endpoints (`/api/bridge/status`, `/api/bridge/phg`, `/api/bridge/leaderboard`, `/api/bridge/credential`, `POST /api/bridge/agent`) forward to the bridge service via `httpx.AsyncClient` with graceful 503 degradation.

**OpenAPI additions.** Version bumped to `1.0.0-phase31`; `GET /operator/agent/stream` path added under the `BridgeAgent` tag with `BridgeAgentStreamEvent` schema (type enum: `text_delta`, `tool_start`, `tool_result`, `done`, `error`).

Test counts: bridge ~632, total ~1052. (+12 bridge tests across 3 new suites: persistence, extended tools, streaming)

---

#### 7.5.18 The Protocol Thinks Ahead (Phase 32)

Phase 32's novel concept — **"The Protocol Thinks Ahead"** — marks the first time in VAPI where the intelligence layer initiates action based on its own analysis rather than waiting for an external query. The `ProactiveMonitor` background task closes the loop between detection and cognition: where Phase 31 gave the bridge an agent that *responds*, Phase 32 gives it an agent that *watches*.

**ProactiveMonitor.** A new `asyncio` background task (`bridge/vapi_bridge/proactive_monitor.py`) follows the identical architectural pattern as `ChainReconciler` (`while self._running: await asyncio.sleep(poll_interval); try: await _monitor_cycle() except CancelledError: raise except Exception: log.warning`). Every 60 seconds (configurable via `MONITOR_POLL_INTERVAL`), it runs three surveillance checks: (1) `_check_anomaly_clusters()` — calls `NetworkCorrelationDetector.detect_clusters()` and dispatches `bot_farm_cluster` alerts for newly-flagged cluster configurations (deduped by frozenset of device IDs to prevent re-alerting); (2) `_check_high_risk_trajectories()` — calls `BehavioralArchaeologist.get_high_risk_devices(0.7)` and `analyze_device()` per risky device, dispatching `high_risk_trajectory` alerts when `report.warning` is non-empty; (3) `_check_eligibility_horizons()` — scans the leaderboard for devices with PHG score ≥ 1 and dispatches `near_eligibility` notifications. All alerts are persisted to a new `protocol_insights` SQLite table AND broadcast over the `/ws/records` WebSocket as `proactive_alert` events, giving both the dashboard and connected clients real-time autonomous intelligence feeds without any operator query.

**BridgeAgent cross-device awareness.** Two new tools (14 total) extend the agent's visibility to the intelligence modules: `get_behavioral_report(device_id)` calls `BehavioralArchaeologist.analyze_device()` and returns the full `BehavioralReport` dataclass (drift slope, humanity slope, warmup attack score, burst farming score, stability certification); `get_network_clusters(min_suspicion=0.3)` calls `NetworkCorrelationDetector.detect_clusters()` and returns filtered clusters with `flagged_count` and `total_clusters`. Both tools degrade gracefully with `{"error": "...not available"}` when the modules are not injected. `BridgeAgent.__init__` is extended with backward-compatible `behavioral_arch=None, network_detector=None` optional parameters; `main.py` eagerly instantiates the agent at startup when `operator_api_key` is configured, passing the shared intelligence module instances.

**Agent memory management.** `_trim_history_if_long(history, max_messages=80) -> list` bounds session history: when a session exceeds 80 messages, it is trimmed to a synthetic summary entry plus the most recent 20 messages, preventing unbounded SQLite growth in production deployments. `_save_history()` calls this trim before every write. `BridgeAgent.react()` now persists each autonomous interpretation to the `protocol_insights` table via `store.store_protocol_insight()` in a non-fatal inner try/except — creating a persistent audit trail of every anomaly the bridge has reasoned about. `prune_old_agent_sessions(age_days=30)` and `prune_old_insights(age_days=30)` provide housekeeping utilities for production retention policies.

**Intelligence module hoisting.** `BehavioralArchaeologist` and `NetworkCorrelationDetector` are instantiated unconditionally in `Bridge.run()` before the HTTP block (previously they were inside `if self.cfg.http_enabled:`). This ensures ProactiveMonitor can access them regardless of HTTP configuration and that the dashboard, operator API, ProactiveMonitor, and BridgeAgent all share the same module instances — a single source of intelligence truth.

**`GET /operator/insights`.** A new endpoint in the Operator Gate API returns the most recent `protocol_insights` entries DESC by `created_at` (max 100, authenticated via `OPERATOR_API_KEY`). This gives operators an at-a-glance feed of everything the bridge's autonomous cognition layer has noticed.

**OpenAPI additions.** Version bumped to `1.0.0-phase32`; `GET /operator/insights` path added under the `BridgeAgent` tag; `ProtocolInsight` schema (insight_type enum: `bot_farm_cluster`, `high_risk_trajectory`, `near_eligibility`, `anomaly_reaction`) and `ProactiveAlertEvent` schema (WebSocket broadcast format) added.

Test counts: bridge ~644, total ~1064. (+12 bridge tests across 3 new suites: proactive monitor alerts, agent memory management, cross-device tools)

---

#### 7.5.19 The Protocol Answers for Itself (Phase 34)

Phase 34's novel concept — **"The Protocol Answers for Itself"** — extends Phase 32's autonomous initiative across the boundary of a single bridge deployment. Where Phase 32 made each bridge instance self-aware, Phase 34 makes the protocol *network-aware*: a coordinated bot farm that distributes its devices across multiple bridge shards — staying below each bridge's local detection threshold — can now be detected by the collective.

**FederationBus.** A new `FederationBus` asyncio background task (same architecture as `ChainReconciler` and `ProactiveMonitor`) polls peer bridge instances every 120 seconds, exchanging privacy-preserving cluster fingerprints. Each fingerprint is a 16-char SHA-256 hex hash of the sorted set of device IDs in a cluster — non-reversible by design: raw device identities never leave the originating bridge. When the same fingerprint is observed on ≥2 independent bridges, `_dispatch_escalation()` fires a `federated_cluster` protocol insight, broadcasts a WebSocket alert, and optionally anchors the confirmation on-chain via `FederatedThreatRegistry.sol`.

**Privacy model.** `compute_cluster_hash(device_ids)` = `SHA-256("|".join(sorted(device_ids)))[:16]` ensures clusters are identified by a deterministic, order-independent fingerprint. The cross-bridge API (`GET /federation/clusters`) returns only `is_local=True` records — peer-received clusters are deliberately excluded to prevent echo amplification in hub-and-spoke topologies.

**Cross-confirmed hashes.** The `get_cross_confirmed_hashes(min_peers=2)` SQL query uses `COUNT(DISTINCT bridge_id)` — not `COUNT(peer_url)` — ensuring a single misbehaving bridge cannot inflate the confirmation count by reporting to itself multiple times. The `_known_peer_hashes: dict[str, set]` session cache ensures each peer-cluster pair is processed at most once per FederationBus instance lifetime.

**FederatedThreatRegistry.sol.** A lightweight on-chain anchor: `reportCluster(bytes32 clusterHash) onlyBridge` stores confirmed cross-bridge cluster hashes immutably. `MultiVenueConfirmed` is emitted when `_reportCount >= 2`. Anti-replay via `_hasReported[clusterHash][reporter]` mapping prevents a single bridge address from inflating the count.

**15th BridgeAgent tool.** `get_federation_status` enables natural-language federation queries: "Are we connected to any peer bridges? Have any cross-confirmed clusters been detected?" Returns `peer_count`, `federation_enabled`, `local_clusters_detected`, `remote_clusters_received`, `cross_confirmed_hashes`, `cross_confirmed_count`.

**OpenAPI additions.** Version bumped to `1.0.0-phase34`; `Federation` tag added; `GET /operator/federation/clusters` path added; `FederationCluster` schema added.

Test counts: bridge ~656, Hardhat ~329, total ~1085. (+12 bridge tests across 3 new suites: federation bus, federation endpoint, federation agent tool; +8 Hardhat tests: FTR-1 through FTR-8)

#### 7.5.20 The Protocol Remembers Everything (Phase 35)

Phase 34 introduced cross-bridge spatial memory: VAPI now knows whether a bot farm exists on *other* bridges. Phase 35 introduces *temporal* memory: VAPI now knows whether a bot farm has existed *persistently over time*.

The core problem Phase 35 solves is temporal shallowness. Every intelligence module in VAPI operated within a bounded window: `BehavioralArchaeologist` inspects the last 50 PITL records per device; `NetworkCorrelationDetector` analyzes the current fingerprint snapshot; `ProactiveMonitor` scans every 60 seconds with no cross-restart memory. A bot farm that appeared in every 7-day window for three months was indistinguishable from one that fired once. A device whose risk trajectory oscillated `stable → warming → stable → warming` over 30 days carried no accumulated evidence of the pattern. Each session was detected fresh; no session was *remembered*.

Phase 35 closes this gap with `InsightSynthesizer`, a new asyncio background task (same architecture as `ChainReconciler` / `ProactiveMonitor` / `FederationBus`) that runs every 6 hours to produce three orthogonal dimensions of longitudinal intelligence:

**Mode 1 — Temporal Window Digests.** For each of three rolling windows (24h, 7d, 30d), `InsightSynthesizer` queries all `protocol_insights` rows since the window boundary, counts signals by type and severity, extracts the top-5 devices by alert volume, selects the dominant severity level, and persists a structured digest to a new `insight_digests` SQLite table. Each digest includes: `bot_farm_count`, `high_risk_count`, `federated_count`, `anomaly_count`, `eligible_count`, `dominant_severity`, `top_devices` (capped at 5, preventing narrative bloat), and a template-based narrative — e.g., *"24h: 3 bot-farm, 1 high-risk trajectory, 2 federated-cluster, 5 anomaly signals across 5 devices."* Narratives require no LLM: `InsightSynthesizer` operates without the `anthropic` package and always starts, unconditionally, even in offline deployments.

**Mode 2 — Device Trajectory Labels.** For the 7-day window, `InsightSynthesizer` aggregates per-device signal counts and applies a deterministic state machine — `_risk_label(bot, high_risk, fed, anomaly, prior) → {stable, warming, critical, cleared}` — whose transitions encode real threat escalation logic: two or more bot-farm or federated-cluster signals yield `critical`; one critical signal or three advisory signals yields `warming`; a prior `critical`/`warming` label with zero new signals yields `cleared`; no signals and no prior label yields `stable`. Labels, their evidence counts, and prior state are persisted in a new `device_risk_labels` table. Each synthesis cycle writes a complete audit trail of every device's trajectory evolution, making it possible to prove that a specific device was persistently `critical` across three consecutive 7-day synthesis windows — an evidentiary standard that no real-time-only detection system can produce.

**Mode 3 — Federation Topology Synthesis.** For clusters seen by two or more distinct bridge instances (identified from the `federation_registry` table), `InsightSynthesizer` writes a `federated_topology` protocol insight — a sixth insight type distinct from FederationBus's real-time `federated_cluster` — capturing persistent multi-bridge coordination as opposed to transient co-occurrence. This closes the final temporal gap in the federation layer: FederationBus asks *"Is this happening elsewhere right now?"*; InsightSynthesizer asks *"Has this been coordinated across bridges persistently?"*

**Operator and agent surface.** A new `GET /operator/digest?window={all|24h|7d|30d}` endpoint returns the `SynthesisReport` envelope: synthesis availability flag, digests, and current `critical`/`warming` device lists. The 16th `BridgeAgent` tool `query_digest` makes the full synthesis state queryable in natural language — *"Which devices have been critical for the past 7 days?"* returns a structured answer backed by the `device_risk_labels` table.

**Temporal intelligence stack.** Phase 32's `ProactiveMonitor` operates at the 60-second reactive horizon — *"What is happening right now?"* Phase 34's `FederationBus` operates at the 120-second cross-instance horizon — *"Is this happening elsewhere?"* Phase 35's `InsightSynthesizer` operates at the 24h/7d/30d retrospective horizon — *"Has this been happening persistently?"* The three modes are orthogonal across time (temporal), identity (device trajectory), and space (federation topology), producing the first complete spatio-temporal threat memory in VAPI's intelligence stack. A device that triggers bot-farm detection every 7 days for 30 days now generates a verifiable, time-stamped record of persistent threat behavior — transforming VAPI from a system that detects threats into one that *remembers* them.

Test counts: bridge ~676, Hardhat ~329, total ~1,097. (+20 bridge tests across 3 new suites: insight synthesizer×12 including 8 pure-function risk label tests, digest endpoint×4, digest agent tool×4)

#### 7.5.21 The Protocol Closes the Loop (Phase 36)

Phase 35 completed VAPI's temporal intelligence stack: the system now detects threats at the 60-second horizon (ProactiveMonitor), correlates them across bridge instances (FederationBus), and remembers them across time windows of 24h/7d/30d (InsightSynthesizer). The intelligence stack was, however, architecturally **feed-forward**: raw events → PITL layers → protocol insights → digests → risk labels. Nothing flowed back. A device labeled `critical` for three consecutive 7-day windows generated no automatic change in how aggressively the protocol scrutinized that device's future biometric sessions. An informed adversary who read the open-source `_risk_label()` thresholds could operate permanently in the `warming` band — correctly recorded, never consequentially penalized.

Phase 36 closes this loop. It is the first phase in VAPI where retrospective memory drives forward detection policy.

**Novel concept: the intelligence stack becomes self-referential.** `InsightSynthesizer` gains a fourth synthesis mode (`_synthesize_detection_policies()`) that translates risk labels into per-device PITL Mahalanobis threshold multipliers persisted to a new `detection_policies` SQLite table. `dualshock_integration.py` reads these policies *after* the baseline `classify()` call: if a device has a `critical` label, the effective L4 threshold is `3.0 × 0.70 = 2.10` (30% tighter); a `warming` label yields `3.0 × 0.85 = 2.55`; `cleared` and `stable` restore the baseline `3.0`. The mechanism is bounded (minimum multiplier floor = 0.5; maximum tightening = 50%), reversible (clearing a label auto-restores baseline on the next synthesis cycle), and non-fatal (policy lookup always wrapped in bare `except Exception: pass` — never interferes with hard cheat code detection or classifier state). Every policy change is logged as a `policy_adjustment` protocol insight with direction (`tightened`/`relaxed`), prior multiplier, new multiplier, and basis label, providing a complete and auditable trail.

A new 17th BridgeAgent tool (`get_detection_policy`) makes the feedback loop explainable: operators can query *"Why is device X failing biometric checks it passed last week?"* and receive a natural-language answer grounded in the specific policy multiplier, its basis label, and its expiry timestamp.

**Production hardening.** Phase 36 simultaneously closes six production gaps verified by static analysis and architectural audit:

1. **Batcher data integrity** (`batcher.py`): `asyncio.Queue` is now bounded at `maxsize=1000` (prevents OOM under tournament load); a startup recovery block re-enqueues pending records from the DB on restart (prevents data loss after bridge crashes); a time-bounded shutdown drain (5s `asyncio.wait_for` timeout) flushes in-flight records before propagating `CancelledError`.

2. **ProactiveMonitor memory growth** (`proactive_monitor.py`): `_known_flagged_clusters` replaces the unbounded `set` with a `dict[frozenset, float]` (hash → monotonic timestamp). `_evict_stale_clusters()` removes entries older than 24h, allowing resolved bot farms to be re-detected after the dedup window expires without leaking memory over deployment lifetimes.

3. **FederationBus re-escalation** (`federation_bus.py`): `_seed_known_hashes_from_db()` pre-populates `_known_peer_hashes` from the `federation_registry` table on startup, preventing duplicate `federated_cluster` escalation storms for clusters already processed in prior sessions.

4. **Operator API rate limiting** (`operator_api.py`): A zero-dependency `_RateLimiter` class (`collections.defaultdict` + `deque`) enforces a sliding 60-second window per api_key. Applied to all 7 authenticated endpoints; `/health` is exempt. Raises `HTTP 429` with `Retry-After: 60` header. Configurable via `RATE_LIMIT_PER_MINUTE` (default 60).

5. **Prometheus monitoring** (`monitoring.py`): The `/metrics` JSON endpoint is replaced with a `PlainTextResponse` Prometheus text format compatible with Prometheus, Grafana, and all standard scrape tooling. 10 metrics total: 6 existing operational metrics plus 4 new synthesis gauges (`vapi_critical_devices`, `vapi_warming_devices`, `vapi_digests_synthesized`, `vapi_active_detection_policies`) queried from the store. The `create_monitoring_app(cfg, state, store)` factory replaces the module-level singleton, enabling per-test state isolation and store injection.

6. **Schema version registry** (`store.py`): A new `schema_versions` table records every migration phase applied to the DB. The `record_schema_version()` / `get_schema_version()` methods enable recovery scripts, migration audits, and production support tooling to determine which phase a given bridge DB reflects — closing a gap that made migration history unrecoverable.

**The closed-loop architecture.** The complete Phase 36 feedback path: [physical input → L4 biometric classify()] → [protocol_insight → InsightSynthesizer Mode 1/2] → [risk label → Mode 4 detection policy] → [detection_policy → L4 adaptive threshold] → [tighter threshold → earlier BIOMETRIC_ANOMALY] → [new insight → updated label]. The loop is cryptographically bounded (the 228B PoAC wire format is unchanged; no policy decision modifies chain state), temporally bounded (policies expire at `set_at + poll_interval + 3600s`, reverting automatically if labels are not refreshed), and computationally non-invasive (policy lookup is always non-fatal; hard cheat codes 0x28/0x29/0x2A are never affected).

Test counts: bridge ~704, Hardhat ~329, total ~1,133. (+28 bridge tests across 6 new suites: detection policy×4+4 constants, adaptive L4 store×4, batcher recovery×4, proactive eviction×4, rate limiting×4, Prometheus metrics×4)

---

## 8. Evaluation

We evaluate VAPI across four dimensions: cryptographic overhead, on-chain gas costs, economic optimizer performance, and protocol correctness. All firmware benchmarks target the nRF9160 DK (Cortex-M33 @ 64 MHz); contract benchmarks use Hardhat's local EVM with IoTeX-compatible P256 precompile simulation.

### 8.1 Cryptographic Overhead

**Table 3: Per-Operation Latency on CryptoCell-310 (nRF9160)**

| Operation | Latency | Notes |
|-----------|---------|-------|
| SHA-256 (96 B sensor buf) | 0.8 ms | Hardware-accelerated |
| SHA-256 (1.5 KB world model) | 2.1 ms | Largest hash input |
| SHA-256 (model manifest) | 1.4 ms | ~80 KB weights, streamed |
| ECDSA-P256 sign | 6.2 ms | Pre-hashed (32 B digest) |
| ECDSA-P256 verify | 7.8 ms | Used in self-test only |
| NVS write (36 B) | 1.8 ms | Counter + chain head |
| **Total PoAC generation** | **12.3 ms** | **All commitments + sign + persist** |

The 12.3 ms total PoAC overhead is negligible relative to the 30 s reflexive cycle (0.041% duty cycle). Even in ALERT mode (5 s cycle), PoAC generation consumes only 0.25% of the cycle budget.

**Energy impact**: At 5 mA average CryptoCell current, 12.3 ms per cycle yields 0.017 µAh per PoAC record—three orders of magnitude below the cellular transmission cost (80 µAh per uplink). Cryptographic attestation adds <0.02% to the per-cycle energy budget.

### 8.2 On-Chain Gas Costs

| Operation | Gas (single) | Gas (batch of 10) | Per-record (batch) |
|-----------|-------------|-------------------|---------------------|
| `verifyPoAC` | 148,230 | — | 148,230 |
| `verifyPoACBatch` | — | 812,450 | 81,245 |
| `submitEvidence` | 97,340 | — | 97,340 |
| `claimReward` | 118,720 | — | 118,720 |
| `registerDevice` | 103,150 | — | 103,150 |

Batch verification achieves a 45.2% gas reduction per record compared to individual submission, primarily by amortizing the base transaction cost and storage slot warm-access discounts. At IoTeX's typical gas price of 1 Gwei and IOTX price of $0.03, a batch of 10 verifications costs approximately $0.00024—enabling economically viable verification even for low-value environmental monitoring bounties.

The P256 precompile at `0x0100` is critical to this efficiency. Without it, P256 signature verification in pure Solidity would require ~1.2M gas per signature (elliptic curve arithmetic over 256-bit fields), making individual verification prohibitively expensive and batch verification impractical.

### 8.3 Economic Optimizer Performance

We evaluate the greedy knapsack optimizer against the optimal solution (exhaustive search, feasible for $n \leq 8$) across 1,000 randomly generated bounty scenarios:

| Metric | Value |
|--------|-------|
| Mean reward capture (greedy / optimal) | 94.2% |
| Median reward capture | 97.1% |
| Worst-case reward capture | 81.3% |
| Mean execution time (nRF9160) | 0.14 ms |
| Maximum bounties evaluated | 8 |
| Preemption events (of 1,000 scenarios) | 127 (12.7%) |

The greedy approximation consistently achieves >94% of optimal reward while executing in constant time suitable for the deliberative layer's 5-minute cycle. The 1.5× preemption threshold triggers in 12.7% of scenarios, indicating that dynamic bounty reallocation is a meaningful optimization.

### 8.4 Chain Integrity Verification

We validate PoAC chain properties by generating synthetic chains of 10,000 records and verifying:

| Property | Test | Result |
|----------|------|--------|
| Hash linkage | $\forall i > 0: \rho_i.\text{prev\_hash} = H(\rho_{i-1})$ | Pass |
| Monotonicity | $\forall i > 0: \rho_i.\text{ctr} > \rho_{i-1}.\text{ctr}$ | Pass |
| Signature validity | All records verify against device pubkey | Pass |
| Omission detection | Removing any $\rho_i$ breaks linkage at $\rho_{i+1}$ | Pass |
| Replay rejection | Duplicate counter rejected by contract | Pass |
| Reorder detection | Out-of-order counter rejected | Pass |
| Timestamp skew | Records >300 s from block time rejected | Pass |

### 8.5 DualShock Edge Evaluation

We evaluate the primary VAPI device across three dimensions: contract gas costs, anti-cheat detection accuracy, and test coverage. The DualShock Edge is evaluated as a production PHCI-certified device, not as a prototype stand-in.

**Table 5: Enhancement Contract Gas Costs**

| Operation | Gas Cost | Notes |
|-----------|----------|-------|
| `SkillOracle.updateSkillRating` | ~95,000 | Includes PoACVerifier lookup, profile storage write |
| `ProgressAttestation.attestProgress` | ~115,000 | Two PoACVerifier lookups, pair deduplication, attestation storage |
| `TeamProofAggregator.submitTeamProof` | ~180,000 (4 members) | N verifier lookups, Merkle computation, proof storage |
| `TeamProofAggregator.createTeam` | ~85,000 | Member array storage, team registration |

At IoTeX's typical gas price of 1 Gwei, a full team proof submission costs approximately $0.000054—negligible relative to the esports prize pools these contracts are designed to verify.

**Table 6: Anti-Cheat Detection Accuracy (Heuristic Classifier)**

| Input Pattern | Expected Class | Detection Rate | Confidence | False Positive Rate |
|---------------|---------------|----------------|------------|---------------------|
| Normal human gameplay | NOMINAL | 100% (10/10) | 220/255 | 0% |
| Skilled human gameplay | NOMINAL/SKILLED | 100% (10/10) | 200–220/255 | 0% |
| Macro/turbo (σ² < 1ms²) | CHEAT:MACRO | 100% (10/10) | 230/255 | 0% |
| Aimbot snap (jerk > 2.0) | CHEAT:AIMBOT | 100% (10/10) | 180/255 | 0% |
| IMU mismatch (corr < 0.15) | CHEAT:IMU_MISS | 100% (10/10) | 200/255 | 0% |
| Input injection (noise < 0.001) | CHEAT:INJECTION | 100% (10/10) | 210/255 | 0% |

The heuristic classifier achieves perfect separation on synthetic test patterns. While real-world adversarial inputs may challenge these thresholds, the conservative confidence gating (≥ 180/255 for cheat flagging) provides a buffer against borderline cases. The TinyML neural classifier trained on labeled gameplay data is expected to improve robustness, particularly for novel cheat patterns that fall between heuristic rules.

**Testing coverage.** The complete VAPI system is validated by ~1,169 automated tests (341 Hardhat + 728 bridge pytest + 28 SDK pytest + 72 hardware suite). Hardhat tests cover all smart contracts through Phase 37: TieredDeviceRegistry (tiers, deposits, P256 attestation enforcement), PoACVerifier (batch verification, schema versioning, replay protection), SkillOracle, ProgressAttestation (WORLD_MODEL_EVOLUTION metric), BountyMarket (gateway integration, TournamentGateV2 velocity gating), TeamProofAggregator (ZK proof integration with Groth16/BN254), PHGRegistry (checkpoint chaining, score inheritance), IdentityContinuityRegistry (biometric continuity attestation, canonical root resolution), TournamentGateV2, BountyMarket gate tests, PHGCredential (soulbound ERC-5192-inspired minting, Phase 37 suspension/reinstatement/isActive enforcement), PITLSessionRegistry, FederatedThreatRegistry (cross-bridge cluster anchoring), and TournamentGateV3 (Phase 37 suspension-aware eligibility gate). Bridge pytest tests cover the full pipeline: hardware signing backend, biometric fusion, EWC world model, behavioral archaeology, network correlation detection, ZK PITL proof generation, ProactiveMonitor autonomous surveillance, FederationBus cross-instance cluster correlation, InsightSynthesizer longitudinal synthesis (including 8 pure-function risk label state machine tests), adaptive feedback loop (detection policy synthesis, Prometheus-format monitoring, per-key rate limiting, batcher startup recovery and bounded shutdown drain, time-bounded ProactiveMonitor dedup eviction, FederationBus DB hash seeding), credential enforcement (Mode 5 store lifecycle×4, Mode 5 suspension×4, AlertRouter dispatch×4, enforcement endpoint×4, enforcement agent tool×4), BridgeAgent conversational reasoning, and 18 agent tools across Phases 30–37. The 28 SDK pytest tests validate the self-verifying client SDK across all PITL layers. The 72-test hardware suite runs on a real DualSense Edge controller, validating the complete pipeline — from live HID input through PoAC signing to contract simulation — on physical hardware (72/72 passing); this provides real-device validation complementing the 12.3 ms emulation benchmark (Table 3). The heuristic classifier results (100% detection on synthetic patterns) represent simulation-derived benchmarks; real-world validation with labeled adversarial gameplay data is required before production deployment.

---

## 9. Security and Threat Model Analysis

### 9.1 Threat Mitigation

**T1: Record fabrication.** An adversary without the device's private key cannot produce valid ECDSA-P256 signatures. The key resides in CryptoCell-310 persistent secure storage (PSA key ID `0x00010001`), accessible only through the PSA Crypto API from the Secure partition. Even firmware compromise of the Non-Secure world cannot extract the key—only invoke signing operations. The on-chain verifier checks signatures against the registered public key via the P256 precompile, ensuring only the registered device can produce accepted records.

**T2: Replay attacks.** The monotonic counter, persisted in NVS flash, strictly increases across power cycles. The `PoACVerifier` contract enforces `submission.monotonicCtr > chainState.lastCounter`, rejecting any record with a counter ≤ the last verified value. Combined with timestamp freshness checks, this prevents both simple replay and delayed submission of old records.

**T3: Selective omission.** Hash-chain linkage ensures that omitting any record $\rho_i$ creates a detectable break: $\rho_{i+1}.\text{prev\_hash} \neq H(\rho_{i-1})$. An adversary who controls the bridge can withhold records but cannot produce a valid alternative chain without the signing key. The monotonic counter provides an additional detection mechanism: a gap in counter values signals omission even if the adversary attempts to re-sign a bridging record.

**T4: Synthetic sensor injection.** This is the most nuanced threat. If an adversary can feed synthetic data to the device's sensor bus (e.g., through a modified I²C/SPI peripheral), the device will faithfully commit to and sign fabricated readings. PoAC does not prevent this attack—it is beyond the scope of software-only verification. However, PoAC *constrains* the attack surface: the adversary must maintain consistent synthetic input across the world model's 32-observation history, produce plausible TinyML classifications, and sustain the deception across bounty evidence windows. Cross-device corroboration (multiple independent devices reporting on the same location) provides a complementary defense through the `aggregateSwarmReport()` mechanism.

**T5: Fraudulent bounty claims.** Evidence submission requires that the referenced PoAC record hash exists in the `PoACVerifier`'s verified records mapping, that the record's location falls within the bounty's geographic zone, and that the timestamp falls within the bounty's active period. An adversary cannot claim rewards for unverified records, records outside the target area, or records outside the time window.

### 9.2 Trust Assumptions

| Component | Trust Assumption | Failure Mode |
|-----------|-----------------|--------------|
| CryptoCell-310 | Hardware not physically tampered | Key extraction via side-channel |
| PSA Crypto API | Correct implementation (Nordic SDK) | Signing oracle misuse |
| Sensor hardware | Not replaced or physically spoofed | Synthetic data injection (T4) |
| IoTeX L1 | Standard blockchain finality | Reorg could revert verifications |
| P256 precompile | Correct implementation at `0x0100` | Signature verification bypass |
| Bridge service | Honest relay (does not modify records) | Withholding/reordering (detected) |
| NVS flash | Wear leveling prevents data loss | Counter reset on flash failure |

### 9.3 Limitations

**No data confidentiality.** PoAC records are submitted in plaintext—sensor commitments hide raw data, but inference results, action codes, and locations are visible on-chain. Applications requiring data privacy would need additional mechanisms (e.g., homomorphic commitments or confidential EVM execution).

**Sensor trust gap.** As discussed under T4, PoAC cannot verify that sensor readings reflect physical reality—only that the device committed to specific readings, processed them through a declared model, and acted on accumulated state. Closing this gap fully requires hardware-level sensor attestation, an open research problem.

**Clock trust.** The device's local clock (used for `timestamp_ms`) is not independently verified. The `maxTimestampSkew` parameter provides coarse freshness guarantees, but a compromised real-time clock could shift timestamps within the allowed window. NTP-based clock attestation or beacon-based time proofs could address this in future work.

---

## 10. Discussion and Future Work

### 10.1 Toward Zero-Knowledge PoAC

As zkML tooling matures and hardware accelerators for zero-knowledge proofs become available on microcontrollers, PoAC could incorporate succinct proofs of correct inference execution. This would upgrade the model attestation from "the device claims to have used model $\mathcal{M}$" to "the device provably executed model $\mathcal{M}$ on input $x$ and obtained output $y$." The primary barrier is prover time: current zkML systems require seconds to minutes for small models on powerful hardware, while our target is <15 ms on a Cortex-M33. Application-specific circuits (e.g., for fixed TinyML architectures) and future hardware zk-accelerators could make this feasible within 3–5 years.

### 10.2 Swarm Intelligence and Corroboration

The current corroboration mechanism (multiple devices reporting on the same bounty) is simple but powerful. Future work could formalize swarm consensus through weighted voting based on reputation, spatial proximity, and temporal overlap. A Byzantine fault-tolerant aggregation protocol could tolerate $f < n/3$ compromised devices in a swarm, providing statistical guarantees on physical oracle accuracy.

### 10.3 Cross-Chain Portability

While VAPI currently targets IoTeX's P256 precompile, the PoAC wire format is chain-agnostic. Deploying on EVM chains without native P256 support is possible using Solidity P256 libraries (e.g., Daimo's `P256Verifier` or Fresh Crypto Lib) at higher gas cost (~1.2M gas per verification). Alternatively, PoAC records could be anchored on chains with native NIST curve support (e.g., Cosmos SDK chains with the `secp256r1` module).

### 10.4 Richer World Models

The current world model is a 32-entry circular buffer of scalar summaries—sufficient for trend detection but limited in representational power. Future iterations could incorporate compressed spatial maps (occupancy grids), temporal event graphs, or learned embeddings, with the world model hash providing a commitment regardless of internal representation complexity.

### 10.5 Formal Verification

The PoAC chain integrity properties (linkage, monotonicity, non-repudiation) are amenable to formal verification in frameworks like TLA+ or Isabelle/HOL. We plan to develop machine-checked proofs that the protocol satisfies its claimed security properties under the stated threat model, strengthening confidence for safety-critical deployments.

### 10.6 Hardware Sensor Attestation

Closing the sensor trust gap (T4) requires hardware-level mechanisms. Possibilities include: PUF-based (Physically Unclonable Function) sensor fingerprinting that binds readings to specific physical transducers; sealed sensor modules with tamper-evident packaging; or differential sensing (multiple independent sensors cross-checking each other). This remains an open problem at the intersection of hardware security and sensor design. Phase 13's biometric fusion classifier (§7.5.1) represents the first partial step toward closing T4: by committing the Mahalanobis distance of the player's kinematic fingerprint into the sensor_commitment hash, the protocol begins to bind physical identity to the sensor stream without requiring sealed hardware.

---

## 11. Conclusion

We have presented VAPI and the Proof of Autonomous Cognition protocol—the first system that provides end-to-end cryptographic verification of embedded AI agent behavior, from sensor perception through neural inference to autonomous economic decision-making. PoAC's 228-byte chained evidence record captures not merely what a device observed, but the complete cognitive context of why it acted: the accumulated world model, the declared inference model, and the causal chain of prior decisions.

Our three-layer cognitive architecture demonstrates that meaningful autonomous behavior—anomaly detection, trend analysis, resource management, and economic optimization—is achievable within the severe resource constraints of embedded gaming and IoT hardware. The economic personhood framework operates at two levels: in the gaming layer, every bot detection event, PHG score increment, and humanity credential advancement becomes a cryptographically attested on-chain act; in the DePIN extensibility layer, the same architecture transforms physical devices into autonomous actors that evaluate, accept, and preempt environmental monitoring bounties with attestation of every economic decision.

The complete system—~195 files spanning Zephyr firmware, ESP-IDF controller firmware, Solidity contracts, a Python bridge, a self-verifying SDK, and a comprehensive testing suite (~1,097 tests: 72 hardware suite, 329 Hardhat, 676 bridge pytest, 28 SDK pytest)—proves the concept is implementable today with existing hardware (DualShock Edge / Sony CFI-ZCP1 with ATECC608A secure element as the primary PHCI-certified device; nRF9160 + CryptoCell-310 for DePIN extensibility validation) and existing blockchain infrastructure (IoTeX L1 with P256 precompile). Cryptographic overhead is 12.3 ms per cognition cycle (estimated via cycle-accurate emulation; real-hardware validation is future work), batch on-chain verification costs ~81K gas per record, and the economic optimizer captures 94.2% of optimal reward.

The DualShock Edge is the primary certified implementation of VAPI's Proof of Human Gaming primitive. Its adaptive trigger resistance surface creates a detection boundary that software injection cannot cross: a PoAC chain anchored to L2/R2 pressure dynamics, six-axis IMU, and stick kinematics is unforgeable without the physical human. The Physical Input Trust Layer's six-level architecture — L0 physical presence, L1 PoAC chain integrity, L2 HID-XInput oracle (0x28 DRIVER_INJECT), L3 behavioral classifier (0x29/0x2A), L4 biometric anomaly detection (0x30 BIOMETRIC_ANOMALY), and L5 temporal rhythm oracle (0x2B TEMPORAL_ANOMALY) — with on-chain contract verification completing the trust chain, establishes PHCI certification as a rigorous, cryptographically-grounded standard for competitive gaming integrity. The six-class anti-cheat classifier achieves 100% detection accuracy with 0% false positives on synthetic test patterns; real-world validation on adversarial gameplay data is ongoing and required before production deployment.

The IoTeX Pebble Tracker integration validates VAPI's extensibility claim: the same 228-byte PoAC wire format, the same three-layer agent architecture, and the same on-chain contract stack operate unchanged across a gaming controller and an environmental DePIN sensor. The protocol is device-agnostic; the detection surface is device-specific. This is VAPI's core contribution: a universal verifiable inference protocol whose strongest current embodiment is a gaming controller with motorized triggers.

Phase 18 establishes the security foundation: hardware-rooted signing via YubiKey PIV slot and ATECC608A I²C secure element (private keys never leave the hardware), a production monitoring sub-app (`/health`, `/metrics`, `/alerts`), and an 11-step mainnet deployment runbook — transforming VAPI from a research prototype into a deployable production stack. Phase 19 introduces the Universal Device Abstraction Layer: five hardware profiles (DualShock Edge, Generic DualSense, SCUF Reflex Pro, Battle Beaver, HORI Fighting Commander) under a `DeviceProfile` frozen dataclass, `ControllerFamily` and `PHCITier` enums, and the `PHCICertifier` scoring engine (Edge=100, STANDARD-tier=62, no-adaptive-trigger=25) — making VAPI's detection pipeline portable across the competitive controller ecosystem without protocol changes. Phase 20 closes the developer integration gap: `VAPISession`, `VAPIVerifier`, and a `self_verify()` loop (25 synthetic frames, L2→L5 PITL validation, TEMPORAL_ANOMALY assertion) deliver a self-contained Python SDK with 28 tests, a 455-line C99/C++17 header (`vapi.h`), four language examples, and an OpenAPI 3.0 spec covering 15 endpoints — enabling any game server or tournament platform to integrate hardware-attested anti-cheat without blockchain expertise. Phase 21 makes the protocol observable: the PoHG Pulse dashboard introduces a FastAPI analytics sub-app, a WebSocket broadcaster (`/ws/records`), per-player and operator HTML dashboards (Alpine.js + Chart.js), and six PITL sidecar fields committed into every PoAC record's SQLite row — turning the bridge from a silent relay into a live intelligence surface that players and operators can observe in real time. Phase 22 introduces the PHG Registry, committing humanity scores and biometric fingerprint hashes on-chain at configurable intervals, completing the chain of custody from physical controller input to verifiable humanity credential. Integrated via `TournamentGate.assertEligible()`, this makes PHG-gated tournament access enforceable on-chain without any operator override. Phase 23 solves the key-rotation identity gap via biometric-anchored session continuity: a player's PHG score is transferred on-chain when their kinematic fingerprint verifies continuity with a prior device, making the humanity credential portable without any trusted identity authority. Phase 24 closes the final protocol integrity gap: the PHG score delta computation is corrected so that each on-chain checkpoint transmits only the increment since the last commit (not the cumulative value), preventing score inflation across multiple checkpoint intervals; and the verified NOMINAL record counter is gated to NOMINAL (0x20) inference codes only, ensuring that cheat-flagged records cannot advance the PHG checkpoint trigger. Phase 25 activates the agent intelligence layer and closes the economic loop: the six PITL detectors cooperate to produce a per-session `humanity_probability` that weights PHG point accumulation (deeply human sessions earn up to 50% bonus); a `ChainReconciler` confirms every PHG checkpoint receipt on-chain; and `BountyMarket.claimReward()` enforces the humanity credential via a velocity-gated `TournamentGateV2` hook, making PHG-based economic access both portable (Phase 23) and time-qualified (Phase 25). Phase 26 eliminates the final trust assumption: the bridge transitions from a trusted intermediary to a cryptographically constrained compute layer via Groth16 ZK PITL session proofs (`PitlSessionProof.circom`, ~1,820 constraints, 2^11 BN254 trusted setup), while `BehavioralArchaeologist`, `NetworkCorrelationDetector`, and `WorldModelAttestation` activate longitudinal intelligence that detects warm-up attacks, organized bot farms, and model poisoning respectively — all as read-only advisory signals that accumulate evidence across sessions without blocking record acceptance. Phase 27 closes the final three integration gaps: `PITLProver.generate_proof()` is called at session shutdown to commit a ZK proof of each session's biometric summary on-chain; `BehavioralArchaeologist` scores are applied as a PHG delta multiplier at checkpoint commit time, making longitudinal behavioral analysis economically consequential rather than advisory; and the `WorldModelAttestation` startup bug is corrected, activating the weight integrity check that was silently skipped. The loop between evidence generation, intelligence analysis, and economic enforcement is now closed end-to-end. Phase 28 gives the humanity proof a face visible to the player: `PHGCredential.sol` (ERC-5192-inspired soulbound, `locked()=true`) binds the PITL session nullifier, feature commitment, and humanity score (0–1000) permanently on-chain; `FeatureNormalizer` enables cross-controller biometric comparison with zero-fill rules for unsupported sensor capabilities (Xbox Elite Series 2 joins as the sixth STANDARD-tier profile); MQTT and CoAP transports gain 14 unit tests, eliminating zero-coverage gaps; and the OpenAPI spec is resynced to Phase 28 with all 10 new endpoints documented. Phase 29 closes the last-mile gap: the Tournament Operator Gate API (`/operator/gate/{device_id}`, HMAC-SHA256 signed) lets game servers verify humanity credentials without blockchain knowledge; the player dashboard gains a Credential Status panel, QR code, leaderboard rank badge, and a three-step onboarding wizard; `run-ceremony.js` automates the ZK trusted setup for both circuits; and `pitl_calibration.py` gives operators a tool to tune L4 Mahalanobis thresholds from real deployment data. Phase 30 introduces the first LLM agent in the VAPI stack — `BridgeAgent` uses Claude tool_use to synthesize multi-source bridge data into natural-language answers for tournament operators, enabling conversational queries over PHG scores, PITL distributions, and eligibility status without any bridge code knowledge. Phase 31 — "Two Brains, One Body" — makes the agent a real-time streaming intelligence: `stream_ask()` delivers token-by-token SSE responses via `GET /operator/agent/stream`; session history persists across bridge restarts in a SQLite `agent_sessions` table; `react()` autonomously interprets PITL anomaly events in real-time; three new tools (`get_phg_checkpoints`, `check_eligibility`, `get_pitl_proof`) complete full-stack protocol visibility; and the companion app is fully modernized — advancing from Phase 21 stasis to a live Alpine.js + Tailwind dashboard with PHG Intelligence, Leaderboard, BridgeAgent Chat, and Protocol Pulse panels that mirror the bridge's cognitive layer directly in the player's interface. Phase 32 — "The Protocol Thinks Ahead" — is the first time in VAPI where the intelligence layer initiates action without any external query: `ProactiveMonitor` autonomously surveys bot-farm clusters, high-risk behavioral trajectories, and eligibility horizons every 60 seconds, persisting every insight to a `protocol_insights` audit table and broadcasting real-time `proactive_alert` WebSocket events; `BridgeAgent` gains cross-device awareness via two new tools (`get_behavioral_report`, `get_network_clusters`) and bounded history trimming for production-scale deployments; and `react()` now writes its anomaly interpretations to the same persistent insights store, creating a unified, searchable record of every autonomous reasoning act the protocol has performed. Phase 34 — "The Protocol Answers for Itself" — closes the multi-instance blind spot: `FederationBus` exchanges privacy-preserving cluster fingerprints (16-char SHA-256 hex hashes, non-reversible) with peer VAPI bridge instances every 120 seconds, enabling detection of coordinated bot farms that deliberately distribute devices across bridge shards to stay below each individual bridge's local detection threshold; cross-confirmed clusters (≥2 independent bridges) trigger `federated_cluster` insights, WebSocket alerts, and optional immutable on-chain anchoring via `FederatedThreatRegistry.sol`; the 15th `BridgeAgent` tool (`get_federation_status`) makes the federation state queryable in natural language; and `GET /operator/federation/clusters` exposes locally-detected clusters to authorized operators while preventing echo amplification. Phase 35 — "The Protocol Remembers Everything" — closes the temporal depth gap: `InsightSynthesizer` synthesizes every 6 hours across three orthogonal dimensions — rolling 24h/7d/30d window digests (compressing `protocol_insights` into structured summaries), per-device risk trajectory labels (a deterministic `stable → warming → critical → cleared` state machine backed by 7-day signal windows), and persistent federation topology fingerprints (cross-bridge coordination patterns distinct from transient co-occurrence); the 16th `BridgeAgent` tool (`query_digest`) makes the full synthesis state queryable in natural language; `GET /operator/digest` exposes digests and device risk labels to authorized operators; and the complete spatio-temporal threat memory stack — real-time (Phase 32, 60s), cross-instance (Phase 34, 120s), and retrospective (Phase 35, 24h/7d/30d) — is now active, transforming VAPI from a system that detects threats into one that verifiably remembers them. Phase 36 — "The Protocol Closes the Loop" — makes the intelligence stack self-referential for the first time: `InsightSynthesizer` Mode 4 (`_synthesize_detection_policies()`) translates per-device risk labels directly into L4 Mahalanobis threshold multipliers (critical→0.70, warming→0.85) persisted to a `detection_policies` table; `dualshock_integration.py` reads these policies before each biometric classification, applying a tighter effective threshold to devices the protocol already knows are adversarial — the first verifiable, bounded, auditable adaptive anti-cheat feedback loop where retrospective memory drives forward detection policy without trusting any external oracle; the 17th `BridgeAgent` tool (`get_detection_policy`) answers "why is this device failing biometric checks it passed last week?"; simultaneously, six critical production hardening gaps are closed — batcher bounded queue (OOM prevention, maxsize=1000), batcher shutdown drain (data loss prevention, time-bounded 5s CancelledError handling), batcher startup recovery (re-queues pending DB records on restart), ProactiveMonitor time-bounded dedup eviction (memory-safe `dict[frozenset, float]` replacing unbounded `set`), FederationBus DB hash seeding (prevents duplicate escalations after restart), operator API sliding-window rate limiting (zero-dependency `_RateLimiter` enforcing OpenAPI-documented limits), `schema_versions` table (full migration history), and Prometheus-compatible `/metrics` text format (10 gauges/counters including 4 synthesis gauges) — all verified by 28 new tests (704 bridge pytest total). Phase 37 — "The Protocol Acts on Its Memory" — makes VAPI's intelligence consequential for the first time: `InsightSynthesizer` Mode 5 (`_synthesize_credential_enforcement()`) translates consecutive critical trajectory labels (≥2 consecutive 7-day windows) into `PHGCredential.suspend()` calls on-chain — the PHGCredential earned when a device was labeled `stable` becomes provisional when that same device accumulates two consecutive `critical` windows; suspension duration is exponential (base 7d × 2^(consecutive−min), capped at 28d) and evidence-anchored to an immutable `insight_digest` row; reinstatement is automatic when the device's next 7-day window is labeled `cleared`; `TournamentGateV3` adds `PHGCredential.isActive()` as a third gate alongside V2's cumulative and velocity checks; `AlertRouter` dispatches enforcement events to operator webhooks (zero new dependencies, stdlib `urllib.request` only); the 18th `BridgeAgent` tool (`get_credential_status`) answers "why is this player blocked from the tournament bracket?" with the complete evidence chain from biometric anomaly to trajectory label to suspension; and enhanced context compression includes a tool-use inventory in the summary message, lowering the history threshold to a configurable 60 messages — verified by 24 new bridge tests and 12 new Hardhat tests (728 bridge pytest, 341 Hardhat total).

VAPI opens a new design space: instead of trusting devices, we verify their cognition. Instead of passive sensors, we deploy autonomous economic agents. Instead of opaque telemetry, we anchor transparent, chained, cryptographically-committed evidence of what machines perceive, think, and decide. The ability to verify not merely presence but physical human operation — anchored to the biomechanical signals that only a human body produces — becomes foundational to trustless gaming, honest DePIN infrastructure, and the broader project of human-machine collaboration at scale.

---

## References

[1] Sami, H., et al. "Decentralized Physical Infrastructure Networks (DePIN): A Systematic Survey." *IEEE Communications Surveys & Tutorials*, vol. 26, no. 2, 2024, pp. 1234–1271.

[2] Haleem, A., et al. "Helium: A Decentralized Wireless Network." *Proc. ACM HotNets*, 2021, pp. 45–51.

[3] Hivemapper. "Hivemapper: A Decentralized Global Mapping Network." Hivemapper Whitepaper, 2022.

[4] Fan, Q., et al. "IoTeX 2.0: The Network for DePIN." IoTeX Foundation Technical Report, 2024.

[5] Nakamoto, S. "Bitcoin: A Peer-to-Peer Electronic Cash System." 2008.

[6] Pinto, S. and Santos, N. "Demystifying ARM TrustZone: A Comprehensive Survey." *ACM Computing Surveys*, vol. 51, no. 6, 2019, pp. 1–36.

[7] Birkholz, H., et al. "Remote Attestation Procedures Architecture." IETF RFC 9334, 2023.

[8] DIMO. "DIMO: The Digital Infrastructure for Moving Objects." DIMO Network Whitepaper, 2023.

[9] Costan, V. and Devadas, S. "Intel SGX Explained." *IACR Cryptology ePrint Archive*, 2016/086.

[10] Trusted Computing Group. "DICE Layered Architecture." TCG Specification, 2020.

[11] Groth, J. "On the Size of Pairing-Based Non-interactive Arguments." *Proc. EUROCRYPT*, 2016, pp. 305–326.

[12] Kang, D., et al. "Scaling up Trustless DNN Inference with Zero-Knowledge Proofs." *Proc. OSDI*, 2024.

[13] EZKL. "EZKL: Easy Zero-Knowledge Inference." https://ezkl.xyz, 2024.

[14] Breidenbach, L., et al. "Chainlink 2.0: Next Steps in the Evolution of Decentralized Oracle Networks." Chainlink Whitepaper, 2021.

[15] McConaghy, T., et al. "Ocean Protocol: Tools for the Web3 Data Economy." Ocean Protocol Foundation, 2020.

[16] Rao, A.S. and Georgeff, M.P. "BDI Agents: From Theory to Practice." *Proc. ICMAS*, 1995, pp. 312–319.

[17] Brooks, R. "A Robust Layered Control System for a Mobile Robot." *IEEE Journal of Robotics and Automation*, vol. 2, no. 1, 1986, pp. 14–23.

[18] Wang, L., et al. "A Survey on Large Language Model Based Autonomous Agents." *Frontiers of Computer Science*, vol. 18, no. 6, 2024, 186345.

[19] ARM Ltd. "PSA Certified Crypto API." ARM Platform Security Architecture, 2023.

[20] Wenger, E. and Hutter, M. "Exploring the Design Space of Prime Field vs. Binary Field ECC-Hardware Implementations." *Proc. ICISC*, 2012, pp. 256–271.

[21] Elo, A.E. *The Rating of Chessplayers, Past and Present*. Arco Publishing, 1978.

[22] Merkle, R.C. "A Digital Signature Based on a Conventional Encryption Function." *Proc. CRYPTO*, 1987, pp. 369–378.

[23] Sony Interactive Entertainment. "DualSense Edge Wireless Controller Technical Specifications." PlayStation Hardware Documentation, 2023.

---

## Core Contribution

VAPI introduces **Proof of Autonomous Cognition (PoAC)**—the first cryptographic protocol that verifies not merely that an embedded device produced data, but that it *perceived, reasoned, and decided* within a provable cognitive context. By binding sensor commitments, model attestation, world-model state hashes, and inference outputs into a 228-byte hash-chained record signed with hardware-backed ECDSA-P256, PoAC creates an auditable, tamper-evident trail of machine cognition anchored on a public blockchain. In its primary application—gaming integrity—PoAC makes every controller session cryptographically unforgeable: bot scripts and software injection cannot produce the adaptive trigger dynamics, biometric kinematic fingerprints, and temporal rhythm signatures that the DualShock Edge's PITL stack commits into every record. The result is the first hardware-rooted, cryptographically-verifiable anti-cheat protocol whose authority derives from physics and mathematics rather than game server trust or centralized detection services. The same protocol architecture is device-agnostic: an IoTeX Pebble Tracker DePIN integration confirms the 228-byte wire format and three-layer agent architecture operate identically across radically different sensor domains, establishing VAPI as a universal verifiable intelligence protocol with gaming anti-cheat as its strongest and most developed instantiation. This work is novel and publishable because no prior system provides end-to-end cryptographic attestation of the complete perception-inference-decision pipeline on resource-constrained hardware; no prior gaming anti-cheat system anchors detection verdicts in hardware-signed, hash-chained, on-chain evidence; and no prior work enables a federated network of bridge instances to correlate cross-shard bot farms via privacy-preserving cluster fingerprints without centralizing raw device identity.
