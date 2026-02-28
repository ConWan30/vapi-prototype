// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title PITLSessionRegistry — Phase 26
 * @notice On-chain registry for ZK-proven PITL biometric session credentials.
 *
 * The bridge submits a 256-byte Groth16 proof after each PITL session.
 * In mock mode (pitlVerifier == address(0)) proofs are accepted without
 * cryptographic verification — useful for testnet and integration tests.
 *
 * Public circuit input order (must match PitlSessionProof.circom declaration):
 *   [0] featureCommitment  — Poseidon(scaledFeatures[0..6])
 *   [1] humanityProbInt    — l5_humanity × 1000 ∈ [0, 1000]
 *   [2] inferenceResult    — 8-bit inference code
 *   [3] nullifierHash      — Poseidon(deviceIdHash, epoch)
 *   [4] epoch
 */

interface IPITLSessionVerifier {
    function verifyProof(
        uint256[2] memory a,
        uint256[2][2] memory b,
        uint256[2] memory c,
        uint256[5] memory input
    ) external view returns (bool);
}

contract PITLSessionRegistry {
    // ── State ────────────────────────────────────────────────────────────────

    /// @notice Immutable bridge address — only address that can submit proofs.
    address public immutable bridge;

    /// @notice Optional Groth16 verifier. address(0) = mock/open mode.
    address public pitlVerifier;

    /// @notice Anti-replay: nullifierHash → used.
    mapping(bytes32 => bool) public usedNullifiers;

    /// @notice Most recent proven humanityProbInt per device [0, 1000].
    mapping(bytes32 => uint256) public latestHumanityProb;

    /// @notice Count of proven sessions per device.
    mapping(bytes32 => uint256) public sessionCount;

    // ── Events ────────────────────────────────────────────────────────────────

    event PITLSessionProofSubmitted(
        bytes32 indexed deviceId,
        uint256 humanityProbInt,
        uint256 featureCommitment,
        uint256 indexed epoch
    );

    event PITLVerifierSet(address indexed verifier);

    // ── Errors ────────────────────────────────────────────────────────────────

    error OnlyBridge();
    error NullifierUsed(bytes32 nullifier);
    error ProofVerificationFailed();
    error HumanityProbOutOfRange(uint256 value);

    // ── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyBridge() {
        if (msg.sender != bridge) revert OnlyBridge();
        _;
    }

    // ── Constructor ───────────────────────────────────────────────────────────

    /// @param _bridge Address of the VAPI bridge (the only authorised submitter).
    constructor(address _bridge) {
        bridge = _bridge;
    }

    // ── Administration ────────────────────────────────────────────────────────

    /**
     * @notice Set the Groth16 verifier contract (one-time, only by bridge).
     * @param _verifier Address of the deployed IPITLSessionVerifier.
     */
    function setPITLVerifier(address _verifier) external onlyBridge {
        require(pitlVerifier == address(0), "verifier already set");
        pitlVerifier = _verifier;
        emit PITLVerifierSet(_verifier);
    }

    // ── Core ──────────────────────────────────────────────────────────────────

    /**
     * @notice Submit a PITL ZK session proof.
     *
     * @param deviceId          keccak256(pubkey) — 32-byte device identifier.
     * @param proof             256-byte ABI-packed Groth16 proof.
     * @param featureCommitment Poseidon(scaledFeatures[0..6]).
     * @param humanityProbInt   l5_humanity × 1000 ∈ [0, 1000].
     * @param nullifierHash     Poseidon(deviceIdHash, epoch) — anti-replay.
     * @param epoch             block.number / EPOCH_BLOCKS at proof time.
     */
    function submitPITLProof(
        bytes32 deviceId,
        bytes calldata proof,
        uint256 featureCommitment,
        uint256 humanityProbInt,
        uint256 nullifierHash,
        uint256 epoch
    ) external onlyBridge {
        // Range check
        if (humanityProbInt > 1000) revert HumanityProbOutOfRange(humanityProbInt);

        // Anti-replay via nullifier
        bytes32 nullKey = bytes32(nullifierHash);
        if (usedNullifiers[nullKey]) revert NullifierUsed(nullKey);
        usedNullifiers[nullKey] = true;

        // Proof length invariant
        require(proof.length == 256, "invalid proof length");

        // Cryptographic verification (skipped in mock mode)
        if (pitlVerifier != address(0)) {
            (
                uint256[2] memory a,
                uint256[2][2] memory b,
                uint256[2] memory c
            ) = abi.decode(proof, (uint256[2], uint256[2][2], uint256[2]));

            // Public inputs in circuit declaration order:
            // [featureCommitment, humanityProbInt, inferenceResult, nullifierHash, epoch]
            uint256[5] memory pub;
            pub[0] = featureCommitment;
            pub[1] = humanityProbInt;
            pub[2] = 0;             // inferenceResult not checked here — stored off-chain
            pub[3] = nullifierHash;
            pub[4] = epoch;

            if (!IPITLSessionVerifier(pitlVerifier).verifyProof(a, b, c, pub))
                revert ProofVerificationFailed();
        }

        // Update state
        latestHumanityProb[deviceId] = humanityProbInt;
        sessionCount[deviceId] += 1;

        emit PITLSessionProofSubmitted(deviceId, humanityProbInt, featureCommitment, epoch);
    }
}
