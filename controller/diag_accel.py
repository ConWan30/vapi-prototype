"""Diagnostic: track accelerometer values over time to find the correct scale."""
import time, math
try:
    from pydualsense import pydualsense
except ImportError:
    print("pydualsense not installed"); exit(1)

ds = pydualsense()
ds.init()
print(f"is_edge={getattr(ds, 'is_edge', False)}")
print("Hold controller STILL on a flat surface.\n")

print("=== Accel values over 5 seconds (sampling every 50ms) ===")
print(f"{'Time':>6s}  {'X':>8s}  {'Y':>8s}  {'Z':>8s}  {'Mag':>8s}")

prev_mag = None
mags = []
start = time.time()
while time.time() - start < 5:
    ax = ds.state.accelerometer.X
    ay = ds.state.accelerometer.Y
    az = ds.state.accelerometer.Z
    mag = math.sqrt(ax**2 + ay**2 + az**2)
    elapsed = time.time() - start

    # Print every 200ms or when magnitude changes significantly
    if prev_mag is None or abs(mag - prev_mag) > 0.5 or int(elapsed * 5) != int((elapsed - 0.05) * 5):
        print(f"{elapsed:6.2f}s  {ax:8.1f}  {ay:8.1f}  {az:8.1f}  {mag:8.2f}")
        prev_mag = mag

    if elapsed > 0.5:  # Skip first 0.5s warmup
        mags.append(mag)
    time.sleep(0.05)

if mags:
    avg = sum(mags) / len(mags)
    med = sorted(mags)[len(mags)//2]
    print(f"\nAfter warmup: avg_mag={avg:.2f}  median_mag={med:.2f}  samples={len(mags)}")
    print(f"  /8192 = {med/8192:.4f}g")
    print(f"  /9.81 = {med/9.81:.4f}g")
    print(f"  /1.0  = {med/1.0:.4f}g")
    print(f"\nRecommended divisor for 1g: {med:.1f}")

ds.close()
