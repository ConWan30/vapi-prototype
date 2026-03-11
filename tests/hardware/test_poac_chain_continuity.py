"""
test_poac_chain_continuity.py — PoAC chain continuity across a simulated power cycle.

Gap filled:
  - Demonstrates the protocol requirement for continuity: if the device persists
    (counter, chain_head) across restarts, the next record links correctly.

Why "simulated":
  The Python laptop-side PoACEngine does not persist chain_head/counter by default.
  Real firmware claims to persist via monotonic counter storage. This test provides
  an explicit hardware-in-the-loop procedure (unplug/replug) and a concrete
  continuity proof when state is persisted.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import tempfile
import time
from pathlib import Path

import pytest


def _persist_state(db_path: str, counter: int, chain_head_hex: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS poac_state (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO poac_state(k,v) VALUES('counter', ?)", (str(counter),))
        conn.execute("INSERT OR REPLACE INTO poac_state(k,v) VALUES('chain_head', ?)", (chain_head_hex,))
        conn.commit()


def _load_state(db_path: str) -> tuple[int, bytes]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["k"]: r["v"] for r in conn.execute("SELECT k,v FROM poac_state")}
    return int(rows["counter"]), bytes.fromhex(rows["chain_head"])


@pytest.mark.hardware
def test_chain_resumes_after_disconnect(hid_device, tmp_path):
    """
    Phase A: generate 5 PoAC records, persist (counter, chain_head).
    Prompt: disconnect/reconnect controller (HITL step).
    Phase B: restore persisted state into a new PoACEngine instance and generate 2 records.
    Assert: record6.prev_poac_hash == SHA-256(record5_body[:164]) and counter is monotonic.
    """
    import sys
    ctrl_dir = str(Path(__file__).parents[2] / "controller")
    if ctrl_dir not in sys.path:
        sys.path.insert(0, ctrl_dir)
    from dualshock_emulator import PoACEngine, POAC_BODY_SIZE

    # Generate Phase A records
    eng_a = PoACEngine()
    wm_hash = b"\x11" * 32
    sensor_hash = b"\x22" * 32

    recs_a = []
    for _ in range(5):
        r = eng_a.generate(sensor_hash, wm_hash, inference=0x20, action=0x01, confidence=220, battery_pct=90)
        recs_a.append(r)
    head_a = eng_a.chain_head
    ctr_a = eng_a.counter

    # Persist state to sqlite (simulating firmware NVS)
    db_path = os.path.join(tempfile.mkdtemp(), "poac_state.sqlite")
    _persist_state(db_path, ctr_a, head_a.hex())

    # HITL action (device disconnect/reconnect)
    print("\n[POAC] ACTION: Unplug USB cable now. Wait 3 seconds. Plug back in.")
    time.sleep(3.0)
    print("[POAC] Waiting for HID to produce at least 1 report...")
    raw = hid_device.read(128, timeout_ms=4000)
    assert raw, "No HID reports after reconnect. Ensure controller is connected via USB."

    # Phase B: new engine instance (simulated reboot), restored state
    eng_b = PoACEngine()
    ctr_b, head_b = _load_state(db_path)
    eng_b.counter = ctr_b
    eng_b.chain_head = head_b

    r6 = eng_b.generate(sensor_hash, wm_hash, inference=0x20, action=0x01, confidence=220, battery_pct=90)
    r7 = eng_b.generate(sensor_hash, wm_hash, inference=0x20, action=0x01, confidence=220, battery_pct=90)

    # Validate linkage to record5 (chain head persisted)
    assert r6.prev_poac_hash == head_a, (
        "Chain did not resume: record6.prev_poac_hash != persisted chain_head."
    )
    assert r6.monotonic_ctr == ctr_a + 1
    assert r7.monotonic_ctr == ctr_a + 2
    assert eng_b.chain_head == hashlib.sha256(r7.serialize_body()).digest()

    print("[POAC] PASS: Chain continuity holds when (counter, chain_head) are persisted.")


@pytest.mark.hardware
def test_chain_counter_monotonic_across_sessions(hid_device, tmp_path):
    """Two sessions using the same persisted state must strictly increase the counter."""
    import sys
    ctrl_dir = str(Path(__file__).parents[2] / "controller")
    if ctrl_dir not in sys.path:
        sys.path.insert(0, ctrl_dir)
    from dualshock_emulator import PoACEngine

    db_path = os.path.join(tempfile.mkdtemp(), "poac_state.sqlite")

    e1 = PoACEngine()
    wm_hash = b"\x11" * 32
    sensor_hash = b"\x22" * 32
    for _ in range(3):
        e1.generate(sensor_hash, wm_hash, inference=0x20, action=0x01, confidence=220, battery_pct=90)
    _persist_state(db_path, e1.counter, e1.chain_head.hex())
    c1 = e1.counter

    # "restart"
    c_load, head_load = _load_state(db_path)
    e2 = PoACEngine()
    e2.counter = c_load
    e2.chain_head = head_load
    e2.generate(sensor_hash, wm_hash, inference=0x20, action=0x01, confidence=220, battery_pct=90)
    assert e2.counter == c1 + 1
    print(f"[POAC] PASS: counter monotonic across restart ({c1} -> {e2.counter}).")

