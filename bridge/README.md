# VAPI Bridge — PoAC Record Relay Service

Receives PoAC records from Pebble Tracker devices via MQTT, CoAP, or HTTP webhook,
validates ECDSA-P256 signatures, batches records, and submits them to the IoTeX
blockchain for on-chain verification and bounty settlement.

## Architecture

```
Pebble Devices                    VAPI Bridge                        IoTeX Blockchain
┌────────────┐                 ┌─────────────────┐               ┌──────────────────┐
│ NB-IoT/LTE │─── MQTT ──────▶│ Transport Layer  │               │ PoACVerifier.sol │
│            │─── CoAP ──────▶│  (mqtt/coap/http)│               │ (P256 verify)    │
│            │─── HTTP ──────▶│        │         │               └────────┬─────────┘
└────────────┘                 │        ▼         │                        │
                               │ Codec + P256     │                        ▼
                               │ Signature Verify │               ┌──────────────────┐
                               │        │         │               │ BountyMarket.sol │
                               │        ▼         │               │ (evidence + pay) │
                               │ SQLite Store     │               └──────────────────┘
                               │        │         │                        ▲
                               │        ▼         │                        │
                               │ Batcher + Retry  │───── Web3.py ─────────┘
                               │        │         │
                               │        ▼         │
                               │ Dashboard (8080) │
                               └─────────────────┘
```

## Quick Start

### Option 1: Docker Compose (Recommended)

```bash
cd bridge/

# Configure
cp .env.example .env
# Edit .env: set POAC_VERIFIER_ADDRESS, BRIDGE_PRIVATE_KEY, etc.

# Start (builds bridge + mosquitto MQTT broker)
docker compose up -d

# Dashboard: http://localhost:8080
# MQTT broker: localhost:1883
```

### Option 2: Python (Development)

```bash
cd bridge/

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or: venv\Scripts\activate (Windows)

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your values

# Run
python -m vapi_bridge.main
```

### Option 3: Systemd (Production Linux)

```bash
# Install
sudo mkdir -p /opt/vapi-bridge/data
sudo cp -r bridge/* /opt/vapi-bridge/
cd /opt/vapi-bridge
sudo python -m venv venv
sudo venv/bin/pip install -r requirements.txt

# Configure
sudo cp .env.example .env
sudo vim .env

# Create service user
sudo useradd -r -s /bin/false vapi
sudo chown -R vapi:vapi /opt/vapi-bridge

# Install and start service
sudo cp systemd/vapi-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vapi-bridge
sudo systemctl start vapi-bridge

# Monitor
sudo journalctl -u vapi-bridge -f
```

## Configuration

All configuration via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `IOTEX_RPC_URL` | `https://babel-api.testnet.iotex.io` | IoTeX JSON-RPC endpoint |
| `IOTEX_CHAIN_ID` | `4690` | Chain ID (4690=testnet, 4689=mainnet) |
| `POAC_VERIFIER_ADDRESS` | *required* | PoACVerifier contract address |
| `BOUNTY_MARKET_ADDRESS` | | BountyMarket contract address |
| `DEVICE_REGISTRY_ADDRESS` | | DeviceRegistry contract address |
| `BRIDGE_PRIVATE_KEY` | *required* | Wallet private key for gas |
| `MQTT_ENABLED` | `true` | Enable MQTT listener |
| `MQTT_BROKER` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_TOPIC_PREFIX` | `vapi/poac` | Topic prefix (subscribes to `prefix/#`) |
| `COAP_ENABLED` | `false` | Enable CoAP server |
| `COAP_PORT` | `5683` | CoAP UDP port |
| `HTTP_ENABLED` | `true` | Enable HTTP webhook + dashboard |
| `HTTP_PORT` | `8080` | HTTP server port |
| `BATCH_SIZE` | `10` | Max records per batch submission |
| `BATCH_TIMEOUT_S` | `30` | Max wait time before submitting partial batch |
| `MAX_RETRIES` | `5` | Retries before dead-lettering a submission |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |

## API Endpoints

### Webhook (Record Submission)

```bash
# Submit a single 228-byte PoAC record
curl -X POST http://localhost:8080/api/v1/records \
     -H "Content-Type: application/octet-stream" \
     --data-binary @record.bin

# Submit multiple records (concatenated binary)
curl -X POST http://localhost:8080/api/v1/records/batch \
     -H "Content-Type: application/octet-stream" \
     --data-binary @records.bin
```

### Monitoring

```bash
# Bridge statistics
curl http://localhost:8080/api/v1/stats

# Active devices
curl http://localhost:8080/api/v1/devices

# Device details
curl http://localhost:8080/api/v1/devices/{device_id_hex}

# Recent records
curl http://localhost:8080/api/v1/records/recent?limit=50
```

### Dashboard

Open `http://localhost:8080` in a browser for the real-time monitoring dashboard.

## MQTT Topic Scheme

The bridge subscribes to `{MQTT_TOPIC_PREFIX}/#` and expects:

| Topic | Payload | Description |
|-------|---------|-------------|
| `vapi/poac/{device_id}` | 228 bytes binary | Single PoAC record |

The Pebble firmware publishes to this topic via the NB-IoT MQTT bridge.

## Record Processing Pipeline

1. **Receive** — Transport listener receives 228-byte payload
2. **Parse** — `codec.py` deserializes big-endian fields from wire format
3. **Verify** — ECDSA-P256 signature checked against device's public key
4. **Persist** — Record stored in SQLite with `pending` status
5. **Batch** — Accumulate up to `BATCH_SIZE` records or `BATCH_TIMEOUT_S`
6. **Submit** — `verifyPoACBatch()` called on PoACVerifier contract
7. **Confirm** — Wait for transaction receipt; update status to `verified`
8. **Evidence** — If `bounty_id > 0`, auto-call `submitEvidence()` on BountyMarket
9. **Retry** — Failed submissions retried with exponential backoff + jitter
10. **Dead-letter** — After `MAX_RETRIES`, records moved to dead-letter queue

## Testing Without Hardware

```python
# Generate a simulated PoAC record and send via HTTP
import struct, os, hashlib, requests

# Build a fake 228-byte record (invalid signature, for transport testing)
body = os.urandom(164)  # Random body
sig = os.urandom(64)    # Random signature
record = body + sig

# Submit to bridge
resp = requests.post(
    "http://localhost:8080/api/v1/records",
    data=record,
    headers={"Content-Type": "application/octet-stream"},
)
print(resp.json())
```

For end-to-end testing with valid signatures, use `tools/poac_inspector.py` to
generate test records from the firmware's output.

## File Structure

```
bridge/
├── vapi_bridge/              # Python package
│   ├── __init__.py
│   ├── config.py             # Environment-based configuration
│   ├── codec.py              # 228-byte PoAC parsing + P256 verification
│   ├── store.py              # SQLite persistence (records, devices, submissions)
│   ├── chain.py              # Web3 contract client (PoACVerifier, BountyMarket)
│   ├── batcher.py            # Record batching + retry with exponential backoff
│   ├── transports/
│   │   ├── mqtt.py           # MQTT listener (aiomqtt)
│   │   ├── coap.py           # CoAP server (aiocoap)
│   │   └── http.py           # FastAPI webhook + dashboard
│   └── main.py               # Entry point + orchestration
├── Dockerfile
├── docker-compose.yml        # Bridge + Mosquitto MQTT broker
├── mosquitto.conf            # MQTT broker config
├── requirements.txt
├── .env.example
├── systemd/
│   └── vapi-bridge.service   # Systemd unit file
└── README.md                 # This file
```
