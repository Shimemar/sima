#!/usr/bin/env python3
"""AI SCOUTER step 1: webcam -> YOLO26 person detection -> ByteTrack -> annotated UDP video.

Experiment for /workspace/Scouter/README.md sections 2-4 (YOLO + ByteTrack only, no VLM
yet). Goal: measure on the real DevKit whether detection+tracking holds the fps target
and whether track IDs stay stable enough to key a per-person VLM result cache on.

Architecture is this workspace's standard 4-stage threaded pipeline, adapted from
vlm_ngen_demo/main.py (itself from test_1080p) with the VLM leg removed:
  capture thread -> pusher thread -> model Run -> puller thread (decode + ByteTrack +
  draw) -> sender thread -> H.264/RTP UDP output.
Every inter-stage handoff is a drop-oldest LatestQueue except the model's own push/pull
queue (OverflowPolicy.Block, depth model_queue_depth) -- see /workspace/CLAUDE.md.

ByteTrack needs LOW-score detections too (its 2nd association stage), so the YOLO
score_threshold here is deliberately much lower (0.10) than a plain detector app would
use. Display/recall is controlled by the tracker thresholds, not score_threshold.

pyneat practices carried over from vlm_ngen_demo: NormalizePreset.COCO_YOLO +
BoxDecodeType.YoloV26 for YOLO26, input_max_depth=3, no Model.build([seed]) (plain Graph
+ graph.build(run_opts)), input_max_width/height = actual capture size, UDP output.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import glob
import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time

cv2 = None
np = None
pyneat = None
tracker_mod = None
hud_mod = None
vlm_mod = None
hdmi_mod = None

COCO_PERSON = 0

# Per-track-ID box colors as (Y, U, V); BGR source colors converted in id_color().
ID_PALETTE_BGR = [
    (0, 255, 0), (0, 200, 255), (255, 128, 0), (255, 0, 255), (0, 255, 255),
    (255, 255, 0), (128, 0, 255), (0, 128, 255), (255, 0, 128), (128, 255, 0),
]
LOW_SCORE_COLOR = (90, 128, 128)  # dark grey: raw low-score detections (debug overlay)


@dataclass
class Config:
    # --- webcam capture ---
    camera_device: str = "/dev/video16"
    camera_flip: str = "none"
    capture_width: int = 1280
    capture_height: int = 720
    fallback_fps: int = 30

    # --- YOLO26 detector ---
    model_path: str = "./assets/yolo26m-det-bf16-mla_tess-b1.tar.gz"
    model_width: int = 640
    model_height: int = 640
    score_threshold: float = 0.10
    nms_iou: float = 0.60
    top_k: int = 100
    num_classes: int = 80

    # --- ByteTrack ---
    track_high_thresh: float = 0.50
    track_low_thresh: float = 0.10
    new_track_thresh: float = 0.60
    match_thresh: float = 0.80
    lost_timeout_s: float = 1.5
    draw_raw_detections: bool = False

    # --- output ---
    hdmi_enabled: bool = True
    hdmi_driver: str = "smifb"
    hdmi_width: int = 0
    hdmi_height: int = 0
    hdmi_max_fps: float = 10.0
    udp_host: str = "10.42.0.1"
    udp_port: int = 9000
    bitrate_kbps: int = 4000
    model_queue_depth: int = 4
    warmup_frames: int = 20
    track_log: str = "./track_log.jsonl"
    event_log: str = "./scouter_events.log"
    sysmon_log: str = "./sysmon.jsonl"
    model_stall_timeout_s: float = 30.0

    # --- VLM (vlm_worker.ScouterVlm) ---
    vlm_enabled: bool = True
    vlm_dry_run: bool = False
    vlm_dry_run_delay_s: float = 6.0
    vlm_model_dir: str = "/media/nvme/llima/models/gemma-4-E2B-it-GPTQ-a16w4"
    vlm_max_new_tokens: int = 120
    vlm_max_pending: int = 3
    vlm_max_attempts: int = 2
    vlm_min_track_hits: int = 8
    vlm_min_score: float = 0.60
    vlm_min_area_frac: float = 0.03
    vlm_crop_margin: float = 0.08
    vlm_crop_max_side: int = 512
    vlm_worker_nice: int = 0
    vlm_results_log: str = "./scouter_results.jsonl"

    # --- power / HUD ---
    power_boost: bool = True
    power_bonus: bool = True
    power_measure_weight: float = 0.3
    vlm_min_track_age_s: float = 2.0
    energy_window_s: float = 1.5
    energy_noise_floor: float = 1.5
    energy_full_scale: float = 30.0
    vlm_prompt_version: int = 3
    hud_enabled: bool = True
    hud_countup_s: float = 1.5
    vlm_expected_s: float = 3.5

    frames: int = 0
    print_backend: bool = False


INT_KEYS = {"capture_width", "capture_height", "fallback_fps", "model_width", "model_height",
            "top_k", "num_classes", "udp_port", "bitrate_kbps", "model_queue_depth",
            "warmup_frames", "frames", "vlm_max_new_tokens", "hdmi_width", "hdmi_height", "vlm_max_pending",
            "vlm_max_attempts", "vlm_min_track_hits", "vlm_crop_max_side", "vlm_worker_nice",
            "vlm_prompt_version"}
FLOAT_KEYS = {"score_threshold", "nms_iou", "track_high_thresh", "track_low_thresh",
              "new_track_thresh", "match_thresh", "lost_timeout_s", "vlm_dry_run_delay_s",
              "vlm_min_score", "vlm_min_area_frac", "vlm_crop_margin", "hud_countup_s",
              "vlm_expected_s", "power_measure_weight", "vlm_min_track_age_s", "energy_window_s",
              "energy_noise_floor", "energy_full_scale", "model_stall_timeout_s", "hdmi_max_fps"}
BOOL_KEYS = {"draw_raw_detections", "print_backend", "vlm_enabled", "vlm_dry_run", "hdmi_enabled",
             "power_boost", "power_bonus", "hud_enabled"}
STR_KEYS = {"camera_device", "camera_flip", "model_path", "udp_host", "track_log", "event_log", "sysmon_log",
            "hdmi_driver",
            "vlm_model_dir", "vlm_results_log"}


def load_runtime_dependencies() -> None:
    global cv2, np, pyneat, tracker_mod, hud_mod, vlm_mod, hdmi_mod
    if pyneat is not None:
        return
    for path in glob.glob("/usr/lib/python3*/dist-packages"):
        if path not in sys.path:
            sys.path.insert(0, path)
    import cv2 as cv2_module
    import numpy as np_module
    import pyneat as pyneat_module
    import tracker as tracker_module
    import hud as hud_module
    import vlm_worker as vlm_module
    cv2, np, pyneat, tracker_mod = cv2_module, np_module, pyneat_module, tracker_module
    import hdmi_out as hdmi_module
    hud_mod, vlm_mod, hdmi_mod = hud_module, vlm_module, hdmi_module


def resolve_app_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(__file__).resolve().parent / path


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def apply_config_value(cfg: Config, key: str, value: str) -> None:
    if key in INT_KEYS:
        setattr(cfg, key, int(value))
    elif key in FLOAT_KEYS:
        setattr(cfg, key, float(value))
    elif key in BOOL_KEYS:
        setattr(cfg, key, parse_bool(value))
    elif key in STR_KEYS:
        setattr(cfg, key, value)
    else:
        raise ValueError(f"unknown config key: {key}")


def load_config_file(cfg: Config, path: Path) -> None:
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_no}: expected key=value")
        key, value = [part.strip() for part in line.split("=", 1)]
        apply_config_value(cfg, key, value)


def parse_args(argv: list[str] | None) -> Config:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path,
                        default=Path(__file__).resolve().parent / "config" / "default.conf")
    parser.add_argument("--camera-device")
    parser.add_argument("--camera-flip", choices=["none", "rotate-180", "horizontal-flip", "vertical-flip"])
    parser.add_argument("--model", help="YOLO26 archive (e.g. ./assets/yolo26n-det-bf16-mla_tess-b1.tar.gz)")
    parser.add_argument("--frames", type=int, help="0 = run forever")
    parser.add_argument("--udp-host")
    parser.add_argument("--udp-port", type=int)
    parser.add_argument("--draw-raw", action="store_true",
                        help="生のYOLO検出(低スコア含む)を灰色で重ねて描画する")
    parser.add_argument("--track-log", help="トラックのJSON-linesログ(空文字で無効化)")
    parser.add_argument("--no-vlm", action="store_false", dest="vlm", default=None,
                        help="VLMを使わない(YOLO+ByteTrackのみ、Step 1相当)")
    parser.add_argument("--vlm-dry-run", action="store_true", default=None,
                        help="VLMをロードせず、架空の解析結果を返す(HUD・トリガー確認用)")
    parser.add_argument("--vlm-nice", type=int, help="VLMワーカースレッドのnice値(0-19)")
    parser.add_argument("--vlm-model-dir", help="VLMモデルディレクトリ (例: .../gemma-4-E2B-it-GPTQ-a16w4)")
    parser.add_argument("--hdmi", action="store_true", dest="hdmi", default=None,
                        help="DevKitのHDMIにも出す(実行中lightdmを停止)。既定でオン")
    parser.add_argument("--no-hdmi", action="store_false", dest="hdmi", default=None,
                        help="DevKitのHDMIに出さない(lightdmも止めない)。UDP出力のみ")
    parser.add_argument("--no-hud", action="store_false", dest="hud", default=None,
                        help="HUDを描かずID枠だけ描く(描画コスト比較用)")
    parser.add_argument("--print-backend", action="store_true")
    args = parser.parse_args(argv)

    cfg = Config()
    if args.config.exists():
        load_config_file(cfg, args.config)
    if args.camera_device is not None:
        cfg.camera_device = args.camera_device
    if args.camera_flip is not None:
        cfg.camera_flip = args.camera_flip
    if args.model is not None:
        cfg.model_path = args.model
    if args.frames is not None:
        cfg.frames = args.frames
    if args.udp_host is not None:
        cfg.udp_host = args.udp_host
    if args.udp_port is not None:
        cfg.udp_port = args.udp_port
    if args.draw_raw:
        cfg.draw_raw_detections = True
    if args.track_log is not None:
        cfg.track_log = args.track_log
    if args.vlm is not None:
        cfg.vlm_enabled = args.vlm
    if args.vlm_dry_run:
        cfg.vlm_dry_run = True
    if args.vlm_nice is not None:
        cfg.vlm_worker_nice = args.vlm_nice
    if args.vlm_model_dir is not None:
        cfg.vlm_model_dir = args.vlm_model_dir
    if args.hud is not None:
        cfg.hud_enabled = args.hud
    if args.hdmi is not None:
        cfg.hdmi_enabled = args.hdmi
    if args.print_backend:
        cfg.print_backend = True
    return cfg


def validate_config(cfg: Config) -> None:
    if not resolve_app_path(cfg.model_path).exists():
        raise FileNotFoundError(f"model file not found: {resolve_app_path(cfg.model_path)}")
    if not (0 < cfg.udp_port <= 65535):
        raise ValueError("udp_port must be in 1..65535")
    if cfg.score_threshold > cfg.track_low_thresh:
        print(f"[WARN] score_threshold={cfg.score_threshold} > track_low_thresh="
              f"{cfg.track_low_thresh}: ByteTrack's low-score association gets no input",
              file=sys.stderr)


# ---------------------------------------------------------------- graphs (from vlm_ngen_demo)

def camera_fragment(cfg: Config) -> str:
    frag = (
        f"v4l2src device={cfg.camera_device} io-mode=mmap"
        f" ! image/jpeg,width={cfg.capture_width},height={cfg.capture_height},framerate={cfg.fallback_fps}/1"
        f" ! queue leaky=downstream max-size-buffers=2"
        f" ! jpegparse ! jpegdec"
    )
    if cfg.camera_flip != "none":
        frag += f" ! videoflip method={cfg.camera_flip}"
    frag += (
        f" ! videoconvert n-threads=4"
        f" ! video/x-raw,format=NV12,width={cfg.capture_width},height={cfg.capture_height}"
        f",framerate={cfg.fallback_fps}/1"
        f" ! queue leaky=downstream max-size-buffers=2"
    )
    return frag


def build_source_run(cfg: Config):
    graph = pyneat.Graph(f"source_{cfg.capture_width}x{cfg.capture_height}")
    graph.add(pyneat.nodes.custom(camera_fragment(cfg), pyneat.InputRole.Source))
    graph.add(pyneat.nodes.output(pyneat.OutputOptions.every_frame(1)))
    run_opts = pyneat.RunOptions()
    run_opts.preset = pyneat.RunPreset.Realtime
    run_opts.queue_depth = 3
    run_opts.overflow_policy = pyneat.OverflowPolicy.KeepLatest
    run_opts.output_memory = pyneat.OutputMemory.Owned
    return graph, graph.build(run_opts)


def make_model_options(cfg: Config, width: int, height: int):
    opt = pyneat.ModelOptions()
    opt.preprocess.kind = pyneat.InputKind.Image
    opt.preprocess.enable = pyneat.AutoFlag.On
    opt.preprocess.input_max_width = width
    opt.preprocess.input_max_height = height
    opt.preprocess.input_max_depth = 3
    opt.preprocess.resize.enable = pyneat.AutoFlag.On
    opt.preprocess.resize.width = cfg.model_width
    opt.preprocess.resize.height = cfg.model_height
    opt.preprocess.color_convert.input_format = pyneat.PreprocessColorFormat.NV12
    opt.preprocess.color_convert.output_format = pyneat.PreprocessColorFormat.RGB
    opt.preprocess.resize.mode = pyneat.ResizeMode.Letterbox
    opt.preprocess.resize.pad_value = 114
    opt.preprocess.preset = pyneat.NormalizePreset.COCO_YOLO
    opt.decode_type = pyneat.BoxDecodeType.YoloV26
    opt.score_threshold = cfg.score_threshold
    opt.nms_iou_threshold = cfg.nms_iou
    opt.top_k = cfg.top_k
    opt.num_classes = cfg.num_classes
    if cfg.print_backend:
        opt.verbose = pyneat.VerboseOptions.debug_plugins()
    return opt


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
    input_opt.use_simaai_pool = False
    return input_opt


def build_model_run(cfg: Config, width: int, height: int, fps: int):
    model = pyneat.Model(str(resolve_app_path(cfg.model_path)), make_model_options(cfg, width, height))
    graph = pyneat.Graph("model")
    graph.add(pyneat.nodes.input(make_nv12_input_options(width, height, fps)))
    graph.add(model)
    graph.add(pyneat.nodes.output("detections", pyneat.OutputOptions.every_frame(1)))
    run_opts = pyneat.RunOptions()
    run_opts.preset = pyneat.RunPreset.Reliable
    run_opts.queue_depth = cfg.model_queue_depth
    run_opts.overflow_policy = pyneat.OverflowPolicy.Block
    run_opts.output_memory = pyneat.OutputMemory.ZeroCopy
    return graph, graph.build(run_opts)


def build_video_run(cfg: Config, width: int, height: int, fps: int):
    sender_opt = pyneat.VideoSenderOptions.h264_rtp_udp_from_raw(width, height, max(1, fps))
    sender_opt.host = cfg.udp_host
    sender_opt.channel = 0
    sender_opt.video_port_base = cfg.udp_port
    sender_opt.encoder.bitrate_kbps = cfg.bitrate_kbps
    graph = pyneat.Graph("video_out")
    graph.add(pyneat.nodes.input(make_nv12_input_options(width, height, fps)))
    graph.add(pyneat.groups.video_sender(sender_opt))
    seed_nv12 = np.full((height * 3 // 2, width), 128, dtype=np.uint8)
    seed_nv12[:height, :] = 16
    return graph, graph.build([make_nv12_tensor(seed_nv12, width, height)]), sender_opt.video_port


# ---------------------------------------------------------------- tensors / boxes / drawing

def tensor_dim(tensor, name: str) -> int:
    value = getattr(tensor, name)
    return int(value() if callable(value) else value)


def tensor_nv12_from_decoded(tensor):
    if not tensor.is_nv12():
        raise RuntimeError("expected decoded NV12 frame")
    width = tensor_dim(tensor, "width")
    height = tensor_dim(tensor, "height")
    payload = np.frombuffer(tensor.copy_payload_bytes(), dtype=np.uint8)
    expected = width * height * 3 // 2
    if payload.size < expected:
        raise RuntimeError(f"NV12 payload too small: {payload.size} < {expected}")
    return np.ascontiguousarray(payload[:expected].reshape((height * 3 // 2, width))).copy(), width, height


def extract_tensors(sample) -> list:
    if sample is None or not hasattr(sample, "kind"):
        return []
    if sample.kind == pyneat.SampleKind.Tensor and sample.tensor is not None:
        return [sample.tensor]
    if sample.kind == pyneat.SampleKind.TensorSet:
        return list(sample.tensors)
    tensors = []
    for f in getattr(sample, "fields", []):
        tensors.extend(extract_tensors(f))
    return tensors


def decode_person_dets(tensors: list, width: int, height: int, top_k: int, min_score: float):
    """-> (N,5) float array [x1,y1,x2,y2,score], person class only."""
    decoded = pyneat.decode_bbox(tensors, clamp_to=(width, height), top_k=top_k)
    rows = []
    for tensor in decoded:
        arr = np.asarray(tensor.to_numpy(copy=True), dtype=np.float32).reshape((-1, 6))
        rows.append(arr[(arr[:, 5].astype(np.int32) == COCO_PERSON) & (arr[:, 4] >= min_score), :5])
    if not rows:
        return np.zeros((0, 5), dtype=np.float32)
    return np.concatenate(rows, axis=0)[:top_k]


def bgr_to_yuv(bgr) -> tuple[int, int, int]:
    b, g, r = bgr
    y = 0.257 * r + 0.504 * g + 0.098 * b + 16
    u = -0.148 * r - 0.291 * g + 0.439 * b + 128
    v = 0.439 * r - 0.368 * g - 0.071 * b + 128
    return tuple(int(max(0, min(255, round(c)))) for c in (y, u, v))


ID_PALETTE_YUV = [bgr_to_yuv(c) for c in ID_PALETTE_BGR]


def id_color(track_id: int) -> tuple[int, int, int]:
    return ID_PALETTE_YUV[(track_id - 1) % len(ID_PALETTE_YUV)]


def fill_nv12_rect(y_plane, uv_plane, x1, y1, x2, y2, y_value, u_value, v_value) -> None:
    height, width = y_plane.shape
    x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
    y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return
    y_plane[y1:y2, x1:x2] = y_value
    uv_y1, uv_y2 = y1 // 2, min(uv_plane.shape[0], (y2 + 1) // 2)
    uv_x1, uv_x2 = x1 & ~1, min(width, (x2 + 1) & ~1)
    if uv_x2 <= uv_x1 or uv_y2 <= uv_y1:
        return
    uv_plane[uv_y1:uv_y2, uv_x1:uv_x2:2] = u_value
    uv_plane[uv_y1:uv_y2, uv_x1 + 1:uv_x2:2] = v_value


def draw_box(y_plane, uv_plane, tlbr, color, thickness: int, label: str | None) -> None:
    height, width = y_plane.shape
    x1, y1 = max(0, int(tlbr[0])), max(0, int(tlbr[1]))
    x2, y2 = min(width - 1, int(tlbr[2])), min(height - 1, int(tlbr[3]))
    if x2 <= x1 or y2 <= y1:
        return
    yv, uv, vv = color
    fill_nv12_rect(y_plane, uv_plane, x1, y1, x2 + 1, y1 + thickness, yv, uv, vv)
    fill_nv12_rect(y_plane, uv_plane, x1, y2 - thickness + 1, x2 + 1, y2 + 1, yv, uv, vv)
    fill_nv12_rect(y_plane, uv_plane, x1, y1, x1 + thickness, y2 + 1, yv, uv, vv)
    fill_nv12_rect(y_plane, uv_plane, x2 - thickness + 1, y1, x2 + 1, y2 + 1, yv, uv, vv)
    if label:
        # solid tag behind the text so the ID stays readable on any background
        tag_y1 = y1 - 28 if y1 >= 28 else y1
        fill_nv12_rect(y_plane, uv_plane, x1, tag_y1, x1 + 14 * len(label) + 8, tag_y1 + 28, yv, uv, vv)
        cv2.putText(y_plane, label, (x1 + 4, tag_y1 + 21), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    16 if yv > 128 else 235, 2, cv2.LINE_AA)


def draw_frame(nv12, width: int, height: int, tracks, raw_dets, banner: str) -> None:
    y_plane = nv12[:height, :]
    uv_plane = nv12[height:height + height // 2, :]
    if raw_dets is not None:
        for d in raw_dets:
            draw_box(y_plane, uv_plane, d[:4], LOW_SCORE_COLOR, 1, None)
    for t in tracks:
        draw_box(y_plane, uv_plane, t.tlbr, id_color(t.track_id), 3, f"#{t.track_id:03d} {t.score:.2f}")
    cv2.putText(y_plane, banner, (10, height - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 235, 2, cv2.LINE_AA)


def nv12_to_bgr(nv12, width: int, height: int):
    return cv2.cvtColor(nv12[: height * 3 // 2, :], cv2.COLOR_YUV2BGR_NV12)


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


# ---------------------------------------------------------------- pipeline

class EventLog:
    """Timestamped error/stall log written immediately (survives a lost terminal tail)."""

    def __init__(self, path: Path | None) -> None:
        self._lock = threading.Lock()
        self._f = open(path, "w", encoding="utf-8", buffering=1) if path else None

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(f"[EVENT] {line}", file=sys.stderr, flush=True)
        if self._f is not None:
            with self._lock:
                self._f.write(line + "\n")

    def close(self) -> None:
        if self._f is not None:
            self._f.close()


class LatestQueue:
    """Single-slot mailbox: always holds the most recent item, dropping older ones."""

    def __init__(self) -> None:
        self._q: "queue.Queue" = queue.Queue(maxsize=1)

    def put(self, item) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass

    def get(self, timeout: float):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.processed = 0
        self.push_dropped = 0
        self.pull_dropped = 0
        self.measure_start: float | None = None
        self.measured = 0
        self.track_ms: list[float] = []
        self.track_first_seen: dict[int, int] = {}
        self.track_last_seen: dict[int, int] = {}
        self.draw_ms: list[float] = []
        self.stalls: list[float] = []
        # fps split by whether a VLM call was in flight for that frame interval
        self._last_frame_t: float | None = None
        self.busy_frames = self.idle_frames = 0
        self.busy_time = self.idle_time = 0.0

    def record_interval(self, vlm_busy: bool) -> None:
        now = time.perf_counter()
        with self._lock:
            if self.measure_start is not None and self._last_frame_t is not None:
                dt = now - self._last_frame_t
                if vlm_busy:
                    self.busy_frames += 1
                    self.busy_time += dt
                else:
                    self.idle_frames += 1
                    self.idle_time += dt
            self._last_frame_t = now

    def record_stall(self, seconds: float) -> None:
        with self._lock:
            self.stalls.append(seconds)

    def split_fps(self) -> tuple[float, float]:
        with self._lock:
            return (self.idle_frames / self.idle_time if self.idle_time else 0.0,
                    self.busy_frames / self.busy_time if self.busy_time else 0.0)

    def record(self, warmup: int) -> int:
        with self._lock:
            self.processed += 1
            if self.processed == warmup + 1:
                self.measure_start = time.perf_counter()
            if self.measure_start is not None:
                self.measured += 1
            return self.processed

    def fps(self) -> float:
        with self._lock:
            if self.measure_start is None or self.measured < 2:
                return 0.0
            return (self.measured - 1) / max(1e-6, time.perf_counter() - self.measure_start)

    def drop(self, *, push: bool) -> None:
        with self._lock:
            if push:
                self.push_dropped += 1
            else:
                self.pull_dropped += 1


MOTION_STRIDE = 4  # Y-plane subsampling for frame differencing (1280x720 -> 320x180)


def update_pixel_motion(tracks, y_small, prev_small, now: float, keep_s: float = 3.0) -> None:
    """Append each track's mean |Y(t) - Y(t-1)| inside its box (on the subsampled luma
    plane) to track.motion_samples. Catches gestures/jumps that barely move the box."""
    if prev_small is None or prev_small.shape != y_small.shape:
        return
    h, w = y_small.shape
    for t in tracks:
        x1, y1, x2, y2 = [int(v) // MOTION_STRIDE for v in t.tlbr]
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        diff = cv2.absdiff(y_small[y1:y2, x1:x2], prev_small[y1:y2, x1:x2])
        t.motion_samples.append((now, float(diff.mean())))
        cutoff = now - keep_s
        while t.motion_samples and t.motion_samples[0][0] < cutoff:
            t.motion_samples.pop(0)


def capture_loop(source_run, capture_q, stop_event, errors) -> None:
    try:
        while not stop_event.is_set():
            tensors = source_run.pull_tensors(timeout_ms=1000)
            if not tensors:
                continue
            try:
                capture_q.put(tensor_nv12_from_decoded(tensors[0]))
            except RuntimeError:
                continue
    except Exception as exc:
        errors.append(f"capture: {exc}")
        stop_event.set()


def pusher_loop(model_run, capture_q, pending_q, stats, stop_event, errors, events) -> None:
    """Stage 2: submit the newest captured frame to the model WITHOUT blocking.

    Must be try_push(), not push(): Run.push() waits up to a hard-coded 5 s
    (GraphRuntimeOptions.push_timeout_ms, not exposed via RunOptions) and on timeout
    calls graph_request_stop() -- it stops the whole model Run for good
    (core/src/pipeline/runtime/RunCore.cpp, request_stop_on_backpressure=true). On-device
    the YOLO pipeline stalls 5-6 s now and then while the VLM is generating, and that
    killed the app twice (681 frames; 18,810 frames / 24.5 min). try_push() has timeout 0
    and no stop: a full queue just means "drop this frame", and capture_q already holds
    only the newest frame. The model queue itself stays OverflowPolicy.Block (see
    CLAUDE.md: making *that* queue drop crashed an earlier app).
    """
    stalled_since = None
    announced = False
    try:
        while not stop_event.is_set():
            item = capture_q.get(timeout=0.5)
            if item is None:
                continue
            nv12, w, h = item
            if model_run.try_push([make_nv12_tensor(nv12, w, h)]):
                pending_q.put(item)
                if stalled_since is not None:
                    dur = time.monotonic() - stalled_since
                    stats.record_stall(dur)
                    if announced:
                        events.log(f"model input recovered after {dur:.1f}s")
                    stalled_since, announced = None, False
            else:
                # brief "queue full" blips are normal (the MLA is simply busy); only
                # stalls >= 1 s are logged as events
                stats.drop(push=True)
                now = time.monotonic()
                if stalled_since is None:
                    stalled_since = now
                elif not announced and now - stalled_since >= 1.0:
                    announced = True
                    events.log("model input queue full for >=1s (stall)")
                time.sleep(0.01)
    except Exception as exc:
        errors.append(f"push: {exc}")
        events.log(f"push error (fatal): {exc}")
        stop_event.set()


def puller_loop(cfg: Config, model_run, pending_q, output_q, tracker, vlm, hud, stats: Stats,
                stop_event, errors, track_log, events, hdmi_q=None) -> None:
    """Stage 3: pull detections -> ByteTrack -> VLM trigger -> HUD draw -> sender.

    Pull timeouts are expected while the model stalls during VLM generation (5-6 s seen
    on-device), so they are not counted as failures; the app only gives up when no
    detections at all have arrived for cfg.model_stall_timeout_s.
    """
    last_ok = time.monotonic()
    prev_small = None
    while not stop_event.is_set():
        try:
            sample = model_run.pull("detections", 2000)
            if sample is None:
                continue
            try:
                nv12, w, h = pending_q.get(timeout=2.0)
            except queue.Empty:
                stats.drop(push=False)
                continue

            dets = decode_person_dets(extract_tensors(sample), w, h, cfg.top_k, cfg.track_low_thresh)
            now = time.monotonic()
            t0 = time.perf_counter()
            tracks = tracker.update(dets, now)
            y_small = nv12[:h:MOTION_STRIDE, ::MOTION_STRIDE].copy()
            update_pixel_motion(tracks, y_small, prev_small, now)
            prev_small = y_small
            track_ms = (time.perf_counter() - t0) * 1000.0

            vlm_busy = vlm is not None and vlm.busy.is_set()
            processed = stats.record(cfg.warmup_frames)
            stats.record_interval(vlm_busy)
            with stats._lock:
                stats.track_ms.append(track_ms)
                for t in tracks:
                    stats.track_first_seen.setdefault(t.track_id, processed)
                    stats.track_last_seen[t.track_id] = processed

            if track_log is not None:
                track_log.write(json.dumps({
                    "frame": processed, "t": round(now, 3), "n_dets": int(len(dets)),
                    "vlm_busy": vlm_busy,
                    "tracks": [{"id": t.track_id, "score": round(t.score, 3), "hits": t.hits,
                                "pm": round(t.motion_samples[-1][1], 2) if t.motion_samples else None,
                                "tlbr": [round(float(v), 1) for v in t.tlbr]} for t in tracks],
                }) + "\n")

            fps = stats.fps()
            t1 = time.perf_counter()
            bgr = None
            if vlm is not None and tracks:
                def frame_bgr():
                    nonlocal bgr
                    if bgr is None:
                        bgr = nv12_to_bgr(nv12, w, h)
                    return bgr
                vlm.maybe_submit(frame_bgr, tracks, w, h)
            if hud is not None:
                if bgr is None:  # crops are copied in maybe_submit, so drawing in place is safe
                    bgr = nv12_to_bgr(nv12, w, h)
                qsize = vlm.queue.qsize() if vlm is not None else 0
                status = (f"FPS {fps:4.1f}  PERSONS {len(tracks)}  IDS {tracker.total_ids}  "
                          f"VLM {'BUSY' if vlm_busy else 'IDLE'} Q{qsize}")
                hud.draw(bgr, tracks, vlm, now, status)
                out = bgr_to_nv12(bgr)
                if hdmi_q is not None:
                    hdmi_q.put(bgr)   # not modified after this point
            else:
                banner = (f"PERSONS:{len(tracks)}  IDS:{tracker.total_ids}  "
                          f"FPS:{fps:.1f}  TRACK:{track_ms:.1f}ms")
                draw_frame(nv12, w, h, tracks, dets if cfg.draw_raw_detections else None, banner)
                out = nv12
                if hdmi_q is not None:
                    hdmi_q.put(nv12_to_bgr(nv12, w, h))
            draw_ms = (time.perf_counter() - t1) * 1000.0
            with stats._lock:
                stats.draw_ms.append(draw_ms)
            output_q.put((out, w, h))

            last_ok = time.monotonic()
            if processed % 60 == 0:
                ids = " ".join(f"#{t.track_id}" for t in tracks)
                idle_fps, busy_fps = stats.split_fps()
                print(f"frame={processed} dets={len(dets)} tracks={len(tracks)} [{ids}] "
                      f"total_ids={tracker.total_ids} track_ms={track_ms:.2f} draw_ms={draw_ms:.1f} "
                      f"fps={fps:.1f} (vlm idle {idle_fps:.1f} / busy {busy_fps:.1f})",
                      flush=True)
        except Exception as exc:
            silent = time.monotonic() - last_ok
            msg = f"pull: {exc} (no output for {silent:.1f}s)"
            events.log(msg)
            if silent >= cfg.model_stall_timeout_s:
                errors.append(msg)
                print(f"[ERR] model produced no output for {silent:.0f}s, stopping", file=sys.stderr, flush=True)
                stop_event.set()
                break


def sender_loop(video_run, output_q, stop_event, errors) -> None:
    try:
        while not stop_event.is_set():
            item = output_q.get(timeout=0.5)
            if item is None:
                continue
            nv12, w, h = item
            try:
                video_run.push([make_nv12_tensor(nv12, w, h)])
            except RuntimeError:
                pass
    except Exception as exc:
        errors.append(f"send: {exc}")
        stop_event.set()


def summarize_ms(values: list[float]) -> str:
    s = sorted(values)
    return (f"mean={sum(s) / len(s):.2f}ms p95={s[max(0, int(len(s) * 0.95) - 1)]:.2f}ms "
            f"max={s[-1]:.2f}ms")


def hdmi_loop(hdmi, hdmi_q, stop_event, events, max_fps: float = 0.0) -> None:
    """Stage 4b: HUD frames -> DevKit HDMI (own thread: resize+push costs ~9 ms/frame).
    An HDMI failure only disables HDMI; the app keeps running with UDP output.
    max_fps caps the PCIe traffic to the SM768 (see TRACKING.md: board resets with
    VLM + YOLO + full-rate 1080p HDMI)."""
    min_dt = 1.0 / max_fps if max_fps > 0 else 0.0
    last = 0.0
    while not stop_event.is_set():
        bgr = hdmi_q.get(timeout=0.5)
        if bgr is None:
            continue
        now = time.monotonic()
        if now - last < min_dt:
            continue
        last = now
        try:
            hdmi.push(bgr)
        except Exception as exc:  # noqa: BLE001
            hdmi.error = str(exc)
        if hdmi.error:
            events.log(f"HDMI output disabled: {hdmi.error}")
            return


def print_summary(stats: Stats, tracker, vlm, elapsed: float) -> None:
    with stats._lock:
        processed, track_ms, draw_ms = stats.processed, list(stats.track_ms), list(stats.draw_ms)
        first, last = dict(stats.track_first_seen), dict(stats.track_last_seen)
    idle_fps, busy_fps = stats.split_fps()
    print("\n=== summary ===")
    print(f"frames={processed} dropped={stats.push_dropped + stats.pull_dropped} "
          f"(push={stats.push_dropped} pull={stats.pull_dropped}) elapsed={elapsed:.1f}s "
          f"fps(after warmup)={stats.fps():.1f}")
    print(f"fps while VLM idle={idle_fps:.1f} ({stats.idle_frames} frames) / "
          f"VLM busy={busy_fps:.1f} ({stats.busy_frames} frames)")
    if stats.stalls:
        long_stalls = [x for x in stats.stalls if x >= 1.0]
        print(f"model input stalls: {len(stats.stalls)} (>=1s: {len(long_stalls)}), "
              f"longest={max(stats.stalls):.1f}s")
    if track_ms:
        print(f"tracker[{tracker_mod.ASSIGNMENT_BACKEND}]: {summarize_ms(track_ms)}")
    if draw_ms:
        print(f"trigger+draw: {summarize_ms(draw_ms)}")
    if vlm is not None and vlm.call_seconds:
        with vlm.lock:
            done = [a for a in vlm.analyses.values() if a.state == vlm_mod.DONE]
            failed = [a for a in vlm.analyses.values() if a.state == vlm_mod.FAILED]
        print(f"VLM calls={vlm.calls} done={len(done)} failed={len(failed)} "
              f"latency: mean={sum(vlm.call_seconds) / len(vlm.call_seconds):.1f}s "
              f"max={max(vlm.call_seconds):.1f}s")
        for a in sorted(done, key=lambda a: a.track_id):
            print(f"  #{a.track_id:03d} power={a.power:,} {a.rank_ja} 「{a.result.get('title', '')}」")
    print(f"confirmed track IDs={tracker.total_ids}")
    for tid in sorted(first):
        print(f"  #{tid:03d}: frames {first[tid]}..{last[tid]} (span {last[tid] - first[tid] + 1})")


def run(cfg: Config) -> int:
    load_runtime_dependencies()
    os.environ.setdefault("SIMA_ALLOW_INPUTSTREAM_CPU_TO_EV74_COPY", "1")
    validate_config(cfg)
    width, height, fps = cfg.capture_width, cfg.capture_height, cfg.fallback_fps

    # YOLO model graph FIRST, then the VLM: vlm_ngen_demo found that loading the VLM
    # before any Neat Model/Graph existed reproducibly fails ("Failed to allocate buffer
    # (embeddings)"). The VLM is fully loaded before capture starts, so the first person
    # seen never waits on the multi-minute load.
    model_graph, model_run = build_model_run(cfg, width, height, fps)

    vlm = None
    if cfg.vlm_enabled:
        results_path = None
        if cfg.vlm_results_log:
            results_path = resolve_app_path(cfg.vlm_results_log)
            results_path.write_text("", encoding="utf-8")
            print(f"VLM results log -> {results_path}")
        vlm = vlm_mod.ScouterVlm(cfg, dry_run=cfg.vlm_dry_run, results_log_path=results_path)
        if cfg.vlm_dry_run:
            print(f"VLM: dry-run (fake answers after {cfg.vlm_dry_run_delay_s:.1f}s)")
        else:
            print(f"VLM: loading {cfg.vlm_model_dir} ...", flush=True)
            t0 = time.perf_counter()
            vlm.load()
            print(f"VLM: ready ({time.perf_counter() - t0:.1f}s)", flush=True)
        vlm.start()
    else:
        print("VLM: disabled")
    hud = hud_mod.ScouterHud(cfg) if cfg.hud_enabled else None
    if hud is not None and hud.text.font_path is None:
        print("[WARN] no CJK font found: HUD falls back to ASCII text", file=sys.stderr)

    source_graph, source_run = build_source_run(cfg)
    video_graph, video_run, video_port = build_video_run(cfg, width, height, fps)
    if cfg.print_backend:
        print(f"Source backend:\n{source_graph.describe_backend()}")
        print(f"Model backend:\n{model_graph.describe_backend()}")
        print(f"Video backend:\n{video_graph.describe_backend()}")

    tracker = tracker_mod.ByteTracker(
        track_high_thresh=cfg.track_high_thresh, track_low_thresh=cfg.track_low_thresh,
        new_track_thresh=cfg.new_track_thresh, match_thresh=cfg.match_thresh,
        lost_timeout_s=cfg.lost_timeout_s)

    track_log = None
    if cfg.track_log:
        log_path = resolve_app_path(cfg.track_log)
        track_log = open(log_path, "w", encoding="utf-8", buffering=1)
        print(f"Track log -> {log_path}")

    print(f"Model: {resolve_app_path(cfg.model_path)} (YOLO26, person) "
          f"tracker=ByteTrack[{tracker_mod.ASSIGNMENT_BACKEND}]")
    print(f"Camera {cfg.camera_device} ({width}x{height}@{fps}) -> udp://{cfg.udp_host}:{video_port}")
    print(f"  Viewer: gst-launch-1.0 -v udpsrc port={video_port} "
          f"caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96\" "
          f"! rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! autovideosink sync=false")

    stop_event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop_event.set())
    errors: list[str] = []
    capture_q, output_q = LatestQueue(), LatestQueue()
    pending_q: "queue.Queue" = queue.Queue()
    stats = Stats()
    events = EventLog(resolve_app_path(cfg.event_log) if cfg.event_log else None)
    hdmi, hdmi_q = None, None
    mon = None
    if cfg.sysmon_log:
        import sysmon
        mon = sysmon.SysMon(resolve_app_path(cfg.sysmon_log), extra=lambda: {
            "frames": stats.processed, "fps": round(stats.fps(), 1),
            "vlm_busy": bool(vlm is not None and vlm.busy.is_set()),
            "hdmi_frames": hdmi.frames if hdmi is not None else None}).start()
    start = time.perf_counter()

    if cfg.hdmi_enabled:
        try:
            hdmi = hdmi_mod.HdmiOutput(driver_name=cfg.hdmi_driver, width=cfg.hdmi_width,
                                       height=cfg.hdmi_height)
            print("HDMI: stopping lightdm (desktop) to take over the DevKit HDMI output...", flush=True)
            dw, dh = hdmi.start()
            hdmi_q = LatestQueue()
            print(f"HDMI: {dw}x{dh} via kmssink ({cfg.hdmi_driver}); lightdm is restarted on exit", flush=True)
        except Exception as exc:  # noqa: BLE001 - HDMI is optional, UDP keeps working
            print(f"[WARN] HDMI output unavailable ({exc}); continuing with UDP only", file=sys.stderr)
            if hdmi is not None:
                hdmi.close()
            hdmi = None

    threads = [
        threading.Thread(target=capture_loop, args=(source_run, capture_q, stop_event, errors),
                         name="capture", daemon=True),
        threading.Thread(target=pusher_loop,
                         args=(model_run, capture_q, pending_q, stats, stop_event, errors, events),
                         name="model-push", daemon=True),
        threading.Thread(target=puller_loop,
                         args=(cfg, model_run, pending_q, output_q, tracker, vlm, hud, stats,
                               stop_event, errors, track_log, events, hdmi_q),
                         name="model-pull", daemon=True),
        threading.Thread(target=sender_loop, args=(video_run, output_q, stop_event, errors),
                         name="video-send", daemon=True),
    ]
    if hdmi is not None:
        threads.append(threading.Thread(target=hdmi_loop, args=(hdmi, hdmi_q, stop_event, events,
                                                                cfg.hdmi_max_fps),
                                        name="hdmi-send", daemon=True))
    try:
        for t in threads:
            t.start()
        while not stop_event.is_set() and (cfg.frames <= 0 or stats.processed < cfg.frames):
            time.sleep(0.2)
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=3.0)
        if mon is not None:
            mon.close()
        if hdmi is not None:  # first, so the desktop (lightdm) comes back even if cleanup fails
            print(f"HDMI: {hdmi.frames} frames shown; restarting lightdm", flush=True)
            hdmi.close()
        print_summary(stats, tracker, vlm, time.perf_counter() - start)
        for err in errors:
            print(f"[ERR] {err}", file=sys.stderr, flush=True)
        if track_log is not None:
            track_log.close()
        events.close()
        if vlm is not None:
            vlm.close()
        model_run.close()
        source_run.close()
        video_run.close()
    return stats.processed


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)
    try:
        run(cfg)
    except Exception as exc:  # noqa: BLE001 - top-level: report and exit non-zero
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
