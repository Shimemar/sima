#!/usr/bin/env python3
"""MiDaS v2.1 small の公式ONNXを MLA 向けに書き換える（開発PC、Model Compiler venv）。

    python3 tools/mla_surgery.py assets/midas_v21_small_256.onnx assets/midas_v21_small_256_mla.onnx

1. 入力直後の Sub(mean)/Div(std) を削除 → 入力は ImageNet 正規化済み RGB（ホスト側/コンパイラの mean/std で扱う）
2. 定数 Pad → 直後の Conv の pads 属性へ畳み込む（EfficientNet系の TF-SAME 非対称パディング）
3. 末尾の Squeeze を削除 → 出力は 4D (1,1,256,256)
4. opset 10 → 13 へ変換（Resize を scales/sizes 入力形式に）
数学的に等価。ORT で元モデルとの出力差を自己検証する。
"""
import sys

import numpy as np
import onnx
from onnx import helper, numpy_helper, version_converter

src, dst = sys.argv[1], sys.argv[2]
m = onnx.load(src)
g = m.graph
consts = {n.output[0]: numpy_helper.to_array(n.attribute[0].t) for n in g.node if n.op_type == 'Constant'}
inp = g.input[0].name

# 1. Sub/Div 削除
sub = next(n for n in g.node if n.op_type == 'Sub' and n.input[0] == inp)
div = next(n for n in g.node if n.op_type == 'Div' and n.input[0] == sub.output[0])
mean, std = consts[sub.input[1]].reshape(-1), consts[div.input[1]].reshape(-1)
print('removed input normalization mean', mean, 'std', std)
for n in g.node:
    n.input[:] = [inp if x == div.output[0] else x for x in n.input]
drop = {id(sub), id(div)} | {id(n) for n in g.node if n.op_type == 'Constant' and n.output[0] in (sub.input[1], div.input[1])}

# 2. Pad → Conv.pads
users = {}
for n in g.node:
    for x in n.input:
        users.setdefault(x, []).append(n)
for pad in [n for n in g.node if n.op_type == 'Pad']:
    a = {x.name: helper.get_attribute_value(x) for x in pad.attribute}
    assert a.get('mode', b'constant') == b'constant' and a.get('value', 0.0) == 0.0
    p = a['pads']  # [n,c,h,w begin, n,c,h,w end]
    assert p[0] == p[1] == p[4] == p[5] == 0
    (conv,) = users[pad.output[0]]
    assert conv.op_type == 'Conv'
    cp = next((x for x in conv.attribute if x.name == 'pads'), None)
    old = list(cp.ints) if cp else [0, 0, 0, 0]
    new = [old[0] + p[2], old[1] + p[3], old[2] + p[6], old[3] + p[7]]
    if cp:
        conv.attribute.remove(cp)
    conv.attribute.append(helper.make_attribute('pads', new))
    conv.input[0] = pad.input[0]
    drop.add(id(pad))
    print('folded Pad', p, '-> Conv', conv.output[0], 'pads', new)

# 3. Squeeze 削除
sq = next(n for n in g.node if n.op_type == 'Squeeze')
assert sq.output[0] == g.output[0].name
drop.add(id(sq))
out_name = sq.input[0]
nodes = [n for n in g.node if id(n) not in drop]
del g.node[:]
g.node.extend(nodes)
g.output[0].CopyFrom(helper.make_tensor_value_info(out_name, onnx.TensorProto.FLOAT, [1, 1, 256, 256]))
g.output[0].name = 'depth'
for n in g.node:
    n.output[:] = ['depth' if x == out_name else x for x in n.output]
g.input[0].name = 'input'
for n in g.node:
    n.input[:] = ['input' if x == inp else x for x in n.input]
del g.value_info[:]

# 4. opset 13
m = version_converter.convert_version(m, 13)
# opset 10 の Resize は暗黙に asymmetric 座標。変換後は既定が half_pixel になり出力がずれるので明示する
for n in m.graph.node:
    if n.op_type == 'Resize':
        n.attribute.append(helper.make_attribute('coordinate_transformation_mode', 'asymmetric'))
onnx.checker.check_model(m)
onnx.save(m, dst)
print('saved', dst)

# 自己検証
import onnxruntime as ort  # noqa: E402
x = np.random.default_rng(0).random((1, 3, 256, 256), dtype=np.float32)
ref = ort.InferenceSession(src)
a = ref.run(None, {ref.get_inputs()[0].name: x})[0]
xn = ((x - mean.reshape(1, 3, 1, 1)) / std.reshape(1, 3, 1, 1)).astype(np.float32)
b = ort.InferenceSession(dst).run(None, {'input': xn})[0]
print('max |diff| =', float(np.abs(a.reshape(-1) - b.reshape(-1)).max()), ' range', float(a.min()), float(a.max()))
