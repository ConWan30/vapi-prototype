# VAPI Attestation Enforcement Guide

## Phase 10: Full Manufacturer P256 Verification

---

## 1. Overview

| Mode | `attestationEnforced` | Behaviour |
|---|---|---|
| Testnet (default) | `false` | Any 64-byte proof accepted — no crypto verification |
| Mainnet | `true` | Proof must be a valid ECDSA-P256 signature from a registered manufacturer key |

The V2 registration functions (`registerAttestedV2`, `registerAttestedWithCertV2`) call
`_validateAttestationV2()` which invokes the IoTeX P256 precompile at `0x0100`.
The old functions (`registerAttested`, `registerAttestedWithCert`) are unchanged and still
call `_validateAttestation()`, which reverts with `AttestationValidatorNotImplemented` when
`attestationEnforced=true`. Use V2 functions for mainnet Attested-tier registrations.

---

## 2. ManufacturerKey Setup

Before any V2 Attested registration can succeed with enforcement enabled, the contract owner
must register the manufacturer's P256 public key:

```solidity
// On-chain transaction (owner only)
registry.setManufacturerKey(
    manufacturerAddress,   // Ethereum address used as registry key
    pubkeyX,               // bytes32 — raw P256 x-coordinate
    pubkeyY,               // bytes32 — raw P256 y-coordinate
    "Yubico Inc"           // Human-readable label
);
```

Via bridge (Python):
```python
await chain._registry.functions.setManufacturerKey(
    manufacturer_address, pubkey_x_bytes32, pubkey_y_bytes32, "Yubico Inc"
).build_transaction(...)
```

---

## 3. Yubico PIV Attestation Flow

Yubico PIV generates an X.509 attestation certificate for each key slot.

**Step 1: Generate attestation certificate**
```bash
yubico-piv-tool -a attest -s 9a -o attestation.der
```

**Step 2: Extract public key and signature from attestation cert**
```bash
# Convert DER to PEM for inspection
openssl x509 -in attestation.der -inform DER -noout -text

# Extract P256 public key coordinates (65-byte uncompressed point)
openssl x509 -in attestation.der -inform DER -noout -pubkey | \
  openssl ec -pubin -text -noout 2>/dev/null | grep -A 4 "pub:"
```

**Step 3: Parse coordinates into bytes32**
```python
import subprocess, hashlib

# Run openssl to get the raw pub key bytes
result = subprocess.run(
    ["openssl", "x509", "-in", "attestation.der", "-inform", "DER",
     "-noout", "-pubkey"],
    capture_output=True
)
# Parse the 65-byte uncompressed point (0x04 || x || y)
pub_bytes = extract_ec_pubkey_bytes(result.stdout)   # implement per openssl output
pubkey_x = pub_bytes[1:33]    # bytes 1-32 = x
pubkey_y = pub_bytes[33:65]   # bytes 33-64 = y

# Register on-chain
pubkey_x_hex = "0x" + pubkey_x.hex()
pubkey_y_hex = "0x" + pubkey_y.hex()
```

**Step 4: Generate attestation proof (64-byte r||s)**

The attestation proof is an ECDSA-P256 signature over `keccak256(device_pubkey)` by the
manufacturer's signing key. For YubiKey PIV:
```python
import hashlib
from yubico_piv import PIVApplication   # or use PKCS11

msg_hash = hashlib.new("sha256", keccak256(device_pubkey)).digest()
# PIV sign takes pre-hashed digest
r, s = piv_sign_prehashed(slot="9a", digest=msg_hash)
proof = r.to_bytes(32, "big") + s.to_bytes(32, "big")   # 64 bytes
```

---

## 4. ATECC608A Attestation Flow

The ATECC608A uses the `atcacert` library for certificate-based attestation.

**Step 1: Read device attestation certificate**
```c
uint8_t cert_der[512];
size_t cert_len = sizeof(cert_der);
atcacert_read_cert(&g_cert_def, device_public_key, cert_der, &cert_len);
```

**Step 2: Extract P256 coordinates from certificate**
```python
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

cert = x509.load_der_x509_certificate(cert_der_bytes)
pub = cert.public_key()
assert isinstance(pub, EllipticCurvePublicKey)
pub_numbers = pub.public_key().public_numbers()
pubkey_x = pub_numbers.x.to_bytes(32, "big")
pubkey_y = pub_numbers.y.to_bytes(32, "big")
```

**Step 3: Sign device pubkey hash with ATECC608A**
```c
uint8_t digest[32];
uint8_t signature[64];   // raw r||s
atcab_sha(device_pubkey, 65, digest);
// keccak256 is not natively supported; compute off-chip and pass digest
atcab_sign(ATCA_SLOT_AUTH, digest, signature);
// signature is already 64-byte raw r||s format
```

---

## 5. Privacy and Consent

Attestation proves **hardware identity only** — it binds a device's P256 public key to a
verified manufacturer chain of trust. No personally identifiable information (PII) is
stored on-chain.

**Operator obligations before linking attestation to gameplay data:**
- Obtain informed consent from users before correlating hardware attestation identity with
  gameplay records, session history, or any PII.
- Document the data retention policy for attestation certificate hashes stored on-chain
  (these are permanent and cannot be deleted from the blockchain).
- Ensure compliance with applicable privacy regulations (GDPR, CCPA) before deploying
  `attestationEnforced=true` in production.

Hardware attestation links a physical device to an on-chain identity. The VAPI protocol
does not link device identity to human identity — this linkage, if any, is the
responsibility of the application layer.

---

## 6. Enabling Enforcement

**Warning**: Setting `attestationEnforced=true` is irreversible from a functional
standpoint — existing Attested-tier devices registered via old functions remain active,
but new `registerAttested` calls will revert. Thoroughly test on testnet first.

```solidity
// Only after verifying E2E flow on testnet
registry.setAttestationEnforced(true);
```

Via bridge:
```python
tx = await chain._registry.functions.setAttestationEnforced(True).build_transaction(...)
```

---

## 7. Mainnet Transition Checklist

| Step | Action | Status |
|---|---|---|
| 1 | Register manufacturer key on testnet (`setManufacturerKey`) | [ ] |
| 2 | Run full E2E with real YubiKey or ATECC608A on testnet | [ ] |
| 3 | Set `attestationEnforced=true` on testnet | [ ] |
| 4 | Update tier deposits to mainnet values (10/100/1 IOTX) | [ ] |
| 5 | Deploy `TieredDeviceRegistry` to mainnet | [ ] |
| 6 | Transfer contract ownership to Gnosis Safe 2-of-3 | [ ] |
| 7 | Call `setManufacturerKey` on mainnet registry | [ ] |
| 8 | Call `setAttestationEnforced(true)` on mainnet registry | [ ] |

---

## 8. Key Revocation

When a manufacturer's signing key is compromised, call:

```solidity
registry.revokeManufacturerKey(manufacturerAddress);
// emits ManufacturerKeyRevoked(manufacturerAddress)
```

**Effects of revocation:**
- Existing Attested-tier devices remain active — revocation does not affect already-registered devices.
- New `registerAttestedV2` / `registerAttestedWithCertV2` calls with the revoked manufacturer address will revert with `ManufacturerKeyInactive`.
- Old `registerAttested` / `registerAttestedWithCert` calls are unaffected by revocation (they do not check `manufacturerKeys`).

To register a replacement key, call `setManufacturerKey` with the new P256 coordinates —
this overwrites the revoked entry and sets `active=true` again.
