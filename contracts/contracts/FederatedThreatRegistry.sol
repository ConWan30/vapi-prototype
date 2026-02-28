// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * FederatedThreatRegistry — Phase 34
 *
 * Lightweight on-chain anchor for cross-bridge confirmed bot-farm cluster hashes.
 * A single authorized bridge address reports cluster fingerprints; when ≥2 distinct
 * reporters have reported the same hash, MultiVenueConfirmed is emitted.
 *
 * Privacy: only 32-byte hash values are stored — never raw device IDs.
 * The cluster hash is derived from compute_cluster_hash() in federation_bus.py:
 *   SHA-256(sorted_device_ids.join("|"))[:16 bytes], zero-padded to 32 bytes.
 *
 * Architecture note: In the current single-bridge deployment, `onlyBridge` restricts
 * reporting to the one authorized bridge address. Multi-bridge deployments would
 * require upgrading to a whitelist pattern or separate deploying per bridge.
 */
contract FederatedThreatRegistry {
    address public immutable bridge;

    // clusterHash => reporter address => has reported
    mapping(bytes32 => mapping(address => bool)) private _hasReported;
    // clusterHash => total distinct reporter count
    mapping(bytes32 => uint256) private _reportCount;

    // --------------- Events ---------------

    event ClusterReported(
        bytes32 indexed clusterHash,
        address indexed reporter,
        uint256 reportCount
    );

    /**
     * Emitted when ≥2 distinct reporters have confirmed the same cluster hash,
     * indicating a coordinated bot farm operating across multiple bridge shards.
     */
    event MultiVenueConfirmed(bytes32 indexed clusterHash, uint256 confirmedBy);

    // --------------- Errors ---------------

    error OnlyBridge();
    error AlreadyReported(bytes32 clusterHash, address reporter);

    // --------------- Constructor ---------------

    constructor(address bridge_) {
        bridge = bridge_;
    }

    // --------------- Modifiers ---------------

    modifier onlyBridge() {
        if (msg.sender != bridge) revert OnlyBridge();
        _;
    }

    // --------------- State-changing functions ---------------

    /**
     * Report a cluster hash as confirmed by this bridge.
     *
     * Each bridge address may only report a given clusterHash once.
     * Emits ClusterReported every time; emits MultiVenueConfirmed when
     * the total reporter count reaches 2 (or more on subsequent reports).
     *
     * @param clusterHash 32-byte cluster fingerprint (16 significant bytes, zero-padded).
     *
     * @dev FINGERPRINT ENTROPY AND BRUTE-FORCE LIMITATION:
     *   The cluster fingerprint stored in `clusterHash` is derived off-chain as:
     *     SHA-256("|".join(sorted(device_ids)))[:16]
     *   That is, the first 16 hex characters (8 bytes = 64 bits) of the SHA-256 digest,
     *   zero-padded to fill the 32-byte `bytes32` field. Only 64 bits of entropy are
     *   meaningful; the remaining 128 bits are always zero.
     *
     *   PRIVACY RISK � BRUTE-FORCE REVERSAL IS FEASIBLE AT SMALL SCALE:
     *   If the total population of device IDs is small (e.g., fewer than 100,000 active
     *   devices), an adversary with access to the device ID list can enumerate all pairs
     *   and triples, compute SHA-256 for each, and compare against reported hashes.
     *   Example: 100,000 devices x 100,000 = 10 billion pairs. At ~500M SHA-256/second
     *   on consumer hardware, this takes roughly 20 seconds per cluster size. Larger
     *   cluster sizes (3, 4, 5 devices) grow combinatorially but remain tractable for
     *   small populations. An operator or malicious insider with access to the full device
     *   ID list could de-anonymize clusters -- identifying which specific devices were
     *   flagged as a coordinated bot farm.
     *
     *   MITIGATIONS (not yet implemented):
     *   1. Use the full 32-byte SHA-256 fingerprint (256 bits) instead of truncating to
     *      16 hex characters. This makes brute-force infeasible regardless of population
     *      size without changing the contract interface.
     *   2. Add a per-bridge salt: SHA-256(salt || "|".join(sorted(device_ids))). Salt is
     *      known only to the bridge operator, preventing cross-bridge correlation attacks
     *      without a salt-sharing protocol.
     *   3. Use a keyed hash (HMAC-SHA256) where the key is a bridge-private secret.
     *   Until these mitigations are adopted, operators should treat reported cluster hashes
     *   as pseudonymous, not anonymous, when device populations are below ~1M devices.
     */
    function reportCluster(bytes32 clusterHash) external onlyBridge {
        if (_hasReported[clusterHash][msg.sender]) {
            revert AlreadyReported(clusterHash, msg.sender);
        }
        _hasReported[clusterHash][msg.sender] = true;
        uint256 count = ++_reportCount[clusterHash];
        emit ClusterReported(clusterHash, msg.sender, count);
        if (count >= 2) {
            emit MultiVenueConfirmed(clusterHash, count);
        }
    }

    // --------------- View functions ---------------

    /**
     * Return the number of distinct reporters for a given cluster hash.
     */
    function getReportCount(bytes32 clusterHash) external view returns (uint256) {
        return _reportCount[clusterHash];
    }

    /**
     * Return true if the cluster has been confirmed by at least minBridges reporters.
     */
    function isMultiVenueConfirmed(bytes32 clusterHash, uint256 minBridges)
        external
        view
        returns (bool)
    {
        return _reportCount[clusterHash] >= minBridges;
    }

    /**
     * Return true if a specific reporter address has already reported this cluster hash.
     */
    function hasReported(bytes32 clusterHash, address reporter)
        external
        view
        returns (bool)
    {
        return _hasReported[clusterHash][reporter];
    }
}
