// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "../ITeamProofVerifier.sol";

/**
 * @title MockTeamProofVerifier
 * @author VAPI Project
 * @notice Configurable mock Groth16 verifier for Phase 14C Hardhat tests.
 *
 * @dev Implements ITeamProofVerifier with a settable return value.
 *      Used to test TeamProofAggregatorZK's real _verifyZKProof path:
 *        - Deploy with true to simulate a valid proof (accepted).
 *        - Call setResult(false) to simulate an invalid proof (rejected).
 *      NOT for production. Hardhat tests only.
 */
contract MockTeamProofVerifier is ITeamProofVerifier {

    bool public mockResult;

    constructor(bool _result) {
        mockResult = _result;
    }

    function setResult(bool _result) external {
        mockResult = _result;
    }

    function verifyProof(
        uint256[2] memory,
        uint256[2][2] memory,
        uint256[2] memory,
        uint256[4] memory
    ) external view override returns (bool) {
        return mockResult;
    }
}
