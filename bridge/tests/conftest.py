"""
Bridge test conftest.py

Provides a session-wide autouse fixture that ensures a fresh asyncio event loop
exists before each test. This prevents asyncio.run() (called in test_chain_v2_methods.py
and similar files) from leaving subsequent tests without a current event loop.

In Python 3.12+, asyncio.run() calls set_event_loop(None) on exit, which causes
asyncio.get_event_loop() to raise RuntimeError in the next test. The fixture
restores a new loop before each test so legacy patterns like
asyncio.get_event_loop().run_until_complete(...) continue to work.
"""

import asyncio
import pytest


@pytest.fixture(autouse=True, scope="function")
def ensure_event_loop():
    """Create a fresh event loop before each test (Python 3.12+ safety)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    # asyncio.run() may have already set the loop to None — that is fine.
    # The next invocation of this fixture will restore a fresh loop.
    try:
        if not loop.is_closed():
            loop.close()
    except Exception:
        pass
