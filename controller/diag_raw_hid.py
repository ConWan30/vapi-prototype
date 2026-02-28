"""Diagnostic: watch raw HID bytes change in real-time as you move sticks/triggers."""
import time
try:
    from pydualsense import pydualsense
except ImportError:
    print("pydualsense not installed"); exit(1)

ds = pydualsense()
ds.init()
print(f"is_edge={getattr(ds, 'is_edge', '?')}  conType={getattr(ds, 'conType', '?')}")
print(f"Battery: Level={getattr(getattr(ds, 'battery', None), 'Level', '?')}")
print()

# Check what attributes the state object actually has for sticks/triggers
state = ds.state
print("=== State object attributes for sticks/triggers ===")
for attr in ['LX', 'LY', 'RX', 'RY', 'L2', 'R2', 'L2_value', 'R2_value',
             'L2Btn', 'R2Btn', 'lAnalog', 'rAnalog']:
    val = getattr(state, attr, 'MISSING')
    print(f"  ds.state.{attr} = {val}  (type={type(val).__name__})")

# Check accelerometer raw values
print("\n=== Accelerometer ===")
ax = ds.state.accelerometer.X
ay = ds.state.accelerometer.Y
az = ds.state.accelerometer.Z
import math
mag = math.sqrt(ax**2 + ay**2 + az**2)
print(f"  X={ax}  Y={ay}  Z={az}  mag={mag:.2f}")
print(f"  /8192 -> {ax/8192:.4f}, {ay/8192:.4f}, {az/8192:.4f}  mag={mag/8192:.4f}g")
print(f"  /1    -> {ax:.4f}, {ay:.4f}, {az:.4f}  mag={mag:.4f}")
if mag > 0:
    print(f"  /mag  -> {ax/mag:.4f}, {ay/mag:.4f}, {az/mag:.4f}  (normalized to 1g)")

print("\n=== Raw HID byte monitoring ===")
print("Move sticks and press L2/R2 triggers NOW. Watching for 10 seconds...")
print("Format: [byte1..byte10] | state.LX | state.L2 | state.L2_value")
print()

prev_bytes = None
start = time.time()
while time.time() - start < 10:
    raw = getattr(ds, 'states', None)
    if raw is None:
        print("ds.states not available!")
        break

    # Show first 10 bytes of raw report
    first10 = list(raw[:10]) if len(raw) >= 10 else list(raw)

    # Also check parsed state
    lx = ds.state.LX
    ly = ds.state.LY
    rx = ds.state.RX
    ry = ds.state.RY
    l2 = ds.state.L2
    r2 = ds.state.R2
    l2v = getattr(ds.state, 'L2_value', 'N/A')
    r2v = getattr(ds.state, 'R2_value', 'N/A')

    # Only print when something changes
    cur = (tuple(first10), lx, ly, rx, ry, l2, r2, l2v, r2v)
    if cur != prev_bytes:
        print(f"  raw[0:10]={first10}  "
              f"state: LX={lx} LY={ly} RX={rx} RY={ry}  "
              f"L2={l2}({l2v}) R2={r2}({r2v})")
        prev_bytes = cur

    time.sleep(0.02)

# Final check: try to find sticks in the full raw report
print(f"\n=== Full raw report ({len(raw)} bytes) ===")
print("Looking for bytes that change near stick positions...")
# Take a baseline
base = list(raw[:64])
print(f"  Baseline bytes[0:32]: {base[:32]}")
print(f"  Baseline bytes[32:64]: {base[32:64]}")

print("\nMove LEFT stick HARD LEFT now (3 seconds)...")
time.sleep(3)
moved = list(raw[:64])
print(f"  Moved   bytes[0:32]: {moved[:32]}")
print(f"  Moved  bytes[32:64]: {moved[32:64]}")

# Find which bytes changed
diffs = []
for i in range(min(len(base), len(moved))):
    if base[i] != moved[i]:
        diffs.append((i, base[i], moved[i]))
if diffs:
    print(f"\n  Changed bytes: {[(f'[{i}]:{b}->{m}') for i,b,m in diffs]}")
else:
    print("\n  NO BYTES CHANGED -- pydualsense may not update ds.states for Edge")

ds.close()
print("\nDone.")
