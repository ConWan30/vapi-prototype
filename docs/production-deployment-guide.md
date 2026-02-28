# VAPI Production Deployment Guide

Step-by-step runbook for deploying VAPI to the IoTeX mainnet (chain ID 4689).
Follow every step in order. Do **not** skip the security steps — a compromised
deployer key means a compromised VAPI protocol.

---

## Prerequisites

Before you begin, ensure you have:

- [ ] Node.js ≥ 18 and npm installed
- [ ] Python ≥ 3.11 and pip installed
- [ ] `npx hardhat` works from `contracts/`
- [ ] A funded IoTeX mainnet wallet (minimum 5 IOTX recommended for gas)
- [ ] Hardware security key (YubiKey 5 with PIV or ATECC608A over I²C) — **strongly recommended**
- [ ] A Gnosis Safe multisig address with ≥ 2 of 3 signers configured

---

## Step 1: Generate the Deployer Keystore

Never store the deployer private key in plaintext. Use an Ethereum keystore JSON
(AES-128-CTR + scrypt KDF) encrypted with a strong password.

```bash
cd contracts

# Interactive keystore generation (prompts for password twice)
node -e "
const { ethers } = require('ethers');
const readline = require('readline');
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.question('Password: ', async (pw) => {
  const wallet = ethers.Wallet.createRandom();
  console.error('Address:', wallet.address);
  console.error('SAVE THIS ADDRESS — it must be funded with IOTX for deployment');
  const ks = await wallet.encrypt(pw);
  require('fs').writeFileSync('deployer-keystore.json', ks);
  console.error('Keystore written to deployer-keystore.json');
  rl.close();
});
"

# Move keystore to secure location outside the repo
mv deployer-keystore.json /secure/vault/deployer-keystore.json
```

**Fund the deployer wallet** with at least 5 IOTX from the IoTeX mainnet.

---

## Step 2: Deploy Contracts to Mainnet

Set required environment variables and run the deployment script.

```bash
cd contracts

# Configure deployment
export DEPLOYER_KEY_SOURCE=keystore
export DEPLOYER_KEYSTORE_PATH=/secure/vault/deployer-keystore.json
export DEPLOYER_KEYSTORE_PASSWORD=<your-strong-password>   # use secrets manager in CI

# Deploy all contracts (TieredDeviceRegistry → PoACVerifier → BountyMarket →
#                        ProgressAttestation → TeamProofAggregator)
npx hardhat run scripts/deploy-mainnet.js --network iotex_mainnet
```

The script outputs contract addresses — save them. You will need them for Step 4.

Example output:
```
TieredDeviceRegistry: 0xABC...
PoACVerifier:         0xDEF...
BountyMarket:         0x123...
ProgressAttestation:  0x456...
TeamProofAggregator:  0x789...
```

---

## Step 3: Verify Contracts on iotexscan.io

```bash
# Verify each contract (repeat for each address)
npx hardhat verify --network iotex_mainnet <CONTRACT_ADDRESS> [constructor_args...]
```

Open https://iotexscan.io and confirm each contract is verified (green checkmark).

---

## Step 4: E2E Test with a Real Attested Device

Before enabling enforcement, verify the full pipeline works end-to-end.

```bash
cd bridge

# Copy and fill the bridge env file
cp .env.example .env
# Edit .env: set all CONTRACT ADDRESSES from Step 2
# Set DUALSHOCK_ENABLED=true, BRIDGE_PRIVATE_KEY_SOURCE=env (testnet key for E2E)
# Set IOTEX_RPC_URL=https://babel-api.mainnet.iotex.io, IOTEX_CHAIN_ID=4689

pip install -e .
python -m vapi_bridge.main &

# Wait for "All services started" log, then plug in your DualSense Edge
# Confirm records appear in the HTTP dashboard at http://localhost:8080
# Confirm records are submitted to chain (watch bridge logs for tx hashes)
```

---

## Step 5: Enable Attestation Enforcement

Once E2E test passes, enable on-chain attestation enforcement.
This means unattested devices can no longer submit records.

```bash
cd contracts

npx hardhat run scripts/enable-enforcement.js --network iotex_mainnet
```

---

## Step 6: Transfer Contract Ownership to Gnosis Safe

The deployer wallet must not hold contract ownership long-term — it is a hot key.
Transfer to your Gnosis Safe multisig immediately after deployment.

```bash
cd contracts

export GNOSIS_SAFE_ADDRESS=0x<YOUR_GNOSIS_SAFE>
npx hardhat run scripts/transfer-ownership.js --network iotex_mainnet
```

The script will print a confirmation prompt. Type `yes` to proceed.
Verify on iotexscan.io that `owner()` now returns the Gnosis Safe address.

---

## Step 7: Generate Bridge Keystore

The bridge wallet (which submits PoAC records) also needs a keystore.

```bash
cd bridge

# Generate bridge keystore (prompts for address and password)
python scripts/generate_bridge_keystore.py

# Move to secure location
mv bridge-keystore.json /secure/vault/bridge-keystore.json
```

Fund the bridge wallet with IOTX (0.5–2 IOTX recommended; monitor balance).

---

## Step 8: Configure Bridge for Production

```bash
cd bridge
cp .env.example .env
```

Edit `.env`:
```env
IOTEX_RPC_URL=https://babel-api.mainnet.iotex.io
IOTEX_CHAIN_ID=4689

POAC_VERIFIER_ADDRESS=0x<from Step 2>
BOUNTY_MARKET_ADDRESS=0x<from Step 2>
DEVICE_REGISTRY_ADDRESS=0x<from Step 2>
PROGRESS_ATTESTATION_ADDRESS=0x<from Step 2>
TEAM_AGGREGATOR_ADDRESS=0x<from Step 2>

BRIDGE_PRIVATE_KEY_SOURCE=keystore
BRIDGE_KEYSTORE_PATH=/secure/vault/bridge-keystore.json
BRIDGE_KEYSTORE_PASSWORD_ENV=BRIDGE_KEYSTORE_PASSWORD
# Set BRIDGE_KEYSTORE_PASSWORD in your secrets manager

DUALSHOCK_ENABLED=true
IDENTITY_BACKEND=yubikey          # or "atecc608" for hardware signing
YUBIKEY_PIV_SLOT=9c

HTTP_ENABLED=true
HTTP_PORT=8080
LOG_LEVEL=INFO
```

---

## Step 9: Start Bridge in Production Mode

```bash
cd bridge

# Export keystore password from secrets manager
export BRIDGE_KEYSTORE_PASSWORD=$(vault kv get -field=password secret/vapi/bridge)

# Start bridge
python -m vapi_bridge.main
```

For systemd / Docker deployment, set `BRIDGE_KEYSTORE_PASSWORD` as a secret
environment variable (never write it to disk or `.env`).

---

## Step 10: Verify Bridge is Operational

```bash
# Health check (should return {"status": "ok"})
curl http://localhost:8080/monitor/health | python -m json.tool

# Metrics
curl http://localhost:8080/monitor/metrics | python -m json.tool

# Alerts (should be empty list when healthy)
curl http://localhost:8080/monitor/alerts | python -m json.tool
```

Set up a monitoring cron or Grafana dashboard polling these endpoints.

---

## Step 11: Key Rotation SOP

### When to Rotate

- Suspected key compromise
- Scheduled quarterly rotation (recommended)
- Team member departure (if key was shared)
- Hardware security key replacement

### How to Rotate the Bridge Key

```bash
# 1. Generate new keystore
python bridge/scripts/generate_bridge_keystore.py
# 2. Fund the new address with IOTX
# 3. Update BRIDGE_KEYSTORE_PATH and BRIDGE_KEYSTORE_PASSWORD in secrets manager
# 4. Restart the bridge
# 5. The DualSense Edge generates a new device keypair on first run
#    → device_id changes → on-chain registration required again
#    (The bridge calls ensure_device_registered_tiered() automatically)
```

### How to Rotate the Deployer Key

```bash
# 1. Ensure contract ownership is already with Gnosis Safe (Step 6)
# 2. Generate new deployer keystore
# 3. Update DEPLOYER_KEYSTORE_PATH in secrets manager
# 4. The deployer key is only needed for future deployments
```

---

## Security Checklist

- [ ] All contract addresses saved to a secure location
- [ ] Deployer keystore NOT in the git repository
- [ ] Bridge keystore NOT in the git repository
- [ ] Contract ownership transferred to Gnosis Safe
- [ ] `BRIDGE_KEYSTORE_PASSWORD` stored in a secrets manager (not in `.env`)
- [ ] `DEPLOYER_KEYSTORE_PASSWORD` stored in a secrets manager
- [ ] Bridge wallet balance monitored (set up an alert at < 0.1 IOTX)
- [ ] `/monitor/health` endpoint accessible to your monitoring stack
- [ ] iotexscan.io contract verification complete for all contracts

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `DEPLOYER_KEYSTORE_PATH must be set` | `DEPLOYER_KEY_SOURCE=keystore` but path not set | Set `DEPLOYER_KEYSTORE_PATH` |
| `Keystore decryption failed` | Wrong password | Check `DEPLOYER_KEYSTORE_PASSWORD` value |
| `Low balance` warning in bridge logs | Bridge wallet needs IOTX | Fund bridge wallet address |
| `/monitor/health` returns `"status": "degraded"` | Check `/monitor/alerts` | Fix the alerted condition |
| `DeviceProfileRegistry unavailable` | `controller/profiles/` not importable | Check `PYTHONPATH` or run bridge from project root |
| DualSense Edge not detected | pydualsense not installed | `pip install pydualsense` |
