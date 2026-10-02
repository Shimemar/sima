#!/usr/bin/env python3
"""前回・前々回ブートの最後のカーネルログ(smifb/drm/pcie/メモリ関連の手掛かり)"""
import subprocess
def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
for b in ("-1", "-2"):
    print(f"== boot {b}: last 25 kernel lines")
    print(sh(f"sudo -n journalctl -b {b} -k --no-pager -o short-precise | tail -25"))
    print(f"== boot {b}: smifb/drm/pcie/aer lines")
    print(sh(f"sudo -n journalctl -b {b} -k --no-pager -o short-precise | grep -i -E 'smi|drm|pcie|aer|serror|asynchronous|mce' | tail -15"))
