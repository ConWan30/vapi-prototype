"""
PHGRegistryClient — Async wrapper for the PHGRegistry on-chain contract.

Thin convenience layer over ChainClient for PHG-specific operations.
Used by batcher.py to commit checkpoints and by tests to assert on-chain state.
"""

import logging

from .chain import ChainClient, PHG_REGISTRY_ABI

log = logging.getLogger(__name__)


class PHGRegistryClient:
    """Async wrapper for PHGRegistry contract interactions.

    Instantiate with a configured ChainClient and the registry address.
    All methods are no-ops (returning 0 / False / empty string) when
    the contract address is empty, matching the bridge's optional behaviour.
    """

    def __init__(self, chain: ChainClient, contract_address: str):
        self._chain = chain
        self._address = contract_address
        if contract_address:
            self._contract = chain._w3.eth.contract(
                address=chain._w3.to_checksum_address(contract_address),
                abi=PHG_REGISTRY_ABI,
            )
        else:
            self._contract = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def commit_checkpoint(
        self,
        device_id: bytes,
        score_delta: int,
        count: int,
        biometric_hash: bytes,
    ) -> str:
        """Commit a PHG checkpoint on-chain. Returns tx hash or '' if unconfigured."""
        if not self._contract:
            return ""
        bio_bytes32 = (biometric_hash[:32].ljust(32, b"\x00")
                       if biometric_hash else bytes(32))
        return await self._chain._send_tx(
            self._contract.functions.commitCheckpoint,
            device_id,
            score_delta,
            count,
            bio_bytes32,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_cumulative_score(self, device_id: bytes) -> int:
        """Return the on-chain cumulative PHG score. Returns 0 if unconfigured."""
        if not self._contract:
            return 0
        return await self._contract.functions.cumulativeScore(device_id).call()

    async def is_eligible(self, device_id: bytes, min_score: int) -> bool:
        """Return True if the device's PHG score >= min_score."""
        if not self._contract:
            return False
        return await self._contract.functions.isEligible(device_id, min_score).call()

    async def get_device_state(self, device_id: bytes) -> dict:
        """Return (score, count, head) for a device. Returns zeros if unconfigured."""
        if not self._contract:
            return {"score": 0, "count": 0, "head": bytes(32)}
        result = await self._contract.functions.getDeviceState(device_id).call()
        return {"score": result[0], "count": result[1], "head": result[2]}
