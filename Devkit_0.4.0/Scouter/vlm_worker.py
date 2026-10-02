"""Per-track-ID VLM analysis worker (README sections 4-6, 12).

Each confirmed ByteTrack ID is analyzed AT MOST ONCE (plus one retry on a bad answer):
the detection loop calls `maybe_submit()` every frame, which enqueues a person crop only
when the track is stable and big enough, and the result is cached by track ID for the
HUD. The VLM never runs per frame.

Concurrency follows vlm_ngen_demo / demo-neat detection-vlm-assistant: one in-process
background thread owns `pyneat.genai.VisionLanguageModel`; the queue is bounded so a
crowd can't build an unbounded backlog. Unlike vlm_ngen_demo, detection is NOT paused
while the VLM runs (SCOUTER must keep tracking/HUD live) -- measuring what that costs in
fps is the point of this step.

`dry_run=True` loads nothing and fabricates a plausible answer after `dry_run_delay_s`,
so the trigger logic and HUD can be developed without the multi-minute VLM load (and
without the CMA-fragmentation reboot cycle described in vlm_ngen_demo/README.md).

API checked against core/include/genai/GenAITypes.h (GenerationRequest has prompt,
system_prompt, images, max_new_tokens) and vlm_ngen_demo/vlm_commenter.py (images are
uint8 HWC *RGB*; BGR silently degrades answers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import random
import threading
import time

import power as power_mod

SYSTEM_PROMPT = """あなたは展示会用の架空の戦闘力分析AIです。

人物画像から、服装、ポーズ、持ち物、表情など、
画像から直接確認できる特徴だけを分析してください。

実際の能力や性格を断定してはいけません。
あくまでゲーム的なジョークとして評価してください。
年齢・性別・人種・体型・障害などには一切言及しないでください。"""

# v1: README section 6 as-is. On-device it answered in round 10s and gave the same
# person identical stats every run (70/60/40/50), so power levels clustered.
USER_PROMPT_V1 = """この人物を分析し、以下のJSON形式のみ返してください。説明やコードブロックは不要です。

{
  "fashion": 0-100の整数,
  "confidence": 0-100の整数,
  "energy": 0-100の整数,
  "coolness": 0-100の整数,
  "pose": "ポーズ(10文字以内)",
  "item": "持ち物(10文字以内、無ければ「なし」)",
  "title": "面白い称号(15文字以内)",
  "comment": "30文字程度の実況"
}"""

# v2: same JSON, plus scoring guidance to spread the stats (1-point steps, wide range,
# independent axes) and a pose vocabulary that the power.POSE_BONUS keywords match.
USER_PROMPT_V2 = """この人物を分析し、以下のJSON形式のみ返してください。説明やコードブロックは不要です。

採点ルール:
- 4つのスコアは1点刻みの整数で、0〜100を広く使う(10の倍数は避ける)
- 目安: ふつう=35〜55、目立つ特徴がある=60〜80、強烈な印象=85以上
- 4つは互いに独立に評価し、同じ値を並べない
- fashion=服装の個性や色使い、confidence=姿勢や表情の堂々さ、
  energy=動きや躍動感、coolness=全体の決まり具合
- poseは見たままを書く(例: 腕組み、ピース、ガッツポーズ、バンザイ、指差し、
  ファイティングポーズ、ジャンプ、立っている、座っている)

{
  "fashion": 整数,
  "confidence": 整数,
  "energy": 整数,
  "coolness": 整数,
  "pose": "ポーズ(10文字以内)",
  "item": "持ち物(10文字以内、無ければ「なし」)",
  "title": "面白い称号(15文字以内)",
  "comment": "30文字程度の実況"
}"""

# v3: v2's scoring rules, but the answer as ONE compact line (no indentation/newlines --
# those were ~15-20 of the ~84 generated tokens on-device) and shorter title/comment.
# Generation is ~85% of VLM latency (84 tok at 10.5 tok/s), so tokens are the lever.
_RULES = """採点ルール:
- 4つのスコアは1点刻みの整数で、0〜100を広く使う(10の倍数は避ける)
- 目安: ふつう=35〜55、目立つ特徴がある=60〜80、強烈な印象=85以上
- 4つは互いに独立に評価し、同じ値を並べない
- fashion=服装の個性や色使い、confidence=姿勢や表情の堂々さ、
  energy=動きや躍動感、coolness=全体の決まり具合
- poseは見たままを書く(例: 腕組み、ピース、ガッツポーズ、バンザイ、指差し、
  ファイティングポーズ、ジャンプ、立っている、座っている)"""

USER_PROMPT_V3 = """この人物を分析し、改行や空白を入れない1行のJSONだけを返してください。

""" + _RULES + """

形式: {"fashion":整数,"confidence":整数,"energy":整数,"coolness":整数,"pose":"ポーズ(8文字以内)","item":"持ち物(8文字以内、無ければなし)","title":"面白い称号(12文字以内)","comment":"20文字以内の実況"}"""

# v4: v3 with one-letter keys (f/c/e/k/p/i/t/m), mapped back by parse_vlm_json.
USER_PROMPT_V4 = """この人物を分析し、改行や空白を入れない1行のJSONだけを返してください。

""" + _RULES.replace("fashion=", "f(fashion)=").replace("confidence=", "c(confidence)=") \
    .replace("energy=", "e(energy)=").replace("coolness=", "k(coolness)=").replace("poseは", "p(pose)は") + """

キー: f=fashion, c=confidence, e=energy, k=coolness, p=ポーズ(8文字以内), i=持ち物(8文字以内、無ければなし), t=面白い称号(12文字以内), m=20文字以内の実況
形式: {"f":整数,"c":整数,"e":整数,"k":整数,"p":"...","i":"...","t":"...","m":"..."}"""

USER_PROMPTS = {1: USER_PROMPT_V1, 2: USER_PROMPT_V2, 3: USER_PROMPT_V3, 4: USER_PROMPT_V4}
KEY_ALIASES = {"f": "fashion", "c": "confidence", "e": "energy", "k": "coolness",
               "p": "pose", "i": "item", "t": "title", "m": "comment"}

PENDING, ANALYZING, DONE, FAILED = "pending", "analyzing", "done", "failed"


@dataclass
class Analysis:
    track_id: int
    state: str = PENDING
    submitted_at: float = 0.0
    started_at: float = 0.0
    done_at: float = 0.0
    attempts: int = 0
    result: dict = field(default_factory=dict)
    power: int = 0
    rank_ja: str = ""
    rank_en: str = ""
    bonus_labels: list = field(default_factory=list)
    raw_text: str = ""
    error: str = ""


def parse_vlm_json(text: str) -> dict:
    """Pull the first {...} out of a VLM answer and normalize it. Raises ValueError."""
    cleaned = text.replace("```json", "").replace("```", "")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in answer")
    data = json.loads(cleaned[start:end + 1])
    data = {KEY_ALIASES.get(k, k): v for k, v in data.items()}  # v4 one-letter keys
    out = {}
    for key in power_mod.STAT_KEYS:
        try:
            out[key] = max(0, min(100, int(float(data.get(key, 0)))))
        except (TypeError, ValueError):
            out[key] = 0
    if not any(out[k] for k in power_mod.STAT_KEYS):
        raise ValueError("all stats missing/zero")
    for key, limit in (("pose", 16), ("item", 16), ("title", 20), ("comment", 48)):
        value = str(data.get(key, "") or "").strip()
        out[key] = value[:limit] if value else ("なし" if key == "item" else "")
    return out


def colorfulness(bgr) -> float:
    """Hasler & Suesstrunk (2003) colourfulness: ~0 greyscale, ~30 moderate, ~60+ vivid."""
    import numpy as np
    b, g, r = [c.astype(np.float32) for c in (bgr[..., 0], bgr[..., 1], bgr[..., 2])]
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))


def motion_level(track, now: float, window_s: float, skip_first_s: float) -> float | None:
    """Mean in-box frame difference over the last `window_s`, ignoring the first
    `skip_first_s` of the track (walking into frame is not "energy"). None if no samples."""
    lo = max(now - window_s, track.start_time + skip_first_s)
    vals = [v for t, v in track.motion_samples if t >= lo]
    return sum(vals) / len(vals) if vals else None


def measure_features(crop_bgr, track, frame_h: int, cfg, now: float) -> dict:
    """Image/tracking-derived 0-100 values blended into the VLM's stats (see blend_stats).
      fashion    <- colourfulness of the person crop
      confidence <- how tall the person stands in the frame (presence)
      energy     <- mean in-box frame difference (gestures, jumping) over the last
                    second or so; (level - noise floor) scaled by energy_full_scale
    The 4B VLM only answers in multiples of 5 and repeats the same sets, so these give
    continuous person-to-person variation, and make moving/approaching visibly matter."""
    x1, y1, x2, y2 = track.tlbr
    clamp = lambda v: int(round(max(0.0, min(100.0, v))))
    out = {
        "fashion": clamp(colorfulness(crop_bgr) / 80.0 * 100.0),
        "confidence": clamp((y2 - y1) / max(1, frame_h) * 110.0),
    }
    level = motion_level(track, now, cfg.energy_window_s, 0.5)
    if level is not None:
        out["energy"] = clamp((level - cfg.energy_noise_floor) / cfg.energy_full_scale * 100.0)
        out["motion_level"] = round(level, 2)   # raw value, logged for calibration
    return out


def blend_stats(result: dict, measured: dict, weight: float) -> dict:
    """stat = (1-w)*VLM + w*measured for the measured keys; originals kept for the log."""
    out = dict(result)
    out["vlm_stats"] = {k: result[k] for k in power_mod.STAT_KEYS}
    out["measured"] = dict(measured)
    if weight > 0:
        for k, v in measured.items():
            if k in power_mod.STAT_KEYS:
                out[k] = int(round((1.0 - weight) * result[k] + weight * v))
    return out


def crop_person(frame_bgr, tlbr, margin: float, max_side: int):
    import cv2
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = tlbr
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    x1, y1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    x2, y2 = min(w, int(x2 + mx)), min(h, int(y2 + my))
    if x2 - x1 < 16 or y2 - y1 < 16:
        return None
    crop = frame_bgr[y1:y2, x1:x2]
    scale = max_side / max(crop.shape[:2])
    if scale < 1.0:
        crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
                          interpolation=cv2.INTER_AREA)
    return crop.copy()


class ScouterVlm:
    def __init__(self, cfg, dry_run: bool, results_log_path: Path | None = None) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.results_log_path = results_log_path
        self.queue: "Queue[tuple[int, object, dict]]" = Queue(maxsize=cfg.vlm_max_pending)
        self.analyses: dict[int, Analysis] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.busy = threading.Event()      # set while a VLM call is in flight
        self.worker = threading.Thread(target=self._run, name="vlm-worker", daemon=True)
        self._model = None
        self.calls = 0
        self.call_seconds: list[float] = []

    # -- lifecycle --------------------------------------------------------------
    def load(self) -> None:
        if self.dry_run or self._model is not None:
            return
        import pyneat
        self._model = pyneat.genai.VisionLanguageModel(self.cfg.vlm_model_dir)
        print(f"vlm: loaded {self._model.model_id()} accepts_image={self._model.accepts_image()}",
              flush=True)

    def start(self) -> None:
        self.worker.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.worker.is_alive():
            self.worker.join(timeout=3.0)

    # -- trigger (called from the detection loop every frame; never blocks) -----
    def get(self, track_id: int) -> Analysis | None:
        with self.lock:
            return self.analyses.get(track_id)

    def maybe_submit(self, frame_bgr_fn, tracks, frame_w: int, frame_h: int) -> int:
        """Enqueue crops for tracks that are stable, large enough and not analyzed yet.
        `frame_bgr_fn` is called lazily (NV12->BGR only when something is submitted).
        Returns the number of tracks enqueued this frame."""
        frame_area = float(frame_w * frame_h)
        now = time.monotonic()
        candidates = []
        with self.lock:
            for t in tracks:
                a = self.analyses.get(t.track_id)
                if a is not None and not (a.state == FAILED and a.attempts < self.cfg.vlm_max_attempts):
                    continue
                x1, y1, x2, y2 = t.tlbr
                area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
                # wait vlm_min_track_age_s after first sight: on-device the VLM fired ~0.5s
                # in, while the person was still walking into frame and before any pose
                if (t.hits < self.cfg.vlm_min_track_hits or t.score < self.cfg.vlm_min_score
                        or now - t.start_time < self.cfg.vlm_min_track_age_s
                        or area / frame_area < self.cfg.vlm_min_area_frac):
                    continue
                candidates.append((area, t))
        if not candidates or self.queue.full():
            return 0
        frame_bgr = frame_bgr_fn()
        submitted = 0
        for _, t in sorted(candidates, key=lambda c: -c[0]):   # biggest (closest) first
            crop = crop_person(frame_bgr, t.tlbr, self.cfg.vlm_crop_margin, self.cfg.vlm_crop_max_side)
            if crop is None:
                continue
            measured = measure_features(crop, t, frame_h, self.cfg, now)
            try:
                self.queue.put_nowait((t.track_id, crop, measured))
            except Full:
                break
            with self.lock:
                a = self.analyses.setdefault(t.track_id, Analysis(t.track_id))
                a.state, a.submitted_at, a.error = PENDING, time.monotonic(), ""
            submitted += 1
        return submitted

    # -- worker -----------------------------------------------------------------
    def _run(self) -> None:
        if self.cfg.vlm_worker_nice:
            try:
                os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), self.cfg.vlm_worker_nice)
            except OSError as exc:
                print(f"vlm: setpriority failed: {exc}", flush=True)
        while not self.stop_event.is_set():
            try:
                track_id, crop, measured = self.queue.get(timeout=0.2)
            except Empty:
                continue
            with self.lock:
                a = self.analyses[track_id]
                a.state, a.started_at = ANALYZING, time.monotonic()
                a.attempts += 1
            self.busy.set()
            t0 = time.perf_counter()
            metrics, text = None, ""
            try:
                text, metrics = self._fake(track_id) if self.dry_run else self._call(crop)
                result = blend_stats(parse_vlm_json(text), measured, self.cfg.power_measure_weight)
                pw = power_mod.calc_power(result, track_id, boost=self.cfg.power_boost,
                                          bonus=self.cfg.power_bonus)
                ja, en = power_mod.rank_of(pw)
                labels = power_mod.bonus_of(result)[1] if self.cfg.power_bonus else []
                with self.lock:
                    a.result, a.power, a.rank_ja, a.rank_en = result, pw, ja, en
                    a.bonus_labels = labels
                    a.raw_text, a.state, a.done_at = text, DONE, time.monotonic()
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                with self.lock:
                    a.state, a.error, a.done_at = FAILED, str(exc), time.monotonic()
                    a.raw_text = text
            finally:
                self.busy.clear()
            dt = time.perf_counter() - t0
            self.calls += 1
            self.call_seconds.append(dt)
            self._report(a, dt, metrics)

    def _call(self, crop_bgr):
        import cv2
        import numpy as np
        import pyneat
        self.load()
        request = pyneat.genai.GenerationRequest()
        request.system_prompt = SYSTEM_PROMPT
        request.prompt = USER_PROMPTS[self.cfg.vlm_prompt_version]
        request.images = [np.ascontiguousarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))]
        request.max_new_tokens = self.cfg.vlm_max_new_tokens
        if hasattr(request, "enable_thinking"):  # present in the DevKit's pyneat 0.4.0
            request.enable_thinking = False      # no <think> preamble before the JSON
        result = self._model.run(request)
        return result.text.strip(), result.metrics

    def _fake(self, track_id: int):
        self.stop_event.wait(self.cfg.vlm_dry_run_delay_s)
        rng = random.Random(track_id)
        stats = {k: rng.randint(35, 100) for k in power_mod.STAT_KEYS}
        text = json.dumps({**stats, "pose": "直立", "item": "なし",
                           "title": f"テスト戦士{track_id}号", "comment": "dry-run: 架空の測定結果です。"},
                          ensure_ascii=False)
        return text, None

    def _report(self, a: Analysis, dt: float, metrics) -> None:
        perf = ""
        if metrics is not None:
            perf = (f" ({metrics.generated_tokens} tok, {metrics.tokens_per_second:.1f} tok/s, "
                    f"ttft={metrics.time_to_first_token_s:.2f}s)")
        if a.state == DONE:
            print(f"vlm[#{a.track_id}] {dt:.1f}s{perf} power={a.power:,} {a.rank_ja} {a.bonus_labels} "
                  f"「{a.result.get('title', '')}」 {a.result}", flush=True)
        else:
            print(f"vlm[#{a.track_id}] FAILED attempt={a.attempts} {dt:.1f}s{perf}: {a.error} "
                  f"raw={a.raw_text[:200]!r}", flush=True)
        if self.results_log_path is None:
            return
        record = {"ts": time.time(), "track_id": a.track_id, "state": a.state, "attempt": a.attempts,
                  "seconds": round(dt, 2), "dry_run": self.dry_run, "power": a.power,
                  "rank": a.rank_ja, "result": a.result, "raw": a.raw_text, "error": a.error}
        if metrics is not None:
            record.update(generated_tokens=metrics.generated_tokens,
                          tokens_per_second=metrics.tokens_per_second,
                          ttft_s=metrics.time_to_first_token_s)
        try:
            with open(self.results_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"vlm: failed to write results log: {exc}", flush=True)
