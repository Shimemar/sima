#!/usr/bin/env python3
"""VLMプロンプト比較ベンチ: 静止画の人物写真に v1/v2 プロンプトを当て、スコアの散らばりと
戦闘力を比較する。カメラ不要。結果 -> tools/vlm_bench.jsonl
   dk ./tools/vlm_bench.py [画像...]   (省略時 taste_in_clothes/*.jp*)"""
import glob, json, statistics, sys, time
from pathlib import Path
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main as app
app.load_runtime_dependencies()
cv2, np, pyneat = app.cv2, app.np, app.pyneat
import power
import vlm_worker as vw

images = sys.argv[1:] or sorted(glob.glob("/workspace/taste_in_clothes/*.jp*"))
cfg = app.Config()
# vlm_ngen_demo: loading the VLM before any Neat Model exists fails -> build YOLO graph first
_graph, model_run = app.build_model_run(cfg, 1280, 720, 30)
t0 = time.perf_counter()
vlm = pyneat.genai.VisionLanguageModel(cfg.vlm_model_dir)
print(f"VLM loaded in {time.perf_counter() - t0:.1f}s", flush=True)

out = open(ROOT / "tools" / "vlm_bench.jsonl", "w", encoding="utf-8")
by_version = {1: [], 2: []}
for i, path in enumerate(images, start=1):
    bgr = cv2.imread(path)
    s = cfg.vlm_crop_max_side / max(bgr.shape[:2])
    if s < 1:
        bgr = cv2.resize(bgr, (int(bgr.shape[1] * s), int(bgr.shape[0] * s)), interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    for version in (1, 2):
        req = pyneat.genai.GenerationRequest()
        req.system_prompt = vw.SYSTEM_PROMPT
        req.prompt = vw.USER_PROMPTS[version]
        req.images = [rgb]
        req.max_new_tokens = cfg.vlm_max_new_tokens
        req.enable_thinking = False
        t1 = time.perf_counter()
        res = vlm.run(req)
        dt = time.perf_counter() - t1
        rec = {"image": Path(path).name, "version": version, "seconds": round(dt, 2),
               "tokens": res.metrics.generated_tokens, "tok_s": round(res.metrics.tokens_per_second, 1),
               "raw": res.text}
        try:
            r = vw.parse_vlm_json(res.text)
            rec["result"] = r
            rec["power"] = power.calc_power(r, i)
            rec["power_nobonus"] = power.calc_power(r, i, bonus=False)
            rec["bonus"] = power.bonus_of(r)[1]
            by_version[version].append(rec)
            print(f"{Path(path).name} v{version} {dt:4.1f}s {rec['tok_s']}tok/s "
                  f"{[r[k] for k in power.STAT_KEYS]} pose={r['pose']} item={r['item']} "
                  f"power={rec['power']:,} {rec['bonus']} 「{r['title']}」", flush=True)
        except ValueError as exc:
            rec["error"] = str(exc)
            print(f"{Path(path).name} v{version} PARSE FAIL: {exc} raw={res.text[:120]!r}", flush=True)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()

print("\n=== summary ===")
for v, recs in by_version.items():
    if not recs:
        continue
    vals = [r["result"][k] for r in recs for k in power.STAT_KEYS]
    round10 = sum(1 for x in vals if x % 10 == 0) / len(vals)
    distinct_stat_sets = len({tuple(r["result"][k] for k in power.STAT_KEYS) for r in recs})
    pw = [r["power"] for r in recs]
    pw0 = [r["power_nobonus"] for r in recs]
    ranks = {}
    for p in pw:
        ranks[power.rank_of(p)[0]] = ranks.get(power.rank_of(p)[0], 0) + 1
    print(f"v{v}: ok={len(recs)}/{len(images)} stat stdev={statistics.pstdev(vals):.1f} "
          f"range={min(vals)}-{max(vals)} multiples_of_10={round10:.0%} distinct_stat_sets={distinct_stat_sets} "
          f"| power(no bonus) {min(pw0):,}-{max(pw0):,} | power {min(pw):,}-{max(pw):,} "
          f"mean_s={statistics.mean(r['seconds'] for r in recs):.1f} ranks={ranks}")
model_run.close()
