// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title DeviceRegistry
 * @author VAPI Project
 * @notice Manages registration and reputation of VAPI autonomous devices on IoTeX.
 *
 * @dev Each device is identified by a `deviceId` derived as keccak256 of its
 *      uncompressed SEC1 P256 public key (65 bytes: 0x04 || x || y).
 *
 *      IoTeX supports ECDSA-P256 signature verification via a precompile at
 *      address 0x0100. The EVM does not natively support P256, so all on-chain
 *      signature checks MUST use this precompile. See {PoACVerifier} for usage.
 *
 *      Registration requires a deposit to prevent Sybil attacks. The deposit is
 *      held for the lifetime of the device registration and can be reclaimed
 *      upon deactivation after a cooldown period.
 *
 *      Integrates with IoTeX ioID: the deviceId concept maps to the ioID
 *      decentralized identity, enabling cross-protocol device lookup.
 */
contract DeviceRegistry is Ownable, ReentrancyGuard {

    // -------------------------------------------------------------------------
    //  Constants
    // -------------------------------------------------------------------------

    /// @notice Length of an uncompressed SEC1 P256 public key (0x04 || x || y).
    uint256 public constant PUBKEY_LENGTH = 65;

    /// @notice Minimum registration deposit in wei (anti-Sybil).
    uint256 public minimumDeposit;

    /// @notice Cooldown period after deactivation before deposit can be withdrawn.
    uint256 public constant DEACTIVATION_COOLDOWN = 7 days;

    /// @notice Maximum value for the reputation score (basis-point scale, 10000 = 100%).
    uint16 public constant MAX_REPUTATION = 10000;

    // -------------------------------------------------------------------------
    //  Data structures
    // -------------------------------------------------------------------------

    /**
     * @notice On-chain metadata for a registered VAPI device.
     * @param owner              The Ethereum address that registered this device.
     * @param registeredAt       Block timestamp of registration.
     * @param deactivatedAt      Block timestamp of deactivation (0 if active).
     * @param deposit            Amount of IOTX deposited at registration.
     * @param active             Whether the device is currently active.
     * @param pubkey             Uncompressed SEC1 P256 public key (65 bytes).
     * @param verifiedPoACCount  Total number of PoAC records verified on-chain for this device.
     * @param corroborationCount Number of times this device's reports were corroborated by swarm.
     * @param disputeCount       Number of disputes raised against this device.
     * @param reputationScore    Computed reputation score [0, MAX_REPUTATION].
     */
    struct DeviceInfo {
        address owner;
        uint64  registeredAt;
        uint64  deactivatedAt;
        uint256 deposit;
        bool    active;
        bytes   pubkey;
        uint32  verifiedPoACCount;
        uint32  corroborationCount;
        uint32  disputeCount;
        uint16  reputationScore;
    }

    // -------------------------------------------------------------------------
    //  State
    // -------------------------------------------------------------------------

    /// @notice deviceId => DeviceInfo. deviceId = keccak256(pubkey).
    mapping(bytes32 => DeviceInfo) private _devices;

    /// @notice Addresses authorized to call updateReputation (e.g. PoACVerifier, BountyMarket).
    mapping(address => bool) public reputationUpdaters;

    /// @notice Total number of registered devices (including deactivated).
    uint256 public deviceCount;

    // -------------------------------------------------------------------------
    //  Events
    // -------------------------------------------------------------------------

    /// @notice Emitted when a new device is registered.
    event DeviceRegistered(
        bytes32 indexed deviceId,
        address indexed owner,
        uint256 deposit,
        uint64  registeredAt
    );

    /// @notice Emitted when a device is deactivated by its owner.
    event DeviceDeactivated(
        bytes32 indexed deviceId,
        address indexed owner,
        uint64  deactivatedAt
    );

    /// @notice Emitted when a device's reputation score is updated.
    event ReputationUpdated(
        bytes32 indexed deviceId,
        uint16  oldScore,
        uint16  newScore,
        uint32  verifiedPoACCount,
        uint32  corroborationCount,
        uint32  disputeCount
    );

    /// @notice Emitted when deposit is withdrawn after deactivation + cooldown.
    event DepositWithdrawn(
        bytes32 indexed deviceId,
        address indexed owner,
        uint256 amount
    );

    // -------------------------------------------------------------------------
    //  Errors
    // -------------------------------------------------------------------------

    error InvalidPubkeyLength(uint256 provided, uint256 expected);
    error InvalidPubkeyPrefix(uint8 provided);
    error DeviceAlreadyRegistered(bytes32 deviceId);
    error InsufficientDeposit(uint256 provided, uint256 required);
    error DeviceNotFound(bytes32 deviceId);
    error DeviceNotActive(bytes32 deviceId);
    error NotDeviceOwner(bytes32 deviceId, address caller);
    error CooldownNotElapsed(bytes32 deviceId, uint256 availableAt);
    error DepositAlreadyWithdrawn(bytes32 deviceId);
    error NotAuthorizedUpdater(address caller);

    // -------------------------------------------------------------------------
    //  Modifiers
    // -------------------------------------------------------------------------

    /// @dev Restricts access to authorized reputation updaters or the contract owner.
    modifier onlyReputationUpdater() {
        if (!reputationUpdaters[msg.sender] && msg.sender != owner()) {
            revert NotAuthorizedUpdater(msg.sender);
        }
        _;
    }

    // -------------------------------------------------------------------------
    //  Constructor
    // -------------------------------------------------------------------------

    /**
     * @param _minimumDeposit Initial minimum deposit in wei for device registration.
     */
    constructor(uint256 _minimumDeposit) Ownable(msg.sender) {
        minimumDeposit = _minimumDeposit;
    }

    // -------------------------------------------------------------------------
    //  Admin functions
    // -------------------------------------------------------------------------

    /**
     * @notice Update the minimum registration deposit. Owner-only.
     * @param _newMinimum New minimum deposit in wei.
     */
    function setMinimumDeposit(uint256 _newMinimum) external onlyOwner {
        minimumDeposit = _newMinimum;
    }

    /**
     * @notice Grant or revoke reputation-updater role (for PoACVerifier, BountyMarket).
     * @param _updater Address to grant/revoke.
     * @param _authorized True to grant, false to revoke.
     */
    function setReputationUpdater(address _updater, bool _authorized) external onlyOwner {
        reputationUpdaters[_updater] = _authorized;
    }

    // -------------------------------------------------------------------------
    //  Registration
    // -------------------------------------------------------------------------

    /**
     * @notice Register a new VAPI device with its P256 public key.
     *
     * @dev The caller becomes the device owner. A deposit >= minimumDeposit must
     *      be sent with the transaction. The deviceId is derived as
     *      keccak256(pubkey) and must not already be registered.
     *
     *      The pubkey must be a 65-byte uncompressed SEC1 point (prefix 0x04).
     *      Actual P256 curve-point validation is deferred to the first PoAC
     *      verification via the IoTeX P256 precompile (0x0100).
     *
     * @param _pubkey Uncompressed SEC1 P256 public key (65 bytes).
     * @return deviceId The derived device identifier.
     */
    function registerDevice(bytes calldata _pubkey)
        external
        payable
        virtual
        nonReentrant
        returns (bytes32 deviceId)
    {
        return _registerCore(_pubkey, minimumDeposit);
    }

    /// @dev Called by registerDevice and TieredDeviceRegistry._registerTiered.
    ///      Separated to allow tier-specific deposit validation by the child
    ///      without triggering a double nonReentrant guard via super call.
    function _registerCore(bytes calldata _pubkey, uint256 _requiredDeposit)
        internal returns (bytes32 deviceId)
    {
        // Validate pubkey format
        if (_pubkey.length != PUBKEY_LENGTH) {
            revert InvalidPubkeyLength(_pubkey.length, PUBKEY_LENGTH);
        }
        if (uint8(_pubkey[0]) != 0x04) {
            revert InvalidPubkeyPrefix(uint8(_pubkey[0]));
        }

        // Derive device identity
        deviceId = keccak256(_pubkey);

        // Ensure not already registered
        if (_devices[deviceId].registeredAt != 0) {
            revert DeviceAlreadyRegistered(deviceId);
        }

        // Enforce deposit
        if (msg.value < _requiredDeposit) {
            revert InsufficientDeposit(msg.value, _requiredDeposit);
        }

        // Store device info
        DeviceInfo storage dev = _devices[deviceId];
        dev.owner          = msg.sender;
        dev.registeredAt   = uint64(block.timestamp);
        dev.deposit        = msg.value;
        dev.active         = true;
        dev.pubkey         = _pubkey;
        dev.reputationScore = 5000; // Start at 50% — neutral reputation

        deviceCount++;

        emit DeviceRegistered(deviceId, msg.sender, msg.value, uint64(block.timestamp));
    }

    // -------------------------------------------------------------------------
    //  Deactivation & Withdrawal
    // -------------------------------------------------------------------------

    /**
     * @notice Deactivate a registered device. Only the device owner may call this.
     *
     * @dev After deactivation, the device can no longer submit PoAC records.
     *      The deposit is locked for DEACTIVATION_COOLDOWN before withdrawal.
     *
     * @param _deviceId The device to deactivate.
     */
    function deactivateDevice(bytes32 _deviceId) external {
        DeviceInfo storage dev = _devices[_deviceId];
        if (dev.registeredAt == 0) revert DeviceNotFound(_deviceId);
        if (!dev.active) revert DeviceNotActive(_deviceId);
        if (dev.owner != msg.sender) revert NotDeviceOwner(_deviceId, msg.sender);

        dev.active = false;
        dev.deactivatedAt = uint64(block.timestamp);

        emit DeviceDeactivated(_deviceId, msg.sender, uint64(block.timestamp));
    }

    /**
     * @notice Withdraw deposit after deactivation cooldown has elapsed.
     * @param _deviceId The deactivated device whose deposit to withdraw.
     */
    function withdrawDeposit(bytes32 _deviceId) external nonReentrant {
        DeviceInfo storage dev = _devices[_deviceId];
        if (dev.registeredAt == 0) revert DeviceNotFound(_deviceId);
        if (dev.owner != msg.sender) revert NotDeviceOwner(_deviceId, msg.sender);
        if (dev.active) revert DeviceNotActive(_deviceId); // must be deactivated first
        if (dev.deposit == 0) revert DepositAlreadyWithdrawn(_deviceId);

        uint256 availableAt = uint256(dev.deactivatedAt) + DEACTIVATION_COOLDOWN;
        if (block.timestamp < availableAt) {
            revert CooldownNotElapsed(_deviceId, availableAt);
        }

        uint256 amount = dev.deposit;
        dev.deposit = 0;

        (bool success, ) = payable(dev.owner).call{value: amount}("");
        require(success, "DeviceRegistry: ETH transfer failed");

        emit DepositWithdrawn(_deviceId, dev.owner, amount);
    }

    // -------------------------------------------------------------------------
    //  Reputation
    // -------------------------------------------------------------------------

    /**
     * @notice Update a device's reputation counters and recompute the score.
     *
     * @dev Called by authorized updaters (PoACVerifier, BountyMarket) after
     *      verification events. The reputation score is computed as:
     *
     *          base = verifiedPoACCount (capped contribution)
     *          bonus = corroborationCount * 2
     *          penalty = disputeCount * 10
     *          score = clamp((base + bonus - penalty) * MAX_REPUTATION / normalization, 0, MAX_REPUTATION)
     *
     *      This is a simple initial model; governance can upgrade the formula.
     *
     * @param _deviceId            Target device.
     * @param _addVerified         Number of newly verified PoACs to add.
     * @param _addCorroborations   Number of new corroborations to add.
     * @param _addDisputes         Number of new disputes to add.
     */
    function updateReputation(
        bytes32 _deviceId,
        uint32  _addVerified,
        uint32  _addCorroborations,
        uint32  _addDisputes
    )
        external
        onlyReputationUpdater
    {
        DeviceInfo storage dev = _devices[_deviceId];
        if (dev.registeredAt == 0) revert DeviceNotFound(_deviceId);

        uint16 oldScore = dev.reputationScore;

        // Update counters
        dev.verifiedPoACCount  += _addVerified;
        dev.corroborationCount += _addCorroborations;
        dev.disputeCount       += _addDisputes;

        // Recompute reputation score
        dev.reputationScore = _computeReputation(
            dev.verifiedPoACCount,
            dev.corroborationCount,
            dev.disputeCount
        );

        emit ReputationUpdated(
            _deviceId,
            oldScore,
            dev.reputationScore,
            dev.verifiedPoACCount,
            dev.corroborationCount,
            dev.disputeCount
        );
    }

    /**
     * @dev Internal reputation computation.
     *
     *      Formula (simple linear model with diminishing returns on volume):
     *        - verifiedBase  = min(verifiedCount, 10000) (cap raw PoAC contribution)
     *        - corrobBonus   = corroborations * 2
     *        - disputePenalty = disputes * 10
     *        - rawScore = verifiedBase + corrobBonus - disputePenalty
     *        - Normalize into [0, MAX_REPUTATION] using a logistic-style clamp:
     *          score = rawScore * MAX_REPUTATION / (rawScore + 1000)
     *          (Approaches MAX_REPUTATION asymptotically as rawScore grows)
     *        - If rawScore <= 0, score = 0.
     */
    function _computeReputation(
        uint32 _verified,
        uint32 _corroborations,
        uint32 _disputes
    )
        internal
        pure
        returns (uint16)
    {
        // Cap verified contribution to prevent pure volume gaming
        uint256 verifiedBase = _verified > 10000 ? 10000 : uint256(_verified);
        uint256 corrobBonus = uint256(_corroborations) * 2;
        uint256 disputePenalty = uint256(_disputes) * 10;

        // Compute raw score (can underflow, so use signed logic)
        uint256 positive = verifiedBase + corrobBonus;
        if (disputePenalty >= positive) {
            return 0;
        }
        uint256 rawScore = positive - disputePenalty;

        // Logistic normalization: score = rawScore * MAX / (rawScore + K)
        // K = 1000 gives a nice curve where 1000 raw -> 50%, 5000 -> 83%, 10000 -> 91%
        uint256 score = (rawScore * uint256(MAX_REPUTATION)) / (rawScore + 1000);

        return score > MAX_REPUTATION ? MAX_REPUTATION : uint16(score);
    }

    // -------------------------------------------------------------------------
    //  View functions
    // -------------------------------------------------------------------------

    /**
     * @notice Retrieve full device information.
     * @param _deviceId Device identifier (keccak256 of pubkey).
     * @return info The DeviceInfo struct.
     */
    function getDeviceInfo(bytes32 _deviceId)
        external
        view
        returns (DeviceInfo memory info)
    {
        info = _devices[_deviceId];
        if (info.registeredAt == 0) revert DeviceNotFound(_deviceId);
    }

    /**
     * @notice Check whether a device is registered and active.
     * @param _deviceId Device identifier.
     * @return True if the device exists and is active.
     */
    function isDeviceActive(bytes32 _deviceId) external view returns (bool) {
        return _devices[_deviceId].active;
    }

    /**
     * @notice Returns true if the device is eligible to claim bounty rewards.
     * @dev Base implementation: any active device is eligible.
     *      Overridden in TieredDeviceRegistry to enforce tier-specific restrictions
     *      (e.g., Emulated-tier devices cannot claim bounties).
     * @param _deviceId Device identifier.
     */
    function canClaimBounty(bytes32 _deviceId) public virtual view returns (bool) {
        return _devices[_deviceId].active;
    }

    /**
     * @notice Get the public key bytes for a registered device.
     * @param _deviceId Device identifier.
     * @return The uncompressed SEC1 P256 public key (65 bytes).
     */
    function getDevicePubkey(bytes32 _deviceId) external view returns (bytes memory) {
        DeviceInfo storage dev = _devices[_deviceId];
        if (dev.registeredAt == 0) revert DeviceNotFound(_deviceId);
        return dev.pubkey;
    }

    /**
     * @notice Get the reputation score for a device.
     * @param _deviceId Device identifier.
     * @return score Reputation score in [0, MAX_REPUTATION] (basis points).
     */
    function getReputationScore(bytes32 _deviceId) external view returns (uint16 score) {
        DeviceInfo storage dev = _devices[_deviceId];
        if (dev.registeredAt == 0) revert DeviceNotFound(_deviceId);
        return dev.reputationScore;
    }

    /**
     * @notice Derive the deviceId from a public key without registering.
     * @param _pubkey Uncompressed SEC1 P256 public key (65 bytes).
     * @return deviceId keccak256 hash of the public key.
     */
    function computeDeviceId(bytes calldata _pubkey) external pure returns (bytes32) {
        return keccak256(_pubkey);
    }
}
