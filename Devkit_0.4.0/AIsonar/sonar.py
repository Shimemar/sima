"""深度マップ → 距離プロファイル → ソナー風PPI描画（すべてA65 CPU上、numpy/OpenCV）。

座標系: PPI の頂点（自艦）は画面下中央、真上がカメラ正面。画像の左列 = 左方位（鏡像にしない）。
"""
import math
import time

import numpy as np

from depth import cv2


# ---------------------------------------------------------------- 距離プロファイル (FR-03〜06)

class RangeProfile:
    """逆深度マップから方位ビンごとの最近距離を求め、時間方向に平滑化する。

    MiDaS の出力は相対逆深度 d（大きいほど近い、スケール・シフト不定）。
    距離は range = depth_scale / (d + depth_shift) で近似する（--calibrate で係数を合わせる）。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.bins = cfg.bearing_bins
        half = math.radians(cfg.camera_hfov_deg) / 2
        # ピンホールモデル: 列位置 u∈[-1,1] → 方位 atan(u·tan(HFOV/2))（線形割り当てより端が正確）
        u = (np.arange(self.bins) + 0.5) / self.bins * 2 - 1
        self.bearings = np.arctan(u * math.tan(half)).astype(np.float32)  # rad, 左が負
        self.ranges = None  # EMA 後の距離 [m]

    def update(self, inv_depth):
        h, w = inv_depth.shape
        cfg = self.cfg
        band = inv_depth[int(h * cfg.band_top):max(int(h * cfg.band_top) + 1, int(h * cfg.band_bottom))]
        # 列方向のビン化: (rows, bins, cols_per_bin) にまとめて上位パーセンタイル = 「最も近い」
        # 最大値ではなくパーセンタイルにするのは、1画素のノイズで最近距離が跳ねないため
        cols = w // self.bins * self.bins
        b = band[:, :cols].reshape(band.shape[0], self.bins, -1).transpose(1, 0, 2).reshape(self.bins, -1)
        near = np.percentile(b, cfg.nearest_percentile, axis=1).astype(np.float32)
        rng = cfg.depth_scale / np.maximum(near + cfg.depth_shift, 1e-3)
        rng = np.clip(rng, 0.0, cfg.max_range_m * 1.5)
        # FR-04: 時間方向 EMA（ちらつき抑制）
        a = cfg.ema_alpha
        self.ranges = rng if self.ranges is None else (a * rng + (1 - a) * self.ranges)
        return self.ranges


# ---------------------------------------------------------------- PPI 描画 (FR-07〜11)

GREEN = (60, 255, 90)        # BGR: 線・文字の基調色
DIM = (20, 90, 30)
GRID = (25, 110, 40)
ALERT = (40, 60, 255)


def _phosphor_lut():
    """強度(0-255) → 緑の燐光色。強い所はやや白く飛ばす。"""
    i = np.arange(256, dtype=np.float32) / 255
    g = np.clip(i * 1.15, 0, 1) * 255
    rb = np.clip(i ** 3, 0, 1) * 170
    lut = np.stack([rb * 0.6, g, rb], axis=1).astype(np.uint8)  # BGR
    return lut.reshape(256, 1, 3)


class SonarRenderer:
    def __init__(self, cfg, width, height):
        self.cfg = cfg
        self.w, self.h = width, height
        self.half = math.radians(cfg.camera_hfov_deg) / 2
        # 頂点と半径: 扇形が画面に収まる最大サイズ
        margin = 40
        r_by_h = height - margin - 70
        r_by_w = (width / 2 - margin) / math.sin(self.half)
        self.radius = int(min(r_by_h, r_by_w))
        self.apex = (width // 2, height - margin)
        self.px_per_m = self.radius / cfg.max_range_m
        self.echo = np.zeros((height, width), np.float32)   # エコー残光（ゆっくり減衰, FR-09）
        self.beam = np.zeros((height, width), np.float32)   # スイープの尾（速く減衰）
        self.lut = _phosphor_lut()
        self.sector_mask = np.zeros((height, width), np.uint8)
        self._fan(self.sector_mask, -self.half, self.half, self.radius, 255, -1)
        self.background = self._make_background()
        self.sweep_angle = -self.half
        self.sweep_dir = 1
        self.last_t = None
        self.lock = None  # (bearing, range, since)

    # 角度 θ（0 = 正面, 右が正）→ 画面座標
    def _pt(self, theta, r_px):
        return (int(round(self.apex[0] + r_px * math.sin(theta))),
                int(round(self.apex[1] - r_px * math.cos(theta))))

    def _fan(self, img, a0, a1, r, color, thickness):
        # cv2.ellipse の角度は x軸から時計回り(画面座標)。正面(上)は -90°
        # アンチエイリアスは 8bit 画像のみ対応なので、float の残光バッファには LINE_8 で描く
        lt = cv2.LINE_AA if img.dtype == np.uint8 else cv2.LINE_8
        cv2.ellipse(img, self.apex, (int(r), int(r)), 0, math.degrees(a0) - 90, math.degrees(a1) - 90,
                    color, thickness, lt)
        if thickness < 0:
            pts = np.array([self.apex, self._pt(a0, r), self._pt(a1, r)], np.int32)
            cv2.fillConvexPoly(img, pts, color, lt)

    def _make_background(self):
        cfg = self.cfg
        bg = np.zeros((self.h, self.w, 3), np.uint8)
        fill = np.zeros_like(bg)
        self._fan(fill, -self.half, self.half, self.radius, (0, 22, 6), -1)
        bg = cv2.add(bg, fill)
        # FR-10: 距離リング + ラベル
        step = cfg.ring_step_m
        n = int(cfg.max_range_m / step + 1e-6)
        for i in range(1, n + 1):
            r = i * step * self.px_per_m
            self._fan(bg, -self.half, self.half, r, GRID, 1)
            x, y = self._pt(self.half, r)
            cv2.putText(bg, f'{i * step:g}m', (x + 6, y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1, cv2.LINE_AA)
        # 方位線（15°ごと）
        deg = 0
        while deg <= math.degrees(self.half) + 1e-6:
            for s in ((1,) if deg == 0 else (1, -1)):
                th = math.radians(deg * s)
                cv2.line(bg, self.apex, self._pt(th, self.radius), GRID, 1, cv2.LINE_AA)
                x, y = self._pt(th, self.radius + 16)
                cv2.putText(bg, f'{deg * s:+d}' if deg else '000', (x - 14, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.42, GREEN, 1, cv2.LINE_AA)
            deg += 15
        # 扇の外枠
        self._fan(bg, -self.half, self.half, self.radius, GREEN, 2)
        cv2.line(bg, self.apex, self._pt(-self.half, self.radius), GREEN, 2, cv2.LINE_AA)
        cv2.line(bg, self.apex, self._pt(self.half, self.radius), GREEN, 2, cv2.LINE_AA)
        cv2.circle(bg, self.apex, 5, GREEN, -1, cv2.LINE_AA)
        return bg

    def _advance_sweep(self, dt):
        """FR-08: 扇の端から端へ往復するスイープ。前回角→今回角の区間を返す。"""
        cfg = self.cfg
        speed = 2 * self.half / max(0.1, cfg.sweep_period_s / 2)  # 片道 = 周期の半分
        a0 = self.sweep_angle
        a1 = a0 + self.sweep_dir * speed * dt
        if a1 > self.half:
            a1, self.sweep_dir = self.half, -1
        elif a1 < -self.half:
            a1, self.sweep_dir = -self.half, 1
        self.sweep_angle = a1
        return min(a0, a1), max(a0, a1)

    def render(self, ranges, bearings, now=None):
        cfg = self.cfg
        now = time.perf_counter() if now is None else now
        dt = 1 / 30 if self.last_t is None else min(0.2, now - self.last_t)
        self.last_t = now
        # 残光の減衰（半減期指定 → 1フレームの係数）
        self.echo *= 0.5 ** (dt / cfg.echo_half_life_s)
        self.beam *= 0.5 ** (dt / cfg.beam_half_life_s)

        lo, hi = self._advance_sweep(dt)
        # スイープが通過した区間を尾として塗る（速いスイープでも隙間が出ないよう扇形で塗る）
        self._fan(self.beam, lo - 0.004, hi + 0.004, self.radius, 0.55, -1)

        # スイープ通過区間のビンにエコーを打つ（ビン幅分の短い円弧）
        nearest = None
        if ranges is not None:
            bw = (bearings[-1] - bearings[0]) / max(1, len(bearings) - 1)
            hit = (bearings + bw / 2 >= lo) & (bearings - bw / 2 <= hi)
            thick = max(2, int(cfg.echo_thickness_m * self.px_per_m))
            for i in np.nonzero(hit)[0]:
                r = ranges[i]
                if r > cfg.max_range_m:
                    continue
                cv2.ellipse(self.echo, self.apex, (int(r * self.px_per_m),) * 2, 0,
                            math.degrees(bearings[i] - bw * 0.6) - 90, math.degrees(bearings[i] + bw * 0.6) - 90,
                            1.0, thick, cv2.LINE_8)
            valid = ranges <= cfg.max_range_m
            if valid.any():
                k = int(np.argmin(np.where(valid, ranges, np.inf)))
                nearest = (float(bearings[k]), float(ranges[k]))

        inten = np.clip(self.echo + self.beam * 0.35, 0, 1)
        inten8 = (inten * 255).astype(np.uint8)
        inten8 = cv2.bitwise_and(inten8, self.sector_mask)
        glow = cv2.applyColorMap(inten8, self.lut)
        frame = cv2.add(self.background, glow)
        # スイープ線本体
        cv2.line(frame, self.apex, self._pt(self.sweep_angle, self.radius), (170, 255, 190), 2, cv2.LINE_AA)
        # 現在の距離プロファイル（薄い線, 任意）
        if ranges is not None and cfg.draw_profile:
            pts = [self._pt(float(b), min(float(r), cfg.max_range_m) * self.px_per_m) for b, r in zip(bearings, ranges)]
            cv2.polylines(frame, [np.array(pts, np.int32)], False, DIM, 1, cv2.LINE_AA)
        self._draw_lock(frame, nearest, now)
        return frame, nearest

    def _draw_lock(self, frame, nearest, now):
        """Step 4 演出: 最近目標のターゲットロック枠 + 距離アラート。"""
        cfg = self.cfg
        if nearest is None or not cfg.target_lock:
            self.lock = None
            return
        b, r = nearest
        # ロック位置も平滑化してガタつきを抑える
        if self.lock is None or abs(self.lock[0] - b) > 0.25:
            self.lock = (b, r, now)
        else:
            lb, lr, since = self.lock
            self.lock = (lb * 0.7 + b * 0.3, lr * 0.7 + r * 0.3, since)
        lb, lr, since = self.lock
        alert = lr < cfg.alert_range_m
        color = ALERT if alert else GREEN
        x, y = self._pt(lb, lr * self.px_per_m)
        s = 16 if now - since > 0.6 else int(16 + 40 * (0.6 - (now - since)))  # ロック時に枠が縮む
        for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            cv2.line(frame, (x + dx * s, y + dy * s), (x + dx * (s - 7), y + dy * s), color, 2, cv2.LINE_AA)
            cv2.line(frame, (x + dx * s, y + dy * s), (x + dx * s, y + dy * (s - 7)), color, 2, cv2.LINE_AA)
        cv2.putText(frame, f'TGT {math.degrees(lb):+05.1f}deg {lr:4.2f}m', (x + s + 6, y - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        if alert and int(now * 4) % 2 == 0:
            cv2.putText(frame, 'PROXIMITY ALERT', (self.w // 2 - 130, 40), cv2.FONT_HERSHEY_DUPLEX, 0.9, ALERT, 2,
                        cv2.LINE_AA)
