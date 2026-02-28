// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "./DeviceRegistry.sol";

/**
 * @title TieredDeviceRegistry
 * @author VAPI Project
 * @notice Extends DeviceRegistry with a three-tier registration model for Sybil resistance.
 *
 * @dev IS-A DeviceRegistry — all downstream contracts (PoACVerifier, BountyMarket,
 *      SkillOracle) accept TieredDeviceRegistry through the DeviceRegistry interface
 *      without modification.
 *
 * Tier model (testnet / mainnet targets):
 *   Emulated  — DualShock/software  — 0.1 / 10 IOTX  — SkillOracle only, no bounty rewards
 *   Standard  — IoT devices         — 1   / 100 IOTX  — Full access, 50% reward weight
 *   Attested  — Pebble ioID cert    — 0.01 / 1 IOTX   — Full access, 100% reward weight
 *
 * Phase 8 work items (deferred):
 *   - Full ioID P256 certificate chain verification via IoTeX precompile 0x0100
 *   - BountyMarket / SkillOracle enforcement of tier capabilities
 *   - Certificate revocation mechanism
 */
contract TieredDeviceRegistry is DeviceRegistry {

    // -------------------------------------------------------------------------
    //  Enums
    // -------------------------------------------------------------------------

    enum RegistrationTier { Emulated, Standard, Attested }

    // -------------------------------------------------------------------------
    //  Data Structures
    // -------------------------------------------------------------------------

    struct TierConfig {
        uint256 depositWei;        // Required deposit in wei
        uint16  rewardWeightBps;   // Reward weight [0-10000 bps]; 10000 = 100%
        bool    canClaimBounties;  // Can submit BountyMarket evidence
        bool    canUseSkillOracle; // Can participate in SkillOracle rating
    }

    struct ManufacturerKey {
        bytes32 pubkeyX;   // raw P256 x-coordinate
        bytes32 pubkeyY;   // raw P256 y-coordinate
        bool    active;    // revocable
        string  name;      // human label, e.g. "Yubico Inc"
    }

    // -------------------------------------------------------------------------
    //  State
    // -------------------------------------------------------------------------

    /// @notice Tier configuration per registration tier.
    mapping(RegistrationTier => TierConfig) public tierConfigs;

    /// @notice Registration tier per device ID.
    mapping(bytes32 => RegistrationTier) public deviceTiers;

    /// @notice Addresses of approved hardware manufacturers (for Attested tier).
    mapping(address => bool) public approvedManufacturers;

    /// @notice When false (testnet): any 64-byte proof accepted for Attested tier.
    ///         When true (mainnet):  full ioID P256 cert verification required (Phase 8).
    bool public attestationEnforced;

    /// @notice Phase 9: hardware attestation certificate hash per device.
    ///         SHA-256 of the DER-encoded manufacturer attestation cert (YubiKey/ATECC608A).
    ///         Zero bytes32 if not set. Used by Phase 10 attestationEnforced=true path.
    mapping(bytes32 => bytes32) public attestationCertificateHashes;

    /// @notice P256 public key registry for approved hardware manufacturers.
    mapping(address => ManufacturerKey) public manufacturerKeys;

    // -------------------------------------------------------------------------
    //  Events
    // -------------------------------------------------------------------------

    event TierRegistered(bytes32 indexed deviceId, RegistrationTier tier, uint256 deposit);
    event ManufacturerApproved(address indexed manufacturer, bool approved);
    event TierConfigUpdated(RegistrationTier indexed tier);
    event AttestationEnforcementChanged(bool enforced);

    /// @notice Emitted when a hardware attestation certificate hash is stored.
    event AttestationCertHashSet(bytes32 indexed deviceId, bytes32 certHash);

    event ManufacturerKeySet(address indexed manufacturer, bytes32 pubkeyX, bytes32 pubkeyY, string name);
    event ManufacturerKeyRevoked(address indexed manufacturer);
    event ManufacturerAttested(bytes32 indexed deviceId, address indexed manufacturer);

    // -------------------------------------------------------------------------
    //  Errors
    // -------------------------------------------------------------------------

    /// @notice Thrown when a caller tries to use registerTieredDevice for Attested tier.
    error InvalidTierForFunction(RegistrationTier tier);

    /// @notice Thrown when a non-owner tries to update a device's cert hash.
    error UnauthorizedCertHashUpdate(bytes32 deviceId, address caller);

    /// @notice Thrown when msg.value < tier's required deposit.
    error InvalidTierDeposit(RegistrationTier tier, uint256 provided, uint256 required);

    /// @notice Thrown when attestation proof format is invalid (must be 64 bytes).
    error InvalidAttestationProof();

    /// @notice Thrown when attestationEnforced=true but Phase 8 crypto is not yet implemented.
    error AttestationValidatorNotImplemented();

    /// @notice Thrown when manufacturer address has no registered P256 key.
    error NoApprovedManufacturer(address manufacturer);

    /// @notice Thrown when manufacturer's key has been revoked.
    error ManufacturerKeyInactive(address manufacturer);

    /// @notice Thrown when IoTeX P256 precompile rejects the attestation signature.
    error AttestationVerificationFailed(bytes32 pubkeyHash);

    // -------------------------------------------------------------------------
    //  Constructor
    // -------------------------------------------------------------------------

    /**
     * @param _emulatedDeposit  Required deposit for Emulated tier (e.g. 0.1 IOTX testnet).
     * @param _standardDeposit  Required deposit for Standard tier (e.g. 1 IOTX testnet).
     * @param _attestedDeposit  Required deposit for Attested tier (e.g. 0.01 IOTX testnet).
     *
     * The parent DeviceRegistry.minimumDeposit is set to _standardDeposit so that
     * any call through the base interface registers at Standard tier deposit level.
     */
    constructor(
        uint256 _emulatedDeposit,
        uint256 _standardDeposit,
        uint256 _attestedDeposit
    ) DeviceRegistry(_standardDeposit) {
        tierConfigs[RegistrationTier.Emulated] = TierConfig({
            depositWei:        _emulatedDeposit,
            rewardWeightBps:   0,
            canClaimBounties:  false,
            canUseSkillOracle: true
        });
        tierConfigs[RegistrationTier.Standard] = TierConfig({
            depositWei:        _standardDeposit,
            rewardWeightBps:   5000,
            canClaimBounties:  true,
            canUseSkillOracle: true
        });
        tierConfigs[RegistrationTier.Attested] = TierConfig({
            depositWei:        _attestedDeposit,
            rewardWeightBps:   10000,
            canClaimBounties:  true,
            canUseSkillOracle: true
        });
        attestationEnforced = false;
    }

    // -------------------------------------------------------------------------
    //  Registration
    // -------------------------------------------------------------------------

    /**
     * @notice Register a device at the Standard tier (backward-compatible override).
     *
     * @dev Overrides DeviceRegistry.registerDevice so that callers using only the
     *      base interface automatically get Standard tier assignment.
     *
     * @param _pubkey Uncompressed SEC1 P256 public key (65 bytes).
     * @return deviceId The derived device identifier.
     */
    function registerDevice(bytes calldata _pubkey)
        external payable override nonReentrant returns (bytes32 deviceId)
    {
        return _registerTiered(_pubkey, RegistrationTier.Standard);
    }

    /**
     * @notice Register a device with explicit tier selection.
     *
     * @dev Only Emulated and Standard tiers are allowed here. For Attested tier
     *      use registerAttested() which requires an attestation proof.
     *
     * @param _pubkey Uncompressed SEC1 P256 public key (65 bytes).
     * @param _tier   RegistrationTier.Emulated or RegistrationTier.Standard.
     * @return deviceId The derived device identifier.
     */
    function registerTieredDevice(bytes calldata _pubkey, RegistrationTier _tier)
        external payable nonReentrant returns (bytes32 deviceId)
    {
        if (_tier == RegistrationTier.Attested)
            revert InvalidTierForFunction(_tier);
        return _registerTiered(_pubkey, _tier);
    }

    /**
     * @notice Register an Attested-tier device with a manufacturer attestation proof.
     *
     * @dev When attestationEnforced=false (testnet default), any 64-byte proof is
     *      accepted. When attestationEnforced=true, full ioID P256 certificate
     *      verification via IoTeX precompile 0x0100 is required (Phase 8).
     *
     * @param _pubkey           Uncompressed SEC1 P256 public key (65 bytes).
     * @param _attestationProof 64-byte manufacturer attestation proof.
     * @return deviceId The derived device identifier.
     */
    function registerAttested(bytes calldata _pubkey, bytes calldata _attestationProof)
        external payable nonReentrant returns (bytes32 deviceId)
    {
        _validateAttestation(_pubkey, _attestationProof);
        return _registerTiered(_pubkey, RegistrationTier.Attested);
    }

    /**
     * @notice Register an Attested device and store its hardware attestation cert hash.
     * @dev Phase 9 addition. The existing registerAttested(bytes,bytes) is unchanged
     *      for backward compatibility.
     * @param _pubkey           Uncompressed SEC1 P256 public key (65 bytes).
     * @param _attestationProof 64-byte proof (same validation as registerAttested).
     * @param _certificateHash  SHA-256 of the DER-encoded hardware attestation cert.
     */
    function registerAttestedWithCert(
        bytes calldata _pubkey,
        bytes calldata _attestationProof,
        bytes32 _certificateHash
    ) external payable nonReentrant returns (bytes32 deviceId) {
        _validateAttestation(_pubkey, _attestationProof);
        deviceId = _registerTiered(_pubkey, RegistrationTier.Attested);
        attestationCertificateHashes[deviceId] = _certificateHash;
        emit AttestationCertHashSet(deviceId, _certificateHash);
    }

    /**
     * @notice Register an Attested-tier device with V2 manufacturer P256 verification.
     * @dev When attestationEnforced=false, any 64-byte proof is accepted.
     *      When attestationEnforced=true, calls IoTeX P256 precompile 0x0100 to
     *      verify the proof against the registered manufacturer public key.
     * @param _pubkey            Uncompressed SEC1 P256 public key (65 bytes).
     * @param _attestationProof  64-byte manufacturer attestation signature (r||s).
     * @param _manufacturer      Address of the registered manufacturer key.
     */
    function registerAttestedV2(
        bytes calldata _pubkey,
        bytes calldata _attestationProof,
        address _manufacturer
    ) external payable nonReentrant returns (bytes32 deviceId) {
        _validateAttestationV2(_pubkey, _attestationProof, _manufacturer);
        deviceId = _registerTiered(_pubkey, RegistrationTier.Attested);
        emit ManufacturerAttested(deviceId, _manufacturer);
    }

    /**
     * @notice Register an Attested-tier device with V2 verification and cert hash storage.
     * @param _pubkey            Uncompressed SEC1 P256 public key (65 bytes).
     * @param _attestationProof  64-byte manufacturer attestation signature (r||s).
     * @param _certificateHash   SHA-256 of the DER-encoded hardware attestation cert.
     * @param _manufacturer      Address of the registered manufacturer key.
     */
    function registerAttestedWithCertV2(
        bytes calldata _pubkey,
        bytes calldata _attestationProof,
        bytes32 _certificateHash,
        address _manufacturer
    ) external payable nonReentrant returns (bytes32 deviceId) {
        _validateAttestationV2(_pubkey, _attestationProof, _manufacturer);
        deviceId = _registerTiered(_pubkey, RegistrationTier.Attested);
        attestationCertificateHashes[deviceId] = _certificateHash;
        emit AttestationCertHashSet(deviceId, _certificateHash);
        emit ManufacturerAttested(deviceId, _manufacturer);
    }

    /**
     * @notice Update the attestation certificate hash for an existing device.
     * @dev Only callable by the address that originally registered the device.
     *      Uses this.getDeviceInfo() consistent with existing view pattern.
     */
    function setAttestationCertHash(bytes32 _deviceId, bytes32 _certHash) external {
        if (this.getDeviceInfo(_deviceId).owner != msg.sender)
            revert UnauthorizedCertHashUpdate(_deviceId, msg.sender);
        attestationCertificateHashes[_deviceId] = _certHash;
        emit AttestationCertHashSet(_deviceId, _certHash);
    }

    // -------------------------------------------------------------------------
    //  Internal
    // -------------------------------------------------------------------------

    function _registerTiered(bytes calldata _pubkey, RegistrationTier _tier)
        internal returns (bytes32 deviceId)
    {
        TierConfig memory cfg = tierConfigs[_tier];
        if (msg.value < cfg.depositWei)
            revert InvalidTierDeposit(_tier, msg.value, cfg.depositWei);
        deviceId = _registerCore(_pubkey, cfg.depositWei);
        deviceTiers[deviceId] = _tier;
        emit TierRegistered(deviceId, _tier, msg.value);
    }

    function _validateAttestation(bytes calldata /*_pubkey*/, bytes calldata _proof)
        internal view
    {
        if (_proof.length != 64) revert InvalidAttestationProof();
        if (!attestationEnforced) return; // testnet: any 64-byte proof accepted
        // Phase 10: verify ECDSA_P256(keccak256(_pubkey), _proof, manufacturer_key)
        //           via IoTeX precompile 0x0100; check manufacturer_key in approvedManufacturers.
        //           Use attestationCertificateHashes[deviceId] for cert chain lookup.
        revert AttestationValidatorNotImplemented();
    }

    /// @dev V2 attestation validation using registered manufacturer P256 key.
    ///      When attestationEnforced=false, skips crypto verification (testnet mode).
    ///      When attestationEnforced=true, calls _p256Verify() which invokes the
    ///      IoTeX P256 precompile at 0x0100. Override _p256Verify() in test harnesses.
    function _validateAttestationV2(
        bytes calldata _pubkey,
        bytes calldata _proof,
        address _manufacturer
    ) internal virtual view {
        if (_proof.length != 64) revert InvalidAttestationProof();
        if (!attestationEnforced) return;
        ManufacturerKey memory mk = manufacturerKeys[_manufacturer];
        if (mk.pubkeyX == bytes32(0) && mk.pubkeyY == bytes32(0))
            revert NoApprovedManufacturer(_manufacturer);
        if (!mk.active) revert ManufacturerKeyInactive(_manufacturer);
        bytes32 msgHash = keccak256(_pubkey);
        if (!_p256Verify(msgHash, _proof, mk.pubkeyX, mk.pubkeyY))
            revert AttestationVerificationFailed(msgHash);
    }

    /// @dev Calls IoTeX P256 precompile 0x0100 with 160-byte input:
    ///      msgHash(32) || r(32) || s(32) || manuf_x(32) || manuf_y(32).
    ///      Returns true iff the call succeeds and returns uint256(1).
    ///      Virtual so test harnesses can override without calling the precompile.
    function _p256Verify(
        bytes32 msgHash,
        bytes calldata proof,
        bytes32 pubkeyX,
        bytes32 pubkeyY
    ) internal virtual view returns (bool) {
        bytes memory input = abi.encodePacked(msgHash, proof, pubkeyX, pubkeyY);
        (bool ok, bytes memory result) = address(0x0100).staticcall(input);
        return ok && result.length >= 32 && abi.decode(result, (uint256)) == 1;
    }

    // -------------------------------------------------------------------------
    //  View functions
    // -------------------------------------------------------------------------

    /// @notice Get the registration tier of a device.
    function getDeviceTier(bytes32 _deviceId) external view returns (RegistrationTier) {
        return deviceTiers[_deviceId];
    }

    /// @notice Get the reward weight (bps) for a device based on its tier.
    function getDeviceRewardWeightBps(bytes32 _deviceId) external view returns (uint16) {
        return tierConfigs[deviceTiers[_deviceId]].rewardWeightBps;
    }

    /// @notice True if the device is active AND its tier allows bounty claims.
    function canClaimBounty(bytes32 _deviceId) public view override returns (bool) {
        if (!this.isDeviceActive(_deviceId)) return false;
        return tierConfigs[deviceTiers[_deviceId]].canClaimBounties;
    }

    /// @notice True if the device is active AND its tier allows SkillOracle participation.
    function canUseSkillOracle(bytes32 _deviceId) external view returns (bool) {
        if (!this.isDeviceActive(_deviceId)) return false;
        return tierConfigs[deviceTiers[_deviceId]].canUseSkillOracle;
    }

    /// @notice Get the P256 manufacturer key record for an address.
    function getManufacturerKey(address _manufacturer)
        external view returns (ManufacturerKey memory)
    {
        return manufacturerKeys[_manufacturer];
    }

    // -------------------------------------------------------------------------
    //  Admin
    // -------------------------------------------------------------------------

    /// @notice Update configuration for a registration tier. Owner-only.
    function setTierConfig(RegistrationTier _tier, TierConfig calldata _config)
        external onlyOwner
    {
        tierConfigs[_tier] = _config;
        emit TierConfigUpdated(_tier);
    }

    /// @notice Approve or revoke a hardware manufacturer address. Owner-only.
    /// @dev    Deprecated — use setManufacturerKey for V2 attestation instead.
    ///         The approvedManufacturers mapping is not read by any V2 attestation
    ///         path (_validateAttestationV2 uses manufacturerKeys). Retained for
    ///         backward-compatible storage reads only.
    function setApprovedManufacturer(address _manufacturer, bool _approved)
        external onlyOwner
    {
        approvedManufacturers[_manufacturer] = _approved;
        emit ManufacturerApproved(_manufacturer, _approved);
    }

    /// @notice Enable or disable strict attestation enforcement. Owner-only.
    /// @dev Set to true before mainnet launch when Phase 8 crypto is implemented.
    function setAttestationEnforced(bool _enforced) external onlyOwner {
        attestationEnforced = _enforced;
        emit AttestationEnforcementChanged(_enforced);
    }

    /// @notice Register or update a hardware manufacturer's P256 public key. Owner-only.
    /// @param _manufacturer  Ethereum address used as the manufacturer registry key.
    /// @param _pubkeyX       Raw P256 x-coordinate (32 bytes).
    /// @param _pubkeyY       Raw P256 y-coordinate (32 bytes).
    /// @param _name          Human-readable label (e.g. "Yubico Inc").
    function setManufacturerKey(
        address _manufacturer,
        bytes32 _pubkeyX,
        bytes32 _pubkeyY,
        string calldata _name
    ) external onlyOwner {
        manufacturerKeys[_manufacturer] = ManufacturerKey({
            pubkeyX: _pubkeyX,
            pubkeyY: _pubkeyY,
            active:  true,
            name:    _name
        });
        emit ManufacturerKeySet(_manufacturer, _pubkeyX, _pubkeyY, _name);
    }

    /// @notice Revoke a manufacturer's P256 key. Owner-only.
    ///         Existing device registrations are unaffected; new V2 registrations blocked.
    function revokeManufacturerKey(address _manufacturer) external onlyOwner {
        manufacturerKeys[_manufacturer].active = false;
        emit ManufacturerKeyRevoked(_manufacturer);
    }
}
