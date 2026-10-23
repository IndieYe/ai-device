#!/usr/bin/env python3
"""Install beside runtime.py in the private operator profile, only at cutover."""

import sys
from pathlib import Path

from runtime import main

if __name__ == "__main__":
    if sys.argv[1:] != ["mcp"]:
        raise SystemExit(2)
    raise SystemExit(main(Path(__file__).resolve().parent, "mcp", []))
