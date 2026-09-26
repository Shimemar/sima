#!/usr/bin/env python3
"""
Step 2-4b: YOLO11-seg のヘッド切断 (Modalix向け)
  output0 (bboxデコード済み + sigmoid済みスコア + マスク係数 を1本に連結) は
  値域がバラバラで INT8 量子化に不利なので、デコード直前の Conv 出力で切る。

  切断後の出力 (各スケール 80x80 / 40x40 / 20x20):
    box : 1 x 64 x S x S   DFL分布ロジット (4辺 x reg_max16)
    cls : 1 x 80 x S x S   クラスロジット (sigmoid前)
    mc  : 1 x 32 x S x S   マスク係数
  + proto: 1 x 32 x 160 x 160
  → 合計10出力。DFLデコード / sigmoid / NMS / マスク復元は CPU 側。

  さらに numpy で CPU デコードを実装し、元の output0 と一致するかを検証する。

実行例:
  python yolo_cut_head.py --onnx export/yolo11n-seg.onnx

出力:
  export/yolo11n-seg_cut.onnx
  export/yolo11n-seg_cut.json   出力名とスケールの対応表 (後段パイプラインで使用)
"""
import argparse
import glob
import json
import os

import cv2
import numpy as np
import onnx
import onnxruntime as ort
from onnx import shape_inference


# ---------------------------------------------------------------------------
# ヘッド出力の自動検出
# ---------------------------------------------------------------------------
def tensor_shapes(model):
    m = shape_inference.infer_shapes(model)
    shapes = {}
    for vi in list(m.graph.value_info) + list(m.graph.output) + list(m.graph.input):
        dims = [d.dim_value if d.HasField("dim_value") else None
                for d in vi.type.tensor_type.shape.dim]
        shapes[vi.name] = dims
    return shapes


def find_heads(model, nc, nm, reg_max):
    """活性化を持たず Concat/Reshape に直結する最終 Conv = 各ヘッドの出力"""
    shapes = tensor_shapes(model)
    consumers = {}
    for n in model.graph.node:
        for i in n.input:
            consumers.setdefault(i, []).append(n.op_type)
    kinds = {4 * reg_max: "box", nc: "cls", nm: "mc"}
    heads = []
    for n in model.graph.node:
        if n.op_type != "Conv":
            continue
        out = n.output[0]
        s = shapes.get(out)
        if not s or len(s) != 4 or None in s:
            continue
        cons = consumers.get(out, [])
        if not cons or any(op not in ("Concat", "Reshape") for op in cons):
            continue
        if s[1] in kinds:
            heads.append(dict(name=out, kind=kinds[s[1]], c=s[1], h=s[2], w=s[3]))
    return heads


def build_meta(heads, proto_name, imgsz, nc, nm, reg_max):
    by_hw = {}
    for h in heads:
        by_hw.setdefault((h["h"], h["w"]), {})[h["kind"]] = h["name"]
    levels = []
    for (h, w) in sorted(by_hw, key=lambda k: -k[0]):  # 80 -> 40 -> 20
        d = by_hw[(h, w)]
        if set(d) != {"box", "cls", "mc"}:
            raise SystemExit(f"スケール {h}x{w} のヘッドが揃っていません: {d}")
        levels.append(dict(h=h, w=w, stride=imgsz // h, **d))
    return dict(imgsz=imgsz, nc=nc, nm=nm, reg_max=reg_max, levels=levels, proto=proto_name)


# ---------------------------------------------------------------------------
# CPU側デコード (Modalixの後処理でもそのまま使う想定)
# ---------------------------------------------------------------------------
def dfl(box, reg_max):
    """(4*reg_max, N) -> (4, N) 各辺の距離 (グリッド単位)"""
    b = box.reshape(4, reg_max, -1)
    b = np.exp(b - b.max(1, keepdims=True))
    b /= b.sum(1, keepdims=True)
    return (b * np.arange(reg_max, dtype=np.float32)[None, :, None]).sum(1)


def decode(outs, meta):
    """ヘッド出力 dict -> (4+nc+nm, N)  元の output0 と同じ並び (xywh, score, mc)"""
    reg_max = meta["reg_max"]
    boxes, clss, mcs = [], [], []
    for lv in meta["levels"]:
        h, w, s = lv["h"], lv["w"], lv["stride"]
        box = outs[lv["box"]][0].reshape(4 * reg_max, h * w)
        cls = outs[lv["cls"]][0].reshape(-1, h * w)
        mc = outs[lv["mc"]][0].reshape(-1, h * w)
        ys, xs = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
        ax, ay = xs.reshape(-1) + 0.5, ys.reshape(-1) + 0.5
        d = dfl(box, reg_max)
        x1, y1, x2, y2 = ax - d[0], ay - d[1], ax + d[2], ay + d[3]
        boxes.append(np.stack([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1]) * s)
        clss.append(1.0 / (1.0 + np.exp(-cls)))
        mcs.append(mc)
    return np.concatenate([np.concatenate(boxes, 1),
                           np.concatenate(clss, 1),
                           np.concatenate(mcs, 1)], 0)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", default="export/yolo11n-seg.onnx")
    ap.add_argument("--out", default=None)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--nc", type=int, default=80)
    ap.add_argument("--nm", type=int, default=32)
    ap.add_argument("--reg-max", type=int, default=16)
    ap.add_argument("--calib", default="export/calib_yolo")
    ap.add_argument("--n-verify", type=int, default=10)
    args = ap.parse_args()

    out_path = args.out or os.path.splitext(args.onnx)[0] + "_cut.onnx"
    meta_path = os.path.splitext(out_path)[0] + ".json"

    model = onnx.load(args.onnx)
    in_name = model.graph.input[0].name
    orig_out0 = model.graph.output[0].name
    proto_name = model.graph.output[1].name

    heads = find_heads(model, args.nc, args.nm, args.reg_max)
    print(f"[heads] {len(heads)} 個検出")
    for h in sorted(heads, key=lambda x: (-x["h"], x["kind"])):
        print(f"  {h['kind']:4s} {h['c']:3d}x{h['h']}x{h['w']}  <- {h['name']}")
    if len(heads) != 9:
        raise SystemExit("ヘッドが9個(3種x3スケール)見つかりません。上の一覧を共有してください")

    meta = build_meta(heads, proto_name, args.imgsz, args.nc, args.nm, args.reg_max)
    outputs = [lv[k] for lv in meta["levels"] for k in ("box", "cls", "mc")] + [proto_name]

    onnx.utils.extract_model(args.onnx, out_path, [in_name], outputs)
    onnx.checker.check_model(onnx.load(out_path))
    meta["input"] = in_name
    meta["outputs"] = outputs
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=1)
    ops = sorted({n.op_type for n in onnx.load(out_path).graph.node})
    print(f"\n[cut] -> {out_path}  ({os.path.getsize(out_path) / 1e6:.1f} MB)")
    print(f"[cut] meta -> {meta_path}")
    print(f"  ops: {', '.join(ops)}")

    # ---- 検証: 切断モデル + CPUデコード vs 元モデル output0 ----
    paths = sorted(glob.glob(os.path.join(args.calib, "*.jpg")))[:args.n_verify]
    if not paths:
        print(f"[verify] {args.calib} に画像がないためスキップ")
        return
    so = ort.SessionOptions()
    so.log_severity_level = 3
    s_orig = ort.InferenceSession(args.onnx, so, providers=["CPUExecutionProvider"])
    s_cut = ort.InferenceSession(out_path, so, providers=["CPUExecutionProvider"])

    nc = args.nc
    err = dict(box=0.0, score=0.0, mc=0.0, proto=0.0)
    for p in paths:
        img = cv2.imread(p)
        if img.shape[:2] != (args.imgsz, args.imgsz):
            img = cv2.resize(img, (args.imgsz, args.imgsz))
        x = (img[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None].copy()

        ref0, ref_proto = s_orig.run([orig_out0, proto_name], {in_name: x})
        outs = dict(zip(outputs, s_cut.run(outputs, {in_name: x})))
        dec = decode(outs, meta)
        ref = ref0[0]

        err["box"] = max(err["box"], float(np.abs(dec[:4] - ref[:4]).max()))
        err["score"] = max(err["score"], float(np.abs(dec[4:4 + nc] - ref[4:4 + nc]).max()))
        err["mc"] = max(err["mc"], float(np.abs(dec[4 + nc:] - ref[4 + nc:]).max()))
        err["proto"] = max(err["proto"], float(np.abs(outs[proto_name] - ref_proto).max()))

    print(f"\n[verify] {len(paths)} 枚で 元output0 と CPUデコード結果を比較 (最大誤差)")
    print(f"  box(px) {err['box']:.2e}  score {err['score']:.2e}  "
          f"mc {err['mc']:.2e}  proto {err['proto']:.2e}")
    ok = err["box"] < 1e-2 and err["score"] < 1e-4 and err["mc"] < 1e-4
    print("  -> 一致" if ok else "  -> 不一致あり (ログを共有してください)")


if __name__ == "__main__":
    main()
