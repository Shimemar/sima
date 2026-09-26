# Devkit_0.4.0

このフォルダは、SiMa / Modalix DevKit 向けのサンプルアプリケーション、実験コード、デモ実行用スクリプトをまとめたワークスペースです。

主な用途は、DevKit 上で動作する AI/画像処理アプリの検証と、ホストPC との連携確認です。実験時に使う設定ファイル、起動手順メモ、GUI/CLI 実装例が含まれています。

## 目的

- DevKit でのアプリケーション実行確認
- ホストPC / DevKit / Insight 連携の検証
- 画像認識、マルチモーダルAI、物体検出の実験コード管理
- 実行コマンドや起動メモを残して再現性を確保

## 含まれる主な内容

### 1. high-density-multi-stream-object-detector

複数 RTSP 映像ストリームをまとめて処理する高密度マルチストリーム物体検出サンプルです。

- C++ 実装
- Python 実装
- 設定 YAML
- YOLO 系のモデル設定
- 16 / 24 / 48 ストリーム向けの構成例

このアプリは多数の映像入力を同時に処理し、検出結果を Insight に送る構成になっています。

### 2. multimodal-assistant_e5

マルチモーダルAIアシスタント向けの実装が含まれています。

- Flask ベースの UI
- サーバーコード
- API テスト用スクリプト
- 証明書類
- 画像・音声・テキスト連携の実験コード

音声入力や画像入力を扱うアプリケーションの開発・デバッグに利用されます。

### 3. taste_in_clothes

ファッション評価 / 画像判定の実験コードです。

- GUI ベースのホストPC側アプリ
- DevKit 側のサーバー・画像処理実装
- 推論用スクリプト
- 画像データセット
- 実行用の Python ファイル群

画像分類や視覚判定を用いたデモ用途に合わせた構成です。

### 4. oled

OLED 表示デバイス向けの簡易制御/テストコードが含まれています。

- 初期動作確認
- 表示テスト
- デバイス制御のサンプル

### 5. Yolo_panoptic — 室内パノプティックセグメンテーション (Modalix向け)

USB webcamの映像から、家の中の **物(インスタンス)** と **壁・床・天井・ドア(領域)** を画素単位で認識する Modalix DevKit 用アプリです。最終ターゲットは SiMa.ai Modalix DevKit(Palette / Neat SDK)。

#### 方式

YOLOはパノプティックセグメンテーションを直接出力しないため、2モデルの出力を後段で統合します:

- **things** (物のインスタンス): YOLO11n-seg (COCO学習済み)
- **stuff** (壁/床/天井/ドア): LRASPP-MobileNetV3-Large (自宅映像の擬似ラベルで学習)
- **教師** (擬似ラベル生成): UperNet-ConvNeXt-small (ADE20K)

#### 主なファイル

| ファイル | 用途 |
|---|---|
| `start.md` | 起動方法だけをまとめたクイックスタート |
| `panoptic_webcam.py` | PC検証 (webcam + YOLO-seg + 教師 + 統合) |
| `record_frames.py` | 学習用フレーム録画 |
| `make_pseudo_labels.py` | 教師による擬似ラベル生成 |
| `train_student.py` | 生徒モデル学習 |
| `export_onnx.py` | ONNXエクスポート + 検証 |
| `quantize_compile.py` | Palette量子化・コンパイル |
| `attention_surgery.py` | C2PSA MatMul→Einsum置換 |
| `eval_quantized.py` | 量子化後の精度検証 |
| `main.py` | Modalixパイプライン本体 |
| `probe_camera.py` | USBカメラ検出・設定ヘルパー |
| `config/` | カメラ・モデル・しきい値・UDP出力の設定ファイル |

#### 起動方法

初回: USBカメラの検出・設定
```bash
dk ./probe_camera.py                                 # USBカメラを検出
dk ./probe_camera.py --device /dev/videoNN --apply   # config に反映
```

本番起動
```bash
dk ./main.py --config ./config/default.conf --frames 300    # 有限フレームでテスト
dk ./main.py --config ./config/default.conf                 # 無制限実行
```

#### 実績

- **実物のUSBカメラでの動作確認済み** (Anker PowerConf C200、1280x720@30)
- **実測性能**: 約6.9fps (Step 4の最適化適用後)
- **モデルサイズ**:
  - 生徒モデル (LRASPP): 6.0MB
  - YOLO (attention Einsum化 + ヘッド切断): 11MB
- **精度**:
  - 生徒モデル val mIoU: 0.863 (INT8量子化後、FP32: 0.879)
  - マスク輪郭・壁/床領域の位置判定: 目視で許容範囲

詳細な設計・工程記録は `Yolo_panoptic/README.md` を参照してください。

### 6. Readme.txt

DevKit / HOSTPC での起動手順や、デモ実行用コマンドがまとめられたメモです。

- 環境の立ち上げ手順
- NFS マウントや仮想環境の有効化
- LLM / VLM / 物体検出 / ファッション判定などの実行例

## 典型的な利用シーン

- DevKit での AI アプリの動作確認
- RTSP 映像入力の解析実験
- マルチモーダル入力の検証
- 画像認識や音声・LLM連携のデモ構築
- ホストPC と DevKit の連携テスト
- パノプティックセグメンテーション (室内シーン認識) の実装・検証

## 補足

このフォルダは、実験用・検証用のワークスペースとして設計されています。実行環境やモデル配置パス、IP アドレス、ホスト情報が README や設定ファイルに記載されているため、各自の環境に合わせてカスタマイズしてください。

## 関連ファイル

- `Readme.txt` : 実行手順メモ
- `high-density-multi-stream-object-detector/README.md` : 物体検出アプリの詳細説明
- `multimodal-assistant_e5/README.md` : マルチモーダルアシスタントの説明
- `taste_in_clothes/README.md` : 画像判定アプリの説明
- `Yolo_panoptic/README.md` : パノプティックセグメンテーションの詳細設計・工程記録
- `Yolo_panoptic/start.md` : パノプティックセグメンテーションの起動方法
