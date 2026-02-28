// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "../TieredDeviceRegistry.sol";

/**
 * @title TieredDeviceRegistryV2Testable
 * @dev Test helper that overrides _p256Verify() so V2 attestation tests can
 *      exercise manufacturer key lookup and revocation logic without calling
 *      the IoTeX P256 precompile (0x0100), which is unavailable on Hardhat EVM.
 *
 *      _validateAttestationV2() still enforces:
 *        - 64-byte proof length
 *        - attestationEnforced flag
 *        - NoApprovedManufacturer (key must be registered)
 *        - ManufacturerKeyInactive (key must be active)
 *
 *      Only the final precompile call is bypassed — it always returns true.
 *      Set _mockP256Result = false to simulate precompile rejection (AttestationVerificationFailed).
 */
contract TieredDeviceRegistryV2Testable is TieredDeviceRegistry {
    bool public mockP256Result = true;  // default: accept

    constructor(uint256 _emulatedDeposit, uint256 _standardDeposit, uint256 _attestedDeposit)
        TieredDeviceRegistry(_emulatedDeposit, _standardDeposit, _attestedDeposit)
    {}

    /// @notice Owner can flip the mock P256 result for negative-path testing.
    function setMockP256Result(bool _result) external onlyOwner {
        mockP256Result = _result;
    }

    function _p256Verify(
        bytes32,
        bytes calldata,
        bytes32,
        bytes32
    ) internal view override returns (bool) {
        return mockP256Result;
    }
}
