// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./PHGRegistry.sol";

/**
 * @title TournamentGate — PHG-gated eligibility enforcement
 * @notice Reads PHGRegistry.isEligible() on-chain before allowing actions.
 *
 * Integrate by calling assertEligible(deviceId) at the top of any function
 * that should be gated behind a proven humanity threshold.
 *
 * Example usage in BountyMarket:
 *   TournamentGate gate = TournamentGate(tournamentGateAddress);
 *   gate.assertEligible(deviceId);  // reverts if below minPHGScore
 */
contract TournamentGate {

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    PHGRegistry public immutable phgRegistry;
    uint256     public immutable minPHGScore;

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    /**
     * @notice Reverts when a device's cumulative PHG score is below the minimum.
     * @param deviceId The device that failed the eligibility check.
     * @param have     The device's actual PHG score.
     * @param need     The minimum required PHG score.
     */
    error InsufficientHumanityScore(bytes32 deviceId, uint256 have, uint256 need);

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /**
     * @param _phgRegistry Address of the deployed PHGRegistry.
     * @param _minPHGScore Minimum cumulative PHG score required to pass.
     */
    constructor(address _phgRegistry, uint256 _minPHGScore) {
        require(_phgRegistry != address(0), "TournamentGate: zero registry address");
        phgRegistry = PHGRegistry(_phgRegistry);
        minPHGScore = _minPHGScore;
    }

    // -----------------------------------------------------------------------
    // View
    // -----------------------------------------------------------------------

    /**
     * @notice Asserts that a device meets the minimum PHG score.
     * @dev    Reverts with InsufficientHumanityScore if not eligible.
     *         Call from any function that requires proven humanity.
     * @param deviceId The 32-byte device identifier to check.
     */
    function assertEligible(bytes32 deviceId) external view {
        uint256 score = phgRegistry.cumulativeScore(deviceId);
        if (score < minPHGScore) {
            revert InsufficientHumanityScore(deviceId, score, minPHGScore);
        }
    }

    /**
     * @notice Returns whether a device passes the eligibility gate.
     * @param deviceId The 32-byte device identifier to check.
     */
    function isEligible(bytes32 deviceId) external view returns (bool) {
        return phgRegistry.isEligible(deviceId, minPHGScore);
    }
}
