#!/usr/bin/env python3
"""Stub for PI_BIN in tests. Prints the JSON in PI_STUB_JSON and ignores argv.

Set PI_STUB_JSON to a string, or PI_STUB_FILE to a file containing it.
"""

import os
import sys

payload = os.environ.get("PI_STUB_JSON", "")
path = os.environ.get("PI_STUB_FILE", "")
if not payload and path:
    with open(path, encoding="utf-8") as fh:
        payload = fh.read()
if not payload:
    payload = "[]"
sys.stdout.write(payload)
sys.stdout.write("\n")
