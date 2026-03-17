# Contributing to VAPI

VAPI is open source under Apache 2.0. Contributions are welcome across three tracks:
hardware profiles, detection research, and platform integration.

---

## Before You Start

All PRs must pass:
```bash
python -m pytest bridge/tests/ --ignore=bridge/tests/test_e2e_simulation.py -q  # must be 1000
python -m pytest sdk/tests/ -v                                                   # must be 28
cd contracts && npx hardhat test                                                  # must be 354
```

Do not submit a PR that reduces any of these counts.

---

## Track 1: Adding a Hardware Profile (Manufacturer)

Hardware profiles live in `controller/profiles/`. Each profile declares HID parameters,
ECDSA key generation method, polling rate, and PHCI tier (1–5).

**Steps:**
1. Read `docs/hardware-certification.md` for PHCI tier criteria and HID format requirements
2. Copy the closest existing profile (e.g. `controller/profiles/sony_dualshock_edge_v1.py`)
3. Fill in: `VID`, `PID`, `INTERFACE`, `POLLING_HZ`, `PHCI_TIER`, and the 12-field
   biometric feature defaults
4. Run the certification harness:
   ```bash
   python controller/phci_certification.py --profile controller/profiles/your_profile.py
   ```
   This validates HID report format, polling rate bounds, and ECDSA compatibility.
5. Add the profile to `controller/profiles/__init__.py`
6. Add at least 2 unit tests to `bridge/tests/` covering profile load and PHCI tier assertion
7. Open a PR with title: `feat(profile): add <controller name> (PHCI Tier N)`

**Reference profiles (6 certified):**
- `sony_dualshock_edge_v1.py` — primary VAPI reference device
- `sony_dualsense_v1.py`
- `xbox_elite_series2.py`
- `hori_fighting_commander.py`
- `scuf_reflex_pro.py`
- `battle_beaver_customs.py`

---

## Track 2: Reporting Detection Gaps

Detection gaps — scenarios where VAPI fails to flag cheat inputs — are high-value contributions.

**Report format (GitHub issue):**
```
## Gap Report

**Layer(s) affected:** L4 / L5 / L2B / L2C / etc.
**Attack class:** [e.g. threshold-aware macro, spectral mimicry, biometric transplant]
**Observed behavior:** [what VAPI returns vs what it should return]
**Reproduction:** [minimal script or session file]
**Affected inference code(s):** [0x28, 0x2B, 0x30, etc.]
```

**Known documented limitations (not bugs):**
- Inter-person separation ratio 0.362 — L4 is an intra-player anomaly detector, not an
  identity verifier. See `SECURITY.md` §Known Limitations.
- Biometric transplant attack: 0% detection. Documented in whitepaper §8.6.
- L2C phantom weight in NCAA CFB 26 (dead-zone stick): the L2C oracle returns None,
  resolving to a 0.5 neutral prior. The humanity formula is still valid at 4 effective signals.
- L6_CHALLENGES_ENABLED=false: the active trigger challenge layer is disabled by default.
  This is intentional, not a bug.

---

## Track 3: Calibration Contributions

If you have ≥10 VAPI sessions from a certified controller and want to contribute to
threshold calibration:

1. Capture sessions with `python scripts/capture_session.py --player <ID> --sessions 10`
2. Sessions land in `sessions/human/hw_<NNN>.json`
3. Run the calibrator:
   ```bash
   python scripts/threshold_calibrator.py sessions/human/hw_*.json
   ```
4. Document: old threshold → new threshold → N sessions → player count → controller model
5. Open a PR with the session files, calibration output, and updated `calibration_profile.json`

**Calibration invariants — never override:**
- Thresholds can only tighten (min() enforcement)
- Stable EMA track updates NOMINAL sessions only
- L4 anomaly threshold: 7.009 | L4 continuity: 5.367 (Phase 57, N=74)

---

## PR Conventions

- **Title format:** `feat(scope): description` / `fix(scope): description` / `docs(scope): description`
- **Branch:** `feat/<short-name>` or `fix/<issue-number>-description`
- **Commit messages:** concise, imperative mood
- **No whitepaper changes without test count updates** — if bridge tests change, update
  `docs/vapi-whitepaper-v3.md` §7.1 and abstract atomically
- **Wire format is frozen:** never modify the 228-byte PoAC layout, chain hash algorithm
  (SHA-256 of 164-byte body), or inference code assignments

---

## Development Setup

```bash
git clone https://github.com/ConWan30/vapi-prototype
cd vapi-pebble-prototype
pip install -r bridge/requirements.txt
pip install -e sdk/
cd contracts && npm install
```

Python 3.10+ required. Node 18+ required for Hardhat.
