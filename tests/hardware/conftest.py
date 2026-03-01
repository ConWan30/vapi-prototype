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

def pytest_configure(config):
    config.addinivalue_line("markers", "hardware: requires physical DualShock Edge connected via USB")
