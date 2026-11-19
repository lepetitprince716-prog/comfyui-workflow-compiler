"""Access to the bundled data files (node definitions, presets, palettes, tables).

The files live in the ``cwc.data`` package, so a source checkout and an installed
package read them the same way. Every reader goes through :func:`read_bytes`.
"""

from __future__ import annotations

import importlib.resources
import json
from functools import lru_cache
from typing import Any


def read_bytes(name: str) -> bytes:
    """Return the raw bytes of a bundled data file."""
    resource = importlib.resources.files("cwc.data").joinpath(name)
    if not resource.is_file():
        raise FileNotFoundError(f"bundled data file missing: {name}")
    return resource.read_bytes()


@lru_cache(maxsize=None)
def read_json(name: str) -> Any:
    """Parse a bundled JSON file. Cached, treat as read-only."""
    return json.loads(read_bytes(name).decode("utf-8"))
