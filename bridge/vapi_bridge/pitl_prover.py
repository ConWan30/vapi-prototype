"""
Phase 26 — PITLProver: Groth16 ZK proof for individual PITL biometric sessions.

Proves bridge honestly computed PITL outputs without revealing raw sensor data.
Mirrors ZKProver class structure exactly (mock/real dual-mode).

Artifact env vars:
  VAPI_PITL_WASM_PATH  — bridge/zk_artifacts/PitlSessionProof.wasm
  VAPI_PITL_ZKEY_PATH  — bridge/zk_artifacts/PitlSessionProof_final.zkey
  VAPI_PITL_VKEY_PATH  — bridge/zk_artifacts/PitlSession_verification_key.json

Setup (one-time, after circom trusted setup):
  cd contracts/circuits && npm install && bash setup-pitl.sh
  cp PitlSessionProof_js/PitlSessionProof.wasm bridge/zk_artifacts/
  cp PitlSessionProof_final.zkey               bridge/zk_artifacts/
  cp PitlSession_verification_key.json         bridge/zk_artifacts/

Proof wire format (256 bytes, same as ZKProver):
  [0:64]    pi_a  (a[0], a[1]) — G1 point
  [64:192]  pi_b  (b[0][0..3]) — G2 point
  [192:256] pi_c  (c[0], c[1]) — G1 point

Mock proof wire format (PITL-specific encoding):
  [0:32]    feature_commitment_int  (32B big-endian)
  [32:64]   nullifier_hash_int      (32B big-endian)
  [64:66]   humanity_prob_int       (uint16 big-endian)
  [66:256]  zeros

Circuit public inputs (5, must match PitlSessionProof.circom declaration order):
  [0] featureCommitment  — Poseidon(scaledFeatures[0..6])
  [1] humanityProbInt    — l5_humanity × 1000 ∈ [0, 1000]
  [2] inferenceResult    — 8-bit inference code
  [3] nullifierHash      — Poseidon(deviceIdHash, epoch)
  [4] epoch
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FEATURE_KEYS = [
    "trigger_resistance_change_rate",
    "trigger_onset_velocity_l2",
    "trigger_onset_velocity_r2",
    "micro_tremor_accel_variance",
    "grip_asymmetry",
    "stick_autocorr_lag1",
    "stick_autocorr_lag5",
]

FEATURE_SCALE  = 1000   # float × 1000 → non-negative integer for circuit
HUMANITY_SCALE = 1000   # [0,1] × 1000 → [0,1000]
DRIFT_SCALE    = 100    # cognitive_drift × 100 → non-negative integer
PROOF_SIZE     = 256    # Groth16 BN254 uncompressed (same as ZKProver)

_THIS_DIR            = Path(__file__).parent.parent   # bridge/
ZK_ARTIFACTS_DIR     = _THIS_DIR / "zk_artifacts"

_PITL_WASM = os.getenv("VAPI_PITL_WASM_PATH",
                        str(ZK_ARTIFACTS_DIR / "PitlSessionProof.wasm"))
_PITL_ZKEY = os.getenv("VAPI_PITL_ZKEY_PATH",
                        str(ZK_ARTIFACTS_DIR / "PitlSessionProof_final.zkey"))
_PITL_VKEY = os.getenv("VAPI_PITL_VKEY_PATH",
                        str(ZK_ARTIFACTS_DIR / "PitlSession_verification_key.json"))

_COMPUTE_INPUTS_PITL_JS = ZK_ARTIFACTS_DIR / "compute_inputs_pitl.js"


def _artifacts_available(wasm: str, zkey: str) -> bool:
    """True when all required PITL ZK artifacts exist and are non-empty."""
    js_ok = (
        _COMPUTE_INPUTS_PITL_JS.is_file() and
        (ZK_ARTIFACTS_DIR / "node_modules" / "circomlibjs").is_dir()
    )
    for p in (wasm, zkey):
        if not p or not Path(p).is_file() or Path(p).stat().st_size == 0:
            return False
    return js_ok


# Module-level flag — importable by callers to branch between real/mock paths
PITL_ZK_ARTIFACTS_AVAILABLE: bool = _artifacts_available(_PITL_WASM, _PITL_ZKEY)


# ---------------------------------------------------------------------------
# PITLProver
# ---------------------------------------------------------------------------

class PITLProver:
    """
    Groth16 ZK proof generation and verification for the PitlSessionProof circuit.

    Usage:
        prover = PITLProver()
        proof, fc, hp, null = prover.generate_proof(
            features_dict={...},
            device_id="aa" * 32,
            l5_humanity=0.8,
            e4_drift=0.2,
            inference_result=0x20,
            epoch=100,
        )
        ok = prover.verify_proof(proof, fc, hp, 0x20, null, 100)
    """

    # Class-level alias so callers can do prover.FEATURE_KEYS
    FEATURE_KEYS = FEATURE_KEYS

    def __init__(
        self,
        wasm_path: str = _PITL_WASM,
        zkey_path: str = _PITL_ZKEY,
        vkey_path: str = _PITL_VKEY,
    ) -> None:
        self._wasm = wasm_path
        self._zkey = zkey_path
        self._vkey = vkey_path
        self._available = _artifacts_available(wasm_path, zkey_path)
        if not self._available:
            log.info(
                "PITL ZK artifacts unavailable — using mock proofs. "
                "Run setup-pitl.sh and copy artifacts to bridge/zk_artifacts/ "
                "after trusted setup."
            )

    # ── Public API ──────────────────────────────────────────────────────────

    def generate_proof(
        self,
        features_dict: dict,
        device_id: str,
        l5_humanity: float,
        e4_drift: float,
        inference_result: int,
        epoch: int,
    ) -> Tuple[bytes, int, int, int]:
        """Generate a 256-byte PITL session proof.

        Args:
            features_dict:    {FEATURE_KEY: float} — L4 biometric features
            device_id:        64-char hex device identifier
            l5_humanity:      L5 rhythm_humanity_score ∈ [0, 1]
            e4_drift:         E4 cognitive_drift ≥ 0
            inference_result: 8-bit VAPI inference code
            epoch:            block.number / EPOCH_BLOCKS (anti-replay)

        Returns:
            (proof_bytes[256], feature_commitment_int, humanity_prob_int, nullifier_hash_int)
        """
        scaled = self._scale_features(features_dict)
        humanity_prob_int = max(0, min(1000, round(l5_humanity * HUMANITY_SCALE)))
        e4_drift_int = max(0, round(e4_drift * DRIFT_SCALE))

        if self._available:
            return self._real_proof(
                scaled, device_id, humanity_prob_int, e4_drift_int,
                inference_result, epoch
            )
        return self._mock_proof(
            scaled, device_id, humanity_prob_int, e4_drift_int,
            inference_result, epoch
        )

    def verify_proof(
        self,
        proof_bytes: bytes,
        feature_commitment: int,
        humanity_prob_int: int,
        inference_result: int,
        nullifier_hash: int,
        epoch: int,
    ) -> bool:
        """Verify a 256-byte PITL proof.

        Real mode: Groth16 cryptographic verification via snarkjs.
        Mock mode: structural check against encoded values.
        """
        if len(proof_bytes) != PROOF_SIZE:
            return False
        if self._available and self._vkey and Path(self._vkey).is_file():
            return self._verify_real(
                proof_bytes, feature_commitment, humanity_prob_int,
                inference_result, nullifier_hash, epoch
            )
        return self._verify_mock(
            proof_bytes, feature_commitment, humanity_prob_int, nullifier_hash
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _scale_features(self, features_dict: dict) -> list:
        """Scale L4 feature floats → non-negative integers for circuit (N7: max(0, ...))."""
        return [
            max(0, round(features_dict.get(k, 0.0) * FEATURE_SCALE))
            for k in FEATURE_KEYS
        ]

    @staticmethod
    def _device_id_to_field_element(device_id: str) -> int:
        """Convert 64-char hex device ID to a BN254 field element (N5).

        Uses SHA-256(device_id bytes) >> 4 to guarantee the value stays
        within the ~254-bit BN254 scalar field without modular arithmetic.
        """
        raw = bytes.fromhex(device_id)
        h = hashlib.sha256(raw).digest()
        return int.from_bytes(h, "big") >> 4

    # ── Mock proof path ───────────────────────────────────────────────────────

    def _mock_proof(
        self,
        scaled: list,
        device_id: str,
        humanity_prob_int: int,
        e4_drift_int: int,
        inference_result: int,
        epoch: int,
    ) -> Tuple[bytes, int, int, int]:
        """Mock proof (no real circuit). Encodes commitments into 256-byte wire format."""
        # Feature commitment: SHA-256 of packed scaled feature ints + inference code
        # Phase 62: include inference_result to mirror C1 Poseidon(8) circuit binding.
        # Different inference codes produce different commitments — forensically detectable.
        fc_bytes = hashlib.sha256(
            struct.pack(">7I", *scaled) + struct.pack(">I", inference_result)
        ).digest()
        feature_commitment_int = int.from_bytes(fc_bytes, "big")

        # Nullifier: SHA-256(device_field_element bytes || epoch bytes)
        dev_fe = self._device_id_to_field_element(device_id)
        null_bytes = hashlib.sha256(
            dev_fe.to_bytes(32, "big") + struct.pack(">I", epoch & 0xFFFFFFFF)
        ).digest()
        nullifier_hash_int = int.from_bytes(null_bytes, "big")

        # Build mock proof wire:
        # [0:32]  feature_commitment (32B big-endian)
        # [32:64] nullifier_hash (32B big-endian)
        # [64:66] humanity_prob_int (uint16 big-endian)
        # [66:256] zeros
        buf = bytearray(PROOF_SIZE)
        buf[0:32]  = fc_bytes
        buf[32:64] = null_bytes
        struct.pack_into(">H", buf, 64, humanity_prob_int & 0xFFFF)

        return bytes(buf), feature_commitment_int, humanity_prob_int, nullifier_hash_int

    def _verify_mock(
        self,
        proof_bytes: bytes,
        feature_commitment: int,
        humanity_prob_int: int,
        nullifier_hash: int,
    ) -> bool:
        """Structural mock verification — checks encoded values match."""
        fc_stored   = int.from_bytes(proof_bytes[0:32], "big")
        null_stored = int.from_bytes(proof_bytes[32:64], "big")
        hp_stored   = struct.unpack(">H", proof_bytes[64:66])[0]
        return (
            fc_stored   == feature_commitment and
            null_stored == nullifier_hash and
            hp_stored   == humanity_prob_int
        )

    # ── Real proof path ───────────────────────────────────────────────────────

    def _real_proof(
        self,
        scaled: list,
        device_id: str,
        humanity_prob_int: int,
        e4_drift_int: int,
        inference_result: int,
        epoch: int,
    ) -> Tuple[bytes, int, int, int]:
        """Real Groth16 proof via snarkjs subprocess."""
        with tempfile.TemporaryDirectory() as _tmp:
            tmpdir = Path(_tmp)

            # Write private inputs
            private_in = {
                "scaledFeatures":          [str(v) for v in scaled],
                "deviceId":                device_id,
                "l5HumanityInt":           humanity_prob_int,
                "e4DriftInt":              e4_drift_int,
                "inferenceResult":         inference_result,
                "inferenceCodeFromBody":   inference_result,   # Phase 62: binds pub[2] to C1 commitment
                "humanityProbInt":         humanity_prob_int,
                "epoch":                   epoch,
            }
            priv_path = tmpdir / "private_inputs_pitl.json"
            priv_path.write_text(json.dumps(private_in))

            # Compute Poseidon-based circuit inputs via Node.js
            circuit_in_path = tmpdir / "circuit_input_pitl.json"
            _run_node(
                str(_COMPUTE_INPUTS_PITL_JS),
                [str(priv_path)],
                capture_to=circuit_in_path,
                cwd=str(ZK_ARTIFACTS_DIR),
            )

            circuit_inputs = json.loads(circuit_in_path.read_text())
            feature_commitment_int = int(circuit_inputs["featureCommitment"])
            nullifier_hash_int     = int(circuit_inputs["nullifierHash"])
            hp_int                 = int(circuit_inputs["humanityProbInt"])

            # snarkjs groth16 fullprove
            proof_path  = tmpdir / "proof_pitl.json"
            public_path = tmpdir / "public_pitl.json"
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
                "PITL ZK proof generated — fc=%s null=%s hp=%d size=%d",
                hex(feature_commitment_int)[:18], hex(nullifier_hash_int)[:18],
                hp_int, len(proof_bytes),
            )
            return proof_bytes, feature_commitment_int, hp_int, nullifier_hash_int

    def _verify_real(
        self,
        proof_bytes: bytes,
        feature_commitment: int,
        humanity_prob_int: int,
        inference_result: int,
        nullifier_hash: int,
        epoch: int,
    ) -> bool:
        """Real Groth16 verification via snarkjs groth16 verify."""
        with tempfile.TemporaryDirectory() as _tmp:
            tmpdir = Path(_tmp)
            proof_json = _decode_proof(proof_bytes)
            (tmpdir / "proof_pitl.json").write_text(json.dumps(proof_json))

            # Public input order must exactly match circuit main declaration:
            # [featureCommitment, humanityProbInt, inferenceResult, nullifierHash, epoch]
            public = [
                str(feature_commitment),
                str(humanity_prob_int),
                str(inference_result),
                str(nullifier_hash),
                str(epoch),
            ]
            (tmpdir / "public_pitl.json").write_text(json.dumps(public))

            result = _run_snarkjs([
                "groth16", "verify",
                self._vkey,
                str(tmpdir / "public_pitl.json"),
                str(tmpdir / "proof_pitl.json"),
            ], check=False)
            return result.returncode == 0


# ---------------------------------------------------------------------------
# Proof encoding / decoding (same 256-byte ABI-packed format as ZKProver)
# ---------------------------------------------------------------------------

def _encode_proof(proof_json: dict) -> bytes:
    """Encode snarkjs proof.json → 256-byte ABI wire format."""
    def to_bytes32(v) -> bytes:
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


# ---------------------------------------------------------------------------
# Subprocess helpers (mirrors zk_prover.py exactly)
# ---------------------------------------------------------------------------

def _run_node(script: str, args: list, capture_to=None, cwd=None) -> None:
    """Run a Node.js script, optionally capturing stdout to a file."""
    cmd = ["node", script] + list(args)
    if capture_to:
        with Path(capture_to).open("w") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, cwd=cwd, check=False)
    else:
        r = subprocess.run(cmd, capture_output=True, cwd=cwd, check=False)
    if r.returncode != 0:
        stderr = r.stderr.decode(errors="replace") if r.stderr else ""
        raise RuntimeError(f"Node.js helper failed: {stderr[:600]}")


def _run_snarkjs(args: list, check: bool = True):
    """Run snarkjs via npx."""
    cmd = ["npx", "--yes", "snarkjs"] + args
    r = subprocess.run(cmd, capture_output=True, check=False)
    if check and r.returncode != 0:
        stderr = r.stderr.decode(errors="replace") if r.stderr else ""
        raise RuntimeError(f"snarkjs failed: {stderr[:600]}")
    return r
