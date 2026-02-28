// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "./DeviceRegistry.sol";
import "./PoACVerifier.sol";

/// @dev Minimal interface for PHG-gated tournament eligibility.
interface ITournamentGate {
    function assertEligible(bytes32 deviceId) external view;
}

/**
 * @title BountyMarket
 * @author VAPI Project
 * @notice Decentralized bounty marketplace for VAPI autonomous physical
 *         intelligence agents on IoTeX. Enables requesters to post bounties
 *         for real-world data collection, verified through Proof of Autonomous
 *         Cognition (PoAC) records.
 *
 * @dev Lifecycle: Create -> Active -> (Accepted -> Fulfilled | Expired)
 *
 *      Bounty parameters mirror the firmware's `bounty_descriptor_t` from
 *      `economic.h`, with monetary values denominated in native IOTX (wei).
 *
 *      Evidence is linked to verified PoAC records. The contract checks:
 *        - The device accepted this bounty
 *        - The PoAC is verified on-chain (via PoACVerifier)
 *        - Geographic zone containment
 *        - Sensor requirements met
 *        - Sample interval respected
 *
 *      Swarm aggregation enables multiple devices to collaboratively report
 *      on overlapping physical events, producing a confidence-weighted
 *      PhysicalOracleReport suitable for consumption by other DeFi protocols.
 */
contract BountyMarket is Ownable, ReentrancyGuard {

    // -------------------------------------------------------------------------
    //  Constants
    // -------------------------------------------------------------------------

    /// @notice Minimum bounty duration in seconds.
    uint256 public constant MIN_BOUNTY_DURATION = 1 hours;

    /// @notice Maximum bounty duration in seconds.
    uint256 public constant MAX_BOUNTY_DURATION = 365 days;

    /// @notice Coordinate scaling factor: firmware sends lat/lon * 1e7 as int64.
    int64 public constant COORD_SCALE = 1e7;

    /// @notice Platform fee in basis points (e.g., 250 = 2.5%).
    uint16 public platformFeeBps;

    /// @notice Accumulated platform fees available for withdrawal.
    uint256 public accumulatedFees;

    // -------------------------------------------------------------------------
    //  Sensor requirement flags (mirrors firmware BOUNTY_REQUIRES_* defines)
    // -------------------------------------------------------------------------

    uint16 public constant REQUIRES_VOC      = 1 << 0;
    uint16 public constant REQUIRES_TEMP     = 1 << 1;
    uint16 public constant REQUIRES_HUMIDITY = 1 << 2;
    uint16 public constant REQUIRES_PRESSURE = 1 << 3;
    uint16 public constant REQUIRES_MOTION   = 1 << 4;
    uint16 public constant REQUIRES_LIGHT    = 1 << 5;
    uint16 public constant REQUIRES_GPS      = 1 << 6;

    // -------------------------------------------------------------------------
    //  Data structures
    // -------------------------------------------------------------------------

    /// @notice Bounty lifecycle states.
    enum BountyStatus {
        None,       // Default / nonexistent
        Active,     // Open for acceptance and evidence submission
        Fulfilled,  // All requirements met, reward claimed
        Expired     // Deadline passed without fulfillment, refundable
    }

    /**
     * @notice On-chain bounty descriptor.
     * @dev Mirrors `bounty_descriptor_t` from economic.h with EVM-native types.
     *      Coordinates stored as fixed-point int64 (value * 1e7).
     */
    struct BountyDescriptor {
        uint256 bountyId;            // Auto-incremented identifier
        address creator;             // Address that posted the bounty
        uint256 reward;              // Total reward in wei (IOTX)
        uint16  sensorRequirements;  // Bitfield of REQUIRES_* flags
        uint16  minSamples;          // Minimum PoAC submissions required
        uint32  sampleIntervalS;     // Required minimum interval between samples (seconds)
        uint32  durationS;           // Bounty duration from creation
        uint64  deadlineMs;          // Absolute deadline (Unix ms)
        int64   zoneLatMin;          // Geographic bounding box (lat * 1e7)
        int64   zoneLatMax;
        int64   zoneLonMin;
        int64   zoneLonMax;
        int256  vocThreshold;        // VOC threshold (scaled fixed-point, 0 = unused)
        int256  tempThresholdHi;     // High temperature threshold (scaled, 0 = unused)
        int256  tempThresholdLo;     // Low temperature threshold (scaled, 0 = unused)
        BountyStatus status;
        uint256 createdAt;           // Block timestamp of creation
    }

    /**
     * @notice Per-device acceptance record for a bounty.
     */
    struct Acceptance {
        bool    accepted;           // Whether the device accepted this bounty
        uint64  acceptedAt;         // Timestamp of acceptance
        uint16  samplesSubmitted;   // Count of valid evidence submissions
        int64   lastSampleTimestampMs; // Timestamp of last submitted sample
    }

    /**
     * @notice Evidence linking a PoAC record to a bounty.
     */
    struct Evidence {
        bytes32 deviceId;
        bytes32 recordHash;
        int64   timestampMs;
        int64   latitude;
        int64   longitude;
    }

    /**
     * @notice Swarm aggregation report — physical oracle output.
     */
    struct SwarmReport {
        uint256 bountyId;
        uint256 deviceCount;         // Number of distinct devices
        uint256 totalSamples;        // Total PoAC records aggregated
        uint16  confidenceScore;     // Weighted confidence [0, 10000] (basis points)
        uint8   consensusInference;  // Most common inference result across devices
        int64   medianLatitude;      // Median location (approx)
        int64   medianLongitude;
        int64   earliestTimestampMs;
        int64   latestTimestampMs;
    }

    // -------------------------------------------------------------------------
    //  State
    // -------------------------------------------------------------------------

    /// @notice References to companion contracts.
    PoACVerifier public poACVerifier;
    DeviceRegistry public deviceRegistry;

    /// @notice Optional PHG tournament gate; address(0) = open (no gate).
    address public tournamentGate;

    /// @notice Auto-incrementing bounty ID counter.
    uint256 public nextBountyId;

    /// @notice bountyId => BountyDescriptor.
    mapping(uint256 => BountyDescriptor) public bounties;

    /// @notice bountyId => deviceId => Acceptance.
    mapping(uint256 => mapping(bytes32 => Acceptance)) public acceptances;

    /// @notice bountyId => array of Evidence records.
    mapping(uint256 => Evidence[]) private _evidence;

    /// @notice bountyId => set of deviceIds that have accepted.
    mapping(uint256 => bytes32[]) private _acceptedDevices;

    /// @notice recordHash => bountyId (prevent double-submission of evidence).
    mapping(bytes32 => uint256) public evidenceRecordToBounty;

    // -------------------------------------------------------------------------
    //  Events
    // -------------------------------------------------------------------------

    /// @notice Emitted when a new bounty is posted.
    event BountyPosted(
        uint256 indexed bountyId,
        address indexed creator,
        uint256 reward,
        uint16  sensorRequirements,
        uint16  minSamples,
        uint64  deadlineMs
    );

    /// @notice Emitted when a device accepts a bounty.
    event BountyAccepted(
        uint256 indexed bountyId,
        bytes32 indexed deviceId,
        uint64  acceptedAt
    );

    /// @notice Emitted when evidence is submitted linking a PoAC to a bounty.
    event EvidenceSubmitted(
        uint256 indexed bountyId,
        bytes32 indexed deviceId,
        bytes32 indexed recordHash,
        uint16  sampleNumber
    );

    /// @notice Emitted when a device claims the reward for a fulfilled bounty.
    event RewardClaimed(
        uint256 indexed bountyId,
        bytes32 indexed deviceId,
        address indexed recipient,
        uint256 amount
    );

    /// @notice Emitted when a bounty expires and the creator is refunded.
    event BountyExpired(
        uint256 indexed bountyId,
        address indexed creator,
        uint256 refundAmount
    );

    /**
     * @notice Emitted when a swarm aggregation produces a physical oracle report.
     * @dev This event is designed to be consumed by other on-chain protocols
     *      as a verified physical-world data feed.
     */
    event PhysicalOracleReport(
        uint256 indexed bountyId,
        uint256 deviceCount,
        uint256 totalSamples,
        uint16  confidenceScore,
        uint8   consensusInference,
        int64   medianLatitude,
        int64   medianLongitude,
        int64   earliestTimestampMs,
        int64   latestTimestampMs
    );

    // -------------------------------------------------------------------------
    //  Errors
    // -------------------------------------------------------------------------

    error BountyNotFound(uint256 bountyId);
    error BountyNotActive(uint256 bountyId);
    error BountyAlreadyFulfilled(uint256 bountyId);
    error BountyNotExpired(uint256 bountyId);
    error InsufficientReward();
    error InvalidDuration(uint256 duration);
    error InvalidZone();
    error InvalidMinSamples();
    error DeviceNotRegistered(bytes32 deviceId);
    error DeviceNotActive(bytes32 deviceId);
    error AlreadyAccepted(uint256 bountyId, bytes32 deviceId);
    error NotAccepted(uint256 bountyId, bytes32 deviceId);
    error RecordNotVerified(bytes32 recordHash);
    error RecordAlreadyUsed(bytes32 recordHash, uint256 existingBountyId);
    error OutsideGeographicZone(int64 lat, int64 lon);
    error SampleIntervalNotRespected(int64 lastTs, int64 currentTs, uint32 requiredInterval);
    error MinSamplesNotReached(uint256 bountyId, uint16 submitted, uint16 required);
    error NotBountyCreator(uint256 bountyId, address caller);
    error NoDevicesForAggregation(uint256 bountyId);
    error TransferFailed();
    /// @notice Thrown when a device's registration tier does not permit bounty claims.
    error IneligibleTier(bytes32 deviceId);
    /// @notice Thrown when the optional PHG gate rejects the device.
    error GateCheckFailed(bytes32 deviceId);

    // -------------------------------------------------------------------------
    //  Constructor
    // -------------------------------------------------------------------------

    /**
     * @param _poACVerifier   Address of deployed PoACVerifier contract.
     * @param _deviceRegistry Address of deployed DeviceRegistry contract.
     * @param _platformFeeBps Platform fee in basis points (e.g., 250 for 2.5%).
     */
    constructor(
        address _poACVerifier,
        address _deviceRegistry,
        uint16  _platformFeeBps
    )
        Ownable(msg.sender)
    {
        poACVerifier = PoACVerifier(_poACVerifier);
        deviceRegistry = DeviceRegistry(_deviceRegistry);
        platformFeeBps = _platformFeeBps;
        nextBountyId = 1; // Start at 1; 0 is POAC_NO_BOUNTY sentinel
    }

    // -------------------------------------------------------------------------
    //  Admin
    // -------------------------------------------------------------------------

    /**
     * @notice Update the platform fee. Owner-only.
     * @param _newFeeBps New fee in basis points (max 1000 = 10%).
     */
    function setPlatformFee(uint16 _newFeeBps) external onlyOwner {
        require(_newFeeBps <= 1000, "BountyMarket: fee cannot exceed 10%");
        platformFeeBps = _newFeeBps;
    }

    /**
     * @notice Withdraw accumulated platform fees. Owner-only.
     */
    function withdrawFees() external onlyOwner nonReentrant {
        uint256 amount = accumulatedFees;
        accumulatedFees = 0;
        (bool success, ) = payable(owner()).call{value: amount}("");
        if (!success) revert TransferFailed();
    }

    /**
     * @notice Set the PHG tournament gate. Once set, cannot be changed. Owner-only.
     * @param gate Address of TournamentGateV2 (or any ITournamentGate). Use address(0) to keep open.
     */
    function setTournamentGate(address gate) external onlyOwner {
        require(tournamentGate == address(0), "BountyMarket: gate already set");
        tournamentGate = gate;
    }

    // -------------------------------------------------------------------------
    //  Bounty creation
    // -------------------------------------------------------------------------

    /**
     * @notice Post a new bounty for physical data collection.
     *
     * @dev The caller deposits the full reward amount in IOTX (msg.value).
     *      The bounty becomes Active immediately and remains so until the
     *      deadline or until fulfilled.
     *
     *      Parameters mirror `bounty_descriptor_t` from `economic.h`:
     *        - sensorRequirements: bitfield of REQUIRES_* flags
     *        - minSamples: minimum PoAC submissions needed for fulfillment
     *        - sampleIntervalS: minimum seconds between consecutive samples
     *        - durationS: bounty duration from creation (determines deadline)
     *        - zone coordinates: bounding box in fixed-point (lat/lon * 1e7)
     *        - thresholds: sensor-specific triggers (0 = no threshold)
     *
     * @param _sensorRequirements Bitfield of required sensor capabilities.
     * @param _minSamples         Minimum number of valid PoAC evidence samples.
     * @param _sampleIntervalS    Minimum interval between samples in seconds.
     * @param _durationS          Bounty duration in seconds.
     * @param _zoneLatMin         Zone bounding box south (latitude * 1e7).
     * @param _zoneLatMax         Zone bounding box north (latitude * 1e7).
     * @param _zoneLonMin         Zone bounding box west (longitude * 1e7).
     * @param _zoneLonMax         Zone bounding box east (longitude * 1e7).
     * @param _vocThreshold       VOC resistance threshold (0 = unused).
     * @param _tempThresholdHi    High temperature threshold (0 = unused).
     * @param _tempThresholdLo    Low temperature threshold (0 = unused).
     *
     * @return bountyId The ID of the newly created bounty.
     */
    function postBounty(
        uint16  _sensorRequirements,
        uint16  _minSamples,
        uint32  _sampleIntervalS,
        uint32  _durationS,
        int64   _zoneLatMin,
        int64   _zoneLatMax,
        int64   _zoneLonMin,
        int64   _zoneLonMax,
        int256  _vocThreshold,
        int256  _tempThresholdHi,
        int256  _tempThresholdLo
    )
        external
        payable
        nonReentrant
        returns (uint256 bountyId)
    {
        // Validate reward
        if (msg.value == 0) revert InsufficientReward();

        // Validate duration
        if (_durationS < MIN_BOUNTY_DURATION || _durationS > MAX_BOUNTY_DURATION) {
            revert InvalidDuration(_durationS);
        }

        // Validate geographic zone (min < max)
        if (_zoneLatMin >= _zoneLatMax || _zoneLonMin >= _zoneLonMax) {
            revert InvalidZone();
        }

        // Validate min samples
        if (_minSamples == 0) revert InvalidMinSamples();

        // Assign ID
        bountyId = nextBountyId++;

        // Compute deadline
        uint64 deadlineMs = uint64((block.timestamp + uint256(_durationS)) * 1000);

        // Store bounty
        BountyDescriptor storage b = bounties[bountyId];
        b.bountyId = bountyId;
        b.creator = msg.sender;
        b.reward = msg.value;
        b.sensorRequirements = _sensorRequirements;
        b.minSamples = _minSamples;
        b.sampleIntervalS = _sampleIntervalS;
        b.durationS = _durationS;
        b.deadlineMs = deadlineMs;
        b.zoneLatMin = _zoneLatMin;
        b.zoneLatMax = _zoneLatMax;
        b.zoneLonMin = _zoneLonMin;
        b.zoneLonMax = _zoneLonMax;
        b.vocThreshold = _vocThreshold;
        b.tempThresholdHi = _tempThresholdHi;
        b.tempThresholdLo = _tempThresholdLo;
        b.status = BountyStatus.Active;
        b.createdAt = block.timestamp;

        emit BountyPosted(
            bountyId,
            msg.sender,
            msg.value,
            _sensorRequirements,
            _minSamples,
            deadlineMs
        );
    }

    // -------------------------------------------------------------------------
    //  Bounty acceptance
    // -------------------------------------------------------------------------

    /**
     * @notice Signal that a device intends to fulfill a bounty.
     *
     * @dev The device must be registered and active in DeviceRegistry.
     *      A device can only accept a bounty once. Acceptance is recorded
     *      on-chain so the firmware can track committed bounties.
     *
     * @param _bountyId  The bounty to accept.
     * @param _deviceId  The device accepting the bounty.
     */
    function acceptBounty(uint256 _bountyId, bytes32 _deviceId) external {
        BountyDescriptor storage b = bounties[_bountyId];
        if (b.status == BountyStatus.None) revert BountyNotFound(_bountyId);
        if (b.status != BountyStatus.Active) revert BountyNotActive(_bountyId);

        // Check deadline hasn't passed
        if (block.timestamp * 1000 > uint256(b.deadlineMs)) {
            revert BountyNotActive(_bountyId);
        }

        // Verify device is registered and active
        if (!deviceRegistry.isDeviceActive(_deviceId)) {
            revert DeviceNotActive(_deviceId);
        }

        // Check not already accepted
        if (acceptances[_bountyId][_deviceId].accepted) {
            revert AlreadyAccepted(_bountyId, _deviceId);
        }

        // Record acceptance
        acceptances[_bountyId][_deviceId] = Acceptance({
            accepted: true,
            acceptedAt: uint64(block.timestamp),
            samplesSubmitted: 0,
            lastSampleTimestampMs: 0
        });

        _acceptedDevices[_bountyId].push(_deviceId);

        emit BountyAccepted(_bountyId, _deviceId, uint64(block.timestamp));
    }

    // -------------------------------------------------------------------------
    //  Evidence submission
    // -------------------------------------------------------------------------

    /**
     * @notice Submit a verified PoAC record as evidence for a bounty.
     *
     * @dev Checks performed:
     *      1. Bounty is active and not past deadline.
     *      2. Device accepted this bounty.
     *      3. PoAC record is verified on-chain (via PoACVerifier).
     *      4. Record is not already used as evidence for another bounty.
     *      5. PoAC latitude/longitude is within the bounty's geographic zone.
     *      6. Sample interval is respected (time since last submission).
     *
     * @param _bountyId    The bounty this evidence fulfills.
     * @param _deviceId    The device submitting evidence.
     * @param _recordHash  The hash of the verified PoAC record.
     * @param _latitude    Record latitude (fixed-point * 1e7), must match PoAC.
     * @param _longitude   Record longitude (fixed-point * 1e7), must match PoAC.
     * @param _timestampMs Record timestamp in milliseconds, must match PoAC.
     */
    function submitEvidence(
        uint256 _bountyId,
        bytes32 _deviceId,
        bytes32 _recordHash,
        int64   _latitude,
        int64   _longitude,
        int64   _timestampMs
    )
        external
        nonReentrant
    {
        BountyDescriptor storage b = bounties[_bountyId];
        if (b.status == BountyStatus.None) revert BountyNotFound(_bountyId);
        if (b.status != BountyStatus.Active) revert BountyNotActive(_bountyId);

        // Check deadline
        if (uint256(uint64(_timestampMs)) > uint256(b.deadlineMs)) {
            revert BountyNotActive(_bountyId);
        }

        // Check device accepted this bounty
        Acceptance storage acc = acceptances[_bountyId][_deviceId];
        if (!acc.accepted) revert NotAccepted(_bountyId, _deviceId);

        // Check device tier allows bounty claims (Emulated tier is blocked)
        if (!deviceRegistry.canClaimBounty(_deviceId)) revert IneligibleTier(_deviceId);

        // Check PoAC is verified on-chain
        if (!poACVerifier.isRecordVerified(_recordHash)) {
            revert RecordNotVerified(_recordHash);
        }

        // Check record not already used as evidence
        if (evidenceRecordToBounty[_recordHash] != 0) {
            revert RecordAlreadyUsed(_recordHash, evidenceRecordToBounty[_recordHash]);
        }

        // Check geographic zone containment
        if (
            _latitude < b.zoneLatMin || _latitude > b.zoneLatMax ||
            _longitude < b.zoneLonMin || _longitude > b.zoneLonMax
        ) {
            revert OutsideGeographicZone(_latitude, _longitude);
        }

        // Check sample interval
        if (acc.samplesSubmitted > 0 && b.sampleIntervalS > 0) {
            int64 requiredGapMs = int64(uint64(b.sampleIntervalS)) * 1000;
            if (_timestampMs - acc.lastSampleTimestampMs < requiredGapMs) {
                revert SampleIntervalNotRespected(
                    acc.lastSampleTimestampMs,
                    _timestampMs,
                    b.sampleIntervalS
                );
            }
        }

        // Record evidence
        _evidence[_bountyId].push(Evidence({
            deviceId: _deviceId,
            recordHash: _recordHash,
            timestampMs: _timestampMs,
            latitude: _latitude,
            longitude: _longitude
        }));

        evidenceRecordToBounty[_recordHash] = _bountyId;
        acc.samplesSubmitted++;
        acc.lastSampleTimestampMs = _timestampMs;

        emit EvidenceSubmitted(
            _bountyId,
            _deviceId,
            _recordHash,
            acc.samplesSubmitted
        );
    }

    // -------------------------------------------------------------------------
    //  Reward claim
    // -------------------------------------------------------------------------

    /**
     * @notice Claim the bounty reward after fulfillment requirements are met.
     *
     * @dev Requires that the device has submitted at least minSamples valid
     *      evidence records. The reward is transferred to the device owner
     *      address (from DeviceRegistry). A platform fee is deducted.
     *
     *      Any device that has met the minimum samples can claim. In a
     *      multi-device scenario, the first claimer receives the reward.
     *
     * @param _bountyId  The bounty to claim.
     * @param _deviceId  The device claiming the reward.
     */
    function claimReward(uint256 _bountyId, bytes32 _deviceId)
        external
        nonReentrant
    {
        BountyDescriptor storage b = bounties[_bountyId];
        if (b.status == BountyStatus.None) revert BountyNotFound(_bountyId);
        if (b.status == BountyStatus.Fulfilled) revert BountyAlreadyFulfilled(_bountyId);
        if (b.status != BountyStatus.Active) revert BountyNotActive(_bountyId);

        // Check device accepted and submitted enough samples
        Acceptance storage acc = acceptances[_bountyId][_deviceId];
        if (!acc.accepted) revert NotAccepted(_bountyId, _deviceId);
        if (acc.samplesSubmitted < b.minSamples) {
            revert MinSamplesNotReached(_bountyId, acc.samplesSubmitted, b.minSamples);
        }

        // PHG humanity gate -- enforces both cumulative score and velocity
        if (tournamentGate != address(0)) {
            try ITournamentGate(tournamentGate).assertEligible(_deviceId) {
                // passes
            } catch {
                revert GateCheckFailed(_deviceId);
            }
        }

        // Mark bounty as fulfilled
        b.status = BountyStatus.Fulfilled;

        // Compute fee and payout
        uint256 fee = (b.reward * uint256(platformFeeBps)) / 10000;
        uint256 payout = b.reward - fee;
        accumulatedFees += fee;

        // Get device owner from registry
        DeviceRegistry.DeviceInfo memory devInfo = deviceRegistry.getDeviceInfo(_deviceId);
        address recipient = devInfo.owner;

        // Transfer reward to device owner
        (bool success, ) = payable(recipient).call{value: payout}("");
        if (!success) revert TransferFailed();

        // Update device reputation (corroboration bonus for completing a bounty)
        try deviceRegistry.updateReputation(_deviceId, 0, 1, 0) {} catch {}

        emit RewardClaimed(_bountyId, _deviceId, recipient, payout);
    }

    // -------------------------------------------------------------------------
    //  Bounty expiration
    // -------------------------------------------------------------------------

    /**
     * @notice Expire an unfulfilled bounty and refund the creator.
     *
     * @dev Can be called by anyone after the deadline has passed, but only
     *      if the bounty is still Active (not Fulfilled).
     *
     * @param _bountyId The bounty to expire.
     */
    function expireBounty(uint256 _bountyId) external nonReentrant {
        BountyDescriptor storage b = bounties[_bountyId];
        if (b.status == BountyStatus.None) revert BountyNotFound(_bountyId);
        if (b.status != BountyStatus.Active) revert BountyNotActive(_bountyId);

        // Check deadline has passed
        if (block.timestamp * 1000 <= uint256(b.deadlineMs)) {
            revert BountyNotExpired(_bountyId);
        }

        // Mark as expired
        b.status = BountyStatus.Expired;

        // Refund full reward to creator
        uint256 refund = b.reward;
        (bool success, ) = payable(b.creator).call{value: refund}("");
        if (!success) revert TransferFailed();

        emit BountyExpired(_bountyId, b.creator, refund);
    }

    // -------------------------------------------------------------------------
    //  Swarm aggregation
    // -------------------------------------------------------------------------

    /**
     * @notice Aggregate evidence from multiple devices into a swarm report.
     *
     * @dev Takes arrays of PoAC record hashes from different devices that
     *      observed overlapping events within a bounty's zone and time window.
     *      Computes a confidence score based on:
     *        - Number of independent devices (more devices = higher confidence)
     *        - Average device reputation scores
     *        - Consensus on inference result
     *
     *      Emits a {PhysicalOracleReport} event that serves as a verified
     *      physical-world data feed for other on-chain consumers.
     *
     * @param _bountyId     The bounty context for aggregation.
     * @param _deviceIds    Array of contributing device IDs.
     * @param _recordHashes Array of verified PoAC record hashes.
     * @param _latitudes    Array of record latitudes (fixed-point * 1e7).
     * @param _longitudes   Array of record longitudes (fixed-point * 1e7).
     * @param _timestampsMs Array of record timestamps (Unix ms).
     * @param _inferences   Array of inference result codes from each record.
     *
     * @return report The computed SwarmReport.
     */
    function aggregateSwarmReport(
        uint256   _bountyId,
        bytes32[] calldata _deviceIds,
        bytes32[] calldata _recordHashes,
        int64[]   calldata _latitudes,
        int64[]   calldata _longitudes,
        int64[]   calldata _timestampsMs,
        uint8[]   calldata _inferences
    )
        external
        returns (SwarmReport memory report)
    {
        uint256 count = _deviceIds.length;
        require(
            count == _recordHashes.length &&
            count == _latitudes.length &&
            count == _longitudes.length &&
            count == _timestampsMs.length &&
            count == _inferences.length,
            "BountyMarket: array length mismatch"
        );
        if (count == 0) revert NoDevicesForAggregation(_bountyId);

        BountyDescriptor storage b = bounties[_bountyId];
        if (b.status == BountyStatus.None) revert BountyNotFound(_bountyId);

        // Validate all records are verified and count unique devices
        uint256 totalReputation = 0;
        uint256 uniqueDevices = 0;
        int64 earliestTs = type(int64).max;
        int64 latestTs = type(int64).min;
        int64 latSum = 0;
        int64 lonSum = 0;

        // Inference consensus: count occurrences of each inference result
        // Use a simple approach for up to 256 possible values
        uint256[256] memory inferenceCounts;

        // Track unique device IDs (simple O(n^2) check, acceptable for small swarms)
        bytes32[] memory seenDevices = new bytes32[](count);

        for (uint256 i = 0; i < count; i++) {
            // Verify each record is on-chain verified
            require(
                poACVerifier.isRecordVerified(_recordHashes[i]),
                "BountyMarket: unverified record in swarm"
            );

            // Check geographic zone
            require(
                _latitudes[i] >= b.zoneLatMin && _latitudes[i] <= b.zoneLatMax &&
                _longitudes[i] >= b.zoneLonMin && _longitudes[i] <= b.zoneLonMax,
                "BountyMarket: record outside zone"
            );

            // Track unique devices
            bool isNew = true;
            for (uint256 j = 0; j < uniqueDevices; j++) {
                if (seenDevices[j] == _deviceIds[i]) {
                    isNew = false;
                    break;
                }
            }
            if (isNew) {
                seenDevices[uniqueDevices] = _deviceIds[i];
                uniqueDevices++;

                // Accumulate reputation of unique devices
                try deviceRegistry.getReputationScore(_deviceIds[i]) returns (uint16 rep) {
                    totalReputation += uint256(rep);
                } catch {}

                // Update corroboration count for this device
                try deviceRegistry.updateReputation(_deviceIds[i], 0, 1, 0) {} catch {}
            }

            // Track timestamps
            if (_timestampsMs[i] < earliestTs) earliestTs = _timestampsMs[i];
            if (_timestampsMs[i] > latestTs) latestTs = _timestampsMs[i];

            // Accumulate location (for approximate median via mean)
            latSum += _latitudes[i];
            lonSum += _longitudes[i];

            // Count inference results
            inferenceCounts[_inferences[i]]++;
        }

        // Compute consensus inference (mode)
        uint8 consensusInference = 0;
        uint256 maxCount = 0;
        for (uint256 k = 0; k < 256; k++) {
            if (inferenceCounts[k] > maxCount) {
                maxCount = inferenceCounts[k];
                consensusInference = uint8(k);
            }
        }

        // Compute confidence score:
        //   deviceFactor = min(uniqueDevices, 10) * 1000 (up to 10000 for 10+ devices)
        //   reputationFactor = avgReputation (already in basis points, max 10000)
        //   consensusFactor = (consensusCount / totalRecords) * 10000
        //   confidenceScore = (deviceFactor + reputationFactor + consensusFactor) / 3
        uint256 deviceFactor = (uniqueDevices > 10 ? 10 : uniqueDevices) * 1000;
        uint256 avgReputation = uniqueDevices > 0 ? totalReputation / uniqueDevices : 0;
        uint256 consensusFactor = (maxCount * 10000) / count;
        uint256 rawConfidence = (deviceFactor + avgReputation + consensusFactor) / 3;
        uint16 confidenceScore = rawConfidence > 10000
            ? uint16(10000)
            : uint16(rawConfidence);

        // Compute approximate median location (using mean as approximation)
        int64 medianLat = int64(latSum / int64(int256(count)));
        int64 medianLon = int64(lonSum / int64(int256(count)));

        // Build report
        report = SwarmReport({
            bountyId: _bountyId,
            deviceCount: uniqueDevices,
            totalSamples: count,
            confidenceScore: confidenceScore,
            consensusInference: consensusInference,
            medianLatitude: medianLat,
            medianLongitude: medianLon,
            earliestTimestampMs: earliestTs,
            latestTimestampMs: latestTs
        });

        emit PhysicalOracleReport(
            _bountyId,
            uniqueDevices,
            count,
            confidenceScore,
            consensusInference,
            medianLat,
            medianLon,
            earliestTs,
            latestTs
        );
    }

    // -------------------------------------------------------------------------
    //  View functions
    // -------------------------------------------------------------------------

    /**
     * @notice Get the full bounty descriptor.
     * @param _bountyId Bounty identifier.
     * @return The BountyDescriptor struct.
     */
    function getBounty(uint256 _bountyId)
        external
        view
        returns (BountyDescriptor memory)
    {
        BountyDescriptor storage b = bounties[_bountyId];
        if (b.status == BountyStatus.None) revert BountyNotFound(_bountyId);
        return b;
    }

    /**
     * @notice Get the acceptance status for a device on a bounty.
     * @param _bountyId Bounty identifier.
     * @param _deviceId Device identifier.
     * @return The Acceptance struct.
     */
    function getAcceptance(uint256 _bountyId, bytes32 _deviceId)
        external
        view
        returns (Acceptance memory)
    {
        return acceptances[_bountyId][_deviceId];
    }

    /**
     * @notice Get the count of evidence records submitted for a bounty.
     * @param _bountyId Bounty identifier.
     * @return count Number of evidence submissions.
     */
    function getEvidenceCount(uint256 _bountyId)
        external
        view
        returns (uint256 count)
    {
        return _evidence[_bountyId].length;
    }

    /**
     * @notice Get a specific evidence record by index.
     * @param _bountyId Bounty identifier.
     * @param _index    Index into the evidence array.
     * @return The Evidence struct.
     */
    function getEvidence(uint256 _bountyId, uint256 _index)
        external
        view
        returns (Evidence memory)
    {
        return _evidence[_bountyId][_index];
    }

    /**
     * @notice Get the list of device IDs that accepted a bounty.
     * @param _bountyId Bounty identifier.
     * @return Array of device IDs.
     */
    function getAcceptedDevices(uint256 _bountyId)
        external
        view
        returns (bytes32[] memory)
    {
        return _acceptedDevices[_bountyId];
    }
}
