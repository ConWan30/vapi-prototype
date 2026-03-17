"""
Phase 56 — PassportProver: Groth16 ZK proof for TournamentPassport circuit.

Proves N=5 consecutive NOMINAL PITL sessions meet tournament eligibility
(humanity >= 60%, ioID-bound device) without revealing raw session data.

Artifact env vars:
  VAPI_PASSPORT_WASM_PATH  — bridge/zk_artifacts/TournamentPassport.wasm
  VAPI_PASSPORT_ZKEY_PATH  — bridge/zk_artifacts/TournamentPassport_final.zkey
  VAPI_PASSPORT_VKEY_PATH  — bridge/zk_artifacts/TournamentPassport_verification_key.json

Setup (one-time, after Phase 56 circom ceremony):
  Artifacts are already in bridge/zk_artifacts/ after Phase 56 setup.sh execution.

Proof wire format (256 bytes, same as PITLProver / ZKProver):
  [0:64]    pi_a  (a[0], a[1]) — G1 point
  [64:192]  pi_b  (b[0][0..3]) — G2 point
  [192:256] pi_c  (c[0], c[1]) — G1 point

Mock proof wire format (Phase 56 testnet default):
  [0:32]    passport_hash       (SHA-256 of concatenated nullifiers, 32B big-endian)
  [32:34]   min_humanity_int    (uint16 big-endian)
  [34:256]  zeros

Circuit public inputs (5, must match TournamentPassport.circom declaration order):
  [0] deviceIdHash    — Poseidon(deviceSecret)
  [1] ioidTokenId     — ioID token ID (0 for testnet mock)
  [2] passportHash    — Poseidon(sessionNullifiers[0..4])
  [3] minHumanityInt  — min(sessionHumanities)
  [4] epoch

Session count: N=5 (SESSION_COUNT constant in PITLTournamentPassport.sol)
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
from typing import List, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_COUNT  = 5       # Must match PITLTournamentPassport.SESSION_COUNT
HUMANITY_SCALE = 1000    # [0,1] × 1000 → [0,1000]
MIN_HUMANITY   = 600     # 60% — circuit C2 constraint
PROOF_SIZE     = 256     # Groth16 BN254 uncompressed (same as ZKProver / PITLProver)

_THIS_DIR        = Path(__file__).parent.parent   # bridge/
ZK_ARTIFACTS_DIR = _THIS_DIR / "zk_artifacts"

_PASSPORT_WASM = os.getenv(
    "VAPI_PASSPORT_WASM_PATH",
    str(ZK_ARTIFACTS_DIR / "TournamentPassport.wasm"),
)
_PASSPORT_ZKEY = os.getenv(
    "VAPI_PASSPORT_ZKEY_PATH",
    str(ZK_ARTIFACTS_DIR / "TournamentPassport_final.zkey"),
)
_PASSPORT_VKEY = os.getenv(
    "VAPI_PASSPORT_VKEY_PATH",
    str(ZK_ARTIFACTS_DIR / "TournamentPassport_verification_key.json"),
)

_COMPUTE_INPUTS_PASSPORT_JS = ZK_ARTIFACTS_DIR / "compute_inputs_passport.js"


def _artifacts_available(wasm: str, zkey: str) -> bool:
    """True when all required TournamentPassport ZK artifacts exist and are non-empty."""
    js_ok = (
        _COMPUTE_INPUTS_PASSPORT_JS.is_file() and
        (ZK_ARTIFACTS_DIR / "node_modules" / "circomlibjs").is_dir()
    )
    for p in (wasm, zkey):
        if not p or not Path(p).is_file() or Path(p).stat().st_size == 0:
            return False
    return js_ok


# Module-level flag — importable by callers to branch between real/mock paths
PASSPORT_ZK_ARTIFACTS_AVAILABLE: bool = _artifacts_available(_PASSPORT_WASM, _PASSPORT_ZKEY)


# ---------------------------------------------------------------------------
# PassportProver
# ---------------------------------------------------------------------------

class PassportProver:
    """
    Groth16 ZK proof generation and verification for the TournamentPassport circuit.

    Usage:
        prover = PassportProver()
        proof, passport_hash, min_hp = prover.generate_proof(
            session_nullifiers=["123...", ...],   # 5 decimal-string nullifiers
            session_humanitys=[0.82, 0.75, ...],  # 5 humanity_prob floats [0,1]
            device_secret="aabb..." * 32,          # 64-char hex device secret
            ioid_token_id=0,
            epoch=0,
        )
        ok = prover.verify_proof(proof, device_id_hash, 0, passport_hash, min_hp, 0)
    """

    def __init__(
        self,
        wasm_path: str = _PASSPORT_WASM,
        zkey_path: str = _PASSPORT_ZKEY,
        vkey_path: str = _PASSPORT_VKEY,
    ) -> None:
        self._wasm = wasm_path
        self._zkey = zkey_path
        self._vkey = vkey_path
        self._available = _artifacts_available(wasm_path, zkey_path)
        if not self._available:
            log.info(
                "TournamentPassport ZK artifacts available — using real Groth16 proofs."
                if Path(wasm_path).is_file() and Path(zkey_path).is_file()
                else "TournamentPassport ZK artifacts unavailable — using mock proofs. "
                     "Artifacts exist at bridge/zk_artifacts/ after Phase 56 setup."
            )

    # ── Public API ──────────────────────────────────────────────────────────

    def generate_proof(
        self,
        session_nullifiers: List[str],
        session_humanitys: List[float],
        device_secret: str,
        ioid_token_id: int = 0,
        epoch: int = 0,
    ) -> Tuple[bytes, bytes, int]:
        """Generate a 256-byte TournamentPassport proof.

        Args:
            session_nullifiers: 5 PITL nullifier hashes (hex strings or decimal strings)
            session_humanitys:  5 humanity_prob values ∈ [0, 1]
            device_secret:      64-char hex device secret (private — never logged)
            ioid_token_id:      ioID token ID (0 for testnet mock)
            epoch:              batch epoch (anti-replay)

        Returns:
            (proof_bytes[256], passport_hash_bytes[32], min_humanity_int)

        Raises:
            ValueError: if session count != 5 or any humanity < 0.60
        """
        if len(session_nullifiers) != SESSION_COUNT:
            raise ValueError(f"Expected {SESSION_COUNT} nullifiers, got {len(session_nullifiers)}")
        if len(session_humanitys) != SESSION_COUNT:
            raise ValueError(f"Expected {SESSION_COUNT} humanitys, got {len(session_humanitys)}")

        humanity_ints = [
            max(0, min(1000, round(h * HUMANITY_SCALE))) for h in session_humanitys
        ]
        min_humanity_int = min(humanity_ints)

        if min_humanity_int < MIN_HUMANITY:
            raise ValueError(
                f"All sessions must have humanity >= {MIN_HUMANITY/HUMANITY_SCALE:.0%}; "
                f"got min={min_humanity_int/HUMANITY_SCALE:.1%}"
            )

        # Normalise nullifiers to decimal strings for the circuit
        nullifier_ints = [
            int(n, 16) if (isinstance(n, str) and n.startswith(("0x", "0X")))
            else int(n, 16) if (isinstance(n, str) and len(n) == 64)
            else int(n)
            for n in session_nullifiers
        ]

        if self._available:
            return self._real_proof(
                nullifier_ints, humanity_ints, device_secret,
                ioid_token_id, min_humanity_int, epoch,
            )
        return self._mock_proof(nullifier_ints, min_humanity_int)

    def verify_proof(
        self,
        proof_bytes: bytes,
        device_id_hash: int,
        ioid_token_id: int,
        passport_hash: bytes,
        min_humanity_int: int,
        epoch: int,
    ) -> bool:
        """Verify a 256-byte TournamentPassport proof.

        Real mode: Groth16 cryptographic verification via snarkjs.
        Mock mode: structural check against encoded passport_hash and min_humanity_int.
        """
        if len(proof_bytes) != PROOF_SIZE:
            return False
        if self._available and self._vkey and Path(self._vkey).is_file():
            return self._verify_real(
                proof_bytes, device_id_hash, ioid_token_id,
                passport_hash, min_humanity_int, epoch,
            )
        return self._verify_mock(proof_bytes, passport_hash, min_humanity_int)

    # ── Mock proof path ───────────────────────────────────────────────────────

    def _mock_proof(
        self,
        nullifier_ints: List[int],
        min_humanity_int: int,
    ) -> Tuple[bytes, bytes, int]:
        """Mock proof (no real circuit). Encodes passport_hash + min_humanity into wire."""
        # passport_hash: SHA-256 of concatenated nullifiers (mirrors Poseidon in mock)
        passport_hash_bytes = hashlib.sha256(
            b"".join(n.to_bytes(32, "big") for n in nullifier_ints)
        ).digest()

        buf = bytearray(PROOF_SIZE)
        buf[0:32] = passport_hash_bytes
        struct.pack_into(">H", buf, 32, min_humanity_int & 0xFFFF)

        return bytes(buf), passport_hash_bytes, min_humanity_int

    def _verify_mock(
        self,
        proof_bytes: bytes,
        passport_hash: bytes,
        min_humanity_int: int,
    ) -> bool:
        """Structural mock verification — checks encoded values match."""
        ph_stored  = proof_bytes[0:32]
        mh_stored  = struct.unpack(">H", proof_bytes[32:34])[0]
        return ph_stored == passport_hash and mh_stored == min_humanity_int

    # ── Real proof path ───────────────────────────────────────────────────────

    def _real_proof(
        self,
        nullifier_ints: List[int],
        humanity_ints: List[int],
        device_secret: str,
        ioid_token_id: int,
        min_humanity_int: int,
        epoch: int,
    ) -> Tuple[bytes, bytes, int]:
        """Real Groth16 proof via snarkjs subprocess."""
        with tempfile.TemporaryDirectory() as _tmp:
            tmpdir = Path(_tmp)

            private_in = {
                "deviceSecret":       device_secret.replace("0x", ""),
                "sessionNullifiers":  [str(n) for n in nullifier_ints],
                "sessionHumanities":  humanity_ints,
                "ioidTokenId":        ioid_token_id,
                "minHumanityInt":     min_humanity_int,
                "epoch":              epoch,
            }
            priv_path = tmpdir / "private_inputs_passport.json"
            priv_path.write_text(json.dumps(private_in))

            # Compute Poseidon-based circuit inputs via Node.js
            circuit_in_path = tmpdir / "circuit_input_passport.json"
            _run_node(
                str(_COMPUTE_INPUTS_PASSPORT_JS),
                [str(priv_path)],
                capture_to=circuit_in_path,
                cwd=str(ZK_ARTIFACTS_DIR),
            )

            circuit_inputs  = json.loads(circuit_in_path.read_text())
            passport_hash_int = int(circuit_inputs["passportHash"])

            # snarkjs groth16 fullprove
            proof_path  = tmpdir / "proof_passport.json"
            public_path = tmpdir / "public_passport.json"
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
            passport_hash_bytes = passport_hash_int.to_bytes(32, "big")

            log.info(
                "TournamentPassport ZK proof generated — passport=%s min_hp=%d size=%d",
                hex(passport_hash_int)[:18], min_humanity_int, len(proof_bytes),
            )
            return proof_bytes, passport_hash_bytes, min_humanity_int

    def _verify_real(
        self,
        proof_bytes: bytes,
        device_id_hash: int,
        ioid_token_id: int,
        passport_hash: bytes,
        min_humanity_int: int,
        epoch: int,
    ) -> bool:
        """Real Groth16 verification via snarkjs groth16 verify."""
        with tempfile.TemporaryDirectory() as _tmp:
            tmpdir = Path(_tmp)
            proof_json = _decode_proof(proof_bytes)
            (tmpdir / "proof_passport.json").write_text(json.dumps(proof_json))

            # Public input order must exactly match TournamentPassport.circom main declaration:
            # [deviceIdHash, ioidTokenId, passportHash, minHumanityInt, epoch]
            passport_hash_int = int.from_bytes(passport_hash, "big")
            public = [
                str(device_id_hash),
                str(ioid_token_id),
                str(passport_hash_int),
                str(min_humanity_int),
                str(epoch),
            ]
            (tmpdir / "public_passport.json").write_text(json.dumps(public))

            result = _run_snarkjs([
                "groth16", "verify",
                self._vkey,
                str(tmpdir / "public_passport.json"),
                str(tmpdir / "proof_passport.json"),
            ], check=False)
            return result.returncode == 0


# ---------------------------------------------------------------------------
# Proof encoding / decoding (same 256-byte ABI-packed format as PITLProver)
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
# Subprocess helpers (mirrors pitl_prover.py exactly)
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
