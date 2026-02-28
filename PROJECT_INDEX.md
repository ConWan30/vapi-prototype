# VAPI — Verified Autonomous Physical Intelligence

## Project Index

### Overview
Complete end-to-end VAPI stack: firmware (Zephyr RTOS), smart contracts (Solidity/Hardhat
for IoTeX), bridge service (Python/asyncio), and development tools.
**v0.2.0-rc1 — 43 files, 11,602 lines. Full agent-to-blockchain pipeline.**

### Architecture Summary

**PoAC Record (202 bytes)**: `prev_hash(32) + sensor_commit(32) + model_manifest(32) +
world_model(32) + inference(1) + action(1) + confidence(1) + battery(1) + counter(4) +
timestamp(8) + lat(8) + lon(8) + bounty_id(4) + signature(64)`

**Agent Layers**: Reflexive (30s, TinyML) | Deliberative (5m, knapsack optimizer) |
Strategic (1hr, cloud sync with autonomy guard)

**On-Chain**: DeviceRegistry (ioID) -> PoACVerifier (P256 precompile) -> BountyMarket (rewards)

### Directory Structure

```
vapi-pebble-prototype/
|
|-- firmware/                          # Zephyr / nRF Connect SDK application
|   |-- include/                       # Public headers
|   |   |-- poac.h                     # PoAC record (202B) + CryptoCell-310 API
|   |   |-- perception.h               # Unified sensor abstraction (BME680, ICM, TSL, GPS)
|   |   |-- agent.h                    # Three-layer autonomous agent + world model
|   |   |-- economic.h                 # Bounty evaluator + greedy knapsack optimizer
|   |   |-- tinyml.h                   # Edge Impulse / heuristic TinyML wrapper
|   |-- src/                           # Implementation
|   |   |-- main.c                     # Boot sequence, model attestation, uplink queue
|   |   |-- poac.c                     # PoAC chaining, ECDSA-P256 signing, NVS persistence
|   |   |-- perception.c              # Sensor drivers + deterministic serialization
|   |   |-- agent.c                    # Reflexive/deliberative/strategic threads
|   |   |-- economic.c                 # Utility function, battery economics, optimizer
|   |   |-- tinyml.c                   # TinyML engine (heuristic + Edge Impulse bridge)
|   |-- boards/
|   |   |-- pebble_tracker.overlay     # Device tree: I2C, SPI, GPIO, ADC pin mapping
|   |-- Kconfig                        # Custom VAPI Kconfig symbols
|   |-- CMakeLists.txt                 # Zephyr build configuration
|   |-- prj.conf                       # Full kernel/crypto/sensor/cellular config
|
|-- contracts/                         # Hardhat project for IoTeX blockchain
|   |-- contracts/
|   |   |-- DeviceRegistry.sol         # Device identity, anti-Sybil deposit, reputation
|   |   |-- PoACVerifier.sol           # P256 signature verify (precompile 0x0100), chain validation
|   |   |-- BountyMarket.sol           # Bounty lifecycle, evidence validation, swarm aggregation
|   |-- scripts/
|   |   |-- deploy.js                  # Full deployment with dependency wiring + role grants
|   |-- hardhat.config.js              # IoTeX testnet (4690) + mainnet (4689) config
|   |-- package.json                   # npm dependencies (hardhat, openzeppelin, dotenv)
|   |-- .env.example                   # Environment variable template
|
|-- bridge/                            # PoAC record relay service (Python)
|   |-- vapi_bridge/                   # Python package
|   |   |-- config.py                  # Environment-based configuration
|   |   |-- codec.py                   # 228-byte PoAC parsing + P256 verification
|   |   |-- store.py                   # SQLite persistence (records, devices, submissions)
|   |   |-- chain.py                   # Web3 contract client (PoACVerifier, BountyMarket)
|   |   |-- batcher.py                 # Record batching + retry with exponential backoff
|   |   |-- main.py                    # Entry point + orchestration
|   |   |-- transports/
|   |   |   |-- mqtt.py                # MQTT listener (aiomqtt)
|   |   |   |-- coap.py                # CoAP server (aiocoap)
|   |   |   |-- http.py                # FastAPI webhook + monitoring dashboard
|   |-- Dockerfile                     # Container image
|   |-- docker-compose.yml             # Bridge + Mosquitto MQTT broker
|   |-- requirements.txt               # Python dependencies
|   |-- .env.example                   # Configuration template
|   |-- systemd/
|   |   |-- vapi-bridge.service        # Systemd unit file
|   |-- README.md                      # Bridge deployment guide
|
|-- tools/
|   |-- poac_inspector.py              # CLI: decode, verify chain, monitor serial
|
|-- README.md                          # Complete flashing + first-run instructions
|-- PROJECT_INDEX.md                   # This file
```

### Build Quick Reference

```bash
# Firmware
west build -b nrf9160dk_nrf9160_ns firmware/ -- \
    -DDTC_OVERLAY_FILE=firmware/boards/pebble_tracker.overlay
west flash

# Contracts
cd contracts && npm install && npx hardhat compile
npx hardhat run scripts/deploy.js --network iotex_testnet

# Monitor
python tools/poac_inspector.py monitor --port COM3 --baud 115200
```

### Key Data Flow

```
Sensor Read -> perception_serialize() -> poac_commit_sensors() [SHA-256]
                                              |
World Model -> wm_compute_hash() ---------> poac_generate() [ECDSA-P256 sign]
                                              |
TinyML Infer -> inference_result ----------> PoAC Record (228 bytes)
                                              |
Economic Eval -> bounty_id, action_code ---->  |
                                              v
                                    NB-IoT Cellular Uplink
                                              |
                                              v
                               VAPI Bridge [MQTT/CoAP/HTTP]
                              (P256 verify, batch, retry)
                                              |
                                              v
                               PoACVerifier.verifyPoACBatch() [on-chain]
                                              |
                                              v
                              BountyMarket.submitEvidence() [reward]
```

### Bridge Quick Reference

```bash
# Docker (recommended)
cd bridge && cp .env.example .env && docker compose up -d
# Dashboard: http://localhost:8080

# Python (development)
cd bridge && pip install -r requirements.txt && python -m vapi_bridge.main
```
