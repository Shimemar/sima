"""ByteTrack (Zhang et al., ECCV 2022) person tracker -- numpy-only port.

Neat SDK has no multi-object tracker (core/include only has KLT feature-point tracking),
so this runs on the DevKit CPU in the puller stage, after YOLO box decode. Follows the
reference implementation (ifzhang/ByteTrack, yolox/tracker/byte_tracker.py) closely:

  1. predict every tracked+lost track with a constant-velocity Kalman filter (x,y,a,h)
  2. 1st association: HIGH-score detections vs. tracked+lost tracks (IoU fused with score)
  3. 2nd association: LOW-score detections vs. still-unmatched *tracked* tracks
     (the ByteTrack idea: occluded/blurred people often drop to a low score -- keep
     them attached to their track instead of losing the ID)
  4. unconfirmed (1-frame-old) tracks vs. leftover high-score detections
  5. start new tracks from leftover detections above new_track_thresh
  6. drop lost tracks after lost_timeout_s

Deviations from the reference, both deliberate for this app:
  - Track IDs are assigned on *confirmation* (2nd hit), not on first sight, so the HUD
    shows dense IDs (#1, #2, #3...) instead of gaps left by 1-frame false positives.
  - Lost-track expiry is wall-clock seconds, not a frame count: the pipeline fps here is
    not fixed (it drops while the VLM runs), so "30 frames" would mean different times.

Linear assignment uses scipy.optimize.linear_sum_assignment when available, otherwise a
greedy lowest-cost-first matcher (adequate for the handful of people in frame).
"""

from __future__ import annotations

import time
import warnings

import numpy as np

# The DevKit's system scipy (1.10) is built for numpy<1.27, but pyneat's env ships numpy
# 2.x, so the import fails there (confirmed on-device) and the greedy matcher is used.
try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from scipy.optimize import linear_sum_assignment as _lsa
except Exception:  # noqa: BLE001 - scipy is optional on the DevKit
    _lsa = None

ASSIGNMENT_BACKEND = "scipy" if _lsa is not None else "greedy"

TRACKED, LOST, REMOVED = 1, 2, 3


class KalmanFilter:
    """Constant-velocity Kalman filter over (cx, cy, aspect, h), as in ByteTrack/SORT."""

    _std_pos = 1.0 / 20
    _std_vel = 1.0 / 160

    def __init__(self) -> None:
        self._motion = np.eye(8)
        for i in range(4):
            self._motion[i, 4 + i] = 1.0
        self._update = np.eye(4, 8)

    def initiate(self, z: np.ndarray):
        mean = np.r_[z, np.zeros(4)]
        h = z[3]
        std = [2 * self._std_pos * h, 2 * self._std_pos * h, 1e-2, 2 * self._std_pos * h,
               10 * self._std_vel * h, 10 * self._std_vel * h, 1e-5, 10 * self._std_vel * h]
        return mean, np.diag(np.square(std))

    def predict(self, mean: np.ndarray, cov: np.ndarray):
        h = mean[3]
        std = [self._std_pos * h, self._std_pos * h, 1e-2, self._std_pos * h,
               self._std_vel * h, self._std_vel * h, 1e-5, self._std_vel * h]
        mean = self._motion @ mean
        cov = self._motion @ cov @ self._motion.T + np.diag(np.square(std))
        return mean, cov

    def update(self, mean: np.ndarray, cov: np.ndarray, z: np.ndarray):
        h = mean[3]
        std = [self._std_pos * h, self._std_pos * h, 1e-1, self._std_pos * h]
        proj_mean = self._update @ mean
        proj_cov = self._update @ cov @ self._update.T + np.diag(np.square(std))
        gain = np.linalg.solve(proj_cov, (cov @ self._update.T).T).T
        mean = mean + gain @ (z - proj_mean)
        cov = cov - gain @ proj_cov @ gain.T
        return mean, cov


def tlbr_to_xyah(tlbr: np.ndarray) -> np.ndarray:
    w = tlbr[2] - tlbr[0]
    h = tlbr[3] - tlbr[1]
    return np.array([tlbr[0] + w / 2, tlbr[1] + h / 2, w / max(h, 1e-6), h], dtype=np.float64)


class STrack:
    def __init__(self, tlbr, score: float) -> None:
        self._tlbr = np.asarray(tlbr, dtype=np.float64)
        self.score = float(score)
        self.mean = None
        self.cov = None
        self.track_id = 0          # 0 until confirmed (see module docstring)
        self.state = TRACKED
        self.is_activated = False
        self.hits = 0
        self.start_time = 0.0
        self.last_seen = 0.0
        # (time, in-box frame-difference) samples, filled by main.update_pixel_motion() for
        # the power level's "energy" stat. Box-centre speed was tried first and measured
        # entry/box-jitter instead of gestures on-device, so motion is measured on pixels.
        self.motion_samples: list[tuple[float, float]] = []

    @property
    def tlbr(self) -> np.ndarray:
        if self.mean is None:
            return self._tlbr.copy()
        cx, cy, a, h = self.mean[:4]
        w = a * h
        return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])

    def predict(self, kf: KalmanFilter) -> None:
        if self.state != TRACKED:
            self.mean = self.mean.copy()
            self.mean[7] = 0.0  # freeze height velocity while lost, as the reference does
        self.mean, self.cov = kf.predict(self.mean, self.cov)

    def activate(self, kf: KalmanFilter, now: float, first_frame: bool, next_id) -> None:
        self.mean, self.cov = kf.initiate(tlbr_to_xyah(self._tlbr))
        self.state = TRACKED
        self.hits = 1
        self.start_time = now
        self.last_seen = now
        self.is_activated = first_frame
        if first_frame:
            self.track_id = next_id()

    def update(self, det: "STrack", kf: KalmanFilter, now: float, next_id) -> None:
        self.mean, self.cov = kf.update(self.mean, self.cov, tlbr_to_xyah(det._tlbr))
        self.score = det.score
        self.state = TRACKED
        self.hits += 1
        self.last_seen = now
        if not self.is_activated:
            self.is_activated = True
            self.track_id = next_id()


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def iou_cost(tracks: list[STrack], dets: list[STrack], fuse_score: bool) -> np.ndarray:
    a = np.array([t.tlbr for t in tracks]).reshape(-1, 4)
    b = np.array([d.tlbr for d in dets]).reshape(-1, 4)
    iou = iou_matrix(a, b)
    if fuse_score and len(dets):
        iou = iou * np.array([d.score for d in dets])[None, :]
    return 1.0 - iou


def linear_assignment(cost: np.ndarray, thresh: float):
    """Return (matches, unmatched_rows, unmatched_cols), rejecting pairs with cost > thresh."""
    rows, cols = cost.shape
    if rows == 0 or cols == 0:
        return [], list(range(rows)), list(range(cols))
    if _lsa is not None:
        r_idx, c_idx = _lsa(cost)
        pairs = [(r, c) for r, c in zip(r_idx, c_idx) if cost[r, c] <= thresh]
    else:
        pairs = []
        used_r, used_c = set(), set()
        for flat in np.argsort(cost, axis=None):
            r, c = divmod(int(flat), cols)
            if cost[r, c] > thresh:
                break
            if r in used_r or c in used_c:
                continue
            pairs.append((r, c))
            used_r.add(r)
            used_c.add(c)
    matched_r = {r for r, _ in pairs}
    matched_c = {c for _, c in pairs}
    return (pairs,
            [r for r in range(rows) if r not in matched_r],
            [c for c in range(cols) if c not in matched_c])


class ByteTracker:
    def __init__(self, track_high_thresh: float = 0.5, track_low_thresh: float = 0.1,
                 new_track_thresh: float = 0.6, match_thresh: float = 0.8,
                 lost_timeout_s: float = 1.5) -> None:
        self.high = track_high_thresh
        self.low = track_low_thresh
        self.new_thresh = new_track_thresh
        self.match_thresh = match_thresh
        self.lost_timeout_s = lost_timeout_s
        self.kf = KalmanFilter()
        self.tracked: list[STrack] = []
        self.lost: list[STrack] = []
        self.frame_count = 0
        self._id_counter = 0

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    @property
    def total_ids(self) -> int:
        return self._id_counter

    def update(self, dets: np.ndarray, now: float | None = None) -> list[STrack]:
        """dets: (N,5) array of [x1, y1, x2, y2, score]. Returns confirmed, currently
        tracked STracks (state TRACKED and is_activated)."""
        now = time.monotonic() if now is None else now
        self.frame_count += 1
        first_frame = self.frame_count == 1
        dets = np.asarray(dets, dtype=np.float64).reshape(-1, 5)
        scores = dets[:, 4]
        high = [STrack(d[:4], d[4]) for d in dets[scores >= self.high]]
        low = [STrack(d[:4], d[4]) for d in dets[(scores >= self.low) & (scores < self.high)]]

        unconfirmed = [t for t in self.tracked if not t.is_activated]
        confirmed = [t for t in self.tracked if t.is_activated]
        pool = confirmed + self.lost
        for t in pool:
            t.predict(self.kf)

        activated: list[STrack] = []
        lost_now: list[STrack] = []
        removed: list[STrack] = []

        # 1st association: high-score dets vs. tracked + lost
        matches, u_track, u_det = linear_assignment(iou_cost(pool, high, True), self.match_thresh)
        for ti, di in matches:
            pool[ti].update(high[di], self.kf, now, self._next_id)
            activated.append(pool[ti])

        # 2nd association: low-score dets vs. remaining *tracked* (not lost) tracks
        r_tracked = [pool[i] for i in u_track if pool[i].state == TRACKED]
        matches, u_r, _ = linear_assignment(iou_cost(r_tracked, low, False), 0.5)
        for ti, di in matches:
            r_tracked[ti].update(low[di], self.kf, now, self._next_id)
            activated.append(r_tracked[ti])
        for i in u_r:
            r_tracked[i].state = LOST
            lost_now.append(r_tracked[i])

        # unconfirmed tracks vs. leftover high-score dets
        left = [high[i] for i in u_det]
        matches, u_unc, u_det = linear_assignment(iou_cost(unconfirmed, left, True), 0.7)
        for ti, di in matches:
            unconfirmed[ti].update(left[di], self.kf, now, self._next_id)
            activated.append(unconfirmed[ti])
        for i in u_unc:
            unconfirmed[i].state = REMOVED
            removed.append(unconfirmed[i])

        # new tracks
        for i in u_det:
            det = left[i]
            if det.score < self.new_thresh:
                continue
            det.activate(self.kf, now, first_frame, self._next_id)
            activated.append(det)

        # expire long-lost tracks
        for t in self.lost:
            if t.state == LOST and now - t.last_seen > self.lost_timeout_s:
                t.state = REMOVED
                removed.append(t)

        tracked = {id(t): t for t in self.tracked if t.state == TRACKED}
        for t in activated:
            tracked[id(t)] = t
        self.tracked = list(tracked.values())
        lost = {id(t): t for t in self.lost if t.state == LOST}
        for t in lost_now:
            lost[id(t)] = t
        self.lost = [t for t in lost.values() if t.state == LOST]
        self._remove_duplicates()
        return [t for t in self.tracked if t.is_activated]

    def _remove_duplicates(self) -> None:
        """Same as reference remove_duplicate_stracks: a tracked and a lost track that
        overlap almost fully are the same person -- keep the longer-lived one."""
        if not self.tracked or not self.lost:
            return
        cost = iou_cost(self.tracked, self.lost, False)
        drop_t, drop_l = set(), set()
        for p, q in zip(*np.where(cost < 0.15)):
            if self.tracked[p].last_seen - self.tracked[p].start_time > \
                    self.lost[q].last_seen - self.lost[q].start_time:
                drop_l.add(q)
            else:
                drop_t.add(p)
        self.tracked = [t for i, t in enumerate(self.tracked) if i not in drop_t]
        self.lost = [t for i, t in enumerate(self.lost) if i not in drop_l]
