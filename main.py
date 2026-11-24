#!/usr/bin/env python3
"""KalciCam entry point: `python main.py` (or the built KalciCam binary)."""

import sys

from kalcicam.app import main

if __name__ == "__main__":
    sys.exit(main())
