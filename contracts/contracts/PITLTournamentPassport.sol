// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

interface IPITLSessionRegistry {
    function usedNullifiers(bytes32 nullifier) external view returns (bool);
}
interface IVAPIioIDRegistry {
    function isRegistered(bytes32 deviceId) external view returns (bool);
    function getDID(bytes32 deviceId) external view returns (string memory);
}
interface ITournamentPassportVerifier {
    function verifyProof(uint256[2] memory a, uint256[2][2] memory b,
        uint256[2] memory c, uint256[5] memory input) external view returns (bool);
}

/**
 * PITLTournamentPassport — Phase 56
 *
 * Issues ZK-proven tournament passport credentials.
 * Proof attests: N=5 NOMINAL sessions, humanity >= 60%, from ioID-registered device.
 * Mock mode: passportVerifier == address(0) -> proof bypassed (testnet default).
 */
contract PITLTournamentPassport {
    uint8 public constant SESSION_COUNT = 5;

    address public immutable bridge;
    IPITLSessionRegistry public immutable sessionRegistry;
    IVAPIioIDRegistry    public immutable ioidRegistry;
    address public passportVerifier; // address(0) = mock mode

    struct Passport {
        bytes32 passportHash;
        uint256 ioidTokenId;
        uint256 minHumanityInt;
        uint256 issuedAt;
        bool    active;
    }
    mapping(bytes32 => Passport) public passports;

    event PassportIssued(
        bytes32 indexed deviceId, bytes32 passportHash,
        uint256 minHumanityInt, string did
    );
    event PassportVerifierSet(address indexed verifier);

    error OnlyBridge();
    error DeviceNotInioID();
    error SessionNotProven(bytes32 nullifier);
    error ProofFailed();
    error VerifierAlreadySet();

    modifier onlyBridge() { if (msg.sender != bridge) revert OnlyBridge(); _; }

    constructor(address _bridge, address _sessionReg, address _ioidReg) {
        bridge          = _bridge;
        sessionRegistry = IPITLSessionRegistry(_sessionReg);
        ioidRegistry    = IVAPIioIDRegistry(_ioidReg);
    }

    function setPassportVerifier(address _v) external onlyBridge {
        if (passportVerifier != address(0)) revert VerifierAlreadySet();
        passportVerifier = _v;
        emit PassportVerifierSet(_v);
    }

    function submitPassport(
        bytes32    deviceId,
        bytes calldata proof,
        bytes32[5] calldata nullifiers,
        bytes32    passportHash,
        uint256    ioidTokenId,
        uint256    minHumanityInt,
        uint256    epoch
    ) external onlyBridge {
        if (!ioidRegistry.isRegistered(deviceId)) revert DeviceNotInioID();
        for (uint256 i = 0; i < SESSION_COUNT; i++) {
            if (!sessionRegistry.usedNullifiers(nullifiers[i]))
                revert SessionNotProven(nullifiers[i]);
        }
        if (passportVerifier != address(0) && proof.length == 256) {
            (uint256[2] memory a, uint256[2][2] memory b, uint256[2] memory c)
                = abi.decode(proof, (uint256[2], uint256[2][2], uint256[2]));
            uint256[5] memory pub = [
                uint256(deviceId), ioidTokenId, uint256(passportHash),
                minHumanityInt, epoch
            ];
            if (!ITournamentPassportVerifier(passportVerifier).verifyProof(a, b, c, pub))
                revert ProofFailed();
        }
        string memory did = ioidRegistry.getDID(deviceId);
        passports[deviceId] = Passport(passportHash, ioidTokenId,
                                       minHumanityInt, block.timestamp, true);
        emit PassportIssued(deviceId, passportHash, minHumanityInt, did);
    }

    function getPassport(bytes32 deviceId) external view returns (Passport memory) {
        return passports[deviceId];
    }
    function hasPassport(bytes32 deviceId) external view returns (bool) {
        return passports[deviceId].active;
    }
}
