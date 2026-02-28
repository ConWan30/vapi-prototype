"""
Phase 25 — Chain Reconciler

Background coroutine that confirms PHG checkpoints via on-chain event polling.
Polls PHGCheckpointCommitted getLogs every poll_interval seconds.
Marks confirmed=True for matched SQLite checkpoints.
Re-queues confirmed=False checkpoints older than retry_age_s seconds.
"""

import asyncio
import logging

log = logging.getLogger(__name__)


class ChainReconciler:
    """
    Background coroutine: confirms PHG checkpoints via on-chain event polling.

    Polls PHGCheckpointCommitted getLogs every poll_interval seconds.
    Marks confirmed=True for matched SQLite checkpoints.
    Re-queues confirmed=False checkpoints older than retry_age_s (default 300s).

    Usage (from main.py):
        reconciler = ChainReconciler(store, chain, poll_interval=30.0)
        asyncio.create_task(reconciler.run())
    """

    def __init__(self, store, chain, poll_interval: float = 30.0, retry_age_s: float = 300.0):
        self._store = store
        self._chain = chain
        self._poll_interval = poll_interval
        self._retry_age_s = retry_age_s
        self._running = False
        self._last_block: int = 0

    async def run(self):
        """Run the reconciler loop until cancelled."""
        self._running = True
        log.info(
            "ChainReconciler started (poll_interval=%.0fs, retry_age=%.0fs)",
            self._poll_interval, self._retry_age_s,
        )
        # Initialize last_block
        try:
            self._last_block = await self._chain._w3.eth.block_number
        except Exception:
            self._last_block = 0

        while self._running:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._reconcile_cycle()
            except asyncio.CancelledError:
                log.info("ChainReconciler shutting down")
                self._running = False
                return
            except Exception as exc:
                log.warning("ChainReconciler cycle error: %s", exc)

    async def _reconcile_cycle(self):
        """Single reconciliation pass: fetch events and mark confirmed."""
        try:
            current_block = await self._chain._w3.eth.block_number
        except Exception as exc:
            log.warning("ChainReconciler: could not fetch block number: %s", exc)
            return

        if current_block <= self._last_block:
            return

        # Fetch events for new blocks
        try:
            events = await self._chain.get_phg_checkpoint_events(
                self._last_block + 1, current_block
            )
        except Exception as exc:
            log.warning("ChainReconciler: getLogs error (non-fatal): %s", exc)
            events = []

        for event in events:
            tx_hash = event["transactionHash"]
            if isinstance(tx_hash, bytes):
                tx_hash = tx_hash.hex()
            if not tx_hash.startswith("0x"):
                tx_hash = "0x" + tx_hash
            self._store.mark_checkpoint_confirmed(tx_hash)
            log.debug("ChainReconciler: confirmed checkpoint tx=%s", tx_hash[:16])

        self._last_block = current_block

        # Re-queue old unconfirmed checkpoints
        unconfirmed = self._store.get_unconfirmed_checkpoints(age_s=self._retry_age_s)
        for cp in unconfirmed:
            log.warning(
                "ChainReconciler: unconfirmed checkpoint id=%s device=%s tx=%s — scheduling retry",
                cp.get("id"), str(cp.get("device_id", ""))[:16], str(cp.get("tx_hash", ""))[:16],
            )
            asyncio.create_task(self._requeue_checkpoint(cp))

    async def _requeue_checkpoint(self, checkpoint: dict):
        """Attempt to re-confirm a stale unconfirmed checkpoint by tx hash lookup."""
        tx_hash = checkpoint.get("tx_hash", "")
        if not tx_hash:
            return
        try:
            receipt = await self._chain.wait_for_receipt(tx_hash, timeout=10)
            if receipt and receipt.get("status") == 1:
                self._store.mark_checkpoint_confirmed(tx_hash)
                log.info("ChainReconciler: requeued checkpoint confirmed: tx=%s", tx_hash[:16])
        except Exception as exc:
            log.debug("ChainReconciler: requeue failed for tx=%s: %s", tx_hash[:16], exc)
