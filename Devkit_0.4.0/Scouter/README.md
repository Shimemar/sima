# AI SCOUTER - YOLO + VLM 戦闘力測定システム

## 1. 概要

**AI SCOUTER** は、カメラ映像からYOLOで人物を検出し、ByteTrackで人物を追跡、VLM（Vision Language Model）で服装・ポーズ・持ち物・雰囲気などを解析して、架空の「戦闘力」として表示する展示向けAIデモです。

画面表示は、SF作品に登場する**スカウター風HUD**をイメージした演出とします。

```text
              ◇ TARGET LOCKED
                   │
            ┌──────┴──────┐
            │             │
          PERSON        18,420
            │
       ┌─────────┐
       │         │
       │ PERSON  │────── POWER LEVEL
       │         │       18,420
       └─────────┘

      ANALYZING ██████████ 100%

      「この人物……
       想定以上の戦闘力です。」
```

## 2. システム構成

```text
Camera / RTSP
      │
      ▼
┌───────────────┐
│     YOLO      │
│ Person Detect │
└───────┬───────┘
        │
        ▼
┌───────────────┐
│   ByteTrack   │
│ Person Track  │
└───────┬───────┘
        │
        ├── ID=12
        ├── ID=18
        └── ID=21
              │
              ▼
       Person ROI Crop
              │
              ▼
┌───────────────────────┐
│          VLM          │
│  Qwen3-VL-4B etc.     │
└───────────┬───────────┘
            │
            ▼
       JSON Analysis
            │
            ▼
┌───────────────────────┐
│   Power Calculator    │
└───────────┬───────────┘
            │
            ▼
┌───────────────────────┐
│     SCOUTER HUD       │
│ OpenCV / Web Frontend │
└───────────────────────┘
```

| 処理 | 使用技術 |
|---|---|
| カメラ入力 | USB Camera / RTSP |
| 人物検出 | YOLO11 / YOLO26 |
| 人物追跡 | ByteTrack |
| 人物解析 | Qwen3-VL-4B等 |
| 戦闘力計算 | Python |
| HUD表示 | OpenCV / Web UI |
| 実行環境 | SiMa.ai Modalix |

## 3. 処理フロー

```text
Camera
  ↓
YOLO
  ↓
Person Detection
  ↓
ByteTrack
  ↓
Track ID
  ↓
人物画像Crop
  ↓
VLM
  ↓
特徴量JSON
  ↓
Power Calculator
  ↓
戦闘力
  ↓
SCOUTER HUD
```

重要なのは、**VLMを毎フレーム実行しないこと**です。

```text
YOLO       : 30 FPS程度
ByteTrack  : 30 FPS程度
VLM        : 新規人物発見時のみ
```

## 4. ByteTrackによる人物管理

人物にはByteTrackでTrack IDを付与し、解析結果をTrack ID単位でキャッシュします。

```python
analyzed_persons = {}

if track_id not in analyzed_persons:
    crop = frame[y1:y2, x1:x2]
    result = run_vlm(crop)
    power = calc_power(result)

    analyzed_persons[track_id] = {
        "power": power,
        "result": result
    }
```

## 5. VLMによる人物解析

VLMには人物ROI画像を入力し、戦闘力そのものではなく演出用の特徴量を取得します。

```json
{
  "fashion": 82,
  "confidence": 74,
  "energy": 63,
  "coolness": 91,
  "pose": "腕組み",
  "item": "バックパック",
  "title": "歴戦のプロジェクトリーダー",
  "comment": "静かにこちらを見ている。只者ではない雰囲気。"
}
```

年齢・性別・人種・障害などのセンシティブな属性は戦闘力計算に使用しません。

## 6. VLM Prompt

```text
あなたは展示会用の架空の戦闘力分析AIです。

人物画像から、服装、ポーズ、持ち物、表情など、
画像から直接確認できる特徴だけを分析してください。

実際の能力や性格を断定してはいけません。
あくまでゲーム的なジョークとして評価してください。

以下のJSON形式のみ返してください。

{
  "fashion": 0-100,
  "confidence": 0-100,
  "energy": 0-100,
  "coolness": 0-100,
  "pose": "ポーズ",
  "item": "持ち物",
  "title": "面白い称号",
  "comment": "30文字程度の実況"
}
```

## 7. 戦闘力計算

```python
import random

def calc_power(result):
    fashion = result["fashion"]
    confidence = result["confidence"]
    energy = result["energy"]
    coolness = result["coolness"]

    power = (
        fashion * 35
        + confidence * 45
        + energy * 30
        + coolness * 50
    )

    power *= random.uniform(0.90, 1.15)
    return int(power)
```

## 8. 戦闘力ランク

| 戦闘力 | クラス |
|---:|---|
| 800～3,000 | 一般人級 |
| 3,000～10,000 | 強者級 |
| 10,000～30,000 | エリート級 |
| 30,000～100,000 | ボス級 |
| 100,000以上 | 測定不能 |

## 9. SCOUTER HUD

```text
SCANNING...

128
847
2,841
8,932
12,481
16,892
18,420

TARGET LOCKED
```

```text
┌─────────────────────────┐
│ TARGET ID : #007        │
│                         │
│ POWER LEVEL             │
│        18,420           │
│                         │
│ CLASS : ELITE           │
│                         │
│ 歴戦の                  │
│ プロジェクトリーダー    │
│                         │
│ 「この落ち着き……       │
│  只者ではありません」  │
└─────────────────────────┘
```

## 10. 測定不能演出

```text
91,284
95,821
98,432
99,847
99,999

ERROR
ERROR
ERROR

POWER LEVEL
████████████████████

測 定 不 能

WARNING
SCOUTER OVERLOAD

「スカウターが耐えられません！」
```

## 11. 複数人物への対応

```text
TARGET #03      12,840
TARGET #07      38,210
TARGET #11       8,720
──────────────────────
TEAM POWER      59,770
```

## 12. 非同期VLM処理

VLM推論はメイン映像処理から分離します。

```text
                ┌──────────────┐
Camera ───────→ │ YOLO         │
                │ ByteTrack    │
                └──────┬───────┘
                       │ New Person
                       ▼
                   VLM Queue
                       │
                       ▼
                ┌──────────────┐
                │ VLM Worker   │
                └──────┬───────┘
                       ▼
                  JSON Result
                       │
                       ▼
                  Result Cache
                       │
                       ▼
Camera ─────────────────────→ HUD
```

## 13. 推奨ディレクトリ構成

```text
ai-scouter/
├── README.md
├── main.py
├── detector.py
├── tracker.py
├── vlm_worker.py
├── power_calculator.py
├── scouter_hud.py
├── config/
│   └── config.yaml
├── prompts/
│   └── person_analysis.txt
├── assets/
│   ├── sounds/
│   │   ├── scan.wav
│   │   ├── warning.wav
│   │   └── overload.wav
│   └── hud/
└── models/
```

## 14. main.py イメージ

```python
while True:
    frame = camera.read()
    detections = yolo(frame)
    tracks = bytetrack.update(detections)

    for track in tracks:
        track_id = track.id
        x1, y1, x2, y2 = track.xyxy

        if track_id not in analyzed_persons:
            crop = frame[y1:y2, x1:x2]

            vlm_queue.put({
                "track_id": track_id,
                "image": crop
            })

        if track_id in analyzed_persons:
            result = analyzed_persons[track_id]

            draw_scouter(
                frame,
                bbox=(x1, y1, x2, y2),
                track_id=track_id,
                power=result["power"],
                title=result["title"],
                comment=result["comment"]
            )

    cv2.imshow("AI SCOUTER", frame)
```

## 15. Modalix構成案

```text
                Modalix
                   │
                   ▼
            Camera / RTSP
                   │
                   ▼
            YOLO11 / YOLO26
                   │
                   ▼
               ByteTrack
                   │
                   ▼
             Person Crop
                   │
                   ▼
            Qwen3-VL-4B
                   │
                   ▼
             JSON Result
                   │
                   ▼
          Power Calculator
                   │
                   ▼
            SCOUTER HUD
                   │
                   ▼
               Display
```

## 16. 展示会向け追加アイデア

- 戦闘力カウントアップ
- TARGET LOCK演出
- HUDスキャンライン
- レーダー表示
- 警告音
- 戦闘力100,000超えで画面振動
- ERROR / OVERLOAD演出
- VLMによる面白い称号
- VLMによる実況コメント
- ByteTrackによる人物追跡
- 複数人の総戦闘力表示
- ランキング表示
- 「本日の最高戦闘力」表示

## 17. コンセプト

**「AIが人を見ると、世界はゲームになる。」**

YOLOによるリアルタイム人物検出、ByteTrackによる人物追跡、VLMによる視覚理解を組み合わせることで、単なる人物検出デモではなく、来場者自身が参加できるインタラクティブなEdge AIデモを実現します。

> ※ 本システムの「戦闘力」は展示・ゲーム演出用に生成される架空の数値であり、人物の実際の能力・性格・価値を評価するものではありません。
