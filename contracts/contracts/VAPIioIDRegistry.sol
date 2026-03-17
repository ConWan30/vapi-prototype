// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * VAPIioIDRegistry — Phase 55
 *
 * On-chain device identity registry producing W3C DIDs in did:io: namespace.
 * DID derivation: did:io:<EIP-55 checksum of last 20 bytes of device_id>
 * Bridge wallet is sole registrar. Compatible with IoTeX ioID namespace.
 * Upgradeable to real ioIDStore delegation in Phase 57+.
 */
contract VAPIioIDRegistry {
    address public immutable bridge;

    struct DeviceIdentity {
        address deviceAddress;   // last 20 bytes of device_id, checksummed
        string  did;             // "did:io:" + checksumAddress
        uint256 registeredAt;    // block.timestamp
        uint256 sessionCount;    // incremented per proven PITL session
        bool    active;
    }

    mapping(bytes32 => DeviceIdentity) public devices;
    bytes32[] public deviceIds;

    event DeviceRegistered(
        bytes32 indexed deviceId,
        address indexed deviceAddress,
        string did
    );
    event SessionIncremented(bytes32 indexed deviceId, uint256 newCount);

    error OnlyBridge();
    error AlreadyRegistered();
    error NotRegistered();

    modifier onlyBridge() {
        if (msg.sender != bridge) revert OnlyBridge();
        _;
    }

    constructor(address _bridge) { bridge = _bridge; }

    function register(
        bytes32 deviceId,
        address deviceAddress,
        string calldata did
    ) external onlyBridge {
        if (devices[deviceId].active) revert AlreadyRegistered();
        devices[deviceId] = DeviceIdentity({
            deviceAddress: deviceAddress,
            did:           did,
            registeredAt:  block.timestamp,
            sessionCount:  0,
            active:        true
        });
        deviceIds.push(deviceId);
        emit DeviceRegistered(deviceId, deviceAddress, did);
    }

    function incrementSession(bytes32 deviceId) external onlyBridge {
        if (!devices[deviceId].active) revert NotRegistered();
        devices[deviceId].sessionCount++;
        emit SessionIncremented(deviceId, devices[deviceId].sessionCount);
    }

    function getDID(bytes32 deviceId) external view returns (string memory) {
        return devices[deviceId].did;
    }

    function isRegistered(bytes32 deviceId) external view returns (bool) {
        return devices[deviceId].active;
    }

    function getDeviceCount() external view returns (uint256) {
        return deviceIds.length;
    }
}
