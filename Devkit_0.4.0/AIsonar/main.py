#!/usr/bin/env python3
"""AI SONAR: USBカメラ単眼深度推定 (MiDaS v2.1 small / MLA) → 潜水艦ソナー風 PPI 表示 → Insight。

    dk ./main.py --frames 300                      # 有限フレームでの動作確認（まずこれ）
    dk ./main.py                                   # 連続実行（Ctrl-C / SIGTERM で終了）
    dk ./main.py --calibrate --frames 600          # 距離換算の校正用に中央の逆深度を表示
    dk ./main.py --frames 200 --snapshot output/sonar.png

スレッド構成:
    camera (V4L2, 最新1枚を保持)
      → infer: 前処理 → MLA 推論 (~7 ms) → 列ごとの最近距離 + EMA   … カメラのフレームごとに1回 (FR-01)
      → main:  out_fps の一定周期でソナー描画 → NV12 → H.264/RTP UDP
描画を推論と切り離しているのは、スイープ線の動きをカメラ/推論の揺らぎに関係なく滑らかにするため。
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
import math
import signal
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from camera import Camera  # noqa: E402
from depth import MidasMla, cv2, np, preprocess  # noqa: E402
from sonar import GREEN, RangeProfile, SonarRenderer  # noqa: E402

import pyneat  # noqa: E402


@dataclass
class Config:
    camera_device: str = "/dev/video0"
    capture_width: int = 640
    capture_height: int = 360
    camera_hfov_deg: float = 65.0
    model_path: str = "./assets/midas_v21_small_256_mla_mpk.tar.gz"
    band_top: float = 0.30
    band_bottom: float = 0.60
    bearing_bins: int = 64
    nearest_percentile: float = 90.0
    ema_alpha: float = 0.35
    depth_scale: float = 1250.0
    depth_shift: float = 245.0
    max_range_m: float = 4.0
    ring_step_m: float = 1.0
    sweep_period_s: float = 3.0
    echo_half_life_s: float = 1.2
    beam_half_life_s: float = 0.25
    echo_thickness_m: float = 0.06
    draw_profile: bool = True
    target_lock: bool = True
    alert_range_m: float = 0.7
    udp_host: str = "10.42.0.1"
    udp_port: int = 9000
    out_width: int = 1280
    out_height: int = 720
    out_fps: int = 30
    bitrate_kbps: int = 4000
    show_inset: bool = True


def load_config(path: Path) -> Config:
    cfg = Config()
    types = {f.name: f.type for f in fields(Config)}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, sep, val = (s.strip() for s in line.partition("="))
        if not sep or key not in types:
            raise ValueError(f"{path}:{lineno}: unknown or malformed line: {raw!r}")
        t = types[key]
        if t == "bool":
            v = val.lower() in ("1", "true", "yes", "on")
        elif t == "int":
            v = int(val)
        elif t == "float":
            v = float(val)
        else:
            v = val
        setattr(cfg, key, v)
    return cfg


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(APP_DIR / "config/default.conf"))
    ap.add_argument("--frames", type=int, default=0, help="この出力フレーム数で終了 (0=無制限)")
    ap.add_argument("--udp-host")
    ap.add_argument("--udp-port", type=int)
    ap.add_argument("--camera-device")
    ap.add_argument("--no-video", action="store_true", help="UDP 送出しない（描画・計測のみ）")
    ap.add_argument("--snapshot", help="終了時に最後の出力フレームを PNG 保存")
    ap.add_argument("--calibrate", action="store_true",
                    help="中央付近の逆深度と現在の換算距離を1秒ごとに表示 (config の depth_scale/shift 校正用)")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    if args.udp_host:
        cfg.udp_host = args.udp_host
    if args.udp_port:
        cfg.udp_port = args.udp_port
    if args.camera_device:
        cfg.camera_device = args.camera_device
    if 256 % cfg.bearing_bins:
        raise SystemExit("bearing_bins must divide 256")
    return args, cfg


# ---------------------------------------------------------------- video out (Scouter と同じ)

def make_nv12_input_options(width: int, height: int, fps: int):
    input_opt = pyneat.InputOptions()
    input_opt.payload_type = pyneat.PayloadType.Image
    input_opt.format = pyneat.Format.NV12
    input_opt.width = width
    input_opt.height = height
    input_opt.depth = 1
    input_opt.max_width = width
    input_opt.max_height = height
    input_opt.max_depth = 1
    input_opt.fps_n = max(1, fps)
    input_opt.fps_d = 1
    input_opt.caps_override = (
        f"video/x-raw,format=NV12,width={width},height={height},framerate={max(1, fps)}/1"
    )
    input_opt.memory_policy = pyneat.InputMemoryPolicy.SystemMemory
    return input_opt


def bgr_to_nv12(bgr):
    """cv2 has no BGR->NV12, so go through planar I420 and interleave U/V."""
    h, w = bgr.shape[:2]
    i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
    nv12 = np.empty((h * 3 // 2, w), dtype=np.uint8)
    nv12[:h] = i420[:h]
    uv = nv12[h:].reshape(h // 2, w // 2, 2)
    uv[..., 0] = i420[h:h + h // 4].reshape(h // 2, w // 2)
    uv[..., 1] = i420[h + h // 4:].reshape(h // 2, w // 2)
    return nv12


def make_nv12_tensor(nv12, width: int, height: int):
    tensor = pyneat.Tensor.from_numpy(np.ascontiguousarray(nv12), copy=True,
                                      layout=pyneat.TensorLayout.HW, memory=pyneat.TensorMemory.CPU)
    tensor.shape = [height, width]
    tensor.strides_bytes = [width, 1]
    tensor.byte_offset = 0
    image = pyneat.ImageSpec()
    image.format = pyneat.PixelFormat.NV12
    semantic = tensor.semantic
    semantic.image = image
    tensor.semantic = semantic
    y = pyneat.Plane()
    y.role = pyneat.PlaneRole.Y
    y.shape = [height, width]
    y.strides_bytes = [width, 1]
    y.byte_offset = 0
    uv = pyneat.Plane()
    uv.role = pyneat.PlaneRole.UV
    uv.shape = [height // 2, width]
    uv.strides_bytes = [width, 1]
    uv.byte_offset = width * height
    tensor.planes = [y, uv]
    return tensor


def build_video_run(cfg: Config):
    w, h, fps = cfg.out_width, cfg.out_height, cfg.out_fps
    sender_opt = pyneat.VideoSenderOptions.h264_rtp_udp_from_raw(w, h, max(1, fps))
    sender_opt.host = cfg.udp_host
    sender_opt.channel = 0
    sender_opt.video_port_base = cfg.udp_port
    sender_opt.encoder.bitrate_kbps = cfg.bitrate_kbps
    graph = pyneat.Graph("video_out")
    graph.add(pyneat.nodes.input(make_nv12_input_options(w, h, fps)))
    graph.add(pyneat.groups.video_sender(sender_opt))
    seed = np.full((h * 3 // 2, w), 128, dtype=np.uint8)
    seed[:h, :] = 16
    return graph, graph.build([make_nv12_tensor(seed, w, h)]), sender_opt.video_port


# ---------------------------------------------------------------- inference thread

class DepthWorker(threading.Thread):
    """カメラの新しいフレームごとに 深度推定 → 距離プロファイル更新。結果は最新のみ保持。"""

    def __init__(self, cfg: Config, cam: Camera, model: MidasMla, stop: threading.Event):
        super().__init__(daemon=True, name="depth")
        self.cfg, self.cam, self.model, self.stop = cfg, cam, model, stop
        self.profile = RangeProfile(cfg)
        self.lock = threading.Lock()
        self.state = None  # dict(frame, depth, ranges, stamp)
        self.count = 0
        self.infer_ms: list[float] = []
        self.total_ms: list[float] = []
        self.error = None

    def run(self):
        last_stamp = 0.0
        try:
            while not self.stop.is_set():
                frame, stamp = self.cam.latest_stamped()
                if stamp == last_stamp:
                    time.sleep(0.002)
                    continue
                last_stamp = stamp
                t0 = time.perf_counter()
                x = preprocess(frame, self.model.size)
                t1 = time.perf_counter()
                inv = self.model.infer(x)
                t2 = time.perf_counter()
                ranges = self.profile.update(inv)
                self.infer_ms.append((t2 - t1) * 1000)
                self.total_ms.append((time.perf_counter() - t0) * 1000)
                with self.lock:
                    self.state = {"frame": frame, "depth": inv, "ranges": ranges.copy(), "stamp": stamp}
                    self.count += 1
        except Exception as exc:  # noqa: BLE001 - surface to main thread
            self.error = f"depth: {exc}"
            self.stop.set()

    def latest(self):
        with self.lock:
            return self.state


# ---------------------------------------------------------------- composition

def depth_vis(inv, w, h):
    d = inv - inv.min()
    d = (d * (255.0 / max(1e-6, float(d.max())))).astype(np.uint8)
    g = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)
    z = np.zeros_like(g)
    return cv2.merge([(g.astype(np.uint16) * 60 // 255).astype(np.uint8), g, z])  # 緑の濃淡


def compose(cfg: Config, sonar_img, state, stats_lines):
    if not cfg.show_inset:
        return sonar_img
    W, H = cfg.out_width, cfg.out_height
    pw = W - sonar_img.shape[1]
    out = np.zeros((H, W, 3), np.uint8)
    out[:, pw:] = sonar_img
    th = pw * 9 // 16
    y = 30
    cv2.putText(out, "CAMERA", (10, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1, cv2.LINE_AA)
    if state is not None:
        cam = cv2.resize(state["frame"], (pw, th), interpolation=cv2.INTER_AREA)
        y0, y1 = int(th * cfg.band_top), int(th * cfg.band_bottom)
        cv2.rectangle(cam, (1, y0), (pw - 2, y1), GREEN, 1)  # 距離抽出に使っている水平帯
        out[y:y + th, :pw] = cam
        y2 = y + th + 30
        cv2.putText(out, "DEPTH (MiDaS / MLA)", (10, y2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1, cv2.LINE_AA)
        dv = depth_vis(state["depth"], pw, th)
        cv2.rectangle(dv, (1, y0), (pw - 2, y1), GREEN, 1)
        out[y2:y2 + th, :pw] = dv
        y = y2 + th + 30
    else:
        y = y + 2 * th + 60
    for line in stats_lines:
        cv2.putText(out, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 1, cv2.LINE_AA)
        y += 24
    cv2.line(out, (pw - 1, 0), (pw - 1, H), (25, 110, 40), 1)
    return out


def summarize(values):
    if not values:
        return "n/a"
    s = sorted(values)
    return f"mean={sum(s) / len(s):.1f}ms p95={s[max(0, int(len(s) * 0.95) - 1)]:.1f}ms"


# ---------------------------------------------------------------- main

def main() -> int:
    args, cfg = parse_args()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    model_path = Path(cfg.model_path)
    model_path = model_path if model_path.is_absolute() else APP_DIR / model_path
    t0 = time.perf_counter()
    model = MidasMla(str(model_path))
    print(f"Model: {model_path.name} loaded in {time.perf_counter() - t0:.1f}s", flush=True)

    video_run, port = None, None
    if not args.no_video:
        _, video_run, port = build_video_run(cfg)
        print(f"Video: {cfg.out_width}x{cfg.out_height}@{cfg.out_fps} H.264/RTP -> udp://{cfg.udp_host}:{port}")
        print(f"  Viewer: Insight, or gst-launch-1.0 -v udpsrc port={port} "
              f"caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" "
              f"! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! autovideosink sync=false", flush=True)

    cam = Camera(cfg.camera_device, cfg.capture_width, cfg.capture_height)
    cam.start()
    worker = DepthWorker(cfg, cam, model, stop)
    worker.start()

    sonar_w = cfg.out_width - (cfg.out_width // 4 if cfg.show_inset else 0)
    renderer = SonarRenderer(cfg, sonar_w, cfg.out_height)
    period = 1.0 / max(1, cfg.out_fps)
    frames = sent = dropped = 0
    render_ms: list[float] = []
    start = time.perf_counter()
    next_t = start
    fps_win, fps_val = [], 0.0
    last_calib = 0.0
    last_depth_count, depth_fps, depth_t = 0, 0.0, start
    out = None
    try:
        while not stop.is_set():
            now = time.perf_counter()
            if now < next_t:
                time.sleep(next_t - now)
                continue
            next_t = max(next_t + period, now - period)  # 遅れても追いつこうと連射しない

            state = worker.latest()
            t_r = time.perf_counter()
            ranges = state["ranges"] if state is not None else None
            sonar_img, nearest = renderer.render(ranges, worker.profile.bearings, now)
            fps_win.append(now)
            fps_win = [t for t in fps_win if now - t < 1.0]
            fps_val = len(fps_win)
            if now - depth_t >= 1.0:
                depth_fps = (worker.count - last_depth_count) / (now - depth_t)
                last_depth_count, depth_t = worker.count, now
            infer = worker.infer_ms[-1] if worker.infer_ms else 0.0
            lines = [f"OUT {fps_val:4.1f} fps   DEPTH {depth_fps:4.1f} fps",
                     f"MLA infer {infer:4.1f} ms",
                     f"HFOV {cfg.camera_hfov_deg:g}deg  RANGE {cfg.max_range_m:g}m"]
            if nearest is not None:
                lines.append(f"NEAREST {math.degrees(nearest[0]):+05.1f}deg {nearest[1]:.2f}m")
            if state is None:
                lines.append("WAITING FOR CAMERA...")
            out = compose(cfg, sonar_img, state, lines)
            cv2.putText(out, "AI SONAR", (cfg.out_width - 150, 32), cv2.FONT_HERSHEY_DUPLEX, 0.8, GREEN, 1,
                        cv2.LINE_AA)
            nv12 = bgr_to_nv12(out)
            render_ms.append((time.perf_counter() - t_r) * 1000)
            if video_run is not None:
                if video_run.try_push([make_nv12_tensor(nv12, cfg.out_width, cfg.out_height)]):
                    sent += 1
                else:
                    dropped += 1
            frames += 1

            if args.calibrate and state is not None and now - last_calib >= 1.0:
                last_calib = now
                d = state["depth"]
                h, w = d.shape
                c = float(np.median(d[h * 2 // 5:h * 3 // 5, w * 2 // 5:w * 3 // 5]))
                mid = state["ranges"][len(state["ranges"]) // 2]
                print(f"[CAL] center inv_depth={c:7.1f}  -> range now {cfg.depth_scale / (c + cfg.depth_shift):.2f}m "
                      f"(center bin {mid:.2f}m)", flush=True)
            if args.frames and frames >= args.frames:
                break
            if frames % 150 == 0:
                print(f"[{frames}] out={fps_val:.1f}fps depth={depth_fps:.1f}fps infer={infer:.1f}ms "
                      f"render={render_ms[-1]:.1f}ms sent={sent} dropped={dropped}", flush=True)
    finally:
        stop.set()
        worker.join(timeout=3)
        cam.stop()
        elapsed = time.perf_counter() - start
        print(f"Done: {frames} frames in {elapsed:.1f}s ({frames / max(elapsed, 1e-6):.1f} fps out), "
              f"depth frames={worker.count} ({worker.count / max(elapsed, 1e-6):.1f} fps), sent={sent} dropped={dropped}")
        print(f"  MLA infer {summarize(worker.infer_ms[5:])} | depth total {summarize(worker.total_ms[5:])} "
              f"| render+nv12 {summarize(render_ms[5:])}")
        if args.snapshot and out is not None:
            snap = Path(args.snapshot)
            snap = snap if snap.is_absolute() else APP_DIR / snap
            snap.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(snap), out)
            print(f"Snapshot -> {snap}")
    if worker.error:
        print(f"[ERR] {worker.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
