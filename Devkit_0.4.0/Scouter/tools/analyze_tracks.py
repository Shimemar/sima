#!/usr/bin/env python3
"""track_log.jsonl の事後分析: IDごとの出現区間・途切れ・検出数とトラック数の差"""
import json, sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "track_log.jsonl"
rows = [json.loads(l) for l in open(path, encoding="utf-8")]
t0 = rows[0]["t"]
seen = defaultdict(list)
for r in rows:
    for t in r["tracks"]:
        seen[t["id"]].append((r["frame"], r["t"] - t0, t["score"], t["tlbr"]))

print(f"frames={len(rows)} duration={rows[-1]['t'] - t0:.1f}s")
hist = defaultdict(int)
for r in rows:
    hist[(r["n_dets"], len(r["tracks"]))] += 1
print("(n_dets>=low, n_tracks) -> frames:", dict(sorted(hist.items())))
for tid, obs in sorted(seen.items()):
    frames = [o[0] for o in obs]
    gaps = [(a, b) for a, b in zip(frames, frames[1:]) if b - a > 1]
    first, last = obs[0], obs[-1]
    cx = lambda o: (o[3][0] + o[3][2]) / 2
    print(f"#{tid}: t={first[1]:.1f}..{last[1]:.1f}s frames={len(obs)} "
          f"x {cx(first):.0f}->{cx(last):.0f} w={first[3][2]-first[3][0]:.0f} "
          f"mean_score={sum(o[2] for o in obs)/len(obs):.2f} "
          f"gaps={[(a, b) for a, b in gaps][:6]}")
# ID transitions: when an ID ends, does another start nearby soon after?
print("--- ID handoffs (end of one ID followed by start of another within 2s) ---")
spans = {tid: (obs[0], obs[-1]) for tid, obs in seen.items()}
for a, (_, a_end) in spans.items():
    for b, (b_start, _) in spans.items():
        if a != b and 0 <= b_start[1] - a_end[1] <= 2.0:
            ac = (a_end[3][0] + a_end[3][2]) / 2; bc = (b_start[3][0] + b_start[3][2]) / 2
            print(f"  #{a} ended {a_end[1]:.1f}s @x={ac:.0f} -> #{b} started {b_start[1]:.1f}s @x={bc:.0f}")
