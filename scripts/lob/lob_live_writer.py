#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin production entrypoint for the Polymarket LOB live writer."""

from __future__ import annotations

import sys
from pathlib import Path

_lob_dir = Path(__file__).resolve().parent
if str(_lob_dir) not in sys.path:
    sys.path.insert(0, str(_lob_dir))

from lob_service import main


if __name__ == "__main__":
    raise SystemExit(main())
