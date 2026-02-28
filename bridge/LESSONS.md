# VAPI Bridge — Architectural Lessons (Phases 32–36)

## 1. Feed-Forward vs. Feedback Loops in Adaptive Systems

**Lesson:** A purely feed-forward pipeline (events → insights → labels → reports) is incomplete until
the intelligence it produces can influence the detection surface that generated it. Phases 32–35 built
the complete temporal intelligence stack but left the loop open: a device labeled `critical` for three
consecutive windows still faced the same Mahalanobis threshold as a freshly-seen device.

**Phase 36 pattern:** `InsightSynthesizer` Mode 4 writes per-device `detection_policies` rows
(multiplier ∈ [floor, 1.0], basis_label, expires_at) immediately after Mode 2 (trajectory labels).
`dualshock_integration.py` reads the policy **after** `classify()` returns `None`, applying a tighter
effective threshold. The loop is closed without modifying classifier state.

**Invariants that make this safe:**
- Policy check is always wrapped in bare `except Exception: pass` — never fatal
- Hard cheat codes (0x28/0x29/0x2A) are never affected — the policy block is skipped when `bio_result is not None`
- Multiplier floor = 0.5 (configurable via `POLICY_MULTIPLIER_FLOOR`) — prevents false-positive storms
- Policies auto-expire: `expires_at = now + poll_interval + 3600s`
- Every policy change is logged as a `policy_adjustment` protocol insight (verifiable audit trail)

---

## 2. Async Dedup Pitfalls: In-Memory Set vs. Time-Bounded Dict

**Lesson (ProactiveMonitor):** Using `set[frozenset]` for deduplication of flagged clusters is an
unbounded memory leak in production. Under tournament load where thousands of device clusters are
generated per day, the set grows forever. More critically: a cluster flagged at hour 0 will NEVER
re-alert even at hour 240, silently masking persistent threats.

**Phase 36 fix:** Changed to `dict[frozenset, float]` storing `time.monotonic()` timestamps.
`_evict_stale_clusters()` removes entries older than 86400s (24h) before each detection cycle.
This means: a cluster flagged 25 hours ago will re-alert on the next cycle, providing accurate
re-notification for persistent threats while still preventing alert spam within a 24h window.

**Pattern:** Whenever dedup state must survive >1 cycle, use a time-bounded dict, not a set.

---

## 3. Prometheus Compatibility as a First-Class Production Concern

**Lesson:** Returning a JSON dict from `/metrics` is convenient for development but is a production
blocker: no existing monitoring infrastructure (Prometheus, Grafana, Datadog agent, k8s auto-scraping)
can ingest it without custom transformation. The OpenAPI spec documented a Prometheus endpoint but
the implementation was JSON — a documentation-reality gap that would surface immediately at first
deployment.

**Phase 36 fix:** `PlainTextResponse` with `# HELP`, `# TYPE`, and value lines for all 10 metrics.
`create_monitoring_app(state, store)` factory pattern enables per-test isolation and allows production
code to pass the real `store` for synthesis gauge population.

**Rule:** Any endpoint named `/metrics` MUST return `text/plain; charset=utf-8` in Prometheus
exposition format. Verify with `curl | grep "# HELP"`, not `curl | python -m json.tool`.

---

## 4. Factory Functions Over Module-Level Singletons for Testability

**Lesson:** Module-level singletons (e.g., `monitoring_app = FastAPI()`) capture state at import time.
When tests want to inject a fresh state, they must monkey-patch module internals — this creates
ordering dependencies between tests and makes parallel test execution unsafe.

**Phase 36 fix:** `create_monitoring_app(cfg=None, state=None, store=None)` factory creates an
isolated app instance per call. Tests call `create_monitoring_app(state=fresh_state)`. The module
still exports `monitoring_app = create_monitoring_app()` for backward compatibility.

**General rule:** Any FastAPI sub-app that needs state injection in tests MUST be a factory function.
The module-level singleton is acceptable only as a convenience backward-compat alias.

---

## 5. Why the Policy Multiplier Floor Matters

**Lesson:** Without a floor, the adaptive threshold could theoretically drop to near-zero for a device
with an extremely negative risk trajectory history. A threshold near zero would flag nearly every
biometric session as anomalous, causing a false-positive storm that de-legitimizes the detection system
entirely.

**Phase 36 design:** `policy_multiplier_floor = 0.5` (configurable via `POLICY_MULTIPLIER_FLOOR` env).
The minimum effective L4 threshold is `3.0 × 0.5 = 1.5`. At this threshold, only sessions with
Mahalanobis distance > 1.5 (vs. the baseline 3.0) are flagged — still meaningfully tight but not
pathologically sensitive.

**Principle:** Every adaptive parameter needs a bounded floor. Unbounded tightening is as dangerous as
no tightening. The floor also defines the maximum enforcement strength, making the system's behavior
explainable: "The protocol can tighten your threshold by at most 50%."

---

## 6. Zero-Dependency Rate Limiting

**Lesson:** Third-party rate-limiting libraries (slowapi, fastapi-limiter) add deployment complexity
and version coupling. For a system like VAPI where each `create_operator_app()` instance is the unit
of isolation, a simple in-process solution is both sufficient and correct.

**Phase 36 pattern:**
```python
class _RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self._rpm = requests_per_minute
        self._buckets: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._buckets[key]
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        if len(dq) >= self._rpm:
            return False
        dq.append(now)
        return True
```

**Key properties:** Per-instance (not global), per-api-key buckets, sliding 60s window, O(1) amortized.
The `/health` endpoint is explicitly exempt — monitoring probes must never be rate-limited.

---

## 7. Batcher Shutdown and Recovery

**Lesson (batcher.py):** Two production failure modes are unacceptable:
1. **Data loss on shutdown** — `CancelledError` caught and re-raised without draining the queue means
   in-flight records are lost even though they exist in the DB as `pending`.
2. **Memory exhaustion under load** — `asyncio.Queue()` with no maxsize is an OOM vector.

**Phase 36 fix:**
- `asyncio.Queue(maxsize=1000)` — bounded; producers catch `QueueFull` gracefully
- Startup recovery: on `run()` entry, call `store.get_pending_records(limit=500)` and re-enqueue via
  `put_nowait()` (catching `QueueFull` per item)
- Shutdown drain: on `CancelledError`, run a time-bounded drain loop (`asyncio.wait_for(collect, timeout=5.0)`)
  before re-raising — ensures the queue is flushed without hanging indefinitely

**Principle:** Every async queue must have a maxsize. Every CancelledError handler that owns inflight
work must drain before propagating.

---

## 8. DB Seeding for In-Memory Dedup State

**Lesson (FederationBus):** `_known_peer_hashes: dict[str, set]` was populated only at runtime by
processing incoming peers. After a bridge restart, all previously seen cross-bridge hashes were
forgotten, causing every re-seen hash to be treated as a new escalation — generating duplicate
`federated_cluster` insights and spurious WebSocket alerts.

**Phase 36 fix:** `_seed_known_hashes_from_db()` called at the start of `run()` reads all `is_local=False`
federation clusters from the store and populates `_known_peer_hashes`. This is non-fatal — wrapped in
`try/except` so a DB error never prevents startup.

**Principle:** Any in-memory dedup structure that guards against re-escalation MUST be seeded from
persistent storage on startup. "Warm startup" vs. "cold startup" should be indistinguishable to
external observers.

---

## 9. Schema Version Registry

**Lesson:** Without a `schema_versions` table, there is no way to answer "what migrations have been
applied to this database?" during incident response, blue-green deployment, or schema rollback planning.
The absence of migration history is a production operations blocker.

**Phase 36 fix:** `schema_versions (phase INTEGER PRIMARY KEY, migration_name TEXT, applied_at REAL)`
bootstrapped at DB init with `INSERT OR IGNORE` for all phases 21–36. `get_schema_version()` returns
`MAX(phase)`. This enables `IF schema_version < N: apply_migration_N()` patterns in future phases.

---

## 10. Test Isolation: Factory Pattern + Fresh Store

**Pattern confirmed across Phases 32–36:** Every test file that tests stateful behavior must:
1. Create a fresh `Store` via `tempfile.mkdtemp()` + `Store(path)` (NOT `TemporaryDirectory` — WAL
   PermissionError on Windows cleanup)
2. Use factory functions (`create_monitoring_app`, `create_operator_app`) not module singletons
3. Stub external dependencies at the `sys.modules` level BEFORE importing the module under test
4. For async tests: use `IsolatedAsyncioTestCase` (not bare `asyncio.run()`) when testing methods that
   use `asyncio.create_task()` internally

**Web3 stub pattern (required for any test importing batcher.py):**
```python
for _mod_name in ("web3", "web3.exceptions", "eth_account"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
_web3_mod = sys.modules["web3"]
for _attr in ("AsyncWeb3", "AsyncHTTPProvider"):
    if not hasattr(_web3_mod, _attr):
        setattr(_web3_mod, _attr, MagicMock())
```
