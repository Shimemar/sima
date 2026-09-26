#!/usr/bin/env python3
"""
Step 2-5 (追加): 量子化後の精度劣化を実測する。

  生徒モデル: val.txt の全画像で FP32(ONNX Runtime) と INT8量子化(afe execute, compile不要)
              の mIoU を比較。
  YOLO      : val画像をレターボックスして FP32 と INT8量子化 のデコード結果
              (box座標 / クラススコア / マスク係数) を比較。

quantize_compile.py と同じ量子化設定(calib画像・mean/std・calib_method)を使うが、
精度評価が目的なので --compile はスキップする(quantize結果のexecute()で十分)。

実行 (要 activate-model-compiler):
  python eval_quantized.py --model student
  python eval_quantized.py --model yolo
"""
import argparse
import json
import os

import cv2
import numpy as np
import onnxruntime as ort
import torch

from afe.apis.defines import (
    default_quantization, quantization_scheme, RequantizationMode,
    CalibrationMethod, gen2_target, InputName,
)
from afe.load.importers.general_importer import ImporterParams, ModelFormat
from afe.ir.tensor_type import ScalarType
from afe.apis.loaded_net import load_model
from sima_utils.data.data_generator import DataGenerator
from afe.core.utils import convert_data_generator_to_iterable

from train_student import CLASSES, IGNORE, NC, SegDataset
from export_onnx import preprocess as student_preprocess, upsample_logits, iou_from_conf, letterbox
from yolo_cut_head import decode as yolo_decode


def quantize_only(onnx_path, input_name, input_shape, calib_dir, mean, std,
                   num_calib=50, calib_method="mse", fixed_batch=False):
    """quantize_compile.py と同じ設定で量子化のみ行い、(loaded_net, quant_model) を返す"""
    importer_params = ImporterParams(
        format=ModelFormat.onnx,
        file_paths=[onnx_path],
        input_names=[input_name],
        input_shapes=[input_shape],
        input_types=[ScalarType.float32],
        layout="NCHW",
    )
    loaded_net = load_model(importer_params, target=gen2_target,
                             flexible_batch_size=not fixed_batch)

    H, W = input_shape[2], input_shape[3]
    paths = sorted(
        os.path.join(calib_dir, f) for f in os.listdir(calib_dir)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    )[:num_calib]
    imgs = []
    for p in paths:
        img = Image_open_rgb_resize(p, W, H)
        t = (torch.tensor(img).float() / 255.0 - torch.tensor(mean)) / torch.tensor(std)
        imgs.append(t.permute(2, 0, 1))
    calib_np = torch.stack(imgs).permute(0, 2, 3, 1).numpy()  # NHWC
    calib_data = convert_data_generator_to_iterable(
        DataGenerator({InputName(input_name): calib_np}))

    quant_config = (default_quantization
                    .with_activation_quantization(quantization_scheme(True, False, 8))
                    .with_weight_quantization(quantization_scheme(False, True, 8))
                    .with_requantization_mode(RequantizationMode.sima)
                    .with_calibration(CalibrationMethod.from_str(calib_method)))

    quant_model = loaded_net.quantize(
        calibration_data=calib_data,
        quantization_config=quant_config,
        model_name=os.path.splitext(os.path.basename(onnx_path))[0],
    )
    return loaded_net, quant_model


def Image_open_rgb_resize(path, w, h):
    from PIL import Image
    return np.array(Image.open(path).convert("RGB").resize((w, h), Image.Resampling.BILINEAR))


def exec_quant(quant_model, input_name, nchw_batch1):
    """nchw_batch1: (1,C,H,W) 正規化済み -> NHWC にして afe execute()"""
    nhwc = np.transpose(nchw_batch1, (0, 2, 3, 1)).astype(np.float32)
    out = quant_model.execute(inputs={InputName(input_name): nhwc}, use_jax=True)
    return list(out)


# ---------------------------------------------------------------------------
def eval_student(args):
    size = (args.h, args.w)
    mean = [0.485, 0.456, 0.406]
    std = [0.229, 0.224, 0.225]

    print("[quantize] student_lowres.onnx ...")
    _, quant_model = quantize_only(
        args.student_onnx, "input", (1, 3, size[0], size[1]),
        args.calib_student, mean, std, num_calib=args.num_calib,
        calib_method=args.calib_method)

    sess = ort.InferenceSession(args.student_onnx, providers=["CPUExecutionProvider"])
    val_ds = SegDataset(args.root, "val.txt", size, train=False)

    conf_fp32 = np.zeros((NC, NC), np.int64)
    conf_int8 = np.zeros((NC, NC), np.int64)
    H, W = size
    for i in range(len(val_ds)):
        img, lbl = val_ds.load(i)
        lbl = cv2.resize(lbl, (W, H), interpolation=cv2.INTER_NEAREST)
        x = student_preprocess(img, size)  # (1,3,H,W) NCHW, RGB normalized

        fp32_logits = sess.run(None, {"input": x})[0][0]
        int8_logits = exec_quant(quant_model, "input", x)[0][0]
        # afe execute may return NHWC; normalize to (C,h,w)
        if int8_logits.shape[-1] == NC and int8_logits.ndim == 3:
            int8_logits = np.transpose(int8_logits, (2, 0, 1))

        pred_fp32 = upsample_logits(fp32_logits, H, W).argmax(0)
        pred_int8 = upsample_logits(int8_logits, H, W).argmax(0)

        m = lbl != IGNORE
        conf_fp32 += np.bincount(lbl[m].astype(np.int64) * NC + pred_fp32[m], minlength=NC * NC).reshape(NC, NC)
        conf_int8 += np.bincount(lbl[m].astype(np.int64) * NC + pred_int8[m], minlength=NC * NC).reshape(NC, NC)

    iou_fp32 = iou_from_conf(conf_fp32)
    iou_int8 = iou_from_conf(conf_int8)
    print(f"\n[student] val images: {len(val_ds)}")
    print(f"[student] FP32(ONNX) mIoU {np.nanmean(iou_fp32):.4f}  " +
          " ".join(f"{c}:{v:.2f}" for c, v in zip(CLASSES, iou_fp32)))
    print(f"[student] INT8(量子化) mIoU {np.nanmean(iou_int8):.4f}  " +
          " ".join(f"{c}:{v:.2f}" for c, v in zip(CLASSES, iou_int8)))
    print(f"[student] mIoU degradation: {np.nanmean(iou_fp32) - np.nanmean(iou_int8):.4f}")


# ---------------------------------------------------------------------------
def match_boxes(fp32, int8, score_thr):
    """score>thr の検出を貪欲に(クラス一致 かつ IoUが最大)マッチさせて差分を集計"""
    def to_dets(arr, nc):
        boxes = arr[:4].T  # (N,4) cxcywh
        scores = arr[4:4 + nc].T  # (N,nc)
        cls = scores.argmax(1)
        conf = scores.max(1)
        keep = conf > score_thr
        return boxes[keep], cls[keep], conf[keep], keep

    nc = 80
    b0, c0, s0, k0 = to_dets(fp32, nc)
    b1, c1, s1, k1 = to_dets(int8, nc)

    def xywh_to_xyxy(b):
        cx, cy, w, h = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
        return np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1)

    def iou_mat(a, b):
        a, b = xywh_to_xyxy(a), xywh_to_xyxy(b)
        x1 = np.maximum(a[:, None, 0], b[None, :, 0])
        y1 = np.maximum(a[:, None, 1], b[None, :, 1])
        x2 = np.minimum(a[:, None, 2], b[None, :, 2])
        y2 = np.minimum(a[:, None, 3], b[None, :, 3])
        inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
        area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
        area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)

    n_fp32, n_int8 = len(b0), len(b1)
    if n_fp32 == 0 or n_int8 == 0:
        return dict(n_fp32=n_fp32, n_int8=n_int8, matched=0, box_err=None, score_err=None,
                    class_flips=None)

    ious = iou_mat(b0, b1)
    matched, box_errs, score_errs, flips = 0, [], [], 0
    used = set()
    order = np.argsort(-s0)
    for i in order:
        j = np.argmax(ious[i])
        if j in used or ious[i, j] < 0.5:
            continue
        used.add(j)
        matched += 1
        box_errs.append(np.abs(b0[i] - b1[j]).max())
        score_errs.append(abs(s0[i] - s1[j]))
        if c0[i] != c1[j]:
            flips += 1
    return dict(n_fp32=n_fp32, n_int8=n_int8, matched=matched,
                box_err=float(np.max(box_errs)) if box_errs else None,
                score_err=float(np.max(score_errs)) if score_errs else None,
                class_flips=flips)


def eval_yolo(args):
    meta = json.load(open(args.yolo_meta))
    S = meta["imgsz"]

    print("[quantize] yolo11n-seg_cut.onnx (fixed-batch, C2PSA workaround) ...")
    _, quant_model = quantize_only(
        args.yolo_onnx, meta["input"], (1, 3, S, S),
        args.calib_yolo, [0.0, 0.0, 0.0], [1.0, 1.0, 1.0],
        num_calib=args.num_calib, calib_method=args.calib_method, fixed_batch=True)

    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(args.yolo_onnx, so, providers=["CPUExecutionProvider"])
    out_names = meta["outputs"]

    paths = sorted(
        os.path.join(args.root, "images", d, f)
        for d in os.listdir(os.path.join(args.root, "images"))
        for f in os.listdir(os.path.join(args.root, "images", d))
    )
    # val.txt に載っている画像だけを使う(キャリブ画像と重複させない)
    val_rel = {l.strip() for l in open(os.path.join(args.root, "val.txt")) if l.strip()}
    paths = [p for p in paths if os.path.relpath(p, os.path.join(args.root, "images")) in val_rel]
    paths = paths[:args.num_calib]

    agg = dict(n_fp32=0, n_int8=0, matched=0, flips=0)
    box_errs, score_errs = [], []
    top1_scores_fp32, top1_scores_int8, top1_flips = [], [], 0
    for p in paths:
        img = cv2.imread(p)
        img = letterbox(img, S)
        x = (img[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None].copy()

        fp32_outs = dict(zip(out_names, sess.run(out_names, {meta["input"]: x})))
        int8_raw = exec_quant(quant_model, meta["input"], x)
        int8_outs = dict(zip(out_names, int8_raw))

        # afe may return NHWC; convert back to NCHW to match decode()'s expectations
        for name in out_names:
            a = int8_outs[name]
            if a.ndim == 4 and a.shape[1] != fp32_outs[name].shape[1] and a.shape[-1] == fp32_outs[name].shape[1]:
                int8_outs[name] = np.transpose(a, (0, 3, 1, 2))

        dec_fp32 = yolo_decode(fp32_outs, meta)
        dec_int8 = yolo_decode(int8_outs, meta)

        r = match_boxes(dec_fp32, dec_int8, args.score_thr)
        agg["n_fp32"] += r["n_fp32"]
        agg["n_int8"] += r["n_int8"]
        agg["matched"] += r["matched"]
        agg["flips"] += r["class_flips"] or 0
        if r["box_err"] is not None:
            box_errs.append(r["box_err"])
            score_errs.append(r["score_err"])

        # Hallway-only val scenes rarely contain a confident COCO "thing", so the
        # threshold-gated match above is often empty. Track the single
        # highest-scoring anchor per image (regardless of threshold) as a
        # quantization-noise signal that stays meaningful even with weak signal.
        i_fp32 = np.unravel_index(np.argmax(dec_fp32[4:4 + 80]), dec_fp32[4:4 + 80].shape)
        cls_fp32, anchor_fp32 = i_fp32
        i_int8 = np.unravel_index(np.argmax(dec_int8[4:4 + 80]), dec_int8[4:4 + 80].shape)
        cls_int8, anchor_int8 = i_int8
        top1_scores_fp32.append(dec_fp32[4 + cls_fp32, anchor_fp32])
        top1_scores_int8.append(dec_int8[4 + cls_fp32, anchor_fp32])
        if cls_fp32 != cls_int8 or anchor_fp32 != anchor_int8:
            top1_flips += 1

    print(f"\n[yolo] val images: {len(paths)}  score_thr={args.score_thr}")
    print(f"[yolo] detections FP32={agg['n_fp32']}  INT8={agg['n_int8']}  matched(IoU>0.5)={agg['matched']}")
    print(f"[yolo] class flips among matched: {agg['flips']}")
    if box_errs:
        print(f"[yolo] box max-abs-err (px): mean {np.mean(box_errs):.2f}  max {np.max(box_errs):.2f}")
        print(f"[yolo] score max-abs-err   : mean {np.mean(score_errs):.3f}  max {np.max(score_errs):.3f}")
    print(f"[yolo] top-1 anchor score (regardless of threshold): "
          f"FP32 mean {np.mean(top1_scores_fp32):.3f}  INT8(same anchor) mean {np.mean(top1_scores_int8):.3f}")
    print(f"[yolo] top-1 anchor/class changed after quantization: {top1_flips}/{len(paths)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["student", "yolo", "both"], default="both")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--student-onnx", default="export/student_lowres.onnx")
    ap.add_argument("--yolo-onnx", default="export/yolo11n-seg_cut.onnx")
    ap.add_argument("--yolo-meta", default="export/yolo11n-seg_cut.json")
    ap.add_argument("--calib-student", default="export/calib_student")
    ap.add_argument("--calib-yolo", default="export/calib_yolo")
    ap.add_argument("--num-calib", type=int, default=50)
    ap.add_argument("--calib-method", default="mse")
    ap.add_argument("--h", type=int, default=384)
    ap.add_argument("--w", type=int, default=512)
    ap.add_argument("--score-thr", type=float, default=0.25)
    args = ap.parse_args()

    if args.model in ("student", "both"):
        eval_student(args)
    if args.model in ("yolo", "both"):
        eval_yolo(args)


if __name__ == "__main__":
    main()
