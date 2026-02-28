# Hardware Signing Guide — Phase 9

VAPI Phase 9 moves ECDSA-P256 signing from an unprotected JSON key file into
a hardware Secure Element (SE). This guide covers both supported backends and
how to transition a running deployment.

---

## 1. Why Hardware Signing?

### The Root Trust Gap

Before Phase 9, the signing key lived in:

```
~/.vapi/dualshock_device_key.json
  { "private_der_hex": "308187020100..." }
```

Any process with read access to that file could extract the private key and
forge an arbitrarily clean PoAC chain indistinguishable from a legitimate
device. The phrase "hardware-rooted attestation" was aspirational.

### What Phase 9 Provides

| Property | Software (pre-Phase 9) | YubiKey PIV | ATECC608A |
|---|---|---|---|
| Key extraction by OS process | Yes | No | No |
| Key survives device loss | Yes (backup) | No (PIN-protected) | No (locked) |
| Manufacturer attestation cert | No | Yes (PIV attest) | No (Phase 10) |
| On-chain cert hash storage | No | Yes (Phase 9) | Phase 10 |
| Wire format changes | — | None | None |
| Existing tests affected | — | None | None |

The 228-byte PoAC wire format is **unchanged**. `_HardwareKeyProxy` duck-types
`EllipticCurvePrivateKey.sign()` so `PoACEngine.generate()` is called through
to hardware transparently.

---

## 2. Backend Comparison

| Feature | Software | YubiKey 5 | ATECC608A |
|---|---|---|---|
| Cost | Free | ~$50 USD | ~$1-3 USD |
| Setup complexity | Trivial | Low | Medium (I2C wiring) |
| Key storage | Plaintext JSON | PIV slot, PIN-protected | Locked OTP slot |
| Sign speed | ~1 ms | ~80-120 ms | ~20-40 ms |
| Attestation cert | None | PIV attestation | None (Phase 10) |
| Production ready | **No** | Yes | Yes |
| Platform | Any | USB-A/C | I2C (embedded/RPi) |

**Recommendation:** Use YubiKey for laptop/server deployments. Use ATECC608A
when embedding into an IoT device alongside the Pebble Tracker.

---

## 3. Option A: YubiKey 5 PIV

### Prerequisites

```bash
pip install yubikey-manager
# On Linux, also:
sudo apt install pcscd libpcsclite-dev
sudo systemctl enable --now pcscd
```

### Wiring / Connection

Plug the YubiKey into any USB port. No additional wiring required.

### Step-by-Step Setup

**Step 1 — Verify YubiKey is detected:**
```bash
ykman list
# Expected: YubiKey 5 Series (5.x.x) [OTP+FIDO+CCID] Serial: XXXXXXXX
```

**Step 2 — Set environment variables:**
```bash
export IDENTITY_BACKEND=yubikey
export YUBIKEY_PIV_SLOT=9c          # Slot 9c = Digital Signature (default)
# Optional — override PIV management key if you changed the default:
# export YUBIKEY_MANAGEMENT_KEY=<hex>
```

**Step 3 — First run (key generation):**

On first run, `setup()` will:
1. Connect to the YubiKey over USB
2. Detect that PIV slot 9c is empty
3. Authenticate with the management key (default or provided)
4. Generate an ECDSA-P256 key in the slot — **the private key never leaves the device**
5. Read the public key and store it for device identity
6. Attempt to read the attestation certificate from the attestation slot

**Step 4 — Register on-chain:**

If `DEVICE_REGISTRATION_TIER=Attested` and the YubiKey produced an attestation
cert, `registerAttestedWithCert()` is called automatically with the SHA-256 of
the attestation cert DER stored on-chain.

**Step 5 — Verify:**
```bash
python -c "
from vapi_bridge.hardware_identity import create_backend
b = create_backend('yubikey', piv_slot='9c')
b.setup()
print('backend_type:', b.backend_type)
print('is_hardware_backed:', b.is_hardware_backed)
print('pubkey len:', len(b.public_key_bytes))
print('cert_hash:', b.attestation_certificate_hash.hex() if b.attestation_certificate_hash else None)
sig = b.sign(b'test')
print('sig len:', len(sig), '(expect 64)')
"
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `pyscard.System.readers()` returns empty | Enable pcscd: `sudo systemctl start pcscd` |
| `Authentication failed` | Reset management key: `ykman piv access change-management-key` |
| Slot already has an EC384 key | Switch to slot 9a or factory reset: `ykman piv reset` |
| `ImportError: No module named yubikit` | `pip install yubikey-manager` |
| YubiKey not found in VM | Pass USB device through to the VM |

---

## 4. Option B: ATECC608A I2C

### Prerequisites

```bash
pip install cryptoauthlib
```

**Hardware needed:**
- Microchip ATECC608A-MAHDA-T or compatible breakout
- I2C connection to host (Raspberry Pi GPIO, or USB-I2C bridge like MCP2221)

### Wiring (Raspberry Pi)

```
ATECC608A Pin    RPi GPIO Header
─────────────    ───────────────
VCC  (pin 8)  -> 3.3V  (pin 1)
GND  (pin 4)  -> GND   (pin 6)
SDA  (pin 5)  -> GPIO2 (pin 3, I2C1 SDA)
SCL  (pin 6)  -> GPIO3 (pin 5, I2C1 SCL)
```

Add 4.7kΩ pull-up resistors to SDA and SCL if not on the breakout board.

Default I2C address: **0x60** (can be changed by OTP programming).

### Step-by-Step Setup

**Step 1 — Enable I2C on Raspberry Pi:**
```bash
sudo raspi-config   # Interface Options > I2C > Enable
sudo reboot
```

**Step 2 — Verify the chip is detected:**
```bash
sudo i2cdetect -y 1
# Should show 0x60 in the address grid
```

**Step 3 — Set environment variables:**
```bash
export IDENTITY_BACKEND=atecc608
export ATECC608_I2C_BUS=1           # I2C bus number (default: 1)
# I2C address defaults to 0x60
```

**Step 4 — First run (key generation):**

On first run, `setup()` will:
1. Init the cryptoauthlib HAL with the I2C bus and address
2. Attempt `atcab_get_pubkey(slot=0)` to read an existing key
3. If slot 0 is empty or not locked, call `atcab_genkey(0)` to generate a new key
4. The private key is permanently stored in the chip's secure slot — it cannot be extracted

**Step 5 — Verify:**
```bash
python -c "
from vapi_bridge.hardware_identity import create_backend
b = create_backend('atecc608', i2c_bus=1, i2c_address=0x60)
b.setup()
print('backend_type:', b.backend_type)
print('is_hardware_backed:', b.is_hardware_backed)
print('pubkey len:', len(b.public_key_bytes))
sig = b.sign(b'test')
print('sig len:', len(sig), '(expect 64)')
"
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `atcab_init` returns non-zero | Check wiring and pull-ups; verify I2C address with `i2cdetect` |
| `atcab_genkey` returns non-zero | Slot 0 may be locked from a prior configuration; use slot 1 |
| `ImportError: No module named cryptoauthlib` | `pip install cryptoauthlib` |
| Permission denied on `/dev/i2c-1` | `sudo usermod -aG i2c $USER` then re-login |

---

## 5. Verifying Your Backend (Smoke Test)

Run the full end-to-end smoke test to confirm the signing chain is intact:

```bash
cd bridge
python -c "
import sys; sys.path.insert(0, '../controller')
from persistent_identity import _HardwareKeyProxy, PersistentPoACEngine
from vapi_bridge.hardware_identity import SoftwareIdentityBackend
from vapi_bridge.codec import parse_record, verify_signature

b = SoftwareIdentityBackend('/tmp/smoke_test_key.json')
b.setup()
eng = PersistentPoACEngine(private_key_der=None, signing_backend=b)
rec = eng.generate(b'\x00'*32, b'\x00'*32, 0x20, 0x01, 220, 95)
raw = rec.serialize_full()
result = verify_signature(parse_record(raw), b.public_key_bytes)
print('verify:', result)
assert result, 'FAIL: signature did not verify'
print('Smoke test PASSED — 228-byte wire format intact')
"
```

Replace `SoftwareIdentityBackend` with `YubiKeyIdentityBackend` or
`ATECC608IdentityBackend` (and call `b.setup()`) to smoke-test hardware.

---

## 6. Registering as Attested Tier

Set these environment variables before starting the bridge:

```bash
# For YubiKey:
export IDENTITY_BACKEND=yubikey
export YUBIKEY_PIV_SLOT=9c
export DEVICE_REGISTRATION_TIER=Attested
export ATTESTATION_PROOF_HEX=<64-byte hex proof>   # provided by ioID issuer

# For ATECC608A:
export IDENTITY_BACKEND=atecc608
export ATECC608_I2C_BUS=1
export DEVICE_REGISTRATION_TIER=Attested
export ATTESTATION_PROOF_HEX=<64-byte hex proof>
```

When `DEVICE_REGISTRATION_TIER=Attested`:

1. The bridge calls `ensure_device_registered_tiered()` with `tier="Attested"`
2. If the backend returns a non-None `attestation_certificate_hash`, the bridge
   calls `registerAttestedWithCert()` (Phase 9) instead of `registerAttested()`
3. The SHA-256 of the hardware attestation cert is stored on-chain in
   `TieredDeviceRegistry.attestationCertificateHashes[deviceId]`
4. Phase 10's `attestationEnforced=true` path will verify this cert hash against
   the approved manufacturer registry

**On-chain result:**
```
attestationCertificateHashes[keccak256(pubkey)] = sha256(attest_cert_der)
```

This is verifiable by any party:

```bash
cast call $REGISTRY_ADDR \
  "attestationCertificateHashes(bytes32)(bytes32)" \
  $(cast keccak <pubkey_hex>)
```

---

## 7. Transitioning to `attestationEnforced=true` (Phase 10)

Phase 10 will enforce full ioID P256 certificate chain verification via the
IoTeX precompile at `0x0100`. Rollout checklist:

- [ ] All production devices registered with `registerAttestedWithCert()` and cert hashes stored on-chain
- [ ] `approvedManufacturers` mapping populated with known manufacturer addresses (owner-only `setApprovedManufacturer()`)
- [ ] `_validateAttestation()` updated to call IoTeX precompile with `manufacturer_key` from `approvedManufacturers`
- [ ] Testnet dry-run: set `attestationEnforced=true` temporarily and register a test device
- [ ] Coordinate with IoTeX team to confirm precompile 0x0100 availability on mainnet
- [ ] Set `attestationEnforced=true` on mainnet via owner transaction
- [ ] Monitor logs for `AttestationValidatorNotImplemented` errors (should be zero)

```solidity
// Owner-only, irreversible in practice:
registry.setAttestationEnforced(true);
```

---

## 8. Emergency Recovery / Revert to Software Mode

If a hardware backend fails (YubiKey lost, ATECC608A damaged):

**Step 1 — Revert to software mode immediately:**
```bash
export IDENTITY_BACKEND=software
# Restart the bridge — it will fall back to ~/.vapi/dualshock_device_key.json
```

**Step 2 — Note: the device_id will change.**

The device identity (`keccak256(pubkey)`) is tied to the hardware key. If the
hardware is lost, the old device_id is permanently retired. You must:

1. Register a new device with a new pubkey at whatever tier is appropriate
2. Update any bounty subscriptions or team memberships with the new device_id
3. The old on-chain records remain valid (they are immutable) but no new records
   can be submitted under the old device_id without the private key

**Step 3 — YubiKey PIN-locked recovery:**

If the YubiKey PIN is locked (3 wrong PINs), use the PUK:
```bash
ykman piv access unblock-pin --puk <PUK> --new-pin <PIN>
```

If both PIN and PUK are locked:
```bash
ykman piv reset   # WARNING: destroys all PIV keys on the device
```
After reset, re-run the bridge to generate a new key and re-register.

**Step 4 — Bridge software fallback path:**

The `_init_hardware()` function wraps backend init in a try/except. If the
hardware backend fails for any reason, it automatically falls back to
`SoftwareIdentityBackend` and logs a warning:

```
WARNING: Hardware backend init failed (...) — software fallback
```

This keeps the bridge running but reverts to the pre-Phase-9 trust level.
Monitor logs and replace the hardware as soon as possible.
