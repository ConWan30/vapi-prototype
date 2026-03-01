"""Phase 9: Hardware Signing Backend abstraction for VAPI PoAC pipeline.

Provides a unified SigningBackend interface that routes ECDSA-P256 signing
to hardware security elements (YubiKey PIV, ATECC608A) or a software fallback.

The _HardwareKeyProxy (in persistent_identity.py) wraps any SigningBackend so
PoACEngine.generate() continues to call a duck-typed EllipticCurvePrivateKey.sign()
without modification.

DER/Raw-RS contract:
    All hardware backends return 64-byte raw r||s.
    _HardwareKeyProxy converts to DER for PoACEngine, which decodes it back to r||s.
    SoftwareIdentityBackend also returns raw r||s (not DER) for consistency.
"""

import abc
import hashlib
import json
import logging
import os
import tempfile
import warnings
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SigningBackend(abc.ABC):
    """Abstract signing backend.  All backends sign over raw body bytes."""

    @abc.abstractmethod
    def sign(self, body: bytes) -> bytes:
        """Sign body data.  Returns 64-byte raw r||s (NOT DER)."""

    @property
    @abc.abstractmethod
    def public_key_bytes(self) -> bytes:
        """Return 65-byte uncompressed SEC1 public key (0x04 || x || y)."""

    @property
    @abc.abstractmethod
    def backend_type(self) -> str:
        """Short identifier string, e.g. 'software', 'yubikey', 'atecc608'."""

    @property
    @abc.abstractmethod
    def is_hardware_backed(self) -> bool:
        """True if the private key resides in a hardware secure element."""

    @abc.abstractmethod
    def setup(self) -> None:
        """Idempotent setup: load or generate keypair.  Must be called before sign()."""

    @property
    def attestation_certificate_hash(self) -> Optional[bytes]:
        """SHA-256 of the hardware attestation certificate DER, or None."""
        return None


# ---------------------------------------------------------------------------
# Software backend (plaintext key file — dev/testing only)
# ---------------------------------------------------------------------------

class SoftwareIdentityBackend(SigningBackend):
    """
    ECDSA-P256 signing backed by a plaintext JSON key file.

    WARNING: INSECURE — the private key is stored unencrypted on disk.
    Use YubiKeyIdentityBackend or ATECC608IdentityBackend for production.

    JSON schema::

        {
          "private_der_hex":  "<PKCS8-DER hex>",
          "public_key_hex":   "<65-byte uncompressed SEC1 hex>",
          "created_at_iso":   "<ISO timestamp>",
          // Any additional metadata fields are preserved on setup().
        }
    """

    def __init__(self, key_path: str):
        self._key_path   = Path(key_path)
        self._private_key = None  # cryptography EllipticCurvePrivateKey
        self._pub_bytes  : Optional[bytes] = None

    # --- SigningBackend interface ---

    def setup(self) -> None:
        """Load from JSON file or generate and persist a new keypair."""
        warnings.warn(
            "SoftwareIdentityBackend: private key stored in plaintext at "
            f"{self._key_path}. INSECURE — DEV ONLY. "
            "Use a hardware backend for production.",
            UserWarning,
            stacklevel=2,
        )
        log.warning(
            "SoftwareIdentityBackend: INSECURE/DEV ONLY — plaintext key at %s",
            self._key_path,
        )
        if self._key_path.exists():
            self._load()
        else:
            self._generate()

    def sign(self, body: bytes) -> bytes:
        """Sign body; returns 64-byte raw r||s."""
        if self._private_key is None:
            raise RuntimeError("setup() must be called before sign()")
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.hashes import SHA256
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        der = self._private_key.sign(body, ec.ECDSA(SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    @property
    def public_key_bytes(self) -> bytes:
        if self._pub_bytes is None:
            raise RuntimeError("setup() must be called before public_key_bytes")
        return self._pub_bytes

    @property
    def backend_type(self) -> str:
        return "software"

    @property
    def is_hardware_backed(self) -> bool:
        return False

    @property
    def attestation_certificate_hash(self) -> Optional[bytes]:
        return None

    # --- Private helpers ---

    def _load(self) -> None:
        try:
            data = json.loads(self._key_path.read_text())
            from cryptography.hazmat.primitives.serialization import (
                load_der_private_key, Encoding, PublicFormat,
            )
            priv_der = bytes.fromhex(data["private_der_hex"])
            self._private_key = load_der_private_key(priv_der, password=None)
            self._pub_bytes = bytes.fromhex(data["public_key_hex"])
            log.info("SoftwareIdentityBackend: loaded keypair from %s", self._key_path)
        except Exception as exc:
            log.warning("SoftwareIdentityBackend: load failed (%s) — regenerating", exc)
            self._generate()

    def _generate(self) -> None:
        import datetime
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, PrivateFormat, NoEncryption,
        )

        # Read any existing metadata fields to preserve them
        preserved: dict = {}
        if self._key_path.exists():
            try:
                existing = json.loads(self._key_path.read_text())
                for k in ("registered_tx", "registry_address",
                           "registration_tier", "registered_at_iso"):
                    if k in existing:
                        preserved[k] = existing[k]
            except Exception:
                pass

        privkey = ec.generate_private_key(ec.SECP256R1())
        priv_der = privkey.private_bytes(
            Encoding.DER, PrivateFormat.PKCS8, NoEncryption()
        )
        pub_bytes = privkey.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        self._private_key = privkey
        self._pub_bytes   = pub_bytes

        data = {
            "private_der_hex": priv_der.hex(),
            "public_key_hex":  pub_bytes.hex(),
            "created_at_iso":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        data.update(preserved)

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp → rename
        tmp = self._key_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._key_path)
        log.info("SoftwareIdentityBackend: generated new keypair at %s", self._key_path)


# ---------------------------------------------------------------------------
# YubiKey PIV backend
# ---------------------------------------------------------------------------

_YUBIKEY_SLOT_MAP = {
    "9a": "AUTHENTICATION",
    "9c": "SIGNATURE",
    "9d": "KEY_MANAGEMENT",
    "9e": "CARD_AUTH",
}


class YubiKeyIdentityBackend(SigningBackend):
    """
    ECDSA-P256 signing backed by a YubiKey 5 PIV slot.

    Requirements:
        pip install yubikey-manager

    The YubiKey PIV sign() takes a pre-computed digest — this backend
    MUST pre-hash with SHA-256 before passing to the device.
    """

    def __init__(
        self,
        piv_slot: str = "9c",
        management_key: Optional[bytes] = None,
    ):
        self._piv_slot_str   = piv_slot.lower()
        self._management_key = management_key
        self._pub_bytes      : Optional[bytes] = None
        self._cert_hash      : Optional[bytes] = None
        self._slot           = None   # yubikit SLOT constant
        self._session        = None   # PivSession (reconnect each sign)

    # --- SigningBackend interface ---

    def setup(self) -> None:
        try:
            from yubikit.piv import PivSession, KEY_TYPE, SLOT
            from ykman.device import connect_to_device
        except ImportError:
            raise ImportError(
                "YubiKey backend requires yubikey-manager: pip install yubikey-manager"
            )

        slot_name = _YUBIKEY_SLOT_MAP.get(self._piv_slot_str, "SIGNATURE")
        self._slot = getattr(SLOT, slot_name)

        connection, device, info = connect_to_device()
        piv = PivSession(connection)

        # Generate key if slot is empty
        try:
            cert = piv.get_certificate(self._slot)
            pub  = cert.public_key()
        except Exception:
            # Slot empty — generate a new key
            if self._management_key:
                piv.authenticate(self._management_key)
            elif hasattr(piv, "authenticate"):
                # Use default management key for testnet / dev
                from yubikit.piv import DEFAULT_MANAGEMENT_KEY
                try:
                    piv.authenticate(DEFAULT_MANAGEMENT_KEY)
                except Exception:
                    pass
            pub = piv.generate_key(self._slot, KEY_TYPE.ECCP256)

        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat,
        )
        self._pub_bytes = pub.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )

        # Attempt attestation cert hash
        try:
            from yubikit.piv import SLOT as PIV_SLOT
            attest_cert = piv.get_certificate(PIV_SLOT.ATTESTATION)
            attest_der  = attest_cert.public_bytes(Encoding.DER)
            self._cert_hash = hashlib.sha256(attest_der).digest()
        except Exception:
            self._cert_hash = None

        log.info(
            "YubiKeyIdentityBackend: slot=%s pubkey=%s... cert_hash=%s",
            self._piv_slot_str,
            self._pub_bytes.hex()[:16],
            self._cert_hash.hex()[:16] if self._cert_hash else "None",
        )

    def sign(self, body: bytes) -> bytes:
        """Pre-hash with SHA-256 then sign via PIV.  Returns 64-byte raw r||s."""
        if self._pub_bytes is None:
            raise RuntimeError("setup() must be called before sign()")
        try:
            from yubikit.piv import PivSession, KEY_TYPE, SLOT
            from ykman.device import connect_to_device
        except ImportError:
            raise ImportError(
                "YubiKey backend requires yubikey-manager: pip install yubikey-manager"
            )
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from cryptography.hazmat.primitives import hashes

        digest = hashlib.sha256(body).digest()  # PIV takes digest, NOT raw data

        connection, device, info = connect_to_device()
        piv = PivSession(connection)

        # Build prehashed algorithm object — location varies by cryptography version
        try:
            from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
            prehashed_algo = Prehashed(hashes.SHA256())
        except ImportError:
            try:
                prehashed_algo = hashes.Prehashed(hashes.SHA256())
            except AttributeError:
                prehashed_algo = None

        # yubikey-manager 5.x: accepts Prehashed digest; 4.x: raw body + SHA256
        try:
            if prehashed_algo is not None:
                der_sig = piv.sign(
                    self._slot,
                    KEY_TYPE.ECCP256,
                    digest,
                    prehashed_algo,
                )
            else:
                raise TypeError("Prehashed not available")
        except (TypeError, AttributeError):
            der_sig = piv.sign(self._slot, KEY_TYPE.ECCP256, body, hashes.SHA256())

        r, s = decode_dss_signature(der_sig)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    @property
    def public_key_bytes(self) -> bytes:
        if self._pub_bytes is None:
            raise RuntimeError("setup() must be called before public_key_bytes")
        return self._pub_bytes

    @property
    def backend_type(self) -> str:
        return "yubikey"

    @property
    def is_hardware_backed(self) -> bool:
        return True

    @property
    def attestation_certificate_hash(self) -> Optional[bytes]:
        return self._cert_hash


# ---------------------------------------------------------------------------
# ATECC608A I2C backend
# ---------------------------------------------------------------------------

class ATECC608IdentityBackend(SigningBackend):
    """
    ECDSA-P256 signing backed by a Microchip ATECC608A via I2C.

    Requirements:
        pip install cryptoauthlib

    The atcab_sign() function takes a 32-byte digest — this backend
    MUST pre-hash with SHA-256 before passing to the chip.
    """

    def __init__(self, i2c_bus: int = 1, i2c_address: int = 0x60):
        self._i2c_bus     = i2c_bus
        self._i2c_address = i2c_address
        self._pub_bytes   : Optional[bytes] = None

    # --- SigningBackend interface ---

    def setup(self) -> None:
        try:
            import cryptoauthlib as cal
            from cryptoauthlib import (
                atcab_init, atcab_get_pubkey, atcab_genkey,
                cfg_ateccx08a_i2c_default,
            )
        except ImportError:
            raise ImportError(
                "ATECC608A backend requires cryptoauthlib: pip install cryptoauthlib"
            )

        cfg = cfg_ateccx08a_i2c_default()
        cfg.cfg.atcai2c.slave_address = self._i2c_address
        cfg.cfg.atcai2c.bus           = self._i2c_bus
        atcab_init(cfg)

        from cryptoauthlib import atcab_get_pubkey, atcab_genkey, ATCA_SUCCESS
        pub_raw = bytearray(64)
        ret = atcab_get_pubkey(0, pub_raw)
        if ret != 0:  # slot empty or not locked
            ret = atcab_genkey(0, pub_raw)
            if ret != 0:
                raise RuntimeError(f"atcab_genkey returned {ret}")

        # ATECC608 returns raw 64-byte x||y — prepend 0x04 for uncompressed SEC1
        self._pub_bytes = b"\x04" + bytes(pub_raw)
        log.info(
            "ATECC608IdentityBackend: bus=%d addr=0x%02x pubkey=%s...",
            self._i2c_bus, self._i2c_address, self._pub_bytes.hex()[:16],
        )

    def sign(self, body: bytes) -> bytes:
        """Pre-hash with SHA-256 then sign via ATECC608.  Returns 64-byte raw r||s."""
        if self._pub_bytes is None:
            raise RuntimeError("setup() must be called before sign()")
        try:
            from cryptoauthlib import atcab_sign
        except ImportError:
            raise ImportError(
                "ATECC608A backend requires cryptoauthlib: pip install cryptoauthlib"
            )

        digest = hashlib.sha256(body).digest()  # atcab_sign takes digest, NOT raw
        sig_buf = bytearray(64)
        ret = atcab_sign(0, digest, sig_buf)
        if ret != 0:
            raise RuntimeError(f"atcab_sign returned {ret}")
        return bytes(sig_buf)

    @property
    def public_key_bytes(self) -> bytes:
        if self._pub_bytes is None:
            raise RuntimeError("setup() must be called before public_key_bytes")
        return self._pub_bytes

    @property
    def backend_type(self) -> str:
        return "atecc608"

    @property
    def is_hardware_backed(self) -> bool:
        return True

    @property
    def attestation_certificate_hash(self) -> Optional[bytes]:
        # Phase 10: read cert via atcab_read_config_zone
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_backend(backend_type: str, **kwargs) -> SigningBackend:
    """
    Instantiate a SigningBackend by type name.

    Supported types:
        "software"              — SoftwareIdentityBackend(key_path=...)
        "yubikey"               — YubiKeyIdentityBackend(piv_slot=..., management_key=...)
        "atecc608" / "atecc608a" / "atecc608b"
                                — ATECC608IdentityBackend(i2c_bus=..., i2c_address=...)

    Does NOT call setup() — caller's responsibility.

    Raises ValueError for unknown backend type.
    """
    t = backend_type.lower()

    if t == "software":
        key_path = kwargs.get("key_path")
        if not key_path:
            raise ValueError("create_backend('software') requires key_path=...")
        return SoftwareIdentityBackend(key_path=key_path)

    if t == "yubikey":
        return YubiKeyIdentityBackend(
            piv_slot=kwargs.get("piv_slot", "9c"),
            management_key=kwargs.get("management_key"),
        )

    if t in ("atecc608", "atecc608a", "atecc608b"):
        return ATECC608IdentityBackend(
            i2c_bus=kwargs.get("i2c_bus", 1),
            i2c_address=kwargs.get("i2c_address", 0x60),
        )

    raise ValueError(
        f"Unknown backend type: {backend_type!r}. "
        "Valid values: 'software', 'yubikey', 'atecc608'."
    )
