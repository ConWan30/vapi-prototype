# VAPI IoTeX Testnet Deployment Runbook

**Target network:** IoTeX Testnet (chain ID 4690)
**RPC:** `https://babel-api.testnet.iotex.io`
**Explorer:** `https://testnet.iotexscan.io`
**Faucet:** `https://faucet.iotex.io` (~50 IOTX needed for full deployment)

All commands run from `contracts/` unless otherwise noted.

---

## Prerequisites

### 1. Environment
```bash
node --version    # >= 18
npx hardhat --version  # >= 2.19
python --version  # >= 3.10 (bridge)
```

### 2. Install dependencies
```bash
cd contracts && npm install
cd circuits && npm install
```

### 3. Deployer wallet
```bash
# Generate a fresh testnet key (never reuse production keys)
node -e "const { ethers } = require('ethers'); const w = ethers.Wallet.createRandom(); \
  console.log('address:', w.address); console.log('key:', w.privateKey);"

# Fund via faucet — need ~50 IOTX
# https://faucet.iotex.io
```

Set the key in your shell (DO NOT commit):
```bash
export DEPLOYER_PRIVATE_KEY=0x<your-key>
```

### 4. Compile contracts
```bash
cd contracts
npx hardhat compile   # must produce 0 errors; viaIR=true handles PoACVerifier stack depth
```

---

## Phase 0 — ZK Circuit Setup (one-time)

Run ONLY if `contracts/circuits/TeamProof_final.zkey` or
`PitlSessionProof_final.zkey` do not exist. These files are committed to the
repo and can be skipped if present.

```bash
cd contracts/circuits
bash setup.sh
# Generates:
#   TeamProofVerifier.sol         → contracts/contracts/TeamProofVerifier.sol
#   PitlSessionProofVerifier.sol  → contracts/contracts/PitlSessionProofVerifier.sol
#   TeamProof_final.zkey          (KEEP SECRET)
#   PitlSessionProof_final.zkey   (KEEP SECRET)

cd ..
npx hardhat compile   # recompile with new verifier contracts
```

---

## Phase 1 — Verify P256 Precompile

The P256 precompile must be deployed at `address(0x0100)` on the testnet.
Run this before deploying PoACVerifier.

```bash
npx hardhat run scripts/test_p256_precompile.js --network iotex_testnet
# Expected output: "P256 precompile is LIVE at 0x0100"
# If it fails: check IoTeX testnet version; contact IoTeX team
```

---

## Phase 2 — Core Contracts

Deploys: TieredDeviceRegistry, PoACVerifier, BountyMarket, SkillOracle,
ProgressAttestation, TeamProofAggregator.

```bash
npx hardhat run scripts/deploy.js --network iotex_testnet
# Auto-writes addresses to: bridge/.env.testnet (appended)
# Save output — you need these addresses for subsequent phases
```

Record from output:
```
POAC_VERIFIER_ADDRESS=0x...
BOUNTY_MARKET_ADDRESS=0x...
DEVICE_REGISTRY_ADDRESS=0x...
SKILL_ORACLE_ADDRESS=0x...
PROGRESS_ATTESTATION_ADDRESS=0x...
TEAM_AGGREGATOR_ADDRESS=0x...
```

Export for subsequent scripts:
```bash
export POAC_VERIFIER_ADDRESS=0x...
export BOUNTY_MARKET_ADDRESS=0x...
export DEVICE_REGISTRY_ADDRESS=0x...
```

---

## Phase 3 — PHGRegistry (Phase 22)

Deploys: PHGRegistry, TournamentGate V1.

```bash
BRIDGE_ADDRESS=<your-deployer-or-dedicated-bridge-wallet> \
  npx hardhat run scripts/deploy-phase22.js --network iotex_testnet
# Writes: bridge/.env.phase22
```

Record:
```
PHG_REGISTRY_ADDRESS=0x...
```

```bash
export PHG_REGISTRY_ADDRESS=0x...
```

---

## Phase 4 — Identity Continuity Registry (Phase 23)

Deploys: IdentityContinuityRegistry.

```bash
npx hardhat run scripts/deploy-phase23.js --network iotex_testnet
# Writes: bridge/.env.phase23
```

Record:
```
IDENTITY_REGISTRY_ADDRESS=0x...
```

```bash
export IDENTITY_REGISTRY_ADDRESS=0x...
```

---

## Phase 5 — TournamentGateV2 (Phase 25)

Deploys: TournamentGateV2. Wires into BountyMarket.

```bash
npx hardhat run scripts/deploy-phase25.js --network iotex_testnet
# Requires: PHG_REGISTRY_ADDRESS, BOUNTY_MARKET_ADDRESS
# Writes: bridge/.env.phase25
```

---

## Phase 6 — PITLSessionRegistry (Phase 26)

Deploys: PITLSessionRegistry in mock mode (no ZK verifier yet).

```bash
npx hardhat run scripts/deploy-phase26.js --network iotex_testnet
# Writes: bridge/.env.phase26
```

Record:
```
PITL_SESSION_REGISTRY_ADDRESS=0x...
```

```bash
export PITL_SESSION_REGISTRY_ADDRESS=0x...
```

---

## Phase 7 — PHGCredential (Phase 28)

Deploys: PHGCredential soulbound ERC-5192 credential registry.

```bash
npx hardhat run scripts/deploy-phase28.js --network iotex_testnet
# Writes: bridge/.env.phase28
```

Record:
```
PHG_CREDENTIAL_ADDRESS=0x...
```

```bash
export PHG_CREDENTIAL_ADDRESS=0x...
```

---

## Phase 8 — FederatedThreatRegistry (Phase 34)

Deploys: FederatedThreatRegistry for cross-bridge cluster anchoring.

```bash
npx hardhat run scripts/deploy-phase34.js --network iotex_testnet
# Writes: bridge/.env.phase34
```

Record:
```
FEDERATED_THREAT_REGISTRY_ADDRESS=0x...
```

---

## Phase 9 — TournamentGateV3 (Phase 37) ← NEW

Deploys: TournamentGateV3 (suspension-aware). Optionally wires to BountyMarket.

```bash
# Gate parameters (adjust as needed for your tournament configuration)
export GATE_MIN_CUMULATIVE=100
export GATE_MIN_VELOCITY=20
export GATE_VELOCITY_WINDOW=3

npx hardhat run scripts/deploy-phase37.js --network iotex_testnet
# Requires: PHG_REGISTRY_ADDRESS, PHG_CREDENTIAL_ADDRESS
# Optional: BOUNTY_MARKET_ADDRESS (for BountyMarket wiring)
# Writes: bridge/.env.phase37
```

Record:
```
TOURNAMENT_GATE_V3_ADDRESS=0x...
```

---

## Phase 10 — ZK Verifiers (Optional — enables live ZK proofs)

### TeamProofVerifier

```bash
TEAM_AGGREGATOR_ZK_ADDRESS=0x... \
  npx hardhat run scripts/deploy-verifier.js --network iotex_testnet
# Requires: TeamProofVerifier.sol (from setup.sh)
# Wires into TeamProofAggregatorZK
# Writes: bridge/.env.verifier
```

### PitlSessionProofVerifier

```bash
npx hardhat run scripts/deploy-pitl-verifier.js --network iotex_testnet
# Requires: PitlSessionProofVerifier.sol (from setup.sh), PITL_SESSION_REGISTRY_ADDRESS
# Wires into PITLSessionRegistry (one-time operation)
# NOTE: After this, PITLSessionRegistry requires real Groth16 proofs
# Writes: bridge/.env.pitl-verifier
```

---

## Phase 11 — Bridge Configuration

### Merge all env files into bridge/.env

```bash
cd bridge

# Start from the testnet template
cp .env.testnet .env

# Fill in addresses from each phase's output
# Edit .env and replace all 0x<...> placeholders with real addresses:
# - From deploy.js:            POAC_VERIFIER_ADDRESS, BOUNTY_MARKET_ADDRESS,
#                              DEVICE_REGISTRY_ADDRESS, SKILL_ORACLE_ADDRESS,
#                              PROGRESS_ATTESTATION_ADDRESS, TEAM_AGGREGATOR_ADDRESS
# - From deploy-phase22.js:    PHG_REGISTRY_ADDRESS
# - From deploy-phase23.js:    IDENTITY_REGISTRY_ADDRESS
# - From deploy-phase26.js:    PITL_SESSION_REGISTRY_ADDRESS
# - From deploy-phase28.js:    PHG_CREDENTIAL_ADDRESS
# - From deploy-phase34.js:    FEDERATED_THREAT_REGISTRY_ADDRESS
# - From deploy-phase37.js:    TOURNAMENT_GATE_V3_ADDRESS

# Set your bridge wallet key (separate from deployer key, must be funded)
# echo "BRIDGE_PRIVATE_KEY=0x<your-bridge-key>" >> .env
```

### Validate all addresses are set

```bash
python3 - << 'EOF'
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('bridge/.env')

required = [
    'POAC_VERIFIER_ADDRESS',
    'BOUNTY_MARKET_ADDRESS',
    'DEVICE_REGISTRY_ADDRESS',
    'PHG_REGISTRY_ADDRESS',
    'IDENTITY_REGISTRY_ADDRESS',
    'PITL_SESSION_REGISTRY_ADDRESS',
    'PHG_CREDENTIAL_ADDRESS',
    'FEDERATED_THREAT_REGISTRY_ADDRESS',
    'TOURNAMENT_GATE_V3_ADDRESS',
    'BRIDGE_PRIVATE_KEY',
]

missing = []
placeholder = []
for key in required:
    val = os.getenv(key, '')
    if not val:
        missing.append(key)
    elif '<' in val or 'YOUR' in val.upper():
        placeholder.append(key + '=' + val[:40])

if missing:
    print('MISSING env vars:', missing)
if placeholder:
    print('PLACEHOLDER (unfilled):', placeholder)
if not missing and not placeholder:
    print('All required env vars are set.')
EOF
```

### Copy ZK artifacts to bridge

```bash
mkdir -p bridge/zk_artifacts
cp contracts/circuits/TeamProof_js/TeamProof.wasm         bridge/zk_artifacts/
cp contracts/circuits/TeamProof_final.zkey                 bridge/zk_artifacts/
cp contracts/circuits/verification_key.json                bridge/zk_artifacts/
cp contracts/circuits/PitlSessionProof_js/PitlSessionProof.wasm  bridge/zk_artifacts/
cp contracts/circuits/PitlSessionProof_final.zkey          bridge/zk_artifacts/
cp contracts/circuits/PitlSessionProof_verification_key.json     bridge/zk_artifacts/
```

---

## Phase 12 — Bridge Startup + E2E Validation

### Start bridge

```bash
cd bridge
pip install -r requirements.txt  # if not already done
python -m vapi_bridge.main
# Watch for: "All services started"
# Watch for: PoAC records submitted (tx hashes in logs)
```

### E2E test (requires running Hardhat node OR testnet RPC)

```bash
# Against testnet
HARDHAT_RPC_URL=https://babel-api.testnet.iotex.io \
  python -m pytest bridge/tests/test_e2e_simulation.py -v
```

### Smoke test all bridge agents

```bash
cd bridge/..
python scripts/test_bridge_agents_live.py --verbose
# Expected: 52 passed, 0 failed
```

---

## Deployment Address Record Template

After completing all phases, record addresses here:

```
# IoTeX Testnet — deployed <DATE>
POAC_VERIFIER_ADDRESS=
BOUNTY_MARKET_ADDRESS=
DEVICE_REGISTRY_ADDRESS=
SKILL_ORACLE_ADDRESS=
PROGRESS_ATTESTATION_ADDRESS=
TEAM_AGGREGATOR_ADDRESS=
PHG_REGISTRY_ADDRESS=
IDENTITY_REGISTRY_ADDRESS=
PITL_SESSION_REGISTRY_ADDRESS=
PHG_CREDENTIAL_ADDRESS=
FEDERATED_THREAT_REGISTRY_ADDRESS=
TOURNAMENT_GATE_V3_ADDRESS=
TEAM_PROOF_VERIFIER_ADDRESS=        # (after Phase 10)
PITL_SESSION_PROOF_VERIFIER_ADDRESS= # (after Phase 10)
```

Verify each address on the IoTeX testnet explorer:
`https://testnet.iotexscan.io/address/<ADDRESS>`

---

## Troubleshooting

### "DEPLOYER_PRIVATE_KEY too short"
The placeholder `0xYOUR_PRIVATE_KEY_HERE` is truthy but invalid.
Either set a real key or run with `DEPLOYER_PRIVATE_KEY=""` for local testing only.

### "P256PrecompileEmptyReturn" errors
The P256 precompile at `address(0x0100)` is not active on the testnet node.
Run `test_p256_precompile.js` to diagnose. Contact IoTeX team if precompile is absent.

### "setPITLVerifier() can only be called once"
PITLSessionRegistry.pitlVerifier is already set.
Check the current value: call `pitlVerifier()` view function on the registry.

### BountyMarket.setTournamentGate() reverts
Deployer may not have the `GATE_SETTER_ROLE`. Check BountyMarket access control
or wire manually via IoTeX explorer write functions.

### Gas estimation failures on PoACVerifier
Ensure `viaIR: true` is set in hardhat.config.js (it is). The PoACVerifier batch
verification is stack-deep and requires IR compilation.

---

## Contract Deployment Dependency Graph

```
TieredDeviceRegistry
        │
        ├─► PoACVerifier(registry, skew)
        │       └─► BountyMarket(verifier, registry, fee)
        │               └─► setTournamentGate(V2/V3)
        ├─► SkillOracle(verifier)
        ├─► ProgressAttestation(verifier)
        └─► TeamProofAggregator(verifier)

PHGRegistry(bridge)
        ├─► TournamentGate V1 (superseded)
        ├─► TournamentGateV2(registry) → wired to BountyMarket
        ├─► TournamentGateV3(registry, credential) ← Phase 37
        └─► IdentityContinuityRegistry(bridge, registry)

PHGCredential(bridge) ─────────────────────────────────►─┐
        └─► TournamentGateV3 reads isActive()             │
                                                          │
PITLSessionRegistry(bridge)                               │
        └─► setPITLVerifier(PitlSessionProofVerifier)     │
                                                          │
FederatedThreatRegistry(bridge)          PHGCredential ◄──┘
```
