#!/usr/bin/env python3
"""
VAPI Testnet Smoke Test
=======================
Calls read-only view functions on every deployed IoTeX testnet contract.
Addresses loaded from contracts/deployed-addresses.json.

Usage:
    python scripts/testnet_smoke_test.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IOTEX_TESTNET_RPC = "https://babel-api.testnet.iotex.io"

# Resolve path to deployed-addresses.json relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
ADDRESSES_FILE = PROJECT_ROOT / "contracts" / "deployed-addresses.json"

# ---------------------------------------------------------------------------
# Minimal ABI snippets — only the view functions we call
# ---------------------------------------------------------------------------

ABI_UINT256_VIEW = lambda name: [
    {
        "name": name,
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    }
]

ABI_BOOL_VIEW = lambda name: [
    {
        "name": name,
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    }
]

ABI_ADDRESS_VIEW = lambda name: [
    {
        "name": name,
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
    }
]

# locked(uint256 tokenId) returns bool — PHGCredential ERC-5192
ABI_LOCKED = [
    {
        "name": "locked",
        "type": "function",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "pure",
    }
]

# getReportCount(bytes32 clusterHash) returns uint256 — FederatedThreatRegistry
ABI_GET_REPORT_COUNT = [
    {
        "name": "getReportCount",
        "type": "function",
        "inputs": [{"name": "clusterHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    }
]

# CHEAT_PENALTY() — constant on SkillOracle
ABI_CHEAT_PENALTY = [
    {
        "name": "CHEAT_PENALTY",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint32"}],
        "stateMutability": "view",
    }
]

# ---------------------------------------------------------------------------
# Test definitions
# Each entry: (display_name, address_key, abi, fn_name, call_args, expected_hint)
# ---------------------------------------------------------------------------

def build_tests(addresses: dict) -> list:
    """
    Build the test list once addresses are loaded.
    Returns a list of dicts with keys:
        contract, address, fn, abi, call_args, expected_hint
    """
    tests = [
        {
            "contract": "PoACVerifier",
            "address": addresses["PoACVerifier"],
            "fn": "totalVerifiedCount",
            "abi": ABI_UINT256_VIEW("totalVerifiedCount"),
            "call_args": [],
            "expected_hint": "uint256 >= 0 (expect 0 on fresh deploy)",
        },
        {
            "contract": "TieredDeviceRegistry",
            "address": addresses["TieredDeviceRegistry"],
            "fn": "deviceCount",
            "abi": ABI_UINT256_VIEW("deviceCount"),
            "call_args": [],
            "expected_hint": "uint256 >= 0",
        },
        {
            "contract": "PHGCredential (locked)",
            "address": addresses["PHGCredential"],
            "fn": "locked",
            "abi": ABI_LOCKED,
            "call_args": [0],  # tokenId 0 — always returns true (pure function)
            "expected_hint": "bool = True (soulbound, always locked)",
        },
        {
            "contract": "PHGCredential (bridge)",
            "address": addresses["PHGCredential"],
            "fn": "bridge",
            "abi": ABI_ADDRESS_VIEW("bridge"),
            "call_args": [],
            "expected_hint": "address = deployer bridge",
        },
        {
            "contract": "PITLSessionRegistry (bridge)",
            "address": addresses["PITLSessionRegistry"],
            "fn": "bridge",
            "abi": ABI_ADDRESS_VIEW("bridge"),
            "call_args": [],
            "expected_hint": "address = deployer bridge",
        },
        {
            "contract": "PHGRegistry (bridge)",
            "address": addresses["PHGRegistry"],
            "fn": "bridge",
            "abi": ABI_ADDRESS_VIEW("bridge"),
            "call_args": [],
            "expected_hint": "address = deployer bridge",
        },
        {
            "contract": "FederatedThreatRegistry (bridge)",
            "address": addresses["FederatedThreatRegistry"],
            "fn": "bridge",
            "abi": ABI_ADDRESS_VIEW("bridge"),
            "call_args": [],
            "expected_hint": "address = deployer bridge",
        },
        {
            "contract": "FederatedThreatRegistry (getReportCount)",
            "address": addresses["FederatedThreatRegistry"],
            "fn": "getReportCount",
            "abi": ABI_GET_REPORT_COUNT,
            "call_args": [b"\x00" * 32],  # zero bytes32
            "expected_hint": "uint256 = 0 (no clusters reported)",
        },
        {
            "contract": "SkillOracle (CHEAT_PENALTY)",
            "address": addresses["SkillOracle"],
            "fn": "CHEAT_PENALTY",
            "abi": ABI_CHEAT_PENALTY,
            "call_args": [],
            "expected_hint": "uint32 = 200",
        },
        {
            "contract": "SkillOracle (totalProfileCount)",
            "address": addresses["SkillOracle"],
            "fn": "totalProfileCount",
            "abi": ABI_UINT256_VIEW("totalProfileCount"),
            "call_args": [],
            "expected_hint": "uint256 >= 0",
        },
        {
            "contract": "BountyMarket (nextBountyId)",
            "address": addresses["BountyMarket"],
            "fn": "nextBountyId",
            "abi": ABI_UINT256_VIEW("nextBountyId"),
            "call_args": [],
            "expected_hint": "uint256 = 1 (starts at 1, 0 is POAC_NO_BOUNTY sentinel)",
        },
        {
            "contract": "BountyMarket (platformFeeBps)",
            "address": addresses["BountyMarket"],
            "fn": "platformFeeBps",
            "abi": [
                {
                    "name": "platformFeeBps",
                    "type": "function",
                    "inputs": [],
                    "outputs": [{"name": "", "type": "uint16"}],
                    "stateMutability": "view",
                }
            ],
            "call_args": [],
            "expected_hint": "uint16 = platform fee bps",
        },
    ]
    return tests


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_smoke_test():
    # Load deployed addresses
    if not ADDRESSES_FILE.exists():
        print(f"ERROR: {ADDRESSES_FILE} not found. Deploy contracts first.")
        sys.exit(1)

    with open(ADDRESSES_FILE) as f:
        addresses = json.load(f)

    print(f"VAPI Testnet Smoke Test")
    print(f"RPC: {IOTEX_TESTNET_RPC}")
    print(f"Addresses: {ADDRESSES_FILE}")
    print(f"Network: {addresses.get('_network', 'unknown')} (chainId {addresses.get('_chainId', '?')})")
    print()

    # Connect
    w3 = AsyncWeb3(AsyncHTTPProvider(IOTEX_TESTNET_RPC))
    try:
        chain_id = await w3.eth.chain_id
        latest = await w3.eth.block_number
        print(f"Connected  chain_id={chain_id}  latest_block={latest}")
    except Exception as e:
        print(f"ERROR: Cannot connect to RPC: {e}")
        sys.exit(1)
    print()

    tests = build_tests(addresses)

    # Column widths
    COL_CONTRACT = 40
    COL_ADDRESS = 44
    COL_FN = 30
    COL_RESULT = 50
    COL_STATUS = 6

    header = (
        f"{'CONTRACT':<{COL_CONTRACT}} "
        f"{'ADDRESS':<{COL_ADDRESS}} "
        f"{'FUNCTION':<{COL_FN}} "
        f"{'RESULT':<{COL_RESULT}} "
        f"STATUS"
    )
    separator = "-" * (COL_CONTRACT + COL_ADDRESS + COL_FN + COL_RESULT + COL_STATUS + 4)
    print(header)
    print(separator)

    passed = 0
    failed = 0
    results = []

    for t in tests:
        contract_name = t["contract"]
        address = t["address"]
        fn_name = t["fn"]
        abi = t["abi"]
        call_args = t["call_args"]

        try:
            contract = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(address),
                abi=abi,
            )
            fn = getattr(contract.functions, fn_name)
            result = await fn(*call_args).call()
            result_str = str(result)
            if len(result_str) > COL_RESULT - 1:
                result_str = result_str[: COL_RESULT - 4] + "..."
            status = "PASS"
            passed += 1
        except Exception as e:
            result_str = f"ERROR: {e}"
            if len(result_str) > COL_RESULT - 1:
                result_str = result_str[: COL_RESULT - 4] + "..."
            status = "FAIL"
            failed += 1

        short_addr = address[:20] + "..." + address[-8:] if len(address) > 30 else address
        row = (
            f"{contract_name:<{COL_CONTRACT}} "
            f"{short_addr:<{COL_ADDRESS}} "
            f"{fn_name:<{COL_FN}} "
            f"{result_str:<{COL_RESULT}} "
            f"{status}"
        )
        print(row)
        results.append((contract_name, status))

    print(separator)
    total = passed + failed
    print(f"\nSummary: {passed}/{total} contracts LIVE")
    if failed == 0:
        print("All checks PASSED — testnet deployment healthy.")
    else:
        print(f"WARNING: {failed} check(s) FAILED — inspect errors above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
