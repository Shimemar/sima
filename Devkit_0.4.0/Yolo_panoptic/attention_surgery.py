#!/usr/bin/env python3
"""Step 2-5 fix: replace YOLO11n-seg's C2PSA MatMul attention with MLA-compatible Einsum.

Background (see README.md Step 3): the compiled YOLO MPK ends up split across 3 MLA
segments because model.10's C2PSA self-attention (MatMul/Softmax) can't be assigned to
MLA -- the compiler's own log says "Reshape affecting the batch axis is not supported"
for the reshapes around those MatMuls. That multi-segment MPK then fails on the real
DevKit at pyneat.Model() construction with "preprocess planner: MPK contract is
missing an MLA stage for pre route selection" (verified independent of any
ModelOptions -- see README.md Step 3 for the full diagnostic trail).

Fix (ported from demo-neat/apps/pcb-defect-detection-yolo26n/compile/
surgery_einsum_attention.py, which used this exact rewrite to get YOLO26's
structurally-identical C2PSA block down to a single MLA segment, confirmed via that
app's own --strict-one-mla compile gate): rewrite the two MatMul nodes in the
attention block as Einsum nodes with an equation that is mathematically identical for
these exact shapes, in place -- same inputs/outputs, so nothing else in the graph
needs to change. Einsum is documented as MLA-compatible in
sima-model-surgery's supported_operators.json.

Verified for THIS model's shapes (see verify_identical() below):
  MatMul  : [1,2,400,32] x [1,2,32,400] -> [1,2,400,400]   == Einsum "bhnc,bhck->bhnk"
  MatMul_1: [1,2,64,400] x [1,2,400,400] -> [1,2,64,400]   == Einsum "bhcn,bhnm->bhcm"

Usage:
  python attention_surgery.py --onnx export/yolo11n-seg.onnx --out export/yolo11n-seg_attnfix.onnx
"""
import argparse

import numpy as np
import onnx
import onnxruntime as ort
from onnx import helper

ATTN_BLOCK = "/model.10/m/m.0/attn"


def node_by_name(model, name):
    for node in model.graph.node:
        if node.name == name:
            return node
    raise KeyError(f"node not found: {name}")


def replace_node(model, old_name, new_node):
    nodes = model.graph.node
    for index, node in enumerate(nodes):
        if node.name == old_name:
            nodes.remove(node)
            nodes.insert(index, new_node)
            return
    raise KeyError(f"node not found: {old_name}")


def replace_attention_matmuls(model, prefix=ATTN_BLOCK):
    matmul0 = node_by_name(model, f"{prefix}/MatMul")
    replace_node(model, matmul0.name, helper.make_node(
        "Einsum", inputs=list(matmul0.input), outputs=list(matmul0.output),
        name=f"{prefix}/Einsum", equation="bhnc,bhck->bhnk"))

    matmul1 = node_by_name(model, f"{prefix}/MatMul_1")
    replace_node(model, matmul1.name, helper.make_node(
        "Einsum", inputs=list(matmul1.input), outputs=list(matmul1.output),
        name=f"{prefix}/Einsum_1", equation="bhcn,bhnm->bhcm"))


def verify_identical(orig_path, new_path, n=5, atol=2e-3, calib_dir="export/calib_yolo"):
    """Diff between original and rewritten ONNX on random + real inputs -- confirms
    the Einsum swap is numerically exact (up to kernel summation-order float noise),
    not just topologically valid."""
    import glob
    import os

    import cv2

    so = ort.SessionOptions()
    so.log_severity_level = 3
    s_orig = ort.InferenceSession(orig_path, so, providers=["CPUExecutionProvider"])
    s_new = ort.InferenceSession(new_path, so, providers=["CPUExecutionProvider"])
    in_name = s_orig.get_inputs()[0].name
    shape = s_orig.get_inputs()[0].shape
    out_names = [o.name for o in s_orig.get_outputs()]

    def diff_for(x):
        ref = s_orig.run(out_names, {in_name: x})
        got = s_new.run(out_names, {in_name: x})
        return max(float(np.abs(r - g).max()) for r, g in zip(ref, got))

    max_diff = 0.0
    for _ in range(n):
        x = np.random.rand(*shape).astype(np.float32)
        max_diff = max(max_diff, diff_for(x))

    paths = sorted(glob.glob(os.path.join(calib_dir, "*.jpg")))[:n]
    for p in paths:
        img = cv2.imread(p)
        x = (img[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None].copy()
        max_diff = max(max_diff, diff_for(x))

    print(f"[verify] max abs diff over {n} random + {len(paths)} real inputs, "
          f"{len(out_names)} outputs: {max_diff:.2e}")
    assert max_diff < atol, f"surgery changed model output! max diff {max_diff:.2e} >= {atol:.2e}"
    print("[verify] OK -- numerically identical (within float kernel-order noise)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", default="export/yolo11n-seg.onnx")
    ap.add_argument("--out", default="export/yolo11n-seg_attnfix.onnx")
    args = ap.parse_args()

    model = onnx.load(args.onnx)
    onnx.checker.check_model(model)

    replace_attention_matmuls(model)
    print(f"[surgery] {ATTN_BLOCK}: MatMul, MatMul_1 -> Einsum")

    onnx.checker.check_model(model)
    onnx.save(model, args.out)
    print(f"[surgery] saved: {args.out}")

    verify_identical(args.onnx, args.out)


if __name__ == "__main__":
    main()
