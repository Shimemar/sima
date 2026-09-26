#!/usr/bin/env python3
"""
Step 1: PC検証用 室内パノプティックセグメンテーション
  things : YOLO11-seg (COCO学習済み)            -> 物(インスタンス単位)
  stuff  : ADE20K学習済みセマンティックモデル     -> 壁 / 床 / 天井
  入力   : USB webcam

依存:
  pip install ultralytics transformers opencv-python numpy torch

実行例:
  python panoptic_webcam.py --camera 0
  python panoptic_webcam.py --sem-model nvidia/segformer-b0-finetuned-ade-512-512   # CPUで軽く試す場合

キー操作:
  q / ESC : 終了
  s       : 保存 (可視化PNG, pan_cls.npy, pan_inst.npy)
  v       : 表示切替 (オーバーレイ / マスクのみ)
"""
import argparse
import time

import cv2
import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# 出力クラス定義
#   stuff : 0..N-1   (下の STUFF_GROUPS)
#   thing : THING_OFFSET + COCOクラスID
#   その他 : -1
# ---------------------------------------------------------------------------
STUFF_GROUPS = {
    # 出力ID: (表示名, 対応するADE20Kラベル名, BGR色)
    0: ("wall",    ["wall"],                           (180, 130, 70)),
    1: ("floor",   ["floor", "flooring", "rug", "carpet", "carpeting"], (60, 160, 60)),
    2: ("ceiling", ["ceiling"],                        (200, 200, 120)),
}
THING_OFFSET = 100


# ---------------------------------------------------------------------------
# stuff: ADE20K セマンティックセグメンテーション
# ---------------------------------------------------------------------------
def build_ade_lut(id2label, num_labels):
    """ADE20KクラスID -> 出力stuff ID のルックアップテーブル"""
    lut = np.full(num_labels, -1, np.int16)
    for idx, name in id2label.items():
        tokens = [t.strip().lower() for t in name.replace(",", ";").split(";")]
        for sid, (_, cands, _) in STUFF_GROUPS.items():
            if any(t in cands for t in tokens):
                lut[int(idx)] = sid
    return lut


class SemanticStuff:
    def __init__(self, model_name, device):
        print(f"[sem] loading {model_name}")
        self.proc = AutoImageProcessor.from_pretrained(model_name)
        self.model = AutoModelForSemanticSegmentation.from_pretrained(model_name).to(device).eval()
        self.device = device
        cfg = self.model.config
        self.lut = build_ade_lut(cfg.id2label, cfg.num_labels)
        for sid, (n, _, _) in STUFF_GROUPS.items():
            src = [cfg.id2label[i] for i in np.where(self.lut == sid)[0]]
            print(f"[sem]   {n:8s} <- {src if src else '見つかりません(ラベル名を確認)'}")

    @torch.no_grad()
    def __call__(self, frame_bgr):
        H, W = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        inputs = self.proc(images=rgb, return_tensors="pt").to(self.device)
        logits = self.model(**inputs).logits[0]                 # (C, h, w)
        label = logits.argmax(0).to(torch.uint8).cpu().numpy()  # ADE20Kは150クラス < 256
        label = cv2.resize(label, (W, H), interpolation=cv2.INTER_NEAREST)
        return self.lut[label]                                  # (H, W) stuff ID or -1


# ---------------------------------------------------------------------------
# things: YOLO-seg
# ---------------------------------------------------------------------------
def run_yolo(model, frame, conf, imgsz, device):
    r = model.predict(frame, imgsz=imgsz, conf=conf, retina_masks=True,
                      device=device, verbose=False)[0]
    H, W = frame.shape[:2]
    if r.masks is None or len(r.boxes) == 0:
        return np.zeros((0, H, W), bool), np.zeros(0, int), np.zeros(0, float)
    masks = r.masks.data.cpu().numpy() > 0.5            # (N, H, W)
    cls = r.boxes.cls.cpu().numpy().astype(int)
    confs = r.boxes.conf.cpu().numpy()
    return masks, cls, confs


# ---------------------------------------------------------------------------
# パノプティック統合
# ---------------------------------------------------------------------------
def merge_panoptic(stuff_map, masks, cls, confs, overlap_th=0.5, stuff_min_area=2000):
    H, W = stuff_map.shape
    pan_cls = np.full((H, W), -1, np.int32)
    pan_inst = np.zeros((H, W), np.int32)
    occupied = np.zeros((H, W), bool)
    segments = []

    # 1) things: 信頼度の高い順に配置。既存領域と重なりすぎたものは捨てる
    next_id = 1
    for i in np.argsort(-confs):
        m = masks[i]
        area = int(m.sum())
        if area == 0:
            continue
        free = m & ~occupied
        free_area = int(free.sum())
        if free_area / area < overlap_th:
            continue
        pan_cls[free] = THING_OFFSET + cls[i]
        pan_inst[free] = next_id
        occupied |= free
        segments.append(dict(id=next_id, cls=int(cls[i]), conf=float(confs[i]),
                             area=free_area, isthing=True))
        next_id += 1

    # 2) stuff: 残りの画素を壁/床/天井で埋める
    for sid in STUFF_GROUPS:
        region = (stuff_map == sid) & ~occupied
        a = int(region.sum())
        if a >= stuff_min_area:
            pan_cls[region] = sid
            occupied |= region
            segments.append(dict(id=0, cls=sid, area=a, isthing=False))

    return pan_cls, pan_inst, segments


# ---------------------------------------------------------------------------
# 可視化
# ---------------------------------------------------------------------------
def inst_color(i):
    hsv = np.uint8([[[(i * 47) % 180, 200, 255]]])
    return tuple(int(v) for v in cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0])


def put_label(img, text, org, scale=0.5):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = org
    cv2.rectangle(img, (x, y - th - 4), (x + tw + 4, y + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x + 2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, scale,
                (255, 255, 255), 1, cv2.LINE_AA)


def visualize(frame, pan_cls, pan_inst, segments, names, alpha, mask_only):
    H, W = frame.shape[:2]
    color = np.zeros_like(frame)
    for sid, (_, _, c) in STUFF_GROUPS.items():
        color[pan_cls == sid] = c
    for s in segments:
        if s["isthing"]:
            color[pan_inst == s["id"]] = inst_color(s["id"])

    if mask_only:
        out = color.copy()
    else:
        out = frame.copy()
        valid = pan_cls >= 0
        blend = cv2.addWeighted(frame, 1 - alpha, color, alpha, 0)
        out[valid] = blend[valid]

    # thing: 輪郭 + ラベル
    for s in segments:
        if not s["isthing"]:
            continue
        m = (pan_inst == s["id"]).astype(np.uint8)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, cnts, -1, (255, 255, 255), 1)
        ys, xs = np.nonzero(m)
        put_label(out, f"#{s['id']} {names[s['cls']]} {s['conf']:.2f}",
                  (int(xs.mean()), int(ys.mean())))

    # stuff: 凡例 (面積%)
    y = 20
    total = H * W
    for sid, (n, _, c) in STUFF_GROUPS.items():
        a = sum(s["area"] for s in segments if not s["isthing"] and s["cls"] == sid)
        cv2.rectangle(out, (W - 150, y - 12), (W - 136, y + 2), c, -1)
        put_label(out, f"{n} {100 * a / total:4.1f}%", (W - 132, y + 2))
        y += 20
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--mjpg", action="store_true", help="webcamをMJPGで取り込む")
    ap.add_argument("--yolo", default="yolo11n-seg.pt")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--conf", type=float, default=0.4)
    ap.add_argument("--sem-model", default="openmmlab/upernet-convnext-small")
    ap.add_argument("--sem-interval", type=int, default=3,
                    help="セマンティック推論をNフレームに1回実行(床・壁はほぼ動かない)")
    ap.add_argument("--overlap", type=float, default=0.5)
    ap.add_argument("--stuff-min-area", type=int, default=2000)
    ap.add_argument("--alpha", type=float, default=0.5)
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[info] device = {device}")

    yolo = YOLO(args.yolo)
    names = yolo.names
    sem = SemanticStuff(args.sem_model, device)

    cap = cv2.VideoCapture(args.camera)
    if args.mjpg:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(f"カメラ {args.camera} を開けません")
    print(f"[info] camera {int(cap.get(3))}x{int(cap.get(4))}")

    stuff_map = None
    frame_idx, mask_only = 0, False
    fps, t_prev = 0.0, time.perf_counter()
    t_sem = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[warn] フレーム取得失敗")
            break

        t0 = time.perf_counter()
        masks, cls, confs = run_yolo(yolo, frame, args.conf, args.imgsz, device)
        t1 = time.perf_counter()
        if stuff_map is None or frame_idx % args.sem_interval == 0:
            stuff_map = sem(frame)
            t_sem = (time.perf_counter() - t1) * 1000
        t2 = time.perf_counter()
        pan_cls, pan_inst, segments = merge_panoptic(
            stuff_map, masks, cls, confs, args.overlap, args.stuff_min_area)
        t3 = time.perf_counter()

        vis = visualize(frame, pan_cls, pan_inst, segments, names, args.alpha, mask_only)

        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-6)
        t_prev = now
        n_things = sum(s["isthing"] for s in segments)
        put_label(vis, f"FPS {fps:5.1f} | yolo {(t1 - t0) * 1000:5.1f}ms "
                       f"sem {t_sem:6.1f}ms(1/{args.sem_interval}) "
                       f"merge {(t3 - t2) * 1000:4.1f}ms | things {n_things}", (8, 20))

        cv2.imshow("panoptic (q:quit s:save v:view)", vis)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("v"):
            mask_only = not mask_only
        if key == ord("s"):
            ts = time.strftime("%Y%m%d_%H%M%S")
            cv2.imwrite(f"pan_{ts}.png", vis)
            cv2.imwrite(f"raw_{ts}.png", frame)
            np.save(f"pan_cls_{ts}.npy", pan_cls)
            np.save(f"pan_inst_{ts}.npy", pan_inst)
            print(f"[save] pan_{ts}.*")
        frame_idx += 1

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
