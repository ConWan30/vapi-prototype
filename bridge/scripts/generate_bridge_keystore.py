#!/usr/bin/env python3
"""
generate_bridge_keystore.py — VAPI Bridge Key Security Utility

Reads the BRIDGE_PRIVATE_KEY environment variable and encrypts it to an
Ethereum keystore JSON file (EIP-55 compatible, eth_account format).

Usage:
    BRIDGE_PRIVATE_KEY=0x<key> python bridge/scripts/generate_bridge_keystore.py \\
        --output /etc/vapi/bridge-keystore.json

The password is read interactively (not stored anywhere). After confirming
that the bridge loads correctly with BRIDGE_PRIVATE_KEY_SOURCE=keystore,
delete BRIDGE_PRIVATE_KEY from your environment and CI secrets.

To use the keystore in the bridge, set these env vars:
    BRIDGE_PRIVATE_KEY_SOURCE=keystore
    BRIDGE_KEYSTORE_PATH=/etc/vapi/bridge-keystore.json
    BRIDGE_KEYSTORE_PASSWORD=<your-password>
"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).parents[1]))

from eth_account import Account


def main():
    parser = argparse.ArgumentParser(
        description="Encrypt BRIDGE_PRIVATE_KEY to an Ethereum keystore file."
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Path to write the keystore JSON file (e.g. /etc/vapi/bridge-keystore.json)",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite output file if it already exists",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        print(f"ERROR: {output_path} already exists. Use --force to overwrite.")
        sys.exit(1)

    private_key = os.environ.get("BRIDGE_PRIVATE_KEY", "")
    if not private_key:
        print("ERROR: BRIDGE_PRIVATE_KEY env var is not set.")
        sys.exit(1)

    try:
        account = Account.from_key(private_key)
    except Exception as e:
        print(f"ERROR: Invalid private key: {e}")
        sys.exit(1)

    print(f"Bridge address: {account.address}")
    print("Enter keystore password (will not be echoed):")
    password = getpass.getpass("> ")
    if not password:
        print("ERROR: Password cannot be empty.")
        sys.exit(1)

    print("Confirm password:")
    confirm = getpass.getpass("> ")
    if password != confirm:
        print("ERROR: Passwords do not match.")
        sys.exit(1)

    keystore = Account.encrypt(private_key, password)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(keystore, f, indent=2)

    print(f"\nKeystore written to: {output_path}")
    print(f"Bridge address:      {account.address}")
    print("")
    print("Next steps:")
    print("  1. Set BRIDGE_PRIVATE_KEY_SOURCE=keystore in your environment")
    print(f"  2. Set BRIDGE_KEYSTORE_PATH={output_path}")
    print("  3. Set BRIDGE_KEYSTORE_PASSWORD=<your-password> (or use a secrets manager)")
    print("  4. Start the bridge and confirm it logs: 'Bridge key loaded from keystore'")
    print("  5. Delete BRIDGE_PRIVATE_KEY from env after confirming keystore works")
    print("")
    print("WARNING: If you lose the password, the keystore CANNOT be recovered.")


if __name__ == "__main__":
    main()
