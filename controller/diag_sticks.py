"""Diagnostic: monitor ds.state stick and trigger values for 15 seconds."""
import time
try:
    from pydualsense import pydualsense
except ImportError:
    print("pydualsense not installed"); exit(1)

ds = pydualsense()
ds.init()
print(f"is_edge={getattr(ds, 'is_edge', False)}  conType={getattr(ds, 'conType', '?')}")

# Check all possible stick-related attributes
print("\n=== All numeric state attributes ===")
for attr in sorted(dir(ds.state)):
    if attr.startswith('_'):
        continue
    try:
        val = getattr(ds.state, attr)
        if isinstance(val, (int, float, bool)):
            print(f"  {attr} = {val}  ({type(val).__name__})")
    except Exception:
        pass

print("\n=== MOVE STICKS AND PRESS L2/R2 for 15 seconds ===")
print("Printing whenever a value changes...\n")

prev = {}
start = time.time()
changes_seen = 0
while time.time() - start < 15:
    cur = {
        'LX': ds.state.LX, 'LY': ds.state.LY,
        'RX': ds.state.RX, 'RY': ds.state.RY,
        'L2_value': getattr(ds.state, 'L2_value', None),
        'R2_value': getattr(ds.state, 'R2_value', None),
        'L2': ds.state.L2, 'R2': ds.state.R2,
    }
    # Check for changes
    for key, val in cur.items():
        if key not in prev or prev[key] != val:
            elapsed = time.time() - start
            print(f"  [{elapsed:5.1f}s] {key}: {prev.get(key, '?')} -> {val}")
            changes_seen += 1
    prev = cur.copy()
    time.sleep(0.01)

if changes_seen <= len(prev):
    print("\n>>> WARNING: No stick/trigger changes detected!")
    print(">>> pydualsense may not update state for DualSense Edge sticks.")
    print(">>> Checking if pydualsense has an input report callback...")
    # Check for alternative input mechanisms
    for attr in ['input_report', 'report', 'hid', 'device', '_device',
                 'receive', 'read', 'readReport', 'input_report_handler']:
        val = getattr(ds, attr, 'MISSING')
        if val != 'MISSING':
            print(f"    ds.{attr} = {type(val).__name__}: {str(val)[:80]}")
else:
    print(f"\n>>> {changes_seen - len(prev)} value changes detected. Sticks working!")

# Check underlying HID device
print("\n=== pydualsense internals ===")
for attr in ['device', '_device', 'ds', '_ds', 'hid', 'hidDevice',
             'send_report', 'receive_report', 'states', 'input_report',
             'light', 'audio', 'triggerL', 'triggerR', 'conType']:
    val = getattr(ds, attr, 'MISSING')
    if val != 'MISSING':
        print(f"  ds.{attr} = {type(val).__name__}")

ds.close()
print("\nDone.")
