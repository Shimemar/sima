# PCIe_HHHL

このフォルダーは、SiMa Modalix PCIe Card を利用した実験・アプリケーションのまとめです。
主に次の 2 つの用途を対象にしています。

- PCIe カード上で動作する LLM / 音声・画像対応のローカルアシスタント
- PCIe カードを使う高密度マルチストリーム物体検出アプリケーション

---

## 1. フォルダー構成

```text
PCIe_HHHL/
├── README.md
├── llm/
│   ├── README.md
│   ├── card_server.py
│   ├── launch.py
│   ├── tts.py
│   ├── setup_tts.sh
│   ├── static/
│   │   ├── index.html
│   │   ├── app.js
│   │   ├── style.css
│   │   └── ...
│   ├── tests/
│   │   ├── test_server.py
│   │   ├── test_tts.py
│   │   ├── browser_smoke.py
│   │   └── ...
│   ├── vendor/
│   │   └── open-jtalk/
│   ├── test-results/
│   │   ├── browser.png
│   │   ├── speech.wav
│   │   ├── offline-speech.wav
│   │   └── ...
│   └── __pycache__/
├── pcie-high-density-multi-stream-object-detector/
│   ├── README.md
│   ├── run.sh
│   ├── start.md
│   └── src/
│       ├── common/
│       ├── cpp/
│       └── host/
└── README.md
```

---

## 2. llm

`llm/` は、Modalix PCIe Card 上の生成AIモデル（Gemma4 / Whisper）を使うローカルWebアプリです。
ブラウザからカメラ、マイク、キーボード入力を受け取り、音声認識と画像付き質問をまとめて処理します。

### 主な機能

- 日本語の音声入力（Whisper による文字起こし）
- カメラ画像と質問を一緒に送信
- LLM からの回答をブラウザに逐次表示
- ホスト側で音声を Open JTalk で読み上げ
- `--external-server` オプションで既存のカードサーバーを利用可能
- `tests/` に smoke test や shutdown test が含まれる

### 実行イメージ

```bash
cd /home/shinko/pcie_work/llm
./run.sh
```

通常は `http://localhost:8080` で起動し、利用可能なポートが埋まっている場合は 8081 以降へ自動切り替えされます。

### 主要ファイル

- `README.md` : 機能・構成・動作説明
- `launch.py` : アプリ起動・終了管理
- `card_server.py` : PCIe カード側 HTTP サーバー
- `tts.py` : オフライン音声合成
- `static/` : Web UI（HTML/JS/CSS）
- `tests/` : 自動検証スクリプト
- `vendor/open-jtalk/` : オフライン音声生成用の依存資産

---

## 3. pcie-high-density-multi-stream-object-detector

`pcie-high-density-multi-stream-object-detector/` は、複数の RTSP/H.264 ストリームを高速に処理し、PCIe カード上で物体検出を行うアプリケーションです。
主に「多チャネルの映像をまとめて推論し、Insight に結果を送る」構成を想定しています。

### 主な特徴

- 16 / 24 / 48 ストリーム対応の設定ファイルを持つ
- `config.yaml` と `config-24x720p20fps.yaml` / `config-48x720p10fps.yaml` を利用
- Host / PCIe Card の両方のアプリを起動する `run.sh` を保持
- YOLO26n det INT8 batch 1 を利用する前提
- `Insight` への検出結果出力に対応

### 代表的な構成

```bash
APP_DIR=examples/object-detection/pcie-high-density-multi-stream-object-detector
cmake -S "$APP_DIR/src/host" -B build-host-pcie -DCMAKE_BUILD_TYPE=Release
cmake --build build-host-pcie --parallel
```

```bash
./${APP_DIR}/run.sh --config ${APP_DIR}/src/common/config.local.yaml
```

### 主要ファイル

- `README.md` : アプリの紹介と実行方法
- `run.sh` : ホスト側の起動スクリプト
- `src/common/` : 設定ファイルや共通パーサ
- `src/cpp/` : PCIe カード側のグラフ処理と demux
- `src/host/` : ホスト側の C++ アプリケーション

---

## 4. 目的と全体像

このフォルダーは、以下のような PCIe 開発ワークフローをまとめるための実験スペースです。

1. PCIe カードとホストの接続確認
2. Neat Host / DevKit の準備
3. モデルの取得と設定
4. 高密度マルチストリーム推論の実行
5. AI アシュスタントとしてのローカル生成AI利用

つまり、`llm/` は対話型アシスタント、`pcie-high-density-multi-stream-object-detector/` は高速映像解析の例として位置づけられます。

---

## 5. 前提条件

- Ubuntu ホスト
- SiMa PCIe Host / DevKit の導入済み環境
- Modalix PCIe Card の接続
- 生成AIモデルや推論用のモデルファイルが `/workspace` などに配置済み
- ネットワーク経由のホスト/カード通信（SSH / HTTP / NFS 等）

---

## 6. 備考

- `llm/README.md` には詳細な動作手順、データフロー、検証方法が記載されている。
- `pcie-high-density-multi-stream-object-detector/README.md` には高密度マルチストリーム検出の前提条件と実行方法が記載されている。
- このフォルダーの README は、各サブプロジェクトの入口として利用することを想定している。

---

このフォルダーは、PCIe を使った実行環境・推論アプリケーション・ローカルAIの連携を確認するための実験置き場として整理されている。
