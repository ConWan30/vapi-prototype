# VAPI Bridge Service — Configuration Reference

All configuration is read from environment variables at startup. No restart is required
for most monitoring settings; the bridge must be restarted to change transport, chain, or
key settings.

---

## Minimal required environment

```sh
# Device keypair (one of hardware or software)
ATECC608A_ADDR=0x60       # I2C address of ATECC608A secure element (hardware signing)
# OR
DEVICE_PRIVATE_KEY_HEX=   # hex-encoded P256 private key (software signing, dev only)

# On-chain (optional — bridge runs in offline mode if omitted)
IOTEX_RPC_URL=https://babel-api.mainnet.iotex.io
POAC_VERIFIER_ADDRESS=0x...
PHG_REGISTRY_ADDRESS=0x...
```

---

## Complete variable reference

### Transport layer

| Variable | Default | Description |
|---|---|---|
| `HTTP_ENABLED` | `true` | Enable FastAPI HTTP ingestion and dashboard |
| `HTTP_HOST` | `0.0.0.0` | Bind address for the HTTP server |
| `HTTP_PORT` | `8080` | Port for HTTP server |
| `MQTT_ENABLED` | `false` | Enable MQTT transport listener |
| `MQTT_HOST` | `localhost` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_TOPIC_PREFIX` | `vapi/poac` | Topic prefix; device records arrive at `{prefix}/{device_id}` |
| `COAP_ENABLED` | `false` | Enable CoAP transport listener |
| `COAP_HOST` | `0.0.0.0` | CoAP bind address |
| `COAP_PORT` | `5683` | CoAP UDP port |

### Signing backend

| Variable | Default | Description |
|---|---|---|
| `SIGNING_BACKEND` | `software` | `atecc608a` or `yubikey` or `software` |
| `ATECC608A_ADDR` | `0x60` | I2C address (hardware signing) |
| `YUBIKEY_SLOT` | `9a` | PIV slot (YubiKey signing) |
| `DEVICE_PRIVATE_KEY_HEX` | — | Hex P256 key for software signing (dev/test only) |

> **Security note**: `software` signing keeps the private key in memory. Use
> `atecc608a` or `yubikey` for any deployment where tamper-evidence matters.

### Chain relay

| Variable | Default | Description |
|---|---|---|
| `IOTEX_RPC_URL` | — | IoTeX JSON-RPC endpoint |
| `CHAIN_PRIVATE_KEY` | — | Relay wallet key (pays gas for batch submissions) |
| `POAC_VERIFIER_ADDRESS` | — | Deployed `PoACVerifier` contract address |
| `PHG_REGISTRY_ADDRESS` | — | Deployed `PHGRegistry` contract address |
| `PHG_CREDENTIAL_ADDRESS` | — | Deployed `PHGCredential` contract address |
| `PITL_SESSION_REGISTRY_ADDRESS` | — | Deployed `PITLSessionRegistry` contract address |
| `FEDERATED_THREAT_REGISTRY_ADDRESS` | — | Deployed `FederatedThreatRegistry` contract address |
| `BATCH_SIZE` | `10` | Records per batch submission |
| `BATCH_TIMEOUT_S` | `30` | Max seconds to wait before flushing a partial batch |

### Database

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `vapi.db` | SQLite database file path |

### Batcher / queue

| Variable | Default | Description |
|---|---|---|
| `QUEUE_MAX_SIZE` | `1000` | Bounded asyncio.Queue depth (OOM prevention) |
| `BATCH_SIZE` | `10` | Records per on-chain batch submission |
| `BATCH_TIMEOUT_S` | `30` | Flush partial batch after this many seconds |

### PHG credential enforcement (Phase 37)

| Variable | Default | Description |
|---|---|---|
| `PHG_CREDENTIAL_ENFORCEMENT_ENABLED` | `true` | Enable Mode 5 suspension synthesis |
| `CREDENTIAL_ENFORCEMENT_MIN_CONSECUTIVE` | `2` | Consecutive critical windows before suspension |
| `CREDENTIAL_SUSPENSION_BASE_DAYS` | `7.0` | Base suspension duration (days) |
| `CREDENTIAL_SUSPENSION_MAX_DAYS` | `28.0` | Maximum suspension duration cap (days) |

### Alert router (Phase 37)

| Variable | Default | Description |
|---|---|---|
| `ALERT_WEBHOOK_URL` | — | Webhook endpoint for enforcement alerts |
| `ALERT_WEBHOOK_FORMAT` | `generic` | `slack`, `pagerduty`, or `generic` |
| `ALERT_SEVERITY_THRESHOLD` | `medium` | Minimum severity to dispatch: `low`, `medium`, `critical` |

**Slack format** — posts to `ALERT_WEBHOOK_URL` as a Slack Incoming Webhook:
```json
{
  "text": "[VAPI] credential_suspended — device abc123... (severity: critical)"
}
```

**PagerDuty format** — posts to Events API v2:
```json
{
  "routing_key": "<ALERT_WEBHOOK_URL path component used as routing key>",
  "event_action": "trigger",
  "payload": { "summary": "...", "severity": "critical", "source": "vapi-bridge" }
}
```

**Generic format** — plain JSON body:
```json
{
  "insight_type": "credential_suspended",
  "device_id": "abc123...",
  "severity": "critical",
  "content": "...",
  "created_at": 1700000000.0
}
```

### Federation (Phase 34)

| Variable | Default | Description |
|---|---|---|
| `FEDERATION_PEERS` | — | Comma-separated peer bridge URLs, e.g. `https://peer1.example.com,https://peer2.example.com` |
| `FEDERATION_API_KEY` | — | Shared API key sent in `X-API-Key` header to peers |
| `FEDERATION_POLL_INTERVAL` | `120.0` | Seconds between federation sync cycles |

### InsightSynthesizer (Phase 35)

| Variable | Default | Description |
|---|---|---|
| `SYNTHESIZER_POLL_INTERVAL` | `21600.0` | Seconds between synthesis cycles (default: 6h) |
| `DIGEST_RETENTION_DAYS` | `90.0` | Days to keep old insight digests before pruning |

### Adaptive thresholds (Phase 36)

| Variable | Default | Description |
|---|---|---|
| `ADAPTIVE_THRESHOLDS_ENABLED` | `true` | Apply detection policy multipliers to L4 Mahalanobis threshold |
| `POLICY_MULTIPLIER_FLOOR` | `0.5` | Minimum allowed multiplier (50% of baseline threshold) |
| `RATE_LIMIT_PER_MINUTE` | `60` | Operator API requests per API key per 60-second window |

### BridgeAgent (Phase 30–37)

| Variable | Default | Description |
|---|---|---|
| `OPERATOR_API_KEY` | — | Shared secret for `/operator/*` endpoints (required to enable agent) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required for BridgeAgent LLM calls) |
| `AGENT_MAX_HISTORY_BEFORE_COMPRESS` | `60` | Session messages before history trimming |

### Monitoring

| Variable | Default | Description |
|---|---|---|
| `MONITORING_ENABLED` | `true` | Enable `/metrics` Prometheus endpoint |
| `MONITORING_HOST` | `0.0.0.0` | Monitoring sub-app bind address |
| `MONITORING_PORT` | `9090` | Monitoring sub-app port |
| `MONITOR_POLL_INTERVAL` | `60.0` | ProactiveMonitor seconds between surveillance cycles |

### ProactiveMonitor (Phase 32)

| Variable | Default | Description |
|---|---|---|
| `MONITOR_POLL_INTERVAL` | `60.0` | Seconds between surveillance cycles |

---

## Docker Compose example

```yaml
version: "3.9"
services:
  bridge:
    image: vapi-bridge:latest
    environment:
      HTTP_PORT: "8080"
      SIGNING_BACKEND: atecc608a
      ATECC608A_ADDR: "0x60"
      IOTEX_RPC_URL: https://babel-api.mainnet.iotex.io
      CHAIN_PRIVATE_KEY: "${CHAIN_PRIVATE_KEY}"
      POAC_VERIFIER_ADDRESS: "${POAC_VERIFIER_ADDRESS}"
      PHG_REGISTRY_ADDRESS: "${PHG_REGISTRY_ADDRESS}"
      PHG_CREDENTIAL_ADDRESS: "${PHG_CREDENTIAL_ADDRESS}"
      OPERATOR_API_KEY: "${OPERATOR_API_KEY}"
      ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
      ALERT_WEBHOOK_URL: "${SLACK_WEBHOOK_URL}"
      ALERT_WEBHOOK_FORMAT: slack
      ALERT_SEVERITY_THRESHOLD: medium
      DB_PATH: /data/vapi.db
    volumes:
      - vapi-data:/data
    ports:
      - "8080:8080"
      - "9090:9090"

  mosquitto:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"

volumes:
  vapi-data:
```

---

## Operator API endpoints

All `/operator/*` endpoints require `?api_key={OPERATOR_API_KEY}` query parameter.
Key comparison uses `hmac.compare_digest()` for constant-time safety.

Rate limit: `RATE_LIMIT_PER_MINUTE` requests per key per 60-second sliding window.
Over-limit requests receive HTTP 429 with `Retry-After: 60`.

| Endpoint | Method | Description |
|---|---|---|
| `/operator/health` | GET | Health check (no auth required) |
| `/operator/gate/{device_id}` | GET | Tournament eligibility gate (HMAC-signed response) |
| `/operator/gate/batch` | POST | Batch eligibility check (up to 50 devices) |
| `/operator/agent` | POST | BridgeAgent conversational query |
| `/operator/agent/stream` | GET | BridgeAgent SSE streaming query |
| `/operator/insights` | GET | Recent protocol insights (max 100) |
| `/operator/digest` | GET | InsightSynthesizer digests (`?window=24h\|7d\|30d\|all`) |
| `/operator/federation/clusters` | GET | Locally-detected federation clusters |
| `/operator/enforcement` | GET | PHGCredential enforcement state |
| `/operator/metrics` | GET | Prometheus text-format metrics (no auth) |

### Gate response format

```json
{
  "device_id": "abc123...",
  "eligible": true,
  "phg_score": 1842,
  "timestamp": 1700000000,
  "signature": "sha256_hmac_hex"
}
```

Verify signature: `HMAC-SHA256(f"{device_id}:{int(eligible)}:{timestamp}", OPERATOR_API_KEY)`

---

## Production checklist

- [ ] `SIGNING_BACKEND` set to `atecc608a` or `yubikey` (never `software` in production)
- [ ] `OPERATOR_API_KEY` set to a random 32+ byte value
- [ ] `ANTHROPIC_API_KEY` set (BridgeAgent disabled otherwise)
- [ ] `CHAIN_PRIVATE_KEY` wallet has sufficient IOTX for batch gas
- [ ] All contract addresses set and verified on IoTeX mainnet
- [ ] `ALERT_WEBHOOK_URL` set for enforcement notifications
- [ ] `DB_PATH` points to a persistent volume (not container ephemeral storage)
- [ ] `MONITORING_PORT` firewalled from public internet (Prometheus metrics are unauthenticated)
- [ ] `FEDERATION_PEERS` and `FEDERATION_API_KEY` set if running multi-instance deployment
