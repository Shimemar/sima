#!/usr/bin/env python3
"""
Step 2-4: ONNXエクスポート + 検証
  1) 生徒モデル(LRASPP)を2種類エクスポート
       student_lowres.onnx : 出力 1x5x(H/8)x(W/8)  ← Modalix向け推奨(拡大はCPU側)
       student_full.onnx   : 出力 1x5xHxW          ← 比較用
     ※ 正規化(mean/std)はモデル外(前処理側)で行う前提
  2) YOLO11n-seg をエクスポート(NMS・マスク復元はモデル外)
  3) PyTorch vs ONNX Runtime の出力差、ONNX版での val mIoU を確認
  4) キャリブレーション用画像を書き出し(Paletteの量子化用)

依存:
  pip install onnx onnxruntime onnxsim   (onnxsimは任意)

実行例:
  python export_onnx.py --ckpt runs/lraspp_20260925_201325/best.pt
  python export_onnx.py --ckpt runs/lraspp_.../best.pt --opset 13 --skip-yolo
"""
import argparse
import glob
import os
import random
import shutil

import cv2
import numpy as np
import onnx
import onnxruntime as ort
import torch
import torch.nn as nn
import torch.nn.functional as F

from train_student import CLASSES, IGNORE, NC, SegDataset, build_model

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


# ---------------------------------------------------------------------------
# エクスポート用ラッパー(dict出力をやめ、テンソル1本を返す)
# ---------------------------------------------------------------------------
class StudentLowRes(nn.Module):
    """backbone + LRASPP head のみ。出力は 1/8 解像度のロジット"""
    def __init__(self, m):
        super().__init__()
        self.backbone, self.classifier = m.backbone, m.classifier

    def forward(self, x):
        return self.classifier(self.backbone(x))


class StudentFull(StudentLowRes):
    def forward(self, x):
        y = super().forward(x)
        return F.interpolate(y, size=x.shape[-2:], mode="bilinear", align_corners=False)


def simplify(path):
    try:
        import onnxsim
    except ImportError:
        print("  [simplify] onnxsim 未インストールのためスキップ")
        return
    model, ok = onnxsim.simplify(onnx.load(path))
    if ok:
        onnx.save(model, path)
        print("  [simplify] OK")
    else:
        print("  [simplify] 失敗(元のまま)")


def export(module, path, size, opset):
    H, W = size
    dummy = torch.randn(1, 3, H, W)
    kw = dict(input_names=["input"], output_names=["logits"], opset_version=opset,
              do_constant_folding=True)
    try:
        torch.onnx.export(module, dummy, path, dynamo=False, **kw)
    except TypeError:  # 古いtorchは dynamo 引数なし
        torch.onnx.export(module, dummy, path, **kw)
    onnx.checker.check_model(onnx.load(path))
    simplify(path)
    ops = sorted({n.op_type for n in onnx.load(path).graph.node})
    print(f"  -> {path}  ({os.path.getsize(path) / 1e6:.1f} MB)")
    print(f"  ops: {', '.join(ops)}")


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------
def preprocess(bgr, size):
    H, W = size
    img = cv2.resize(bgr, (W, H), interpolation=cv2.INTER_LINEAR)
    rgb = img[:, :, ::-1].astype(np.float32) / 255.0
    return ((rgb - MEAN) / STD).transpose(2, 0, 1)[None].copy()


def upsample_logits(logits, H, W):
    """CPU側の後処理を想定: (5,h,w) -> (5,H,W) bilinear"""
    return np.stack([cv2.resize(c, (W, H), interpolation=cv2.INTER_LINEAR) for c in logits])


def iou_from_conf(conf):
    tp = np.diag(conf).astype(np.float64)
    denom = conf.sum(0) + conf.sum(1) - tp
    return np.where(denom > 0, tp / np.maximum(denom, 1), np.nan)


def verify_student(torch_low, onnx_low, onnx_full, ds, size):
    H, W = size
    sess_low = ort.InferenceSession(onnx_low, providers=["CPUExecutionProvider"])
    sess_full = ort.InferenceSession(onnx_full, providers=["CPUExecutionProvider"])
    torch_low.eval()

    variants = {
        "full (モデル内bilinear)": np.zeros((NC, NC), np.int64),
        "lowres + CPU bilinear": np.zeros((NC, NC), np.int64),
        "lowres + argmax→nearest": np.zeros((NC, NC), np.int64),
    }
    max_diff = 0.0

    for i in range(len(ds)):
        img, lbl = ds.load(i)
        lbl = cv2.resize(lbl, (W, H), interpolation=cv2.INTER_NEAREST)
        x = preprocess(img, size)

        with torch.no_grad():
            ref = torch_low(torch.from_numpy(x)).numpy()[0]
        low = sess_low.run(None, {"input": x})[0][0]
        full = sess_full.run(None, {"input": x})[0][0]
        max_diff = max(max_diff, float(np.abs(ref - low).max()))

        preds = {
            "full (モデル内bilinear)": full.argmax(0),
            "lowres + CPU bilinear": upsample_logits(low, H, W).argmax(0),
            "lowres + argmax→nearest": cv2.resize(low.argmax(0).astype(np.uint8), (W, H),
                                                  interpolation=cv2.INTER_NEAREST),
        }
        m = lbl != IGNORE
        for k, p in preds.items():
            variants[k] += np.bincount(lbl[m].astype(np.int64) * NC + p[m], minlength=NC * NC
                                       ).reshape(NC, NC)

    print(f"\n[verify] PyTorch vs ONNX(lowres) 最大誤差: {max_diff:.2e}")
    print(f"[verify] 出力 shape: lowres {tuple(low.shape)}  full {tuple(full.shape)}")
    print("[verify] val mIoU (ONNX Runtime)")
    for k, conf in variants.items():
        iou = iou_from_conf(conf)
        detail = " ".join(f"{c}:{v:.2f}" for c, v in zip(CLASSES, iou))
        print(f"  {k:26s} mIoU {np.nanmean(iou):.3f}  ({detail})")


def export_yolo(weights, imgsz, opset, out_dir):
    from ultralytics import YOLO
    print(f"\n[yolo] exporting {weights} imgsz={imgsz} opset={opset}")
    path = YOLO(weights).export(format="onnx", imgsz=imgsz, opset=opset,
                                simplify=True, dynamic=False, nms=False)
    dst = os.path.join(out_dir, os.path.basename(path))
    shutil.move(path, dst)
    sess = ort.InferenceSession(dst, providers=["CPUExecutionProvider"])
    for o in sess.get_inputs():
        print(f"  input  {o.name}: {o.shape}")
    for o in sess.get_outputs():
        print(f"  output {o.name}: {o.shape}")
    ops = sorted({n.op_type for n in onnx.load(dst).graph.node})
    print(f"  -> {dst}")
    print(f"  ops: {', '.join(ops)}")


def letterbox(img, S, color=(114, 114, 114)):
    """YOLOと同じ: アスペクト比維持で縮小し、正方形にパディング"""
    h, w = img.shape[:2]
    r = S / max(h, w)
    nw, nh = int(round(w * r)), int(round(h * r))
    img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, left = (S - nh) // 2, (S - nw) // 2
    return cv2.copyMakeBorder(img, top, S - nh - top, left, S - nw - left,
                              cv2.BORDER_CONSTANT, value=color)


def dump_calib(root, size, n, out_dir, use_letterbox=False):
    """Palette量子化用: 学習画像からN枚を 入力サイズ(BGR保存, 正規化前)で保存"""
    H, W = size
    paths = sorted(glob.glob(os.path.join(root, "images", "**", "*.jpg"), recursive=True))
    random.Random(0).shuffle(paths)
    os.makedirs(out_dir, exist_ok=True)
    for i, p in enumerate(paths[:n]):
        img = cv2.imread(p)
        img = letterbox(img, W) if use_letterbox else cv2.resize(img, (W, H),
                                                                 interpolation=cv2.INTER_LINEAR)
        cv2.imwrite(os.path.join(out_dir, f"calib_{i:03d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"\n[calib] {min(n, len(paths))} images ({W}x{H}) -> {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--out", default="export")
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--yolo", default="yolo11n-seg.pt")
    ap.add_argument("--yolo-imgsz", type=int, default=640)
    ap.add_argument("--skip-yolo", action="store_true")
    ap.add_argument("--calib", type=int, default=50, help="キャリブレーション画像枚数 (0で無効)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    ckpt = torch.load(args.ckpt, map_location="cpu")
    size = tuple(ckpt["size"])
    print(f"[student] {ckpt['arch']}  size={size}  epoch={ckpt.get('epoch')}  "
          f"mIoU(学習時)={ckpt.get('miou', float('nan')):.3f}")

    model = build_model(ckpt["arch"], pretrained=False)
    model.load_state_dict(ckpt["model"])
    model.eval()

    low = StudentLowRes(model).eval()
    full = StudentFull(model).eval()
    p_low = os.path.join(args.out, "student_lowres.onnx")
    p_full = os.path.join(args.out, "student_full.onnx")
    print("\n[export] student_lowres")
    export(low, p_low, size, args.opset)
    print("[export] student_full")
    export(full, p_full, size, args.opset)

    val_ds = SegDataset(args.root, "val.txt", size, train=False)
    verify_student(low, p_low, p_full, val_ds, size)

    if not args.skip_yolo:
        export_yolo(args.yolo, args.yolo_imgsz, args.opset, args.out)

    if args.calib > 0:
        dump_calib(args.root, size, args.calib, os.path.join(args.out, "calib_student"))
        dump_calib(args.root, (args.yolo_imgsz, args.yolo_imgsz), args.calib,
                   os.path.join(args.out, "calib_yolo"), use_letterbox=True)

    print(f"\n[done] -> {args.out}/")


if __name__ == "__main__":
    main()
