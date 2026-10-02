#!/usr/bin/env python3
"""hud_preview と同じ描画を cProfile にかけ、重い関数を表示する"""
import cProfile, pstats, runpy, sys, io
from pathlib import Path
sys.argv = [str(Path(__file__).with_name("hud_preview.py"))]
pr = cProfile.Profile()
pr.enable()
runpy.run_path(sys.argv[0], run_name="__main__")
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(12)
print(s.getvalue()[:3500])
