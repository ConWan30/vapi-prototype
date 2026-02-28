"""
ProactiveMonitor — Phase 32: The Protocol Thinks Ahead

Background asyncio task that autonomously surveys protocol state every poll_interval
seconds and dispatches proactive alerts when threats or anomalies are detected —
without waiting for operator queries.

Three surveillance checks per cycle:
  1. Anomaly clusters     — NetworkCorrelationDetector flagged clusters (dedup by frozenset)
  2. High-risk trajectories — BehavioralArchaeologist devices with warning
  3. Eligibility horizons   — Leaderboard devices approaching legitimate eligibility

Alerts are persisted to the protocol_insights table AND broadcast over WebSocket.

This is the first VAPI component that initiates action based on its own analysis
rather than waiting for an external query — autonomous protocol cognition.
"""

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)

# Module-level import with fallback so tests can patch vapi_bridge.proactive_monitor.ws_broadcast
try:
    from .transports.http import ws_broadcast
except Exception:
    async def ws_broadcast(message: str) -> None:  # noqa: F811
        pass


class ProactiveMonitor:
    """Autonomous protocol surveillance task (Phase 32).

    Polls every poll_interval seconds and runs three surveillance checks:
    cluster detection, trajectory analysis, and eligibility horizon tracking.
    All errors are caught non-fatally — the monitor never crashes the bridge.
    """

    def __init__(self, store, behavioral_arch, network_detector, agent, cfg,
                 poll_interval: float = 60.0):
        self._store = store
        self._behavioral_arch = behavioral_arch
        self._network_detector = network_detector
        self._agent = agent
        self._cfg = cfg
        self._poll_interval = poll_interval
        self._running = True
        # Phase 36: time-bounded dedup dict (frozenset → monotonic timestamp)
        # Replaced unbounded set to prevent memory growth on long-running bridges.
        self._known_flagged_clusters: dict[frozenset, float] = {}

    async def run(self) -> None:
        """Main loop — poll every _poll_interval seconds."""
        log.info("ProactiveMonitor started (interval=%.0fs)", self._poll_interval)
        while self._running:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._monitor_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ProactiveMonitor cycle error (non-fatal): %s", exc)

    async def _monitor_cycle(self) -> None:
        """Run all three surveillance checks."""
        await self._check_anomaly_clusters()
        await self._check_high_risk_trajectories()
        await self._check_eligibility_horizons()

    def _evict_stale_clusters(self) -> int:
        """Remove cluster dedup entries older than 24h. Returns eviction count (Phase 36)."""
        cutoff = time.monotonic() - 86400.0
        stale = [k for k, ts in self._known_flagged_clusters.items() if ts < cutoff]
        for k in stale:
            del self._known_flagged_clusters[k]
        return len(stale)

    async def _check_anomaly_clusters(self) -> None:
        """Alert on newly-flagged bot-farm clusters (dedup by device-set frozenset)."""
        self._evict_stale_clusters()
        try:
            clusters = self._network_detector.detect_clusters()
        except Exception as exc:
            log.warning("_check_anomaly_clusters: detect_clusters error: %s", exc)
            return
        for cluster in clusters:
            if not cluster.is_flagged:
                continue
            key = frozenset(cluster.device_ids)
            if key in self._known_flagged_clusters:
                continue
            self._known_flagged_clusters[key] = time.monotonic()
            n = len(cluster.device_ids)
            preview = ", ".join(cluster.device_ids[:5]) + (" …" if n > 5 else "")
            content = (
                f"Bot-farm cluster detected: {n} devices, "
                f"suspicion={cluster.farm_suspicion_score:.2f}, "
                f"avg_intra_distance={cluster.avg_intra_distance:.3f}. "
                f"Devices: {preview}"
            )
            severity = "critical" if cluster.farm_suspicion_score > 0.85 else "medium"
            await self._dispatch_alert(
                insight_type="bot_farm_cluster",
                device_id="",
                content=content,
                severity=severity,
                extra={
                    "cluster_id": cluster.cluster_id,
                    "device_count": n,
                },
            )

    async def _check_high_risk_trajectories(self) -> None:
        """Alert on devices with high-risk behavioral trajectories."""
        try:
            risky = self._behavioral_arch.get_high_risk_devices(threshold=0.7)
        except Exception as exc:
            log.warning("_check_high_risk_trajectories: error: %s", exc)
            return
        for device_id in risky:
            try:
                report = self._behavioral_arch.analyze_device(device_id)
            except Exception:
                continue
            if not report.warning:
                continue
            content = (
                f"High-risk trajectory: {report.warning} "
                f"(warmup_attack={report.warmup_attack_score:.2f}, "
                f"burst_farming={report.burst_farming_score:.2f}, "
                f"sessions={report.session_count})"
            )
            await self._dispatch_alert(
                insight_type="high_risk_trajectory",
                device_id=device_id,
                content=content,
                severity="medium",
                extra={
                    "warmup_attack_score": report.warmup_attack_score,
                    "burst_farming_score": report.burst_farming_score,
                },
            )

    async def _check_eligibility_horizons(self) -> None:
        """Alert when devices cross the eligibility threshold (PHG score > 0)."""
        try:
            leaderboard = self._store.get_leaderboard(100)
        except Exception as exc:
            log.warning("_check_eligibility_horizons: error: %s", exc)
            return
        for entry in leaderboard:
            score = entry.get("phg_score", 0)
            device_id = entry.get("device_id", "")
            if score < 1:
                continue
            content = (
                f"Device {device_id[:16]} is eligible for tournament entry "
                f"(cumulative_score={score})."
            )
            await self._dispatch_alert(
                insight_type="near_eligibility",
                device_id=device_id,
                content=content,
                severity="low",
                extra={"cumulative_score": score},
            )

    async def _dispatch_alert(self, insight_type: str, device_id: str,
                               content: str, severity: str, extra: dict = None) -> None:
        """Persist alert to protocol_insights table + broadcast over WebSocket."""
        try:
            self._store.store_protocol_insight(
                insight_type=insight_type,
                device_id=device_id,
                content=content,
                severity=severity,
            )
        except Exception as exc:
            log.warning("_dispatch_alert: store error: %s", exc)

        event = {
            "type": "proactive_alert",
            "insight_type": insight_type,
            "device_id": device_id,
            "content": content,
            "severity": severity,
            "timestamp": time.time(),
        }
        if extra:
            event.update(extra)
        try:
            await ws_broadcast(json.dumps(event))
        except Exception as exc:
            log.warning("_dispatch_alert: ws_broadcast error: %s", exc)
