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

## Auto-Calibration Agent (Phase 17)

The `CalibrationAgent` runs as the ProactiveMonitor's 4th surveillance check and
automatically recalibrates L4 biometric thresholds as new human sessions accumulate —
replacing the manual `python scripts/threshold_calibrator.py sessions/human/hw_*.json` step.

### Activation Requirements

The agent activates automatically when **both** conditions hold at bridge startup:

1. `OPERATOR_API_KEY` is set in the bridge environment (enables the ProactiveMonitor)
2. The `BridgeAgent` (Claude tool-use) is initialised (requires `ANTHROPIC_API_KEY`)

```bash
# Minimum env to activate auto-calibration
OPERATOR_API_KEY=your-operator-key-here
ANTHROPIC_API_KEY=sk-ant-...
```

If either is absent, the bridge logs `CalibrationAgent unavailable` and continues
without auto-calibration. No sessions or detection are affected.

### What It Does

| Parameter | Value | Description |
|-----------|-------|-------------|
| `RECALIB_SESSION_DELTA` | 5 | New sessions before recalibration triggers |
| `MAX_THRESHOLD_DELTA` | 10% | Maximum fractional threshold change allowed |
| `MIN_INTERVAL_SECS` | 6 h | Minimum time between calibration runs |
| Sessions dir | `sessions/human/` | Watches `hw_*.json` files |
| Polling rate filter | 800–1100 Hz | Excludes anomalous sessions (USB issues) |

On each ProactiveMonitor cycle (~60 s default):

1. Counts `hw_*.json` files in `sessions/human/` with valid polling rate (800–1100 Hz)
2. Skips if delta from last calibration run < 5 sessions or < 6 hours since last run
3. Runs `threshold_calibrator.py` as a subprocess (timeout 180 s)
4. Parses new `L4 anomaly_threshold` and `L4 continuity_threshold` from stdout
5. **Safety guard:** rejects if either threshold changes > 10% from current value
6. Applies new thresholds live to `cfg.l4_anomaly_threshold` / `cfg.l4_continuity_threshold`
7. Logs result and writes a `calibration_auto` insight to `protocol_insights`

### Verifying the Agent is Running

```bash
# Bridge logs on startup (INFO level)
# "Phase 17: CalibrationAgent attached to ProactiveMonitor"

# Check for calibration events in insights
curl -s http://localhost:8080/insights | jq '.[] | select(.type=="calibration_auto")'

# Or check session quality flags (excluded sessions)
curl -s http://localhost:8080/insights | jq '.[] | select(.type=="session_quality_flag")'
```

### Manual Override

To force a calibration run outside the normal cycle:

```bash
# Run calibrator directly against all valid sessions
python scripts/threshold_calibrator.py sessions/human/hw_*.json

# Output format (parsed by CalibrationAgent):
# L4 anomaly_threshold: 7.019
# L4 continuity_threshold: 5.369

# Then restart the bridge to load new defaults from calibration_profile.json
```

The manual script writes updated thresholds to `calibration_profile.json`. The bridge
reads defaults from env vars `L4_ANOMALY_THRESHOLD` / `L4_CONTINUITY_THRESHOLD`
(overrides `calibration_profile.json`) or directly from the file on import.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `CalibrationAgent unavailable` in logs | `OPERATOR_API_KEY` or `ANTHROPIC_API_KEY` not set | Set both env vars |
| `REJECTED: delta X.X% exceeds 10% limit` | New threshold deviates > 10% from current | Review sessions for outliers; run manual calibration after inspection |
| `Calibration subprocess failed` | `threshold_calibrator.py` error | Run manually to see full error output |
| Agent never fires | < 5 new sessions or < 6 h since last run | Add sessions or lower `RECALIB_SESSION_DELTA` via subclass |

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
- [ ] Auto-calibration agent logs `CalibrationAgent attached to ProactiveMonitor` at startup (if operator key configured)
- [ ] `sessions/human/` directory writable and contains ≥ 5 `hw_*.json` sessions for first calibration cycle

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
