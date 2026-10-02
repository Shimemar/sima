# Step 1: YOLO26 + ByteTrack experiment

This experiment covers sections 2–4 of the [README](README.md) (no VLM yet). It checks two things:

- whether person tracking holds up in fps
- whether track IDs stay stable enough to use as keys for a VLM result cache

## Files

| File | Contents |
|---|---|
| `main.py` | 4-stage threaded pipeline: capture → push → pull (decode + ByteTrack + draw) → UDP send. Based on `vlm_ngen_demo/main.py` with the VLM removed |
| `tracker.py` | ByteTrack ported to numpy only (Kalman filter + two-stage association). Uses scipy if available, otherwise a greedy matcher |
| `config/default.conf` | Detection and tracking thresholds (each knob is explained in the comments) |
| `tests/test_tracker.py` | Synthetic-data tests (keep IDs while walking, low-score occlusion, re-acquire after a short disappearance, new ID after a long one, etc.) |
| `tools/probe_env.py` | Check the DevKit's Python packages and camera device names |
| `tools/snapshot.py` | Take a single frame from the camera (to check the field of view) |

## How to run

```bash
cd /workspace/Scouter
dk ./main.py --frames 300                       # bounded smoke test
dk ./main.py                                     # continuous run (stop with Ctrl-C/SIGTERM)
dk ./main.py --model ./assets/yolo26n-det-bf16-mla_tess-b1.tar.gz   # lightweight model for comparison
dk ./main.py --draw-raw                          # also draw raw detections (including low-score) in grey
```

Viewer on the host PC:

```bash
gst-launch-1.0 -v udpsrc port=9000 caps="application/x-rtp,media=video,encoding-name=H264,payload=96" ! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! autovideosink sync=false
```

Each frame shows the box colour, `#ID score`, and a bottom banner with `PERSONS / IDS (total IDs issued) / FPS / TRACK (tracker processing time)`. On exit, a summary prints each ID's frame span. `track_log.jsonl` is the per-frame log for analysing ID switches after the run.

## Design notes

- YOLO's `score_threshold` is low (0.10) because ByteTrack's second association stage also uses low-score detections. Which people get displayed is decided by the `track_*` thresholds instead.
- An ID is assigned only after a track is seen in 2 consecutive frames, so one-frame false positives don't use up IDs (a difference from the original ByteTrack).
- Lost tracks are kept for `lost_timeout_s` **seconds**. fps will drop when the VLM is added, so a frame-count limit would mean a different amount of time.

## Measured results (DevKit, 2026-10-01)

| Item | Result |
|---|---|
| Camera | `/dev/video0` (Anker PowerConf C200; it was `/dev/video16` in vlm_ngen_demo) |
| yolo26m + ByteTrack, 1280x720 | **12.9 fps**, dropped=0 (300 frames, no people in view) |
| Tracker processing time | mean 0.25 ms / max 0.94 ms (greedy) |
| scipy | DevKit numpy 2.4.6 doesn't match scipy 1.10, so it can't be imported → greedy fallback |
| yolo26n + ByteTrack, 1280x720 | **12.9 fps** (same as yolo26m) → the bottleneck is camera input (camera fps or jpegdec/videoconvert), not the model |

Note: `dk` drops empty-string arguments, so `--track-log ""` cannot disable the log (set `track_log=` in the config instead).

## Real tracking run (people in view, 1500 frames / 116 s, yolo26m)

Analysis: `python3 tools/analyze_tracks.py` (track_log.jsonl)

- 5 appearances → IDs #1–#5. **Zero switches** to another ID while a person was in view.
- #1 lost track twice, briefly (~0.5 s and ~0.8 s), and **came back as the same ID** (`lost_timeout_s=1.5` works as intended).
- The gaps between appearances (3–30 s) all exceed 1.5 s, so each got a new ID (by design).
  → For SCOUTER, someone who leaves the frame and comes back is analysed by the VLM again.
  Keeping the same ID in that case needs appearance-feature re-identification (ReID) or a longer timeout.
- Not tested yet: two people tracked at once, or two people passing each other.

## Camera fps investigation (Anker C200)

`tools/camera_fps.py` measures the camera alone, without YOLO or GStreamer (grab only, no decode).

| Format | Measured fps (nominal) |
|---|---|
| MJPG 1920x1080 / 1280x720 / 640x480 | 13.0 / 13.0 / 12.9 (30) |
| YUYV 640x480 / 640x360 | 12.9 / 15.1 (30) |
| H264 1280x720 | 15.4 (60) |

- The camera itself delivers only 13–15 fps → **the app's 12.9 fps is this camera's limit**
  (not jpegdec, the model, or the tracker).
- The fps barely changes with resolution or format, so USB bandwidth is not the cause either. Most likely the
  camera firmware is stretching exposure because the room is dim.
- Setting `auto_exposure=1` (manual) + `exposure_time_absolute` (1–1000) **changes neither brightness nor fps**
  (the firmware ignores it).
  - Side effect: after this test `auto_exposure` reads back as 1 (Manual) and cannot be set back to 0 with `v4l2-ctl`.
    It goes back after a USB replug or reboot (it has no practical effect, since the setting is ignored anyway).
- Next things to check: whether fps rises under brighter lighting (exhibition-hall level), and swapping in another
  UVC camera that guarantees 30 fps or an RTSP camera.

---

# Step 2: VLM integration + power level + HUD

| File | Contents |
|---|---|
| `vlm_worker.py` | Analyses each track ID once with Qwen3-VL-4B (background thread, bounded queue, retry on broken JSON) |
| `power.py` | README §7 formula + rank table (§8). The README formula maxes out at ~18,400 and never reaches BOSS/測定不能, so `power_boost` stretches it non-linearly when the average stat is above 70 |
| `hud.py` | SCOUTER HUD (SCANNING → count-up → panel, 測定不能 effect, TEAM POWER, 本日の最高戦闘力). Japanese text via PIL + Noto Sans CJK |
| `tools/hud_preview.py` | Draws the HUD on a still image with fake data → `tools/hud_preview.jpg` (for checking the look) |
| `tests/test_power_vlm.py` | Tests for power calculation and VLM JSON parsing |

```bash
dk ./main.py --vlm-dry-run --frames 600   # no VLM load; fake results after 6 s (HUD / trigger check)
dk ./main.py --frames 1500                 # real VLM (startup takes a few minutes to load Qwen3-VL)
dk ./main.py --no-vlm                      # Step 1 equivalent
```

When to send a person to the VLM: stable for 8+ ByteTrack frames, score 0.6+, box ≥3% of the frame area, not analysed yet.
The summary shows **fps with the VLM idle and fps with it busy** separately (the point of this step).

Measured (DevKit): HUD draw 11.6 ms/frame with 3 people (30.7 ms before optimisation), 4.8 ms with nobody in view.
Dry-run with nobody in view: 12.9 fps, dropped=0.

## Real VLM run results (DevKit, 2026-10-01, Qwen3-VL-4B, yolo26m)

| | Run A (681 frames) | Run B (1503 frames, completed) |
|---|---|---|
| VLM calls / JSON success | 2 / 2 (both on the first try) | 1 / 1 |
| VLM time | 8.6 s, 10.0 s | 9.0 s |
| Generation | 74–80 tok, 10.4–13.7 tok/s, ttft 1.2–1.3 s | 83 tok, 10.6 tok/s, ttft 1.3 s |
| fps VLM idle / busy | 14.0 / 10.2 and 8.4 | 14.2 / 10.9 |
| Video stalls | **one 5.7 s stall** during the 2nd VLM call | none (max gap between frames 0.27 s) |

- VLM speed is about half the standalone figure (22.4 tok/s, ttft 0.34 s), presumably from contention with YOLO.
- Example answers: 「デスクワークの王」「思考の戦士」 (titles), 「机に向かって座る」 (pose), 「ノートパソコン」 (item) — matched the image, no mention of personal attributes.
- **The same person gets the same scores** (fashion 70 / confidence 60 / energy 40 / coolness 50 in both runs → both 9,379).
  The VLM decodes deterministically and tends to return round multiples of 10, so power levels may cluster.
- CMA: Run B loaded the VLM fine even though CmaFree was down to 0.93 GB from Run A (Qwen3-VL-4B fits in what is left after one run).
  It is now 0.91 GB; whether a third run loads is unconfirmed.

## Making power levels more varied (Step 2-A)

Problem: on the live camera the VLM gave the same person identical stats every time (70/60/40/50 → 9,379 both runs).

### Prompt comparison (`tools/vlm_bench.py`, 12 photos from taste_in_clothes, results in `tools/vlm_bench.jsonl`)

| | v1 (README §6) | v2 (scoring rules + pose vocabulary) |
|---|---|---|
| JSON success | 12/12 | 12/12 |
| Stat stdev / range | 12.1 / 60–98 | **16.0 / 40–95** |
| Multiples of 5 | 100% | 100% ("use 1-point steps" is ignored) |
| Distinct score sets | 11/12 | 9/12 |
| Ranks | BOSS 8 / ELITE 4 (inflated) | BOSS 3 / ELITE 8 / STRONG 1 |
| Time per call | 7.3 s (13.7 tok/s, ttft 1.1 s) | 7.5 s (13.4 tok/s, ttft 1.3 s) |

→ v2 is the default (spreads wider, less inflated), but prompting alone cannot get past multiples of 5 or repeated score sets.

### Fixes
1. **Blend in measured values** (`power_measure_weight=0.3`): fashion ← clothing colourfulness (Hasler–Süsstrunk),
   confidence ← height in the frame, energy ← ByteTrack movement speed (peak-hold, body heights/s).
   Re-scoring the bench results offline: **distinct score sets 12/12, multiples of 5 down to 54%.**
   Weakness: monochrome outfits (e.g. all black) get a lower fashion score.
2. **Pose/item bonus** (`power_bonus=true`, table in `power.POSE_BONUS`): JUMP ×1.8, fighting stance ×1.6,
   guts pose ×1.5, hands up ×1.4, peace / arms crossed / pointing ×1.3, carrying an item ×1.1. Shown as BONUS on the HUD.

Not yet checked on the live camera: whether energy actually rises with movement, and whether the VLM puts poses into words the bonus table recognises.

Note: right after editing on the host, the DevKit sometimes runs the old code (NFS cache). If a change doesn't seem to apply, clear `__pycache__` and run again.

### Live test 1 (4 people, 154 s) → second round of fixes

| # | Behaviour | VLM raw → blended | Power (linear curve) |
|---|---|---|---|
| 1 | seated | 60/70/40/55 → 51/76/49/55 | 10,987 |
| 2 | peace | 40/60/70/50 → 40/72/74/50 (PEACE x1.3) | 11,406 |
| 3 | thumbs up | 40/60/30/50 → 34/61/28/50 (not in bonus table) | 7,383 |
| 4 | moving | 40/60/85/70 → 41/72/84/70 | 11,709 |

- Analysis 4/4 succeeded (~9 s each), no stalls, fps idle 13.5–16.4 / busy 9.5–10.0.
- Problem 1: power levels bunched at 7–12k → **switched to an exponential curve** (same 4 people: 4.1k–16.5k).
- Problem 2: the VLM fired **~0.5 s after the person appeared** (still walking in, before posing) → **`vlm_min_track_age_s=2.0`** (HUD shows LOCKING ON in the meantime).
- Problem 3: box-centre speed measured entry and box jitter (seated #1 got energy 70, "moving" #4 had the smallest value)
  → energy is now the **mean in-box frame difference** (luma, 1/4 scale) over the last 1.5 s. A seated person reads 1.7 (≈ noise floor).
  `energy_full_scale=12` is provisional; calibrate it from `"pm"` in track_log.jsonl.
- Thumbs up (親指 / サムズアップ / グッド) added to the bonus table at ×1.3.

### Live test 2 (4 people, 154 s, after the second round of fixes)

| # | Behaviour | Frame diff during lock-on (mean) | VLM raw | Power (full_scale 12 → 30) |
|---|---|---|---|---|
| 1 | still | 2.3 | 50/65/30/70 | 9,778 → 9,644 強者 |
| 2 | peace | 18.9 | 60/70/40/75 | 21,498 → 17,965 エリート (PEACE x1.3) |
| 3 | arms crossed | 23.1 | 50/70/20/60 | 12,970 → 11,613 エリート (ARMS x1.3) |
| 4 | moving | 21.0 | 50/40/70/60 | 12,048 → 10,350 エリート |

- Analysis 4/4 succeeded (8.4–9.0 s), **0 stalls** (no frame gap >0.3 s), fps while the VLM runs 9.5.
- Frame difference: still ≈2, posing/moving ≈19–23, peak ≈37. At full_scale=12 all three saturated at 100,
  so the default is now **`energy_full_scale=30`** (still → 3, posing/moving → 58–72).
- Striking a pose also moves the body during lock-on, so posing and moving give similar energy (both count as "moving raises energy").
- Spread: 9.6k–18k (×1.9). Reaching BOSS or above needs a large bonus (jump ×1.8, etc.) or high VLM ratings.

## Stops during long runs: cause and fix (2026-10-02)

**Symptom:** a long test stopped by itself after 24.5 min (18,810 frames, 12 VLM analyses all successful).
Both this and the earlier Run A (681 frames) showed the same pattern: **while the VLM was running, detection frames stopped for 5.7–6.3 s → one frame arrives → the app exits**.

**Cause:** `Run.push()` waits **5 s (fixed `push_timeout_ms=5000`; RunOptions cannot change it)** when the model's input queue is full,
and on timeout calls `graph_request_stop()` (`request_stop_on_backpressure=true`, core/src/pipeline/runtime/RunCore.cpp),
**which stops the whole YOLO Run permanently**. Occasionally (2 of ~25 calls) the YOLO pipeline stalls for 5 s or more while the VLM is generating, and that triggers it.

**Reproduced** (`tools/stall_test.py`: push frames for 8 s without consuming the output, then resume):

| Push method | During the stall | After resuming |
|---|---|---|
| `push()` (old) | Run stops internally (`GraphRun::push timed out ... push_timeout_ms=5000`) | **0/20 → never recovers** |
| `try_push()` (new) | Simply rejects while full (accepted 42 / rejected 77, no exception) | **20/20 → recovers** |

**Fix (main.py):**
- The pusher now uses `try_push()` (timeout 0, no stop). When full, that frame is dropped (capture_q only keeps the newest frame anyway).
  The model queue stays `OverflowPolicy.Block`, so this is not the "drop inside the queue" setting CLAUDE.md warns about.
- The puller no longer stops after 5 consecutive pull timeouts (10 s); it stops only if there has been **no output for `model_stall_timeout_s=30` s**.
- Stalls of 1 s or more and all errors are written to `scouter_events.log` with timestamps as they happen; the summary shows the number of stalls and the longest one.

Known side effect: right after a stall clears, up to ~40 queued frames are processed in a burst, so the video can lag by a few seconds.

### Long-run check after the fix (2026-10-02)

| Item | Result |
|---|---|
| Run time / frames | **54.3 min / 44,249 frames, no stop** |
| Frame gaps over 0.5 s | **0** (`scouter_events.log` empty = no stall ≥1 s occurred) |
| VLM | 15/15 succeeded (avg 9.3 s), 20 IDs in total |
| Power distribution | 1,595 – 25,360 (一般人 1 / 強者 6 / エリート 8) |

- No stall happened during this run, so the `try_push` recovery path was not exercised live (only verified by the `tools/stall_test.py` reproduction).
- Not yet tested: 2 or more people in view at once (max simultaneous tracks was 1).

## Step 2-C: making the VLM faster (2026-10-02)

Current breakdown (15 live calls): **generation ~8 s (84 tok × 10.5 tok/s, 85% of the total)** + time to first token 1.44 s.

`tools/vlm_speed.py` (6 photos, with a thread pushing frames into YOLO at 13 fps alongside, results in `tools/vlm_speed.jsonl`):

| Model / prompt / image | Time | Tokens | tok/s | ttft | YOLO alongside | Quality |
|---|---|---|---|---|---|---|
| Qwen3-VL-4B v2 / 512 (old default) | 8.6 s | 86 | 11.9 | 1.43 s | 5.7 fps | — |
| **Qwen3-VL-4B v3 / 512 (new default)** | **5.9 s** | 54 | 11.9 | 1.43 s | 5.6 fps | about the same as v2 |
| Qwen3-VL-4B v4 / 512 | 5.7 s | 52 | 11.1 | 1.43 s | 5.5 fps | **3 people standing misread as "arms crossed"** (wrong bonus) |
| Qwen3-VL-4B v3 / 384 | 5.9 s | 54 | 12.3 | 1.43 s | 5.6 fps | ttft unchanged; garbled text (「漫步者」「気 chất」) |
| gemma-4-E2B v3 / 512 | **3.3 s** | 50 | 18.1 | 0.70 s | 8.1 fps | titles are descriptive; misses arms crossed |

- v3 = v2's content returned as one compact JSON line (no newlines/indentation) with shorter title/comment. JSON 6/6 succeeded.
- Shrinking the image does not shorten ttft (input appears to be resized to a fixed size internally) and only degrades quality → keep 512.
- New defaults: `vlm_prompt_version=3`, `vlm_max_new_tokens=120`, `vlm_expected_s=6.0`.
  For crowds, switch with `--vlm-model-dir /media/nvme/llima/models/gemma-4-E2B-it-GPTQ-a16w4`.
- The YOLO fps above is from the bench's lockstep push→pull and is not the same as the app's fps (app measured 9.5–10.9 while the VLM runs).

- **2026-10-02: at the user's request, the default VLM is now gemma-4-E2B** (`vlm_model_dir`; `vlm_expected_s=3.5` to match).
  Switch back to Qwen with `--vlm-model-dir /media/nvme/llima/models/Qwen3-VL-4B-Instruct-GPTQ-a16w4`.

## HDMI output (2026-10-02)

`hdmi_out.py`: HUD frames → cv2 letterbox resize → `appsrc ! kmssink driver-name=smifb skip-vsync=true`.
It follows the DevKit constraints recorded in taste_in_clothes/devkit_direct_hdmi.py:
lightdm must be stopped to get the DRM master, `driver-name=smifb` is required, no hardware scaling, `skip-vsync` is required.

- Runs alongside UDP (`hdmi_enabled=true` by default; `--no-hdmi` turns it off). Its own thread (`hdmi-send`); if it can't start, the app warns and continues with UDP only.
- lightdm is stopped on startup and restarted on exit (also on Ctrl-C/SIGTERM, since the restart is in finally).
  **If the process is killed with SIGKILL or similar, the desktop stays stopped** → restart it with `dk ./tools/lightdm_status.py --start`.
- Measured: `tools/hdmi_test.py` shows 1920x1080 with a 9.2 ms average push (in the same process as pyneat).
  Dry-run app run: 297/301 frames on HDMI, **fps 12.9 (unchanged from before HDMI)**, lightdm active after exit.

### DevKit reset during HDMI output (2026-10-02, under investigation)

- Symptom: on the serial console (picocom), `FATAL: read zero bytes from port` / `term_exitfunc: reset failed ...`
  = **the USB serial device disappeared because the DevKit reset**.
- journal: in the 06:10 and 06:20 boots, **the log stops 39 s / 31 s after the app stopped lightdm**. No shutdown, no kernel panic or error lines (next boot logs "uncleanly shut down").
- The last run's track_log: the VLM (E2B) finished one analysis in 3.9 s, then **died at 30.8 s while nobody was in view and the VLM was idle** → not triggered by VLM inference.
- My earlier HDMI checks were all **under 30 s** (6 s and 24 s), so they missed it. The app without HDMI has run for 54 min (Qwen).
- Only clue: the camera, which had been fixed at 12.9 fps, delivered 23 fps in this run (720 frames / 30.8 s) → higher load.
- Precaution: **`hdmi_enabled` default changed to false** (`--hdmi` to enable).
- Next: isolation experiments (HDMI alone for 2 min / app without HDMI / app with HDMI but no VLM), plus measuring board power and temperature.

#### Isolation experiments (2026-10-02, with `sysmon.py` logging power, temperature and load every second, fsync'd)

| # | Condition | Result | Peak power / SoC temp |
|---|---|---|---|
| ① | HDMI alone (test pattern 20 fps, 1080p), 120 s | OK | 9.2 W / 66 °C |
| ② | App + HDMI (25 fps, 1080p), **no VLM**, 110 s | OK | 15.6 W / 68.5 °C |
| ③ | App + **VLM (E2B)**, no HDMI, 158 s | OK | 14.4 W / 74 °C |
| ④ | **App + VLM + HDMI (25 fps, 1080p)** | **Reset at 71 s** (earlier 2 runs: 31 s, 39 s) | 16.3 W / 74 °C |
| M1 | App + VLM + HDMI forced to **720p** | kmssink fails to start (smifb doesn't support mode setting) | — |
| M2 | App + VLM + HDMI **1080p capped at 10 fps** | **296 s, no reset** | 15.9 W / 74 °C |

- Just before ④'s reset: power 13.4–14.9 W (below ②), SoC 74 °C / board 63 °C (critical 96 °C), CmaFree 1250 MB, VLM idle.
  → **Not explained by heat, total power or memory shortage**. No kernel log either (the USB serial is cut too) = instant hardware-level reset.
- Only the **combination of YOLO (MLA, continuous) + VLM resident in memory + continuous HDMI writes (SM768 on PCIe, 8.3 MB/frame × 25 fps ≈ 207 MB/s)** triggers it.
  Timing is irregular → suspected hang from bus/PCIe/DDR traffic contention followed by a watchdog reset (platform level; cannot be fixed in the app).
- Workaround: `hdmi_max_fps=10` (HDMI traffic ≈ 83 MB/s). The forced-720p route is impossible with smifb (`driver does not provide mode settings configuration`).
| M3 | Same as M2, long run | **1088 s (18 min), no reset**. VLM 4/4, 9,254 frames to HDMI, no stalls ≥1 s | 15.6 W / 74 °C (board 66 °C) |

- Conclusion: with **`hdmi_max_fps=10`**, HDMI is back on by default (`hdmi_enabled=true`). Do not raise it to 25 fps.
- Remaining risk: the root cause (platform level) is not fixed; the cap only lowers the probability. It would be worth reporting to SiMa.ai
  with the reproduction conditions (④) and `sysmon.jsonl`.
