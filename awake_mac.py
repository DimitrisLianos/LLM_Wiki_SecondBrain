#!/usr/bin/env python3
"""prevent mac sleep during long batch ingests.
uses caffeinate (built into macos). zero external dependencies.

usage:
    python3 awake_mac.py                  # keep awake until ctrl+c.
    python3 awake_mac.py -- python3 ...   # keep awake while command runs.
"""

import subprocess
import sys

if __name__ == "__main__":
    # -d prevent display sleep, -i prevent idle sleep, -s prevent system sleep.
    cmd = ["caffeinate", "-dims"]
    if len(sys.argv) > 1 and sys.argv[1] == "--":
        cmd += sys.argv[2:]
    try:
        print("keeping mac awake (caffeinate). ctrl+c to stop.")
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nstopped.")
