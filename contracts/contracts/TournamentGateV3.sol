// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./PHGRegistry.sol";
import "./PHGCredential.sol";

/**
 * @title TournamentGateV3
 * @notice Suspension-Aware PHG Tournament Eligibility (Phase 37)
 *
 * Adds PHGCredential.isActive() check to V2's cumulative + velocity gates.
 * A device with a suspended PHGCredential cannot enter a tournament even if
 * its PHGRegistry score and velocity meet the minimum thresholds.
 *
 * Invariant: TournamentGateV1 and TournamentGateV2 remain deployed and
 * functional — V3 is additive (follows V1→V2→V3 deployment pattern).
 */
contract TournamentGateV3 {

    PHGRegistry   public immutable phgRegistry;
    PHGCredential public immutable phgCredential;
    uint256       public immutable minCumulative;
    uint256       public immutable minVelocity;
    uint256       public immutable velocityWindow;

    // -------------------------------------------------------------------------
    // Errors
    // -------------------------------------------------------------------------

    error InsufficientHumanityScore(bytes32 deviceId, uint256 have, uint256 need);
    error InsufficientRecentVelocity(bytes32 deviceId, uint256 have, uint256 need);
    error CredentialSuspended(bytes32 deviceId);

    // -------------------------------------------------------------------------
    // Constructor
    // -------------------------------------------------------------------------

    constructor(
        address _phgRegistry,
        address _phgCredential,
        uint256 _minCumulative,
        uint256 _minVelocity,
        uint256 _velocityWindow
    ) {
        require(_phgRegistry   != address(0), "TournamentGateV3: zero registry");
        require(_phgCredential != address(0), "TournamentGateV3: zero credential");
        require(_velocityWindow > 0,           "TournamentGateV3: zero window");
        phgRegistry   = PHGRegistry(_phgRegistry);
        phgCredential = PHGCredential(_phgCredential);
        minCumulative  = _minCumulative;
        minVelocity    = _minVelocity;
        velocityWindow = _velocityWindow;
    }

    // -------------------------------------------------------------------------
    // Eligibility
    // -------------------------------------------------------------------------

    /**
     * @notice Assert that a device is eligible for tournament participation.
     *         Reverts with a descriptive error if any gate fails.
     * @param deviceId  32-byte device identifier (keccak256 of pubkey)
     *
     * Gate order (cheap-to-expensive):
     *   1. PHGRegistry cumulative score >= minCumulative
     *   2. PHGRegistry recent velocity >= minVelocity within velocityWindow
     *   3. PHGCredential.isActive(deviceId) — not minted OR suspended → blocked
     */
    function assertEligible(bytes32 deviceId) external view {
        uint256 cumul = phgRegistry.cumulativeScore(deviceId);
        if (cumul < minCumulative)
            revert InsufficientHumanityScore(deviceId, cumul, minCumulative);

        uint256 velocity = phgRegistry.getRecentVelocity(deviceId, velocityWindow);
        if (velocity < minVelocity)
            revert InsufficientRecentVelocity(deviceId, velocity, minVelocity);

        if (!phgCredential.isActive(deviceId))
            revert CredentialSuspended(deviceId);
    }

    /**
     * @notice Boolean eligibility check (no revert). Returns false on any gate failure.
     * @param deviceId  32-byte device identifier
     */
    function isEligible(bytes32 deviceId) external view returns (bool) {
        if (phgRegistry.cumulativeScore(deviceId) < minCumulative)          return false;
        if (phgRegistry.getRecentVelocity(deviceId, velocityWindow) < minVelocity) return false;
        return phgCredential.isActive(deviceId);
    }
}
