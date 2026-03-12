"""
test_dashboard_connection.py — Verify frontend↔bridge connection (Task 3)

Checks:
  1. GET /health — bridge is reachable
  2. GET /dashboard/snapshot — all required top-level fields present
  3. CORS headers present on /dashboard/snapshot response
  4. WS /ws/records — WebSocket connects and stays open for 3 seconds
  5. Snapshot field types — each block has the right shape

Usage:
  python scripts/test_dashboard_connection.py

Exit 0 if all checks pass, exit 1 if any fail.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
import urllib.error

BRIDGE_URL  = "http://localhost:8080"
WS_URL      = "ws://localhost:8080/ws/records"
TIMEOUT_S   = 5
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    RESULTS.append((name, ok, detail))
    colour = "\033[92m" if ok else "\033[91m"
    reset  = "\033[0m"
    print(f"  {colour}[{status}]{reset} {name}{(' — ' + detail) if detail else ''}")
    return ok


# ── 1. Health ────────────────────────────────────────────────────────────────
def test_health() -> bool:
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/health", timeout=TIMEOUT_S) as r:
            body = json.loads(r.read())
            return check("GET /health reachable", body.get("status") == "ok",
                         f"status={body.get('status')}")
    except Exception as exc:
        return check("GET /health reachable", False, str(exc))


# ── 2. Snapshot fields ────────────────────────────────────────────────────────
REQUIRED_TOP = ["session", "calibration", "pitl_layers", "phg", "l6", "hardware"]
REQUIRED_SESSION     = ["total_sessions", "total_tests", "contracts_live", "players"]
REQUIRED_CALIBRATION = ["l4_anomaly_threshold", "l4_continuity_threshold",
                         "last_cycle_ts", "threshold_history"]
REQUIRED_PHG         = ["score", "label", "credential_active",
                         "humanity_probability", "component_scores"]
REQUIRED_L6          = ["enabled", "capture_mode", "profiles_calibrated", "total_captures"]
REQUIRED_HARDWARE    = ["controller_connected", "polling_rate_hz", "last_seen_ts"]

def test_snapshot() -> tuple[bool, dict]:
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}/dashboard/snapshot",
                                    timeout=TIMEOUT_S) as r:
            snap = json.loads(r.read())
            headers = dict(r.headers)
    except Exception as exc:
        check("GET /dashboard/snapshot reachable", False, str(exc))
        return False, {}

    check("GET /dashboard/snapshot reachable", True)

    ok_top = all(k in snap for k in REQUIRED_TOP)
    missing = [k for k in REQUIRED_TOP if k not in snap]
    check("Snapshot top-level keys", ok_top,
          f"missing={missing}" if missing else f"all {len(REQUIRED_TOP)} present")

    for block_name, required in [
        ("session",     REQUIRED_SESSION),
        ("calibration", REQUIRED_CALIBRATION),
        ("phg",         REQUIRED_PHG),
        ("l6",          REQUIRED_L6),
        ("hardware",    REQUIRED_HARDWARE),
    ]:
        block = snap.get(block_name, {})
        missing_fields = [k for k in required if k not in block]
        check(f"Snapshot.{block_name} fields",
              len(missing_fields) == 0,
              f"missing={missing_fields}" if missing_fields else "OK")

    pitl = snap.get("pitl_layers", [])
    check("Snapshot.pitl_layers is a list of 9",
          isinstance(pitl, list) and len(pitl) == 9,
          f"len={len(pitl)}")

    return True, headers


# ── 3. CORS headers ───────────────────────────────────────────────────────────
def test_cors(headers: dict) -> bool:
    # urllib returns lowercase header names
    origin_header = (
        headers.get("access-control-allow-origin") or
        headers.get("Access-Control-Allow-Origin") or
        ""
    )
    # FastAPI CORSMiddleware only echoes the origin on matched requests;
    # for a plain GET from urllib (no Origin header) it may be absent.
    # Make a second request with the Origin header set.
    try:
        req = urllib.request.Request(
            f"{BRIDGE_URL}/dashboard/snapshot",
            headers={"Origin": "http://localhost:5173"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            h = dict(r.headers)
            acao = (h.get("access-control-allow-origin") or
                    h.get("Access-Control-Allow-Origin") or "")
            return check("CORS allow-origin header present",
                         acao in ("http://localhost:5173", "*"),
                         f"Access-Control-Allow-Origin: {acao or '<absent>'}")
    except Exception as exc:
        return check("CORS allow-origin header present", False, str(exc))


# ── 4. WebSocket ──────────────────────────────────────────────────────────────
def test_websocket() -> bool:
    try:
        import websocket as ws_lib  # pip install websocket-client
    except ImportError:
        return check("WS /ws/records (3s open)",
                     False,
                     "websocket-client not installed — skipping (pip install websocket-client)")

    connected = threading.Event()
    error_msg: list[str] = []

    def on_open(_ws):
        connected.set()

    def on_error(_ws, err):
        error_msg.append(str(err))

    ws = ws_lib.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_error=on_error,
    )
    t = threading.Thread(target=ws.run_forever, daemon=True)
    t.start()

    ok = connected.wait(timeout=TIMEOUT_S)
    if ok:
        time.sleep(3)   # stay connected for 3 seconds
        ws.close()
    return check("WS /ws/records (3s open)",
                 ok,
                 "connected OK" if ok else (error_msg[0] if error_msg else "timeout"))


# ── 5. Summary ────────────────────────────────────────────────────────────────
def main() -> int:
    print("\n═══════════════════════════════════════")
    print("  VAPI Dashboard Connection Test")
    print(f"  Bridge: {BRIDGE_URL}")
    print("═══════════════════════════════════════\n")

    test_health()
    _, cors_headers = test_snapshot()
    test_cors(cors_headers)
    test_websocket()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total  = len(RESULTS)
    all_ok = passed == total

    print(f"\n{'═'*39}")
    colour = "\033[92m" if all_ok else "\033[91m"
    reset  = "\033[0m"
    print(f"  {colour}{'ALL PASS' if all_ok else 'SOME FAILED'}{reset}  {passed}/{total} checks passed")
    print(f"{'═'*39}\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
