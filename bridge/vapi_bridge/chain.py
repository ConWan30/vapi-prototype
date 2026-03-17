"""
IoTeX Chain Client — Web3 contract interactions for PoAC verification.

Handles:
  - PoACVerifier.verifyPoAC() and verifyPoACBatch()
  - BountyMarket.submitEvidence()
  - DeviceRegistry.isDeviceActive() and getDevicePubkey()
  - ProgressAttestation.attestProgress()
  - TeamProofAggregator.createTeam() / submitTeamProof()
  - Gas estimation, nonce management, and transaction confirmation
"""

import asyncio
import logging
from typing import Sequence

from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.exceptions import ContractLogicError, TransactionNotFound
from eth_account import Account

from .codec import PoACRecord
from .config import Config

log = logging.getLogger(__name__)

# Minimal ABIs — only the functions the bridge calls.
# Generated from the exact Solidity signatures in the contracts.

VERIFIER_ABI = [
    {
        "name": "verifyPoAC",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_deviceId", "type": "bytes32"},
            {"name": "_rawBody", "type": "bytes"},
            {"name": "_signature", "type": "bytes"},
        ],
        "outputs": [{"name": "recordHash", "type": "bytes32"}],
    },
    {
        "name": "verifyPoACBatch",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_deviceIds", "type": "bytes32[]"},
            {"name": "_rawBodies", "type": "bytes[]"},
            {"name": "_signatures", "type": "bytes[]"},
        ],
        "outputs": [{"name": "recordHashes", "type": "bytes32[]"}],
    },
    {
        "name": "isRecordVerified",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_recordHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getVerifiedCount",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "count", "type": "uint32"}],
    },
    # --- Phase 12: Schema version + inference storage ---
    {
        "name": "verifyPoACWithSchema",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_deviceId",      "type": "bytes32"},
            {"name": "_rawBody",       "type": "bytes"},
            {"name": "_signature",     "type": "bytes"},
            {"name": "_schemaVersion", "type": "uint8"},
        ],
        "outputs": [{"name": "recordHash", "type": "bytes32"}],
    },
    {
        "name": "getRecordSchema",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_recordHash", "type": "bytes32"}],
        "outputs": [
            {"name": "schemaVersion", "type": "uint8"},
            {"name": "isSet",         "type": "bool"},
        ],
    },
    {
        "name": "recordInferences",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]

BOUNTY_MARKET_ABI = [
    {
        "name": "submitEvidence",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_bountyId", "type": "uint256"},
            {"name": "_deviceId", "type": "bytes32"},
            {"name": "_recordHash", "type": "bytes32"},
            {"name": "_latitude", "type": "int64"},
            {"name": "_longitude", "type": "int64"},
            {"name": "_timestampMs", "type": "int64"},
        ],
        "outputs": [],
    },
    {
        "name": "getBounty",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_bountyId", "type": "uint256"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "bountyId", "type": "uint256"},
                    {"name": "creator", "type": "address"},
                    {"name": "reward", "type": "uint256"},
                    {"name": "sensorRequirements", "type": "uint16"},
                    {"name": "minSamples", "type": "uint16"},
                    {"name": "sampleIntervalS", "type": "uint32"},
                    {"name": "durationS", "type": "uint32"},
                    {"name": "deadlineMs", "type": "uint64"},
                    {"name": "zoneLatMin", "type": "int64"},
                    {"name": "zoneLatMax", "type": "int64"},
                    {"name": "zoneLonMin", "type": "int64"},
                    {"name": "zoneLonMax", "type": "int64"},
                    {"name": "vocThreshold", "type": "int256"},
                    {"name": "tempThresholdHi", "type": "int256"},
                    {"name": "tempThresholdLo", "type": "int256"},
                    {"name": "status", "type": "uint8"},
                    {"name": "createdAt", "type": "uint256"},
                ],
            },
        ],
    },
]

REGISTRY_ABI = [
    {
        "name": "isDeviceActive",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getDevicePubkey",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bytes"}],
    },
    {
        "name": "getReputationScore",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "score", "type": "uint16"}],
    },
    {
        "name": "registerDevice",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [{"name": "_pubkey", "type": "bytes"}],
        "outputs": [{"name": "deviceId", "type": "bytes32"}],
    },
    {
        "name": "minimumDeposit",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    # --- Phase 7: TieredDeviceRegistry extensions ---
    {
        "name": "registerTieredDevice",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "_pubkey", "type": "bytes"},
            {"name": "_tier",   "type": "uint8"},
        ],
        "outputs": [{"name": "deviceId", "type": "bytes32"}],
    },
    {
        "name": "registerAttested",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "_pubkey",           "type": "bytes"},
            {"name": "_attestationProof", "type": "bytes"},
        ],
        "outputs": [{"name": "deviceId", "type": "bytes32"}],
    },
    {
        "name": "getDeviceTier",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "name": "getDeviceRewardWeightBps",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint16"}],
    },
    {
        "name": "canClaimBounty",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "tierConfigs",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "uint8"}],
        "outputs": [
            {"name": "depositWei",        "type": "uint256"},
            {"name": "rewardWeightBps",   "type": "uint16"},
            {"name": "canClaimBounties",  "type": "bool"},
            {"name": "canUseSkillOracle", "type": "bool"},
        ],
    },
    # --- Phase 9: Hardware attestation cert hash ---
    {
        "name": "registerAttestedWithCert",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "_pubkey",           "type": "bytes"},
            {"name": "_attestationProof", "type": "bytes"},
            {"name": "_certificateHash",  "type": "bytes32"},
        ],
        "outputs": [{"name": "deviceId", "type": "bytes32"}],
    },
    {
        "name": "setAttestationCertHash",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_deviceId", "type": "bytes32"},
            {"name": "_certHash", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "name": "attestationCertificateHashes",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    # --- Phase 10: V2 attestation with manufacturer P256 key verification ---
    {
        "name": "registerAttestedV2",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "_pubkey",           "type": "bytes"},
            {"name": "_attestationProof", "type": "bytes"},
            {"name": "_manufacturer",     "type": "address"},
        ],
        "outputs": [{"name": "deviceId", "type": "bytes32"}],
    },
    {
        "name": "registerAttestedWithCertV2",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "_pubkey",           "type": "bytes"},
            {"name": "_attestationProof", "type": "bytes"},
            {"name": "_certificateHash",  "type": "bytes32"},
            {"name": "_manufacturer",     "type": "address"},
        ],
        "outputs": [{"name": "deviceId", "type": "bytes32"}],
    },
    {
        "name": "setManufacturerKey",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_manufacturer", "type": "address"},
            {"name": "_pubkeyX",      "type": "bytes32"},
            {"name": "_pubkeyY",      "type": "bytes32"},
            {"name": "_name",         "type": "string"},
        ],
        "outputs": [],
    },
    {
        "name": "revokeManufacturerKey",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "_manufacturer", "type": "address"}],
        "outputs": [],
    },
    {
        "name": "manufacturerKeys",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}],
        "outputs": [
            {"name": "pubkeyX", "type": "bytes32"},
            {"name": "pubkeyY", "type": "bytes32"},
            {"name": "active",  "type": "bool"},
            {"name": "name",    "type": "string"},
        ],
    },
    # --- Phase 12: getManufacturerKey view function + revocation event ---
    {
        "name": "getManufacturerKey",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_manufacturer", "type": "address"}],
        "outputs": [
            {"name": "pubkeyX", "type": "bytes32"},
            {"name": "pubkeyY", "type": "bytes32"},
            {"name": "active",  "type": "bool"},
            {"name": "name",    "type": "string"},
        ],
    },
    {
        "name": "ManufacturerKeyRevoked",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "manufacturer", "type": "address", "indexed": True},
        ],
    },
]

# --- Phase 7: Tier constants ---
TIER_VALUES = {"Emulated": 0, "Standard": 1, "Attested": 2}
TIER_NAMES  = {0: "Emulated", 1: "Standard", 2: "Attested"}


PROGRESS_ATTESTATION_ABI = [
    {
        "name": "attestProgress",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_deviceId",       "type": "bytes32"},
            {"name": "_baselineHash",   "type": "bytes32"},
            {"name": "_currentHash",    "type": "bytes32"},
            {"name": "_metricType",     "type": "uint8"},
            {"name": "_improvementBps", "type": "uint32"},
        ],
        "outputs": [{"name": "attestationId", "type": "uint256"}],
    },
    {
        "name": "getDeviceAttestationCount",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "_deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

TEAM_AGGREGATOR_ABI = [
    {
        "name": "createTeam",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_teamId",    "type": "bytes32"},
            {"name": "_deviceIds", "type": "bytes32[]"},
        ],
        "outputs": [],
    },
    {
        "name": "submitTeamProof",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "_teamId",       "type": "bytes32"},
            {"name": "_recordHashes", "type": "bytes32[]"},
            {"name": "_merkleRoot",   "type": "bytes32"},
        ],
        "outputs": [{"name": "proofId", "type": "uint256"}],
    },
    {
        "name": "teamExists",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

PHG_REGISTRY_ABI = [
    {
        "name": "commitCheckpoint",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "deviceId",      "type": "bytes32"},
            {"name": "scoreDelta",    "type": "uint256"},
            {"name": "count",         "type": "uint32"},
            {"name": "biometricHash", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "name": "cumulativeScore",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "isEligible",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "deviceId", "type": "bytes32"},
            {"name": "minScore", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getDeviceState",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [
            {"name": "score", "type": "uint256"},
            {"name": "count", "type": "uint32"},
            {"name": "head",  "type": "bytes32"},
        ],
    },
    # Phase 23: score inheritance (callable only by IdentityContinuityRegistry)
    {
        "name": "inheritScore",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "fromId", "type": "bytes32"},
            {"name": "toId",   "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "name": "setIdentityRegistry",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "reg", "type": "address"}],
        "outputs": [],
    },
    {
        "name": "identityRegistry",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    # Phase 25: on-chain event + velocity view functions
    {
        "name": "PHGCheckpointCommitted",
        "type": "event",
        "inputs": [
            {"name": "deviceId",           "type": "bytes32", "indexed": True},
            {"name": "cumulativeScore",    "type": "uint256", "indexed": False},
            {"name": "recordCount",        "type": "uint32",  "indexed": False},
            {"name": "biometricHash",      "type": "bytes32", "indexed": False},
            {"name": "prevCheckpointHash", "type": "bytes32", "indexed": False},
            {"name": "blockNumber",        "type": "uint256", "indexed": False},
        ],
    },
    {
        "name": "scoreDeltaAt",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getRecentVelocity",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "deviceId",   "type": "bytes32"},
            {"name": "windowSize", "type": "uint256"},
        ],
        "outputs": [{"name": "velocity", "type": "uint256"}],
    },
]

PITL_SESSION_REGISTRY_ABI = [
    {
        "name": "submitPITLProof",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "deviceId",          "type": "bytes32"},
            {"name": "proof",             "type": "bytes"},
            {"name": "featureCommitment", "type": "uint256"},
            {"name": "humanityProbInt",   "type": "uint256"},
            {"name": "nullifierHash",     "type": "uint256"},
            {"name": "epoch",             "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "latestHumanityProb",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "sessionCount",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "usedNullifiers",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "PITLSessionProofSubmitted",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "deviceId",          "type": "bytes32", "indexed": True},
            {"name": "humanityProbInt",   "type": "uint256", "indexed": False},
            {"name": "featureCommitment", "type": "uint256", "indexed": False},
            {"name": "epoch",             "type": "uint256", "indexed": True},
        ],
    },
    {
        "name": "PITLVerifierSet",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "verifier", "type": "address", "indexed": True},
        ],
    },
]

IOID_REGISTRY_ABI = [
    {
        "name": "register",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "deviceId",      "type": "bytes32"},
            {"name": "deviceAddress", "type": "address"},
            {"name": "did",           "type": "string"},
        ],
        "outputs": [],
    },
    {
        "name": "incrementSession",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [],
    },
    {
        "name": "getDID",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "string"}],
    },
    {
        "name": "isRegistered",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getDeviceCount",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

TOURNAMENT_PASSPORT_ABI = [
    {
        "name": "submitPassport",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "deviceId",       "type": "bytes32"},
            {"name": "proof",          "type": "bytes"},
            {"name": "nullifiers",     "type": "bytes32[5]"},
            {"name": "passportHash",   "type": "bytes32"},
            {"name": "ioidTokenId",    "type": "uint256"},
            {"name": "minHumanityInt", "type": "uint256"},
            {"name": "epoch",          "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "getPassport",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "passportHash",   "type": "bytes32"},
                    {"name": "ioidTokenId",    "type": "uint256"},
                    {"name": "minHumanityInt", "type": "uint256"},
                    {"name": "issuedAt",       "type": "uint256"},
                    {"name": "active",         "type": "bool"},
                ],
            }
        ],
    },
    {
        "name": "hasPassport",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

IDENTITY_REGISTRY_ABI = [
    {
        "name": "attestContinuity",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "oldDeviceId",        "type": "bytes32"},
            {"name": "newDeviceId",        "type": "bytes32"},
            {"name": "biometricProofHash", "type": "bytes32"},
        ],
        "outputs": [],
    },
    {
        "name": "isContinuationOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "newId", "type": "bytes32"},
            {"name": "oldId", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getCanonicalRoot",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "root", "type": "bytes32"}],
    },
    {
        "name": "claimed",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "continuedFrom",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
]

# Phase 28: PHG Credential soulbound registry
PHG_CREDENTIAL_ABI = [
    {
        "name": "mintCredential",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "deviceId",          "type": "bytes32"},
            {"name": "nullifierHash",     "type": "bytes32"},
            {"name": "featureCommitment", "type": "bytes32"},
            {"name": "humanityProbInt",   "type": "uint256"},
        ],
        "outputs": [{"name": "id", "type": "uint256"}],
    },
    {
        "name": "hasCredential",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "credentialOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "CredentialMinted",
        "type": "event",
        "inputs": [
            {"name": "deviceId",      "type": "bytes32", "indexed": True},
            {"name": "credentialId",  "type": "uint256", "indexed": True},
            {"name": "humanityProbInt", "type": "uint256", "indexed": False},
            {"name": "blockNumber",   "type": "uint256", "indexed": False},
        ],
    },
    # Phase 37: Provisional enforcement
    {
        "name": "suspend",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "deviceId",         "type": "bytes32"},
            {"name": "evidenceHash",     "type": "bytes32"},
            {"name": "durationSeconds",  "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "reinstate",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [],
    },
    {
        "name": "isActive",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "isSuspended",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "deviceId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "CredentialSuspended",
        "type": "event",
        "inputs": [
            {"name": "deviceId",     "type": "bytes32", "indexed": True},
            {"name": "evidenceHash", "type": "bytes32", "indexed": False},
            {"name": "until",        "type": "uint256", "indexed": False},
        ],
    },
    {
        "name": "CredentialReinstated",
        "type": "event",
        "inputs": [
            {"name": "deviceId", "type": "bytes32", "indexed": True},
        ],
    },
]


# Phase 34: Federated Threat Registry (cross-bridge cluster anchoring)
FEDERATED_THREAT_REGISTRY_ABI = [
    {
        "name": "reportCluster",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "clusterHash", "type": "bytes32"}],
        "outputs": [],
    },
    {
        "name": "getReportCount",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "clusterHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


def _record_raw_body(record: PoACRecord) -> bytes:
    """Get the raw 164-byte body for on-chain submission.

    The contract now accepts the raw body directly (no struct re-serialization),
    ensuring the on-chain SHA-256 hash matches the firmware-computed hash exactly.
    """
    if record.raw_body and len(record.raw_body) == 164:
        return record.raw_body
    raise ValueError(
        f"PoACRecord missing raw_body (len={len(record.raw_body) if record.raw_body else 0})"
    )


class ChainClient:
    """Async Web3 client for IoTeX contract interactions."""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._w3 = AsyncWeb3(AsyncHTTPProvider(cfg.iotex_rpc_url))

        # Phase 11: Support encrypted keystore as an alternative to plaintext env key
        source = getattr(cfg, "bridge_private_key_source", "env")
        if source == "keystore":
            import json as _json
            import os as _os
            ks_path = getattr(cfg, "keystore_path", "")
            pw_env  = getattr(cfg, "keystore_password_env", "BRIDGE_KEYSTORE_PASSWORD")
            password = _os.environ.get(pw_env, "")
            if not password:
                raise ValueError(
                    f"Keystore password env var {pw_env!r} is not set. "
                    "Set it before starting the bridge."
                )
            with open(ks_path) as _f:
                keystore_json = _json.load(_f)
            private_key = Account.decrypt(keystore_json, password)
            self._account = Account.from_key(private_key)
            log.info("Bridge key loaded from keystore: %s (address=%s)", ks_path, self._account.address)
        else:
            # "env" source — existing behaviour; emit advisory if key is set
            if getattr(cfg, "bridge_private_key", ""):
                log.warning(
                    "BRIDGE_PRIVATE_KEY is a plaintext env var. "
                    "For mainnet, migrate to an encrypted keystore "
                    "(BRIDGE_PRIVATE_KEY_SOURCE=keystore)."
                )
            self._account = Account.from_key(cfg.bridge_private_key)

        self._nonce_lock = asyncio.Lock()
        self._nonce: int | None = None
        # Phase 12: Cache of revoked manufacturer addresses (lowercased)
        self._revoked_manufacturers: set[str] = set()

        # Initialize contracts
        self._verifier = self._w3.eth.contract(
            address=self._w3.to_checksum_address(cfg.verifier_address),
            abi=VERIFIER_ABI,
        )
        if cfg.bounty_market_address:
            self._bounty_market = self._w3.eth.contract(
                address=self._w3.to_checksum_address(cfg.bounty_market_address),
                abi=BOUNTY_MARKET_ABI,
            )
        else:
            self._bounty_market = None

        if cfg.device_registry_address:
            self._registry = self._w3.eth.contract(
                address=self._w3.to_checksum_address(cfg.device_registry_address),
                abi=REGISTRY_ABI,
            )
        else:
            self._registry = None

        progress_addr = getattr(cfg, "progress_attestation_address", "")
        if progress_addr:
            self._progress = self._w3.eth.contract(
                address=self._w3.to_checksum_address(progress_addr),
                abi=PROGRESS_ATTESTATION_ABI,
            )
        else:
            self._progress = None

        team_addr = getattr(cfg, "team_aggregator_address", "")
        if team_addr:
            self._team_agg = self._w3.eth.contract(
                address=self._w3.to_checksum_address(team_addr),
                abi=TEAM_AGGREGATOR_ABI,
            )
        else:
            self._team_agg = None

        # Phase 22: PHG Registry (optional)
        phg_addr = getattr(cfg, "phg_registry_address", "")
        if phg_addr:
            self._phg_registry = self._w3.eth.contract(
                address=self._w3.to_checksum_address(phg_addr),
                abi=PHG_REGISTRY_ABI,
            )
        else:
            self._phg_registry = None

        # Phase 23: Identity Continuity Registry (optional)
        identity_addr = getattr(cfg, "identity_registry_address", "")
        if identity_addr:
            self._identity_registry = self._w3.eth.contract(
                address=self._w3.to_checksum_address(identity_addr),
                abi=IDENTITY_REGISTRY_ABI,
            )
        else:
            self._identity_registry = None

        # Phase 26: PITL Session Registry (optional)
        pitl_addr = getattr(cfg, "pitl_session_registry_address", "")
        if pitl_addr:
            self._pitl_registry = self._w3.eth.contract(
                address=self._w3.to_checksum_address(pitl_addr),
                abi=PITL_SESSION_REGISTRY_ABI,
            )
        else:
            self._pitl_registry = None

        # Phase 28: PHG Credential soulbound registry (optional)
        cred_addr = getattr(cfg, "phg_credential_address", "")
        if cred_addr:
            self._phg_credential = self._w3.eth.contract(
                address=self._w3.to_checksum_address(cred_addr),
                abi=PHG_CREDENTIAL_ABI,
            )
        else:
            self._phg_credential = None

        # Phase 34: Federated Threat Registry (optional on-chain anchor)
        ftr_addr = getattr(cfg, "federated_threat_registry_address", "")
        if ftr_addr:
            self._federated_threat_registry = self._w3.eth.contract(
                address=self._w3.to_checksum_address(ftr_addr),
                abi=FEDERATED_THREAT_REGISTRY_ABI,
            )
        else:
            self._federated_threat_registry = None

        # Phase 55: ioID Device Identity Registry (optional)
        ioid_addr = getattr(cfg, "ioid_registry_address", "")
        if ioid_addr:
            self._ioid_registry = self._w3.eth.contract(
                address=self._w3.to_checksum_address(ioid_addr),
                abi=IOID_REGISTRY_ABI,
            )
        else:
            self._ioid_registry = None

        # Phase 62: PITLSessionRegistryV2 (Phase 62 C3 circuit — preferred over v1)
        pitl_v2_addr = getattr(cfg, "pitl_session_registry_v2_address", "")
        if pitl_v2_addr:
            self._pitl_registry_v2 = self._w3.eth.contract(
                address=self._w3.to_checksum_address(pitl_v2_addr),
                abi=PITL_SESSION_REGISTRY_ABI,
            )
        else:
            self._pitl_registry_v2 = None

        # Phase 56: Tournament Passport (optional)
        tp_addr = getattr(cfg, "tournament_passport_address", "")
        if tp_addr:
            self._tournament_passport = self._w3.eth.contract(
                address=self._w3.to_checksum_address(tp_addr),
                abi=TOURNAMENT_PASSPORT_ABI,
            )
        else:
            self._tournament_passport = None

    @classmethod
    def generate_keystore(cls, output_path: str, password: str) -> str:
        """Encrypt the BRIDGE_PRIVATE_KEY env var to an Ethereum keystore JSON file.

        Usage (run once during setup):
            python -c "
            from vapi_bridge.chain import ChainClient
            addr = ChainClient.generate_keystore('/etc/vapi/bridge-keystore.json', 'your-password')
            print('Keystore written. Bridge address:', addr)
            print('Delete BRIDGE_PRIVATE_KEY from env after confirming keystore loads.')
            "

        Args:
            output_path: Where to write the keystore JSON file.
            password:    Encryption password (stored nowhere — you must remember this).

        Returns:
            The checksummed Ethereum address of the encrypted key.
        """
        import json as _json
        import os as _os
        private_key = _os.environ.get("BRIDGE_PRIVATE_KEY", "")
        if not private_key:
            raise ValueError("BRIDGE_PRIVATE_KEY env var is not set")
        account = Account.from_key(private_key)
        keystore = Account.encrypt(private_key, password)
        with open(output_path, "w") as f:
            _json.dump(keystore, f, indent=2)
        log.info("Keystore written to %s (address=%s)", output_path, account.address)
        return account.address

    @property
    def bridge_address(self) -> str:
        return self._account.address

    async def get_balance(self) -> float:
        """Get bridge wallet balance in IOTX."""
        wei = await self._w3.eth.get_balance(self._account.address)
        return float(self._w3.from_wei(wei, "ether"))

    async def _next_nonce(self) -> int:
        """Thread-safe nonce management."""
        async with self._nonce_lock:
            if self._nonce is None:
                self._nonce = await self._w3.eth.get_transaction_count(
                    self._account.address
                )
            else:
                self._nonce += 1
            return self._nonce

    async def _reset_nonce(self):
        """Reset nonce from chain (after error)."""
        async with self._nonce_lock:
            self._nonce = None

    async def _send_tx(self, tx_func, *args, value: int = 0) -> str:
        """Build, sign, and send a transaction. Returns tx hash hex."""
        nonce = await self._next_nonce()
        gas_price = await self._w3.eth.gas_price

        tx_overrides: dict = {
            "from": self._account.address,
            "nonce": nonce,
            "gasPrice": gas_price,
            "chainId": self._cfg.chain_id,
        }
        if value > 0:
            tx_overrides["value"] = value

        tx = await tx_func(*args).build_transaction(tx_overrides)

        # Estimate gas with 20% buffer
        try:
            gas_estimate = await self._w3.eth.estimate_gas(tx)
            tx["gas"] = int(gas_estimate * 1.2)
        except ContractLogicError as e:
            await self._reset_nonce()
            raise RuntimeError(f"Contract revert: {e}") from e

        signed = self._account.sign_transaction(tx)
        try:
            tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
            await self._reset_nonce()  # Phase 54: reset stale nonce on send failure
            raise
        return tx_hash.hex()

    async def wait_for_receipt(self, tx_hash: str, timeout: int = 60) -> dict:
        """Wait for transaction receipt."""
        tx_bytes = bytes.fromhex(tx_hash.removeprefix("0x"))
        receipt = await self._w3.eth.wait_for_transaction_receipt(
            tx_bytes, timeout=timeout
        )
        return dict(receipt)

    # --- PoACVerifier ---

    async def verify_single(self, device_id: bytes, record: PoACRecord) -> str:
        """Submit a single PoAC record for verification. Returns tx hash."""
        raw_body = _record_raw_body(record)
        tx_hash = await self._send_tx(
            self._verifier.functions.verifyPoAC,
            device_id,
            raw_body,
            record.signature,
        )
        log.info(
            "Submitted verifyPoAC: device=%s counter=%d tx=%s",
            device_id.hex()[:16], record.monotonic_ctr, tx_hash[:16],
        )
        return tx_hash

    async def verify_batch(
        self,
        device_ids: Sequence[bytes],
        records: Sequence[PoACRecord],
    ) -> str:
        """Submit a batch of PoAC records for verification. Returns tx hash."""
        raw_bodies = [_record_raw_body(r) for r in records]
        signatures = [r.signature for r in records]
        tx_hash = await self._send_tx(
            self._verifier.functions.verifyPoACBatch,
            list(device_ids),
            raw_bodies,
            signatures,
        )
        log.info(
            "Submitted verifyPoACBatch: %d records, tx=%s",
            len(records), tx_hash[:16],
        )
        return tx_hash

    async def is_record_verified(self, record_hash: bytes) -> bool:
        return await self._verifier.functions.isRecordVerified(record_hash).call()

    async def get_verified_count(self, device_id: bytes) -> int:
        return await self._verifier.functions.getVerifiedCount(device_id).call()

    # --- BountyMarket ---

    async def submit_evidence(
        self,
        bounty_id: int,
        device_id: bytes,
        record: PoACRecord,
    ) -> str:
        """Submit bounty evidence for a verified record. Returns tx hash."""
        if not self._bounty_market:
            raise RuntimeError("BountyMarket address not configured")
        tx_hash = await self._send_tx(
            self._bounty_market.functions.submitEvidence,
            bounty_id,
            device_id,
            record.record_hash,
            record.lat_fixed,
            record.lon_fixed,
            record.timestamp_ms,
        )
        log.info(
            "Submitted evidence: bounty=%d device=%s tx=%s",
            bounty_id, device_id.hex()[:16], tx_hash[:16],
        )
        return tx_hash

    async def get_bounty(self, bounty_id: int) -> dict | None:
        if not self._bounty_market:
            return None
        try:
            result = await self._bounty_market.functions.getBounty(bounty_id).call()
            return {
                "bounty_id": result[0],
                "creator": result[1],
                "reward_wei": result[2],
                "status": result[15],
            }
        except ContractLogicError:
            return None

    # --- DeviceRegistry ---

    async def is_device_active(self, device_id: bytes) -> bool:
        if not self._registry:
            return True  # Assume active if registry not configured
        return await self._registry.functions.isDeviceActive(device_id).call()

    async def get_device_pubkey(self, device_id: bytes) -> bytes | None:
        """Fetch device public key from on-chain registry."""
        if not self._registry:
            return None
        try:
            pubkey = await self._registry.functions.getDevicePubkey(device_id).call()
            return bytes(pubkey) if pubkey else None
        except ContractLogicError:
            return None

    async def get_reputation(self, device_id: bytes) -> int:
        if not self._registry:
            return 0
        return await self._registry.functions.getReputationScore(device_id).call()

    async def register_device_tiered(
        self, pubkey_bytes: bytes, tier: str = "Standard",
        attestation_proof: bytes = b"",
        certificate_hash: bytes = b"",    # Phase 9: optional 32-byte cert hash
    ) -> str:
        """Register device with specific tier. Returns tx hash."""
        if not self._registry:
            raise RuntimeError("DeviceRegistry address not configured")
        tier_int = TIER_VALUES.get(tier)
        if tier_int is None:
            raise ValueError(f"Unknown tier: {tier!r}")
        tier_cfg = await self._registry.functions.tierConfigs(tier_int).call()
        deposit = tier_cfg[0]  # depositWei
        balance_wei = await self._w3.eth.get_balance(self._account.address)
        if balance_wei < deposit:
            raise RuntimeError(
                f"Insufficient balance for {tier} registration: "
                f"have {balance_wei} wei, need {deposit} wei"
            )
        if tier == "Attested":
            if len(attestation_proof) != 64:
                raise ValueError("Attested tier requires 64-byte attestation proof")
            if certificate_hash and len(certificate_hash) == 32:
                # Phase 9: call registerAttestedWithCert
                tx_hash = await self._send_tx(
                    self._registry.functions.registerAttestedWithCert,
                    pubkey_bytes, attestation_proof, certificate_hash,
                    value=deposit,
                )
            else:
                # Backward compat: 2-arg registerAttested
                tx_hash = await self._send_tx(
                    self._registry.functions.registerAttested,
                    pubkey_bytes, attestation_proof, value=deposit,
                )
        else:
            tx_hash = await self._send_tx(
                self._registry.functions.registerTieredDevice,
                pubkey_bytes, tier_int, value=deposit,
            )
        log.info(
            "Device registered: tier=%s pubkey=%s... deposit=%d wei tx=%s...",
            tier, pubkey_bytes.hex()[:16], deposit, tx_hash[:16],
        )
        return tx_hash

    async def register_device(self, pubkey_bytes: bytes) -> str:
        """Backward-compat wrapper: Standard tier registration."""
        return await self.register_device_tiered(pubkey_bytes, tier="Standard")

    async def ensure_device_registered_tiered(
        self, device_id: bytes, pubkey_bytes: bytes,
        tier: str = "Standard", attestation_proof: bytes = b"",
        certificate_hash: bytes = b"",    # Phase 9: optional 32-byte cert hash
    ) -> tuple[bool, "str | None"]:
        """
        Idempotent tiered registration: checks isDeviceActive first, then
        registers at the specified tier only if needed.
        Returns (success, tx_hash_or_None). Non-fatal.
        """
        if not self._registry:
            return False, None
        try:
            if await self._registry.functions.isDeviceActive(device_id).call():
                log.debug("Device already active: %s...", device_id.hex()[:16])
                return True, None
            tx_hash = await self.register_device_tiered(
                pubkey_bytes, tier, attestation_proof, certificate_hash
            )
            return True, tx_hash
        except Exception as exc:
            err_str = str(exc).lower()
            if any(p in err_str for p in ("insufficient funds", "out of gas", "f46a06ea",
                                           "execution reverted", "transaction reverted")):
                log.warning("ensure_device_registered_tiered: permanent gas/revert error (non-fatal): %s", exc)
            else:
                log.warning("ensure_device_registered_tiered failed (non-fatal, may retry): %s", exc)
            return False, None

    async def ensure_device_registered(
        self, device_id: bytes, pubkey_bytes: bytes
    ) -> tuple[bool, "str | None"]:
        """Backward-compat wrapper: Standard tier idempotent registration."""
        return await self.ensure_device_registered_tiered(
            device_id, pubkey_bytes, "Standard"
        )

    # --- ProgressAttestation ---

    async def attest_progress(
        self,
        device_id: bytes,
        baseline_hash: bytes,
        current_hash: bytes,
        metric_type: int,
        improvement_bps: int,
    ) -> str:
        """
        Submit a ProgressAttestation for measurable skill improvement.

        Args:
            device_id:       32-byte device ID (keccak256 of pubkey).
            baseline_hash:   SHA-256 of the pre-coaching PoAC body.
            current_hash:    SHA-256 of the post-coaching PoAC body.
            metric_type:     MetricType enum value (0=REACTION_TIME, 1=ACCURACY,
                             2=CONSISTENCY, 3=COMBO_EXECUTION).
            improvement_bps: Improvement in basis points (100 = 1%). Must be > 0.

        Returns:
            Transaction hash hex string.
        """
        if not self._progress:
            raise RuntimeError("PROGRESS_ATTESTATION_ADDRESS not configured")
        tx_hash = await self._send_tx(
            self._progress.functions.attestProgress,
            device_id,
            baseline_hash,
            current_hash,
            metric_type,
            improvement_bps,
        )
        log.info(
            "ProgressAttestation: device=%s metric=%d bps=%d tx=%s...",
            device_id.hex()[:16], metric_type, improvement_bps, tx_hash[:16],
        )
        return tx_hash

    # --- TeamProofAggregator ---

    async def create_team(self, team_id: bytes, device_ids: list[bytes]) -> str:
        """Register a team on-chain. Returns tx hash."""
        if not self._team_agg:
            raise RuntimeError("TEAM_AGGREGATOR_ADDRESS not configured")
        tx_hash = await self._send_tx(
            self._team_agg.functions.createTeam,
            team_id,
            device_ids,
        )
        log.info(
            "Team created: id=%s members=%d tx=%s...",
            team_id.hex()[:16], len(device_ids), tx_hash[:16],
        )
        return tx_hash

    async def submit_team_proof(
        self,
        team_id: bytes,
        record_hashes: list[bytes],
        merkle_root: bytes,
    ) -> str:
        """Submit aggregated team proof Merkle root. Returns tx hash."""
        if not self._team_agg:
            raise RuntimeError("TEAM_AGGREGATOR_ADDRESS not configured")
        tx_hash = await self._send_tx(
            self._team_agg.functions.submitTeamProof,
            team_id,
            record_hashes,
            merkle_root,
        )
        log.info(
            "TeamProof submitted: team=%s members=%d root=%s... tx=%s...",
            team_id.hex()[:16], len(record_hashes),
            merkle_root.hex()[:16], tx_hash[:16],
        )
        return tx_hash

    async def team_exists(self, team_id: bytes) -> bool:
        if not self._team_agg:
            return False
        return await self._team_agg.functions.teamExists(team_id).call()

    # --- Phase 12: Schema-aware verification + manufacturer V2 methods ---

    async def verify_poac(
        self,
        device_id: bytes,
        raw_body: bytes,
        signature: bytes,
        schema_version: int = 0,
    ) -> str:
        """Verify a single PoAC record on-chain.

        If schema_version > 0, calls verifyPoACWithSchema() so the record is
        tagged with its sensor commitment schema (1=v1 environmental, 2=v2 kinematic).
        If schema_version == 0 (default), calls the legacy verifyPoAC().

        Returns tx hash hex string.
        """
        if schema_version > 0:
            tx_hash = await self._send_tx(
                self._verifier.functions.verifyPoACWithSchema,
                device_id, raw_body, signature, schema_version,
            )
        else:
            tx_hash = await self._send_tx(
                self._verifier.functions.verifyPoAC,
                device_id, raw_body, signature,
            )
        log.info(
            "verify_poac: device=%s schema=%d tx=%s",
            device_id.hex()[:16], schema_version, tx_hash[:16],
        )
        return tx_hash

    async def register_device_attested_v2(
        self,
        pubkey: bytes,
        attestation_proof: bytes,
        manufacturer_addr: str,
    ) -> str:
        """Register an Attested-tier device via the V2 P256-verified path.

        Requires the manufacturer's P256 key to be registered via setManufacturerKey.
        When attestationEnforced=true, the signature is cryptographically verified
        against the manufacturer key via IoTeX precompile 0x0100.

        Returns tx hash hex string.
        """
        if not self._registry:
            raise RuntimeError("DeviceRegistry address not configured")
        tier_cfg = await self._registry.functions.tierConfigs(2).call()  # 2 = Attested
        deposit = tier_cfg[0]
        tx_hash = await self._send_tx(
            self._registry.functions.registerAttestedV2,
            pubkey,
            attestation_proof,
            self._w3.to_checksum_address(manufacturer_addr),
            value=deposit,
        )
        log.info(
            "registerAttestedV2: pubkey=%s manufacturer=%s tx=%s",
            pubkey.hex()[:16], manufacturer_addr[:16], tx_hash[:16],
        )
        return tx_hash

    async def get_manufacturer_key(self, manufacturer_addr: str) -> dict:
        """Fetch manufacturer P256 key from on-chain registry.

        Returns dict with keys: pubkeyX (bytes32), pubkeyY (bytes32), active (bool), name (str).
        """
        if not self._registry:
            raise RuntimeError("DeviceRegistry address not configured")
        result = await self._registry.functions.getManufacturerKey(
            self._w3.to_checksum_address(manufacturer_addr)
        ).call()
        return {
            "pubkeyX": result[0],
            "pubkeyY": result[1],
            "active":  result[2],
            "name":    result[3],
        }

    def is_manufacturer_revoked(self, manufacturer_addr: str) -> bool:
        """Check if a manufacturer address is in the local revocation cache.

        The cache is populated by watch_manufacturer_revocations(). Returns False
        if the address has never been seen as revoked (or the listener isn't running).
        """
        return manufacturer_addr.lower() in self._revoked_manufacturers

    async def watch_manufacturer_revocations(self, poll_interval: float = 30.0) -> None:
        """Background coroutine: poll ManufacturerKeyRevoked events and cache revocations.

        Intended to run as a long-lived background task:
            asyncio.create_task(chain.watch_manufacturer_revocations())

        Updates self._revoked_manufacturers set so is_manufacturer_revoked() reflects
        on-chain revocations without requiring per-call RPC queries.
        """
        if not self._registry:
            log.warning("watch_manufacturer_revocations: registry not configured")
            return
        try:
            event_filter = self._registry.events.ManufacturerKeyRevoked
        except Exception as exc:
            log.warning("watch_manufacturer_revocations: event unavailable (%s)", exc)
            return

        last_block = await self._w3.eth.block_number
        log.info("watch_manufacturer_revocations: polling every %.0fs from block %d", poll_interval, last_block)

        while True:
            await asyncio.sleep(poll_interval)
            try:
                current_block = await self._w3.eth.block_number
                if current_block <= last_block:
                    continue
                logs = await event_filter().get_logs(
                    from_block=last_block + 1, to_block=current_block
                )
                for entry in logs:
                    addr = entry["args"]["manufacturer"].lower()
                    self._revoked_manufacturers.add(addr)
                    log.info("ManufacturerKeyRevoked: %s cached as revoked", addr)
                last_block = current_block
            except Exception as exc:
                log.warning("watch_manufacturer_revocations poll error: %s", exc)

    # --- Phase 22: PHG Registry ---

    async def commit_phg_checkpoint(
        self,
        device_id: str,
        score_delta: int,
        count: int,
        biometric_hash: bytes,
    ) -> str:
        """Commit a PHG checkpoint to the on-chain registry. Returns tx hash.

        Called by the batcher after every N verified NOMINAL records.
        No-op (returns empty string) when PHG_REGISTRY_ADDRESS is not configured.
        """
        if not self._phg_registry:
            log.debug("commit_phg_checkpoint: PHG_REGISTRY_ADDRESS not configured, skipping")
            return ""
        device_id_bytes = bytes.fromhex(device_id)
        bio_hash_bytes32 = biometric_hash[:32].ljust(32, b"\x00") if biometric_hash else bytes(32)
        tx_hash = await self._send_tx(
            self._phg_registry.functions.commitCheckpoint,
            device_id_bytes,
            score_delta,
            count,
            bio_hash_bytes32,
        )
        log.info(
            "PHGCheckpoint committed: device=%s score_delta=%d count=%d tx=%s",
            device_id[:16], score_delta, count, tx_hash[:16],
        )
        return tx_hash

    async def get_phg_score(self, device_id: str) -> int:
        """Return the on-chain cumulative PHG score for a device. Returns 0 if unconfigured."""
        if not self._phg_registry:
            return 0
        return await self._phg_registry.functions.cumulativeScore(
            bytes.fromhex(device_id)
        ).call()

    async def get_phg_checkpoint_events(
        self, from_block: int, to_block: int
    ) -> list[dict]:
        """Fetch PHGCheckpointCommitted events from the PHGRegistry contract.

        Returns list of event dicts with keys: transactionHash, deviceId, cumulativeScore.
        Returns empty list if PHG_REGISTRY_ADDRESS is not configured or on error.
        """
        if not self._phg_registry:
            return []
        try:
            event_filter = self._phg_registry.events.PHGCheckpointCommitted
            events = await event_filter.get_logs(
                from_block=from_block, to_block=to_block
            )
            result = []
            for evt in events:
                result.append({
                    "transactionHash": evt["transactionHash"],
                    "deviceId":        evt["args"]["deviceId"].hex(),
                    "cumulativeScore": evt["args"]["cumulativeScore"],
                })
            return result
        except Exception as exc:
            log.warning("get_phg_checkpoint_events error: %s", exc)
            return []

    # --- Phase 23: Identity Continuity Registry ---

    async def attest_continuity(
        self,
        old_device_id: str,
        new_device_id: str,
        biometric_proof_hash: bytes,
    ) -> str:
        """Attest that new_device_id is the biometric continuation of old_device_id.

        Transfers the old device's PHG score to the new device on-chain.
        No-op (returns empty string) when IDENTITY_REGISTRY_ADDRESS is not configured.

        Args:
            old_device_id:        Source device identifier (hex string, 32 bytes).
            new_device_id:        Destination device identifier (hex string, 32 bytes).
            biometric_proof_hash: 32-byte SHA-256 proof of fingerprint proximity.

        Returns:
            Transaction hash hex string, or "" if registry not configured.
        """
        if not self._identity_registry:
            log.debug("attest_continuity: IDENTITY_REGISTRY_ADDRESS not configured, skipping")
            return ""
        old_bytes = bytes.fromhex(old_device_id)
        new_bytes = bytes.fromhex(new_device_id)
        proof_bytes32 = biometric_proof_hash[:32].ljust(32, b"\x00")
        tx_hash = await self._send_tx(
            self._identity_registry.functions.attestContinuity,
            old_bytes,
            new_bytes,
            proof_bytes32,
        )
        log.info(
            "ContinuityAttested: old=%s new=%s tx=%s",
            old_device_id[:16], new_device_id[:16], tx_hash[:16],
        )
        return tx_hash

    async def is_continuation_of(self, new_device_id: str, old_device_id: str) -> bool:
        """Return True if new_device_id inherited its score from old_device_id."""
        if not self._identity_registry:
            return False
        return await self._identity_registry.functions.isContinuationOf(
            bytes.fromhex(new_device_id),
            bytes.fromhex(old_device_id),
        ).call()

    async def get_canonical_root(self, device_id: str) -> str:
        """Walk the continuity chain and return the canonical root device ID (hex)."""
        if not self._identity_registry:
            return device_id
        root_bytes = await self._identity_registry.functions.getCanonicalRoot(
            bytes.fromhex(device_id)
        ).call()
        return root_bytes.hex()

    # --- Phase 26: PITL Session Registry ---

    async def submit_pitl_proof(
        self,
        device_id: str,
        proof_bytes: bytes,
        feature_commitment: int,
        humanity_prob_int: int,
        inference_code: int,
        nullifier_hash: int,
        epoch: int,
    ) -> str:
        """Submit a PITL ZK session proof to PITLSessionRegistry.

        Submits to PITLSessionRegistryV2 (Phase 62, PITL_SESSION_REGISTRY_V2_ADDRESS) if
        configured; falls back to v1 (PITL_SESSION_REGISTRY_ADDRESS). No-op if neither is set.

        Args:
            device_id:          64-char hex device identifier.
            proof_bytes:        256-byte Groth16 proof wire format.
            feature_commitment: Poseidon(8)(scaledFeatures[0..6], inferenceCodeFromBody) — Phase 62.
            humanity_prob_int:  l5_humanity × 1000 ∈ [0, 1000].
            inference_code:     8-bit VAPI inference result (e.g. 0x00 CLEAN, 0x30 L4 anomaly).
                                Circuit C2 enforces this ∉ [40, 42] — a proof with a hard
                                cheat code is ungenerable, so submission would never reach here.
            nullifier_hash:     Poseidon(deviceIdHash, epoch) as integer.
            epoch:              Block epoch (block.number / EPOCH_BLOCKS).

        Returns:
            Transaction hash hex string, or "" if registry not configured.
        """
        # Phase 62: prefer v2 (Phase 62 C3 circuit) if configured; fallback to v1
        registry = self._pitl_registry_v2 or self._pitl_registry
        registry_ver = "v2" if self._pitl_registry_v2 else "v1"
        if not registry:
            log.debug("submit_pitl_proof: no PITL registry configured, skipping")
            return ""
        device_id_bytes32 = bytes.fromhex(device_id)
        tx_hash = await self._send_tx(
            registry.functions.submitPITLProof,
            device_id_bytes32,
            proof_bytes,
            feature_commitment,
            humanity_prob_int,
            inference_code,
            nullifier_hash,
            epoch,
        )
        log.info(
            "PITLSessionProof submitted (%s): device=%s hp=%d inference=0x%02x tx=%s",
            registry_ver, device_id[:16], humanity_prob_int, inference_code, tx_hash[:16],
        )
        return tx_hash

    async def mint_phg_credential(
        self,
        device_id: str,
        nullifier_hash: str,
        feature_commitment: str,
        humanity_prob_int: int,
    ) -> str:
        """Mint a soulbound PHGCredential on-chain for the device.

        No-op (returns empty string) when PHG_CREDENTIAL_ADDRESS is not configured.

        Args:
            device_id:          Hex device identifier (40–64 chars, no 0x prefix).
            nullifier_hash:     Hex nullifier from PITLProver (with or without 0x).
            feature_commitment: Hex feature commitment from PITLProver (with or without 0x).
            humanity_prob_int:  humanity_prob × 1000, range [0, 1000].

        Returns:
            Transaction hash hex string, or "" if credential contract not configured.
        """
        if not self._phg_credential:
            log.debug("mint_phg_credential: PHG_CREDENTIAL_ADDRESS not configured, skipping")
            return ""
        dev_b32  = bytes.fromhex(device_id.replace("0x", "").ljust(64, "0"))[:32]
        null_b32 = bytes.fromhex(nullifier_hash.replace("0x", "").ljust(64, "0"))[:32]
        fc_b32   = bytes.fromhex(feature_commitment.replace("0x", "").ljust(64, "0"))[:32]
        tx_hash = await self._send_tx(
            self._phg_credential.functions.mintCredential,
            dev_b32,
            null_b32,
            fc_b32,
            humanity_prob_int,
        )
        log.info(
            "PHGCredential minted: device=%s hp_int=%d tx=%s",
            device_id[:16], humanity_prob_int, tx_hash[:16],
        )
        return tx_hash

    async def has_phg_credential(self, device_id: str) -> bool:
        """Returns True if device has a minted credential on-chain.

        Returns False when PHG_CREDENTIAL_ADDRESS is not configured.
        """
        if not self._phg_credential:
            return False
        dev_b32 = bytes.fromhex(device_id.replace("0x", "").ljust(64, "0"))[:32]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._phg_credential.functions.hasCredential(dev_b32).call
        )

    # --- Phase 37: Credential Enforcement ---

    async def suspend_phg_credential(self, device_id: str,
                                      evidence_hash: bytes, duration_s: int) -> str:
        """Suspend a PHGCredential on-chain (Phase 37).

        No-op (returns empty string) when PHG_CREDENTIAL_ADDRESS is not configured.
        On-chain suspension failure is non-fatal — callers catch and log.
        """
        if not self._phg_credential:
            log.debug("suspend_phg_credential: PHG_CREDENTIAL_ADDRESS not configured, skipping")
            return ""
        dev_b32 = bytes.fromhex(device_id.replace("0x", "").ljust(64, "0"))[:32]
        ev_b32_raw = evidence_hash if len(evidence_hash) >= 32 else evidence_hash.ljust(32, b'\x00')
        ev_b32 = ev_b32_raw[:32]
        tx_hash = await self._send_tx(
            self._phg_credential.functions.suspend,
            dev_b32,
            ev_b32,
            duration_s,
        )
        log.info(
            "PHGCredential suspended: device=%s duration=%ds tx=%s",
            device_id[:16], duration_s, tx_hash[:16],
        )
        return tx_hash

    async def reinstate_phg_credential(self, device_id: str) -> str:
        """Reinstate a suspended PHGCredential on-chain (Phase 37).

        No-op (returns empty string) when PHG_CREDENTIAL_ADDRESS is not configured.
        On-chain reinstatement failure is non-fatal — callers catch and log.
        """
        if not self._phg_credential:
            log.debug("reinstate_phg_credential: PHG_CREDENTIAL_ADDRESS not configured, skipping")
            return ""
        dev_b32 = bytes.fromhex(device_id.replace("0x", "").ljust(64, "0"))[:32]
        tx_hash = await self._send_tx(self._phg_credential.functions.reinstate, dev_b32)
        log.info("PHGCredential reinstated: device=%s tx=%s", device_id[:16], tx_hash[:16])
        return tx_hash

    async def is_phg_credential_active(self, device_id: str) -> bool:
        """Returns True if device has an active (non-suspended) credential (Phase 37).

        Fails open: returns True when PHG_CREDENTIAL_ADDRESS is not configured,
        so unconfigured environments do not block tournament access.
        """
        if not self._phg_credential:
            return True
        dev_b32 = bytes.fromhex(device_id.replace("0x", "").ljust(64, "0"))[:32]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._phg_credential.functions.isActive(dev_b32).call
        )

    # --- Phase 34: Federated Threat Registry ---

    async def report_federated_cluster(self, cluster_hash: str) -> str:
        """Anchor a cross-bridge confirmed cluster hash on-chain (Phase 34).

        No-op (returns empty string) when FEDERATED_THREAT_REGISTRY_ADDRESS is not configured.

        Args:
            cluster_hash: 16-char hex fingerprint from compute_cluster_hash().

        Returns:
            Transaction hash hex string, or "" if registry not configured.
        """
        if not self._federated_threat_registry:
            log.debug("report_federated_cluster: FEDERATED_THREAT_REGISTRY_ADDRESS not configured, skipping")
            return ""
        # Pad 16-char hex to full 32-byte bytes32
        padded = bytes.fromhex(cluster_hash.ljust(64, "0"))
        tx_hash = await self._send_tx(
            self._federated_threat_registry.functions.reportCluster,
            padded,
        )
        log.info("FederatedCluster anchored: hash=%s tx=%s", cluster_hash, tx_hash[:16])
        return tx_hash

    # --- Phase 55: ioID Device Identity Registry ---

    async def ensure_ioid_registered(self, device_id: str, store) -> str:
        """Ensure the device is registered in the ioID registry (Phase 55).

        Derives: device_address = last 20 bytes of device_id bytes32.
        DID = "did:io:" + checksum(device_address).
        Idempotent: if already registered on-chain, only updates local store.

        Args:
            device_id: 64-char hex device identifier.
            store:     Store instance for local persistence.

        Returns:
            DID string (e.g. "did:io:0x..."), or "" if registry not configured.
        """
        if not self._ioid_registry:
            log.debug("ensure_ioid_registered: IOID_REGISTRY_ADDRESS not configured, skipping")
            return ""

        # Derive device address = last 20 bytes of 32-byte device_id
        dev_bytes = bytes.fromhex(device_id.ljust(64, "0"))[:32]
        device_address = self._w3.to_checksum_address(("0x" + dev_bytes[-20:].hex()))
        did = f"did:io:{device_address}"

        # Check local store first (avoid unnecessary chain call)
        existing = store.get_ioid_device(device_id)
        if existing and existing.get("did"):
            log.debug("ensure_ioid_registered: device %s already in local store", device_id[:16])
            return existing["did"]

        # Check on-chain registration
        try:
            dev_b32 = dev_bytes
            is_reg = await self._w3.eth.call({
                "to": self._ioid_registry.address,
                "data": self._ioid_registry.encodeABI("isRegistered", [dev_b32]),
            })
            already_registered = bool(int(is_reg.hex() or "0", 16))
        except Exception as exc:
            log.debug("ensure_ioid_registered: isRegistered check failed: %s", exc)
            already_registered = False

        tx_hash = ""
        if not already_registered:
            try:
                tx_hash = await self._send_tx(
                    self._ioid_registry.functions.register,
                    dev_b32,
                    device_address,
                    did,
                )
                log.info(
                    "ioID registered: device=%s did=%s tx=%s",
                    device_id[:16], did, tx_hash[:16],
                )
            except Exception as exc:
                log.warning("ensure_ioid_registered: register tx failed: %s", exc)
        else:
            log.debug("ensure_ioid_registered: device %s already on-chain", device_id[:16])

        # Persist locally regardless of on-chain outcome (store the DID for future use)
        try:
            store.store_ioid_device(device_id, device_address, did, tx_hash)
        except Exception as exc:
            log.debug("ensure_ioid_registered: local store failed (non-fatal): %s", exc)

        return did

    async def ioid_increment_session(self, device_id: str) -> str:
        """Increment the session counter for a registered ioID device (Phase 55).

        No-op if registry not configured or device not registered.

        Args:
            device_id: 64-char hex device identifier.

        Returns:
            Transaction hash hex string, or "" if registry not configured.
        """
        if not self._ioid_registry:
            return ""
        try:
            dev_b32 = bytes.fromhex(device_id.ljust(64, "0"))[:32]
            tx_hash = await self._send_tx(
                self._ioid_registry.functions.incrementSession,
                dev_b32,
            )
            log.debug("ioID session incremented: device=%s tx=%s", device_id[:16], tx_hash[:16])
            return tx_hash
        except Exception as exc:
            log.debug("ioid_increment_session failed (non-fatal): %s", exc)
            return ""

    # --- Phase 56: Tournament Passport ---

    async def submit_tournament_passport(
        self,
        device_id: str,
        proof: bytes,
        nullifiers: list,
        passport_hash: bytes,
        ioid_token_id: int,
        min_humanity_int: int,
        epoch: int,
    ) -> str:
        """Submit a ZK tournament passport to PITLTournamentPassport (Phase 56).

        No-op (returns empty string) when TOURNAMENT_PASSPORT_ADDRESS is not configured.

        Args:
            device_id:        64-char hex device identifier.
            proof:            ZK proof bytes (256 bytes for real Groth16, empty for mock mode).
            nullifiers:       List of 5 bytes32 session nullifier hashes.
            passport_hash:    Poseidon(nullifiers) as bytes32.
            ioid_token_id:    ioID token ID (0 in mock mode).
            min_humanity_int: Minimum humanity_prob * 1000 across sessions.
            epoch:            Current epoch number.

        Returns:
            Transaction hash hex string, or "" if not configured.
        """
        if not self._tournament_passport:
            log.debug("submit_tournament_passport: TOURNAMENT_PASSPORT_ADDRESS not configured, skipping")
            return ""
        dev_b32 = bytes.fromhex(device_id.ljust(64, "0"))[:32]
        null_b32 = [bytes.fromhex(n.replace("0x", "").ljust(64, "0"))[:32] for n in nullifiers]
        ph_b32   = passport_hash if isinstance(passport_hash, bytes) else bytes.fromhex(str(passport_hash).replace("0x", "").ljust(64, "0"))[:32]
        tx_hash = await self._send_tx(
            self._tournament_passport.functions.submitPassport,
            dev_b32,
            proof,
            null_b32,
            ph_b32,
            ioid_token_id,
            min_humanity_int,
            epoch,
        )
        log.info(
            "TournamentPassport submitted: device=%s min_hp=%d tx=%s",
            device_id[:16], min_humanity_int, tx_hash[:16],
        )
        return tx_hash

    async def get_tournament_passport(self, device_id: str) -> dict:
        """Read a device's tournament passport from on-chain (Phase 56).

        Returns dict with passport fields, or empty dict if not configured / not found.
        """
        if not self._tournament_passport:
            return {}
        try:
            dev_b32 = bytes.fromhex(device_id.ljust(64, "0"))[:32]
            result = await self._tournament_passport.functions.getPassport(dev_b32).call()
            return {
                "passport_hash":    result[0].hex() if result[0] else "",
                "ioid_token_id":    result[1],
                "min_humanity_int": result[2],
                "issued_at":        result[3],
                "active":           result[4],
            }
        except Exception as exc:
            log.debug("get_tournament_passport chain call failed (non-fatal): %s", exc)
            return {}
