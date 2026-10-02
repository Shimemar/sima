#!/usr/bin/env python3
"""sysmonの1サンプル表示(電力レールが読めるかの確認)"""
import glob, sys, json
from pathlib import Path
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sysmon import SysMon
m = SysMon("/tmp/sysmon_probe.jsonl")
m._t0 = 0
import time; m._t0 = time.monotonic()
print(json.dumps(m.sample(), ensure_ascii=False, indent=1))
print("power_err:", getattr(m, "_power_err", None))
