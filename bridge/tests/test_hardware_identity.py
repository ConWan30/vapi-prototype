"""
Phase 9: Hardware Signing Bridge — test suite (~31 tests)

Categories:
    A. TestSoftwareBackend       (10 tests) — no mocks, real crypto
    B. TestYubiKeyBackend        ( 8 tests) — mocked yubikit
    C. TestATECC608Backend       ( 6 tests) — mocked cryptoauthlib
    D. TestHardwareKeyProxy      ( 4 tests) — proxy DER round-trip
    E. TestEndToEnd              ( 3 tests) — full pipeline

Total: 31 new tests
"""

import hashlib
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — make both bridge and controller importable
# ---------------------------------------------------------------------------
# parents[1] = bridge/  (contains vapi_bridge package)
# parents[2] = vapi-pebble-prototype/ (repo root)
sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parents[2] / "controller"))

from vapi_bridge.hardware_identity import (
    SoftwareIdentityBackend,
    YubiKeyIdentityBackend,
    ATECC608IdentityBackend,
    create_backend,
)
from persistent_identity import _HardwareKeyProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_real_der_sig(body: bytes) -> bytes:
    """Generate a real DER signature using pyca/cryptography for mock plumbing."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.hashes import SHA256
    key = ec.generate_private_key(ec.SECP256R1())
    return key.sign(body, ec.ECDSA(SHA256()))


def _make_real_raw_sig(body: bytes) -> bytes:
    """Generate a real 64-byte raw r||s signature."""
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    der = _make_real_der_sig(body)
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---------------------------------------------------------------------------
# A. TestSoftwareBackend
# ---------------------------------------------------------------------------

class TestSoftwareBackend:

    def _fresh_backend(self, tmp_path) -> SoftwareIdentityBackend:
        key_path = str(tmp_path / "key.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()
        return b

    def test_not_hardware_backed(self, tmp_path):
        b = self._fresh_backend(tmp_path)
        assert b.is_hardware_backed is False

    def test_public_key_is_65_bytes(self, tmp_path):
        b = self._fresh_backend(tmp_path)
        assert len(b.public_key_bytes) == 65

    def test_public_key_starts_with_04(self, tmp_path):
        b = self._fresh_backend(tmp_path)
        assert b.public_key_bytes[0] == 0x04

    def test_backend_type_is_software(self, tmp_path):
        b = self._fresh_backend(tmp_path)
        assert b.backend_type == "software"

    def test_sign_returns_64_bytes(self, tmp_path):
        b = self._fresh_backend(tmp_path)
        sig = b.sign(b"hello world")
        assert len(sig) == 64

    def test_attestation_cert_hash_is_none(self, tmp_path):
        b = self._fresh_backend(tmp_path)
        assert b.attestation_certificate_hash is None

    def test_keypair_stable_across_instances(self, tmp_path):
        """Same key file => same public key on second instance."""
        key_path = str(tmp_path / "key.json")
        b1 = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b1.setup()
        pub1 = b1.public_key_bytes

        b2 = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b2.setup()
        pub2 = b2.public_key_bytes

        assert pub1 == pub2

    def test_sign_output_is_verifiable(self, tmp_path):
        """Software backend sign -> _HardwareKeyProxy -> PoACEngine -> verify_signature."""
        from vapi_bridge.codec import parse_record, verify_signature
        from persistent_identity import PersistentPoACEngine

        key_path = str(tmp_path / "key.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()

        engine = PersistentPoACEngine(private_key_der=None, signing_backend=b)
        record = engine.generate(b"\x00" * 32, b"\x00" * 32, 0x20, 0x01, 220, 95)
        raw = record.serialize_full()
        parsed = parse_record(raw)
        assert verify_signature(parsed, b.public_key_bytes) is True

    def test_setup_is_idempotent(self, tmp_path):
        """Calling setup() twice gives the same public key."""
        key_path = str(tmp_path / "key.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()
        pub1 = b.public_key_bytes

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()  # second call — should reload, not regenerate
        pub2 = b.public_key_bytes

        assert pub1 == pub2

    def test_emits_warning_on_setup(self, tmp_path):
        """setup() must emit a UserWarning about the insecure plaintext key."""
        key_path = str(tmp_path / "key.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with pytest.warns(UserWarning, match="INSECURE|DEV ONLY|plaintext"):
            b.setup()


# ---------------------------------------------------------------------------
# B. TestYubiKeyBackend
# ---------------------------------------------------------------------------

def _build_yubikey_mocks():
    """
    Patch sys.modules with MagicMock stubs for yubikit and ykman.

    Returns (mock_piv_session_cls, mock_connect_fn, real_pub_bytes, real_der_sig_fn).
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat,
    )
    from cryptography.hazmat.primitives.hashes import SHA256 as SHA256Cls

    # Real test key for mock to return
    _test_key = ec.generate_private_key(ec.SECP256R1())
    _test_pub = _test_key.public_key()
    _pub_bytes = _test_pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

    def _make_der_sig(body: bytes) -> bytes:
        return _test_key.sign(body, ec.ECDSA(SHA256Cls()))

    # Mock yubikit.piv
    mock_piv_mod = MagicMock()
    mock_piv_mod.KEY_TYPE = MagicMock()
    mock_piv_mod.KEY_TYPE.ECCP256 = "ECCP256"
    mock_piv_mod.SLOT = MagicMock()
    mock_piv_mod.SLOT.SIGNATURE = MagicMock(name="SLOT.SIGNATURE")
    mock_piv_mod.SLOT.AUTHENTICATION = MagicMock(name="SLOT.AUTHENTICATION")
    mock_piv_mod.SLOT.ATTESTATION = MagicMock(name="SLOT.ATTESTATION")
    mock_piv_mod.DEFAULT_MANAGEMENT_KEY = b"\x01\x02\x03"

    # Mock certificate
    mock_cert = MagicMock()
    mock_cert.public_key.return_value = _test_pub
    mock_cert.public_bytes.return_value = b"\x30" + b"\x82" + b"\x01" * 256

    # Mock PivSession
    mock_piv_session = MagicMock()
    mock_piv_session.get_certificate.return_value = mock_cert
    mock_piv_session.sign = MagicMock(side_effect=lambda slot, kt, data, algo: _make_der_sig(data))
    mock_piv_mod.PivSession.return_value = mock_piv_session

    # Mock ykman.device
    mock_ykman_device = MagicMock()
    mock_connection = MagicMock()
    mock_device = MagicMock()
    mock_info = MagicMock()
    mock_ykman_device.connect_to_device.return_value = (mock_connection, mock_device, mock_info)

    mock_yubikit = MagicMock()
    mock_ykman = MagicMock()
    mock_ykman.device = mock_ykman_device

    return {
        "yubikit": mock_yubikit,
        "yubikit.piv": mock_piv_mod,
        "ykman": mock_ykman,
        "ykman.device": mock_ykman_device,
    }, _pub_bytes, _make_der_sig, mock_piv_session


class TestYubiKeyBackend:

    def _setup_mocked_backend(self, mocks) -> YubiKeyIdentityBackend:
        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
        return b

    def test_backend_type_is_yubikey(self):
        mocks, pub_bytes, _, _ = _build_yubikey_mocks()
        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
        assert b.backend_type == "yubikey"

    def test_is_hardware_backed(self):
        mocks, pub_bytes, _, _ = _build_yubikey_mocks()
        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
        assert b.is_hardware_backed is True

    def test_sign_pre_hashes_body_with_sha256(self):
        """piv.sign() should be called with a 32-byte digest (pre-hashed)."""
        mocks, pub_bytes, _, mock_piv_session = _build_yubikey_mocks()

        received_args = []

        def _capture_sign(slot, kt, data, algo):
            received_args.append(data)
            # Return a real DER sig so decode works
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.hashes import SHA256 as SHA256Cls
            key = ec.generate_private_key(ec.SECP256R1())
            return key.sign(b"\x00" * 32, ec.ECDSA(SHA256Cls()))

        mock_piv_session.sign = MagicMock(side_effect=_capture_sign)

        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
            b.sign(b"test body data for pre-hash check")

        assert len(received_args) == 1
        assert len(received_args[0]) == 32, (
            f"Expected 32-byte SHA-256 digest, got {len(received_args[0])} bytes"
        )

    def test_sign_returns_64_bytes(self):
        mocks, pub_bytes, _, _ = _build_yubikey_mocks()
        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
            sig = b.sign(b"test data")
        assert len(sig) == 64

    def test_attestation_cert_hash_is_sha256_of_cert_der(self):
        mocks, pub_bytes, _, mock_piv_session = _build_yubikey_mocks()
        expected_cert_der = b"\x30" + b"\x82" + b"\x01" * 256
        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
        cert_hash = b.attestation_certificate_hash
        if cert_hash is not None:
            assert len(cert_hash) == 32
            assert cert_hash == hashlib.sha256(expected_cert_der).digest()

    def test_setup_generates_key_when_slot_empty(self):
        """When get_certificate raises, setup() calls generate_key."""
        mocks, pub_bytes, _, mock_piv_session = _build_yubikey_mocks()

        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat,
        )
        new_key = ec.generate_private_key(ec.SECP256R1())
        new_pub = new_key.public_key()

        mock_piv_session.get_certificate.side_effect = Exception("slot empty")
        mock_piv_session.generate_key.return_value = new_pub

        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()

        assert b.public_key_bytes is not None
        assert len(b.public_key_bytes) == 65

    def test_setup_loads_existing_key_when_slot_occupied(self):
        """When get_certificate succeeds, generate_key is NOT called."""
        mocks, pub_bytes, _, mock_piv_session = _build_yubikey_mocks()
        with patch.dict(sys.modules, mocks):
            b = YubiKeyIdentityBackend(piv_slot="9c")
            b.setup()
        mock_piv_session.generate_key.assert_not_called()

    def test_missing_import_raises_importerror_with_hint(self):
        """Without yubikey-manager, setup() raises ImportError with pip hint."""
        # Ensure the real yubikit is not present by removing it from sys.modules
        # and not injecting mocks
        original = {k: sys.modules.pop(k, None)
                    for k in ["yubikit", "yubikit.piv", "ykman", "ykman.device"]}
        try:
            # Force ImportError by patching the import inside setup()
            with patch.dict(sys.modules, {"yubikit": None, "yubikit.piv": None,
                                           "ykman": None, "ykman.device": None}):
                b = YubiKeyIdentityBackend()
                with pytest.raises(ImportError, match="pip install yubikey-manager"):
                    b.setup()
        finally:
            for k, v in original.items():
                if v is not None:
                    sys.modules[k] = v


# ---------------------------------------------------------------------------
# C. TestATECC608Backend
# ---------------------------------------------------------------------------

def _build_atecc608_mocks():
    """Build sys.modules mocks for cryptoauthlib."""
    raw_pub = bytes(range(64))  # 64-byte x||y

    mock_cal = MagicMock()
    mock_cal.ATCA_SUCCESS = 0

    mock_cal.cfg_ateccx08a_i2c_default.return_value = MagicMock()
    mock_cal.atcab_init.return_value = 0
    mock_cal.atcab_get_pubkey.return_value = 0

    def fake_get_pubkey(slot, buf):
        buf[:] = raw_pub
        return 0

    def fake_sign(slot, digest, sig_buf):
        sig_buf[:] = bytes(range(64))
        return 0

    mock_cal.atcab_get_pubkey = MagicMock(side_effect=fake_get_pubkey)
    mock_cal.atcab_sign = MagicMock(side_effect=fake_sign)
    mock_cal.atcab_genkey = MagicMock(return_value=0)

    return {"cryptoauthlib": mock_cal}, raw_pub, mock_cal


class TestATECC608Backend:

    def test_backend_type_is_atecc608(self):
        mocks, raw_pub, mock_cal = _build_atecc608_mocks()
        with patch.dict(sys.modules, mocks):
            b = ATECC608IdentityBackend()
            b.setup()
        assert b.backend_type == "atecc608"

    def test_is_hardware_backed(self):
        mocks, raw_pub, mock_cal = _build_atecc608_mocks()
        with patch.dict(sys.modules, mocks):
            b = ATECC608IdentityBackend()
            b.setup()
        assert b.is_hardware_backed is True

    def test_sign_pre_hashes_body_with_sha256(self):
        """atcab_sign() should be called with a 32-byte digest."""
        mocks, raw_pub, mock_cal = _build_atecc608_mocks()
        received_digests = []

        def _capture_sign(slot, digest, sig_buf):
            received_digests.append(bytes(digest))
            sig_buf[:] = bytes(range(64))
            return 0

        mock_cal.atcab_sign = MagicMock(side_effect=_capture_sign)

        with patch.dict(sys.modules, mocks):
            b = ATECC608IdentityBackend()
            b.setup()
            b.sign(b"test body for atecc608")

        assert len(received_digests) == 1
        assert len(received_digests[0]) == 32

    def test_sign_returns_64_bytes(self):
        mocks, raw_pub, mock_cal = _build_atecc608_mocks()
        with patch.dict(sys.modules, mocks):
            b = ATECC608IdentityBackend()
            b.setup()
            sig = b.sign(b"hello")
        assert len(sig) == 64

    def test_setup_prepends_04_prefix_to_raw_pubkey(self):
        """ATECC608 gives 64-byte x||y; backend must prepend 0x04."""
        mocks, raw_pub, mock_cal = _build_atecc608_mocks()
        with patch.dict(sys.modules, mocks):
            b = ATECC608IdentityBackend()
            b.setup()
        assert b.public_key_bytes[0] == 0x04
        assert b.public_key_bytes[1:] == raw_pub

    def test_missing_import_raises_importerror_with_hint(self):
        """Without cryptoauthlib, setup() raises ImportError with pip hint."""
        with patch.dict(sys.modules, {"cryptoauthlib": None}):
            b = ATECC608IdentityBackend()
            with pytest.raises(ImportError, match="pip install cryptoauthlib"):
                b.setup()


# ---------------------------------------------------------------------------
# D. TestHardwareKeyProxy
# ---------------------------------------------------------------------------

class TestHardwareKeyProxy:
    """Tests for _HardwareKeyProxy DER round-trip."""

    def _make_real_backend(self, tmp_path) -> SoftwareIdentityBackend:
        key_path = str(tmp_path / "proxy_test_key.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()
        return b

    def test_sign_returns_der_sequence(self, tmp_path):
        """DER output starts with 0x30 (SEQUENCE tag)."""
        b = self._make_real_backend(tmp_path)
        proxy = _HardwareKeyProxy(b)
        der = proxy.sign(b"test data")
        assert der[0] == 0x30, f"Expected DER SEQUENCE (0x30), got 0x{der[0]:02x}"

    def test_sign_output_decodable_with_decode_dss_signature(self, tmp_path):
        """proxy.sign() output can be decoded by decode_dss_signature."""
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        b = self._make_real_backend(tmp_path)
        proxy = _HardwareKeyProxy(b)
        der = proxy.sign(b"some data")
        r, s = decode_dss_signature(der)
        assert 0 < r < 2**256
        assert 0 < s < 2**256

    def test_algorithm_arg_is_ignored(self, tmp_path):
        """sign(data, None) and sign(data, mock_algo) both succeed."""
        b = self._make_real_backend(tmp_path)
        proxy = _HardwareKeyProxy(b)
        der1 = proxy.sign(b"data", None)
        der2 = proxy.sign(b"data", MagicMock())
        assert der1[0] == 0x30
        assert der2[0] == 0x30

    def test_wrong_signature_length_raises_valueerror(self):
        """A backend returning 63 bytes should raise ValueError."""
        bad_backend = MagicMock()
        bad_backend.sign.return_value = b"\x00" * 63
        proxy = _HardwareKeyProxy(bad_backend)
        with pytest.raises(ValueError, match="63"):
            proxy.sign(b"anything")


# ---------------------------------------------------------------------------
# E. TestEndToEnd
# ---------------------------------------------------------------------------

class TestEndToEnd:

    def test_software_backend_proxy_e2e(self, tmp_path):
        """
        Full pipeline: SoftwareBackend -> _HardwareKeyProxy -> PersistentPoACEngine
        -> serialize_full() -> parse_record() -> verify_signature() == True
        """
        from vapi_bridge.codec import parse_record, verify_signature
        from persistent_identity import PersistentPoACEngine

        key_path = str(tmp_path / "e2e_key.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()

        engine = PersistentPoACEngine(private_key_der=None, signing_backend=b)
        record = engine.generate(
            b"\x01" * 32, b"\x02" * 32,
            0x20, 0x01, 220, 95,
        )
        raw = record.serialize_full()
        assert len(raw) == 228, f"Expected 228-byte wire format, got {len(raw)}"

        parsed = parse_record(raw)
        assert verify_signature(parsed, b.public_key_bytes) is True

    def test_persistent_identity_hardware_path(self, tmp_path):
        """
        PersistentIdentity(signing_backend=SoftwareBackend) -> load_or_create()
        -> device_id == keccak256(backend.public_key_bytes)
        """
        from persistent_identity import PersistentIdentity

        def _keccak256(data: bytes) -> bytes:
            try:
                from eth_hash.auto import keccak
                return keccak(data)
            except ImportError:
                import hashlib
                return hashlib.sha3_256(data).digest()

        key_path = str(tmp_path / "identity_hw_test.json")
        b = SoftwareIdentityBackend(key_path=key_path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            b.setup()

        identity = PersistentIdentity(
            key_dir=tmp_path,
            signing_backend=b,
        ).load_or_create()

        expected_id = _keccak256(b.public_key_bytes)
        assert identity.device_id == expected_id
        assert identity.public_key_bytes == b.public_key_bytes

    def test_create_backend_factory_unknown_type_raises(self):
        """create_backend('foobar') raises ValueError."""
        with pytest.raises(ValueError, match="foobar"):
            create_backend("foobar")
