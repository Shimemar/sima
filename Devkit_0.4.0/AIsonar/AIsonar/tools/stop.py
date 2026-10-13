#!/usr/bin/env python3
"""Stop a leftover AIsonar/main.py on the DevKit (run via: dk ./tools/stop.py).

dk runs without a pty, so Ctrl-C can leave the app running and holding the MLA.
SIGTERM lets main.py close its Runs cleanly; SIGKILL only if it is still alive after 5 s.
"""
import subprocess
import time

PATTERN = "AIsonar/./main.py|AIsonar/main.py"


def pids() -> list[str]:
    out = subprocess.run(["pgrep", "-f", PATTERN], capture_output=True, text=True).stdout
    return out.split()


found = pids()
if not found:
    print("main.py is not running")
    raise SystemExit(0)
print(f"SIGTERM -> {' '.join(found)}")
subprocess.run(["kill", "-TERM", *found])
for _ in range(10):
    time.sleep(0.5)
    if not pids():
        print("stopped")
        raise SystemExit(0)
print(f"still running, SIGKILL -> {' '.join(pids())}")
subprocess.run(["kill", "-KILL", *pids()])
