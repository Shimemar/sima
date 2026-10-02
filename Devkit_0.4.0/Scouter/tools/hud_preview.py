#!/usr/bin/env python3
"""HUD見た目確認: 静止画に架空のトラック(解析中/完了/測定不能)を描いて保存し、描画時間も測る。
   dk ./tools/hud_preview.py [image]  -> tools/hud_preview.jpg"""
import glob, sys, time
from pathlib import Path
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
import hud, power
from vlm_worker import Analysis

class Cfg:
    hud_countup_s = 1.5
    vlm_expected_s = 7.0

class T:
    def __init__(self, tid, tlbr, score=0.9):
        self.track_id, self.tlbr, self.score, self.hits = tid, np.array(tlbr, float), score, 30

class FakeVlm:
    def __init__(self, analyses):
        self.analyses = analyses
    def get(self, tid):
        return self.analyses.get(tid)

img_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tools" / "snapshot.jpg")
base = cv2.resize(cv2.imread(img_path), (1280, 720))
now = 1000.0
def done(tid, stats, title, comment, ago):
    r = {**{k: stats for k in power.STAT_KEYS}, "pose": "腕組み", "item": "バックパック",
         "title": title, "comment": comment}
    a = Analysis(tid, state="done", done_at=now - ago, result=r)
    a.power = power.calc_power(r, tid)
    a.rank_ja, a.rank_en = power.rank_of(a.power)
    a.bonus_labels = power.bonus_of(r)[1]
    return a
analyses = {
    3: Analysis(3, state="analyzing", started_at=now - 4.2),
    7: done(7, 74, "歴戦のプロジェクトリーダー", "静かにこちらを見ている。只者ではない雰囲気。", 5.0),
    11: done(11, 97, "伝説のスーパー展示員", "スカウターが耐えられません！", 4.0),
}
tracks = [T(3, (60, 330, 220, 700)), T(7, (420, 180, 640, 700)), T(11, (900, 260, 1060, 700))]
h = hud.ScouterHud(Cfg())
h.draw(base.copy(), tracks, FakeVlm(analyses), now, "warmup")  # fill text cache
frame = base.copy()
t0 = time.perf_counter()
for _ in range(20):
    frame = base.copy()
    h.draw(frame, tracks, FakeVlm(analyses), now, "FPS 12.9  PERSONS 3  IDS 11  VLM BUSY Q1")
print(f"hud draw: {(time.perf_counter() - t0) / 20 * 1000:.1f} ms/frame (font={h.text.font_path})")
t0 = time.perf_counter()
for _ in range(20):
    nv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
    bgr = cv2.cvtColor(np.zeros((1080, 1280), np.uint8), cv2.COLOR_YUV2BGR_NV12)
print(f"color conversions: {(time.perf_counter() - t0) / 20 * 1000:.1f} ms/frame")
for k, v in analyses.items():
    print(f"#{k}: {v.state} power={v.power:,} {v.rank_ja}")
cv2.imwrite(str(ROOT / "tools" / "hud_preview.jpg"), frame)
