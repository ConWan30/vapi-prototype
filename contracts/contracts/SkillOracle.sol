// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./PoACVerifier.sol";
import "./DeviceRegistry.sol";

/**
 * @title SkillOracle
 * @author VAPI Project
 * @notice On-chain skill rating oracle for esports and gaming DAOs.
 *
 * @dev Aggregates verified PoAC records into an ELO-inspired skill rating
 *      per device. Gaming DAOs can query this oracle for:
 *        - Tournament eligibility gating
 *        - Governance weight by proven skill
 *        - Fair reward distribution based on performance
 *
 *      Rating algorithm:
 *        - Base rating starts at 1000 (Bronze)
 *        - NOMINAL gameplay: +5 per verified record
 *        - SKILLED gameplay:  +12 per verified record
 *        - CHEAT detection:   -200 per cheat flag (harsh penalty)
 *        - Confidence scaling: gain *= confidence / 255
 *        - Rating floor: 0, ceiling: 3000
 *
 *      Tier brackets:
 *        Bronze:   0 - 999
 *        Silver:   1000 - 1499
 *        Gold:     1500 - 1999
 *        Platinum: 2000 - 2499
 *        Diamond:  2500 - 3000
 *
 *      Only processes records already verified by {PoACVerifier}.
 */
contract SkillOracle is Ownable {

    // ─── Enums ──────────────────────────────────────────────────────────

    enum SkillTier { Bronze, Silver, Gold, Platinum, Diamond }

    // ─── Data Structures ────────────────────────────────────────────────

    struct SkillProfile {
        uint32  rating;          // ELO-like rating [0, 3000]
        uint32  gamesPlayed;     // Total verified records processed
        uint32  cleanGames;      // Records with no cheat flags
        uint32  cheatFlags;      // Total cheat detections
        uint64  lastUpdated;     // Block timestamp of last update
        bool    initialized;     // True after first record
    }

    // ─── Constants ──────────────────────────────────────────────────────

    uint32 public constant INITIAL_RATING = 1000;
    uint32 public constant MAX_RATING = 3000;
    uint32 public constant NOMINAL_GAIN = 5;
    uint32 public constant SKILLED_GAIN = 12;
    uint32 public constant CHEAT_PENALTY = 200;

    /// @notice Default minimum block interval between rating updates per device.
    /// @dev    At IoTeX's ~5s block time, 1 block ≈ 5 seconds between updates.
    ///         Prevents same-block burst manipulation (submitting all historical
    ///         records in one block to spike rating for tournament eligibility).
    uint256 public constant DEFAULT_MIN_INTERVAL = 1;

    // Inference result code ranges (from firmware)
    uint8 public constant INFER_NOMINAL = 0x20;
    uint8 public constant INFER_SKILLED = 0x21;
    uint8 public constant INFER_CHEAT_MIN = 0x22;
    uint8 public constant INFER_CHEAT_MAX = 0x29;

    // ─── State ──────────────────────────────────────────────────────────

    PoACVerifier public poACVerifier;
    mapping(bytes32 => SkillProfile) private _profiles;
    mapping(bytes32 => bool) public processedRecords;
    uint256 public totalProfileCount;

    // ─── Rate Limiting State ─────────────────────────────────────────────

    /// @notice Minimum blocks between successive rating updates for the same device.
    /// @dev    Owner-adjustable. Set to 0 to disable rate limiting.
    uint256 public minUpdateInterval;

    /// @notice Last block number at which a device received a rating update.
    mapping(bytes32 => uint256) private _lastUpdateBlock;

    // ─── Events ─────────────────────────────────────────────────────────

    event SkillRatingUpdated(
        bytes32 indexed deviceId,
        uint32  oldRating,
        uint32  newRating,
        SkillTier tier,
        uint32  gamesPlayed
    );

    // ─── Errors ─────────────────────────────────────────────────────────

    error RecordNotVerified(bytes32 recordHash);
    error RecordAlreadyProcessed(bytes32 recordHash);
    error RateLimitExceeded(bytes32 deviceId, uint256 currentBlock, uint256 nextAllowedBlock);

    // ─── Constructor ────────────────────────────────────────────────────

    constructor(address _poACVerifier) Ownable(msg.sender) {
        poACVerifier = PoACVerifier(_poACVerifier);
        minUpdateInterval = DEFAULT_MIN_INTERVAL;
    }

    // ─── Core Functions ─────────────────────────────────────────────────

    /**
     * @notice Update a device's skill rating based on a verified PoAC record.
     *
     * @param _deviceId    Device identifier.
     * @param _recordHash  SHA-256 hash of the verified PoAC body.
     * @param _inferenceResult  The inference code from the PoAC record.
     * @param _confidence  The confidence value [0-255] from the PoAC record.
     */
    function updateSkillRating(
        bytes32 _deviceId,
        bytes32 _recordHash,
        uint8   _inferenceResult,
        uint8   _confidence
    ) external {
        // 1. Verify the record exists on-chain
        if (!poACVerifier.isRecordVerified(_recordHash)) {
            revert RecordNotVerified(_recordHash);
        }

        // 2. Prevent double-counting
        if (processedRecords[_recordHash]) {
            revert RecordAlreadyProcessed(_recordHash);
        }

        // 3. Rate limit: enforce minimum block interval between updates per device.
        //    Prevents burst submission of historical records to spike ratings.
        //    Different devices are independent — cross-device updates are not rate-limited.
        uint256 last = _lastUpdateBlock[_deviceId];
        if (last != 0 && block.number < last + minUpdateInterval) {
            revert RateLimitExceeded(_deviceId, block.number, last + minUpdateInterval);
        }

        // State changes (after all checks to avoid partial-write on revert)
        processedRecords[_recordHash] = true;
        _lastUpdateBlock[_deviceId] = block.number;

        // 3. Initialize profile if needed
        SkillProfile storage profile = _profiles[_deviceId];
        if (!profile.initialized) {
            profile.rating = INITIAL_RATING;
            profile.initialized = true;
            totalProfileCount++;
        }

        uint32 oldRating = profile.rating;

        // 4. Compute rating delta
        if (_inferenceResult >= INFER_CHEAT_MIN && _inferenceResult <= INFER_CHEAT_MAX) {
            // Cheat detected — harsh penalty
            profile.cheatFlags++;
            if (profile.rating > CHEAT_PENALTY) {
                profile.rating -= CHEAT_PENALTY;
            } else {
                profile.rating = 0;
            }
        } else if (_inferenceResult == INFER_SKILLED) {
            // Skilled play — larger gain, scaled by confidence
            uint32 gain = (SKILLED_GAIN * uint32(_confidence)) / 255;
            if (gain == 0) gain = 1;
            profile.rating += gain;
            profile.cleanGames++;
        } else {
            // Nominal play — base gain, scaled by confidence
            uint32 gain = (NOMINAL_GAIN * uint32(_confidence)) / 255;
            if (gain == 0) gain = 1;
            profile.rating += gain;
            profile.cleanGames++;
        }

        // 5. Clamp to [0, MAX_RATING]
        if (profile.rating > MAX_RATING) {
            profile.rating = MAX_RATING;
        }

        profile.gamesPlayed++;
        profile.lastUpdated = uint64(block.timestamp);

        // 6. Emit event
        emit SkillRatingUpdated(
            _deviceId, oldRating, profile.rating,
            _getTier(profile.rating), profile.gamesPlayed
        );
    }

    // ─── Admin ──────────────────────────────────────────────────────────

    /**
     * @notice Set the minimum block interval between rating updates for any device.
     * @dev    Set to 0 to disable rate limiting entirely.
     *         At IoTeX's ~5s block time, 12 blocks ≈ 60s minimum between updates.
     */
    function setMinUpdateInterval(uint256 _blocks) external onlyOwner {
        minUpdateInterval = _blocks;
    }

    // ─── View Functions ─────────────────────────────────────────────────

    function getSkillRating(bytes32 _deviceId)
        external view returns (uint32 rating, uint32 gamesPlayed, uint64 lastUpdated)
    {
        SkillProfile storage p = _profiles[_deviceId];
        return (p.rating, p.gamesPlayed, p.lastUpdated);
    }

    function getSkillProfile(bytes32 _deviceId)
        external view returns (SkillProfile memory)
    {
        return _profiles[_deviceId];
    }

    function getSkillTier(bytes32 _deviceId)
        external view returns (SkillTier)
    {
        return _getTier(_profiles[_deviceId].rating);
    }

    // ─── Internal ───────────────────────────────────────────────────────

    function _getTier(uint32 _rating) internal pure returns (SkillTier) {
        if (_rating >= 2500) return SkillTier.Diamond;
        if (_rating >= 2000) return SkillTier.Platinum;
        if (_rating >= 1500) return SkillTier.Gold;
        if (_rating >= 1000) return SkillTier.Silver;
        return SkillTier.Bronze;
    }
}
