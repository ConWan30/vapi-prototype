"""
VAPI Phase 19 — PHCI Certification Engine

PHCICertifier evaluates a DeviceProfile against the Physical Human Controller
Input (PHCI) certification requirements and returns a PHCICertification result.

Two tiers of certification:

    PHCI_CERTIFIED (PHCITier.CERTIFIED)
        Requires: adaptive triggers, gyro, accel, L2+L3+L4+L5 PITL layers,
        schema_version >= 2.  Full Sybil resistance stack.

    PHCI_STANDARD (PHCITier.STANDARD)
        Requires: gyro, L2+L3 PITL layers, schema_version >= 2.
        Basic digital presence proof without biometric layer.

    PHCI_NONE (PHCITier.NONE)
        No adaptive triggers, no sticks or IMU. Only HID-XInput oracle (L2)
        is available. On-chain attestation still provided but without biometric
        signal. Not counted as "certified".

Usage:
    from phci_certification import PHCICertifier
    certifier = PHCICertifier()
    result = certifier.certify(profile)
    print(result.phci_tier.name, result.score, result.is_certified)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent))

from device_profile import DeviceProfile, PHCITier


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PHCICertification:
    """
    Result of a PHCICertifier.certify() call.

    Attributes
    ----------
    profile_id : str
        The profile_id of the evaluated DeviceProfile.

    phci_tier : PHCITier
        The tier this profile qualifies for based on passed checks.

    passed_checks : list[str]
        Human-readable names of checks that passed.

    failed_checks : list[str]
        Human-readable names of checks that failed.

    score : int
        Integer score from 0–100. 100 = all CERTIFIED checks pass.
        Score is proportional to passed_checks / all_checks_evaluated.

    is_certified : bool
        True if phci_tier is STANDARD or CERTIFIED (i.e., some form of
        PHCI certification). False for PHCI_NONE.
    """
    profile_id:    str
    phci_tier:     PHCITier
    passed_checks: List[str]
    failed_checks: List[str]
    score:         int
    is_certified:  bool


# ---------------------------------------------------------------------------
# Certifier
# ---------------------------------------------------------------------------

class PHCICertifier:
    """
    Validates a DeviceProfile against PHCI certification requirements.

    Two sets of requirements are evaluated:
        CERTIFIED_REQUIREMENTS — all must pass for PHCI_CERTIFIED
        STANDARD_REQUIREMENTS  — all must pass for PHCI_STANDARD

    The certify() method determines the highest tier the profile achieves
    and returns a PHCICertification with the full check breakdown.
    """

    # All checks needed for PHCI_CERTIFIED
    CERTIFIED_REQUIREMENTS: tuple = (
        "has_adaptive_triggers",
        "has_gyroscope",
        "has_accelerometer",
        "pitl_l2_present",
        "pitl_l3_present",
        "pitl_l4_present",   # Layer 4 biometric (requires adaptive triggers)
        "pitl_l5_present",   # Layer 5 temporal (requires adaptive triggers)
        "schema_version_2",
    )

    # Subset needed for PHCI_STANDARD
    STANDARD_REQUIREMENTS: tuple = (
        "has_gyroscope",
        "pitl_l2_present",
        "pitl_l3_present",
        "schema_version_2",
    )

    def _run_check(self, check: str, profile: DeviceProfile) -> bool:
        """Return True if the named check passes for the given profile."""
        if check == "has_adaptive_triggers":
            return profile.has_adaptive_triggers
        if check == "has_gyroscope":
            return profile.has_gyroscope
        if check == "has_accelerometer":
            return profile.has_accelerometer
        if check == "has_touchpad":
            return profile.has_touchpad
        if check == "pitl_l2_present":
            return 2 in profile.pitl_layers
        if check == "pitl_l3_present":
            return 3 in profile.pitl_layers
        if check == "pitl_l4_present":
            return 4 in profile.pitl_layers
        if check == "pitl_l5_present":
            return 5 in profile.pitl_layers
        if check == "schema_version_2":
            return profile.schema_version >= 2
        # Unknown check — conservative fail
        return False

    def certify(self, profile: DeviceProfile) -> PHCICertification:
        """
        Evaluate a DeviceProfile and return a PHCICertification result.

        Logic:
            1. Run all CERTIFIED_REQUIREMENTS checks.
            2. If all pass → PHCI_CERTIFIED.
            3. Else run STANDARD_REQUIREMENTS checks.
            4. If all pass → PHCI_STANDARD.
            5. Else → PHCI_NONE.

        Score = (passed_certified_checks / len(CERTIFIED_REQUIREMENTS)) * 100,
        rounded to int.
        """
        # Evaluate all CERTIFIED checks
        certified_passed:  list[str] = []
        certified_failed:  list[str] = []
        for chk in self.CERTIFIED_REQUIREMENTS:
            if self._run_check(chk, profile):
                certified_passed.append(chk)
            else:
                certified_failed.append(chk)

        score = round(len(certified_passed) / len(self.CERTIFIED_REQUIREMENTS) * 100)

        if not certified_failed:
            # All CERTIFIED checks pass
            return PHCICertification(
                profile_id=profile.profile_id,
                phci_tier=PHCITier.CERTIFIED,
                passed_checks=certified_passed,
                failed_checks=[],
                score=score,
                is_certified=True,
            )

        # Check if STANDARD subset passes
        standard_passed: list[str] = []
        standard_failed: list[str] = []
        for chk in self.STANDARD_REQUIREMENTS:
            if self._run_check(chk, profile):
                standard_passed.append(chk)
            else:
                standard_failed.append(chk)

        if not standard_failed:
            return PHCICertification(
                profile_id=profile.profile_id,
                phci_tier=PHCITier.STANDARD,
                passed_checks=certified_passed,   # include all that passed from certified set
                failed_checks=certified_failed,
                score=score,
                is_certified=True,
            )

        # Neither tier achieved
        return PHCICertification(
            profile_id=profile.profile_id,
            phci_tier=PHCITier.NONE,
            passed_checks=certified_passed,
            failed_checks=certified_failed,
            score=score,
            is_certified=False,
        )
