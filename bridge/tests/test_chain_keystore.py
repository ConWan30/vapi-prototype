"""
test_chain_keystore.py — Phase 11 Priority 3

Tests for bridge key security configuration: the new bridge_private_key_source,
keystore_path, and keystore_password_env Config fields, and the updated validate()
logic that supports both "env" (plaintext) and "keystore" (encrypted) sources.

These tests run against vapi_bridge.config only — no web3/eth_account dependency.
The actual keystore encryption round-trip (generate_keystore → ChainClient load)
requires a web3-enabled environment and is covered by integration test notes in
bridge/attestation-enforcement-guide.md §7 (Mainnet Transition Checklist).
"""

import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# --- Path setup ---------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parents[1]))  # bridge/

from vapi_bridge.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**env_overrides):
    """Build a Config with test env vars set, clearing BRIDGE_ vars for isolation."""
    base_env = {
        "IOTEX_RPC_URL": "http://localhost:8545",
        "POAC_VERIFIER_ADDRESS": "0x" + "01" * 20,
        "BRIDGE_PRIVATE_KEY": "0x" + "ab" * 32,
        "DUALSHOCK_ENABLED": "true",
        # Clear keystore vars by default
        "BRIDGE_PRIVATE_KEY_SOURCE": "env",
        "BRIDGE_KEYSTORE_PATH": "",
        "BRIDGE_KEYSTORE_PASSWORD_ENV": "BRIDGE_KEYSTORE_PASSWORD",
    }
    base_env.update(env_overrides)
    with patch.dict(os.environ, base_env, clear=False):
        return Config()


# ---------------------------------------------------------------------------
# 1. New field defaults
# ---------------------------------------------------------------------------

def test_bridge_private_key_source_defaults_to_env():
    """bridge_private_key_source defaults to 'env' when env var is not set."""
    with patch.dict(os.environ, {}, clear=False):
        env = {k: v for k, v in os.environ.items()
               if k != "BRIDGE_PRIVATE_KEY_SOURCE"}
        with patch.dict(os.environ, env, clear=True):
            cfg = Config()
    assert cfg.bridge_private_key_source == "env"


def test_keystore_path_defaults_to_empty():
    """keystore_path defaults to empty string when env var is not set."""
    env = {k: v for k, v in os.environ.items() if k != "BRIDGE_KEYSTORE_PATH"}
    with patch.dict(os.environ, env, clear=True):
        cfg = Config()
    assert cfg.keystore_path == ""


def test_keystore_password_env_defaults_to_standard_name():
    """keystore_password_env defaults to 'BRIDGE_KEYSTORE_PASSWORD'."""
    env = {k: v for k, v in os.environ.items()
           if k != "BRIDGE_KEYSTORE_PASSWORD_ENV"}
    with patch.dict(os.environ, env, clear=True):
        cfg = Config()
    assert cfg.keystore_password_env == "BRIDGE_KEYSTORE_PASSWORD"


# ---------------------------------------------------------------------------
# 2. validate() — "env" source
# ---------------------------------------------------------------------------

def test_validate_env_source_with_key_passes():
    """validate() reports no key error when source='env' and bridge_private_key is set."""
    cfg = _make_config(
        BRIDGE_PRIVATE_KEY_SOURCE="env",
        BRIDGE_PRIVATE_KEY="0x" + "ab" * 32,
    )
    errors = cfg.validate()
    assert not any("BRIDGE_PRIVATE_KEY" in e for e in errors)


def test_validate_env_source_without_key_fails():
    """validate() reports BRIDGE_PRIVATE_KEY error when source='env' and key is empty."""
    cfg = _make_config(
        BRIDGE_PRIVATE_KEY_SOURCE="env",
        BRIDGE_PRIVATE_KEY="",
    )
    errors = cfg.validate()
    assert any("BRIDGE_PRIVATE_KEY" in e for e in errors)


# ---------------------------------------------------------------------------
# 3. validate() — "keystore" source
# ---------------------------------------------------------------------------

def test_validate_keystore_source_without_path_fails():
    """validate() reports BRIDGE_KEYSTORE_PATH error when source='keystore' and path is empty."""
    cfg = _make_config(
        BRIDGE_PRIVATE_KEY_SOURCE="keystore",
        BRIDGE_PRIVATE_KEY="",
        BRIDGE_KEYSTORE_PATH="",
    )
    errors = cfg.validate()
    assert any("BRIDGE_KEYSTORE_PATH" in e for e in errors)


def test_validate_keystore_source_with_path_passes():
    """validate() reports no key error when source='keystore' and keystore_path is set."""
    cfg = _make_config(
        BRIDGE_PRIVATE_KEY_SOURCE="keystore",
        BRIDGE_PRIVATE_KEY="",
        BRIDGE_KEYSTORE_PATH="/etc/vapi/bridge-keystore.json",
    )
    errors = cfg.validate()
    assert not any("BRIDGE_KEYSTORE_PATH" in e for e in errors)
    assert not any("BRIDGE_PRIVATE_KEY is required" in e for e in errors)


def test_validate_keystore_source_does_not_require_plaintext_key():
    """When source='keystore', BRIDGE_PRIVATE_KEY can be empty without validation error."""
    cfg = _make_config(
        BRIDGE_PRIVATE_KEY_SOURCE="keystore",
        BRIDGE_PRIVATE_KEY="",          # empty — fine for keystore source
        BRIDGE_KEYSTORE_PATH="/path/to/ks.json",
    )
    errors = cfg.validate()
    # The "BRIDGE_PRIVATE_KEY is required" error must NOT appear
    assert not any("BRIDGE_PRIVATE_KEY is required" in e for e in errors)
