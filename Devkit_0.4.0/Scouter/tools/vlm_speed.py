#!/usr/bin/env python3
"""VLM速度ベンチ: プロンプト版 x 画像サイズ x モデル の所要時間・トークン数・JSON成功率を測る。
YOLO26に13fpsでダミーフレームを流し続ける負荷スレッドを並走させ、実運用(MLA/CPU共有)に近づける。
   dk ./tools/vlm_speed.py [--model-dir DIR] [--variants v2:512,v3:512,v4:512,v3:384] [--no-load]
結果 -> tools/vlm_speed.jsonl (追記)"""
import argparse, glob, json, statistics, sys, threading, time
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

ap = argparse.ArgumentParser()
ap.add_argument("--model-dir", default=app.Config().vlm_model_dir)
ap.add_argument("--variants", default="v2:512,v3:512,v4:512,v3:384")
ap.add_argument("--images", default="01_f.jpg,03_m.jpg,06_f.jpeg,08_f.jpeg,10_m.jpeg,12_f.jpeg")
ap.add_argument("--no-load", action="store_true", help="YOLO負荷スレッドなし")
args = ap.parse_args()

cfg = app.Config()
W, H = 1280, 720
_g, model_run = app.build_model_run(cfg, W, H, 30)   # must exist before VLM load (vlm_ngen_demo)
stop = threading.Event()
yolo_frames = [0]

def yolo_load():
    nv12 = np.full((H * 3 // 2, W), 128, np.uint8)
    while not stop.is_set():
        if model_run.try_push([app.make_nv12_tensor(nv12, W, H)]):
            try:
                model_run.pull("detections", 2000)
                yolo_frames[0] += 1
            except Exception:
                pass
        time.sleep(1 / 13)

if not args.no_load:
    threading.Thread(target=yolo_load, daemon=True).start()

t0 = time.perf_counter()
vlm = pyneat.genai.VisionLanguageModel(args.model_dir)
model_name = Path(args.model_dir).name
print(f"VLM {model_name} loaded in {time.perf_counter() - t0:.1f}s, load={'off' if args.no_load else 'YOLO@13fps'}", flush=True)

imgs = []
for name in args.images.split(","):
    imgs.append((name, cv2.imread(f"/workspace/taste_in_clothes/{name}")))

out = open(ROOT / "tools" / "vlm_speed.jsonl", "a", encoding="utf-8")
summary = []
for variant in args.variants.split(","):
    ver, side = variant.split(":")
    ver, side = int(ver.lstrip("v")), int(side)
    recs = []
    for i, (name, bgr) in enumerate(imgs, start=1):
        s = side / max(bgr.shape[:2])
        img = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else bgr
        req = pyneat.genai.GenerationRequest()
        req.system_prompt = vw.SYSTEM_PROMPT
        req.prompt = vw.USER_PROMPTS[ver]
        req.images = [np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))]
        req.max_new_tokens = 200
        req.enable_thinking = False
        f0 = yolo_frames[0]
        t1 = time.perf_counter()
        res = vlm.run(req)
        dt = time.perf_counter() - t1
        rec = {"model": model_name, "variant": f"v{ver}:{side}", "image": name, "seconds": round(dt, 2),
               "tokens": res.metrics.generated_tokens, "tok_s": round(res.metrics.tokens_per_second, 1),
               "ttft": round(res.metrics.time_to_first_token_s, 2),
               "yolo_fps": round((yolo_frames[0] - f0) / dt, 1), "raw": res.text, "load": not args.no_load}
        try:
            r = vw.parse_vlm_json(res.text)
            rec["result"] = r
            rec["power"] = power.calc_power(r, i)
            print(f"{model_name} v{ver}:{side} {name:10s} {dt:4.1f}s {rec['tokens']:3d}tok {rec['tok_s']:4.1f}tok/s "
                  f"ttft={rec['ttft']:.2f} yolo={rec['yolo_fps']}fps {[r[k] for k in power.STAT_KEYS]} "
                  f"pose={r['pose']} 「{r['title']}」 {r['comment']}", flush=True)
        except ValueError as exc:
            rec["error"] = str(exc)
            print(f"{model_name} v{ver}:{side} {name} PARSE FAIL {exc}: {res.text[:150]!r}", flush=True)
        recs.append(rec)
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
    ok = [r for r in recs if "result" in r]
    summary.append(f"{model_name} v{ver}:{side}: ok={len(ok)}/{len(recs)} "
                   f"time={statistics.mean(r['seconds'] for r in recs):.1f}s "
                   f"tokens={statistics.mean(r['tokens'] for r in recs):.0f} "
                   f"tok/s={statistics.mean(r['tok_s'] for r in recs):.1f} "
                   f"ttft={statistics.mean(r['ttft'] for r in recs):.2f}s "
                   f"yolo={statistics.mean(r['yolo_fps'] for r in recs):.1f}fps")
print("\n=== summary ===")
for line in summary:
    print(line)
stop.set()
time.sleep(0.3)
model_run.close()
