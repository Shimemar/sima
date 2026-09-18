#!/usr/bin/env python3
"""
コーデべた褒めジャッジ - エッジ版・べた褒めバージョン (Modalix DevKit)

`fashion_judge_edge.py`(毒舌ギャル系)の派生版。判定パイプライン
(YOLO26人物検出 -> Gemma 4 VLM判定)自体は完全に同じで、`JUDGE_PROMPT`の
中身だけを「とにかくべた褒め」なテンションに差し替えている。JSON出力の
スキーマ(score/verdict/roasts/compliment/closing)もそのまま維持している
ので、`fashion_judge_server.py`等から`fashion_judge_edge`の代わりに
`import fashion_judge_edge_rave as fje`のように差し替えて使える
(フィールド名は同じだが、中身はすべて称賛コメントになる)。

入力: 全身が写った静止画ファイル
処理: YOLO26で人物検出・クロップ -> Gemma 4 (E2B, VisionLanguageModel経由) で
      べた褒めギャル系ファッション判定
出力: ターミナルにスコア・べた褒めコメントを表示

使い方:
    python fashion_judge_edge_rave.py --image path/to/photo.jpg

Modalix DevKit上でpyneatが使える環境でのみ動作する(`dk`経由で実行するか、
DevKit上のPython環境で直接実行する)。
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

# pyneat はこの開発ホストにはインストールされていない(DevKit上でのみ利用可能)ので、
# 実際に呼び出す関数の中でだけ import する。これによりホスト側でも
# `python -c "import ast; ast.parse(...)"` 等の静的チェックは通る。

# ============================================================
# 設定
# ============================================================

CROP_MARGIN = 0.12  # bboxの上下左右に足す余白(比率)

# --- YOLO26 人物検出 ---
YOLO_MODEL_PATH = str(Path(__file__).parent / "assets" / "yolo26m-det-bf16-mla_tess-b1.tar.gz")
YOLO_MODEL_WIDTH = 640
YOLO_MODEL_HEIGHT = 640
YOLO_SCORE_THRESHOLD = 0.40
YOLO_NMS_IOU = 0.60
YOLO_TOP_K = 100
YOLO_NUM_CLASSES = 80
COCO_PERSON_CLASS_ID = 0

# --- VLM (Gemma 4, llima経由) ---
# DevKit上で `ls /media/nvme/llima/models` を実際に確認して特定したパス
# (gemma-4-E2B-it-GPTQ-a16w4 / gemma-4-E4B-it-GPTQ-a16w4 の両方が存在する)。
VLM_MODEL_DIR = "/media/nvme/llima/models/gemma-4-E2B-it-GPTQ-a16w4"
VLM_MAX_NEW_TOKENS = 512

JUDGE_PROMPT = """あなたは「べた褒めギャル系ファッション審査員」です。渡された全身コーディネート写真を見て、服・色使い・アイテムのバランス・小物・トレンド感といったファッション要素だけを対象に、とにかく全力でべた褒めしてください。批判・指摘・辛口コメントは一切禁止です。

【厳守ルール】
- 体型、顔立ち、外見そのものへの言及はせず、あくまで「服・スタイリング」への称賛に限定する。
- ギャル語(マジ、ヤバい、尊い、神、しか勝たん、エモい、最高すぎ 等)を交えたテンション高めの口調にすること。
- 批判的なニュアンスは一切含めず、どんな要素も前向きに全力で褒めること。
- スコアは基本的に90〜100点の高得点をつけること(よほどのことがない限り90点未満にはしない)。

出力は前置きや説明、コードブロック記号(```など)を一切つけず、以下のJSON形式のみを返すこと。

{
  "score": 90から100の整数,
  "verdict": "全体評価を一言でキャッチコピー的にべた褒めする(20〜30文字程度)",
  "roasts": ["色使い・バランス・小物・トレンド感などについての具体的なべた褒めコメントを3〜4個の配列で"],
  "compliment": "一番刺さった、とっておきの褒めポイントを1文で",
  "closing": "締めの一言。ファンになった的なノリで1文"
}"""


# ============================================================
# データ型
# ============================================================

@dataclass
class BBox:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float


@dataclass
class JudgeResult:
    score: int
    verdict: str
    roasts: list
    compliment: str
    closing: str


# ============================================================
# 1) YOLO26 人物検出・クロップ
# ============================================================

_yolo_model = None  # 遅延ロードした pyneat.Model のキャッシュ(プロセス内で使い回す)
_yolo_model_size = None  # _yolo_model がビルドされた際の (width, height)


def _load_yolo_model(width: int, height: int):
    """YOLO26人物検出モデルをロードする(初回のみ)。

    画像ごとに解像度が変わりうる静止画入力なので、input_max_width/height は
    毎回その画像の実サイズで作り直す(固定値のままだと解像度違いの画像で
    "input width exceeds max_input_width" を"初回pull時"に投げる -- グラフ構築時
    ではないので気付きにくい、既知の落とし穴)。
    """
    import pyneat

    opt = pyneat.ModelOptions()
    opt.preprocess.kind = pyneat.InputKind.Image
    opt.preprocess.enable = pyneat.AutoFlag.On
    opt.preprocess.input_max_width = width
    opt.preprocess.input_max_height = height
    opt.preprocess.input_max_depth = 3  # 3以外だと color_input_requires_input_shape_channels_3
    opt.preprocess.resize.enable = pyneat.AutoFlag.On
    opt.preprocess.resize.width = YOLO_MODEL_WIDTH
    opt.preprocess.resize.height = YOLO_MODEL_HEIGHT
    opt.preprocess.resize.mode = pyneat.ResizeMode.Letterbox
    opt.preprocess.resize.pad_value = 114
    opt.preprocess.color_convert.input_format = pyneat.PreprocessColorFormat.RGB
    opt.preprocess.color_convert.output_format = pyneat.PreprocessColorFormat.RGB
    opt.preprocess.preset = pyneat.NormalizePreset.COCO_YOLO
    opt.decode_type = pyneat.BoxDecodeType.YoloV26
    opt.score_threshold = YOLO_SCORE_THRESHOLD
    opt.nms_iou_threshold = YOLO_NMS_IOU
    opt.top_k = YOLO_TOP_K
    opt.num_classes = YOLO_NUM_CLASSES
    return pyneat.Model(YOLO_MODEL_PATH, opt)


def preload_yolo_model(width: int, height: int) -> None:
    """YOLO26モデルを指定サイズで即座にロードする(サーバー用のウォームアップ)。

    実機で確認した重要な落とし穴: ModelOptions.preprocess.input_max_width/
    height に大きい上限(例: 4096)を設定してModelを構築し、大きめのダミー画像
    で一度runして"容量を確保"しようとしても、その後より小さい実画像を流すと
    "input height N exceeds configured capacity M" のエラーになったり、
    最悪パイプライン自体がタイムアウトし続ける壊れた状態になった(実機テストで
    確認済み、fashion_judge_edge.py側で採用を撤回済みの対策)。

    実際に安定して動くのは、CLI版(単発実行)と同じ「その画像の実サイズで
    ビルドする」方式のみ。サーバーではリクエストごとに画像サイズが変わり
    うるので、detect_person_bbox() 側で「サイズが変わったら再ビルド」する
    ようにした(_yolo_model_size を参照)。この関数はサーバー起動時に典型的
    なサイズで一度ビルドしておき、最初のリクエストの待ち時間を減らすため
    だけのもの -- 実際に来た画像のサイズと違えば detect_person_bbox() が
    透過的に再ビルドする。
    """
    global _yolo_model, _yolo_model_size
    _yolo_model = _load_yolo_model(width, height)
    _yolo_model_size = (width, height)


def preload_vlm_model() -> None:
    """VLMモデルを即座にロードする(サーバー用。初回リクエストの待ち時間を無くす)。"""
    global _vlm_model
    import pyneat

    if _vlm_model is None:
        _vlm_model = pyneat.genai.VisionLanguageModel(VLM_MODEL_DIR)


def detect_person_bbox(image: np.ndarray) -> BBox:
    """
    画像中の最も信頼度の高い「人物」のbboxを返す。

    Model.run() による1枚画像の同期推論(Model.build([seed], ...) はこのデバイスで
    タイムアウトするため使わない -- 静止画の一発推論なので run() で十分)。
    人物が検出できなければ画像全体をbboxとして返す。

    画像サイズが変わった場合は透過的にモデルを再ビルドする(サーバーのように
    1プロセスで複数の異なる解像度の画像を処理する場合に必要。「大きめの上限
    を設定して使い回す」方式は実機で不安定だったため採用していない
    -- preload_yolo_model() のdocstring参照)。
    """
    global _yolo_model, _yolo_model_size
    import pyneat

    h, w = image.shape[:2]
    if _yolo_model is None or _yolo_model_size != (w, h):
        print(f"YOLO26モデルをロード中(サイズ {w}x{h}): {YOLO_MODEL_PATH}", file=sys.stderr)
        _yolo_model = _load_yolo_model(w, h)
        _yolo_model_size = (w, h)

    tensor = pyneat.Tensor.from_numpy(image, copy=True, image_format=pyneat.PixelFormat.RGB)
    outputs = _yolo_model.run([tensor], timeout_ms=5000)

    decoded = pyneat.decode_bbox(outputs, clamp_to=(w, h), top_k=YOLO_TOP_K)
    boxes = []
    for out_tensor in decoded:
        arr = np.asarray(out_tensor.to_numpy(copy=True), dtype=np.float32).reshape((-1, 6))
        for x1, y1, x2, y2, score, class_id in arr:
            if int(class_id) == COCO_PERSON_CLASS_ID:
                boxes.append((float(x1), float(y1), float(x2), float(y2), float(score)))

    if not boxes:
        print("[WARN] 人物が検出できなかった。画像全体を対象にする。", file=sys.stderr)
        return BBox(x1=0, y1=0, x2=w, y2=h, confidence=0.0)

    x1, y1, x2, y2, score = max(boxes, key=lambda b: b[4])
    return BBox(x1=int(x1), y1=int(y1), x2=int(x2), y2=int(y2), confidence=score)


def crop_with_margin(image: np.ndarray, bbox: BBox, margin: float = CROP_MARGIN) -> np.ndarray:
    """bboxに余白を足してクロップする。"""
    h, w = image.shape[:2]
    box_w = bbox.x2 - bbox.x1
    box_h = bbox.y2 - bbox.y1
    mx = int(box_w * margin)
    my = int(box_h * margin)

    x1 = max(0, bbox.x1 - mx)
    y1 = max(0, bbox.y1 - my)
    x2 = min(w, bbox.x2 + mx)
    y2 = min(h, bbox.y2 + my)

    return image[y1:y2, x1:x2]


# ============================================================
# 2) VLM 判定 (pyneat.genai.VisionLanguageModel 経由 Gemma 4 E2B)
# ============================================================

_vlm_model = None  # 遅延ロードした VisionLanguageModel のキャッシュ


def judge_fashion(cropped_image: np.ndarray) -> JudgeResult:
    """
    クロップ済み画像(uint8 HWC RGB)を渡してべた褒めファッション判定JSONを取得する。

    pyneat.genai.VisionLanguageModel + GenerationRequest を使い、Gemma 4 (E2B) に
    JUDGE_PROMPT(べた褒め版)と画像を渡して1回のリクエストで判定JSONテキストを
    得る。
    """
    global _vlm_model
    import pyneat

    if _vlm_model is None:
        print(f"VLMモデルをロード中: {VLM_MODEL_DIR}", file=sys.stderr)
        _vlm_model = pyneat.genai.VisionLanguageModel(VLM_MODEL_DIR)
        print(f"  -> {_vlm_model.model_id()} accepts_image={_vlm_model.accepts_image()}", file=sys.stderr)

    request = pyneat.genai.GenerationRequest()
    request.prompt = JUDGE_PROMPT
    request.images = [cropped_image]  # uint8 HWC RGB を1枚
    request.max_new_tokens = VLM_MAX_NEW_TOKENS

    result = _vlm_model.run(request)
    print(
        f"  -> {result.metrics.generated_tokens} tok, "
        f"{result.metrics.tokens_per_second:.1f} tok/s, "
        f"ttft={result.metrics.time_to_first_token_s:.2f}s",
        file=sys.stderr,
    )
    return parse_judge_response(result.text)


def parse_judge_response(text: str) -> JudgeResult:
    """VLMの生テキスト出力からJSON部分を抽出してパースする。"""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("VLM応答からJSONを抽出できなかった: " + text[:200])

    data = json.loads(cleaned[start:end + 1])
    score = max(0, min(100, int(data.get("score", 0))))

    return JudgeResult(
        score=score,
        verdict=data.get("verdict", ""),
        roasts=list(data.get("roasts", [])),
        compliment=data.get("compliment", ""),
        closing=data.get("closing", ""),
    )


# ============================================================
# ターミナル表示
# ============================================================

class Ansi:
    PINK = "\033[95m"
    MAGENTA = "\033[35m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def print_result(result: JudgeResult) -> None:
    line = "─" * 44
    print()
    print(f"{Ansi.MAGENTA}{line}{Ansi.RESET}")
    print(f"{Ansi.BOLD}{Ansi.YELLOW}  SCORE: {result.score} / 100{Ansi.RESET}")
    print(f"{Ansi.MAGENTA}{line}{Ansi.RESET}")
    print(f"{Ansi.BOLD}{result.verdict}{Ansi.RESET}")
    print()
    print(f"{Ansi.PINK}べた褒めポイント:{Ansi.RESET}")
    for r in result.roasts:
        print(f"  ✨ {r}")
    print()
    print(f"{Ansi.PINK}💗 {result.compliment}{Ansi.RESET}")
    print()
    print(f"{Ansi.DIM}{result.closing}{Ansi.RESET}")
    print(f"{Ansi.MAGENTA}{line}{Ansi.RESET}")
    print()


# ============================================================
# メイン
# ============================================================

def load_image(path: Path) -> np.ndarray:
    img = Image.open(path).convert("RGB")
    return np.array(img)


def _fail(message: str, as_json: bool) -> int:
    """エラーを報告して終了コード1を返す。--jsonモードでもstdoutに必ず1行のJSONを
    出す(呼び出し元がstdoutをパースする専用GUI等が、成功/失敗を区別せず同じ場所
    だけ見ればいいようにするため)。"""
    if as_json:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    else:
        print(f"[ERROR] {message}", file=sys.stderr)
    return 1


def main() -> int:
    global YOLO_MODEL_PATH, VLM_MODEL_DIR

    parser = argparse.ArgumentParser(description="コーデべた褒めジャッジ (エッジ版・rave)")
    parser.add_argument("--image", required=True, type=Path, help="全身写真のパス")
    parser.add_argument("--model-path", default=YOLO_MODEL_PATH,
                         help="YOLO26人物検出モデルのアーカイブパス")
    parser.add_argument("--vlm-model-dir", default=VLM_MODEL_DIR,
                         help="llimaデプロイ済みVLMモデルのディレクトリ")
    parser.add_argument("--json", action="store_true",
                         help="ANSI表示の代わりに、結果を1行のJSONとしてstdoutに出す"
                              "(HOSTPC GUI等、他プロセスから呼び出す用途向け)")
    args = parser.parse_args()
    YOLO_MODEL_PATH = args.model_path
    VLM_MODEL_DIR = args.vlm_model_dir

    if not args.image.exists():
        return _fail(f"ファイルが見つからない: {args.image}", args.json)
    if not Path(YOLO_MODEL_PATH).exists():
        return _fail(f"YOLOモデルが見つからない: {YOLO_MODEL_PATH}", args.json)

    try:
        import pyneat
    except ImportError:
        return _fail(
            "pyneat が見つからない。このスクリプトはModalix DevKit上の pyneat環境"
            "でのみ動作する(`dk ./fashion_judge_edge_rave.py --image ...` 等で実行)。",
            args.json,
        )

    print(f"画像読み込み中: {args.image}", file=sys.stderr)
    image = load_image(args.image)

    try:
        print("YOLO26で人物検出中...", file=sys.stderr)
        bbox = detect_person_bbox(image)
        cropped = crop_with_margin(image, bbox)
        print(f"  -> bbox=({bbox.x1},{bbox.y1},{bbox.x2},{bbox.y2}) conf={bbox.confidence:.2f}",
              file=sys.stderr)

        print("VLM判定中(Gemma 4 via llima、べた褒めモード)...", file=sys.stderr)
        result = judge_fashion(cropped)
    except pyneat.NeatError as error:
        return _fail(f"{error.error_code}: {error.repro_note}", args.json)

    if args.json:
        print(json.dumps({
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
        }, ensure_ascii=False))
    else:
        print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
