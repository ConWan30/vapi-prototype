// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./PoACVerifier.sol";

/**
 * @title ProgressAttestation
 * @author VAPI Project
 * @notice Verifiable progress proofs for autonomous gaming coaching.
 *
 * @dev Stores cryptographic attestations of measurable skill improvement,
 *      backed by verified PoAC records. Coaching platforms can query these
 *      proofs to verify student improvement before issuing certificates,
 *      badges, or processing refund guarantees.
 *
 *      Workflow:
 *        1. Student plays with VAPI device, generating PoAC chain
 *        2. Agent detects improvement (e.g., reaction_time decreased 15%)
 *        3. Coach or student calls attestProgress() with baseline + current records
 *        4. Contract verifies both records exist on-chain
 *        5. Progress attestation stored with metric type and improvement BPS
 *        6. Coaching platform queries getProgressHistory() for verification
 *
 *      Metric types:
 *        REACTION_TIME   — Average reaction time improvement
 *        ACCURACY        — Stick/aim precision improvement
 *        CONSISTENCY     — Reduced timing variance
 *        COMBO_EXECUTION — Complex input sequence mastery
 */
contract ProgressAttestation is Ownable {

    // ─── Enums ──────────────────────────────────────────────────────────

    enum MetricType {
        REACTION_TIME,       // Average reaction time improvement (lower is better)
        ACCURACY,            // Stick/aim precision improvement
        CONSISTENCY,         // Reduced timing variance
        COMBO_EXECUTION,     // Complex input sequence mastery
        WORLD_MODEL_EVOLUTION // Phase 13: EWC world model hash divergence (cognitive expansion)
    }

    // ─── Data Structures ────────────────────────────────────────────────

    struct ProgressRecord {
        bytes32    deviceId;
        bytes32    baselineRecordHash;   // PoAC hash from before coaching
        bytes32    currentRecordHash;    // PoAC hash from after coaching
        MetricType metricType;
        uint32     improvementBps;       // Improvement in basis points (100 = 1%)
        uint64     attestedAt;           // Block timestamp
        address    attestor;             // Who submitted (coach or student)
    }

    // ─── State ──────────────────────────────────────────────────────────

    PoACVerifier public poACVerifier;

    /// @notice Global attestation counter (also serves as attestation ID).
    uint256 public attestationCount;

    /// @notice attestationId => ProgressRecord
    mapping(uint256 => ProgressRecord) private _attestations;

    /// @notice deviceId => array of attestation IDs
    mapping(bytes32 => uint256[]) private _deviceAttestations;

    /// @notice Prevent duplicate attestations for same baseline+current pair
    mapping(bytes32 => bool) private _pairUsed;

    // ─── Events ─────────────────────────────────────────────────────────

    event ProgressAttested(
        bytes32    indexed deviceId,
        uint256    indexed attestationId,
        MetricType metricType,
        uint32     improvementBps,
        address    attestor
    );

    // ─── Errors ─────────────────────────────────────────────────────────

    error BaselineNotVerified(bytes32 recordHash);
    error CurrentNotVerified(bytes32 recordHash);
    error PairAlreadyAttested(bytes32 baselineHash, bytes32 currentHash);
    error ZeroImprovement();
    error SameRecord();
    /// @notice Thrown when baseline and current records were verified with incompatible schemas.
    ///         Prevents mixing v1 environmental records with v2 kinematic coaching attestations.
    error IncompatibleSchema(uint8 baselineSchema, uint8 currentSchema);

    // ─── Constructor ────────────────────────────────────────────────────

    constructor(address _poACVerifier) Ownable(msg.sender) {
        poACVerifier = PoACVerifier(_poACVerifier);
    }

    // ─── Core Functions ─────────────────────────────────────────────────

    /**
     * @notice Attest to measurable progress between two verified PoAC records.
     *
     * @param _deviceId         Device that generated both records.
     * @param _baselineHash     SHA-256 hash of the baseline (pre-coaching) PoAC body.
     * @param _currentHash      SHA-256 hash of the current (post-coaching) PoAC body.
     * @param _metricType       Which skill metric improved.
     * @param _improvementBps   Improvement magnitude in basis points (100 = 1%).
     * @return attestationId    Unique ID for this attestation.
     */
    function attestProgress(
        bytes32    _deviceId,
        bytes32    _baselineHash,
        bytes32    _currentHash,
        MetricType _metricType,
        uint32     _improvementBps
    ) external returns (uint256 attestationId) {
        // Validations
        if (_baselineHash == _currentHash) revert SameRecord();
        if (_improvementBps == 0) revert ZeroImprovement();

        if (!poACVerifier.isRecordVerified(_baselineHash)) {
            revert BaselineNotVerified(_baselineHash);
        }
        if (!poACVerifier.isRecordVerified(_currentHash)) {
            revert CurrentNotVerified(_currentHash);
        }

        // Schema version compatibility check.
        // If both records have explicit schemas set, they must match.
        // Records verified via legacy verifyPoAC() (no schema) are allowed
        // to attest freely — backward compatibility with pre-Phase-12 records.
        {
            (uint8 baselineSchema, bool baselineHasSchema) = poACVerifier.getRecordSchema(_baselineHash);
            (uint8 currentSchema,  bool currentHasSchema)  = poACVerifier.getRecordSchema(_currentHash);
            if (baselineHasSchema && currentHasSchema && baselineSchema != currentSchema) {
                revert IncompatibleSchema(baselineSchema, currentSchema);
            }
        }

        bytes32 pairKey = keccak256(abi.encodePacked(_baselineHash, _currentHash));
        if (_pairUsed[pairKey]) {
            revert PairAlreadyAttested(_baselineHash, _currentHash);
        }
        _pairUsed[pairKey] = true;

        // Store attestation
        attestationId = attestationCount++;
        _attestations[attestationId] = ProgressRecord({
            deviceId: _deviceId,
            baselineRecordHash: _baselineHash,
            currentRecordHash: _currentHash,
            metricType: _metricType,
            improvementBps: _improvementBps,
            attestedAt: uint64(block.timestamp),
            attestor: msg.sender
        });

        _deviceAttestations[_deviceId].push(attestationId);

        emit ProgressAttested(
            _deviceId, attestationId, _metricType, _improvementBps, msg.sender
        );
    }

    // ─── View Functions ─────────────────────────────────────────────────

    function getAttestation(uint256 _id)
        external view returns (ProgressRecord memory)
    {
        return _attestations[_id];
    }

    function getDeviceAttestationCount(bytes32 _deviceId)
        external view returns (uint256)
    {
        return _deviceAttestations[_deviceId].length;
    }

    function getDeviceAttestationIds(bytes32 _deviceId)
        external view returns (uint256[] memory)
    {
        return _deviceAttestations[_deviceId];
    }

    function getProgressHistory(bytes32 _deviceId)
        external view returns (ProgressRecord[] memory)
    {
        uint256[] storage ids = _deviceAttestations[_deviceId];
        ProgressRecord[] memory records = new ProgressRecord[](ids.length);
        for (uint256 i = 0; i < ids.length; i++) {
            records[i] = _attestations[ids[i]];
        }
        return records;
    }
}
