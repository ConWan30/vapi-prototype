// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./PHGRegistry.sol";

/**
 * @title IdentityContinuityRegistry — Biometric-Anchored Session Continuity
 * @notice Allows a player to transfer their PHG score from an old deviceId to a new one,
 *         proven via a biometric fingerprint proximity proof computed off-chain by the bridge.
 *
 * The proof is SHA-256(old_fp_hash || new_fp_hash || distance_bytes), where:
 *   - old_fp_hash / new_fp_hash are SHA-256 of the JSON-serialised mean feature vectors
 *   - distance_bytes is the big-endian float64 Mahalanobis distance between them
 *
 * Each device may only be a continuity source once and a destination once (anti-replay).
 * Score is zeroed from the old device and added to the new device via PHGRegistry.inheritScore().
 *
 * Phase 23 of the VAPI Protocol — "The Score Follows the Body".
 */
contract IdentityContinuityRegistry {

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    /// @notice Address authorized to attest continuity (the VAPI bridge).
    address public immutable bridge;

    /// @notice The PHGRegistry contract that holds cumulative scores.
    PHGRegistry public immutable phgRegistry;

    /// @notice Maps newDeviceId -> oldDeviceId (the device whose score was inherited).
    mapping(bytes32 => bytes32) public continuedFrom;

    /// @notice Anti-replay: tracks claimed device IDs (both source and destination).
    mapping(bytes32 => bool) public claimed;

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    /**
     * @notice Emitted when a continuity attestation is committed.
     * @param oldDeviceId        Source device whose score is migrated.
     * @param newDeviceId        Destination device that inherits the score.
     * @param biometricProofHash SHA-256(old_fp_hash || new_fp_hash || distance_bytes).
     * @param scoreMigrated      The score transferred (cumulativeScore of oldDeviceId).
     * @param blockNumber        Block at which the attestation was committed.
     */
    event ContinuityAttested(
        bytes32 indexed oldDeviceId,
        bytes32 indexed newDeviceId,
        bytes32 biometricProofHash,
        uint256 scoreMigrated,
        uint256 blockNumber
    );

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error OnlyBridge();
    error AlreadyClaimed(bytes32 deviceId);
    error SourceAlreadyClaimed(bytes32 oldDeviceId);

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /**
     * @param _bridge      Address of the VAPI bridge wallet.
     * @param _phgRegistry Address of the PHGRegistry contract.
     */
    constructor(address _bridge, address _phgRegistry) {
        require(_bridge != address(0), "ICR: zero bridge address");
        require(_phgRegistry != address(0), "ICR: zero registry address");
        bridge = _bridge;
        phgRegistry = PHGRegistry(_phgRegistry);
    }

    // -----------------------------------------------------------------------
    // Modifiers
    // -----------------------------------------------------------------------

    modifier onlyBridge() {
        if (msg.sender != bridge) revert OnlyBridge();
        _;
    }

    // -----------------------------------------------------------------------
    // Write — bridge only
    // -----------------------------------------------------------------------

    /**
     * @notice Attest that newDeviceId is the biometric continuation of oldDeviceId.
     *
     * Transfers cumulativeScore from old -> new via PHGRegistry.inheritScore().
     * Both devices are locked against future claims (anti-replay).
     *
     * @param oldDeviceId        Source device identifier.
     * @param newDeviceId        Destination device identifier.
     * @param biometricProofHash SHA-256(old_fp_hash || new_fp_hash || distance_bytes).
     */
    function attestContinuity(
        bytes32 oldDeviceId,
        bytes32 newDeviceId,
        bytes32 biometricProofHash
    ) external onlyBridge {
        if (claimed[newDeviceId]) revert AlreadyClaimed(newDeviceId);
        if (claimed[oldDeviceId]) revert SourceAlreadyClaimed(oldDeviceId);

        // Lock both devices against future claims
        claimed[newDeviceId] = true;
        claimed[oldDeviceId] = true;

        // Record continuity linkage
        continuedFrom[newDeviceId] = oldDeviceId;

        // Read score before transfer (for event)
        uint256 scoreMigrated = phgRegistry.cumulativeScore(oldDeviceId);

        // Transfer score on-chain
        phgRegistry.inheritScore(oldDeviceId, newDeviceId);

        emit ContinuityAttested(
            oldDeviceId,
            newDeviceId,
            biometricProofHash,
            scoreMigrated,
            block.number
        );
    }

    // -----------------------------------------------------------------------
    // View
    // -----------------------------------------------------------------------

    /**
     * @notice Returns true if newId inherited its score from oldId.
     */
    function isContinuationOf(bytes32 newId, bytes32 oldId) external view returns (bool) {
        return continuedFrom[newId] == oldId;
    }

    /**
     * @notice Walks the continuity chain to find the canonical root device.
     *         Gas-bounded to 8 hops.
     */
    function getCanonicalRoot(bytes32 deviceId) external view returns (bytes32 root) {
        root = deviceId;
        for (uint i = 0; i < 8; i++) {
            bytes32 prev = continuedFrom[root];
            if (prev == bytes32(0)) break;
            root = prev;
        }
    }
}
