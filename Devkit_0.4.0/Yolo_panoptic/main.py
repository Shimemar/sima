#!/usr/bin/env python3
"""Step 3: USB webcam -> (YOLO11n-seg things + LRASPP stuff) -> panoptic merge -> H.264 RTP/UDP.

Two independently-compiled MPK models share the one physical MLA, each pushed/pulled
sequentially per frame (see README.md Step 3 for why this is intentionally simple/
sequential rather than double-buffered -- that pipelining optimization is Step 4).

Both models were compiled WITHOUT an on-device decode_type (see Step 2-4b/2-5 in
README.md): the YOLO model's head was cut before its box/mask decode for INT8-friendly
quantization, and the student model just outputs raw per-class logits. So both model
graphs use the decomposed `model.preprocess() + model.inference() + detess_dequant()`
form (see apps/examples/segmentation/yolov8-instance-segmenter/src/python/main.py) to
get plain dequantized float tensors, instead of the whole-model `graph.add(model)` form
other sibling apps use when a decode_type IS set.

Run (see README.md for a `dk`-based smoke test):
    dk ./main.py --config ./config/default.conf --frames 300

The YOLO model's C2PSA attention block originally split its compiled MPK across 3 MLA
segments, which made pyneat.Model() fail outright on real hardware ("preprocess
planner: MPK contract is missing an MLA stage for pre route selection"). Fixed in
Step 2-5 by rewriting the attention's MatMul ops to an equivalent Einsum form
(attention_surgery.py) before head-cutting/compiling -- now a single MLA segment,
confirmed working end-to-end on the DevKit. See README.md Step 3 for the full story.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Lets Neat copy a CPU-resident appsrc buffer into EV74/SiMa memory for the CVU --
# needed because the camera frame is pushed through appsrc, not consumed zero-copy.
os.environ.setdefault("SIMA_ALLOW_INPUTSTREAM_CPU_TO_EV74_COPY", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pyneat  # noqa: E402

APP_DIR = Path(__file__).resolve().parent

COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]

# Stuff classes + BGR legend colors, kept identical to train_student.py's COLORS so PC
# preview images (val_preview/*.jpg) and the on-device overlay agree visually.
STUFF_CLASSES = ["other", "wall", "floor", "ceiling", "door"]
STUFF_COLORS = {0: (80, 80, 80), 1: (180, 130, 70), 2: (60, 160, 60),
                3: (200, 200, 120), 4: (40, 90, 220)}
THING_OFFSET = 100

_stop = False


def _handle_signal(_signum, _frame) -> None:
    global _stop
    _stop = True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class Config:
    camera_device: str = "/dev/video0"
    width: int = 1280
    height: int = 720
    fps: int = 30
    flip: str = "none"  # none | rotate-180 | horizontal-flip | vertical-flip

    yolo_model_path: str = "./export/build/yolo11n-seg_attnfix_cut/yolo11n-seg_attnfix_cut_mpk.tar.gz"
    yolo_meta_path: str = "./export/yolo11n-seg_attnfix_cut.json"
    yolo_score_threshold: float = 0.25
    yolo_nms_iou: float = 0.45
    yolo_max_det: int = 100

    student_model_path: str = "./export/build/student_lowres/student_lowres_mpk.tar.gz"
    student_width: int = 512
    student_height: int = 384
    # Re-run the stuff model every N frames instead of every frame (stuff barely moves
    # frame-to-frame). 1 = every frame; the Step 3 baseline keeps this off (=1) and
    # leaves tuning it to Step 4 ("optimization").
    student_interval: int = 1

    overlap_th: float = 0.5       # thing must occupy >= this fraction of its own mask
    stuff_min_area: int = 2000    # px; drop tiny leftover stuff regions
    mask_alpha: float = 0.55
    # Do the whole per-pixel panoptic merge (stuff argmax upsample, mask placement,
    # color blend) at frame_w/N x frame_h/N instead of full camera resolution, then
    # upscale the finished overlay once. See README.md Step 4: at 1280x720 this step
    # alone was ~340ms/frame (measured, dominating the ~550ms/frame total) purely from
    # numpy/cv2 full-frame array ops on this board's CPU -- N=4 cut it to a small
    # fraction of that with no visible quality loss at typical viewing distance.
    merge_downscale: int = 4

    udp_host: str = ""
    udp_port: int = 5206
    bitrate_kbps: int = 4000

    frames: int = 0
    queue_depth: int = 3
    profile_interval: float = 1.0
    print_backend: bool = False
    source_override: str = ""

    # Debug: save every Nth processed (panoptic overlay + raw) frame as JPG under
    # save_dir, for visual inspection off-device. 0 disables saving.
    save_every: int = 0
    save_dir: str = "./captures"


_INT_KEYS = {"width", "height", "fps", "yolo_max_det", "student_width", "student_height",
             "student_interval", "stuff_min_area", "udp_port", "bitrate_kbps", "frames",
             "queue_depth", "save_every", "merge_downscale"}
_FLOAT_KEYS = {"yolo_score_threshold", "yolo_nms_iou", "overlap_th", "mask_alpha",
               "profile_interval"}
_BOOL_KEYS = {"print_backend"}


def read_config(path: Path) -> Config:
    cfg = Config()
    if not path.exists():
        return cfg
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_no}: expected key=value")
        key, value = (part.strip() for part in line.split("=", 1))
        if not hasattr(cfg, key):
            raise ValueError(f"{path}:{line_no}: unknown config key: {key}")
        if key in _INT_KEYS:
            setattr(cfg, key, int(value))
        elif key in _FLOAT_KEYS:
            setattr(cfg, key, float(value))
        elif key in _BOOL_KEYS:
            setattr(cfg, key, value.strip().lower() in ("1", "true", "yes"))
        else:
            setattr(cfg, key, value)
    return cfg


def resolve_path(value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else APP_DIR / path)


def parse_args(argv: list[str] | None) -> Config:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=APP_DIR / "config" / "default.conf")
    ap.add_argument("--camera-device")
    ap.add_argument("--udp-host")
    ap.add_argument("--udp-port", type=int)
    ap.add_argument("--frames", type=int)
    ap.add_argument("--print-backend", action="store_true")
    ap.add_argument("--save-every", type=int, help="save every Nth processed frame as JPG for inspection")
    ap.add_argument("--save-dir")
    args = ap.parse_args(argv)

    cfg = read_config(args.config)
    if args.camera_device is not None:
        cfg.camera_device = args.camera_device
    if args.udp_host is not None:
        cfg.udp_host = args.udp_host
    if args.udp_port is not None:
        cfg.udp_port = args.udp_port
    if args.frames is not None:
        cfg.frames = args.frames
    if args.print_backend:
        cfg.print_backend = True
    if args.save_every is not None:
        cfg.save_every = args.save_every
    if args.save_dir is not None:
        cfg.save_dir = args.save_dir
    if not cfg.udp_host:
        raise ValueError("config missing: udp_host")
    return cfg


# ---------------------------------------------------------------------------
# Camera source graph (USB/v4l2, MJPEG -> NV12) -- pattern verified against
# demo-neat/apps/usb-camera-yolo26m/main.py + LEARNING.md.
# ---------------------------------------------------------------------------
def camera_fragment(cfg: Config) -> str:
    if cfg.source_override:
        return cfg.source_override
    frag = (
        f"v4l2src device={cfg.camera_device} io-mode=mmap"
        f" ! image/jpeg,width={cfg.width},height={cfg.height},framerate={cfg.fps}/1"
        f" ! queue leaky=downstream max-size-buffers=2"
        f" ! jpegparse ! jpegdec"
    )
    if cfg.flip != "none":
        frag += f" ! videoflip method={cfg.flip}"
    frag += (
        f" ! videoconvert n-threads=4"
        f" ! video/x-raw,format=NV12,width={cfg.width},height={cfg.height},framerate={cfg.fps}/1"
        f" ! queue leaky=downstream max-size-buffers=2"
    )
    return frag


def build_source_graph(cfg: Config) -> "pyneat.Graph":
    graph = pyneat.Graph("panoptic_source")
    graph.add(pyneat.nodes.custom(camera_fragment(cfg), pyneat.InputRole.Source))
    graph.add(pyneat.nodes.output("frame", pyneat.OutputOptions.latest()))
    return graph


# ---------------------------------------------------------------------------
# Model graphs.
#
# quantize_compile.py (Step 2-5) folds /255 (+ ImageNet mean/std for the student
# model) into the *quantization affine transform itself* -- its calibration data is
# generated by dividing raw images by 255 (and applying mean/std) in Python BEFORE
# handing them to loaded_net.quantize(). That means both compiled MPKs' own leading
# `quantize_0` plugin expects an already-normalized FLOAT tensor, not a raw uint8
# camera frame -- unlike a product model package (e.g. yolo26m) that expects Neat's
# own CVU preprocess pipeline (opt.preprocess.resize/color_convert/normalize) to feed
# it a raw image and does the normalize+quantize on-device.
#
# Concretely: pointing opt.preprocess at NV12 + resize + a NormalizePreset made the
# planner try to build an external CVU preprocess/quantize stage in front of an MPK
# that already has its own, and it failed hard ("MPK contract is missing an MLA
# stage for pre route selection"). The fix is to do resize + color-convert + /255 +
# mean/std normalize ourselves in Python (mirroring export_onnx.py/train_student.py
# exactly) and hand the model an already-normalized float tensor via
# InputKind.Tensor (pass-through preprocess), the same way
# apps/examples/segmentation/yolov8-instance-segmenter feeds its raw-tensor MPK.
# ---------------------------------------------------------------------------
def make_nv12_input_options(cfg: Config) -> "pyneat.InputOptions":
    opt = pyneat.InputOptions()
    opt.payload_type = pyneat.PayloadType.Image
    opt.format = pyneat.Format.NV12
    opt.width = cfg.width
    opt.height = cfg.height
    opt.depth = 1
    opt.max_width = cfg.width
    opt.max_height = cfg.height
    opt.max_depth = 1
    opt.fps_n = cfg.fps
    opt.fps_d = 1
    opt.caps_override = (
        f"video/x-raw,format=NV12,width={cfg.width},height={cfg.height},framerate={cfg.fps}/1"
    )
    opt.use_simaai_pool = False
    return opt


def make_raw_model(model_path: str, input_w: int, input_h: int) -> "pyneat.Model":
    opt = pyneat.ModelOptions()
    opt.preprocess.kind = pyneat.InputKind.Tensor
    opt.preprocess.enable = pyneat.AutoFlag.Off
    opt.preprocess.input_max_width = input_w
    opt.preprocess.input_max_height = input_h
    opt.preprocess.input_max_depth = 3
    # decode_type left Unspecified on purpose: no on-device box/mask decode for
    # either model, we want the raw dequantized tensors.
    return pyneat.Model(model_path, opt)


def build_raw_model_graph(name: str, model: "pyneat.Model") -> "pyneat.Graph":
    graph = pyneat.Graph(name)
    graph.add(pyneat.nodes.input(model.input_appsrc_options(False)))
    graph.add(model.preprocess())
    graph.add(model.inference())
    graph.add(pyneat.nodes.detess_dequant(pyneat.DetessDequantOptions(model)))
    graph.add(pyneat.nodes.output("result", pyneat.OutputOptions.every_frame(4)))
    return graph


# ---------------------------------------------------------------------------
# Model input preprocessing, done here in Python (not by Neat's CVU) -- see the
# comment above make_raw_model() for why. Mirrors export_onnx.py's letterbox()/
# preprocess() and train_student.py's to_tensor() exactly, so the same math that
# was verified there (PyTorch vs ONNX Runtime, see README.md Step 2-4) applies here.
# ---------------------------------------------------------------------------
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


def letterbox_rgb(rgb: np.ndarray, size: int, pad_value: int = 114) -> np.ndarray:
    h, w = rgb.shape[:2]
    scale = size / max(h, w)
    nh, nw = int(round(h * scale)), int(round(w * scale))
    resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, left = (size - nh) // 2, (size - nw) // 2
    return cv2.copyMakeBorder(resized, top, size - nh - top, left, size - nw - left,
                              cv2.BORDER_CONSTANT, value=(pad_value,) * 3)


def preprocess_yolo(rgb: np.ndarray, size: int) -> np.ndarray:
    """RGB uint8 frame -> (size,size,3) float32, /255 only (NormalizePreset.COCO_YOLO)."""
    img = letterbox_rgb(rgb, size)
    return (img.astype(np.float32) / 255.0)


def preprocess_student(rgb: np.ndarray, w: int, h: int) -> np.ndarray:
    """RGB uint8 frame -> (h,w,3) float32, /255 + ImageNet mean/std (stretch resize,
    matching train_student.py's SegDataset -- NOT letterboxed)."""
    img = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_LINEAR)
    f = img.astype(np.float32) / 255.0
    return (f - IMAGENET_MEAN) / IMAGENET_STD


def build_video_graph(cfg: Config):
    sender_opt = pyneat.VideoSenderOptions.h264_rtp_udp_from_raw(cfg.width, cfg.height, cfg.fps)
    sender_opt.host = cfg.udp_host
    sender_opt.channel = 0
    sender_opt.video_port_base = cfg.udp_port
    sender_opt.encoder.bitrate_kbps = cfg.bitrate_kbps

    graph = pyneat.Graph("panoptic_video")
    graph.add(pyneat.nodes.input(make_nv12_input_options(cfg)))
    graph.add(pyneat.groups.video_sender(sender_opt))
    seed_nv12 = np.full((cfg.height * 3 // 2, cfg.width), 128, dtype=np.uint8)
    seed_nv12[:cfg.height, :] = 16
    seed = make_nv12_tensor(seed_nv12, cfg.width, cfg.height)
    return graph, graph.build([seed]), sender_opt.video_port


# ---------------------------------------------------------------------------
# Tensor <-> numpy / NV12 <-> BGR plumbing (verbatim pattern from
# demo-neat/apps/single-stream-yolov8n-seg/main.py, proven across 5 sibling apps).
# ---------------------------------------------------------------------------
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


def tensor_dim(tensor, name: str) -> int:
    value = getattr(tensor, name)
    return int(value() if callable(value) else value)


def tensor_nv12_from_sample(sample):
    tensors = extract_tensors(sample)
    if not tensors:
        raise RuntimeError("camera sample has no tensors")
    tensor = tensors[0]
    width = tensor_dim(tensor, "width")
    height = tensor_dim(tensor, "height")
    payload = np.frombuffer(tensor.copy_payload_bytes(), dtype=np.uint8)
    expected = width * height * 3 // 2
    if payload.size < expected:
        raise RuntimeError(f"NV12 payload too small: {payload.size} < {expected}")
    return np.ascontiguousarray(payload[:expected].reshape((height * 3 // 2, width))).copy(), width, height


def make_nv12_tensor(nv12, width: int, height: int):
    tensor = pyneat.Tensor.from_numpy(
        np.ascontiguousarray(nv12), copy=True,
        layout=pyneat.TensorLayout.HW, memory=pyneat.TensorMemory.CPU,
    )
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


def bgr_to_nv12(frame_bgr):
    height, width = frame_bgr.shape[:2]
    i420 = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420).reshape(-1)
    y_size = width * height
    uv_size = y_size // 4
    y = i420[:y_size].reshape(height, width)
    u = i420[y_size:y_size + uv_size].reshape(height // 2, width // 2)
    v = i420[y_size + uv_size:y_size + uv_size * 2].reshape(height // 2, width // 2)
    uv = np.empty((height // 2, width), dtype=np.uint8)
    uv[:, 0::2] = u
    uv[:, 1::2] = v
    return np.ascontiguousarray(np.vstack((y, uv)))


def push_video(video_run, frame_bgr) -> None:
    nv12 = bgr_to_nv12(frame_bgr)
    tensor = make_nv12_tensor(nv12, frame_bgr.shape[1], frame_bgr.shape[0])
    if not video_run.push([tensor]):
        raise RuntimeError("video push failed")


def tensor_to_numpy_f32(tensor) -> np.ndarray:
    arr = np.asarray(tensor.to_numpy(copy=True), dtype=np.float32)
    if arr.ndim == 4:
        if arr.shape[0] != 1:
            raise ValueError("only batch=1 supported")
        arr = arr[0]
    return arr


# ---------------------------------------------------------------------------
# YOLO: DFL box decode + sigmoid + NMS + mask coefficients (mirrors the numpy
# math in yolo_cut_head.py::decode(), which is verified there against the
# original uncut ONNX output0 -- box 1.53e-04 px / score 1.78e-07 max diff).
# ---------------------------------------------------------------------------
def load_yolo_meta(cfg: Config) -> dict:
    with open(resolve_path(cfg.yolo_meta_path)) as f:
        return json.load(f)


def dfl_distances_hwc(box_hwc: np.ndarray, reg_max: int) -> np.ndarray:
    """(h,w,4*reg_max) DFL logits -> (h,w,4) grid-unit l,t,r,b distances."""
    h, w, _ = box_hwc.shape
    b = box_hwc.reshape(h, w, 4, reg_max)
    b = b - b.max(-1, keepdims=True)
    e = np.exp(b)
    e /= e.sum(-1, keepdims=True)
    return (e * np.arange(reg_max, dtype=np.float32)).sum(-1)


def decode_yolo_raw(tensors: list, meta: dict):
    """tensors in meta['outputs'] order, HWC layout (see DetessDequant.h: "natural
    HWC/CHW layout" -- confirmed HWC in practice by
    apps/examples/segmentation/yolov8-instance-segmenter's tensor_to_hwc_f32()).

    -> boxes(N,4) model-space cxcywh, scores(N,nc) PRE-sigmoid logits, coeffs(N,nm),
       proto(ph,pw,nm). Sigmoid is deferred to nms_yolo_seg, applied only to the
       handful of anchors that pass the score threshold -- computing exp() over
       all N*nc (8400*80) entries eagerly here was a measurable chunk of this
       board's per-frame budget (see README.md Step 4).
    """
    reg_max, nc, nm = meta["reg_max"], meta["nc"], meta["nm"]
    by_name = dict(zip(meta["outputs"], tensors))
    all_boxes, all_scores, all_coeffs = [], [], []
    for lv in meta["levels"]:
        h, w, stride = lv["h"], lv["w"], lv["stride"]
        box_hwc = tensor_to_numpy_f32(by_name[lv["box"]]).reshape(h, w, 4 * reg_max)
        cls_hwc = tensor_to_numpy_f32(by_name[lv["cls"]]).reshape(h, w, nc)
        mc_hwc = tensor_to_numpy_f32(by_name[lv["mc"]]).reshape(h, w, nm)

        dist = dfl_distances_hwc(box_hwc, reg_max)  # (h,w,4) l,t,r,b in grid units
        ys, xs = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        ax, ay = xs + 0.5, ys + 0.5
        l, t, r, b = dist[..., 0], dist[..., 1], dist[..., 2], dist[..., 3]
        x1, y1, x2, y2 = ax - l, ay - t, ax + r, ay + b
        cx = (x1 + x2) / 2 * stride
        cy = (y1 + y2) / 2 * stride
        bw = (x2 - x1) * stride
        bh = (y2 - y1) * stride
        all_boxes.append(np.stack([cx, cy, bw, bh], -1).reshape(-1, 4))
        all_scores.append(cls_hwc.reshape(-1, nc))  # pre-sigmoid logits
        all_coeffs.append(mc_hwc.reshape(-1, nm))

    boxes = np.concatenate(all_boxes, 0)
    scores = np.concatenate(all_scores, 0)
    coeffs = np.concatenate(all_coeffs, 0)
    proto = tensor_to_numpy_f32(by_name[meta["proto"]])  # (ph, pw, nm)
    return boxes, scores, coeffs, proto


def iou_xyxy_vec(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (box[2] - box[0]) * (box[3] - box[1])
    area_b = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return inter / np.maximum(area_a + area_b - inter, 1e-9)


def nms_yolo_seg(boxes: np.ndarray, scores: np.ndarray, coeffs: np.ndarray,
                 score_thr: float, nms_iou: float, max_det: int) -> list[dict]:
    """scores are PRE-sigmoid logits (see decode_yolo_raw). argmax/max over logits
    picks the same class/anchor as over sigmoid(logits) (sigmoid is monotonic), so
    thresholding and ranking happen in logit space and sigmoid is only ever applied
    to the anchors that pass score_thr -- not all 8400."""
    cls = scores.argmax(1)
    conf_logit = scores.max(1)
    logit_thr = np.log(score_thr / (1.0 - score_thr))
    keep_idx = np.where(conf_logit >= logit_thr)[0]
    if keep_idx.size == 0:
        return []
    order = keep_idx[np.argsort(-conf_logit[keep_idx])]

    cx, cy, w, h = boxes[order, 0], boxes[order, 1], boxes[order, 2], boxes[order, 3]
    boxes_xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    classes = cls[order]
    confs = 1.0 / (1.0 + np.exp(-conf_logit[order]))
    sel_coeffs = coeffs[order]

    suppressed = np.zeros(len(order), bool)
    dets = []
    for i in range(len(order)):
        if suppressed[i]:
            continue
        dets.append(dict(x1=float(boxes_xyxy[i, 0]), y1=float(boxes_xyxy[i, 1]),
                          x2=float(boxes_xyxy[i, 2]), y2=float(boxes_xyxy[i, 3]),
                          score=float(confs[i]), class_id=int(classes[i]),
                          coeff=sel_coeffs[i]))
        if len(dets) >= max_det:
            break
        same_cls = classes == classes[i]
        ious = iou_xyxy_vec(boxes_xyxy[i], boxes_xyxy)
        suppressed |= same_cls & (ious > nms_iou) & (np.arange(len(order)) > i)
    return dets


def letterbox_params(frame_w: int, frame_h: int, model_size: int):
    scale = model_size / max(frame_w, frame_h)
    pad_x = (model_size - frame_w * scale) / 2.0
    pad_y = (model_size - frame_h * scale) / 2.0
    return scale, pad_x, pad_y


def model_box_to_frame(det: dict, scale: float, pad_x: float, pad_y: float,
                       frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    x1 = (det["x1"] - pad_x) / scale
    y1 = (det["y1"] - pad_y) / scale
    x2 = (det["x2"] - pad_x) / scale
    y2 = (det["y2"] - pad_y) / scale
    x1 = max(0, min(frame_w - 1, int(round(x1))))
    y1 = max(0, min(frame_h - 1, int(round(y1))))
    x2 = max(x1 + 1, min(frame_w, int(round(x2))))
    y2 = max(y1 + 1, min(frame_h, int(round(y2))))
    return x1, y1, x2, y2


def instance_mask(det: dict, proto: np.ndarray, frame_rect: tuple[int, int, int, int],
                  frame_w: int, frame_h: int, model_size: int) -> np.ndarray:
    """proto (ph,pw,nm) x coeff -> full-frame boolean mask, true only inside frame_rect.

    Only the proto-space crop under frame_rect is ever tensordot'ed/resized (to the
    bbox's own pixel size), not the whole proto grid resized up to the full frame --
    at 1280x720 with several detections/frame the naive full-frame resize was a
    measurable chunk of Step 4's per-frame budget (see README.md Step 4).
    """
    ph, pw, nm = proto.shape
    full = np.zeros((frame_h, frame_w), bool)

    x1, y1, x2, y2 = frame_rect
    sx, sy = pw / model_size, ph / model_size
    # frame_rect is already in FRAME space; map through the same letterbox transform
    # used for boxes (model_box_to_frame) to reach model-space, then to proto-space.
    scale, pad_x, pad_y = letterbox_params(frame_w, frame_h, model_size)
    mx1 = max(0, int(np.floor((x1 * scale + pad_x) * sx)))
    my1 = max(0, int(np.floor((y1 * scale + pad_y) * sy)))
    mx2 = min(pw, int(np.ceil((x2 * scale + pad_x) * sx)))
    my2 = min(ph, int(np.ceil((y2 * scale + pad_y) * sy)))
    if mx2 <= mx1 or my2 <= my1:
        return full

    mask_small = np.tensordot(proto[my1:my2, mx1:mx2], det["coeff"], axes=([2], [0]))
    mask_small = 1.0 / (1.0 + np.exp(-mask_small))
    resized = cv2.resize(mask_small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
    full[y1:y2, x1:x2] = resized > 0.5
    return full


# ---------------------------------------------------------------------------
# Student (stuff): raw (h,w,5) HWC logits -> bilinear upsample -> argmax.
# ---------------------------------------------------------------------------
def decode_student(tensors: list, frame_w: int, frame_h: int) -> np.ndarray:
    logits = tensor_to_numpy_f32(tensors[0])  # (h, w, 5) HWC, see decode_yolo_raw's note
    nc = logits.shape[-1]
    up = np.stack([cv2.resize(logits[:, :, c], (frame_w, frame_h), interpolation=cv2.INTER_LINEAR)
                   for c in range(nc)])
    return up.argmax(0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Panoptic merge + draw (BGR). Same policy as panoptic_webcam.py's PC prototype:
# things placed confidence-first, stuff fills whatever is left over.
# ---------------------------------------------------------------------------
def inst_color(i: int) -> tuple[int, int, int]:
    hsv = np.uint8([[[(i * 47) % 180, 200, 255]]])
    return tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])


def put_label(img, text: str, org, scale: float = 0.5) -> None:
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = org
    cv2.rectangle(img, (x, y - th - 4), (x + tw + 4, y + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x + 2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), 1, cv2.LINE_AA)


def merge_and_draw(frame_bgr, stuff_map: np.ndarray, dets: list[dict], proto: np.ndarray,
                   model_size: int, cfg: Config) -> tuple[np.ndarray, int, list[dict]]:
    """Returns (vis, n_things, segments). vis has stuff/instance colors and mask
    contours baked in at frame_bgr's own (possibly downscaled -- see README.md
    Step 4) resolution; segments carries each label's centroid in THAT SAME
    resolution so the caller can draw crisp text after upscaling (see
    draw_annotations) instead of blowing up already-rendered text/legend boxes."""
    h, w = frame_bgr.shape[:2]
    pan_cls = np.full((h, w), -1, np.int32)
    pan_inst = np.zeros((h, w), np.int32)
    occupied = np.zeros((h, w), bool)
    segments = []

    order = sorted(range(len(dets)), key=lambda i: -dets[i]["score"])
    next_id = 1
    for i in order:
        det = dets[i]
        rect = model_box_to_frame(det, *letterbox_params(w, h, model_size), w, h)
        m = instance_mask(det, proto, rect, w, h, model_size)
        area = int(m.sum())
        if area == 0:
            continue
        free = m & ~occupied
        if free.sum() / area < cfg.overlap_th:
            continue
        pan_cls[free] = THING_OFFSET + det["class_id"]
        pan_inst[free] = next_id
        occupied |= free
        segments.append(dict(id=next_id, cls=det["class_id"], conf=det["score"],
                             area=int(free.sum()), isthing=True))
        next_id += 1

    for sid in range(1, len(STUFF_CLASSES)):  # skip 0="other": not drawn, like the PC prototype
        region = (stuff_map == sid) & ~occupied
        area = int(region.sum())
        if area >= cfg.stuff_min_area:
            pan_cls[region] = sid
            occupied |= region
            segments.append(dict(id=0, cls=sid, area=area, isthing=False))

    color = np.zeros_like(frame_bgr)
    for sid in range(1, len(STUFF_CLASSES)):
        color[pan_cls == sid] = STUFF_COLORS[sid]
    for s in segments:
        if s["isthing"]:
            color[pan_inst == s["id"]] = inst_color(s["id"])

    vis = frame_bgr.copy()
    valid = pan_cls >= 0
    blend = cv2.addWeighted(frame_bgr, 1 - cfg.mask_alpha, color, cfg.mask_alpha, 0)
    vis[valid] = blend[valid]

    n_things = 0
    for s in segments:
        if not s["isthing"]:
            continue
        n_things += 1
        m = (pan_inst == s["id"]).astype(np.uint8)
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(vis, contours, -1, (255, 255, 255), 1)
        ys, xs = np.nonzero(m)
        if len(xs):
            s["centroid"] = (int(xs.mean()), int(ys.mean()))

    return vis, n_things, segments


def draw_annotations(vis, segments: list[dict], scale: float, total_px: int, cfg: Config) -> None:
    """Draws instance labels + the stuff legend at vis's OWN resolution (call this
    after upscaling from merge_and_draw's downscaled canvas -- see README.md Step 4;
    drawing text before the upscale made it blow up into illegible blocky boxes).
    total_px is merge_and_draw's own (downscaled) frame area -- segment "area" values
    are pixel counts in that same resolution, so the percentages below need the
    matching denominator, not vis's (upscaled) pixel count."""
    h, w = vis.shape[:2]
    for s in segments:
        if not s["isthing"] or "centroid" not in s:
            continue
        cx, cy = s["centroid"]
        label = COCO_LABELS[s["cls"]] if s["cls"] < len(COCO_LABELS) else f"class_{s['cls']}"
        put_label(vis, f"#{s['id']} {label} {s['conf']:.2f}", (int(cx * scale), int(cy * scale)))

    y = 20
    for sid in range(1, len(STUFF_CLASSES)):
        area = sum(s["area"] for s in segments if not s["isthing"] and s["cls"] == sid)
        cv2.rectangle(vis, (w - 150, y - 12), (w - 136, y + 2), STUFF_COLORS[sid], -1)
        put_label(vis, f"{STUFF_CLASSES[sid]} {100 * area / total_px:4.1f}%", (w - 132, y + 2))
        y += 20


# ---------------------------------------------------------------------------
# Stage timing -- window-averaged (not cumulative) so a stage that degrades
# halfway through a long run shows up immediately instead of being smoothed away
# by thousands of earlier good frames. See README.md Step 4 for how this was used
# to find the actual per-frame bottleneck instead of guessing.
# ---------------------------------------------------------------------------
class StageTimer:
    STAGES = ("capture", "yolo_infer", "yolo_decode", "student_infer", "student_decode",
              "merge_draw", "encode")

    def __init__(self):
        self.sums = {s: 0.0 for s in self.STAGES}
        self.counts = {s: 0 for s in self.STAGES}

    def add(self, stage: str, seconds: float) -> None:
        self.sums[stage] += seconds
        self.counts[stage] += 1

    def report(self) -> str:
        parts = []
        for s in self.STAGES:
            n = self.counts[s]
            avg_ms = 1000.0 * self.sums[s] / n if n else 0.0
            parts.append(f"{s}={avg_ms:.1f}")
        out = " ".join(parts)
        self.sums = {s: 0.0 for s in self.STAGES}
        self.counts = {s: 0 for s in self.STAGES}
        return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def run(cfg: Config) -> int:
    yolo_meta = load_yolo_meta(cfg)
    yolo_size = yolo_meta["imgsz"]

    yolo_model = make_raw_model(resolve_path(cfg.yolo_model_path), yolo_size, yolo_size)
    student_model = make_raw_model(resolve_path(cfg.student_model_path),
                                   cfg.student_width, cfg.student_height)

    source_graph = build_source_graph(cfg)
    yolo_graph = build_raw_model_graph("panoptic_yolo", yolo_model)
    student_graph = build_raw_model_graph("panoptic_student", student_model)
    video_graph, video_run, video_port = build_video_graph(cfg)

    if cfg.print_backend:
        print("Source backend:\n" + source_graph.describe_backend())
        print("YOLO backend:\n" + yolo_graph.describe_backend())
        print("Student backend:\n" + student_graph.describe_backend())
        print("Video backend:\n" + video_graph.describe_backend())

    run_opt = pyneat.RunOptions()
    run_opt.preset = pyneat.RunPreset.Realtime
    run_opt.queue_depth = cfg.queue_depth
    # Per CLAUDE.md: the model's own push/pull queue must Block, not drop-oldest --
    # an earlier sibling app crashed the MLA when this was KeepLatest instead.
    run_opt.overflow_policy = pyneat.OverflowPolicy.Block

    source_run_opt = pyneat.RunOptions()
    source_run_opt.preset = pyneat.RunPreset.Realtime
    source_run_opt.queue_depth = cfg.queue_depth
    source_run_opt.overflow_policy = pyneat.OverflowPolicy.KeepLatest

    source_run = source_graph.build(source_run_opt)
    yolo_run = yolo_graph.build(run_opt)
    student_run = student_graph.build(run_opt)

    print(f"Camera:  {cfg.camera_device} MJPEG {cfg.width}x{cfg.height}@{cfg.fps}")
    print(f"YOLO:    {cfg.yolo_model_path} ({yolo_size}x{yolo_size})")
    print(f"Student: {cfg.student_model_path} ({cfg.student_width}x{cfg.student_height})")
    print(f"Video:   udp://{cfg.udp_host}:{video_port} H264/RTP payload=96")
    print(
        "Viewer:  gst-launch-1.0 -v udpsrc port=" + str(video_port) +
        ' caps="application/x-rtp,media=video,encoding-name=H264,payload=96" '
        "! rtpjitterbuffer ! rtph264depay ! h264parse ! avdec_h264 "
        "! videoconvert ! autovideosink sync=false"
    )
    print("Running. Press Ctrl-C to stop.")

    stuff_map = None
    processed = 0
    run_start = time.perf_counter()
    last_log = run_start
    timer = StageTimer()

    try:
        while not _stop and (cfg.frames <= 0 or processed < cfg.frames):
            t0 = time.perf_counter()
            frame_sample = source_run.pull("frame", 20000)
            if frame_sample is None:
                print("[warn] no camera frame (timeout or source closed)", file=sys.stderr)
                continue
            nv12, frame_w, frame_h = tensor_nv12_from_sample(frame_sample)
            rgb = cv2.cvtColor(nv12, cv2.COLOR_YUV2RGB_NV12)  # (frame_h, frame_w, 3) uint8
            t1 = time.perf_counter()
            timer.add("capture", t1 - t0)

            yolo_input = preprocess_yolo(rgb, yolo_size)
            if not yolo_run.push([pyneat.Tensor.from_numpy(yolo_input, copy=True,
                                                            memory=pyneat.TensorMemory.EV74)]):
                print("[warn] failed to push frame to yolo model", file=sys.stderr)
                continue
            yolo_sample = yolo_run.pull("result", 20000)
            if yolo_sample is None:
                print("[warn] no yolo result (timeout)", file=sys.stderr)
                continue
            t2 = time.perf_counter()
            timer.add("yolo_infer", t2 - t1)

            yolo_tensors = extract_tensors(yolo_sample)
            if processed == 0:
                shapes = [tuple(int(x) for x in t.shape) for t in yolo_tensors]
                print(f"[debug] yolo output shapes (verify HWC per-tensor): {shapes}")
            boxes, scores, coeffs, proto = decode_yolo_raw(yolo_tensors, yolo_meta)
            dets = nms_yolo_seg(boxes, scores, coeffs, cfg.yolo_score_threshold,
                               cfg.yolo_nms_iou, cfg.yolo_max_det)
            t3 = time.perf_counter()
            timer.add("yolo_decode", t3 - t2)

            if stuff_map is None or processed % cfg.student_interval == 0:
                student_input = preprocess_student(rgb, cfg.student_width, cfg.student_height)
                student_tensor = pyneat.Tensor.from_numpy(student_input, copy=True,
                                                          memory=pyneat.TensorMemory.EV74)
                if not student_run.push([student_tensor]):
                    print("[warn] failed to push frame to student model", file=sys.stderr)
                else:
                    student_sample = student_run.pull("result", 20000)
                    if student_sample is None:
                        print("[warn] no student result (timeout)", file=sys.stderr)
                    else:
                        t4 = time.perf_counter()
                        timer.add("student_infer", t4 - t3)
                        student_tensors = extract_tensors(student_sample)
                        if processed == 0:
                            shapes = [tuple(int(x) for x in t.shape) for t in student_tensors]
                            print(f"[debug] student output shape (verify HWC): {shapes}")
                        merge_w = max(1, frame_w // cfg.merge_downscale)
                        merge_h = max(1, frame_h // cfg.merge_downscale)
                        stuff_map = decode_student(student_tensors, merge_w, merge_h)
                        timer.add("student_decode", time.perf_counter() - t4)
            t5 = time.perf_counter()

            frame_bgr = np.ascontiguousarray(rgb[:, :, ::-1])
            if stuff_map is None:
                vis, n_things = frame_bgr, 0
            else:
                merge_w, merge_h = stuff_map.shape[1], stuff_map.shape[0]
                small_bgr = cv2.resize(frame_bgr, (merge_w, merge_h), interpolation=cv2.INTER_LINEAR)
                small_vis, n_things, segments = merge_and_draw(small_bgr, stuff_map, dets, proto,
                                                               yolo_size, cfg)
                vis = cv2.resize(small_vis, (frame_w, frame_h), interpolation=cv2.INTER_LINEAR)
                draw_annotations(vis, segments, cfg.merge_downscale, merge_w * merge_h, cfg)
            t6 = time.perf_counter()
            timer.add("merge_draw", t6 - t5)

            push_video(video_run, vis)
            timer.add("encode", time.perf_counter() - t6)

            if cfg.save_every > 0 and processed % cfg.save_every == 0:
                save_dir = Path(resolve_path(cfg.save_dir))
                save_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(save_dir / f"frame_{processed:05d}_raw.jpg"), frame_bgr)
                cv2.imwrite(str(save_dir / f"frame_{processed:05d}_panoptic.jpg"), vis)

            processed += 1
            now = time.perf_counter()
            if cfg.profile_interval > 0 and now - last_log >= cfg.profile_interval:
                fps = processed / (now - run_start) if now > run_start else 0.0
                print(f"frame={processed} fps={fps:.1f} things={n_things} dets={len(dets)} "
                      f"ms({timer.report()})", flush=True)
                last_log = now
    finally:
        yolo_run.close()
        student_run.close()
        source_run.close()
        video_run.close()

    return 130 if _stop else 0


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        return run(parse_args(argv))
    except Exception as exc:  # noqa: BLE001
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
