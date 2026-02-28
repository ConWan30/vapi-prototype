// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "./TeamProofAggregatorZK.sol";

/**
 * @title TeamProofAggregatorZKTestable
 * @author VAPI Project
 * @notice Test harness for TeamProofAggregatorZK with a configurable mock ZK verifier.
 *
 * @dev Overrides _verifyZKProof() with a bool controlled via setMockZKResult().
 *      Follows the exact pattern of PoACVerifierTestable and TieredDeviceRegistryV2Testable.
 *      NOT for production deployment — for Hardhat testing only.
 */
contract TeamProofAggregatorZKTestable is TeamProofAggregatorZK {

    bool public mockZKResult = true;

    constructor(address _poACVerifier)
        TeamProofAggregatorZK(_poACVerifier)
    {}

    /// @notice Set the mock ZK verification result. Call with false to simulate invalid proof.
    function setMockZKResult(bool result) external {
        mockZKResult = result;
    }

    function _verifyZKProof(
        bytes32,
        bytes32,
        uint256,
        bytes calldata,
        uint256,
        uint256
    ) internal view override returns (bool) {
        return mockZKResult;
    }
}
