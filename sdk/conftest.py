# sdk/conftest.py — isolates sdk/tests from bridge/tests namespace collision
import sys
from pathlib import Path

# Ensure sdk/ is on sys.path so vapi_sdk is importable when pytest runs from root
_sdk_dir = str(Path(__file__).resolve().parent)
if _sdk_dir not in sys.path:
    sys.path.insert(0, _sdk_dir)
