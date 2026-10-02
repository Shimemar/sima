#!/usr/bin/env python3
"""ストール再現テスト: YOLOモデルRunの出力を8秒引き取らずに入力を押し込み続け、
push() と try_push() で Run が生き残るかを比べる(VLM生成中の詰まりの再現)。
   dk ./tools/stall_test.py push      # 旧実装: 5秒で Run 自体が止まるはず
   dk ./tools/stall_test.py try_push  # 新実装: 詰まりが解ければ回復するはず"""
import glob, sys, time
from pathlib import Path
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main as app
app.load_runtime_dependencies()
np = app.np

mode = sys.argv[1] if len(sys.argv) > 1 else "try_push"
cfg = app.Config()
W, H = 1280, 720
_g, run = app.build_model_run(cfg, W, H, 30)
nv12 = np.full((H * 3 // 2, W), 128, np.uint8)

def push_one():
    t = app.make_nv12_tensor(nv12, W, H)
    return run.push([t]) if mode == "push" else run.try_push([t])

def pull_one(timeout_ms=3000):
    try:
        return run.pull("detections", timeout_ms) is not None
    except Exception as exc:
        print(f"  pull error: {str(exc)[:160]}", flush=True)
        return False

print(f"mode={mode}")
ok = sum(1 for _ in range(20) if push_one() and pull_one())
print(f"phase1 normal: {ok}/20 frames round-tripped", flush=True)

print("phase2: pushing for 8s WITHOUT pulling (simulated downstream stall)", flush=True)
t0, accepted, rejected = time.monotonic(), 0, 0
try:
    while time.monotonic() - t0 < 8.0:
        if push_one():
            accepted += 1
        else:
            rejected += 1
        time.sleep(1 / 15)
    print(f"  accepted={accepted} rejected={rejected} (no exception)", flush=True)
except Exception as exc:
    print(f"  push raised after {time.monotonic() - t0:.1f}s: {str(exc)[:200]}", flush=True)

print("phase3: resume pulling/pushing", flush=True)
drained = 0
while pull_one(500):
    drained += 1
print(f"  drained {drained} queued outputs", flush=True)
ok = 0
for _ in range(20):
    try:
        if push_one() and pull_one():
            ok += 1
    except Exception as exc:
        print(f"  push error: {str(exc)[:160]}", flush=True)
        break
print(f"RESULT mode={mode}: after stall {ok}/20 frames round-tripped -> {'RECOVERED' if ok >= 15 else 'DEAD'}", flush=True)
run.close()
