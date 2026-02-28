// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title PHGRegistry — Proof of Human Gaming On-Chain Registry
 * @notice Accumulates PHG Trust Scores and biometric fingerprint hashes on-chain.
 *
 * Every N verified NOMINAL records (configurable per bridge), the bridge calls
 * commitCheckpoint() to seal the current cumulative score and biometric hash.
 * Checkpoints are linked via prevCheckpointHash — forming an immutable sub-ledger
 * of humanity accumulation anchored to the canonical PoAC chain.
 *
 * TournamentGate.sol reads isEligible() to enforce PHG-gated bounty eligibility.
 */
contract PHGRegistry {

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    /// @notice Address authorized to commit checkpoints (the VAPI bridge).
    address public immutable bridge;

    /// @notice Address of the IdentityContinuityRegistry (set once via setIdentityRegistry).
    address public identityRegistry;

    /// @notice Cumulative PHG Trust Score per device.
    mapping(bytes32 => uint256) public cumulativeScore;

    /// @notice Hash of the most recent checkpoint committed for each device.
    mapping(bytes32 => bytes32) public checkpointHead;

    /// @notice Number of verified NOMINAL records committed for each device.
    mapping(bytes32 => uint32) public recordCount;

    /// @notice scoreDelta stored at each checkpoint hash (for velocity lookup).
    mapping(bytes32 => uint256) public scoreDeltaAt;

    /// @notice Previous checkpoint hash at each checkpoint hash (linked list).
    mapping(bytes32 => bytes32) public prevCheckpointAt;

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    /**
     * @notice Emitted each time the bridge commits a PHG checkpoint.
     * @param deviceId          Keccak256 of the device public key (32 bytes).
     * @param cumulativeScore   Total PHG score after this checkpoint.
     * @param recordCount       Total verified NOMINAL records after this checkpoint.
     * @param biometricHash     SHA-256 of the averaged L4 biometric feature vector JSON.
     * @param prevCheckpointHash Hash of the previous checkpoint (0x0 for the first).
     * @param blockNumber       Block at which this checkpoint was committed.
     */
    event PHGCheckpointCommitted(
        bytes32 indexed deviceId,
        uint256 cumulativeScore,
        uint32  recordCount,
        bytes32 biometricHash,
        bytes32 prevCheckpointHash,
        uint256 blockNumber
    );

    // -----------------------------------------------------------------------
    // Errors
    // -----------------------------------------------------------------------

    error OnlyBridge();
    error NotIdentityRegistry();
    error IdentityRegistryAlreadySet();

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    /**
     * @param _bridge Address of the VAPI bridge wallet that may commit checkpoints.
     */
    constructor(address _bridge) {
        require(_bridge != address(0), "PHGRegistry: zero bridge address");
        bridge = _bridge;
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
     * @notice Commit a PHG checkpoint for a device.
     *
     * Called by the bridge after every N verified NOMINAL records.
     * Increments cumulative score, records count, and chains the checkpoint.
     *
     * @param deviceId      The 32-byte device identifier.
     * @param scoreDelta    PHG score earned since the last checkpoint.
     * @param count         Number of NOMINAL records since the last checkpoint.
     * @param biometricHash SHA-256 of the averaged L4 biometric feature JSON.
     */
    function commitCheckpoint(
        bytes32 deviceId,
        uint256 scoreDelta,
        uint32  count,
        bytes32 biometricHash
    ) external onlyBridge {
        // Compute the hash that links this checkpoint to the previous one
        bytes32 prevHash = checkpointHead[deviceId];

        // Update state
        cumulativeScore[deviceId] += scoreDelta;
        recordCount[deviceId]     += count;

        // Chain the new checkpoint head
        bytes32 newHead = keccak256(
            abi.encodePacked(
                deviceId,
                cumulativeScore[deviceId],
                recordCount[deviceId],
                biometricHash
            )
        );
        checkpointHead[deviceId] = newHead;
        scoreDeltaAt[newHead]  = scoreDelta;
        prevCheckpointAt[newHead] = prevHash;

        emit PHGCheckpointCommitted(
            deviceId,
            cumulativeScore[deviceId],
            recordCount[deviceId],
            biometricHash,
            prevHash,
            block.number
        );
    }

    // -----------------------------------------------------------------------
    // Phase 23: Identity Registry wiring
    // -----------------------------------------------------------------------

    /**
     * @notice Set the IdentityContinuityRegistry address. Can only be called once.
     * @param reg Address of the deployed IdentityContinuityRegistry contract.
     */
    function setIdentityRegistry(address reg) external {
        if (identityRegistry != address(0)) revert IdentityRegistryAlreadySet();
        require(reg != address(0), "PHGRegistry: zero registry address");
        identityRegistry = reg;
    }

    /**
     * @notice Transfer the PHG score from fromId to toId.
     *         Callable only by the IdentityContinuityRegistry.
     *
     * Zeroes the source device's score and record count to prevent double-counting.
     * The checkpoint head of fromId is inherited by toId if toId has no prior head.
     *
     * @param fromId Source device identifier.
     * @param toId   Destination device identifier.
     */
    function inheritScore(bytes32 fromId, bytes32 toId) external {
        if (msg.sender != identityRegistry) revert NotIdentityRegistry();

        uint256 score = cumulativeScore[fromId];
        uint32  count = recordCount[fromId];

        cumulativeScore[fromId] = 0;
        recordCount[fromId]     = 0;
        cumulativeScore[toId]  += score;
        recordCount[toId]      += count;

        // Inherit checkpoint head for audit continuity (if toId has none)
        if (checkpointHead[toId] == bytes32(0)) {
            checkpointHead[toId] = checkpointHead[fromId];
        }
        checkpointHead[fromId] = bytes32(0);
    }

    // -----------------------------------------------------------------------
    // View
    // -----------------------------------------------------------------------

    /**
     * @notice Returns true if a device's cumulative PHG score meets the minimum.
     * @param deviceId  The 32-byte device identifier.
     * @param minScore  Minimum score required (e.g. 100 for a tournament).
     */
    function isEligible(bytes32 deviceId, uint256 minScore) external view returns (bool) {
        return cumulativeScore[deviceId] >= minScore;
    }

    /**
     * @notice Sum of scoreDelta across the last windowSize checkpoints (max 8 hops).
     * @param deviceId   The 32-byte device identifier.
     * @param windowSize Number of recent checkpoints to sum (capped at 8).
     * @return velocity  Sum of scoreDelta over the window.
     */
    function getRecentVelocity(bytes32 deviceId, uint256 windowSize)
        external
        view
        returns (uint256 velocity)
    {
        bytes32 head = checkpointHead[deviceId];
        uint256 hops;
        uint256 cap = windowSize < 8 ? windowSize : 8;
        while (head != bytes32(0) && hops < cap) {
            velocity += scoreDeltaAt[head];
            head = prevCheckpointAt[head];
            hops++;
        }
    }

    /**
     * @notice Returns the full checkpoint state for a device.
     */
    function getDeviceState(bytes32 deviceId) external view returns (
        uint256 score,
        uint32  count,
        bytes32 head
    ) {
        return (cumulativeScore[deviceId], recordCount[deviceId], checkpointHead[deviceId]);
    }
}
