#!/usr/bin/env python3
"""DevKit環境確認: Pythonパッケージ と USB(UVC)カメラのデバイス名"""
import glob, sys, importlib, os
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
print("python", sys.version.split()[0])
for m in ["numpy", "cv2", "scipy", "pyneat"]:
    try:
        mod = importlib.import_module(m)
        print(m, "OK", getattr(mod, "__version__", ""))
    except Exception as e:
        print(m, "MISSING", e)
for d in sorted(glob.glob("/sys/class/video4linux/video[0-9]*")):
    name = open(os.path.join(d, "name")).read().strip()
    if "sima" in name.lower() or "isp" in name.lower():
        continue
    idx = open(os.path.join(d, "index")).read().strip() if os.path.exists(os.path.join(d, "index")) else "?"
    print(f"/dev/{os.path.basename(d)}  name={name!r} index={idx}")
