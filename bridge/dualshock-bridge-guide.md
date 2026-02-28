# DualShock Edge — VAPI Bridge Integration Guide

## Overview

This guide connects a live DualSense Edge controller to the VAPI bridge for full
on-chain bounty fulfillment and SkillOracle rating updates via the IoTeX network.

```
DualSense Edge (USB/BT)
    |
    v
dualshock_integration.py   <- polls HID at ~120 Hz
    |  AntiCheatClassifier  <- 6-class inference (0x20-0x27)
    |  PoACEngine            <- 228-byte ECDSA-P256 signed records
    v
Bridge.on_record("dualshock")
    |
    v
Batcher -> IoTeX PoACVerifier.verifyPoACBatch()
        -> BountyMarket.submitEvidence()   (if bounty_id > 0)
        -> SkillOracle.updateRating()      (session end, if configured)
```

---

## Prerequisites

### Software
```bash
# In the bridge virtualenv:
pip install pydualsense hidapi web3 eth-hash cryptography fastapi uvicorn aiohttp

# Verify pydualsense can see the controller:
python -c "from pydualsense import pydualsense; ds=pydualsense(); ds.init(); print('Edge:', ds.is_edge)"
```

### Hardware
- DualSense Edge controller connected via USB or Bluetooth
- Windows: no extra driver needed (HID via hidapi)
- Linux: add udev rule:
  ```
  echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="054c", ATTRS{idProduct}=="0df2", MODE="0666"' \
    | sudo tee /etc/udev/rules.d/70-dualsense-edge.rules
  sudo udevadm control --reload-rules
  ```

### Contracts deployed
You need three contract addresses from your Hardhat deployment:
- `PoACVerifier` — already required by the base bridge
- `BountyMarket` — for bounty fulfillment (optional but recommended)
- `SkillOracle` — for on-chain rating updates (optional)

---

## Configuration

Create or update `bridge/.env`:

```dotenv
# ---- Required (existing) ----
POAC_VERIFIER_ADDRESS=0xYourVerifierAddress
BRIDGE_PRIVATE_KEY=0xYourPrivateKey
IOTEX_RPC_URL=https://babel-api.testnet.iotex.io
IOTEX_CHAIN_ID=4690

# ---- DualShock transport ----
DUALSHOCK_ENABLED=true

# Seconds between PoAC records (1.0 = one record per second)
DUALSHOCK_RECORD_INTERVAL_S=1.0

# ---- Optional: BountyMarket ----
BOUNTY_MARKET_ADDRESS=0xYourBountyMarketAddress
# Comma-separated bounty IDs to fulfil automatically
DUALSHOCK_ACTIVE_BOUNTIES=1001,1002

# ---- Optional: SkillOracle ----
SKILL_ORACLE_ADDRESS=0xYourSkillOracleAddress

# ---- Disable network transports if running standalone ----
MQTT_ENABLED=false
COAP_ENABLED=false
HTTP_ENABLED=true      # keep dashboard on for monitoring
HTTP_PORT=8080

# ---- Batching: flush every 10 records or 30 seconds ----
BATCH_SIZE=10
BATCH_TIMEOUT_S=30
```

---

## Running the Bridge

### Option A — Direct Python

```bash
cd vapi-pebble-prototype/bridge

# Install dependencies
pip install -r requirements.txt

# Start bridge with DualShock transport
python -m vapi_bridge.main
```

Expected startup output:
```
2026-02-21 00:00:00 [INFO] vapi_bridge.main: ========================================
2026-02-21 00:00:00 [INFO] vapi_bridge.main: VAPI Bridge v0.2.0-rc1 starting
2026-02-21 00:00:00 [INFO] vapi_bridge.main: Bridge balance: 10.0000 IOTX
2026-02-21 00:00:00 [INFO] vapi_bridge.dualshock_integration: DualSense Edge connected (device_id=a3f7c2...)
2026-02-21 00:00:00 [INFO] vapi_bridge.dualshock_integration: Device registered: a3f7c2... pubkey=04ab12...
2026-02-21 00:00:00 [INFO] vapi_bridge.dualshock_integration: DualShock transport ready | device=a3f7c2... | interval=1.0s
2026-02-21 00:00:00 [INFO] vapi_bridge.main: DualShock Edge transport enabled (interval=1.0s)
2026-02-21 00:00:00 [INFO] vapi_bridge.main: All services started — bridge is operational
```

### Option B — Docker Compose

Add to `docker-compose.yml`:

```yaml
services:
  bridge:
    build: ./bridge
    environment:
      - DUALSHOCK_ENABLED=true
      - DUALSHOCK_RECORD_INTERVAL_S=1.0
      - POAC_VERIFIER_ADDRESS=${POAC_VERIFIER_ADDRESS}
      - BRIDGE_PRIVATE_KEY=${BRIDGE_PRIVATE_KEY}
      - IOTEX_RPC_URL=${IOTEX_RPC_URL}
    devices:
      - /dev/hidraw0:/dev/hidraw0   # DualSense Edge HID device
    volumes:
      - bridge_data:/root/.vapi
    ports:
      - "8080:8080"
```

Note: USB passthrough in Docker requires `--privileged` or specific device mapping.
For USB on Windows + Docker, use WSL2 with `usbipd` to attach the controller.

---

## Verifying Operation

### 1. Dashboard (HTTP)

Open `http://localhost:8080` — records appear within seconds of starting play.

Key fields to watch:
- `inference`: `NOMINAL (0x20)` or `SKILLED (0x21)` during clean play
- `bounty_id`: non-zero when a bounty is being fulfilled
- `status`: transitions `pending -> batched -> submitted -> verified`

### 2. IoTeX Testnet Explorer

After the first batch is submitted, check:
- `https://testnet.iotexscan.io/tx/<tx_hash>` — PoACVerifier.verifyPoACBatch()
- `https://testnet.iotexscan.io/tx/<tx_hash>` — BountyMarket.submitEvidence()

### 3. SkillOracle Rating

```bash
# Query current rating from chain:
npx hardhat console --network iotex_testnet
> const oracle = await ethers.getContractAt("SkillOracle", "0xYourOracleAddress")
> const [rating, tier] = await oracle.getRating("0x" + deviceIdHex)
> console.log("Rating:", rating.toString(), "Tier:", tier.toString())
```

Tier mapping: `0=Bronze 1=Silver 2=Gold 3=Platinum 4=Diamond`

### 4. Log monitoring

```bash
# Watch PoAC records flowing:
python -m vapi_bridge.main 2>&1 | grep -E "PoAC|Batch|bounty|rating|CHEAT"
```

---

## Anti-Cheat Inference Codes

| Code | Name            | SkillOracle Effect        | LED     |
|------|-----------------|---------------------------|---------|
| 0x20 | NOMINAL         | +4 rating (conf=220)      | Green   |
| 0x21 | SKILLED         | +10 rating (conf=220)     | Cyan    |
| 0x22 | CHEAT:REACTION  | -200 rating + haptic      | Red     |
| 0x23 | CHEAT:MACRO     | -200 rating + haptic      | Red     |
| 0x24 | CHEAT:AIMBOT    | -200 rating + haptic      | Red     |
| 0x25 | CHEAT:RECOIL    | -200 rating + haptic      | Red     |
| 0x26 | CHEAT:IMU_MISS  | -200 rating + haptic      | Red     |
| 0x27 | CHEAT:INJECTION | -200 rating + haptic      | Red     |

---

## Device Identity and Key Persistence

Each time the bridge starts, a fresh ECDSA-P256 keypair is generated by `PoACEngine`.
The resulting device identity (keccak256 of the public key) is registered in the
local SQLite store, enabling signature verification from the first record.

**Note on session continuity:** The current implementation generates a new keypair
per process start. For persistent on-chain device identity across sessions, register
the device in `DeviceRegistry.sol` after the first run and set
`DEVICE_REGISTRY_ADDRESS` so subsequent sessions can be associated with the same
on-chain identity. A persistent key file feature is planned for v0.3.0.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Cannot import dualshock_emulator` | `controller/` not on path | Check `Path(__file__).parents[3] / "controller"` resolves to `vapi-pebble-prototype/controller` |
| `DualSense Edge not found` | HID permissions or not connected | Run `python diag_sticks.py` in `controller/` to verify |
| `Signature verification FAILED` | Bridge pubkey lookup miss | Ensure bridge is started BEFORE first record arrives (registration happens at startup) |
| `Contract revert` | Low IOTX balance or wrong ABI | Check `BRIDGE_PRIVATE_KEY` balance on testnet; redeploy if ABI mismatch |
| `SkillOracle chain submit failed` | `SKILL_ORACLE_ADDRESS` not set | Add to `.env`; local tracking still works without it |
| Records stuck in `pending` | `POAC_VERIFIER_ADDRESS` missing | Required — set in `.env` |

---

## Session Lifecycle

```
Bridge start
    |
    v
[BOOT record]       inference=NOMINAL, action=0x09 (ACTION_BOOT)
    |
    v
[REPORT records]    Every DUALSHOCK_RECORD_INTERVAL_S seconds
    |               inference = classifier output (0x20-0x27)
    |               bounty_id = first active bounty (if configured)
    |
    v
[Ctrl+C / SIGTERM]
    |
    v
[SkillOracle.updateRating()]   Final session rating submitted on-chain
    |
    v
[LED reset to blue]            Controller disconnects cleanly
```

---

## Next Steps

1. **Deploy contracts to IoTeX mainnet** — update `.env` with mainnet addresses and `IOTEX_CHAIN_ID=4689`
2. **Persistent device identity** — implement key file persistence so device_id survives restarts
3. **ProgressAttestation integration** — submit BPS improvement attestations between verified PoAC pairs
4. **TeamProofAggregator** — multi-player sessions with Merkle-aggregated team proofs
5. **Pebble Tracker firmware** — when hardware arrives, bridge already handles both transports concurrently
