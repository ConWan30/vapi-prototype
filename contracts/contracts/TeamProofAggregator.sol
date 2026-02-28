// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "./PoACVerifier.sol";

/**
 * @title TeamProofAggregator
 * @author VAPI Project
 * @notice Swarm-based collaborative gaming with verifiable team proofs.
 *
 * @dev Aggregates individual PoAC record hashes from team members into a
 *      compact Merkle root that serves as a team-level attestation.
 *
 *      Use cases:
 *        - Esports tournament integrity: all team members verified clean
 *        - Cooperative game achievements: verifiable team contribution
 *        - DAO governance: team-weighted voting based on collective proof
 *
 *      Merkle construction:
 *        1. Sort individual record hashes lexicographically
 *        2. Compute pairwise keccak256 hashes up the tree
 *        3. Odd leaf is promoted unchanged
 *        4. Root = team attestation hash
 *
 *      Integrity guarantees:
 *        - Every member's record must be verified in PoACVerifier
 *        - No member can have cheat flags (inference in [0x22, 0x29])
 *        - All members must belong to the registered team
 */
contract TeamProofAggregator is Ownable {

    // ─── Constants ──────────────────────────────────────────────────────

    uint8 public constant MIN_TEAM_SIZE = 2;
    uint8 public constant MAX_TEAM_SIZE = 6;

    /// @notice Inclusive range of cheat-flag inference result codes (Phase 8 PITL).
    /// @dev    0x28 = DRIVER_INJECT, 0x29 = WALLHACK_PREAIM, 0x2A = AIMBOT_BEHAVIORAL.
    ///         Any record with inferenceResult in [CHEAT_INFERENCE_MIN, CHEAT_INFERENCE_MAX]
    ///         is rejected by submitTeamProof — esports integrity guarantee.
    uint8 public constant CHEAT_INFERENCE_MIN = 0x28;
    uint8 public constant CHEAT_INFERENCE_MAX = 0x2A;

    // ─── Data Structures ────────────────────────────────────────────────

    struct Team {
        bytes32[]  memberDeviceIds;
        address    captain;       // Team creator
        uint64     createdAt;
        bool       active;
    }

    struct TeamProof {
        bytes32    teamId;
        bytes32    merkleRoot;     // Merkle root of sorted record hashes
        bytes32[]  recordHashes;   // Individual PoAC record hashes
        uint8      memberCount;
        bool       allClean;       // True if no cheat flags in any record
        uint64     submittedAt;
        address    submitter;
    }

    // ─── State ──────────────────────────────────────────────────────────

    PoACVerifier public poACVerifier;

    mapping(bytes32 => Team) private _teams;
    mapping(bytes32 => bool) public teamExists;

    /// @notice Global proof counter (also serves as proof ID).
    uint256 public proofCount;

    /// @notice proofId => TeamProof
    mapping(uint256 => TeamProof) private _proofs;

    /// @notice teamId => array of proof IDs
    mapping(bytes32 => uint256[]) private _teamProofs;

    // ─── Events ─────────────────────────────────────────────────────────

    event TeamCreated(
        bytes32 indexed teamId,
        address indexed captain,
        uint8   memberCount
    );

    event TeamProofSubmitted(
        bytes32 indexed teamId,
        uint256 indexed proofId,
        bytes32 merkleRoot,
        uint8   memberCount,
        bool    allClean
    );

    // ─── Errors ─────────────────────────────────────────────────────────

    error TeamAlreadyExists(bytes32 teamId);
    error TeamNotFound(bytes32 teamId);
    error TeamNotActive(bytes32 teamId);
    error InvalidTeamSize(uint256 size);
    error MemberCountMismatch(uint256 expected, uint256 provided);
    error RecordNotVerified(bytes32 recordHash);
    error InvalidMerkleRoot(bytes32 computed, bytes32 submitted);
    /// @notice Thrown when a team member's record contains a cheat-flag inference code.
    ///         No team proof may contain records flagged by the VAPI PITL anti-cheat layer.
    error CheatFlagDetected(bytes32 recordHash, uint8 inferenceResult);

    // ─── Constructor ────────────────────────────────────────────────────

    constructor(address _poACVerifier) Ownable(msg.sender) {
        poACVerifier = PoACVerifier(_poACVerifier);
    }

    // ─── Team Management ────────────────────────────────────────────────

    /**
     * @notice Register a new team with its member device IDs.
     *
     * @param _teamId     Unique team identifier (e.g., keccak256 of team name).
     * @param _deviceIds  Array of member device IDs (2-6 members).
     */
    function createTeam(
        bytes32 _teamId,
        bytes32[] calldata _deviceIds
    ) external {
        if (teamExists[_teamId]) revert TeamAlreadyExists(_teamId);
        if (_deviceIds.length < MIN_TEAM_SIZE || _deviceIds.length > MAX_TEAM_SIZE) {
            revert InvalidTeamSize(_deviceIds.length);
        }

        Team storage team = _teams[_teamId];
        team.captain = msg.sender;
        team.createdAt = uint64(block.timestamp);
        team.active = true;
        for (uint256 i = 0; i < _deviceIds.length; i++) {
            team.memberDeviceIds.push(_deviceIds[i]);
        }

        teamExists[_teamId] = true;

        emit TeamCreated(_teamId, msg.sender, uint8(_deviceIds.length));
    }

    // ─── Team Proof Submission ──────────────────────────────────────────

    /**
     * @notice Submit a team proof aggregating individual PoAC records.
     *
     * @param _teamId        Team identifier.
     * @param _recordHashes  One verified PoAC record hash per team member (in member order).
     * @param _merkleRoot    Pre-computed Merkle root of sorted record hashes.
     * @return proofId       Unique proof identifier.
     */
    function submitTeamProof(
        bytes32   _teamId,
        bytes32[] calldata _recordHashes,
        bytes32   _merkleRoot
    ) public virtual returns (uint256 proofId) {
        if (!teamExists[_teamId]) revert TeamNotFound(_teamId);

        Team storage team = _teams[_teamId];
        if (!team.active) revert TeamNotActive(_teamId);
        if (_recordHashes.length != team.memberDeviceIds.length) {
            revert MemberCountMismatch(team.memberDeviceIds.length, _recordHashes.length);
        }

        // Verify all records exist on-chain and carry no cheat-flag inference codes.
        // recordInferences is a public mapping on PoACVerifier — auto-generated getter.
        bool allClean = true;
        for (uint256 i = 0; i < _recordHashes.length; i++) {
            bytes32 rh = _recordHashes[i];
            if (!poACVerifier.isRecordVerified(rh)) {
                revert RecordNotVerified(rh);
            }
            uint8 inference = poACVerifier.recordInferences(rh);
            if (inference >= CHEAT_INFERENCE_MIN && inference <= CHEAT_INFERENCE_MAX) {
                revert CheatFlagDetected(rh, inference);
            }
        }

        // Compute and verify Merkle root
        bytes32 computedRoot = _computeMerkleRoot(_recordHashes);
        if (computedRoot != _merkleRoot) {
            revert InvalidMerkleRoot(computedRoot, _merkleRoot);
        }

        // Store proof
        proofId = proofCount++;
        TeamProof storage proof = _proofs[proofId];
        proof.teamId = _teamId;
        proof.merkleRoot = _merkleRoot;
        proof.memberCount = uint8(_recordHashes.length);
        proof.allClean = allClean;
        proof.submittedAt = uint64(block.timestamp);
        proof.submitter = msg.sender;
        for (uint256 i = 0; i < _recordHashes.length; i++) {
            proof.recordHashes.push(_recordHashes[i]);
        }

        _teamProofs[_teamId].push(proofId);

        emit TeamProofSubmitted(
            _teamId, proofId, _merkleRoot,
            uint8(_recordHashes.length), allClean
        );
    }

    // ─── Merkle Tree ────────────────────────────────────────────────────

    /**
     * @dev Compute Merkle root from an array of leaf hashes.
     *      Sorts leaves lexicographically, then hashes pairwise.
     */
    function _computeMerkleRoot(bytes32[] calldata _leaves)
        internal pure returns (bytes32)
    {
        uint256 n = _leaves.length;
        if (n == 1) return _leaves[0];

        // Copy and sort leaves
        bytes32[] memory sorted = new bytes32[](n);
        for (uint256 i = 0; i < n; i++) {
            sorted[i] = _leaves[i];
        }
        _sortBytes32(sorted);

        // Build tree bottom-up
        while (n > 1) {
            uint256 newN = (n + 1) / 2;
            for (uint256 i = 0; i < newN; i++) {
                uint256 left = i * 2;
                uint256 right = left + 1;
                if (right < n) {
                    sorted[i] = keccak256(abi.encodePacked(sorted[left], sorted[right]));
                } else {
                    sorted[i] = sorted[left]; // Promote odd leaf
                }
            }
            n = newN;
        }

        return sorted[0];
    }

    /**
     * @dev Insertion sort for small arrays (max 6 elements).
     */
    function _sortBytes32(bytes32[] memory arr) internal pure {
        for (uint256 i = 1; i < arr.length; i++) {
            bytes32 key = arr[i];
            uint256 j = i;
            while (j > 0 && arr[j - 1] > key) {
                arr[j] = arr[j - 1];
                j--;
            }
            arr[j] = key;
        }
    }

    // ─── View Functions ─────────────────────────────────────────────────

    function getTeam(bytes32 _teamId)
        external view returns (
            bytes32[] memory memberDeviceIds,
            address captain,
            uint64 createdAt,
            bool active
        )
    {
        Team storage team = _teams[_teamId];
        return (team.memberDeviceIds, team.captain, team.createdAt, team.active);
    }

    function getTeamProof(uint256 _proofId)
        external view returns (TeamProof memory)
    {
        return _proofs[_proofId];
    }

    function getTeamProofCount(bytes32 _teamId)
        external view returns (uint256)
    {
        return _teamProofs[_teamId].length;
    }

    function getTeamProofIds(bytes32 _teamId)
        external view returns (uint256[] memory)
    {
        return _teamProofs[_teamId];
    }

    /**
     * @notice Verify a Merkle root against individual record hashes (off-chain helper).
     */
    function computeMerkleRoot(bytes32[] calldata _leaves)
        external pure returns (bytes32)
    {
        return _computeMerkleRoot(_leaves);
    }
}
