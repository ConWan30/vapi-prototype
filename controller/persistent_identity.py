"""
VAPI Persistent Device Identity

Provides stable ECDSA-P256 keypair storage for the DualShock VAPI agent so that
device_id = keccak256(pubkey) is consistent across process restarts and can be
permanently registered in DeviceRegistry.sol.

Usage:
    from persistent_identity import PersistentIdentity, PersistentPoACEngine

    identity = PersistentIdentity()
    engine   = identity.make_engine()          # PersistentPoACEngine, key from disk
    dev_id   = identity.device_id             # bytes32, stable across restarts
    pub_hex  = identity.public_key_bytes.hex()  # for DeviceRegistry.registerDevice()
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default key directory — mirrors bridge DB path convention
DEFAULT_KEY_DIR = Path.home() / ".vapi"
KEY_FILE_NAME   = "dualshock_device_key.json"


# ---------------------------------------------------------------------------
# _HardwareKeyProxy — duck-types cryptography EllipticCurvePrivateKey.sign()
# ---------------------------------------------------------------------------

class _HardwareKeyProxy:
    """
    Duck-types cryptography EllipticCurvePrivateKey.sign() for PoACEngine compatibility.

    PoACEngine calls:
        der_sig = self.private_key.sign(body, ec.ECDSA(hashes.SHA256()))

    This proxy routes that call to a hardware backend and returns DER so that
    PoACEngine's subsequent decode_dss_signature() call succeeds transparently.

    The full chain:
        _HardwareKeyProxy.sign(body, algorithm=...)
            -> backend.sign(body)          # returns 64-byte raw r||s
            -> encode_dss_signature(r, s)  # convert to DER for PoACEngine
        PoACEngine:
            r, s = decode_dss_signature(der_sig)
            record.signature = r.to_bytes(32,'big') + s.to_bytes(32,'big')
    """

    def __init__(self, backend):
        """Accept any object with sign(bytes) -> bytes (64-byte raw r||s)."""
        self._backend = backend

    def sign(self, data: bytes, algorithm=None) -> bytes:
        """Called by PoACEngine.  Returns DER (PoACEngine decodes it back to r||s)."""
        raw_rs = self._backend.sign(data)
        if len(raw_rs) != 64:
            raise ValueError(
                f"Hardware backend returned {len(raw_rs)}-byte signature, expected 64"
            )
        r = int.from_bytes(raw_rs[:32], "big")
        s = int.from_bytes(raw_rs[32:], "big")
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        return encode_dss_signature(r, s)


def _keccak256(data: bytes) -> bytes:
    """keccak256 hash — matches DeviceRegistry.sol device ID computation."""
    try:
        from eth_hash.auto import keccak
        return keccak(data)
    except ImportError:
        # Fallback: SHA-3 (not strictly keccak, but acceptable for testing)
        import hashlib
        return hashlib.sha3_256(data).digest()


class PersistentIdentity:
    """
    Manages a persistent ECDSA-P256 (SECP256R1) keypair for a VAPI device.

    The private key is stored as a PKCS8-DER hex blob in a JSON file under
    ``key_dir``.  On first call to ``load_or_create()`` a fresh keypair is
    generated; on subsequent calls the same key is loaded from disk.

    The resulting ``device_id`` (keccak256 of the 65-byte uncompressed public
    key) is stable across restarts — matching the DeviceRegistry.sol formula.
    """

    def __init__(self, key_dir: Optional[Path] = None, signing_backend=None):
        self._key_dir        = Path(key_dir) if key_dir else DEFAULT_KEY_DIR
        self._key_file       = self._key_dir / KEY_FILE_NAME
        self._private_der    : Optional[bytes] = None
        self._public_bytes   : Optional[bytes] = None   # 65-byte uncompressed SEC1
        self._device_id      : Optional[bytes] = None   # 32-byte keccak256(pubkey)
        self._loaded         = False
        self._signing_backend = signing_backend          # Phase 9: hardware backend

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_or_create(self) -> "PersistentIdentity":
        """Load existing keypair from disk, or generate and persist a new one."""
        if self._loaded:
            return self
        if self._signing_backend is not None:
            # Hardware path: pubkey and device_id come from the backend
            self._public_bytes = self._signing_backend.public_key_bytes
            self._device_id    = _keccak256(self._public_bytes)
            # Still read JSON for registration metadata (registered_tx, etc.)
            self._load_metadata_only()
            self._loaded = True
            log.info(
                "Hardware identity: backend=%s device=%s...",
                self._signing_backend.backend_type, self._device_id.hex()[:16],
            )
            log.info("Public key:      %s...", self._public_bytes.hex()[:32])
        else:
            # Software path: identical to original behaviour
            if self._key_file.exists():
                self._load()
            else:
                self._generate()
            self._device_id = _keccak256(self._public_bytes)
            self._loaded = True
            log.info("Device identity: %s...", self._device_id.hex()[:16])
            log.info("Public key:      %s...", self._public_bytes.hex()[:32])
        return self

    @property
    def private_key_der(self) -> bytes:
        """PKCS8-DER encoded private key bytes."""
        self._ensure_loaded()
        return self._private_der

    @property
    def public_key_bytes(self) -> bytes:
        """65-byte uncompressed SEC1 public key (0x04 || x || y)."""
        self._ensure_loaded()
        return self._public_bytes

    @property
    def device_id(self) -> bytes:
        """32-byte device identity = keccak256(public_key_bytes)."""
        self._ensure_loaded()
        return self._device_id

    @property
    def is_chain_registered(self) -> bool:
        """True if a successful on-chain registration tx is recorded in the key file."""
        self._ensure_loaded()
        if not self._key_file.exists():
            return False
        try:
            data = json.loads(self._key_file.read_text())
            return bool(data.get("registered_tx", ""))
        except Exception:
            return False

    @property
    def registration_tier(self) -> str:
        """Registration tier: 'Emulated', 'Standard', or 'Attested'."""
        self._ensure_loaded()
        if not self._key_file.exists():
            return "Standard"
        try:
            return json.loads(self._key_file.read_text()).get("registration_tier", "Standard")
        except Exception:
            return "Standard"

    def mark_chain_registered(
        self, tx_hash: str, registry_address: str, tier: str = "Standard"
    ) -> None:
        """Persist on-chain registration confirmation into the key file (atomic write)."""
        import datetime
        self._ensure_loaded()
        try:
            data = json.loads(self._key_file.read_text())
        except Exception:
            data = {}
        data["registered_tx"]      = tx_hash
        data["registry_address"]   = registry_address
        data["registered_at_iso"]  = datetime.datetime.now(datetime.timezone.utc).isoformat()
        data["registration_tier"]  = tier
        # Atomic write: tmp file + rename
        tmp = self._key_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._key_file)
        log.info("Chain registration persisted: tx=%s... tier=%s", tx_hash[:16], tier)

    def make_engine(self) -> "PersistentPoACEngine":
        """
        Create a PersistentPoACEngine pre-loaded with this keypair.

        The engine generates PoAC records signed with the persistent key,
        ensuring device_id is stable across restarts.
        """
        self._ensure_loaded()
        if self._signing_backend is not None:
            return PersistentPoACEngine(
                private_key_der=None,
                signing_backend=self._signing_backend,
            )
        return PersistentPoACEngine(self._private_der)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self):
        if not self._loaded:
            self.load_or_create()

    def _load_metadata_only(self) -> None:
        """Read registration metadata from JSON without touching key fields."""
        if not self._key_file.exists():
            return
        try:
            data = json.loads(self._key_file.read_text())
            # Only metadata — private key fields are NOT read here
            self._registered_tx      = data.get("registered_tx", "")
            self._registry_address   = data.get("registry_address", "")
            self._registration_tier  = data.get("registration_tier", "Standard")
        except Exception as exc:
            log.debug("_load_metadata_only: could not read JSON (%s)", exc)

    def _load(self):
        try:
            data = json.loads(self._key_file.read_text())
            self._private_der  = bytes.fromhex(data["private_der_hex"])
            self._public_bytes = bytes.fromhex(data["public_key_hex"])
            log.info("Loaded persistent keypair from %s", self._key_file)
        except Exception as exc:
            log.warning("Keypair load failed (%s) — regenerating", exc)
            self._generate()

    def _generate(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption,
        )
        privkey = ec.generate_private_key(ec.SECP256R1())
        self._private_der  = privkey.private_bytes(
            Encoding.DER, PrivateFormat.PKCS8, NoEncryption()
        )
        self._public_bytes = privkey.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        self._key_dir.mkdir(parents=True, exist_ok=True)
        self._key_file.write_text(json.dumps({
            "private_der_hex": self._private_der.hex(),
            "public_key_hex":  self._public_bytes.hex(),
            "created_at_iso":  __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }))
        log.info("Generated persistent keypair -> %s", self._key_file)

    def __repr__(self) -> str:
        loaded = self._loaded
        return (f"PersistentIdentity(loaded={loaded}, "
                f"device_id={self._device_id.hex()[:16] + '...' if self._device_id else 'None'})")


# ---------------------------------------------------------------------------
# PersistentPoACEngine — PoACEngine with injected persistent keypair
# ---------------------------------------------------------------------------

class PersistentPoACEngine:
    """
    Wraps PoACEngine from dualshock_emulator.py, substituting the ephemeral
    session keypair with a persistent one loaded from PersistentIdentity.

    This class dynamically imports PoACEngine so the controller directory does
    not need to be on sys.path at import time.

    Example::

        identity = PersistentIdentity().load_or_create()
        engine   = identity.make_engine()
        raw_228  = engine.generate(sensor_hash, wm_hash, 0x20, 0x01, 220, 95).serialize_full()
    """

    def __init__(self, private_key_der: Optional[bytes], signing_backend=None):
        self._private_der     = private_key_der
        self._signing_backend = signing_backend   # Phase 9: hardware backend
        self._engine          = None
        self._public_bytes    : Optional[bytes] = None

    def _ensure_init(self):
        if self._engine is not None:
            return
        import sys
        from pathlib import Path
        controller_dir = Path(__file__).parent
        if str(controller_dir) not in sys.path:
            sys.path.insert(0, str(controller_dir))

        from dualshock_emulator import PoACEngine

        engine = PoACEngine()   # generates a throwaway ephemeral key

        if self._signing_backend is not None:
            # Hardware path: inject proxy so PoACEngine.generate() calls hardware
            engine.private_key      = _HardwareKeyProxy(self._signing_backend)
            engine.public_key_bytes = self._signing_backend.public_key_bytes
        else:
            # Software path: identical to original behaviour
            from cryptography.hazmat.primitives.serialization import (
                load_der_private_key, Encoding, PublicFormat,
            )
            privkey = load_der_private_key(self._private_der, password=None)
            engine.private_key      = privkey
            engine.public_key_bytes = privkey.public_key().public_bytes(
                Encoding.X962, PublicFormat.UncompressedPoint
            )

        self._engine       = engine
        self._public_bytes = engine.public_key_bytes
        log.debug("PersistentPoACEngine ready, pubkey=%s...", self._public_bytes.hex()[:16])

    # Delegate all PoACEngine API to the wrapped instance
    def generate(self, sensor_hash, wm_hash, inference, action,
                 confidence, battery_pct, bounty_id=0):
        self._ensure_init()
        return self._engine.generate(
            sensor_hash, wm_hash, inference, action,
            confidence, battery_pct, bounty_id=bounty_id,
        )

    @property
    def public_key_bytes(self) -> bytes:
        self._ensure_init()
        return self._public_bytes

    @property
    def counter(self) -> int:
        self._ensure_init()
        return self._engine.counter

    @property
    def chain_head(self) -> bytes:
        self._ensure_init()
        return self._engine.chain_head
