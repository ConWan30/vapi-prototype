"""
PITL Calibration Tool — Phase 29

Analyzes historical L4 Mahalanobis distance and humanity probability
distributions stored in the bridge SQLite database.

Outputs percentile stats and suggests CONTINUITY_THRESHOLD adjustments
based on the observed L4 distance distribution for each device.

Usage:
    python -m vapi_bridge.pitl_calibration [--db PATH] [--device-id HEX]

    --db         Path to bridge SQLite database (default: bridge.db)
    --device-id  Hex device_id to filter (default: all devices)
"""

import argparse
import statistics
import sys


def calibrate(store, device_id: str | None = None):
    """Read PITL sidecar columns and print distribution stats.

    Args:
        store:     Store instance.
        device_id: Hex device_id string to filter, or None for all devices.
    """
    params: list = []
    where = "WHERE (pitl_l4_distance IS NOT NULL OR pitl_humanity_prob IS NOT NULL)"
    if device_id:
        # device_id stored as TEXT hex in records table
        dev_hex = device_id.replace("0x", "").lower()
        if len(dev_hex) != 64 or not all(c in "0123456789abcdef" for c in dev_hex):
            print(f"Invalid device_id hex (expected 64 hex chars): {device_id}", file=sys.stderr)
            return
        where += " AND device_id=?"
        params.append(dev_hex)

    with store._conn() as conn:
        rows = conn.execute(
            f"SELECT pitl_l4_distance, pitl_humanity_prob FROM records {where}",
            params,
        ).fetchall()

    if not rows:
        print("No PITL records found.")
        return

    dists = [r[0] for r in rows if r[0] is not None]
    probs = [r[1] for r in rows if r[1] is not None]

    def _stats(vals: list, label: str) -> None:
        if not vals:
            print(f"\n{label}: no data")
            return
        s = sorted(vals)
        n = len(s)
        mean = statistics.mean(s)
        stdev = statistics.stdev(s) if n > 1 else 0.0
        p25  = s[max(0, n // 4)]
        p50  = s[max(0, n // 2)]
        p75  = s[max(0, 3 * n // 4)]
        p95  = s[max(0, int(n * 0.95))]
        print(f"\n{label}  (n={n})")
        print(f"  mean={mean:.4f}   stdev={stdev:.4f}")
        print(f"  p25={p25:.4f}  p50={p50:.4f}  p75={p75:.4f}  p95={p95:.4f}")
        print(f"  min={s[0]:.4f}   max={s[-1]:.4f}")
        return p50

    print("=" * 55)
    print("PITL Distribution Analysis")
    if device_id:
        print(f"Device: {device_id[:16]}...")
    print("=" * 55)

    p50_dist = _stats(dists, "L4 Mahalanobis Distance")
    _stats(probs, "Humanity Probability [0,1]")

    if dists:
        s = sorted(dists)
        n = len(s)
        p50_dist = s[n // 2]
        lo = p50_dist * 0.8
        hi = p50_dist * 1.2
        print(f"\nSuggested CONTINUITY_THRESHOLD range: {lo:.2f}–{hi:.2f}  (±20% of p50={p50_dist:.4f})")
        print("Set CONTINUITY_THRESHOLD env var to a value in this range.")

    if probs:
        s = sorted(probs)
        n = len(s)
        below_50 = sum(1 for v in probs if v < 0.5)
        print(f"\nHumanity prob < 0.5: {below_50}/{n} records ({100*below_50/n:.1f}%)")
        if below_50 / n > 0.2:
            print("  WARNING: >20% of records have low humanity probability.")
            print("  Consider reviewing device configuration or threshold tuning.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Analyze PITL L4/L5 distributions and suggest thresholds."
    )
    parser.add_argument(
        "--db", default="bridge.db",
        help="Path to bridge SQLite database (default: bridge.db)"
    )
    parser.add_argument(
        "--device-id", default=None, dest="device_id",
        help="Hex device_id to filter (default: all devices)"
    )
    args = parser.parse_args()

    # Lazy import to avoid loading all bridge deps for CLI usage
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from vapi_bridge.store import Store
    calibrate(Store(args.db), args.device_id)
