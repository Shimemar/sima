#!/usr/bin/env python3
"""
コーデ辛口ジャッジ - サーバー版 (Modalix DevKit上で常駐するHTTPサーバー)

エッジ版 (fashion_judge_edge.py) はCLI単発実行(1プロセス=1画像)だったが、
サーバー版はDevKit上でYOLO26/VLMモデルをロードしたまま常駐し、HOSTPC等の
クライアントから画像を受け取ってJSON判定結果を返す。ホストPC側は
`hostpc_gui.py` から HTTP 経由で呼び出す(SSH不使用)。

検出/判定ロジック自体は fashion_judge_edge.py をそのまま import して使う
(重複させない)。このファイルはHTTPの薄いラッパー。

エンドポイント:
  GET  /health        - {"ok": true, "yolo_loaded": bool, "vlm_loaded": bool}
  POST /judge          - body: 画像バイナリ(JPEG/PNG等、Content-Typeは問わない)
                         -> {"ok": true, "bbox": {...}, "judge": {...}}
                         -> {"ok": false, "error": "..."}

使い方(DevKit上、`dk`経由):
    cd /workspace/taste_in_clothes
    dk ./fashion_judge_server.py --port 8765

起動時にYOLO26・VLMの両モデルを即座にロードする(初回リクエストを速くする
ため)。YOLO26は --warmup-width/--warmup-height の想定サイズで一度ビルドして
おくが、実際に来た画像のサイズがそれと違えば detect_person_bbox() 側が
その場で自動的に再ビルドする(「大きめの上限を設定して使い回す」方式は実機
テストで不安定だったため採用していない -- fashion_judge_edge.
preload_yolo_model() のdocstring参照)。

MLA(推論アクセラレータ)は1枚のボードに1つしかない共有リソースなので、
リクエストはグローバルロックで直列化する(同時に複数の推論を投げると
既知の不具合の原因になりうる、demo-neat側の教訓を踏襲)。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

import fashion_judge_edge as fje

_inference_lock = threading.Lock()


def _decode_image(body: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(body)).convert("RGB")
    return np.array(img)


def _run_judge(body: bytes) -> dict:
    image = _decode_image(body)
    with _inference_lock:
        bbox = fje.detect_person_bbox(image)
        cropped = fje.crop_with_margin(image, bbox)
        result = fje.judge_fashion(cropped)
    return {
        "ok": True,
        "bbox": {
            "x1": bbox.x1, "y1": bbox.y1, "x2": bbox.x2, "y2": bbox.y2,
            "confidence": bbox.confidence,
        },
        "judge": {
            "score": result.score,
            "verdict": result.verdict,
            "roasts": result.roasts,
            "compliment": result.compliment,
            "closing": result.closing,
        },
    }


class JudgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "FashionJudgeServer/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib override
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}",
              file=sys.stderr)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/health":
            self._send_json(200, {
                "ok": True,
                "yolo_loaded": fje._yolo_model is not None,
                "vlm_loaded": fje._vlm_model is not None,
            })
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if self.path != "/judge":
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "空のリクエストボディ"})
            return
        body = self.rfile.read(length)

        try:
            result = _run_judge(body)
            self._send_json(200, result)
        except Exception as exc:  # noqa: BLE001 - always report as JSON, never crash the server
            print(f"judge failed: {exc}", file=sys.stderr)
            self._send_json(500, {"ok": False, "error": str(exc)})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="コーデ辛口ジャッジ (サーバー版)")
    parser.add_argument("--host", default="0.0.0.0", help="待受アドレス(既定: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="待受ポート(既定: 8765)")
    parser.add_argument("--warmup-width", type=int, default=1280,
                         help="起動時にYOLO26モデルをウォームアップする際の想定幅"
                              "(既定1280、よくある写真サイズの目安)。実際に来た画像の"
                              "サイズが違えばdetect_person_bbox()側で自動的に"
                              "その場でビルドし直すので、これは最初のリクエストの"
                              "待ち時間を減らすためだけの値(--help参照:"
                              "「大きめの上限を設定して使い回す」方式は実機で不安定"
                              "だったため採用していない)")
    parser.add_argument("--warmup-height", type=int, default=960,
                         help="起動時のウォームアップ想定高さ(既定960)")
    parser.add_argument("--model-path", default=fje.YOLO_MODEL_PATH,
                         help="YOLO26人物検出モデルのアーカイブパス")
    parser.add_argument("--vlm-model-dir", default=fje.VLM_MODEL_DIR,
                         help="llimaデプロイ済みVLMモデルのディレクトリ")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fje.YOLO_MODEL_PATH = args.model_path
    fje.VLM_MODEL_DIR = args.vlm_model_dir

    try:
        import pyneat  # noqa: F401 - import-checked here for a clear error message
    except ImportError:
        print(
            "[ERROR] pyneat が見つからない。このスクリプトはModalix DevKit上の pyneat環境"
            "でのみ動作する(`dk ./fashion_judge_server.py --port ...` 等で実行)。",
            file=sys.stderr,
        )
        return 1

    print(f"YOLO26モデルをウォームアップ中({args.warmup_width}x{args.warmup_height}): "
          f"{fje.YOLO_MODEL_PATH}", file=sys.stderr)
    fje.preload_yolo_model(args.warmup_width, args.warmup_height)
    print("VLMモデルをロード中: " + fje.VLM_MODEL_DIR, file=sys.stderr)
    fje.preload_vlm_model()
    print("両モデルのロード完了。", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), JudgeRequestHandler)
    print(f"待受開始: http://{args.host}:{args.port} (POST /judge, GET /health)",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
