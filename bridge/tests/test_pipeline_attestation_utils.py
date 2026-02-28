"""
Phase 16A — Self-Verifying Pipeline Attestation Utilities
===========================================================

Shared helpers for the E2E test suite. Provides:
  - VAPITestVector: typed result of make_test_vector()
  - make_test_vector(): create a real ECDSA-P256 signed PoAC record
  - compute_merkle_root(): Python replica of TeamProofAggregator._computeMerkleRoot
  - ContractHarness: async deployer/wrapper for all VAPI contracts on Hardhat

Design contract:
  - No web3 imports at module level — ContractHarness.create() imports AsyncWeb3 lazily
  - PoACEngine and eth_hash are imported inside functions (avoid failures if not installed)
  - record_hash = SHA-256(raw_body) [164B] — matches on-chain submittedHashes key
  - device_id = keccak256(pubkey) [65B SEC1] — matches on-chain computeDeviceId()
  - Uses PoACVerifierTestable (no P256 precompile required on Hardhat)
"""

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# ─── Path setup ─────────────────────────────────────────────────────────────

_THIS = Path(__file__).resolve()
# bridge/tests/ -> bridge/ -> vapi-pebble-prototype/
REPO_ROOT = _THIS.parents[2]
CONTROLLER_DIR = REPO_ROOT / "controller"
ARTIFACTS_BASE = REPO_ROOT / "contracts" / "artifacts" / "contracts"

# Add controller/ to sys.path so dualshock_emulator can be imported
sys.path.insert(0, str(CONTROLLER_DIR))


# ─── Data ────────────────────────────────────────────────────────────────────

@dataclass
class VAPITestVector:
    """
    A fully-formed, cryptographically signed PoAC record ready for on-chain submission.

    Fields
    ------
    pubkey         65B SEC1 uncompressed ECDSA-P256 public key
    device_id      keccak256(pubkey) — 32B, matches on-chain computeDeviceId()
    raw_body       164B PoAC body — passed to verifyPoAC / verifyPoACWithSchema
    signature      64B raw r||s — passed as _signature to verifier
    inference      uint8 inference result embedded in raw_body[128]
    schema_version Schema version to tag this record with (1=env/v1, 2=kinematic/v2)
    record_hash    SHA-256(raw_body) — 32B, matches on-chain submittedHashes key
    """
    pubkey: bytes
    device_id: bytes
    raw_body: bytes
    signature: bytes
    inference: int
    schema_version: int
    record_hash: bytes


# ─── Factory ─────────────────────────────────────────────────────────────────

def make_test_vector(inference: int = 0x20, schema_version: int = 2) -> VAPITestVector:
    """
    Create a VAPITestVector backed by a real ECDSA-P256 signed PoAC record.

    Each call creates a fresh ephemeral keypair via PoACEngine — no two vectors
    share a device_id. The PoACVerifierTestable on Hardhat bypasses signature
    verification, so the real sig is cosmetic but authentic.

    Parameters
    ----------
    inference      uint8 inference byte to embed at raw_body[128]
    schema_version Schema version to associate with this vector

    Returns
    -------
    VAPITestVector ready for register_device() + submit_record()
    """
    from dualshock_emulator import PoACEngine  # type: ignore
    from eth_hash.auto import keccak

    engine = PoACEngine()
    # Use a simple sensor commitment for tests (not bio-enriched)
    sensor_commitment = hashlib.sha256(b"\x00" * 48).digest()
    wm_hash = b"\x00" * 32

    record = engine.generate(sensor_commitment, wm_hash, inference, 0x01, 200, 75, 0)
    raw_body = record.serialize_body()          # 164B body
    record_hash = hashlib.sha256(raw_body).digest()  # SHA-256(body) — on-chain key
    device_id = bytes(keccak(engine.public_key_bytes))  # keccak256(65B pubkey)

    return VAPITestVector(
        pubkey=engine.public_key_bytes,
        device_id=device_id,
        raw_body=raw_body,
        signature=record.signature,
        inference=inference,
        schema_version=schema_version,
        record_hash=record_hash,
    )


def make_chained_vectors(
    inferences: List[int],
    schema_version: int = 2,
) -> tuple:
    """
    Create multiple VAPITestVectors from a SINGLE PoACEngine (same device, chained).

    The second record's prev_poac_hash = SHA-256(first record's full 228B bytes),
    and monotonic_ctr increments. This replicates real PoAC chain genealogy.

    Returns
    -------
    (vectors: List[VAPITestVector], engine: PoACEngine)
    """
    from dualshock_emulator import PoACEngine  # type: ignore
    from eth_hash.auto import keccak

    engine = PoACEngine()
    device_id = bytes(keccak(engine.public_key_bytes))
    vectors = []

    for i, inference in enumerate(inferences):
        # Vary sensor commitment per record so hashes differ
        sensor_commitment = hashlib.sha256(bytes([i + 1]) * 48).digest()
        wm_hash = b"\x00" * 32
        record = engine.generate(sensor_commitment, wm_hash, inference, 0x01, 200, 75, 0)
        raw_body = record.serialize_body()
        record_hash = hashlib.sha256(raw_body).digest()

        vectors.append(VAPITestVector(
            pubkey=engine.public_key_bytes,
            device_id=device_id,
            raw_body=raw_body,
            signature=record.signature,
            inference=inference,
            schema_version=schema_version,
            record_hash=record_hash,
        ))

    return vectors, engine


# ─── Merkle root (mirrors Solidity _computeMerkleRoot exactly) ───────────────

def compute_merkle_root(record_hashes: List[bytes]) -> bytes:
    """
    Python replica of TeamProofAggregator._computeMerkleRoot.

    Algorithm (matches Solidity byte-for-byte):
      1. Sort leaves lexicographically (ascending bytes comparison)
      2. Iteratively reduce pairs: keccak256(left_bytes + right_bytes)
      3. Promote odd leaf unchanged (no duplication — matches contract)
      4. Return root when n == 1

    Used by test_python_solidity_merkle_cross_validation to cross-validate
    the Python implementation against the Solidity implementation on-chain.
    """
    from eth_hash.auto import keccak

    leaves = sorted(record_hashes)
    n = len(leaves)
    if n == 1:
        return leaves[0]

    while n > 1:
        new_n = (n + 1) // 2
        next_leaves: List[bytes] = []
        for i in range(new_n):
            left = i * 2
            right = left + 1
            if right < n:
                next_leaves.append(keccak(leaves[left] + leaves[right]))
            else:
                next_leaves.append(leaves[left])  # Promote odd leaf unchanged
        leaves = next_leaves
        n = new_n

    return leaves[0]


# ─── Contract harness ────────────────────────────────────────────────────────

class ContractHarness:
    """
    Async harness for deploying and interacting with all VAPI contracts on Hardhat.

    Deployment order (zero-deposit mode, no ETH required for registration):
      1. TieredDeviceRegistry(0, 0, 0)        — all deposits = 0
      2. PoACVerifierTestable(registry, 10yr) — bypasses P256 sig check
      3. registry.setReputationUpdater(verifier, True)
      4. BountyMarket(verifier, registry, 0)  — 0% platform fee
      5. ProgressAttestation(verifier)
      6. TeamProofAggregator(verifier)

    Usage
    -----
    harness = await ContractHarness.create("http://127.0.0.1:8545")
    device_id = await harness.register_device(pubkey, tier=1)
    await harness.submit_record(vector, schema_version=2)
    inference = await harness.read_inference(record_hash)
    """

    def __init__(self):
        self.w3 = None
        self.deployer: Optional[str] = None
        self.registry = None
        self.verifier = None
        self.bounty_market = None
        self.progress_attestation = None
        self.team_aggregator = None

    @classmethod
    async def create(cls, rpc_url: str) -> "ContractHarness":
        """Connect to Hardhat node and deploy all six VAPI contracts."""
        from web3 import AsyncWeb3  # type: ignore

        h = cls()
        h.w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
        accounts = await h.w3.eth.accounts
        h.deployer = accounts[0]

        # 1. TieredDeviceRegistry — zero-deposit mode so tests need no ETH
        h.registry = await h._deploy(
            ARTIFACTS_BASE / "TieredDeviceRegistry.sol" / "TieredDeviceRegistry.json",
            0, 0, 0,
        )

        # 2. PoACVerifierTestable — overrides _requireValidSignature() as no-op
        #    maxTimestampSkew = 315360000 s (10 years) — accepts any system-time record
        h.verifier = await h._deploy(
            ARTIFACTS_BASE / "test" / "PoACVerifierTestable.sol" / "PoACVerifierTestable.json",
            h.registry.address,
            315_360_000,
        )

        # 3. Allow verifier to update device reputation (required for BountyMarket flow)
        tx = await h.registry.functions.setReputationUpdater(
            h.verifier.address, True
        ).transact({"from": h.deployer})
        await h.w3.eth.wait_for_transaction_receipt(tx)

        # 4. BountyMarket — 0% platform fee
        h.bounty_market = await h._deploy(
            ARTIFACTS_BASE / "BountyMarket.sol" / "BountyMarket.json",
            h.verifier.address, h.registry.address, 0,
        )

        # 5. ProgressAttestation
        h.progress_attestation = await h._deploy(
            ARTIFACTS_BASE / "ProgressAttestation.sol" / "ProgressAttestation.json",
            h.verifier.address,
        )

        # 6. TeamProofAggregator
        h.team_aggregator = await h._deploy(
            ARTIFACTS_BASE / "TeamProofAggregator.sol" / "TeamProofAggregator.json",
            h.verifier.address,
        )

        return h

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _deploy(self, artifact_path: Path, *ctor_args, value: int = 0):
        """Deploy a contract from its Hardhat artifact JSON. Returns contract instance."""
        artifact = json.loads(artifact_path.read_text())
        factory = self.w3.eth.contract(
            abi=artifact["abi"], bytecode=artifact["bytecode"]
        )
        tx_hash = await factory.constructor(*ctor_args).transact(
            {"from": self.deployer, "value": value}
        )
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return self.w3.eth.contract(
            address=receipt["contractAddress"], abi=artifact["abi"]
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def register_device(self, pubkey: bytes, tier: int) -> bytes:
        """
        Register a device via registerTieredDevice(pubkey, tier).
        Returns device_id (bytes32) from on-chain computeDeviceId().
        tier: 0=Emulated, 1=Standard, 2=Attested (all free in zero-deposit mode)
        """
        tx = await self.registry.functions.registerTieredDevice(
            pubkey, tier
        ).transact({"from": self.deployer})
        await self.w3.eth.wait_for_transaction_receipt(tx)
        result = await self.registry.functions.computeDeviceId(pubkey).call()
        return bytes(result)

    async def submit_record(
        self, vector: VAPITestVector, schema_version: int = 2
    ) -> None:
        """
        Submit a PoAC record body + signature to the verifier.
        Uses verifyPoACWithSchema when schema_version > 0, else verifyPoAC.
        """
        device_id_hex = "0x" + vector.device_id.hex()
        if schema_version > 0:
            tx = await self.verifier.functions.verifyPoACWithSchema(
                device_id_hex, vector.raw_body, vector.signature, schema_version
            ).transact({"from": self.deployer})
        else:
            tx = await self.verifier.functions.verifyPoAC(
                device_id_hex, vector.raw_body, vector.signature
            ).transact({"from": self.deployer})
        await self.w3.eth.wait_for_transaction_receipt(tx)

    async def read_inference(self, record_hash: bytes) -> int:
        """
        Read the stored inference byte from on-chain recordInferences[recordHash].
        This is the core 'self-verifying' mechanism: the chain stores what PITL computed.
        """
        return await self.verifier.functions.recordInferences(
            "0x" + record_hash.hex()
        ).call()

    async def post_test_bounty(self) -> int:
        """
        Post a permissive bounty covering all valid coordinates.
        reward = 0.01 ETH, minSamples=1, sampleIntervalS=1, durationS=1 day.
        Returns bounty_id from BountyPosted event.
        """
        tx = await self.bounty_market.functions.postBounty(
            0,           # sensorRequirements bitmask (0 = accept any)
            1,           # minSamples
            1,           # sampleIntervalS
            86400,       # durationS (1 day)
            -9000000,    # zoneLatMin  (> -90 degrees in any COORD_SCALE)
            9000000,     # zoneLatMax
            -18000000,   # zoneLonMin
            18000000,    # zoneLonMax
            0, 0, 0,     # vocThreshold, tempThresholdHi, tempThresholdLo
        ).transact({"from": self.deployer, "value": 10**16})   # 0.01 ETH reward
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx)
        logs = self.bounty_market.events.BountyPosted().process_receipt(receipt)
        return logs[0]["args"]["bountyId"]

    async def create_team(
        self, team_id: bytes, device_ids: List[bytes]
    ) -> None:
        """Create a team with the given bytes32 team_id and member device_ids."""
        tx = await self.team_aggregator.functions.createTeam(
            team_id, device_ids
        ).transact({"from": self.deployer})
        await self.w3.eth.wait_for_transaction_receipt(tx)

    async def submit_team_proof(
        self,
        team_id: bytes,
        record_hashes: List[bytes],
        merkle_root: bytes,
    ) -> None:
        """Submit a team proof. Reverts CheatFlagDetected / InvalidMerkleRoot on failure."""
        tx = await self.team_aggregator.functions.submitTeamProof(
            team_id, record_hashes, merkle_root
        ).transact({"from": self.deployer})
        await self.w3.eth.wait_for_transaction_receipt(tx)
