"""
Record Batcher — Accumulates PoAC records and submits in batches.

Handles:
  - Accumulation up to batch_size or batch_timeout
  - Batch submission via ChainClient.verify_batch()
  - Bounty evidence auto-submission for records with bounty_id > 0
  - Exponential backoff retry with jitter for failed submissions
  - Dead-letter queue after max_retries
"""

import asyncio
import json
import logging
import random
import time

from .chain import ChainClient
from .codec import PoACRecord
from .config import Config
from .store import (
    Store,
    STATUS_BATCHED,
    STATUS_DEAD_LETTER,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUBMITTED,
    STATUS_VERIFIED,
)

log = logging.getLogger(__name__)


class Batcher:
    """Async record batcher with retry logic."""

    def __init__(self, cfg: Config, store: Store, chain: ChainClient):
        self._cfg = cfg
        self._store = store
        self._chain = chain
        self._queue: asyncio.Queue[tuple[PoACRecord, bytes]] = asyncio.Queue(maxsize=1000)
        self._running = False
        # Counter for records dropped when queue is full (exposed via status/metrics)
        self._dropped_records: int = 0

    async def enqueue(self, record: PoACRecord, raw_data: bytes):
        """Add a validated record to the batch queue.

        Uses put_nowait to avoid indefinite blocking on a full queue.
        If the queue is full (QueueFull), the record is counted as dropped
        and a warning is emitted. This prevents unbounded back-pressure on
        callers (e.g. the HTTP ingest handler) while keeping the counter
        observable for alerting.
        """
        try:
            self._queue.put_nowait((record, raw_data))
        except asyncio.QueueFull:
            self._dropped_records += 1
            log.warning(
                "Batcher queue full (maxsize=1000) — record dropped "
                "(total_dropped=%d, device=%s)",
                self._dropped_records,
                getattr(record, "device_id_hex", "unknown")[:16],
            )
            # Mirror drop count into the shared monitoring state for Prometheus /metrics
            try:
                from .monitoring import state as _mon_state
                _mon_state.record_dropped()
            except Exception:
                pass  # monitoring integration is always non-fatal

    @property
    def dropped_records(self) -> int:
        """Total number of records dropped since startup due to a full queue."""
        return self._dropped_records

    async def run(self):
        """Main batcher loop — runs until cancelled."""
        self._running = True
        log.info(
            "Batcher started (batch_size=%d, timeout=%ds, max_retries=%d)",
            self._cfg.batch_size,
            self._cfg.batch_timeout_s,
            self._cfg.max_retries,
        )

        # Phase 36: Startup recovery — re-enqueue pending records from DB
        try:
            pending = self._store.get_pending_records(limit=500)
            if pending:
                log.info("Batcher: re-enqueuing %d pending records from DB", len(pending))
            _skipped = 0
            for row in pending:
                raw = row.get("raw_data")
                if raw is None:
                    continue
                try:
                    from .codec import parse_record
                    record = parse_record(bytes(raw))
                    # Restore device_id from DB — parse_record doesn't derive it
                    # (device_id is keccak256(pubkey), computed during ingest, not in raw bytes)
                    db_device_id = row.get("device_id", "")
                    if db_device_id:
                        record.device_id = bytes.fromhex(db_device_id)
                    self._queue.put_nowait((record, bytes(raw)))
                except Exception as _rec_exc:
                    _skipped += 1
                    log.debug("Batcher startup: skipping corrupt record (row_id=%s): %s",
                              row.get("id", "?"), _rec_exc)
            if _skipped:
                log.warning("Batcher startup: %d corrupt record(s) skipped", _skipped)
        except Exception as exc:
            log.warning("Batcher: startup recovery failed (non-fatal): %s", exc)

        # Start retry loop in parallel
        retry_task = asyncio.create_task(self._retry_loop())

        def _retry_task_done(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception() is not None:
                log.error("Batcher retry_task died unexpectedly: %s", t.exception())

        retry_task.add_done_callback(_retry_task_done)

        try:
            while self._running:
                batch = await self._collect_batch()
                if batch:
                    await self._submit_batch(batch)
        except asyncio.CancelledError:
            log.info("Batcher shutting down — draining remaining queue")
            # Phase 36: Bounded drain — flush in-flight records before propagating CancelledError
            drained = 0
            while not self._queue.empty():
                try:
                    batch = await asyncio.wait_for(self._collect_batch(), timeout=5.0)
                    if batch:
                        await self._submit_batch(batch)
                        drained += len(batch)
                except Exception as _drain_exc:
                    log.warning("Batcher shutdown drain error: %s", _drain_exc)
                    break
            if drained:
                log.info("Batcher: drained %d record(s) on shutdown", drained)
            raise
        finally:
            self._running = False
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass

    async def _collect_batch(self) -> list[tuple[PoACRecord, bytes]]:
        """Collect records until batch_size or timeout."""
        batch = []

        try:
            # Wait for the first record (blocks indefinitely)
            item = await self._queue.get()
            batch.append(item)
        except asyncio.CancelledError:
            raise

        # Collect more records up to batch_size, with timeout
        deadline = time.monotonic() + self._cfg.batch_timeout_s
        while len(batch) < self._cfg.batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=remaining
                )
                batch.append(item)
            except asyncio.TimeoutError:
                break

        return batch

    async def _submit_batch(self, batch: list[tuple[PoACRecord, bytes]]):
        """Submit a batch of records to the chain."""
        records = [r for r, _ in batch]
        record_hashes = [r.record_hash_hex for r in records]
        device_ids = [r.device_id for r in records]

        # Mark records as batched
        self._store.batch_update_status(record_hashes, STATUS_BATCHED)

        # Create submission tracking entry
        sub_id = self._store.create_submission(record_hashes)

        try:
            if len(records) == 1:
                schema_ver = getattr(records[0], "schema_version", 0)
                if schema_ver > 0:
                    # Route through schema-aware path (verifyPoACWithSchema on-chain)
                    tx_hash = await self._chain.verify_poac(
                        device_ids[0], records[0].raw_body,
                        records[0].signature, schema_ver,
                    )
                else:
                    tx_hash = await self._chain.verify_single(
                        device_ids[0], records[0]
                    )
            else:
                # Check if any record needs schema-aware routing
                if any(getattr(r, "schema_version", 0) > 0 for r in records):
                    # Submit each record individually via schema-aware path
                    for dev_id, rec in zip(device_ids, records):
                        sv = getattr(rec, "schema_version", 0)
                        if sv > 0:
                            tx_hash = await self._chain.verify_poac(
                                dev_id, rec.raw_body, rec.signature, sv
                            )
                        else:
                            tx_hash = await self._chain.verify_single(dev_id, rec)
                else:
                    tx_hash = await self._chain.verify_batch(device_ids, records)

            self._store.update_submission(
                sub_id, status=STATUS_SUBMITTED, tx_hash=tx_hash
            )
            self._store.batch_update_status(record_hashes, STATUS_SUBMITTED)

            # Wait for confirmation
            try:
                receipt = await self._chain.wait_for_receipt(tx_hash, timeout=60)
                if receipt.get("status") == 1:
                    self._store.update_submission(
                        sub_id, status=STATUS_VERIFIED
                    )
                    self._store.batch_update_status(
                        record_hashes, STATUS_VERIFIED
                    )
                    # Update per-device verified counts (NOMINAL records only)
                    device_counts: dict[str, int] = {}
                    for r in records:
                        if r.inference_result == 0x20:  # NOMINAL only — cheats don't advance PHG
                            did = r.device_id_hex
                            device_counts[did] = device_counts.get(did, 0) + 1
                    for did, count in device_counts.items():
                        self._store.increment_device_verified(did, count)

                    log.info(
                        "Batch verified: %d records, tx=%s",
                        len(records), tx_hash[:16],
                    )

                    # Phase 22: PHG checkpoint trigger (NOMINAL records only)
                    if getattr(self._cfg, "phg_registry_address", ""):
                        await self._maybe_commit_phg_checkpoints(records)

                    # Auto-submit bounty evidence for records with bounty_id > 0
                    await self._submit_bounty_evidence(records)
                else:
                    raise RuntimeError(f"Transaction reverted: {tx_hash}")

            except asyncio.TimeoutError:
                log.warning("Tx confirmation timeout: %s", tx_hash[:16])
                self._store.update_submission(
                    sub_id, status=STATUS_FAILED, error="confirmation timeout"
                )
                self._store.batch_update_status(record_hashes, STATUS_FAILED)

        except Exception as e:
            err_str = str(e)
            # P256PrecompileEmptyReturn (0xf46a06ea) = IoTeX testnet P256 precompile
            # not available via this RPC endpoint. Dead-letter immediately — retrying
            # won't help; this is a testnet infrastructure limitation, not a transient error.
            if "f46a06ea" in err_str:
                log.warning(
                    "Batch dead-lettered: IoTeX testnet P256 precompile unavailable "
                    "(0xf46a06ea). On-chain submission disabled for this session. "
                    "Local PITL pipeline unaffected."
                )
                self._store.update_submission(
                    sub_id, status=STATUS_DEAD_LETTER, error="P256PrecompileEmptyReturn (testnet)"
                )
                self._store.batch_update_status(record_hashes, STATUS_DEAD_LETTER)
            elif "insufficient funds" in err_str:
                # Dead-letter immediately — retrying won't help without more gas.
                # Local PITL pipeline is unaffected; top up wallet to re-enable on-chain anchoring.
                log.debug("Batch dead-lettered: insufficient funds for gas (top up wallet to re-enable on-chain anchoring)")
                self._store.update_submission(
                    sub_id, status=STATUS_DEAD_LETTER, error="insufficient funds for gas"
                )
                self._store.batch_update_status(record_hashes, STATUS_DEAD_LETTER)
            elif any(pat in err_str.lower() for pat in (
                "out of gas", "intrinsic gas too low", "gas required exceeds allowance",
                "transaction reverted", "execution reverted", "contract revert",
            )):
                # Phase 52: EVM gas/revert errors that are permanent (not transient network).
                # Dead-letter rather than burning retry budget on guaranteed failures.
                log.warning("Batch dead-lettered: EVM revert/gas error — %s", err_str[:200])
                self._store.update_submission(
                    sub_id, status=STATUS_DEAD_LETTER, error=err_str[:500]
                )
                self._store.batch_update_status(record_hashes, STATUS_DEAD_LETTER)
            else:
                log.error("Batch submission failed: %s", e)
                self._store.update_submission(
                    sub_id, status=STATUS_FAILED, error=err_str[:500]
                )
                self._store.batch_update_status(record_hashes, STATUS_FAILED)

    async def _maybe_commit_phg_checkpoints(self, records: list[PoACRecord]):
        """Commit PHG checkpoints for devices that crossed the interval boundary.

        Only fires for NOMINAL (0x20) records. The checkpoint is committed when
        records_verified crosses a multiple of phg_checkpoint_interval.
        """
        interval = getattr(self._cfg, "phg_checkpoint_interval", 10)
        if interval <= 0:
            return

        # Collect unique NOMINAL device IDs from this batch
        nominal_device_ids: set[str] = set()
        for r in records:
            if r.inference_result == 0x20:
                nominal_device_ids.add(r.device_id_hex)

        for dev_id in nominal_device_ids:
            try:
                verified_count = self._store.get_verified_nominal_count(dev_id)
                if verified_count > 0 and verified_count % interval == 0:
                    checkpoint_data = self._store.get_phg_checkpoint_data(dev_id)
                    if checkpoint_data is None:
                        continue
                    score_delta = checkpoint_data["phg_score"]
                    bio_hash = checkpoint_data["biometric_hash"]
                    # Phase 27: apply behavioral modifier — reduce delta for warmup/burst attacks
                    try:
                        from .behavioral_archaeologist import BehavioralArchaeologist
                        _report = BehavioralArchaeologist(self._store).analyze_device(dev_id)
                        _mult = max(0.0, 1.0
                            - _report.warmup_attack_score * 0.8
                            - _report.burst_farming_score * 0.5)
                        if _mult < 0.99:
                            log.info(
                                "Behavioral PHG modifier: device=%s warmup=%.2f burst=%.2f "
                                "mult=%.2f score %d→%d",
                                dev_id[:16], _report.warmup_attack_score,
                                _report.burst_farming_score, _mult,
                                score_delta, int(score_delta * _mult),
                            )
                        score_delta = int(score_delta * _mult)
                    except Exception as _exc:
                        log.debug("Behavioral PHG modifier skipped (non-fatal): %s", _exc)
                    # Compute record count delta since last committed checkpoint.
                    # Using last_checkpoint.record_count (cumulative at last commit) avoids
                    # the silent data corruption when bridge restarts after records accumulate
                    # past an interval boundary (where interval != actual delta).
                    last_cp = self._store.get_last_phg_checkpoint(dev_id)
                    last_cp_count = last_cp["record_count"] if last_cp else 0
                    count_delta = max(0, verified_count - last_cp_count)
                    tx_hash = await self._chain.commit_phg_checkpoint(
                        dev_id, score_delta, count_delta, bio_hash
                    )
                    if tx_hash:
                        confirmed = False
                        try:
                            receipt = await asyncio.wait_for(
                                self._chain.wait_for_receipt(tx_hash, timeout=60),
                                timeout=65.0,
                            )
                            confirmed = receipt.get("status") == 1
                            if not confirmed:
                                log.warning(
                                    "PHG checkpoint reverted: device=%s tx=%s",
                                    dev_id[:16], tx_hash[:16],
                                )
                        except asyncio.TimeoutError:
                            confirmed = False
                            log.warning(
                                "PHG checkpoint receipt timeout: device=%s tx=%s",
                                dev_id[:16], tx_hash[:16],
                            )
                        self._store.store_phg_checkpoint(
                            dev_id, score_delta, verified_count,
                            bio_hash.hex(), tx_hash,
                            cumulative_score=checkpoint_data["cumulative_score"],
                            confirmed=confirmed,
                        )
                        log.info(
                            "PHGCheckpoint: device=%s verified=%d score=%d confirmed=%s tx=%s",
                            dev_id[:16], verified_count, score_delta, confirmed, tx_hash[:16],
                        )
                        # Phase 28: mint PHGCredential if device has a PITL proof and no credential yet
                        try:
                            if self._store.get_credential_mint(dev_id) is None:
                                _proof = self._store.get_latest_pitl_proof(dev_id)
                                if _proof:
                                    _cred_tx = await self._chain.mint_phg_credential(
                                        dev_id,
                                        _proof["nullifier_hash"],
                                        _proof["feature_commitment"],
                                        _proof["humanity_prob_int"],
                                    )
                                    if _cred_tx:
                                        self._store.store_credential_mint(dev_id, 1, _cred_tx)
                                        log.info(
                                            "Phase 28: PHGCredential minted: device=%s tx=%s",
                                            dev_id[:16], _cred_tx[:16],
                                        )
                        except Exception as _cred_exc:
                            log.debug("Phase 28: credential mint skipped (non-fatal): %s", _cred_exc)
            except Exception as exc:
                log.warning("PHG checkpoint failed for %s: %s", dev_id[:16], exc)

    async def _submit_bounty_evidence(self, records: list[PoACRecord]):
        """Submit bounty evidence for records that reference a bounty."""
        for record in records:
            if record.bounty_id == 0:
                continue
            try:
                tx_hash = await self._chain.submit_evidence(
                    record.bounty_id, record.device_id, record
                )
                log.info(
                    "Bounty evidence submitted: bounty=%d device=%s tx=%s",
                    record.bounty_id, record.device_id_hex[:16], tx_hash[:16],
                )
            except Exception as e:
                log.error(
                    "Bounty evidence failed (bounty=%d): %s",
                    record.bounty_id, e,
                )

    async def _retry_loop(self):
        """Periodically retry failed submissions with exponential backoff."""
        while self._running:
            try:
                await asyncio.sleep(self._cfg.retry_base_delay_s * 5)

                failed = self._store.get_failed_submissions(self._cfg.max_retries)
                if not failed:
                    continue

                for sub in failed:
                    retries = sub["retries"]
                    # Exponential backoff with jitter
                    delay = self._cfg.retry_base_delay_s * (2 ** retries)
                    delay += random.uniform(0, delay * 0.25)

                    age = time.time() - sub["created_at"]
                    if age < delay:
                        continue

                    record_hashes = json.loads(sub["record_hashes"])
                    log.info(
                        "Retrying submission %d (%d records, attempt %d/%d)",
                        sub["id"], len(record_hashes),
                        retries + 1, self._cfg.max_retries,
                    )

                    # Fetch raw records from store and re-parse
                    # For simplicity, re-mark as pending and let the main loop pick them up
                    self._store.batch_update_status(record_hashes, STATUS_PENDING)
                    self._store.update_submission(
                        sub["id"],
                        status=STATUS_PENDING,
                        retries=retries + 1,
                    )

                # Dead-letter records that exceeded max retries
                exhausted = self._store.get_failed_submissions(0)
                for sub in exhausted:
                    if sub["retries"] >= self._cfg.max_retries:
                        record_hashes = json.loads(sub["record_hashes"])
                        self._store.batch_update_status(
                            record_hashes, STATUS_DEAD_LETTER
                        )
                        self._store.update_submission(
                            sub["id"], status=STATUS_DEAD_LETTER
                        )
                        log.warning(
                            "Dead-lettered submission %d after %d retries",
                            sub["id"], sub["retries"],
                        )

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Retry loop error: %s", e)
                await asyncio.sleep(10)
