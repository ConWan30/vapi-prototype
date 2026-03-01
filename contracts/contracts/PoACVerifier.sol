// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "./DeviceRegistry.sol";

/**
 * @title PoACVerifier
 * @author VAPI Project
 * @notice On-chain verification of Proof of Autonomous Cognition (PoAC) records
 *         submitted by VAPI devices on the IoTeX blockchain.
 *
 * @dev Verification accepts the raw 164-byte body exactly as signed by the
 *      firmware (no re-serialization), plus the 64-byte ECDSA-P256 signature.
 *
 *      Pipeline:
 *        1. Validate body/signature lengths (164 / 64 bytes).
 *        2. Check device is registered and active in {DeviceRegistry}.
 *        3. Compute SHA-256 of the raw body (identical to firmware digest).
 *        4. Verify the ECDSA-P256 signature over that SHA-256 digest
 *           using the IoTeX P256 precompile at address 0x0100.
 *        5. Parse fields from the raw body for semantic validation.
 *        6. Validate monotonic counter, timestamp skew, chain linkage.
 *        7. Store SHA-256(body) as the chain head and record hash.
 *           This matches the firmware's chain_head = SHA-256(serialized_body),
 *           ensuring prev_poac_hash linkage is consistent end-to-end.
 *        8. Update the device's reputation in {DeviceRegistry}.
 *
 *      Raw body layout (big-endian, matching firmware poac_serialize()):
 *        Offset  Field                  Size
 *        0x00    prev_poac_hash         32B
 *        0x20    sensor_commitment      32B
 *        0x40    model_manifest_hash    32B
 *        0x60    world_model_hash       32B
 *        0x80    inference_result       1B
 *        0x81    action_code            1B
 *        0x82    confidence             1B
 *        0x83    battery_pct            1B
 *        0x84    monotonic_ctr          4B   (uint32 big-endian)
 *        0x88    timestamp_ms           8B   (int64 big-endian)
 *        0x90    latitude               8B   (IEEE 754 double big-endian)
 *        0x98    longitude              8B   (IEEE 754 double big-endian)
 *        0xA0    bounty_id              4B   (uint32 big-endian)
 *        ---     total                  164B
 *
 *      ## IoTeX P256 Precompile (0x0100)
 *      Input:  hash(32) || r(32) || s(32) || x(32) || y(32) = 160 bytes
 *      Output: 0x01 (32-byte left-padded) if valid, 0x00 otherwise.
 */
contract PoACVerifier is Ownable, ReentrancyGuard {

    // -------------------------------------------------------------------------
    //  Constants
    // -------------------------------------------------------------------------

    /// @notice IoTeX P256 signature verification precompile address.
    address public constant P256_PRECOMPILE = address(0x0100);

    /// @notice Size of the serialized PoAC body (before signature).
    uint256 public constant POAC_BODY_SIZE = 164;

    /// @notice Size of the ECDSA-P256 signature (r || s).
    uint256 public constant POAC_SIG_SIZE = 64;

    /// @notice Maximum allowable timestamp skew (seconds).
    uint256 public maxTimestampSkew;

    // -------------------------------------------------------------------------
    //  Data structures
    // -------------------------------------------------------------------------

    /// @notice Chain tracking state for each device.
    struct ChainState {
        bytes32 lastRecordHash; // SHA-256 of the most recent verified body
        uint32  lastCounter;    // Last verified monotonic counter value
        uint32  verifiedCount;  // Total verified PoAC records for this device
        bool    initialized;    // True after first verified record
    }

    // -------------------------------------------------------------------------
    //  State
    // -------------------------------------------------------------------------

    /// @notice Reference to the DeviceRegistry contract.
    DeviceRegistry public deviceRegistry;

    /// @notice Per-device chain integrity state. deviceId => ChainState.
    mapping(bytes32 => ChainState) private _chainState;

    /// @notice Record hash => true if this PoAC has been verified on-chain.
    mapping(bytes32 => bool) public verifiedRecords;

    /// @notice Record hash => true if this body was ever submitted to this contract.
    /// @dev    Unlike {verifiedRecords} (set only on full verification success),
    ///         submittedHashes is set in the OUTER batch context so it persists even
    ///         when the child verification call reverts, permanently preventing
    ///         re-submission of any body that entered a batch — regardless of outcome.
    mapping(bytes32 => bool) public submittedHashes;

    /// @notice Total number of PoAC records verified across all devices.
    uint256 public totalVerifiedCount;

    /// @notice Inference result extracted from each verified PoAC body at byte offset 0x80.
    ///         Key insight: _parseBody() already parses this; we persist it for downstream queries.
    ///         Default 0 for unverified records (cheat codes start at 0x28, so 0 is always clean).
    mapping(bytes32 => uint8) public recordInferences;

    /// @notice Sensor commitment schema version for records verified via verifyPoACWithSchema().
    ///         0 = not set / unknown (record submitted via legacy verifyPoAC()).
    ///         1 = v1 environmental (Pebble Tracker: temp/humidity/pressure/GPS/accel).
    ///         2 = v2 kinematic/haptic (DualShock: sticks/triggers/gyro/accel + adaptive trigger).
    mapping(bytes32 => uint8) public recordSchemas;

    /// @notice True if a schema version was explicitly provided for this record hash.
    ///         Distinguishes "schema not set" (false) from "schema 0 explicitly set" (impossible —
    ///         verifyPoACWithSchema rejects schemaVersion==0).
    mapping(bytes32 => bool) public recordHasSchema;

    // -------------------------------------------------------------------------
    //  Events
    // -------------------------------------------------------------------------

    /// @notice Emitted when a PoAC record is successfully verified.
    event PoACVerified(
        bytes32 indexed deviceId,
        bytes32 indexed recordHash,
        uint32  monotonicCtr,
        int64   timestampMs,
        uint8   actionCode,
        uint8   inferenceResult,
        uint32  bountyId
    );

    /// @notice Emitted when a batch verification completes.
    event BatchVerified(
        uint256 submitted,
        uint256 verified,
        uint256 rejected
    );

    // -------------------------------------------------------------------------
    //  Errors
    // -------------------------------------------------------------------------

    error DeviceNotRegistered(bytes32 deviceId);
    error DeviceNotActive(bytes32 deviceId);
    error InvalidSignature(bytes32 deviceId, bytes32 recordHash);
    error CounterNotMonotonic(bytes32 deviceId, uint32 submitted, uint32 lastKnown);
    error TimestampOutOfRange(bytes32 deviceId, int64 recordTs, uint256 blockTs, uint256 maxSkew);
    error ChainLinkageBroken(bytes32 deviceId, bytes32 submitted, bytes32 expected);
    error RecordAlreadyVerified(bytes32 recordHash);
    error RecordAlreadySubmitted(bytes32 recordHash);
    error InvalidBodyLength(uint256 provided);
    error InvalidSignatureLength(uint256 provided);
    error EmptyBatch();
    error ArrayLengthMismatch();
    error InvalidSchemaVersion(uint8 provided);
    /// @dev Raised when the P256 precompile staticcall itself fails (not enough gas, or
    ///      the precompile is not available on this chain).
    error P256PrecompileCallFailed();
    /// @dev Raised when the P256 precompile returns empty data (zero-length returndata),
    ///      which is distinct from returning 0x00 (invalid signature) and indicates the
    ///      precompile is not deployed at address 0x0100 on this network.
    error P256PrecompileEmptyReturn();

    // -------------------------------------------------------------------------
    //  Constructor
    // -------------------------------------------------------------------------

    /**
     * @param _deviceRegistry   Address of the deployed DeviceRegistry contract.
     * @param _maxTimestampSkew  Maximum acceptable timestamp skew in seconds.
     */
    constructor(address _deviceRegistry, uint256 _maxTimestampSkew) Ownable(msg.sender) {
        deviceRegistry = DeviceRegistry(_deviceRegistry);
        maxTimestampSkew = _maxTimestampSkew;
    }

    // -------------------------------------------------------------------------
    //  Admin
    // -------------------------------------------------------------------------

    function setMaxTimestampSkew(uint256 _newSkew) external onlyOwner {
        maxTimestampSkew = _newSkew;
    }

    // -------------------------------------------------------------------------
    //  Core verification — single record
    // -------------------------------------------------------------------------

    /**
     * @notice Verify a single PoAC record from its raw wire-format bytes.
     *
     * @param _deviceId   Device identifier (keccak256 of device pubkey).
     * @param _rawBody    The 164-byte serialized PoAC body (exactly as signed).
     * @param _signature  ECDSA-P256 signature (r || s, 64 bytes).
     * @return recordHash SHA-256 of the body (matches firmware chain head).
     */
    function verifyPoAC(
        bytes32 _deviceId,
        bytes calldata _rawBody,
        bytes calldata _signature
    )
        external
        nonReentrant
        returns (bytes32 recordHash)
    {
        // Early length check enables hash pre-computation for the replay gate.
        if (_rawBody.length != POAC_BODY_SIZE) revert InvalidBodyLength(_rawBody.length);
        bytes32 h = sha256(_rawBody);
        // Anti-replay gate: reject any body previously seen at this address.
        // In the single-TX path, submittedHashes is set inside _verifyInternal
        // only on full success, so a transient failure (bad sig, clock skew) does
        // NOT permanently blacklist the body — the bridge can retry.
        if (submittedHashes[h]) revert RecordAlreadySubmitted(h);
        return _verifyInternal(_deviceId, _rawBody, _signature);
    }

    // -------------------------------------------------------------------------
    //  Batch verification
    // -------------------------------------------------------------------------

    /**
     * @notice Batch-verify multiple PoAC records.
     *
     * @dev Records that fail are silently skipped (not reverted).
     *      The BatchVerified event reports counts.
     */
    function verifyPoACBatch(
        bytes32[] calldata _deviceIds,
        bytes[] calldata _rawBodies,
        bytes[] calldata _signatures
    )
        external
        nonReentrant
        returns (bytes32[] memory recordHashes)
    {
        uint256 count = _deviceIds.length;
        if (count == 0) revert EmptyBatch();
        if (count != _rawBodies.length || count != _signatures.length) {
            revert ArrayLengthMismatch();
        }

        recordHashes = new bytes32[](count);
        uint256 verified = 0;
        uint256 rejected = 0;

        for (uint256 i = 0; i < count; i++) {
            // Pre-compute hash in OUTER context so submittedHashes[h] persists
            // even when the child call reverts (e.g., bad signature, counter mismatch,
            // timestamp out of range).  This permanently prevents re-submission of any
            // body that entered this batch — distinguishing VAPI's batch path from the
            // single-TX retry-allowed path of verifyPoAC().
            if (_rawBodies[i].length != POAC_BODY_SIZE) {
                recordHashes[i] = bytes32(0);
                rejected++;
                continue;
            }
            bytes32 h = sha256(_rawBodies[i]);
            if (submittedHashes[h]) {
                recordHashes[i] = bytes32(0);
                rejected++;
                continue;
            }
            submittedHashes[h] = true;   // Persists in outer TX even on child revert

            try this.verifyPoACExternal(
                _deviceIds[i],
                _rawBodies[i],
                _signatures[i]
            ) returns (bytes32 hash) {
                recordHashes[i] = hash;
                verified++;
            } catch {
                recordHashes[i] = bytes32(0);
                rejected++;
            }
        }

        emit BatchVerified(count, verified, rejected);
    }

    /**
     * @notice External wrapper for try/catch in batch. Not for direct use.
     */
    function verifyPoACExternal(
        bytes32 _deviceId,
        bytes calldata _rawBody,
        bytes calldata _signature
    )
        external
        returns (bytes32)
    {
        require(msg.sender == address(this), "PoACVerifier: internal only");
        return _verifyInternal(_deviceId, _rawBody, _signature);
    }

    // -------------------------------------------------------------------------
    //  Schema-tagged verification
    // -------------------------------------------------------------------------

    /**
     * @notice Verify a single PoAC record and record its sensor commitment schema version.
     *
     * @dev    Identical to verifyPoAC() but additionally persists the schema version so that
     *         downstream contracts (ProgressAttestation) can enforce schema compatibility.
     *
     * @param _deviceId      Device identifier (keccak256 of device pubkey).
     * @param _rawBody       The 164-byte serialized PoAC body (exactly as signed).
     * @param _signature     ECDSA-P256 signature (r || s, 64 bytes).
     * @param _schemaVersion Sensor schema: 1 = v1 environmental, 2 = v2 kinematic. Must be > 0.
     * @return recordHash    SHA-256 of the body (matches firmware chain head).
     */
    function verifyPoACWithSchema(
        bytes32 _deviceId,
        bytes calldata _rawBody,
        bytes calldata _signature,
        uint8 _schemaVersion
    )
        external
        nonReentrant
        returns (bytes32 recordHash)
    {
        if (_schemaVersion == 0) revert InvalidSchemaVersion(_schemaVersion);
        if (_rawBody.length != POAC_BODY_SIZE) revert InvalidBodyLength(_rawBody.length);
        bytes32 h = sha256(_rawBody);
        if (submittedHashes[h]) revert RecordAlreadySubmitted(h);
        recordHash = _verifyInternal(_deviceId, _rawBody, _signature);
        recordSchemas[recordHash]   = _schemaVersion;
        recordHasSchema[recordHash] = true;
    }

    // -------------------------------------------------------------------------
    //  Internal verification logic
    // -------------------------------------------------------------------------

    /// @dev Parsed fields from the raw body, packed into a struct to avoid stack-too-deep.
    struct ParsedFields {
        bytes32 prevHash;
        uint8   inferenceResult;
        uint8   actionCode;
        uint32  monotonicCtr;
        int64   timestampMs;
        uint32  bountyId;
    }

    function _verifyInternal(
        bytes32 _deviceId,
        bytes calldata _rawBody,
        bytes calldata _signature
    )
        internal
        returns (bytes32 recordHash)
    {
        // 1. Validate lengths
        if (_rawBody.length != POAC_BODY_SIZE) revert InvalidBodyLength(_rawBody.length);
        if (_signature.length != POAC_SIG_SIZE) revert InvalidSignatureLength(_signature.length);

        // 2. Check device is registered and active
        _requireDeviceActive(_deviceId);

        // 3. Compute SHA-256 of raw body — identical to firmware digest
        recordHash = sha256(_rawBody);

        // 4. Prevent duplicate verification
        if (verifiedRecords[recordHash]) revert RecordAlreadyVerified(recordHash);

        // 5. Verify ECDSA-P256 signature via IoTeX precompile
        _requireValidSignature(_deviceId, recordHash, _signature);

        // 6. Parse fields from raw body
        ParsedFields memory f = _parseBody(_rawBody);

        // 7. Validate monotonic counter
        ChainState storage chain = _chainState[_deviceId];
        if (chain.initialized && f.monotonicCtr <= chain.lastCounter) {
            revert CounterNotMonotonic(_deviceId, f.monotonicCtr, chain.lastCounter);
        }

        // 8. Validate timestamp within acceptable skew
        _validateTimestamp(_deviceId, f.timestampMs);

        // 9. Validate chain linkage (if not genesis record)
        if (f.prevHash != bytes32(0)) {
            if (chain.initialized && f.prevHash != chain.lastRecordHash) {
                revert ChainLinkageBroken(_deviceId, f.prevHash, chain.lastRecordHash);
            }
        }

        // 10. Update chain state
        chain.lastRecordHash = recordHash;
        chain.lastCounter = f.monotonicCtr;
        chain.verifiedCount++;
        chain.initialized = true;
        recordInferences[recordHash] = f.inferenceResult;  // Persist for cheat-flag queries

        submittedHashes[recordHash] = true;  // Anti-replay: marks body as seen
        verifiedRecords[recordHash] = true;  // Attestation registry: body fully verified
        totalVerifiedCount++;

        // 11. Update device reputation
        try deviceRegistry.updateReputation(_deviceId, 1, 0, 0) {} catch {}

        // 12. Emit event
        emit PoACVerified(
            _deviceId, recordHash, f.monotonicCtr, f.timestampMs,
            f.actionCode, f.inferenceResult, f.bountyId
        );
    }

    function _requireDeviceActive(bytes32 _deviceId) internal view {
        if (!deviceRegistry.isDeviceActive(_deviceId)) {
            try deviceRegistry.getDeviceInfo(_deviceId) returns (
                DeviceRegistry.DeviceInfo memory
            ) {
                revert DeviceNotActive(_deviceId);
            } catch {
                revert DeviceNotRegistered(_deviceId);
            }
        }
    }

    function _requireValidSignature(
        bytes32 _deviceId, bytes32 _digest, bytes calldata _sig
    ) internal virtual view {
        bytes memory pubkey = deviceRegistry.getDevicePubkey(_deviceId);
        if (!_verifyP256Signature(_digest, _sig, pubkey)) {
            revert InvalidSignature(_deviceId, _digest);
        }
    }

    function _validateTimestamp(bytes32 _deviceId, int64 _timestampMs) internal view {
        uint256 blockTsMs = block.timestamp * 1000;
        uint256 recordTsAbs = _timestampMs >= 0 ? uint256(uint64(_timestampMs)) : 0;
        uint256 skewMs = maxTimestampSkew * 1000;
        if (recordTsAbs > blockTsMs + skewMs || recordTsAbs + skewMs < blockTsMs) {
            revert TimestampOutOfRange(_deviceId, _timestampMs, block.timestamp, maxTimestampSkew);
        }
    }

    function _parseBody(bytes calldata _rawBody)
        internal pure returns (ParsedFields memory f)
    {
        assembly {
            let fPtr := f
            // prevPoACHash: bytes 0-31
            mstore(fPtr, calldataload(_rawBody.offset))

            // Fields at offset 128 (after 4x32B hashes)
            let w := calldataload(add(_rawBody.offset, 128))
            // inferenceResult = byte 0
            mstore(add(fPtr, 0x20), byte(0, w))
            // actionCode = byte 1
            mstore(add(fPtr, 0x40), byte(1, w))
            // monotonicCtr = bytes 4-7
            mstore(add(fPtr, 0x60), and(shr(192, w), 0xFFFFFFFF))
            // timestampMs: bytes 136-143
            mstore(add(fPtr, 0x80), shr(192, calldataload(add(_rawBody.offset, 136))))
            // bountyId: bytes 160-163
            mstore(add(fPtr, 0xA0), shr(224, calldataload(add(_rawBody.offset, 160))))
        }
    }

    // -------------------------------------------------------------------------
    //  P256 Signature Verification
    // -------------------------------------------------------------------------

    /**
     * @dev Verify ECDSA-P256 signature using IoTeX precompile at 0x0100.
     *      Input:  digest(32) || r(32) || s(32) || x(32) || y(32) = 160 bytes
     *      Output: 0x00..01 (32 bytes) if valid, 0x00..00 if invalid.
     *
     *      Edge cases handled explicitly:
     *        1. staticcall returns false — precompile call failed (OOG or not deployed).
     *           Reverts with P256PrecompileCallFailed rather than silently returning false.
     *        2. returndata.length == 0 — precompile is not deployed at 0x0100 on this chain.
     *           Reverts with P256PrecompileEmptyReturn to give a clear diagnosis.
     *        3. returndata.length < 32 but > 0 — malformed output; treated as call failure.
     *        4. returndata value == 0 — precompile ran but declared signature invalid.
     *           Returns false (caller raises InvalidSignature).
     *
     *      Gas safety: The 160-byte staticcall requires ~3,500 gas on IoTeX (precompile).
     *      Hardhat/EVM simulation costs ~2,000 gas. We gate on gasleft() > 5,000 to avoid
     *      OOG inside the call silently swallowing the error in the child frame.
     */
    function _verifyP256Signature(
        bytes32 _digest,
        bytes calldata _sig,
        bytes memory _pubkey
    )
        internal
        view
        returns (bool)
    {
        require(_sig.length == 64, "PoACVerifier: signature must be 64 bytes");
        require(_pubkey.length == 65, "PoACVerifier: pubkey must be 65 bytes");

        bytes32 r;
        bytes32 s;
        assembly {
            r := calldataload(_sig.offset)
            s := calldataload(add(_sig.offset, 32))
        }

        // Extract x, y from pubkey (skip 0x04 prefix byte)
        bytes32 x;
        bytes32 y;
        assembly {
            x := mload(add(_pubkey, 33))
            y := mload(add(_pubkey, 65))
        }

        // Gas guard: ensure we have enough gas to call the precompile without OOG
        // swallowing the error. 5,000 gas floor is conservative — the P256 precompile
        // consumes ~3,500 gas on IoTeX; Hardhat simulation ~2,000.
        require(gasleft() > 5000, "PoACVerifier: insufficient gas for P256 precompile");

        bytes memory input = abi.encodePacked(_digest, r, s, x, y);
        (bool success, bytes memory result) = P256_PRECOMPILE.staticcall(input);

        // Case 1: staticcall returned false — precompile failed or OOG inside call
        if (!success) {
            revert P256PrecompileCallFailed();
        }

        // Case 2: empty returndata — precompile not deployed at 0x0100 on this chain
        if (result.length == 0) {
            revert P256PrecompileEmptyReturn();
        }

        // Case 3: returndata present but shorter than 32 bytes — malformed precompile output
        if (result.length < 32) {
            revert P256PrecompileCallFailed();
        }

        // Case 4: precompile ran; result == 1 means valid, result == 0 means invalid
        uint256 valid;
        assembly {
            valid := mload(add(result, 32))
        }

        return valid == 1;
    }

    // -------------------------------------------------------------------------
    //  View functions
    // -------------------------------------------------------------------------

    function getChainHead(bytes32 _deviceId)
        external
        view
        returns (bytes32 lastRecordHash, uint32 lastCounter, bool initialized)
    {
        ChainState storage chain = _chainState[_deviceId];
        return (chain.lastRecordHash, chain.lastCounter, chain.initialized);
    }

    function getVerifiedCount(bytes32 _deviceId) external view returns (uint32 count) {
        return _chainState[_deviceId].verifiedCount;
    }

    function isRecordVerified(bytes32 _recordHash) external view returns (bool) {
        return verifiedRecords[_recordHash];
    }

    /**
     * @notice Returns the sensor commitment schema version for a verified record.
     *
     * @param _recordHash   SHA-256 hash of the PoAC body.
     * @return schemaVersion 1 = v1 environmental, 2 = v2 kinematic. 0 if not set.
     * @return isSet         False when record was verified via legacy verifyPoAC() call.
     */
    function getRecordSchema(bytes32 _recordHash)
        external view returns (uint8 schemaVersion, bool isSet)
    {
        return (recordSchemas[_recordHash], recordHasSchema[_recordHash]);
    }
}
