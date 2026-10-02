"""SCOUTER HUD renderer (README sections 9-11, 16) -- draws on a BGR frame in place.

Per track: corner-bracket reticle + ID tag, then by VLM state:
  pending/analyzing -> "SCANNING" with flickering random digits and a progress bar
  done              -> power count-up (ease-out), then a panel: ID / POWER LEVEL /
                       CLASS / VLM title / VLM comment
  done >= 100,000   -> count-up stalls at 99,999 -> ERROR flash -> 測 定 不 能 / OVERLOAD
Plus a TEAM POWER list (top-left) and 本日の最高戦闘力 (top-right).

Japanese text uses PIL + Noto Sans CJK (present on the DevKit image); every distinct
(text, size) string is rasterized once into an alpha mask and cached, so steady-state
cost is numpy alpha-blends only. OpenCV's putText can't draw Japanese.
"""

from __future__ import annotations

from collections import OrderedDict
import random

import cv2
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # noqa: BLE001
    Image = None

import power as power_mod

FONT_PATHS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]

GREEN = (90, 255, 90)
DIM_GREEN = (40, 150, 40)
AMBER = (0, 200, 255)
RED = (40, 40, 255)
WHITE = (235, 255, 235)


class TextCache:
    def __init__(self, limit: int = 512) -> None:
        self._fonts: dict[int, object] = {}
        self._cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._limit = limit
        self.font_path = None
        if Image is not None:
            for p in FONT_PATHS:
                try:
                    ImageFont.truetype(p, 12)
                    self.font_path = p
                    break
                except OSError:
                    continue

    def _font(self, size: int):
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(self.font_path, size)
        return self._fonts[size]

    def mask(self, text: str, size: int) -> np.ndarray:
        key = (text, size)
        m = self._cache.get(key)
        if m is not None:
            self._cache.move_to_end(key)
            return m
        if self.font_path is None:  # ASCII-only fallback
            (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, size / 30, 2)
            img = np.zeros((h + base + 4, w + 4), np.uint8)
            cv2.putText(img, text, (2, h + 2), cv2.FONT_HERSHEY_SIMPLEX, size / 30, 255, 2, cv2.LINE_AA)
        else:
            font = self._font(size)
            l, t, r, b = font.getbbox(text or " ")
            img_p = Image.new("L", (max(1, r - l + 4), max(1, b - t + 4)), 0)
            ImageDraw.Draw(img_p).text((2 - l, 2 - t), text, font=font, fill=255)
            img = np.asarray(img_p)
        m = img.astype(np.float32) / 255.0
        self._cache[key] = m
        if len(self._cache) > self._limit:
            self._cache.popitem(last=False)
        return m


def blend_mask(frame, mask, x: int, y: int, color) -> tuple[int, int]:
    """Alpha-blend `color` through `mask` (float32 HxW, cached) at (x, y) top-left.
    cv2.blendLinear does the per-pixel blend in C++ (numpy float math here cost ~10ms
    per HUD frame on the DevKit). Returns mask (w, h)."""
    mh, mw = mask.shape
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(fw, x + mw), min(fh, y + mh)
    if x1 > x0 and y1 > y0:
        m = mask[y0 - y:y1 - y, x0 - x:x1 - x]
        roi = frame[y0:y1, x0:x1]
        patch = np.empty_like(roi)
        patch[:] = color
        frame[y0:y1, x0:x1] = cv2.blendLinear(patch, roi, m, 1.0 - m)
    return mw, mh


def darken(frame, x0: int, y0: int, x1: int, y1: int) -> None:
    """Halve brightness in place (integer shift: ~20x cheaper than float blending)."""
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(fw, x1), min(fh, y1)
    if x1 > x0 and y1 > y0:
        roi = frame[y0:y1, x0:x1]
        np.right_shift(roi, 1, out=roi)


def wrap(text: str, width: int) -> list[str]:
    lines = [text[i:i + width] for i in range(0, len(text), width)] or [""]
    if len(lines) > 1 and len(lines[-1]) <= 2:   # don't leave a lone 「。」」 on its own line
        tail = lines.pop()
        lines[-1] += tail
    return lines


def ease_out(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1.0 - (1.0 - p) ** 3


class ScouterHud:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.text = TextCache()
        self.best_power = 0
        self.best_title = ""
        self._placed: list[tuple[int, int, int, int]] = []

    def put(self, frame, text: str, x: int, y: int, size: int, color) -> tuple[int, int]:
        return blend_mask(frame, self.text.mask(text, size), x, y, color)

    # -- elements ---------------------------------------------------------------
    def reticle(self, frame, tlbr, color, lock_p: float = 1.0) -> None:
        x1, y1, x2, y2 = [int(v) for v in tlbr]
        # TARGET LOCK: brackets start 35% wider and snap in
        grow = (1.0 - ease_out(lock_p)) * 0.35
        dx, dy = int((x2 - x1) * grow / 2), int((y2 - y1) * grow / 2)
        x1, y1, x2, y2 = x1 - dx, y1 - dy, x2 + dx, y2 + dy
        L = max(12, min(x2 - x1, y2 - y1) // 5)
        for (cx, cy, sx, sy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(frame, (cx, cy), (cx + sx * L, cy), color, 3, cv2.LINE_AA)
            cv2.line(frame, (cx, cy), (cx, cy + sy * L), color, 3, cv2.LINE_AA)

    def panel_origin(self, frame, tlbr, pw: int, ph: int) -> tuple[int, int]:
        """Right of the box, else left of it, else over it -- skipping spots that would
        overlap a panel already placed this frame (self._placed); falls back to the
        first candidate if every spot collides."""
        fh, fw = frame.shape[:2]
        x1, y1, x2, _ = [int(v) for v in tlbr]
        py = max(0, min(fh - ph, y1))
        xs = [x2 + 12, x1 - 12 - pw, x1]
        # vertical options: level with the box top, or just below any placed panel
        ys = [py] + sorted(qy + qh + 6 for _, qy, _, qh in self._placed if qy + qh + 6 + ph <= fh)
        cands = [(px, y) for px in xs if 0 <= px and px + pw <= fw for y in ys]
        if not cands:
            cands = [(max(0, min(fw - pw, x1)), py)]
        for px, y in cands:
            if not any(px < qx + qw and qx < px + pw and y < qy + qh and qy < y + ph
                       for qx, qy, qw, qh in self._placed):
                break
        else:
            px, y = cands[0]
        self._placed.append((px, y, pw, ph))
        return px, y

    def draw_analysis(self, frame, t, a, now: float) -> None:
        tid = t.track_id
        if a is None or a.state in ("pending", "analyzing"):
            self.reticle(frame, t.tlbr, AMBER)
            px, py = self.panel_origin(frame, t.tlbr, 230, 96)
            darken(frame, px, py, px + 230, py + 96)
            self.put(frame, f"TARGET #{tid:03d}", px + 8, py + 4, 20, AMBER)
            label = "SCANNING..." if a is None or a.state == "pending" else "ANALYZING"
            self.put(frame, label, px + 8, py + 30, 18, AMBER)
            digits = f"{random.Random(int(now * 8) * 31 + tid).randint(0, 99999):>6,}"
            cv2.putText(frame, digits, (px + 140, py + 48), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        DIM_GREEN, 1, cv2.LINE_AA)
            if a is not None and a.state == "analyzing":
                p = min(0.99, (now - a.started_at) / max(1.0, self.cfg.vlm_expected_s))
            elif a is None:  # lock-on countdown until the VLM trigger (vlm_min_track_age_s)
                label = "LOCKING ON..."
                p = min(1.0, (now - t.start_time) / max(0.1, self.cfg.vlm_min_track_age_s))
            else:
                p = 0.0
            cv2.rectangle(frame, (px + 8, py + 64), (px + 222, py + 82), DIM_GREEN, 1)
            cv2.rectangle(frame, (px + 10, py + 66), (px + 10 + int(210 * p), py + 80), AMBER, -1)
            return
        if a.state == "failed":
            self.reticle(frame, t.tlbr, RED)
            px, py = self.panel_origin(frame, t.tlbr, 230, 40)
            darken(frame, px, py, px + 230, py + 40)
            self.put(frame, f"#{tid:03d} ANALYSIS ERROR", px + 8, py + 8, 18, RED)
            return

        # done
        since = now - a.done_at
        lock_p = since / 0.4
        countup_s = self.cfg.hud_countup_s
        overload = a.power >= power_mod.UNMEASURABLE
        # `animated` values change every frame, so they're drawn with cv2.putText (ASCII)
        # rather than rasterized+cached CJK text; the settled value uses the CJK font.
        animated = True
        if overload:
            # count to 99,999, then stall + ERROR flashes, then 測定不能
            if since < countup_s:
                shown, color = f"{int(99_999 * ease_out(since / countup_s)):,}", GREEN
            elif since < countup_s + 1.2:
                shown, color = ("ERROR" if int(since * 6) % 2 == 0 else "99,999"), RED
            else:
                shown, color, animated = "測 定 不 能", RED, False
        elif since < countup_s:
            shown, color = f"{int(a.power * ease_out(since / countup_s)):,}", GREEN
        else:
            shown, color, animated = f"{a.power:,}", GREEN, False
        self.reticle(frame, t.tlbr, RED if overload and since >= countup_s else GREEN, lock_p)

        title = a.result.get("title", "")
        comment_lines = wrap(f"「{a.result.get('comment', '')}」", 15)[:3]
        bonus = " + ".join(getattr(a, "bonus_labels", []) or [])
        pw = 300
        ph = 178 + 26 * len(comment_lines) + (24 if bonus else 0)
        px, py = self.panel_origin(frame, t.tlbr, pw, ph)
        darken(frame, px, py, px + pw, py + ph)
        cv2.rectangle(frame, (px, py), (px + pw, py + ph), DIM_GREEN, 1)
        self.put(frame, f"TARGET ID : #{tid:03d}", px + 10, py + 6, 20, GREEN)
        self.put(frame, "POWER LEVEL", px + 10, py + 34, 18, DIM_GREEN)
        if animated:
            cv2.putText(frame, shown, (px + 20, py + 96), cv2.FONT_HERSHEY_DUPLEX, 1.4, color, 2,
                        cv2.LINE_AA)
        else:
            self.put(frame, shown, px + 20, py + 56, 40, color)
        if since >= countup_s:
            cls = f"CLASS : {a.rank_en}"
            self.put(frame, cls, px + 10, py + 108, 20, RED if overload else GREEN)
            self.put(frame, title, px + 10, py + 136, 22, WHITE)
            for i, line in enumerate(comment_lines):
                self.put(frame, line, px + 10, py + 170 + 26 * i, 19, WHITE)
            if bonus:
                self.put(frame, f"BONUS {bonus}", px + 10, py + 172 + 26 * len(comment_lines), 14, AMBER)
            if overload and int(since * 3) % 2 == 0:
                self.put(frame, "WARNING  SCOUTER OVERLOAD", px + 10, py + ph + 4, 18, RED)

    def draw(self, frame, tracks, vlm, now: float, status: str) -> None:
        fh, fw = frame.shape[:2]
        self._placed = [(8, 8, 292, 200), (fw - 360, 8, 352, 60)]  # team list / best-power corners
        team = []
        for t in tracks:
            a = vlm.get(t.track_id) if vlm is not None else None
            if vlm is None:
                self.reticle(frame, t.tlbr, GREEN)
                x1, y1 = int(t.tlbr[0]), int(t.tlbr[1])
                self.put(frame, f"#{t.track_id:03d} {t.score:.2f}", x1, max(0, y1 - 28), 20, GREEN)
                continue
            self.draw_analysis(frame, t, a, now)
            if a is not None and a.state == "done":
                team.append((t.track_id, a.power))
                if a.power > self.best_power:
                    self.best_power, self.best_title = a.power, a.result.get("title", "")

        if team:
            h = 34 + 26 * len(team) + 34
            darken(frame, 8, 8, 300, 8 + h)
            self.put(frame, "TEAM SCAN", 16, 12, 18, DIM_GREEN)
            for i, (tid, pw) in enumerate(sorted(team)):
                self.put(frame, f"TARGET #{tid:03d}", 16, 38 + 26 * i, 19, GREEN)
                self.put(frame, f"{pw:>9,}", 170, 38 + 26 * i, 19, GREEN)
            y = 38 + 26 * len(team)
            cv2.line(frame, (16, y + 4), (290, y + 4), DIM_GREEN, 1)
            self.put(frame, "TEAM POWER", 16, y + 8, 19, AMBER)
            self.put(frame, f"{sum(p for _, p in team):>9,}", 170, y + 8, 19, AMBER)
        if self.best_power:
            label = f"本日の最高戦闘力 {self.best_power:,}"
            w, _ = self.text.mask(label, 20).shape[1], 0
            darken(frame, fw - w - 24, 8, fw - 8, 64)
            self.put(frame, label, fw - w - 16, 12, 20, AMBER)
            self.put(frame, self.best_title, fw - w - 16, 38, 18, WHITE)
        darken(frame, 0, fh - 30, fw, fh)
        self.put(frame, status, 10, fh - 28, 18, DIM_GREEN)
