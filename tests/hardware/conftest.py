"""
Hardware test conftest — requires physical DualShock Edge (Sony CFI-ZCP1) connected via USB.

All tests in this directory are marked @pytest.mark.hardware and skipped when no controller
is detected. Run with: pytest tests/hardware/ -v -m hardware
"""
import pytest
import time

# DualShock Edge (CFI-ZCP1) USB identifiers
# These match the Sony DualSense Edge VID/PID
DUALSHOCK_EDGE_VID = 0x054C  # Sony
DUALSHOCK_EDGE_PID = 0x0DF2  # DualSense Edge CFI-ZCP1

def find_dualshock_edge():
    """Attempt to find a connected DualShock Edge. Returns device info or None."""
    try:
        import hid
        devices = hid.enumerate(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
        if devices:
            return devices[0]
    except ImportError:
        pass
    try:
        import pydualsense
        ds = pydualsense.pydualsense()
        ds.init()
        return ds
    except Exception:
        pass
    return None

@pytest.fixture(scope="session")
def controller_device():
    """Session-scoped fixture for DualShock Edge connection. Skips if not found."""
    device = find_dualshock_edge()
    if device is None:
        pytest.skip("No DualShock Edge detected. Connect DualShock Edge CFI-ZCP1 via USB.")
    yield device
    # Cleanup
    try:
        if hasattr(device, 'close'):
            device.close()
    except Exception:
        pass

@pytest.fixture(scope="function")
def hid_device(controller_device):
    """Function-scoped raw HID device handle."""
    try:
        import hid
        h = hid.device()
        h.open(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
        h.set_nonblocking(False)
        yield h
        h.close()
    except ImportError:
        pytest.skip("hidapi not installed. Run: pip install hidapi")
    except OSError as e:
        pytest.skip(f"Cannot open HID device: {e}. Check udev rules / permissions.")

@pytest.fixture(scope="session")
def bt_device():
    """
    Session-scoped fixture for BT DualShock Edge (USB cable must be disconnected).
    Skips if no BT report (78 bytes, ID 0x31) is detected on the first read.
    """
    import sys, os
    _ctrl_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "controller")
    )
    if _ctrl_dir not in sys.path:
        sys.path.insert(0, _ctrl_dir)
    try:
        import hid
        from hid_report_parser import detect_transport, TransportType
    except ImportError:
        pytest.skip("hidapi or hid_report_parser not available")

    devices = hid.enumerate(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
    if not devices:
        pytest.skip("No DualShock Edge detected — connect via Bluetooth (USB disconnected)")

    h = hid.device()
    try:
        h.open(DUALSHOCK_EDGE_VID, DUALSHOCK_EDGE_PID)
        h.set_nonblocking(False)
    except OSError as e:
        pytest.skip(f"Cannot open HID device: {e}")

    raw = bytes(h.read(128, timeout_ms=2000) or b"")
    transport = detect_transport(raw)
    if transport != TransportType.BLUETOOTH:
        h.close()
        pytest.skip(
            f"Expected Bluetooth report (78B, ID 0x31) but got {len(raw)}B "
            f"(transport={transport.value}) — disconnect USB cable and reconnect via BT"
        )

    yield h
    try:
        h.close()
    except Exception:
        pass


def pytest_configure(config):
    config.addinivalue_line("markers", "hardware: requires physical DualShock Edge connected via USB")
    config.addinivalue_line(
        "markers",
        "bluetooth: requires physical DualShock Edge (Sony CFI-ZCP1) connected via Bluetooth "
        "(USB cable must be disconnected)",
    )
