// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title PITLSessionRegistryV2 -- Phase 62
 * @notice On-chain registry for ZK-proven PITL biometric session credentials.
 *         V2 uses the Phase 62 ceremony artifacts where featureCommitment includes
 *         inferenceCodeFromBody (Poseidon 7 inputs -> 8 inputs).
 *
 * Phase 62 circuit changes (PitlSessionProof.circom):
 *   - C1: featureCommitment = Poseidon(8)(scaledFeatures[0..6], inferenceCodeFromBody)
 *   - C3: inferenceResult === inferenceCodeFromBody  (new binding constraint)
 *   A corrupt bridge that generates inferenceResult=NOMINAL while the PoAC body
 *   encodes CHEAT produces a featureCommitment inconsistent with the raw 228-byte
 *   record -- forensically detectable. nPublic remains 5 (unchanged).
 *
 * The bridge submits a 256-byte Groth16 proof after each PITL session.
 * In mock mode (pitlVerifier == address(0)) proofs are accepted without
 * cryptographic verification -- useful for testnet and integration tests.
 *
 * Public circuit input order (must match PitlSessionProof.circom Phase 62 declaration):
 *   [0] featureCommitment  -- Poseidon(8)(scaledFeatures[0..6], inferenceCodeFromBody)
 *   [1] humanityProbInt    -- l5_humanity x 1000 in [0, 1000]
 *   [2] inferenceResult    -- 8-bit inference code (== inferenceCodeFromBody via C3)
 *   [3] nullifierHash      -- Poseidon(deviceIdHash, epoch)
 *   [4] epoch
 *
 * Deploy: pending IOTX top-up (wallet 0x0Cf36dB57fc4680bcdfC65D1Aff96993C57a4692)
 *   npx hardhat run scripts/deploy-pitl-registry-v2.js --network iotex_testnet
 */

interface IPITLSessionVerifierV2 {
    function verifyProof(
        uint256[2] memory a,
        uint256[2][2] memory b,
        uint256[2] memory c,
        uint256[5] memory input
    ) external view returns (bool);
}

contract PITLSessionRegistryV2 {
    // -- State ----------------------------------------------------------------

    /// @notice Immutable bridge address -- only address that can submit proofs.
    address public immutable bridge;

    /// @notice Optional Groth16 verifier (Phase 62 ceremony vkey). address(0) = mock/open mode.
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
     * @dev Emitted when a proof submission is accepted in mock mode.
     * Mock-mode sessions are unverified -- indexers should flag accordingly.
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
     * @notice Set the Phase 62 Groth16 verifier contract (one-time, only by bridge).
     * @param _verifier Address of the deployed IPITLSessionVerifierV2.
     */
    function setPITLVerifier(address _verifier) external onlyBridge {
        require(pitlVerifier == address(0), "verifier already set");
        pitlVerifier = _verifier;
        emit PITLVerifierSet(_verifier);
    }

    // -- Core -----------------------------------------------------------------

    /**
     * @notice Submit a Phase 62 PITL ZK session proof.
     *
     * @param deviceId          keccak256(pubkey) -- 32-byte device identifier.
     * @param proof             256-byte ABI-packed Groth16 proof (Phase 62 ceremony).
     * @param featureCommitment Poseidon(8)(scaledFeatures[0..6], inferenceCodeFromBody).
     * @param humanityProbInt   l5_humanity x 1000 in [0, 1000].
     * @param inferenceCode     8-bit VAPI inference code. Circuit C2 enforces
     *                          inferenceCode not in [40, 42] (hard cheat codes).
     *                          Circuit C3 (Phase 62) enforces inferenceCode == inferenceCodeFromBody.
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

            // Public inputs in circuit declaration order (Phase 62 — nPublic=5, unchanged):
            // [featureCommitment, humanityProbInt, inferenceResult, nullifierHash, epoch]
            // Phase 62 C3: circuit proves inferenceResult === inferenceCodeFromBody,
            // so the on-chain inferenceCode here is bound to the committed featureCommitment.
            uint256[5] memory pub;
            pub[0] = featureCommitment;
            pub[1] = humanityProbInt;
            pub[2] = inferenceCode;
            pub[3] = nullifierHash;
            pub[4] = epoch;

            if (!IPITLSessionVerifierV2(pitlVerifier).verifyProof(a, b, c, pub))
                revert ProofVerificationFailed();
        } else {
            emit MockModeProofBypassed(deviceId, nullKey);
        }

        // Update state
        latestHumanityProb[deviceId] = humanityProbInt;
        sessionCount[deviceId] += 1;

        emit PITLSessionProofSubmitted(deviceId, humanityProbInt, featureCommitment, epoch);
    }
}
