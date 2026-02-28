// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title PHGCredential
 * @notice ERC-5192-inspired soulbound on-chain credential registry for VAPI PHG humanity scores.
 *
 * Phase 28 — "The Credential Becomes a Portal"
 *
 * Each device that accumulates a PHG checkpoint AND submits a PITL session proof earns
 * exactly one soulbound credential on-chain.  The credential records:
 *   - nullifierHash    — ZK anti-replay nullifier from PITLProver
 *   - featureCommitment — biometric commitment hash
 *   - humanityProbInt  — humanity_probability × 1000 (range [0, 1000])
 *   - mintedAt         — block.timestamp of first mint
 *
 * Design decisions:
 *   - No ERC-721 inheritance — avoids wallet-address requirement for Phase 28.
 *     A future phase will add NFT ownership once wallet linking is implemented.
 *   - locked() returns true for all credentials — ERC-5192 soulbound concept.
 *   - credentialOf[deviceId] == 0 is the "not minted" sentinel (_nextId starts at 1).
 *   - INSERT OR IGNORE semantics: AlreadyMinted + NullifierUsed errors prevent re-entry.
 *   - onlyBridge: only the designated bridge address can mint credentials.
 *
 * Access pattern mirrors PHGRegistry.sol and PITLSessionRegistry.sol.
 */
contract PHGCredential {

    address public immutable bridge;
    uint256 private _nextId;

    // -------------------------------------------------------------------------
    // Data
    // -------------------------------------------------------------------------

    struct Credential {
        bytes32 nullifierHash;      // ZK proof nullifier (unique per session)
        bytes32 featureCommitment;  // Biometric feature commitment hash
        uint256 humanityProbInt;    // humanity_probability × 1000, range [0, 1000]
        uint256 mintedAt;           // block.timestamp at mint
    }

    /// @notice deviceId (bytes32 keccak of pubkey) → credentialId (0 = not minted)
    mapping(bytes32 => uint256)    public credentialOf;

    /// @notice credentialId → deviceId (reverse lookup)
    mapping(uint256 => bytes32)    public deviceOfId;

    /// @notice credentialId → Credential data
    mapping(uint256 => Credential) public credentials;

    /// @notice ZK nullifier anti-replay registry
    mapping(bytes32 => bool)       public usedNullifiers;

    // Phase 37: Provisional enforcement — keyed by deviceId
    mapping(bytes32 => bool)    public isSuspended;
    mapping(bytes32 => uint256) public suspendedUntil;    // Unix timestamp
    mapping(bytes32 => bytes32) public suspensionEvidence; // insight_digest reference

    // -------------------------------------------------------------------------
    // Events
    // -------------------------------------------------------------------------

    event CredentialMinted(
        bytes32 indexed deviceId,
        uint256 indexed credentialId,
        uint256 humanityProbInt,
        uint256 blockNumber
    );

    // Phase 37: Enforcement events
    event CredentialSuspended(bytes32 indexed deviceId, bytes32 evidenceHash, uint256 until);
    event CredentialReinstated(bytes32 indexed deviceId);

    // -------------------------------------------------------------------------
    // Errors
    // -------------------------------------------------------------------------

    error OnlyBridge();
    error AlreadyMinted(bytes32 deviceId);
    error NullifierUsed(bytes32 nullifierHash);
    error InvalidScore(uint256 value);

    // Phase 37: Enforcement errors
    error CredentialNotMinted(bytes32 deviceId);
    error AlreadySuspended(bytes32 deviceId);
    error NotSuspended(bytes32 deviceId);

    // -------------------------------------------------------------------------
    // Modifiers
    // -------------------------------------------------------------------------

    modifier onlyBridge() {
        if (msg.sender != bridge) revert OnlyBridge();
        _;
    }

    // -------------------------------------------------------------------------
    // Constructor
    // -------------------------------------------------------------------------

    constructor(address _bridge) {
        bridge = _bridge;
        _nextId = 1; // 0 is reserved as "not minted" sentinel in credentialOf
    }

    // -------------------------------------------------------------------------
    // Write
    // -------------------------------------------------------------------------

    /**
     * @notice Mint a soulbound PHG credential for a device.
     * @param deviceId          keccak256(pubkey) device identifier
     * @param nullifierHash     ZK proof nullifier — enforces one credential per session proof
     * @param featureCommitment Biometric feature commitment hash from PITLProver
     * @param humanityProbInt   humanity_probability × 1000, must be ≤ 1000
     * @return id               Assigned credentialId (≥ 1)
     *
     * Reverts:
     *   AlreadyMinted  — device already has a credential
     *   NullifierUsed  — nullifier was used in a previous mint
     *   InvalidScore   — humanityProbInt > 1000
     *   OnlyBridge     — caller is not the bridge
     */
    function mintCredential(
        bytes32 deviceId,
        bytes32 nullifierHash,
        bytes32 featureCommitment,
        uint256 humanityProbInt
    ) external onlyBridge returns (uint256 id) {
        if (credentialOf[deviceId] != 0)      revert AlreadyMinted(deviceId);
        if (usedNullifiers[nullifierHash])     revert NullifierUsed(nullifierHash);
        if (humanityProbInt > 1000)            revert InvalidScore(humanityProbInt);

        id = _nextId++;
        usedNullifiers[nullifierHash] = true;
        credentialOf[deviceId] = id;
        deviceOfId[id] = deviceId;
        credentials[id] = Credential({
            nullifierHash:     nullifierHash,
            featureCommitment: featureCommitment,
            humanityProbInt:   humanityProbInt,
            mintedAt:          block.timestamp
        });

        emit CredentialMinted(deviceId, id, humanityProbInt, block.number);
    }

    // -------------------------------------------------------------------------
    // Read
    // -------------------------------------------------------------------------

    /// @notice Returns true if the device has a minted credential.
    function hasCredential(bytes32 deviceId) external view returns (bool) {
        return credentialOf[deviceId] != 0;
    }

    /// @notice Returns full Credential struct for the device. Returns zero struct if not minted.
    function getCredential(bytes32 deviceId) external view returns (Credential memory) {
        return credentials[credentialOf[deviceId]];
    }

    // -------------------------------------------------------------------------
    // Phase 37: Provisional Enforcement
    // -------------------------------------------------------------------------

    /**
     * @notice Suspend a PHGCredential by deviceId. Sets isSuspended=true for durationSeconds.
     * @param deviceId        Device to suspend (must have minted credential)
     * @param evidenceHash    Reference to the insight_digest that triggered suspension
     * @param durationSeconds Suspension length in seconds
     *
     * Reverts:
     *   CredentialNotMinted — device has no credential
     *   AlreadySuspended    — credential is already suspended
     *   OnlyBridge          — caller is not the bridge
     */
    function suspend(
        bytes32 deviceId,
        bytes32 evidenceHash,
        uint256 durationSeconds
    ) external onlyBridge {
        if (credentialOf[deviceId] == 0) revert CredentialNotMinted(deviceId);
        if (isSuspended[deviceId])       revert AlreadySuspended(deviceId);
        isSuspended[deviceId]         = true;
        suspendedUntil[deviceId]      = block.timestamp + durationSeconds;
        suspensionEvidence[deviceId]  = evidenceHash;
        emit CredentialSuspended(deviceId, evidenceHash, block.timestamp + durationSeconds);
    }

    /**
     * @notice Reinstate a suspended PHGCredential.
     *
     * Reverts:
     *   NotSuspended — credential is not currently suspended
     *   OnlyBridge   — caller is not the bridge
     */
    function reinstate(bytes32 deviceId) external onlyBridge {
        if (!isSuspended[deviceId]) revert NotSuspended(deviceId);
        isSuspended[deviceId]    = false;
        suspendedUntil[deviceId] = 0;
        emit CredentialReinstated(deviceId);
    }

    /**
     * @notice Returns true if the device has a minted, non-suspended credential.
     * @dev    TournamentGateV3 calls this to gate tournament access.
     */
    function isActive(bytes32 deviceId) external view returns (bool) {
        return credentialOf[deviceId] != 0 && !isSuspended[deviceId];
    }

    // -------------------------------------------------------------------------
    // ERC-5192 concept
    // -------------------------------------------------------------------------

    /**
     * @notice All PHG credentials are permanently locked (soulbound, non-transferable).
     *         Returns true for any tokenId — this is the ERC-5192 locked() interface.
     */
    function locked(uint256 /* tokenId */) external pure returns (bool) {
        return true;
    }
}
