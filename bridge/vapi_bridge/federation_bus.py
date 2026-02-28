"""
FederationBus — Phase 34

Background asyncio task that shares privacy-preserving cluster fingerprints with
peer bridge instances every poll_interval seconds. When a cluster fingerprint
appears on ≥2 independent bridges, a federated_cluster alert is dispatched.

Privacy model: only 16-char SHA-256 hex hashes of sorted device-ID sets are
shared — raw device identities never leave the originating bridge.

Three operations per sync cycle:
  1. _publish_local_clusters — detect flagged clusters locally, store as is_local=True
  2. _fetch_peer_clusters    — GET /federation/clusters from each peer
  3. _process_peer_clusters  — store remote; check cross-confirmation; dispatch escalation
"""
import asyncio
import hashlib
import json
import logging
import time

log = logging.getLogger(__name__)

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

# Module-level import with fallback so tests can patch vapi_bridge.federation_bus.ws_broadcast
try:
    from .transports.http import ws_broadcast
except Exception:
    async def ws_broadcast(message: str) -> None:
        pass


def compute_cluster_hash(device_ids: list) -> str:
    """Stable 16-char hex fingerprint of a device cluster (non-reversible).

    Uses sorted device IDs so cluster identity is order-independent.
    """
    return hashlib.sha256("|".join(sorted(device_ids)).encode()).hexdigest()[:16]


def compute_bridge_id(api_key: str) -> str:
    """Anonymous 16-char bridge identity derived from api_key (non-reversible)."""
    return hashlib.sha256(f"bridge:{api_key}".encode()).hexdigest()[:16]


class FederationBus:
    """Cross-bridge cluster intelligence task (Phase 34).

    Polls peer VAPI bridge instances and exchanges privacy-preserving cluster
    fingerprints. When the same fingerprint is detected on ≥2 independent bridges,
    a federated_cluster protocol insight is dispatched and optionally anchored on-chain.
    """

    def __init__(self, store, network_detector, chain, cfg, poll_interval: float = 120.0):
        self._store = store
        self._network_detector = network_detector
        self._chain = chain
        self._cfg = cfg
        self._poll_interval = poll_interval
        self._running = True
        # Per-peer dedup: peer_url → set[cluster_hash] already processed this session
        self._known_peer_hashes: dict[str, set] = {}
        self._bridge_id = compute_bridge_id(
            getattr(cfg, "federation_api_key", "") or "default"
        )

    def _get_peers(self) -> list:
        raw = getattr(self._cfg, "federation_peers", "")
        return [p.strip() for p in raw.split(",") if p.strip()] if raw else []

    def _seed_known_hashes_from_db(self) -> None:
        """Pre-populate _known_peer_hashes from DB on startup (Phase 36).

        Prevents duplicate escalations for clusters already processed in a prior
        session. Non-fatal — startup proceeds even if seeding fails.
        """
        try:
            rows = self._store.get_federation_clusters(limit=10000, is_local=False)
            for row in rows:
                peer_url = row.get("peer_url", "")
                h = row.get("cluster_hash", "")
                if peer_url and h:
                    self._known_peer_hashes.setdefault(peer_url, set()).add(h)
            total = sum(len(s) for s in self._known_peer_hashes.values())
            if total:
                log.info("FederationBus: seeded %d known peer hashes from DB", total)
        except Exception as exc:
            log.warning("FederationBus: DB seeding failed (non-fatal): %s", exc)

    async def run(self) -> None:
        """Main loop — sync with peers every _poll_interval seconds."""
        log.info(
            "FederationBus started (interval=%.0fs, bridge_id=%s)",
            self._poll_interval,
            self._bridge_id,
        )
        # Phase 36: Seed known hashes from DB before first publish (prevents re-escalation)
        self._seed_known_hashes_from_db()
        # Publish local clusters immediately on startup
        await self._publish_local_clusters()
        while self._running:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._sync_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("FederationBus cycle error (non-fatal): %s", exc)

    async def _sync_cycle(self) -> None:
        """Publish local clusters; fetch and process all peer clusters."""
        await self._publish_local_clusters()
        peers = self._get_peers()
        for peer_url in peers:
            try:
                remote_clusters = await self._fetch_peer_clusters(peer_url)
                await self._process_peer_clusters(peer_url, remote_clusters)
            except Exception as exc:
                log.warning("FederationBus: peer %s fetch error: %s", peer_url, exc)

    async def _publish_local_clusters(self) -> None:
        """Detect flagged clusters locally and store in federation_registry as is_local=True."""
        try:
            clusters = self._network_detector.detect_clusters()
        except Exception as exc:
            log.warning("FederationBus: local detect_clusters error: %s", exc)
            return
        for cluster in clusters:
            if not cluster.is_flagged:
                continue
            h = compute_cluster_hash(cluster.device_ids)
            bucket = "critical" if cluster.farm_suspicion_score > 0.85 else "medium"
            try:
                self._store.store_federation_cluster(
                    cluster_hash=h,
                    peer_url="",
                    device_count=len(cluster.device_ids),
                    suspicion_bucket=bucket,
                    bridge_id=self._bridge_id,
                    is_local=True,
                )
            except Exception as exc:
                log.warning("FederationBus: store local cluster error: %s", exc)

    async def _fetch_peer_clusters(self, peer_url: str) -> list:
        """Fetch /federation/clusters from a peer bridge via httpx.

        Returns empty list when httpx is not installed or peer is unreachable.
        """
        if not _HTTPX_AVAILABLE:
            log.debug("FederationBus: httpx not available — skipping peer %s", peer_url)
            return []
        api_key = getattr(self._cfg, "federation_api_key", "")
        url = peer_url.rstrip("/") + "/federation/clusters"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params={"api_key": api_key, "limit": 50})
            resp.raise_for_status()
            return resp.json()

    async def _process_peer_clusters(self, peer_url: str, remote_clusters: list) -> None:
        """Store new peer clusters; dispatch escalation for cross-confirmed hashes."""
        known = self._known_peer_hashes.setdefault(peer_url, set())
        for c in remote_clusters:
            h = c.get("cluster_hash", "")
            if not h or h in known:
                continue
            known.add(h)
            bridge_id = c.get("bridge_id", peer_url[:16])
            try:
                self._store.store_federation_cluster(
                    cluster_hash=h,
                    peer_url=peer_url,
                    device_count=c.get("device_count", 0),
                    suspicion_bucket=c.get("suspicion_bucket", "medium"),
                    bridge_id=bridge_id,
                    is_local=False,
                )
            except Exception as exc:
                log.warning("FederationBus: store remote cluster error: %s", exc)

        # Check for cross-confirmed hashes after processing new peer data
        try:
            confirmed = self._store.get_cross_confirmed_hashes(min_peers=2)
        except Exception as exc:
            log.warning("FederationBus: get_cross_confirmed_hashes error: %s", exc)
            return
        for h in confirmed:
            await self._dispatch_escalation(h)

    async def _dispatch_escalation(self, cluster_hash: str) -> None:
        """Persist federated_cluster insight + broadcast via WebSocket + optional on-chain anchor."""
        content = (
            f"Cross-bridge confirmed cluster: hash={cluster_hash} "
            f"seen on \u22652 independent bridge instances. "
            f"Coordinated bot farm operating across deployment shards."
        )
        try:
            self._store.store_protocol_insight(
                insight_type="federated_cluster",
                device_id="",
                content=content,
                severity="critical",
            )
        except Exception as exc:
            log.warning("FederationBus: insight store error: %s", exc)

        event = {
            "type": "proactive_alert",
            "insight_type": "federated_cluster",
            "cluster_hash": cluster_hash,
            "content": content,
            "severity": "critical",
            "timestamp": time.time(),
        }
        try:
            await ws_broadcast(json.dumps(event))
        except Exception as exc:
            log.warning("FederationBus: ws_broadcast error: %s", exc)

        # Optional on-chain anchor — non-fatal
        try:
            if self._chain:
                await self._chain.report_federated_cluster(cluster_hash)
        except Exception as exc:
            log.warning("FederationBus: chain anchor error (non-fatal): %s", exc)
