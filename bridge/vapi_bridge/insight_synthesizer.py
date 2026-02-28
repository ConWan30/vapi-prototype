"""
InsightSynthesizer — Phase 35: "The Protocol Remembers Everything."
Longitudinal Insight Synthesis.

Runs every SYNTHESIZER_POLL_INTERVAL seconds (default 6 hours).
Three synthesis modes, each isolated in its own try/except:

  Mode 1 — Temporal window digests:
      Compresses protocol_insights into rolling 24h/7d/30d summaries
      (counts by type, dominant severity, top devices, template narrative).

  Mode 2 — Device trajectory labels:
      Updates per-device risk labels (stable/warming/critical/cleared)
      from 7-day alert history.

  Mode 3 — Federation topology persistence:
      Flags cluster hashes appearing across ≥2 distinct bridges over all time,
      distinguishing persistent cross-bridge threats from transient noise.

No external dependencies (no httpx, no anthropic) — always starts unconditionally.
"""
import asyncio
import logging
import time
from collections import defaultdict

log = logging.getLogger(__name__)

# Time windows for digest synthesis
_WINDOWS: dict[str, float] = {
    "24h":  86_400.0,
    "7d":   604_800.0,
    "30d": 2_592_000.0,
}

_SEVERITY_RANK: dict[str, int] = {"critical": 3, "medium": 2, "low": 1}

# Phase 36: Adaptive PITL threshold multipliers per risk label
_POLICY_MULTIPLIERS: dict[str, float] = {
    "critical": 0.70,   # tighten L4 threshold 30%
    "warming":  0.85,   # tighten L4 threshold 15%
    "cleared":  1.00,   # restore baseline
    "stable":   1.00,   # no adjustment
}
_POLICY_TTL_BUFFER_S: float = 3600.0  # 1h grace beyond next synthesis cycle

# Phase 37: Credential suspension duration bounds
_BASE_SUSPENSION_S: float = 604_800.0    # 7 days
_MAX_SUSPENSION_S:  float = 2_419_200.0  # 28 days


def _dominant_severity(sev_counts: dict) -> str:
    """Return the highest severity present in the counts dict."""
    for sev in ("critical", "medium", "low"):
        if sev_counts.get(sev, 0) > 0:
            return sev
    return "low"


def _risk_label(bot: int, high_risk: int, fed: int, anomaly: int, prior: str) -> str:
    """Compute device risk trajectory label from 7-day alert counts.

    Decision table:
      critical_signals (bot + fed) >= 2          → "critical"
      critical_signals >= 1 OR warming >= 3       → "warming"
      prior was critical/warming, now 0 signals   → "cleared"
      else                                        → "stable"
    """
    critical_signals = bot + fed
    warming_signals  = high_risk + anomaly
    if critical_signals >= 2:
        return "critical"
    if critical_signals >= 1 or warming_signals >= 3:
        return "warming"
    if prior in ("critical", "warming") and critical_signals == 0 and warming_signals == 0:
        return "cleared"
    return "stable"


class InsightSynthesizer:
    """Longitudinal insight synthesis background task (Phase 35)."""

    def __init__(self, store, cfg, poll_interval: float = 21600.0, chain=None):
        self._store = store
        self._cfg = cfg
        self._poll_interval = poll_interval
        self._running = True
        self._chain = chain  # Phase 37: optional ChainClient for credential enforcement

    async def run(self) -> None:
        log.info(
            "InsightSynthesizer started (Phase 35) poll=%.0fs (%.1fh)",
            self._poll_interval, self._poll_interval / 3600,
        )
        # Generate first digest immediately on startup — don't wait one full poll interval
        try:
            await self._synthesis_cycle()
        except Exception as exc:  # pragma: no cover
            log.warning("InsightSynthesizer: startup cycle error (non-fatal): %s", exc)
        while self._running:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._synthesis_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("InsightSynthesizer: cycle error (non-fatal): %s", exc)

    async def _synthesis_cycle(self) -> None:
        """Run all five synthesis modes + housekeeping, each isolated."""
        try:
            await self._synthesize_temporal_windows()
        except Exception as exc:
            log.warning("InsightSynthesizer: temporal mode error: %s", exc)
        try:
            await self._synthesize_device_trajectories()
        except Exception as exc:
            log.warning("InsightSynthesizer: trajectory mode error: %s", exc)
        try:
            await self._synthesize_federation_topology()
        except Exception as exc:
            log.warning("InsightSynthesizer: federation mode error: %s", exc)
        try:
            await self._synthesize_detection_policies()
        except Exception as exc:
            log.warning("InsightSynthesizer Mode 4 (detection policies) failed: %s", exc)
        try:
            await self._synthesize_credential_enforcement()
        except Exception as exc:
            log.warning("InsightSynthesizer Mode 5 (credential enforcement) failed: %s", exc)
        try:
            await self._run_housekeeping()
        except Exception as exc:
            log.warning("InsightSynthesizer: housekeeping error: %s", exc)

    # ------------------------------------------------------------------
    # Mode 1: Temporal Window Digests
    # ------------------------------------------------------------------
    async def _synthesize_temporal_windows(self) -> None:
        """Compress protocol_insights into rolling 24h / 7d / 30d digests."""
        now = time.time()
        for window_label, window_secs in _WINDOWS.items():
            since = now - window_secs
            insights = self._store.get_insights_since(since)

            if not insights:
                self._store.store_insight_digest(
                    window_label=window_label,
                    bot_farm_count=0, high_risk_count=0, federated_count=0,
                    anomaly_count=0, eligible_count=0,
                    dominant_severity="low",
                    top_devices=[],
                    narrative=f"No events in {window_label} window.",
                )
                continue

            type_counts: dict[str, int] = defaultdict(int)
            sev_counts:  dict[str, int] = defaultdict(int)
            device_freq: dict[str, int] = defaultdict(int)

            for row in insights:
                itype = row.get("insight_type", "")
                type_counts[itype] += 1
                sev_counts[row.get("severity", "low")] += 1
                dev = row.get("device_id", "")
                if dev:
                    device_freq[dev] += 1

            top_devices = sorted(device_freq, key=lambda d: -device_freq[d])[:5]
            dominant    = _dominant_severity(sev_counts)

            # Template-based narrative — no LLM dependency
            parts = []
            if type_counts.get("bot_farm_cluster", 0):
                parts.append(f"{type_counts['bot_farm_cluster']} bot-farm alert(s)")
            if type_counts.get("federated_cluster", 0):
                parts.append(f"{type_counts['federated_cluster']} cross-bridge confirmation(s)")
            if type_counts.get("high_risk_trajectory", 0):
                parts.append(f"{type_counts['high_risk_trajectory']} high-risk trajectory(s)")
            if type_counts.get("anomaly_reaction", 0):
                parts.append(f"{type_counts['anomaly_reaction']} anomaly reaction(s)")
            if type_counts.get("federated_topology", 0):
                parts.append(f"{type_counts['federated_topology']} federation topology event(s)")

            narrative_body = "; ".join(parts) if parts else "nominal activity"
            narrative = f"{window_label} digest: {narrative_body}."
            if top_devices:
                narrative += f" Top devices: {', '.join(d[:12] for d in top_devices[:3])}."

            self._store.store_insight_digest(
                window_label=window_label,
                bot_farm_count=type_counts.get("bot_farm_cluster", 0),
                high_risk_count=type_counts.get("high_risk_trajectory", 0),
                federated_count=type_counts.get("federated_cluster", 0),
                anomaly_count=type_counts.get("anomaly_reaction", 0),
                eligible_count=type_counts.get("near_eligibility", 0),
                dominant_severity=dominant,
                top_devices=top_devices,
                narrative=narrative,
            )
            log.debug(
                "InsightSynthesizer: %s digest stored (%d events, dominant=%s)",
                window_label, len(insights), dominant,
            )

    # ------------------------------------------------------------------
    # Mode 2: Device Risk Trajectory Labels
    # ------------------------------------------------------------------
    async def _synthesize_device_trajectories(self) -> None:
        """Update per-device risk trajectory labels from 7-day alert history."""
        since = time.time() - _WINDOWS["7d"]
        insights = self._store.get_insights_since(since)

        # Aggregate per-device alert counts from the 7-day window
        device_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"bot": 0, "high_risk": 0, "fed": 0, "anomaly": 0}
        )
        for row in insights:
            dev = row.get("device_id", "")
            if not dev:
                continue
            itype = row.get("insight_type", "")
            if itype == "bot_farm_cluster":
                device_counts[dev]["bot"] += 1
            elif itype == "high_risk_trajectory":
                device_counts[dev]["high_risk"] += 1
            elif itype == "federated_cluster":
                device_counts[dev]["fed"] += 1
            elif itype == "anomaly_reaction":
                device_counts[dev]["anomaly"] += 1

        for device_id, counts in device_counts.items():
            existing   = self._store.get_device_risk_label(device_id)
            prior      = existing["risk_label"] if existing else "stable"
            new_label  = _risk_label(
                bot=counts["bot"], high_risk=counts["high_risk"],
                fed=counts["fed"],  anomaly=counts["anomaly"],
                prior=prior,
            )
            self._store.set_device_risk_label(
                device_id=device_id,
                risk_label=new_label,
                label_evidence=counts,
                prior_label=prior,
            )
            if new_label != prior:
                log.info(
                    "InsightSynthesizer: device %s trajectory %s → %s",
                    device_id[:16], prior, new_label,
                )

    # ------------------------------------------------------------------
    # Mode 3: Federation Topology Persistence
    # ------------------------------------------------------------------
    async def _synthesize_federation_topology(self) -> None:
        """Flag cross-bridge cluster hashes with multi-bridge persistence (all time)."""
        all_remote = self._store.get_federation_clusters(limit=1000, is_local=False)
        if not all_remote:
            return

        # Group by cluster_hash — count distinct bridge_ids
        hash_bridges: dict[str, set] = defaultdict(set)
        for row in all_remote:
            h = row.get("cluster_hash", "")
            b = row.get("bridge_id", "")
            if h and b:
                hash_bridges[h].add(b)

        # Clusters confirmed on ≥2 distinct bridges are persistent cross-network threats
        persistent = {h: bids for h, bids in hash_bridges.items() if len(bids) >= 2}
        if not persistent:
            return

        count = len(persistent)
        severity = "critical" if count >= 5 else "medium"
        preview = ", ".join(list(persistent)[:3])
        summary = (
            f"federation_topology: {count} cluster hash(es) confirmed across"
            f" ≥2 distinct bridges (hashes: {preview})."
        )
        self._store.store_protocol_insight(
            insight_type="federated_topology",
            content=summary,
            device_id="",
            severity=severity,
        )
        log.info(
            "InsightSynthesizer: %d persistent cross-bridge cluster(s) identified", count
        )

    # ------------------------------------------------------------------
    # Mode 4: Adaptive Detection Policies (Phase 36)
    # ------------------------------------------------------------------
    async def _synthesize_detection_policies(self) -> None:
        """Translate risk labels into per-device PITL threshold multipliers.

        For each label tier (critical/warming/cleared/stable), query devices with
        that label and write a detection_policy with the corresponding multiplier.
        This makes InsightSynthesizer's memory directly drive L4 detection tightness.
        """
        if not getattr(self._cfg, "adaptive_thresholds_enabled", True):
            return
        floor = float(getattr(self._cfg, "policy_multiplier_floor", 0.5))
        expires_at = time.time() + self._poll_interval + _POLICY_TTL_BUFFER_S

        for label_name in ("critical", "warming", "cleared", "stable"):
            rows = self._store.get_devices_by_risk_label(label_name)
            multiplier = max(floor, _POLICY_MULTIPLIERS.get(label_name, 1.0))
            for row in rows:
                device_id = row["device_id"]
                prior = self._store.get_detection_policy(device_id)
                prior_mult = (prior or {}).get("multiplier", 1.0)
                self._store.store_detection_policy(
                    device_id=device_id,
                    multiplier=multiplier,
                    basis_label=label_name,
                    expires_at=expires_at,
                )
                if abs(prior_mult - multiplier) > 0.01:
                    direction = "tightened" if multiplier < prior_mult else "relaxed"
                    self._store.store_protocol_insight(
                        insight_type="policy_adjustment",
                        device_id=device_id,
                        content=(
                            f"L4 threshold {direction}: "
                            f"{prior_mult:.2f}\u2192{multiplier:.2f} (basis: {label_name})"
                        ),
                        severity="low" if multiplier >= 1.0 else "medium",
                    )
        log.info(
            "InsightSynthesizer Mode 4: detection policies synthesized (floor=%.2f)", floor
        )

    # ------------------------------------------------------------------
    # Mode 5: Credential Enforcement (Phase 37)
    # ------------------------------------------------------------------
    async def _synthesize_credential_enforcement(self) -> None:
        """Translate consecutive critical trajectory labels into PHGCredential suspensions.

        Graduation rule: consecutive_critical >= min_consecutive → suspend on-chain.
        Duration is exponential: base_s × 2^(consecutive - min_consecutive), capped at max_s.
        Reversal: a cleared-label device that is suspended gets auto-reinstated.
        Counter reset: stable/warming devices have their consecutive counter cleared.

        All on-chain calls are non-fatal — DB suspension is always written on success,
        chain failure is logged and Mode 5 continues with the next device.
        """
        import hashlib as _hl

        if not getattr(self._cfg, "phg_credential_enforcement_enabled", True):
            return

        min_consec = int(getattr(self._cfg, "credential_enforcement_min_consecutive", 2))
        base_s     = float(getattr(self._cfg, "credential_suspension_base_days", 7.0)) * 86400.0
        max_s      = float(getattr(self._cfg, "credential_suspension_max_days", 28.0)) * 86400.0

        # --- Suspend: critical devices with sufficient consecutive windows ---
        for row in self._store.get_devices_by_risk_label("critical"):
            device_id  = row["device_id"]
            credential = self._store.get_credential_mint(device_id)
            if credential is None:
                continue  # no credential to suspend
            consecutive = self._store.increment_consecutive_critical(device_id)
            if consecutive < min_consec:
                continue  # not yet graduated
            if self._store.is_credential_suspended(device_id):
                continue  # already suspended

            digest = self._store.get_latest_digest("7d")
            evidence_bytes = _hl.sha256(
                f"{device_id}:{digest['id'] if digest else 'none'}".encode()
            ).digest()
            exponent   = consecutive - min_consec
            duration_s = int(min(base_s * (2 ** exponent), max_s))

            try:
                await self._chain.suspend_phg_credential(device_id, evidence_bytes, duration_s)
            except Exception as exc:
                log.warning("Mode 5: on-chain suspend failed device=%s: %s", device_id[:16], exc)

            self._store.store_credential_suspension(
                device_id, evidence_bytes.hex(), time.time() + duration_s
            )
            self._store.store_protocol_insight(
                insight_type="credential_suspended",
                device_id=device_id,
                content=(
                    f"PHGCredential suspended: {consecutive} consecutive critical windows, "
                    f"{duration_s // 86400}d. Evidence: {evidence_bytes.hex()[:16]}"
                ),
                severity="critical",
            )
            log.info(
                "Mode 5: suspended device=%s consecutive=%d duration=%dd",
                device_id[:16], consecutive, duration_s // 86400,
            )

        # --- Reinstate: cleared devices that are currently suspended ---
        for row in self._store.get_devices_by_risk_label("cleared"):
            device_id = row["device_id"]
            if not self._store.is_credential_suspended(device_id):
                continue
            self._store.reset_consecutive_critical(device_id)
            try:
                await self._chain.reinstate_phg_credential(device_id)
            except Exception as exc:
                log.warning("Mode 5: on-chain reinstate failed device=%s: %s", device_id[:16], exc)
            self._store.clear_credential_suspension(device_id)
            self._store.store_protocol_insight(
                insight_type="credential_reinstated",
                device_id=device_id,
                content="PHGCredential reinstated: device trajectory cleared",
                severity="low",
            )
            log.info("Mode 5: reinstated device=%s", device_id[:16])

        # --- Reset consecutive counter for stable/warming (no suspension yet) ---
        for label in ("stable", "warming"):
            for row in self._store.get_devices_by_risk_label(label):
                self._store.reset_consecutive_critical(row["device_id"])

        log.info("InsightSynthesizer Mode 5: credential enforcement complete")

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    async def _run_housekeeping(self) -> None:
        """Prune old digests and stale insights per configured retention windows."""
        digest_age  = float(getattr(self._cfg, "digest_retention_days", 90.0))
        insight_age = 30.0  # matches existing prune_old_insights default
        pd = self._store.prune_old_digests(age_days=digest_age)
        pi = self._store.prune_old_insights(age_days=insight_age)
        if pd or pi:
            log.info(
                "InsightSynthesizer: housekeeping pruned %d digest(s), %d insight(s)",
                pd, pi,
            )
