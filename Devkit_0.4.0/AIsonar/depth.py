"""MiDaS v2.1 small の MLA 推論ラッパ（pyneat）。

MPK は tools/mla_surgery.py で入力正規化を外した ONNX から作っているので、
入力は ImageNet 正規化済みの float32 HWC RGB（256x256, 前処理なしの Tensor）。
出力は相対逆深度（大きいほど近い）、形状 (256, 256)。
"""
import glob
import sys

try:
    import cv2
except ImportError:
    for p in glob.glob('/usr/lib/python3*/dist-packages'):
        sys.path.append(p)
    import cv2
import numpy as np

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
# cv2 のスカラー演算用 (チャンネルごと, 4要素)。numpy のブロードキャストより DevKit で約2.5 ms 速い
_SUB = tuple(float(v) for v in MEAN * 255) + (0.0,)
_MUL = tuple(float(v) for v in 1 / (255 * STD)) + (0.0,)


def preprocess(image_bgr, size=256):
    """BGR画像 → 正規化済み float32 HWC RGB（アスペクト比は保たず引き伸ばす: キャリブレーションと同じ）。"""
    img = cv2.resize(image_bgr, (size, size), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)
    return cv2.multiply(cv2.subtract(rgb, _SUB), _MUL)


class MidasMla:
    def __init__(self, mpk_path, size=256, timeout_ms=10000):
        import pyneat
        self._pyneat = pyneat
        self.size = size
        self.timeout_ms = timeout_ms
        opt = pyneat.ModelOptions()
        opt.preprocess.kind = pyneat.InputKind.Tensor
        opt.preprocess.enable = pyneat.AutoFlag.Off
        self.model = pyneat.Model(mpk_path, opt)
        self.runner = self.model.build([self._tensor(np.zeros((size, size, 3), np.float32))])

    def _tensor(self, x):
        return self._pyneat.Tensor.from_numpy(np.ascontiguousarray(x, dtype=np.float32), copy=True)

    def infer(self, x):
        """正規化済み HWC 入力 → 逆深度マップ (size, size) float32。"""
        out = self.runner.run([self._tensor(x)], timeout_ms=self.timeout_ms)
        out = out[0] if isinstance(out, list) else out
        return np.asarray(out.to_numpy(copy=True), dtype=np.float32).reshape(self.size, self.size)

    def __call__(self, image_bgr):
        return self.infer(preprocess(image_bgr, self.size))
