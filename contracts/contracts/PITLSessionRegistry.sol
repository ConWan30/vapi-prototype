// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title PITLSessionRegistry -- Phase 26
 * @notice On-chain registry for ZK-proven PITL biometric session credentials.
 *
 * The bridge submits a 256-byte Groth16 proof after each PITL session.
 * In mock mode (pitlVerifier == address(0)) proofs are accepted without
 * cryptographic verification -- useful for testnet and integration tests.
 *
 * Public circuit input order (must match PitlSessionProof.circom declaration):
 *   [0] featureCommitment  -- Poseidon(scaledFeatures[0..6])
 *   [1] humanityProbInt    -- l5_humanity x 1000 in [0, 1000]
 *   [2] inferenceResult    -- 8-bit inference code
 *   [3] nullifierHash      -- Poseidon(deviceIdHash, epoch)
 *   [4] epoch
 *
 * @dev MOCK MODE -- SECURITY NOTICE:
 *   When `pitlVerifier == address(0)`, this contract operates in mock mode.
 *   In mock mode, ALL ZK proof cryptographic invariants are bypassed:
 *     - The 256-byte proof bytes are NOT verified against any circuit.
 *     - The featureCommitment is NOT checked to be a valid Poseidon hash.
 *     - The humanityProbInt is NOT proven to be derived from real biometric features.
 *     - The inferenceResult is NOT constrained by the circuit.
 *     - The nullifier linkage (Poseidon(deviceIdHash, epoch)) is NOT verified.
 *   The ONLY invariant that remains enforced in mock mode is nullifier anti-replay:
 *   each nullifierHash may only be used once (usedNullifiers mapping).
 *
 *   Mock mode is intended solely for testnet deployments and integration testing.
 *   It MUST NOT be used in production. Operators can verify whether this contract
 *   is operating in mock mode by reading the `pitlVerifier` public variable on-chain:
 *   if `pitlVerifier == address(0)`, the deployment is in mock/open mode.
 *
 *   Mock-mode proof submissions are made observable on-chain and off-chain via the
 *   `MockModeProofBypassed` event, emitted on every submitPITLProof call when
 *   `pitlVerifier == address(0)`. Indexers and monitoring tools should treat
 *   sessions associated with this event as unverified.
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
    // -- State ----------------------------------------------------------------

    /// @notice Immutable bridge address -- only address that can submit proofs.
    address public immutable bridge;

    /// @notice Optional Groth16 verifier. address(0) = mock/open mode.
    address public pitlVerifier;

    /// @notice Anti-replay: nullifierHash => used.
    mapping(bytes32 => bool) public usedNullifiers;

    /// @notice Most recent proven humanityProbInt per device [0, 1000].
    mapping(bytes32 => uint256) public latestHumanityProb;

    /// @notice Count of proven sessions per device.
    mapping(bytes32 => uint256) public sessionCount;

    // -- Events ---------------------------------------------------------------

    event PITLSessionProofSubmitted(
        bytes32 indexed deviceId,
        uint256 humanityProbInt,
        uint256 featureCommitment,
        uint256 indexed epoch
    );

    event PITLVerifierSet(address indexed verifier);

    /**
     * @dev Emitted when a proof submission is accepted in mock mode
     * (pitlVerifier == address(0)). This means the proof bytes were NOT
     * cryptographically verified -- only nullifier anti-replay was enforced.
     * Indexers and dashboards should flag sessions associated with this event
     * as unverified / testnet-only.
     */
    event MockModeProofBypassed(bytes32 indexed deviceId, bytes32 indexed nullifierHash);

    // -- Errors ---------------------------------------------------------------

    error OnlyBridge();
    error NullifierUsed(bytes32 nullifier);
    error ProofVerificationFailed();
    error HumanityProbOutOfRange(uint256 value);

    // -- Modifiers ------------------------------------------------------------

    modifier onlyBridge() {
        if (msg.sender != bridge) revert OnlyBridge();
        _;
    }

    // -- Constructor ----------------------------------------------------------

    /// @param _bridge Address of the VAPI bridge (the only authorised submitter).
    constructor(address _bridge) {
        bridge = _bridge;
    }

    // -- Administration -------------------------------------------------------

    /**
     * @notice Set the Groth16 verifier contract (one-time, only by bridge).
     * @param _verifier Address of the deployed IPITLSessionVerifier.
     */
    function setPITLVerifier(address _verifier) external onlyBridge {
        require(pitlVerifier == address(0), "verifier already set");
        pitlVerifier = _verifier;
        emit PITLVerifierSet(_verifier);
    }

    // -- Core -----------------------------------------------------------------

    /**
     * @notice Submit a PITL ZK session proof.
     *
     * @param deviceId          keccak256(pubkey) -- 32-byte device identifier.
     * @param proof             256-byte ABI-packed Groth16 proof.
     * @param featureCommitment Poseidon(scaledFeatures[0..6]).
     * @param humanityProbInt   l5_humanity x 1000 in [0, 1000].
     * @param inferenceCode     8-bit VAPI inference code committed by the bridge.
     *                          Must be in [0, 255]. The circuit C2 constraint enforces
     *                          inferenceCode ∉ [40, 42] (cheat codes 0x28–0x2A), so a
     *                          proof generated with a cheat-code result will fail
     *                          verification here. Passing 0 is valid for CLEAN sessions.
     * @param nullifierHash     Poseidon(deviceIdHash, epoch) -- anti-replay.
     * @param epoch             block.number / EPOCH_BLOCKS at proof time.
     */
    function submitPITLProof(
        bytes32 deviceId,
        bytes calldata proof,
        uint256 featureCommitment,
        uint256 humanityProbInt,
        uint256 inferenceCode,
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
            // inferenceCode is the 8-bit VAPI inference result committed by the bridge.
            // Circuit C2 constraint (IsNotCheatCode) enforces inferenceCode ∉ [40, 42],
            // so any proof generated when a hard cheat code (0x28–0x2A) was detected
            // will fail verifyProof() here — the Groth16 proof is ungenerable for
            // inference ∈ {40, 41, 42} due to the constraint violation at proof time.
            pub[2] = inferenceCode;
            pub[3] = nullifierHash;
            pub[4] = epoch;

            if (!IPITLSessionVerifier(pitlVerifier).verifyProof(a, b, c, pub))
                revert ProofVerificationFailed();
        } else {
            // Mock mode: ZK proof invariants bypassed. Emit observable event so
            // indexers and monitoring tools can identify unverified sessions.
            emit MockModeProofBypassed(deviceId, nullKey);
        }

        // Update state
        latestHumanityProb[deviceId] = humanityProbInt;
        sessionCount[deviceId] += 1;

        emit PITLSessionProofSubmitted(deviceId, humanityProbInt, featureCommitment, epoch);
    }
}
