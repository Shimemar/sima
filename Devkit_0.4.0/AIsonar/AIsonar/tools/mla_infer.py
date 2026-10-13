#!/usr/bin/env python3
"""MLA版 MiDaS (MPK) を pyneat で実行し、CPU(ORT)版と精度・速度を比較する（DevKitで実行）。

    dk ./tools/mla_infer.py                       # calib/ の数枚
    dk ./tools/mla_infer.py --mpk assets/xxx_mpk.tar.gz --runs 50
CPU比較には ../AvatarCreator/AnimeGAN/vendor の onnxruntime を借用する（pyneat venv に ORT が無いため）。
output/depth_compare.png に 入力 | CPU | MLA を保存する。
"""
import argparse
import glob
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from depth import MidasMla, preprocess, cv2, np  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--mpk', default=str(ROOT / 'assets/midas_v21_small_256_mla_mpk.tar.gz'))
ap.add_argument('--onnx', default=str(ROOT / 'assets/midas_v21_small_256_mla.onnx'))
ap.add_argument('--images', default=str(ROOT / 'calib/calib_0[0-4]0.jpg'))
ap.add_argument('--runs', type=int, default=30)
args = ap.parse_args()

sys.path.append(str(ROOT.parent / 'AvatarCreator/AnimeGAN/vendor'))
import onnxruntime as ort  # noqa: E402

t0 = time.perf_counter()
mla = MidasMla(args.mpk)
print(f'MLA load+build {time.perf_counter() - t0:.2f}s')
sess = ort.InferenceSession(args.onnx, providers=['CPUExecutionProvider'])

rows = []
for path in sorted(glob.glob(args.images)):
    img = cv2.imread(path)
    x = preprocess(img, mla.size)  # HWC float32 normalized
    ref = sess.run(None, {'input': np.ascontiguousarray(x.transpose(2, 0, 1)[None])})[0].reshape(mla.size, mla.size)
    out = mla.infer(x)
    corr = np.corrcoef(ref.ravel(), out.ravel())[0, 1]
    rel = np.abs(out - ref).mean() / (ref.max() - ref.min())
    print(f'{Path(path).name}: corr={corr:.4f} mean|diff|/range={rel:.4f}  cpu[{ref.min():.0f},{ref.max():.0f}] mla[{out.min():.0f},{out.max():.0f}]')
    def vis(d):
        d = (d - d.min()) / max(1e-6, d.max() - d.min())
        return cv2.applyColorMap((d * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    rows.append(cv2.hconcat([cv2.resize(img, (mla.size, mla.size)), vis(ref), vis(out)]))

x = preprocess(cv2.imread(sorted(glob.glob(args.images))[0]), mla.size)
times = []
for _ in range(args.runs):
    t = time.perf_counter(); mla.infer(x); times.append((time.perf_counter() - t) * 1000)
tc = []
for _ in range(5):
    t = time.perf_counter(); sess.run(None, {'input': np.ascontiguousarray(x.transpose(2, 0, 1)[None])}); tc.append((time.perf_counter() - t) * 1000)
print(f'MLA median {statistics.median(times):.1f} ms (n={len(times)})  CPU(ORT) median {statistics.median(tc):.1f} ms')
(ROOT / 'output').mkdir(exist_ok=True)
cv2.imwrite(str(ROOT / 'output/depth_compare.png'), cv2.vconcat(rows))
print('wrote output/depth_compare.png (入力 | CPU | MLA)')
