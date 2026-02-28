// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./PHGRegistry.sol";

/**
 * @title TournamentGateV2 --- Velocity-Gated PHG Tournament Eligibility
 * @notice Enforces both a minimum cumulative PHG score AND a minimum recent velocity.
 *
 * A device must have:
 *   1. cumulativeScore >= minCumulative  --- sufficient lifetime humanity credential
 *   2. getRecentVelocity(window) >= minVelocity --- sufficient recent play quality
 *
 * This prevents a one-time farming session from granting indefinite tournament access.
 * Deploy alongside TournamentGateV1; both remain active.
 */
contract TournamentGateV2 {

    PHGRegistry public immutable phgRegistry;
    uint256 public immutable minCumulative;
    uint256 public immutable minVelocity;
    uint256 public immutable velocityWindow;

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error InsufficientHumanityScore(bytes32 deviceId, uint256 have, uint256 need);
    error InsufficientRecentVelocity(bytes32 deviceId, uint256 have, uint256 need);

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /**
     * @param _phgRegistry   Address of the deployed PHGRegistry.
     * @param _minCumulative Minimum cumulative PHG score required.
     * @param _minVelocity   Minimum recent velocity (sum of score deltas) required.
     * @param _velocityWindow Number of recent checkpoints to sum for velocity.
     */
    constructor(
        address _phgRegistry,
        uint256 _minCumulative,
        uint256 _minVelocity,
        uint256 _velocityWindow
    ) {
        require(_phgRegistry != address(0), "TournamentGateV2: zero registry");
        require(_velocityWindow > 0, "TournamentGateV2: zero window");
        phgRegistry   = PHGRegistry(_phgRegistry);
        minCumulative = _minCumulative;
        minVelocity   = _minVelocity;
        velocityWindow = _velocityWindow;
    }

    // -----------------------------------------------------------------------
    // Gate check
    // -----------------------------------------------------------------------

    /**
     * @notice Revert if device does not meet both cumulative AND velocity thresholds.
     * @param deviceId The 32-byte device identifier.
     */
    function assertEligible(bytes32 deviceId) external view {
        uint256 cumul = phgRegistry.cumulativeScore(deviceId);
        if (cumul < minCumulative)
            revert InsufficientHumanityScore(deviceId, cumul, minCumulative);

        uint256 velocity = phgRegistry.getRecentVelocity(deviceId, velocityWindow);
        if (velocity < minVelocity)
            revert InsufficientRecentVelocity(deviceId, velocity, minVelocity);
    }
}
