#!/usr/bin/env python3
"""HUD用: PIL と日本語フォントの有無、VLMモデルディレクトリ、CMA空き"""
import glob, sys, os
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
try:
    import PIL; print("PIL", PIL.__version__)
except Exception as e:
    print("PIL MISSING", e)
fonts = [f for f in glob.glob("/usr/share/fonts/**/*", recursive=True) if f.lower().endswith((".ttf", ".otf", ".ttc"))]
print("fonts:", len(fonts))
for f in fonts:
    if any(k in f.lower() for k in ("cjk", "noto", "ipa", "takao", "vl-", "gothic", "droid", "jp")):
        print("  ", f)
print("models:", os.listdir("/media/nvme/llima/models") if os.path.isdir("/media/nvme/llima/models") else "none")
for l in open("/proc/meminfo"):
    if l.startswith(("Cma", "MemAvailable")):
        print(l.strip())
