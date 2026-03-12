"""
l6_capture_session.py — Pre-session operator tool for L6 human baseline capture.

Prepares a live bridge for an L6 capture session:
  1. Checks HID device reachability (DualShock Edge connected)
  2. Checks bridge health (GET /health)
  3. Enables L6 challenges + capture mode via PATCH /config
  4. Displays operator checklist
  5. Monitors live capture progress (polls bridge every 5 s)
  6. On exit (Ctrl-C or --target reached): prints session summary

USAGE
-----
  python scripts/l6_capture_session.py --player P1 --game Warzone --target 50
  python scripts/l6_capture_session.py --player P2 --game FIFA --target 10 --notes "indoor session"
  python scripts/l6_capture_session.py --help

REQUIREMENTS
------------
  Bridge running at BRIDGE_URL (default http://127.0.0.1:8765)
  L6_CAPTURE_MODE=true in bridge environment before starting bridge
  pip install requests hidapi

ENVIRONMENT
-----------
  BRIDGE_URL   — base URL of the running bridge (default http://127.0.0.1:8765)
  HW_SESSION   — hw_*.json reference for this session (default: auto from date)
"""

from __future__ import annotations

import argparse
import datetime
import signal
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: requests not installed — run: pip install requests")
    sys.exit(1)

import os

BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8765")
DS_EDGE_VID = 0x054C
DS_EDGE_PID = 0x0DF2
POLL_INTERVAL_S = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_hid() -> bool:
    """Return True if DualShock Edge is visible via HID."""
    try:
        import hid
        devices = hid.enumerate(DS_EDGE_VID, DS_EDGE_PID)
        return len(devices) > 0
    except ImportError:
        print("  WARNING: hidapi not installed — skipping HID check (pip install hidapi)")
        return True  # non-fatal
    except Exception as exc:
        print(f"  WARNING: HID enumerate failed: {exc}")
        return False


def _check_bridge_health() -> bool:
    """Return True if bridge responds to GET /health."""
    try:
        resp = requests.get(f"{BRIDGE_URL}/health", timeout=5)
        return resp.status_code == 200
    except Exception as exc:
        print(f"  Bridge not reachable at {BRIDGE_URL}: {exc}")
        return False


def _patch_config(
    player_id: str,
    game_title: str,
    hw_session_ref: str,
    notes: str,
) -> bool:
    """Enable L6 + capture mode on bridge via PATCH /config."""
    payload = {
        "l6_challenges_enabled": True,
        "l6_capture_player_id": player_id,
        "l6_capture_game_title": game_title,
        "l6_capture_hw_session_ref": hw_session_ref,
        "l6_capture_notes": notes,
    }
    try:
        resp = requests.patch(f"{BRIDGE_URL}/config", json=payload, timeout=5)
        if resp.status_code in (200, 204):
            return True
        print(f"  PATCH /config returned {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as exc:
        print(f"  PATCH /config failed: {exc}")
        return False


def _get_l6_capture_counts() -> dict[int, int] | None:
    """Query bridge for current l6_capture_sessions count by profile (GET /l6/captures)."""
    try:
        resp = requests.get(f"{BRIDGE_URL}/l6/captures/summary", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("by_profile", {})
    except Exception:
        pass
    return None


def _disable_l6(player_id: str) -> None:
    """Disable L6 challenges on bridge after session ends."""
    try:
        requests.patch(
            f"{BRIDGE_URL}/config",
            json={
                "l6_challenges_enabled": False,
                "l6_capture_player_id": "",
                "l6_capture_game_title": "",
                "l6_capture_hw_session_ref": "",
                "l6_capture_notes": "",
            },
            timeout=5,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------

def _print_checklist(player_id: str, game_title: str, target: int) -> None:
    print()
    print("=" * 60)
    print("  L6 CAPTURE SESSION CHECKLIST")
    print("=" * 60)
    print(f"  Player:        {player_id}")
    print(f"  Game:          {game_title}")
    print(f"  Target:        {target} responses per profile")
    print()
    print("  Before starting:")
    print("    [ ] Controller connected via USB (not Bluetooth)")
    print("    [ ] Bridge running with L6_CAPTURE_MODE=true env var")
    print("    [ ] Run l6_hardware_check.py and confirm all steps PASS")
    print("    [ ] Sit in normal gaming posture, controller in hand")
    print()
    print("  During session:")
    print("    [ ] Play normally — do NOT intentionally press triggers for challenges")
    print("    [ ] Avoid setting controller down (need natural grip variance)")
    print("    [ ] Keep session to 15-30 min per profile target")
    print()
    print("  Session will auto-end when --target reached or on Ctrl-C.")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# Progress monitor
# ---------------------------------------------------------------------------

def _progress_table(counts: dict[int, int], target: int) -> None:
    """Print a compact progress table for all 8 profiles."""
    profile_names = {
        0: "BASELINE_OFF",
        1: "RIGID_LIGHT",
        2: "RIGID_HEAVY",
        3: "PULSE_SLOW",
        4: "PULSE_FAST",
        5: "RIGID_ASYM",
        6: "PULSE_BUILDUP",
        7: "RIGID_MAX",
    }
    print(f"  {'ID':<4} {'Profile':<20} {'Count':>6}  {'Progress':>10}")
    print(f"  {'-'*4} {'-'*20} {'-'*6}  {'-'*10}")
    total = 0
    for pid in range(1, 8):  # skip BASELINE_OFF
        n = counts.get(pid, counts.get(str(pid), 0))
        total += n
        bar_filled = min(int(n / max(target, 1) * 20), 20)
        bar = "#" * bar_filled + "." * (20 - bar_filled)
        status = "DONE" if n >= target else f"{n}/{target}"
        print(f"  {pid:<4} {profile_names.get(pid, '?'):<20} {n:>6}  [{bar}] {status}")
    print(f"  {'':4} {'TOTAL':<20} {total:>6}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    global BRIDGE_URL
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--player", "-p", required=True,
                        help="Player identifier (e.g. P1, P2).")
    parser.add_argument("--game", "-g", required=True,
                        help="Game title for this session (e.g. Warzone).")
    parser.add_argument("--target", "-t", type=int, default=50,
                        help="Target number of responses per profile (default: 50).")
    parser.add_argument("--notes", default="",
                        help="Free-form notes stored with each captured response.")
    parser.add_argument("--hw-session", default="",
                        help="hw_*.json reference file for this session (default: auto).")
    parser.add_argument("--bridge-url", default=BRIDGE_URL,
                        help=f"Bridge base URL (default: {BRIDGE_URL}).")
    args = parser.parse_args()
    BRIDGE_URL = args.bridge_url

    hw_ref = args.hw_session or f"hw_capture_{datetime.date.today().isoformat()}.json"

    print("L6 Capture Session Operator Tool")
    print(f"Bridge: {BRIDGE_URL}")
    print()

    # --- Pre-flight checks ---
    print("Pre-flight checks:")

    print("  [1/3] Checking HID device...")
    hid_ok = _check_hid()
    if not hid_ok:
        print("  FAIL: DualShock Edge not found. Connect via USB and retry.")
        return 1
    print("  [1/3] PASS: DualShock Edge detected")

    print("  [2/3] Checking bridge health...")
    if not _check_bridge_health():
        print(f"  FAIL: Bridge not reachable at {BRIDGE_URL}. Start bridge and retry.")
        return 1
    print(f"  [2/3] PASS: Bridge healthy at {BRIDGE_URL}")

    print("  [3/3] Enabling L6 + capture mode on bridge...")
    if not _patch_config(args.player, args.game, hw_ref, args.notes):
        print("  FAIL: Could not configure bridge. Check PATCH /config endpoint.")
        return 1
    print(f"  [3/3] PASS: L6 enabled, player={args.player}, game={args.game}")

    _print_checklist(args.player, args.game, args.target)

    input("  Press ENTER to start monitoring (Ctrl-C to end session)...")
    print()

    # --- Live monitoring ---
    session_start = time.monotonic()
    done = False

    def _handle_sigint(sig, frame):
        nonlocal done
        done = True

    signal.signal(signal.SIGINT, _handle_sigint)

    print("Monitoring L6 capture progress — Ctrl-C to end session.")
    print()

    try:
        while not done:
            counts = _get_l6_capture_counts()
            elapsed = time.monotonic() - session_start
            ts = datetime.datetime.now().strftime("%H:%M:%S")

            print(f"\r[{ts}] Elapsed: {elapsed:.0f}s", end="", flush=True)

            if counts is not None:
                print()
                _progress_table(counts, args.target)

                # Check if all profiles have met target
                all_done = all(
                    counts.get(pid, counts.get(str(pid), 0)) >= args.target
                    for pid in range(1, 8)
                )
                if all_done:
                    print()
                    print(f"  TARGET REACHED: all profiles have >= {args.target} responses.")
                    done = True
                    break

            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        pass

    # --- Session end ---
    print()
    print()
    print("=" * 60)
    print("  SESSION ENDED")
    print("=" * 60)

    elapsed_total = time.monotonic() - session_start
    print(f"  Duration: {elapsed_total:.0f}s ({elapsed_total/60:.1f} min)")

    final_counts = _get_l6_capture_counts()
    if final_counts:
        print()
        print("  Final capture counts:")
        _progress_table(final_counts, args.target)

    _disable_l6(args.player)
    print()
    print("  L6 challenges disabled on bridge.")
    print()
    print("  Next steps:")
    print("    1. python scripts/l6_threshold_calibrator.py --from-db")
    print("    2. Review l6_calibration_profile.json")
    print("    3. Paste updated CHALLENGE_PROFILES into bridge/controller/l6_challenge_profiles.py")

    return 0


if __name__ == "__main__":
    sys.exit(main())
