# VAPI Documentation Index

## Architecture
- **[Architecture Overview](architecture.md)** — System diagram, data flow, trust boundaries, protocol invariants
- **[OpenAPI Spec](../openapi.yaml)** — REST API reference for bridge + operator endpoints (Phases 28–37)

## Getting Started

### Dev Environment Setup
```bash
# Bridge (Python 3.10+)
cd bridge && pip install -r requirements.txt
python -m pytest bridge/tests/ -q  # should pass 728 tests

# Contracts (Node 18+)
cd contracts && npm install
npx hardhat test  # should pass ~341 tests

# SDK
cd sdk && pip install -r requirements.txt
python -m pytest sdk/tests/ -v  # should pass 28 tests
```

### Quick Start — Bridge
```bash
export OPERATOR_API_KEY=your_key_here
export IOTEX_PRIVATE_KEY=0x...
cd bridge && python -m vapi_bridge.main
```

## Running Tests

| Command | What it runs |
|---------|-------------|
| `make test` | All non-hardware tests |
| `make test-bridge` | Bridge pytest (728 tests) |
| `make test-contracts` | Hardhat tests (~341) |
| `make test-sdk` | SDK pytest (28 tests) |
| `make test-hardware` | Hardware tests (requires DualShock Edge) |
| `pytest -m "not hardware"` | Safe for CI (excludes hardware) |

## Hardware Testing
- **[Hardware Testing Guide](hardware-testing-guide.md)** — Step-by-step for first DualShock Edge session
- **[Hardware Test README](../tests/hardware/README.md)** — Test descriptions and troubleshooting

## Calibration & Benchmarks
- **[Detection Benchmarks](detection-benchmarks.md)** — Synthetic detection rates and path to real-world benchmarks
- `scripts/capture_session.py` — Capture live HID sessions for calibration
- `scripts/threshold_calibrator.py` — Derive empirical PITL thresholds from captured sessions
- `scripts/first_session_protocol.py` — Guided first hardware test session

## Contract Deployment
- **[Gas Report](../contracts/gas-report.md)** — Gas cost estimates (Hardhat simulation)
- **[Production Deployment Guide](production-deployment-guide.md)** — Testnet → mainnet runbook

## Bridge Deployment
- Environment variables: `OPERATOR_API_KEY`, `IOTEX_RPC_URL`, `IOTEX_PRIVATE_KEY`, etc.
- Docker: `docker-compose up` in `bridge/`
- ZK ceremony (before production): `cd contracts && npx hardhat run scripts/run-ceremony.js`

## API Reference
- **OpenAPI**: `openapi.yaml` at project root covers all endpoints from Phases 28–37
- Operator endpoints at `/operator/` — require `X-API-Key` header
- Player dashboard at `/` (Alpine.js + Tailwind)
- Prometheus metrics at `/metrics` (no auth)
- WebSocket: `ws://host/ws/records`

## Whitepaper
- Draft rewrite: **[vapi-whitepaper-v2.md](vapi-whitepaper-v2.md)** — Restructured academic version
- Current: `paper/vapi-whitepaper.md`
- Archived: `whitepaper/vapi-whitepaper.md`

## Key Reference
- **[SDK Guide](vapi-sdk-guide.md)** — VAPISession, VAPIVerifier, self-verify loop
- `bridge/LESSONS.md` — Architectural lessons from Phases 32–36
- `lessons.md` — Project-wide lessons learned
- `memory.md` — Architecture decisions and known gotchas
