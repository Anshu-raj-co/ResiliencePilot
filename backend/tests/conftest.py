import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RP_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live stack test; set RP_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
