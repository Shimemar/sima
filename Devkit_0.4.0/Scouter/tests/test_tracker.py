#!/usr/bin/env python3
"""Synthetic ByteTrack checks (no pyneat/camera needed): python3 tests/test_tracker.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tracker  # noqa: E402


def box(cx, cy, w=100, h=250, s=0.9):
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, s]


def run(frames, **kw):
    tr = tracker.ByteTracker(**kw)
    out = []
    for i, dets in enumerate(frames):
        out.append(tr.update(np.array(dets).reshape(-1, 5), now=i / 30.0))
    return tr, out


def ids(tracks):
    return sorted(t.track_id for t in tracks)


def test_two_people_walking_keep_ids():
    frames = [[box(200 + 5 * i, 360), box(900 - 5 * i, 360)] for i in range(60)]
    tr, out = run(frames)
    assert ids(out[0]) == [1, 2], ids(out[0])
    assert all(ids(o) == [1, 2] for o in out), "IDs changed while walking"
    assert tr.total_ids == 2


def test_low_score_occlusion_keeps_id():
    # score dips to 0.3 (below high, above low) for 10 frames -> 2nd association keeps ID
    frames = [[box(300 + 4 * i, 360, s=0.3 if 20 <= i < 30 else 0.9)] for i in range(50)]
    tr, out = run(frames)
    assert all(ids(o) == [1] for o in out), [ids(o) for o in out]
    assert tr.total_ids == 1


def test_short_disappearance_reacquires_same_id():
    frames = [[] if 20 <= i < 35 else [box(300 + 4 * i, 360)] for i in range(50)]  # 0.5s gap
    tr, out = run(frames, lost_timeout_s=1.5)
    assert ids(out[19]) == [1] and ids(out[20]) == [] and ids(out[40]) == [1]
    assert tr.total_ids == 1


def test_long_disappearance_gets_new_id():
    frames = [[] if 20 <= i < 80 else [box(300, 360)] for i in range(100)]  # 2s gap
    tr, out = run(frames, lost_timeout_s=1.5)
    assert ids(out[95]) == [2], ids(out[95])


def test_single_frame_false_positive_consumes_no_id():
    frames = [[box(300, 360)]] + [[box(300, 360)] + ([box(1000, 300)] if i == 5 else []) for i in range(30)]
    tr, out = run(frames)
    assert tr.total_ids == 1, tr.total_ids


def test_low_score_never_starts_track():
    tr, out = run([[box(300, 360, s=0.4)] for _ in range(20)])
    assert tr.total_ids == 0


if __name__ == "__main__":
    print("assignment backend:", tracker.ASSIGNMENT_BACKEND)
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
