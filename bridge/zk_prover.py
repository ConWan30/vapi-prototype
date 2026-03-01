"""
Phase 14C — ZKProver: Groth16 proof generation and verification for TeamProof.

This module wraps snarkjs (Node.js subprocess) to produce and verify real Groth16
BN254 proofs for the TeamProof circuit (contracts/circuits/TeamProof.circom).

When ZK artifacts are available (WASM + zkey + circomlibjs installed):
  - generate_proof()  calls Node.js → compute_inputs.js → snarkjs fullprove
  - verify_proof()    calls snarkjs groth16 verify
  - returns 256-byte ABI-packed proof (A:64B + B:128B + C:64B)

When artifacts are unavailable (typical before setup.sh is run):
  - Falls back to SwarmZKAggregator mock proof (same 256-byte size)
  - ZK_ARTIFACTS_AVAILABLE == False (exported for callers to check)

ZK artifact paths — set via environment variables or Config:
  VAPI_ZK_WASM_PATH  — bridge/zk_artifacts/TeamProof.wasm
  VAPI_ZK_ZKEY_PATH  — bridge/zk_artifacts/TeamProof_final.zkey
  VAPI_ZK_VKEY_PATH  — bridge/zk_artifacts/verification_key.json

Setup (one-time):
  cd contracts/circuits && npm install && bash setup.sh
  cp TeamProof_js/TeamProof.wasm bridge/zk_artifacts/
  cp TeamProof_final.zkey        bridge/zk_artifacts/   (KEEP SECRET)
  cp verification_key.json       bridge/zk_artifacts/
  cd bridge/zk_artifacts && npm install

Proof wire format (256 bytes, ABI-compatible with Solidity abi.decode):
  [0:32]    pi_a.x          G1 point X
  [32:64]   pi_a.y          G1 point Y
  [64:96]   pi_b[0][0]      G2 point X (Fp2 coefficient 0)
  [96:128]  pi_b[0][1]      G2 point X (Fp2 coefficient 1)
  [128:160] pi_b[1][0]      G2 point Y (Fp2 coefficient 0)
  [160:192] pi_b[1][1]      G2 point Y (Fp2 coefficient 1)
  [192:224] pi_c.x          G1 point X
  [224:256] pi_c.y          G1 point Y

Solidity decode: abi.decode(proof, (uint256[2], uint256[2][2], uint256[2]))
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_MEMBERS  = 6
PROOF_SIZE   = 256        # Groth16 BN254 uncompressed: A(64) + B(128) + C(64)

# Default artifact directory (bridge/zk_artifacts/)
_THIS_DIR        = Path(__file__).parent
ZK_ARTIFACTS_DIR = _THIS_DIR / "zk_artifacts"

# Env-override paths (set by setup.sh instructions or Config)
_ZK_WASM = os.getenv("VAPI_ZK_WASM_PATH", str(ZK_ARTIFACTS_DIR / "TeamProof.wasm"))
_ZK_ZKEY = os.getenv("VAPI_ZK_ZKEY_PATH", str(ZK_ARTIFACTS_DIR / "TeamProof_final.zkey"))
_ZK_VKEY = os.getenv("VAPI_ZK_VKEY_PATH", str(ZK_ARTIFACTS_DIR / "verification_key.json"))

# Node.js helper script (bundled with the bridge)
_COMPUTE_INPUTS_JS = ZK_ARTIFACTS_DIR / "compute_inputs.js"


def _artifacts_available(wasm: str, zkey: str) -> bool:
    """Return True when all required ZK artifacts are present and non-empty."""
    js_ok = (
        _COMPUTE_INPUTS_JS.is_file() and
        (ZK_ARTIFACTS_DIR / "node_modules" / "circomlibjs").is_dir()
    )
    for p in (wasm, zkey):
        if not p or not Path(p).is_file() or Path(p).stat().st_size == 0:
            return False
    return js_ok


# Module-level flag — import this to branch between real/mock paths
ZK_ARTIFACTS_AVAILABLE: bool = _artifacts_available(_ZK_WASM, _ZK_ZKEY)


# ---------------------------------------------------------------------------
# ZKProver
# ---------------------------------------------------------------------------

class ZKProver:
    """
    Groth16 ZK proof generation and verification for the TeamProof circuit.

    Usage:
        prover = ZKProver()           # uses env-var paths
        proof, root, nullifier = prover.generate_proof(
            inference_results=[0x21, 0x22, 0x23, 0, 0, 0],
            identity_secrets=[1111, 2222, 3333, 0, 0, 0],
            active_flags=[1, 1, 1, 0, 0, 0],
            member_count=3,
            epoch=42,
        )
        ok = prover.verify_proof(proof, root, nullifier, member_count=3, epoch=42)
    """

    def __init__(
        self,
        wasm_path: str = _ZK_WASM,
        zkey_path: str = _ZK_ZKEY,
        vkey_path: str = _ZK_VKEY,
    ) -> None:
        self._wasm = wasm_path
        self._zkey = zkey_path
        self._vkey = vkey_path
        self._available = _artifacts_available(wasm_path, zkey_path)
        if not self._available:
            log.warning(
                "ZK artifacts unavailable — falling back to mock proofs. "
                "Run: cd contracts/circuits && npm install && bash setup.sh, "
                "then copy artifacts to bridge/zk_artifacts/ and run npm install there."
            )

    # ── Public API ──────────────────────────────────────────────────────────

    def generate_proof(
        self,
        inference_results: list,
        identity_secrets: list,
        active_flags: list,
        member_count: int,
        epoch: int,
        team_id: bytes = b"\x00" * 32,
    ) -> Tuple[bytes, int, int]:
        """
        Generate a 256-byte Groth16 proof for the TeamProof circuit.

        Args:
            inference_results: 8-bit VAPI inference codes, len 6 (pad inactive slots with 0).
            identity_secrets:  Poseidon(deviceId) per member as integers, len 6.
            active_flags:      1=active slot, 0=padding; sum must equal member_count.
            member_count:      Active member count in [2, MAX_MEMBERS].
            epoch:             block.number / EPOCH_BLOCKS (anti-replay domain tag).
            team_id:           32-byte team ID (mock fallback only).

        Returns:
            (proof_bytes: bytes[256], poseidon_merkle_root: int, nullifier_hash: int)

        Raises:
            RuntimeError: if real proof generation fails (artifacts path but Node/snarkjs
                          unavailable — propagated so caller can handle gracefully).
        """
        if self._available:
            return self._real_proof(
                inference_results, identity_secrets, active_flags, member_count, epoch
            )
        return self._mock_proof(
            inference_results, identity_secrets, active_flags, member_count, epoch, team_id
        )

    def verify_proof(
        self,
        proof_bytes: bytes,
        poseidon_merkle_root: int,
        nullifier_hash: int,
        member_count: int,
        epoch: int,
    ) -> bool:
        """
        Verify a 256-byte proof.

        When artifacts + vkey available: full Groth16 cryptographic verification via snarkjs.
        Otherwise: structural mock check (length + member_count range).
        """
        if len(proof_bytes) != PROOF_SIZE:
            return False
        if self._available and self._vkey and Path(self._vkey).is_file():
            return self._verify_real(proof_bytes, poseidon_merkle_root, nullifier_hash,
                                     member_count, epoch)
        return _verify_mock_structure(proof_bytes)

    # ── Real proof path ──────────────────────────────────────────────────────

    def _real_proof(
        self,
        inference_results: list,
        identity_secrets: list,
        active_flags: list,
        member_count: int,
        epoch: int,
    ) -> Tuple[bytes, int, int]:
        with tempfile.TemporaryDirectory() as _tmp:
            tmpdir = Path(_tmp)

            # Step 1: compute Poseidon-based circuit inputs (Node.js helper)
            private_in = {
                "inferenceResults": [int(v) for v in inference_results],
                "identitySecrets":  [str(int(s)) for s in identity_secrets],
                "activeFlags":      [int(f) for f in active_flags],
                "memberCount":      int(member_count),
                "epoch":            int(epoch),
            }
            priv_path = tmpdir / "private.json"
            priv_path.write_text(json.dumps(private_in))

            circuit_in_path = tmpdir / "input.json"
            _run_node(
                str(_COMPUTE_INPUTS_JS),
                [str(priv_path)],
                capture_to=circuit_in_path,
                cwd=str(ZK_ARTIFACTS_DIR),  # resolves circomlibjs from local node_modules
            )

            circuit_inputs = json.loads(circuit_in_path.read_text())
            poseidon_root = int(circuit_inputs["poseidonMerkleRoot"])
            nullifier     = int(circuit_inputs["nullifierHash"])

            # Step 2: snarkjs groth16 fullprove
            proof_path  = tmpdir / "proof.json"
            public_path = tmpdir / "public.json"
            _run_snarkjs([
                "groth16", "fullprove",
                str(circuit_in_path),
                self._wasm,
                self._zkey,
                str(proof_path),
                str(public_path),
            ])

            proof_json  = json.loads(proof_path.read_text())
            proof_bytes = _encode_proof(proof_json)

            log.info(
                "Real ZK proof generated — root=%s nullifier=%s size=%d",
                hex(poseidon_root), hex(nullifier), len(proof_bytes),
            )
            return proof_bytes, poseidon_root, nullifier

    def _verify_real(
        self,
        proof_bytes: bytes,
        poseidon_merkle_root: int,
        nullifier_hash: int,
        member_count: int,
        epoch: int,
    ) -> bool:
        with tempfile.TemporaryDirectory() as _tmp:
            tmpdir = Path(_tmp)
            proof_json = _decode_proof(proof_bytes)
            (tmpdir / "proof.json").write_text(json.dumps(proof_json))

            # Public input order must match the circuit's public signal declaration order:
            # [poseidonMerkleRoot, nullifierHash, memberCount, epoch]
            public = [
                str(poseidon_merkle_root),
                str(nullifier_hash),
                str(member_count),
                str(epoch),
            ]
            (tmpdir / "public.json").write_text(json.dumps(public))

            result = _run_snarkjs([
                "groth16", "verify",
                self._vkey,
                str(tmpdir / "public.json"),
                str(tmpdir / "proof.json"),
            ], check=False)
            return result.returncode == 0

    # ── Mock fallback ─────────────────────────────────────────────────────────

    def _mock_proof(
        self,
        inference_results: list,
        identity_secrets: list,
        active_flags: list,
        member_count: int,
        epoch: int,
        team_id: bytes,
    ) -> Tuple[bytes, int, int]:
        from swarm_zk_aggregator import SwarmZKAggregator

        # Mock Poseidon root / nullifier via keccak256 proxies (distinguishable from real)
        mock_root = int.from_bytes(
            SwarmZKAggregator._keccak256(
                b"".join(int(r).to_bytes(1, "big") for r in inference_results)
            ), "big"
        )
        mock_nullifier = int.from_bytes(
            SwarmZKAggregator._keccak256(
                team_id + struct.pack(">I", epoch & 0xFFFFFFFF)
            ), "big"
        )

        agg = SwarmZKAggregator()
        dummy_hashes = [
            SwarmZKAggregator._keccak256(team_id + struct.pack(">I", i))
            for i in range(max(member_count, 2))
        ]
        proof = agg.generate_mock_proof(
            team_id=team_id,
            record_hashes=dummy_hashes,
            member_device_ids=[b"\x00" * 32] * max(member_count, 2),
            epoch=epoch,
        )
        return proof, mock_root, mock_nullifier


# ---------------------------------------------------------------------------
# Proof encoding / decoding  (256-byte ABI-packed wire format)
# ---------------------------------------------------------------------------

def _encode_proof(proof_json: dict) -> bytes:
    """
    Encode snarkjs proof.json → 256-byte ABI-packed wire format.

    Layout matches abi.decode(proof, (uint256[2], uint256[2][2], uint256[2])):
      [0:64]    pi_a  (a[0], a[1])
      [64:192]  pi_b  (b[0][0], b[0][1], b[1][0], b[1][1])
      [192:256] pi_c  (c[0], c[1])
    """
    def to_bytes32(v: str) -> bytes:
        n = int(v, 16) if str(v).startswith(("0x", "0X")) else int(v)
        return n.to_bytes(32, "big")

    buf = bytearray(PROOF_SIZE)
    buf[0:32]    = to_bytes32(proof_json["pi_a"][0])
    buf[32:64]   = to_bytes32(proof_json["pi_a"][1])
    buf[64:96]   = to_bytes32(proof_json["pi_b"][0][0])
    buf[96:128]  = to_bytes32(proof_json["pi_b"][0][1])
    buf[128:160] = to_bytes32(proof_json["pi_b"][1][0])
    buf[160:192] = to_bytes32(proof_json["pi_b"][1][1])
    buf[192:224] = to_bytes32(proof_json["pi_c"][0])
    buf[224:256] = to_bytes32(proof_json["pi_c"][1])
    return bytes(buf)


def _decode_proof(proof_bytes: bytes) -> dict:
    """Decode 256-byte wire format → snarkjs proof.json structure."""
    def to_hex(b: bytes) -> str:
        return "0x" + b.hex()

    return {
        "pi_a": [to_hex(proof_bytes[0:32]),   to_hex(proof_bytes[32:64]),  "1"],
        "pi_b": [
            [to_hex(proof_bytes[64:96]),   to_hex(proof_bytes[96:128])],
            [to_hex(proof_bytes[128:160]), to_hex(proof_bytes[160:192])],
            ["1", "0"],
        ],
        "pi_c": [to_hex(proof_bytes[192:224]), to_hex(proof_bytes[224:256]), "1"],
        "protocol": "groth16",
        "curve":    "bn128",
    }


def _verify_mock_structure(proof_bytes: bytes) -> bool:
    """Structural check for mock proofs (non-cryptographic)."""
    if len(proof_bytes) != PROOF_SIZE:
        return False
    member_count = struct.unpack(">H", proof_bytes[64:66])[0]
    return 2 <= member_count <= MAX_MEMBERS


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run_node(
    script: str,
    args: list,
    capture_to: Optional[Path] = None,
    cwd: Optional[str] = None,
) -> None:
    """Run a Node.js script, optionally capturing stdout to a file."""
    cmd = ["node", script] + list(args)
    if capture_to:
        with capture_to.open("w") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, cwd=cwd, check=False)
    else:
        r = subprocess.run(cmd, capture_output=True, cwd=cwd, check=False)
    if r.returncode != 0:
        stderr = r.stderr.decode(errors="replace") if r.stderr else ""
        raise RuntimeError(f"Node.js helper failed: {stderr[:600]}")


def _run_snarkjs(args: list, check: bool = True):
    """Run snarkjs via npx (uses local node_modules if available)."""
    import sys as _sys
    cmd = ["npx", "--yes", "snarkjs"] + args
    # On Windows, npx is a batch script and requires shell=True to locate/execute
    _shell = _sys.platform == "win32"
    r = subprocess.run(cmd, capture_output=True, check=False, shell=_shell)
    if check and r.returncode != 0:
        stderr = r.stderr.decode(errors="replace") if r.stderr else ""
        raise RuntimeError(f"snarkjs failed: {stderr[:600]}")
    return r
