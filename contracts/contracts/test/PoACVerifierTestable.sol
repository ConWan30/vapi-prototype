// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "../PoACVerifier.sol";

/**
 * @title PoACVerifierTestable
 * @dev Test helper that skips P256 signature verification.
 *      Used in Hardhat tests where the IoTeX P256 precompile (0x0100) is unavailable.
 */
contract PoACVerifierTestable is PoACVerifier {
    constructor(address _deviceRegistry, uint256 _maxTimestampSkew)
        PoACVerifier(_deviceRegistry, _maxTimestampSkew)
    {}

    function _requireValidSignature(
        bytes32, bytes32, bytes calldata
    ) internal pure override {
        // Always passes — signature verification is bypassed for testing.
    }
}
