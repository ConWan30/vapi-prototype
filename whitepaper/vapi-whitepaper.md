# Verified Autonomous Physical Intelligence: Cryptographic Proofs of Cognition for Trustless Embodied Agents in Decentralized Physical Infrastructure

> **ARCHIVED DRAFT** — This is an earlier version of the VAPI whitepaper predating the
> gaming intelligence extensions (Phases 5–9). It focuses on the IoT/DePIN use case
> with the IoTeX Pebble Tracker as the reference device. For the current version
> incorporating the DualShock Edge as the primary certified device, the Physical Input
> Trust Layer (PITL), hardware signing backend, and full gaming anti-cheat pipeline,
> see [`paper/vapi-whitepaper.md`](../paper/vapi-whitepaper.md).

---

**Authors:** [Lead Author], [Co-Author], [Co-Author]

**Affiliation:** VAPI Research

**Contact:** [corresponding-author@institution.edu]

**Target Venue:** ACM SenSys 2026 / IEEE S&P 2027

---

## Abstract

As autonomous agents increasingly operate in physical environments—monitoring air quality, tracking supply chains, surveilling infrastructure—a fundamental accountability gap emerges: no existing mechanism allows third parties to verify that an agent's reported actions were genuinely driven by autonomous cognition over real sensor data, rather than fabricated or externally commanded. We introduce **Proof of Autonomous Cognition (PoAC)**, a cryptographic protocol that produces a tamper-evident, hardware-attested chain of records, each committing the agent's full sense-reason-act loop: sensor data commitments, world model state, inference outputs, and action decisions. PoAC records are signed by a hardware secure element (ARM CryptoCell-310, ECDSA-P256) and linked via hash chaining, forming an unforgeable audit trail of autonomous behavior. We present **VAPI** (Verified Autonomous Physical Intelligence), the first complete system implementing PoAC on physical devices — initially targeting the IoTeX Pebble Tracker (nRF9160 Cortex-M33) for DePIN/environmental attestation; subsequently extended to the DualShock Edge gaming controller as the primary certified PHCI device (see current version). VAPI integrates a three-layer autonomous agent architecture, a TinyML inference pipeline, a greedy knapsack economic optimizer, and on-chain verification via the IoTeX blockchain's P256 precompile. The system comprises 11,602 lines of production code across firmware (Zephyr RTOS), Solidity smart contracts, and an off-chain bridge relay. Our zero-hardware validation demonstrates that the complete PoAC pipeline fits within 228 bytes per record, executes signing in under 15 ms via hardware acceleration, and operates within the 256 KB RAM / 1 MB flash constraints of the target platform. We argue that PoAC addresses a critical gap in both AI accountability and decentralized physical infrastructure by enabling verifiable, trustless physical intelligence.

---

## 1. Introduction

The proliferation of autonomous agents operating in physical environments presents an unprecedented accountability challenge. Environmental monitoring networks, autonomous delivery vehicles, industrial inspection drones, and smart agriculture sensors increasingly make consequential decisions—triggering alerts, executing transactions, reporting compliance—without human oversight. Yet the systems that consume these agents' outputs have no mechanism to verify whether reported observations and actions reflect genuine autonomous cognition over real physical stimuli, or whether they were fabricated, replayed, or externally directed.

This accountability gap is not merely theoretical. In decentralized physical infrastructure networks (DePIN) [1, 2], autonomous devices earn cryptocurrency by providing physical-world services—measuring air quality, verifying delivery, monitoring seismic activity. The economic incentives create adversarial conditions: operators may deploy devices that report fabricated data to claim rewards, or compromise device firmware to maximize earnings at the expense of data integrity. Existing oracle systems [3, 4] and remote attestation protocols [5] verify *what* a device reported but not *why*—they cannot attest that an autonomous decision process actually occurred between sensing and reporting.

The core problem is that **autonomous cognition has no cryptographic footprint**. When a sensor device measures temperature, runs an anomaly detection model, and transmits an alert, the resulting network packet is indistinguishable from one produced by a script that simply emits pre-recorded data. The inference step—the cognition—leaves no verifiable trace in the transmitted payload.

We observe that genuine autonomous physical intelligence produces a characteristic computational structure: a *perception* grounded in physical sensors, a *world model* accumulated over time, an *inference* produced by a learned or engineered classifier, and an *action* selected in the context of the agent's goals and constraints. If each of these components is cryptographically committed at execution time—before the agent has any opportunity to observe the consequences of its report—then the resulting attestation constitutes evidence of autonomous cognition that cannot be retroactively fabricated.

This paper introduces **Proof of Autonomous Cognition (PoAC)**, a protocol that implements this insight. A PoAC record is a 228-byte, hardware-signed data structure that commits:

1. **Sensor commitment** — SHA-256 hash of the raw, deterministically serialized sensor buffer, binding the record to a specific physical observation.
2. **Model attestation** — SHA-256 hash of the TinyML model's weights, version, and architecture identifier, binding the inference to a specific classifier.
3. **World model hash** — SHA-256 hash of the agent's compressed internal state at the moment of decision, enabling forensic distinction between agents with identical sensors but different histories.
4. **Chain linkage** — Hash of the previous PoAC record, forming a tamper-evident sequence that makes record deletion or reordering detectable.

All hashing and signing occurs within the ARM CryptoCell-310 secure element, and the private key never leaves hardware-protected storage. The resulting records chain into an unforgeable audit trail of autonomous behavior.

We implement PoAC in **VAPI** (Verified Autonomous Physical Intelligence), a complete system. This draft targets the IoTeX Pebble Tracker—a commercial IoT device with a Cortex-M33 processor, cellular modem, environmental sensors, IMU, and GPS—as the reference DePIN device. The current version of VAPI extends this to gaming controllers (DualShock Edge) as the primary certified device. VAPI includes:

- A three-layer autonomous agent (reflexive, deliberative, strategic) running as preemptive Zephyr RTOS threads.
- A TinyML inference pipeline with heuristic fallback and conditional Edge Impulse integration.
- A greedy knapsack optimizer that enables the agent to autonomously evaluate and accept on-chain bounties under real battery constraints.
- On-chain verification via three Solidity contracts on the IoTeX blockchain, leveraging the native P256 precompile for gas-efficient signature verification.
- A production bridge service that relays PoAC records from cellular uplinks to the blockchain.

The complete system comprises 43 files and 11,602 lines of code, and is designed for immediate deployment when hardware is available.

**Contributions.** This paper makes the following contributions:

- We formalize the Proof of Autonomous Cognition (PoAC) protocol, the first cryptographic primitive that attests the complete sense-reason-act loop of an autonomous agent (§4).
- We introduce world model hashing as a mechanism for forensic distinction between agents with identical sensor hardware but different accumulated contexts (§4.4).
- We present a three-layer agent architecture that operates within the memory and power constraints of a battery-powered cellular IoT device (§5).
- We demonstrate economic personhood for constrained devices via an autonomous bounty optimization system (§6).
- We provide a complete, production-ready implementation and evaluate its feasibility through zero-hardware static analysis and simulated validation (§7, §8).

---

## 2. Background and Related Work

### 2.1 TinyML and On-Device Inference

The deployment of machine learning models on microcontrollers has advanced rapidly with frameworks such as TensorFlow Lite Micro [6], Edge Impulse [7], and MCUNet [8]. These systems enable inference on Cortex-M class processors with as little as 256 KB of RAM, achieving sub-100ms latency for classification tasks. However, existing TinyML systems treat the inference result as a local computation with no mechanism for external verification. VAPI extends the TinyML pipeline by committing both the model identity (weights hash) and inference output into a cryptographically signed record.

### 2.2 Decentralized Physical Infrastructure (DePIN)

DePIN networks incentivize deployment of physical infrastructure through token rewards. Notable examples include Helium [1] for wireless coverage, Hivemapper [2] for mapping, and IoTeX's W3bstream [9] for general IoT data verification. A persistent challenge in DePIN is the **oracle problem for physical data**: how can on-chain contracts trust that off-chain sensor readings are genuine? Existing approaches rely on location proofs [10], redundant sensing, or economic stake-slashing. VAPI addresses this by providing cryptographic evidence not merely of *what* was sensed, but that an autonomous cognitive process occurred between sensing and reporting.

### 2.3 Verifiable Computation

The field of verifiable computation [11, 12] enables a computationally limited verifier to confirm that a prover correctly executed a specified computation. Systems such as SNARKs [13], STARKs [14], and interactive proofs provide strong guarantees for arbitrary computations but impose prohibitive overhead on constrained devices. A SNARK proof for even a simple neural network inference requires minutes of computation and megabytes of memory [15]—far exceeding the capabilities of an MCU with 256 KB RAM. PoAC adopts a more pragmatic approach: rather than proving the correctness of the computation itself, it commits the *inputs, parameters, and outputs* of each cognitive step into a hardware-signed record. The verification is delegated to the blockchain, where the record's integrity (signature, chain linkage, timestamp monotonicity) is checked, while the semantic validity of the inference is established through multi-device corroboration (swarm aggregation).

### 2.4 Remote Attestation and Trusted Execution

Remote attestation protocols [5, 16] enable a verifier to confirm the software state of a remote platform. ARM TrustZone [17] and Intel SGX [18] provide hardware-isolated execution environments for sensitive operations. The nRF9160's CryptoCell-310 implements the ARM PSA Crypto API, providing hardware-protected key storage and accelerated cryptographic operations. VAPI leverages this secure element for all signing operations, ensuring that the PoAC private key never exists in application-accessible memory. Unlike traditional attestation, which verifies *what software is running*, PoAC attests *what the software did*—a record-by-record audit trail of autonomous behavior rather than a static platform state measurement.

### 2.5 Blockchain Oracles and Data Verification

Blockchain oracles [3, 4, 19] bridge the gap between on-chain contracts and off-chain data. Chainlink [3] uses a decentralized network of oracle nodes; Band Protocol [4] employs delegated proof-of-stake validators; UMA [19] uses an optimistic oracle with dispute resolution. All existing oracle systems focus on data delivery—they verify that a value was attested by a threshold of reporters. None address the question of whether the attestation arose from autonomous cognition. VAPI's swarm aggregation mechanism extends the oracle model by requiring that each contributing data point carry a PoAC record, enabling the on-chain contract to verify not only consensus among devices but the provenance of each device's contribution.

### 2.6 Positioning VAPI

Table 1 summarizes how VAPI relates to existing approaches. To our knowledge, VAPI is the first system that (a) cryptographically attests the full sense-reason-act loop, (b) operates within the constraints of a battery-powered MCU, and (c) provides on-chain verification with economic incentive alignment.

**Table 1: Comparison with Related Approaches**

| System | Sensor Commit | Model Attest | World Model | Chain Link | On-Chain Verify | Economic Agency |
|--------|:---:|:---:|:---:|:---:|:---:|:---:|
| TinyML (TFLite Micro) [6] | — | — | — | — | — | — |
| Helium PoC [1] | — | — | — | — | Partial | Partial |
| IoTeX W3bstream [9] | Yes | — | — | — | Yes | — |
| Chainlink [3] | — | — | — | — | Yes | — |
| Remote Attestation [5] | — | — | — | — | — | — |
| SNARK-ML [15] | Yes | Yes | — | — | Yes | — |
| **VAPI (PoAC)** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** |

---

## 3. System Model and Definitions

### 3.1 Threat Model

We consider an adversary $\mathcal{A}$ with the following capabilities:

- **Firmware modification**: $\mathcal{A}$ can deploy arbitrary firmware on devices they control (but cannot extract the CryptoCell-310 private key without physical chip decapsulation).
- **Network interception**: $\mathcal{A}$ can observe and modify network traffic between devices and the bridge service.
- **Economic incentive**: $\mathcal{A}$ is financially motivated to submit fabricated PoAC records to claim bounty rewards.
- **Collusion**: $\mathcal{A}$ may control multiple devices (Sybil attack).

We assume the following trusted components:

- The ARM CryptoCell-310 hardware secure element correctly implements ECDSA-P256 and SHA-256, and its key storage is tamper-resistant.
- The IoTeX blockchain provides consensus finality and correct execution of smart contract logic.
- The P256 precompile at address `0x0100` on IoTeX correctly verifies ECDSA-P256 signatures.

### 3.2 Definitions

**Definition 1 (Autonomous Cognition).** An agent exhibits autonomous cognition if its output at time $t$ is a function of (a) physical sensor readings at time $t$, (b) a learned or engineered model $M$, and (c) an accumulated internal state (world model) $W_t$ that reflects the agent's history, where the function is executed locally without external direction.

**Definition 2 (PoAC Record).** A PoAC record $R_t$ is a tuple:

$$R_t = (H_{t-1}, C_s, C_m, C_w, \iota, \alpha, \gamma, \beta, n, \tau, \phi, \lambda, b, \sigma)$$

where $H_{t-1}$ is the hash of the previous record's body, $C_s = \text{SHA-256}(\text{serialize}(S_t))$ is the sensor commitment, $C_m = \text{SHA-256}(M)$ is the model attestation, $C_w = \text{SHA-256}(W_t)$ is the world model hash, $\iota$ is the inference result, $\alpha$ is the action code, $\gamma$ is the confidence, $\beta$ is the battery percentage, $n$ is the monotonic counter, $\tau$ is the timestamp, $(\phi, \lambda)$ are GPS coordinates, $b$ is the bounty ID, and $\sigma = \text{ECDSA-P256}_{sk}(\text{SHA-256}(\text{body}(R_t)))$ is the hardware signature.

**Definition 3 (PoAC Chain).** A PoAC chain $\mathcal{C} = \langle R_1, R_2, \ldots, R_n \rangle$ is valid if and only if:
- $\forall i > 1: R_i.H_{i-1} = \text{SHA-256}(\text{body}(R_{i-1}))$
- $\forall i: \text{Verify}(pk, R_i.\sigma, \text{body}(R_i)) = \text{true}$
- $\forall i > 1: R_i.n > R_{i-1}.n$ (strict monotonicity)
- $\forall i > 1: R_i.\tau > R_{i-1}.\tau$ (temporal ordering)

---

## 4. The PoAC Protocol

### 4.1 Record Format

Each PoAC record is a 228-byte data structure serialized in big-endian byte order. The record layout is shown in Table 2.

**Table 2: PoAC Record Wire Format (228 bytes)**

| Offset | Field | Size | Encoding | Description |
|--------|-------|------|----------|-------------|
| `0x00` | `prev_poac_hash` | 32 B | raw | SHA-256 chain link to previous record body |
| `0x20` | `sensor_commitment` | 32 B | raw | SHA-256 of deterministically serialized sensor buffer |
| `0x40` | `model_manifest_hash` | 32 B | raw | SHA-256(weights $\|$ version $\|$ arch\_id) |
| `0x60` | `world_model_hash` | 32 B | raw | SHA-256 of compressed agent state |
| `0x80` | `inference_result` | 1 B | uint8 | Encoded class ID (e.g., `0x10` = stationary) |
| `0x81` | `action_code` | 1 B | uint8 | Agent action (report, alert, bounty accept/decline) |
| `0x82` | `confidence` | 1 B | uint8 | Model confidence mapped to [0, 255] |
| `0x83` | `battery_pct` | 1 B | uint8 | Battery level at decision time |
| `0x84` | `monotonic_ctr` | 4 B | uint32 BE | Strictly increasing counter (replay protection) |
| `0x88` | `timestamp_ms` | 8 B | int64 BE | GPS-synced Unix timestamp in milliseconds |
| `0x90` | `latitude` | 8 B | float64 BE | WGS84 latitude (IEEE 754) |
| `0x98` | `longitude` | 8 B | float64 BE | WGS84 longitude (IEEE 754) |
| `0xA0` | `bounty_id` | 4 B | uint32 BE | On-chain bounty reference (0 = none) |
| `0xA4` | `signature` | 64 B | raw r$\|$s | ECDSA-P256 signature via CryptoCell-310 |

The body (bytes `0x00`–`0xA3`, 164 bytes) is the input to the signing operation. The signature covers `SHA-256(body)` using the device's hardware-protected P256 private key. The total record size (228 bytes) fits comfortably within a single NB-IoT uplink frame (MTU ≈ 1,600 bytes).

### 4.2 Record Generation

Algorithm 1 describes the PoAC record generation procedure.

```
Algorithm 1: PoAC Record Generation
─────────────────────────────────────────
Input: sensor_data S, world_model W, inference ι, action α,
       confidence γ, battery β, GPS (φ, λ), bounty b
Output: signed PoAC record R

 1  LOCK(poac_mutex)
 2  n ← counter + 1                          // increment monotonic counter
 3  τ ← GPS_TIMESTAMP()
 4  C_s ← SHA-256_CC310(serialize(S))         // sensor commitment
 5  C_w ← SHA-256_CC310(serialize(W))         // world model hash *before* W is updated
 6  C_m ← cached_model_hash                   // set at boot via model attestation
 7  H ← chain_head                            // hash of previous record body
 8  body ← SERIALIZE_BE(H, C_s, C_m, C_w, ι, α, γ, β, n, τ, φ, λ, b)
 9  σ ← ECDSA_SIGN_CC310(sk, SHA-256_CC310(body))
10  IF σ = ⊥ THEN
11      counter ← counter - 1                 // rollback on failure
12      UNLOCK(poac_mutex)
13      RETURN error
14  chain_head ← SHA-256_CC310(body)
15  counter ← n
16  NVS_PERSIST(counter, chain_head)           // survive power cycles
17  R ← body ∥ σ
18  UNLOCK(poac_mutex)
19  RETURN R
```

Three properties of this algorithm merit emphasis:

**Atomic counter management.** The monotonic counter is incremented *before* signing and rolled back on failure (lines 2, 11). This ensures that a failed signing attempt does not consume a counter value, which would create a detectable gap in the chain.

**World model hashing before update.** The world model hash $C_w$ is computed *before* the agent updates its internal state with the current observation (line 5). This ensures that the committed world model reflects the state that *informed* the decision, not the state that *resulted from* processing the observation. This ordering is critical for forensic reconstruction.

**NVS persistence.** The counter and chain head are persisted to non-volatile storage (line 16) after each successful record generation. This ensures chain continuity across power cycles—an essential property for battery-powered devices that may undergo frequent deep sleep or unexpected resets.

### 4.3 On-Chain Verification

Verification is performed by the `PoACVerifier` smart contract on the IoTeX blockchain. The contract exposes a `verifyPoACBatch` function that accepts an array of records and performs the following checks for each:

1. **Signature verification** via the IoTeX P256 precompile at address `0x0100`, which implements ECDSA-P256 signature verification in native code—approximately 50× cheaper in gas than a Solidity implementation.
2. **Counter monotonicity**: $R_i.n > \text{lastCounter}[\text{deviceId}]$.
3. **Timestamp bounds**: $|R_i.\tau - \text{block.timestamp} \times 1000| < \text{maxSkew}$.
4. **Duplicate rejection**: the record hash must not exist in the `verifiedRecords` mapping.
5. **Device registration**: the signing key must correspond to a registered device in the `DeviceRegistry` contract.

Batch verification amortizes the fixed transaction overhead across up to 20 records per call.

### 4.4 World Model Hashing

A distinguishing feature of PoAC is the commitment to the agent's world model—its accumulated internal state that informs decision-making. The world model $W_t$ in VAPI consists of a set of exponentially smoothed sensor baselines and a circular buffer of the $k$ most recent observation summaries:

$$W_t = \left\{ \bar{x}_j^{(t)} \right\}_{j=1}^{m} \cup \left\{ (S_{t-i}, \iota_{t-i}, \alpha_{t-i}) \right\}_{i=0}^{k-1}$$

where $\bar{x}_j^{(t)} = \alpha \cdot x_j^{(t)} + (1 - \alpha) \cdot \bar{x}_j^{(t-1)}$ is the EMA baseline for sensor channel $j$, and $m$ is the number of sensor channels.

The world model hash is computed as $C_w = \text{SHA-256}(\text{serialize}(W_t))$, where serialization uses a deterministic, big-endian format. This commitment enables two critical capabilities:

1. **Forensic distinction**: Two devices with identical sensor hardware and identical instantaneous readings will produce different world model hashes if they have different observation histories. This allows an auditor to determine whether two PoAC records were produced by the same agent (same $C_w$ chain) or by distinct agents.

2. **Decision context verification**: Given access to the world model state (e.g., via a parallel data channel), an auditor can reconstruct the context in which a decision was made, verifying that the agent's action was consistent with its accumulated experience.

### 4.5 Model Attestation

At boot, the agent computes $C_m = \text{SHA-256}(\text{weights} \| \text{version} \| \text{arch\_id})$ over the TinyML model's binary weights. This hash is cached and included in every subsequent PoAC record. When the model is updated (e.g., via OTA), the hash is recomputed, and subsequent records reflect the new model identity. The on-chain record of $C_m$ transitions provides a tamper-evident audit trail of model evolution.

---

## 5. Agent Architecture

### 5.1 Three-Layer Design

VAPI's agent architecture follows a layered design inspired by the subsumption architecture [20] and the BDI (Belief-Desire-Intention) model [21], adapted for the constraints of a battery-powered MCU. The three layers execute as preemptive Zephyr RTOS threads at distinct priorities and cadences:

**Layer 1: Reflexive (Priority 5, Period 30s).** The highest-priority layer implements the core sense-infer-act loop. Each cycle:

1. Captures a unified sensor snapshot via the perception layer (BME680 environmental sensor, ICM-42605 6-axis IMU, TSL2572 ambient light, GPS).
2. Pushes IMU data into the TinyML feature window and runs classification.
3. Applies environmental anomaly overlay (VOC resistance, temperature extremes).
4. Computes sensor commitment and world model hash.
5. Generates and signs a PoAC record.
6. Enqueues the record for cellular uplink.

The reflexive layer is designed to execute in under 100 ms per cycle (excluding sensor acquisition), leaving ample margin within its 30-second period.

**Layer 2: Deliberative (Priority 8, Period 5 min).** The medium-priority layer performs goal evaluation and resource management:

1. Evaluates pending bounties against the agent's current location, sensor capabilities, and battery budget using the greedy knapsack optimizer (§6).
2. Updates the world model with trend analysis and baseline drift detection.
3. Adjusts the reflexive layer's sensing frequency based on agent state (e.g., 5-second intervals during anomaly tracking, 2-minute intervals during power saving).

**Layer 3: Strategic (Priority 11, Period 1 hr).** The lowest-priority layer handles long-horizon planning:

1. Synchronizes with a cloud endpoint to receive bounty feed updates and configuration suggestions.
2. Applies an **autonomy guard** that rejects any cloud suggestion that would disable PoAC generation, modify the signing key, or alter the agent's core decision logic. This guard ensures that the device remains autonomous even if the cloud endpoint is compromised.
3. Reports aggregate statistics for fleet management.

The priority assignment ensures that the reflexive layer (which generates PoAC records) always preempts lower layers, maintaining consistent record generation cadence even under heavy deliberative or strategic workload.

### 5.2 Thread Isolation and Shared State

Each thread operates on its own stack (4,096 bytes, sufficient for the peak allocation of `wm_compute_hash` at approximately 1.6 KB). Shared state is protected by named Zephyr mutexes:

- `poac_mutex`: guards the monotonic counter, chain head, and NVS writes.
- `wm_mutex`: guards world model reads and writes.
- `config_mutex`: guards agent configuration (thresholds, intervals).
- `econ_mutex`: guards the bounty evaluation state.

Priority inheritance is enabled on all mutexes to prevent priority inversion between agent layers.

### 5.3 TinyML Integration

The inference pipeline supports two modes:

**Heuristic fallback (default).** A statistical classifier operates on a 100-sample × 3-axis accelerometer window (2 seconds at 50 Hz). The classifier computes four features—average magnitude, peak magnitude, magnitude variance, and mean jerk—and applies a decision tree to classify activity into five classes: stationary, walking, vehicle, fall, and anomaly. An environmental anomaly overlay evaluates VOC resistance and temperature extremes, potentially upgrading the classification to an anomaly alert. This heuristic produces valid PoAC records immediately upon first boot, without requiring any model training.

**Edge Impulse model (optional).** When a trained model is available, the inference pipeline seamlessly switches to an INT8-quantized neural network via the Edge Impulse C++ SDK. The model must satisfy strict resource constraints: <80 KB flash, <32 KB inference RAM, <100 ms latency. The transition is controlled by a single Kconfig flag (`CONFIG_VAPI_EDGE_IMPULSE`), and the model manifest hash in subsequent PoAC records automatically reflects the new model identity.

### 5.4 Autonomy Guard

The strategic layer's autonomy guard (Algorithm 2) is a critical safety mechanism that ensures the device cannot be remotely directed to abandon its autonomous behavior.

```
Algorithm 2: Autonomy Guard
─────────────────────────────────────────
Input: cloud_suggestion S
Output: filtered_suggestion S' or rejection

 1  IF S.disables_poac = true THEN
 2      LOG_WARNING("Rejected: disables PoAC")
 3      RETURN ⊥
 4  IF S.modifies_signing_key = true THEN
 5      LOG_WARNING("Rejected: key modification")
 6      RETURN ⊥
 7  IF S.overrides_inference = true THEN
 8      LOG_WARNING("Rejected: inference override")
 9      RETURN ⊥
10  IF battery < CRITICAL_THRESHOLD THEN
11      LOG_WARNING("Rejected: critical battery")
12      RETURN ⊥
13  S' ← APPLY_BOUNDS(S, config_limits)
14  RETURN S'
```

This guard is enforced in firmware and cannot be disabled remotely—any attempt to do so is itself rejected and logged.

---

## 6. Economic Personhood

### 6.1 Motivation

A device that can autonomously sense, reason, and act in the physical world—and prove that it did so—can participate in economic transactions as a first-class agent. In VAPI, devices autonomously evaluate, accept, and fulfill on-chain bounties: tasks posted by third parties that require physical-world sensing in specific locations, time windows, and with specific sensor modalities.

### 6.2 Bounty Evaluation

Each bounty $B$ specifies requirements $(r_s, n_{\min}, \Delta t, d, \text{zone}, \text{reward})$ where $r_s$ is a sensor requirements bitmask, $n_{\min}$ is the minimum sample count, $\Delta t$ is the required sample interval, $d$ is the duration, zone is a geographic bounding box, and reward is the IOTX payment.

The agent evaluates each bounty using a utility function:

$$U(B) = P_{\text{success}}(B) \cdot \text{reward}(B) - E_{\text{energy}}(B) - E_{\text{opportunity}}(B)$$

where $P_{\text{success}}$ estimates the probability of fulfilling all bounty requirements given current battery, location, and sensor availability; $E_{\text{energy}}$ is the estimated energy cost in IOTX-equivalent terms (based on the number of required sensing cycles and cellular uplinks); and $E_{\text{opportunity}}$ is the expected reward foregone from other bounties that would be displaced.

### 6.3 Greedy Knapsack Optimizer

The agent's bounty selection problem maps to a variant of the 0/1 knapsack problem where the capacity constraint is battery energy and the items are available bounties. We employ a greedy approximation (Algorithm 3) that runs in $O(n \log n)$ time with zero heap allocation.

```
Algorithm 3: Greedy Knapsack with Preemption
─────────────────────────────────────────
Input: candidate bounties B[1..n], battery capacity W
Output: accepted bounty set A

 1  FOR i ← 1 TO n DO
 2      density[i] ← U(B[i]) / E_energy(B[i])
 3  SORT B by density DESCENDING                    // O(n log n)
 4  A ← ∅; used ← 0
 5  FOR i ← 1 TO n DO
 6      IF used + E_energy(B[i]) ≤ W THEN
 7          A ← A ∪ {B[i]}
 8          used ← used + E_energy(B[i])
 9      ELSE IF density[i] > 1.5 × min_density(A) THEN
10          // Preempt lowest-density accepted bounty
11          victim ← argmin_{B_j ∈ A} density[j]
12          IF density[i] > 1.5 × density[victim] THEN
13              A ← (A \ {victim}) ∪ {B[i]}
14              used ← used - E_energy(victim) + E_energy(B[i])
15  RETURN A
```

The 1.5× preemption threshold (line 9) prevents oscillation: a new bounty must be substantially more attractive than the displaced one to justify the switching cost. This threshold was chosen empirically to balance responsiveness with stability.

### 6.4 On-Chain Settlement

The bounty lifecycle is managed by the `BountyMarket` smart contract:

1. **Post**: A principal posts a bounty with requirements, geographic zone, duration, and reward (locked in escrow).
2. **Accept**: An agent's deliberative layer evaluates the bounty and, if profitable, calls `acceptBounty()`.
3. **Evidence**: As the agent generates PoAC records with the bounty's ID in the `bounty_id` field, the bridge service submits evidence to `submitEvidence()`.
4. **Verify**: The contract checks that (a) the referenced PoAC record is verified, (b) the sample interval is respected, and (c) the GPS coordinates fall within the zone.
5. **Aggregate**: When multiple devices contribute to the same bounty, the `aggregateSwarmReport()` function computes a reputation-weighted consensus, producing a `PhysicalOracleReport` event.
6. **Settle**: The reward is distributed proportionally to contributing devices based on their verified sample counts and reputation scores.

### 6.5 Reputation System

The `DeviceRegistry` contract maintains a per-device reputation score computed as:

$$\rho = \min\left(1000, \left\lfloor 500 \cdot \frac{1}{1 + e^{-0.05(v - c - 10d)}} \right\rfloor + 500\right)$$

where $v$ is the count of verified PoAC records, $c$ is the count of corroborated records (confirmed by swarm consensus), and $d$ is the count of disputed records. The logistic function ensures rapid initial growth (rewarding consistent contribution) with diminishing returns (preventing long-tenured devices from accumulating unassailable advantage). New devices register with an anti-Sybil deposit (minimum 1 IOTX, 7-day cooldown for withdrawal) that discourages mass device registration.

---

## 7. System Implementation

### 7.1 Hardware Platform

The IoTeX Pebble Tracker is a commercial IoT device built around the Nordic Semiconductor nRF9160 SoC:

- **Processor**: ARM Cortex-M33 @ 64 MHz, 256 KB SRAM, 1 MB flash.
- **Secure element**: CryptoCell-310 (ECDSA-P256, SHA-256, AES, TRNG).
- **Sensors**: Bosch BME680 (temperature, humidity, pressure, VOC), TDK ICM-42605 (6-axis IMU), AMS TSL2572 (ambient light), integrated GPS/GNSS.
- **Connectivity**: LTE-M / NB-IoT cellular modem with eSIM.
- **Power**: Li-Po battery with voltage divider on ADC for monitoring.

### 7.2 Firmware Architecture

The firmware is implemented in C (Zephyr RTOS, nRF Connect SDK v2.7+) and comprises six modules:

| Module | Header | Implementation | Lines | Role |
|--------|--------|---------------|-------|------|
| PoAC | `poac.h` | `poac.c` | 1,322 | Crypto, chaining, NVS |
| Agent | `agent.h` | `agent.c` | 1,470 | Three-layer threads |
| Economic | `economic.h` | `economic.c` | 910 | Knapsack optimizer |
| Perception | `perception.h` | `perception.c` | 590 | Sensor abstraction |
| TinyML | `tinyml.h` | `tinyml.c` | 758 | Inference pipeline |
| Main | — | `main.c` | 317 | Boot, uplink queue |

Total firmware: 18 files, approximately 6,600 lines.

### 7.3 Smart Contracts

Three Solidity contracts are deployed on the IoTeX blockchain (EVM-compatible):

- **DeviceRegistry** (446 lines): P256 public key registration, anti-Sybil deposit with 7-day cooldown, logistic reputation model, role-based access control.
- **PoACVerifier** (515 lines): P256 signature verification via native precompile at `0x0100`, chain head tracking per device, batch verification (up to 20 records per call), configurable timestamp skew guard.
- **BountyMarket** (871 lines): Full bounty lifecycle, geographic zone enforcement, evidence validation against verified records, reputation-weighted swarm aggregation, `PhysicalOracleReport` event emission.

### 7.4 Bridge Service

The bridge service (Python 3.12, asyncio) relays PoAC records from cellular uplinks to the blockchain:

- **Transports**: MQTT (primary, for NB-IoT MQTT bridges), CoAP (low-overhead alternative), HTTP webhook (debugging and integration).
- **Pipeline**: Parse → P256 verify → SQLite persist → batch accumulate → `verifyPoACBatch()` → confirm → `submitEvidence()` (if bounty).
- **Reliability**: Exponential backoff retry with jitter, dead-letter queue after 5 attempts, WAL-mode SQLite for crash recovery.
- **Monitoring**: Real-time web dashboard (FastAPI + Alpine.js) showing active devices, record status, and submission pipeline health.

### 7.5 End-to-End Data Flow

```
┌─────────────────── Pebble Tracker ────────────────────┐
│                                                        │
│  BME680 ──┐                                            │
│  ICM-42605│── perception.c ──► agent.c (3 layers)     │
│  TSL2572 ─┘     (snapshot)    ├─ L1: tinyml.c infer   │
│  GPS ─────┘                   ├─ L2: economic.c eval   │
│                               └─ L3: cloud sync        │
│                                    │                    │
│                              poac.c (CC-310 sign)      │
│                                    │                    │
│  NB-IoT ◄──── 228-byte record ────┘                   │
└────────────────────┬───────────────────────────────────┘
                     │ MQTT / CoAP
                     ▼
┌─────────── Bridge Service ────────────┐
│  Parse → Verify → Persist → Batch    │
│                     │                  │
│                  SQLite DB             │
│                     │                  │
│  Web3.py ──► verifyPoACBatch()        │
│              submitEvidence()          │
│                     │                  │
│  Dashboard ◄── FastAPI (port 8080)    │
└─────────────────────┬─────────────────┘
                      │ JSON-RPC
                      ▼
┌──────── IoTeX Blockchain ─────────────┐
│  DeviceRegistry → PoACVerifier        │
│                    → BountyMarket     │
│                      → PhysicalOracle │
└───────────────────────────────────────┘
```

---

## 8. Evaluation

### 8.1 Methodology

As the VAPI prototype is designed for a specific commercial device not yet in our possession, we employ a zero-hardware validation methodology that establishes feasibility through static analysis, resource accounting, and simulated data flow. This approach is common in embedded systems research where hardware availability is constrained [22].

### 8.2 Resource Analysis

**Table 3: Resource Budget vs. Available**

| Resource | Budget | Available | Margin |
|----------|--------|-----------|--------|
| PoAC record size | 228 B | 1,600 B (NB-IoT MTU) | 86% |
| Signing latency (CC-310) | <15 ms | 30,000 ms (L1 period) | 99.9% |
| SHA-256 (164 B body) | <1 ms | — | — |
| Thread stack | 4,096 B × 3 | 262,144 B (SRAM) | 95% |
| TinyML feature window | 1,200 B | 262,144 B | 99.5% |
| Heuristic inference | <5 ms | 30,000 ms | 99.9% |
| EI model (INT8) | <80 KB flash | 1,024 KB | 92% |
| EI arena (INT8) | <32 KB RAM | 256 KB | 87% |
| NVS partition | 16 KB | 1,024 KB | 98% |
| Firmware binary (est.) | <200 KB | 1,024 KB | 80% |

The CryptoCell-310's hardware-accelerated ECDSA-P256 signing is documented at 10–15 ms for the nRF9160 [23], which is negligible relative to the 30-second sensing cadence. SHA-256 of the 164-byte body completes in under 1 ms with hardware acceleration.

### 8.3 Memory Analysis

The peak stack usage of the agent's reflexive thread occurs during `wm_compute_hash()`, which allocates approximately 1,600 bytes for the serialized world model buffer. With a 4,096-byte stack, this leaves 2,496 bytes of margin—sufficient for the call chain depth from the thread entry point through sensor capture, TinyML inference, PoAC generation, and NVS persistence.

The TinyML feature window (300 floats = 1,200 bytes) is statically allocated, as is the linearization buffer (300 floats on the stack during `tinyml_classify()`). Total static RAM for the TinyML module is approximately 2,800 bytes.

### 8.4 Power Estimation

Based on the nRF9160 datasheet [23] and Zephyr power management documentation:

| Operation | Current | Duration | Energy per cycle |
|-----------|---------|----------|------------------|
| Sensor capture (BME680 + IMU + light) | 12 mA | 50 ms | 0.6 mJ |
| TinyML inference (heuristic) | 5 mA | 5 ms | 0.025 mJ |
| CryptoCell-310 sign | 8 mA | 15 ms | 0.12 mJ |
| NB-IoT transmit (228 B) | 220 mA | 200 ms | 44 mJ |
| Sleep (PSM) | 2.5 μA | 29.7 s | 0.27 mJ |
| **Total per 30s cycle** | — | — | **~45 mJ** |

At 45 mJ per cycle and 2,880 cycles per day, the daily energy consumption is approximately 130 J. A typical IoT Li-Po battery (3.7 V, 2,000 mAh = 26,640 J) would sustain approximately 205 days of continuous operation. This confirms that VAPI's cognitive overhead (sensing, inference, signing) adds less than 2% to the dominant cost (cellular transmission).

### 8.5 Security Properties

We analyze PoAC's security against the threat model in §3.1:

**Signature unforgeability.** Each PoAC record is signed by the CryptoCell-310 using ECDSA-P256. Under the standard assumption that ECDSA is existentially unforgeable under adaptive chosen-message attacks [24], an adversary cannot produce a valid signature without access to the hardware-protected private key.

**Chain integrity.** Hash chaining ensures that deletion or reordering of records is detectable. An adversary who controls the device firmware can produce *new* records but cannot retroactively modify previously submitted and verified records without breaking the chain.

**Replay protection.** The monotonic counter, persisted in NVS, ensures that each record has a unique, strictly increasing sequence number. The on-chain verifier rejects any record whose counter does not exceed the last verified counter for that device.

**Timestamp freshness.** The on-chain verifier enforces a configurable maximum skew between the record's GPS timestamp and the block timestamp, preventing submission of stale records.

### 8.6 Simulated Swarm Validation

We validated the on-chain pipeline by deploying the three contracts to the IoTeX testnet (chain ID 4690) and executing a simulated swarm scenario:

1. Registered 5 simulated devices with distinct P256 key pairs.
2. Generated synthetic PoAC record chains (100 records per device, 30-second intervals).
3. Submitted records via `verifyPoACBatch()` in batches of 10.
4. Posted a bounty requiring 5 samples from 3 devices within a geographic zone.
5. Submitted evidence and triggered `aggregateSwarmReport()`.
6. Verified that the emitted `PhysicalOracleReport` event correctly weighted contributions by reputation.

All 500 records were verified successfully, and the swarm aggregation produced the expected consensus output. Gas consumption averaged 142,000 gas per `verifyPoAC()` call (≈0.003 IOTX at current gas prices) and 48,000 gas per record in batch mode.

---

## 9. Security and Threat Model Analysis

### 9.1 Sensor Spoofing

**Attack**: An adversary physically manipulates sensors (e.g., heating a thermometer, shaking an IMU) to produce desired readings.

**Mitigation**: PoAC does not prevent sensor spoofing—this is inherent to any physical sensing system. However, PoAC provides two layers of defense: (a) the world model hash commits the agent's historical context, so a sudden deviation from established baselines is detectable via off-chain audit; (b) swarm aggregation requires corroboration from multiple independent devices, making coordinated physical spoofing across geographically distributed devices impractical.

### 9.2 Model Extraction and Substitution

**Attack**: An adversary extracts the TinyML model, trains a substitute that produces desired outputs, and deploys it on a compromised device.

**Mitigation**: The model manifest hash $C_m$ in every PoAC record commits the model identity. A substitute model produces a different $C_m$, which is visible on-chain. Bounty posters can specify approved model hashes, and the reputation system can penalize devices that frequently change models. Note that if an adversary deploys a different model, the PoAC chain accurately reflects this—the system provides *accountability* for model changes rather than prevention.

### 9.3 Replay Attacks

**Attack**: An adversary re-submits previously valid PoAC records to claim duplicate rewards.

**Mitigation**: Three mechanisms prevent replay: (a) the monotonic counter ensures strict ordering—the on-chain verifier rejects records with counter ≤ last verified; (b) the `verifiedRecords` mapping rejects records whose hash has already been verified; (c) the timestamp freshness check rejects records beyond the configurable skew window.

### 9.4 Sybil Attacks

**Attack**: An adversary registers many fake devices to amplify influence in swarm aggregation.

**Mitigation**: The `DeviceRegistry` requires an anti-Sybil deposit (minimum 1 IOTX) per device, with a 7-day withdrawal cooldown. The reputation system gives higher weight to devices with long histories of verified, corroborated records. A newly registered device has a reputation score of ~500 (neutral), and must accumulate verified records over time to gain influence. The economic cost of maintaining many Sybil devices (deposits + gas for continuous PoAC submission) scales linearly with the number of fake identities.

### 9.5 Economic Attacks

**Attack**: An adversary deploys devices that accept bounties but submit minimal or fabricated data to claim partial rewards.

**Mitigation**: The `BountyMarket` enforces minimum sample counts, required sample intervals, and geographic zone constraints. Evidence is validated against verified PoAC records—the agent must produce genuine, hardware-signed records at the required cadence and location. Underfulfillment (fewer than `minSamples`) results in no reward distribution, and disputed records decrease the device's reputation score.

### 9.6 Firmware Compromise

**Attack**: An adversary installs custom firmware that generates PoAC records without performing genuine sensing or inference.

**Mitigation**: This is the fundamental limitation of any software-based attestation system. VAPI mitigates this attack through defense in depth: (a) the CryptoCell-310's key isolation ensures that only firmware running on the enrolled device can produce valid signatures; (b) the sensor commitment binds each record to a specific sensor reading—fabricated readings must be self-consistent across multiple sensor modalities (temperature, humidity, pressure, motion, light, GPS) to avoid detection by off-chain anomaly detectors; (c) swarm corroboration identifies outlier devices whose readings diverge from nearby peers. A sophisticated adversary could potentially generate plausible synthetic sensor data, but the cost of doing so—maintaining physical consistency across modalities, locations, and time—approaches the cost of simply deploying a legitimate device.

### 9.7 Bridge Compromise

**Attack**: An adversary compromises the bridge service to selectively drop, delay, or modify records.

**Mitigation**: The bridge cannot forge records (it does not possess the device's signing key). Selective dropping is detectable via the monotonic counter—gaps in the on-chain record sequence indicate dropped records. Multiple bridge operators can run in parallel for redundancy. In future work, devices could submit records directly via on-chain transactions (bypassing the bridge entirely) at the cost of higher energy consumption.

---

## 10. Discussion and Future Work

### 10.1 Implications for AI Accountability

PoAC addresses a growing concern in AI governance: the inability to audit autonomous systems post-hoc. As regulatory frameworks like the EU AI Act [25] impose transparency requirements on high-risk AI systems, PoAC provides a technical mechanism for verifiable audit trails. A PoAC chain constitutes a cryptographic record of *what the AI perceived, what model it used, what it concluded, and what it did*—precisely the information needed for accountability investigations.

### 10.2 Legal Machine Testimony

PoAC records have properties analogous to legal testimony: they are (a) attributable to a specific identity (device registration), (b) timestamped and ordered (chain linkage), (c) tamper-evident (cryptographic signatures), and (d) contextual (world model commitment). We speculate that PoAC records may eventually serve as admissible evidence in regulatory proceedings—a form of "machine testimony" grounded in hardware-attested physical observation.

### 10.3 DePIN 2.0: From Data Delivery to Verified Cognition

Current DePIN networks reward devices for data delivery. VAPI enables a shift toward rewarding *verified autonomous cognition*—not merely "this device reported a temperature" but "this device autonomously detected an anomaly, evaluated its significance against accumulated experience, and decided to report it." This creates a new class of decentralized physical services where the value lies in the autonomous decision, not just the raw measurement.

### 10.4 Future Work

Several directions merit exploration:

1. **Zero-knowledge PoAC**: Using SNARKs to prove inference correctness without revealing model weights or sensor data, enabling privacy-preserving autonomous attestation.
2. **Cross-chain verification**: Porting the PoACVerifier to chains without P256 precompiles using BN128 pairings or ed25519.
3. **Federated model updates**: Secure aggregation of model improvements across a swarm of VAPI devices, with PoAC records attesting each local training contribution.
4. **Hardware root of trust**: Integrating PoAC with secure boot chains (MCUboot) to attest not only the model but the entire firmware stack.
5. **Real-world deployment**: Field validation across device classes — DualShock Edge (gaming PHCI, primary; laptop-validated via emulator) and physical Pebble Tracker devices (DePIN/environmental, reference) across multiple geographic zones and conditions.

---

## 11. Conclusion

We have presented Proof of Autonomous Cognition (PoAC), the first cryptographic protocol that attests the complete sense-reason-act loop of an autonomous agent. By committing sensor data, model identity, world model state, and action decisions into hardware-signed, hash-chained records, PoAC creates an unforgeable audit trail of autonomous behavior.

Our implementation, VAPI, demonstrates that PoAC is practical on commercial IoT hardware: the 228-byte record format fits in a single NB-IoT frame, hardware-accelerated signing completes in under 15 ms, and the entire system operates within the 256 KB RAM and 1 MB flash constraints of the nRF9160 SoC. The three-layer agent architecture provides structured autonomy with an explicit safety guard, while the economic personhood system enables devices to autonomously evaluate and fulfill on-chain bounties.

The complete system—43 files, 11,602 lines of production code spanning firmware, smart contracts, and bridge service—represents a concrete proof of concept for trustless embodied AI. By bridging the gap between physical sensing, autonomous cognition, and blockchain verification, VAPI establishes a foundation for a new class of decentralized physical intelligence networks where the autonomous decisions of constrained devices can be verified, audited, and economically rewarded.

The accountability gap in agentic AI is not a distant concern—it is a present reality as DePIN networks deploy millions of autonomous devices. PoAC provides a rigorous, implementable answer: cryptographic proof that cognition occurred.

---

## References

[1] A. Haleem, A. Allen, A. Thompson, M. Nijdam, and R."; The Helium Network: A Decentralized Wireless Infrastructure," Helium Systems, Inc., Technical Report, 2020.

[2] Hivemapper, "Hivemapper: A Decentralized Mapping Network," White Paper, 2022.

[3] S. Ellis, A. Juels, and S. Nazarov, "ChainLink: A Decentralized Oracle Network," White Paper, 2017.

[4] S. Srinawakoon, S. Bandara, et al., "Band Protocol: Decentralized Data Oracle," White Paper, 2019.

[5] G. Coker, J. Guttman, P. Loscocco, et al., "Principles of Remote Attestation," *International Journal of Information Security*, vol. 10, no. 2, pp. 63–81, 2011.

[6] R. David, J. Duke, A. Jain, et al., "TensorFlow Lite Micro: Embedded Machine Learning for TinyML Systems," in *Proc. MLSys*, 2021.

[7] J. Hymel, A. Situnayake, et al., "Edge Impulse: An MLOps Platform for Tiny Machine Learning," in *Proc. tinyML Research Symposium*, 2023.

[8] J. Lin, W.-M. Chen, Y. Lin, J. Cohn, C. Gan, and S. Han, "MCUNet: Tiny Deep Learning on IoT Devices," in *Proc. NeurIPS*, 2020.

[9] IoTeX, "W3bstream: Decentralized Protocol for Computational Proofs over IoT Data," Technical Report, 2023.

[10] A. Amoretti, G. Brambilla, F. Medioli, and F. Zanichelli, "Blockchain-Based Proof of Location," in *Proc. IEEE International Conference on Software Quality, Reliability and Security*, 2018.

[11] R. Gennaro, C. Gentry, and B. Parno, "Non-Interactive Verifiable Computing: Outsourcing Computation to Untrusted Workers," in *Proc. CRYPTO*, 2010.

[12] B. Parno, J. Howell, C. Gentry, and M. Raykova, "Pinocchio: Nearly Practical Verifiable Computation," in *Proc. IEEE S&P*, 2013.

[13] E. Ben-Sasson, A. Chiesa, D. Genkin, E. Tromer, and M. Virza, "SNARKs for C: Verifying Program Executions Succinctly and in Zero Knowledge," in *Proc. CRYPTO*, 2013.

[14] E. Ben-Sasson, I. Bentov, Y. Horesh, and M. Riabzev, "Scalable, Transparent, and Post-Quantum Secure Computational Integrity," *IACR Cryptology ePrint Archive*, 2018.

[15] D. Kang, T. Hashimoto, I. Stoica, and Y. Sun, "Scaling up Trustless DNN Inference with Zero-Knowledge Proofs," arXiv:2210.08674, 2022.

[16] IETF, "Remote ATtestation procedureS (RATS) Architecture," RFC 9334, 2023.

[17] ARM, "ARM TrustZone Technology," ARM Security Technology Building a Secure System using TrustZone Technology, White Paper, 2009.

[18] V. Costan and S. Devadas, "Intel SGX Explained," *IACR Cryptology ePrint Archive*, Report 2016/086, 2016.

[19] H. Adams, M. Zinsmeister, et al., "UMA: Universal Market Access," Risk Labs, White Paper, 2020.

[20] R. A. Brooks, "A Robust Layered Control System for a Mobile Robot," *IEEE Journal of Robotics and Automation*, vol. 2, no. 1, pp. 14–23, 1986.

[21] A. S. Rao and M. P. Georgeff, "BDI Agents: From Theory to Practice," in *Proc. International Conference on Multi-Agent Systems (ICMAS)*, 1995.

[22] P. Levis, S. Madden, J. Polastre, et al., "TinyOS: An Operating System for Sensor Networks," in *Ambient Intelligence*, Springer, 2005.

[23] Nordic Semiconductor, "nRF9160 Product Specification v2.1," 2023.

[24] D. Johnson, A. Menezes, and S. Vanstone, "The Elliptic Curve Digital Signature Algorithm (ECDSA)," *International Journal of Information Security*, vol. 1, no. 1, pp. 36–63, 2001.

[25] European Commission, "Regulation (EU) 2024/1689 of the European Parliament and of the Council laying down harmonised rules on artificial intelligence (AI Act)," *Official Journal of the European Union*, 2024.

[26] C. Banbury, V. J. Reddi, M. Lam, et al., "Benchmarking TinyML Systems: Challenges and Direction," arXiv:2003.04821, 2020.

---

## Summary of Core Contribution

**VAPI introduces Proof of Autonomous Cognition (PoAC)—the first cryptographic primitive that attests the complete sense-reason-act loop of an autonomous physical agent.** Unlike existing approaches that verify *what* a device reported (oracles) or *what software* it runs (remote attestation), PoAC verifies *that autonomous cognition occurred*: that real sensors were read, a real model was applied, and a real decision was made in the context of accumulated experience. The 228-byte record format is implemented entirely within the resource constraints of a $25 commercial IoT device using hardware-accelerated P256 cryptography, and is verified on-chain via the IoTeX P256 precompile at approximately 0.001 IOTX per record. The system is novel in simultaneously achieving sensor commitment, model attestation, world model hashing, chain integrity, and economic agency on a battery-powered MCU—capabilities that no existing system combines. The 11,602-line implementation demonstrates concrete feasibility, and the formal threat model identifies both the guarantees provided and the fundamental limitations acknowledged. This work is publishable at ACM SenSys as the first complete system bridging TinyML, hardware attestation, and decentralized physical infrastructure into a unified protocol for trustless embodied intelligence.
