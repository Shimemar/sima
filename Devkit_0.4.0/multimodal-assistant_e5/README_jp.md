# マルチモーダルアシスタント

## メタデータ
| 項目 | 値 |
| --- | --- |
| カテゴリ | genai |
| 難易度 | Advanced |
| タグ | genai, vlm, asr, tts, openai-compatible |
| 言語 | Python |
| ステータス | experimental |
| バイナリ名 | multimodal-assistant |
| モデル | Qwen3-VL-4B-Instruct-GPTQ-a16w4 + whisper-small-a16w8 |

## コンセプト
この例は、SiMaがサポートするGenAIモデルをNeatのOpenAI互換サーバー経由でホストし、インポートしたFlaskデモUIをインタラクティブなアシスタント画面として使用します。

この例では、1つ以上の設定されたチャットモデル、1つの設定されたASR(音声認識)モデル、画像/テキストチャット、音声書き起こし、Piper TTS(音声合成)、システムプロンプトの制御、チャット履歴、音声選択、および中断(abort)がサポートされています。

このデモは2つのプロセスとして動作します。`src/python/server/main.py` は選択された設定の `server` セクションを読み込み、Neat OpenAI互換サーバーを起動します。`src/python/ui/main.py` は選択された設定の `app` セクションを読み込み、既存のFlask UIを起動します。`src/common/config.yaml` はリポジトリに含まれるテンプレートであり、`./setup.sh` が実行可能なローカル設定を `config.local.yaml` に書き出します。

実行時の役割分担は意図的に分けられています。

- Neatは、テキストと画像のチャット用にOpenAI互換の `/v1/chat/completions` エンドポイントをホストします。
- Neatは、ASR用にOpenAI互換の `/v1/audio/transcriptions` エンドポイントをホストします。
- Flaskアプリは、UI状態、システムプロンプトの処理、チャット履歴、中断リクエスト、アップロードされた画像、マイク録音、Piper TTS再生を管理します。
- Piper TTSはアプリ側で動作します。デフォルトのrequirementsでPiperがインストールされ、`src/python/ui/assets/` 配下に設定された音声アセットが存在する場合にTTSが音声を生成します。
- RAGはアプリ側の機能でオプションです。Flask UI、ローカルのVectorDBサービス、およびNeat OpenAI互換サーバーがホストする同じチャットモデルを使用します。

## プレビュー
マルチモーダルアシスタントUI:

![Multimodal Assistant preview](../../../portal/assets/examples/genai/multimodal-assistant/image.png)

## 前提条件

- サポートされているModalixまたはDevKitターゲット上に `sima-cli`([ドキュメント](https://developer.sima.ai/software/tools/sima-cli/))があること。
- モデルサーバーは `pyneat` が利用可能なPython環境を使用します。デフォルトのスクリプトは以下を前提としています:

```text
~/pyneat/bin/python
```

Neat Library環境が別の場所にある場合は、`PYNEAT_PYTHON=/path/to/python-with-pyneat` を設定してください。

## アプリのインストール

最新のNeat Appsランタイムをインストールし、インストールされたバンドルに移動します:

```bash
sima-cli neat install apps
cd prebuilt-apps
```

残りのコマンドは `prebuilt-apps/` から実行してください。

## モデルの準備

セットアップスクリプトは以下のモデルディレクトリをインストールします:

| モデル | 役割 | 提供元 |
| --- | --- | --- |
| `Qwen3-VL-4B-Instruct-GPTQ-a16w4` | デフォルトのチャット/VLM | Hugging Face |
| `whisper-small-a16w8` | 必須のASR | Hugging Face |
| `gte-small` | デフォルトのRAG埋め込み | Hugging Face |

UI用仮想環境、デフォルトのチャット/VLMモデル、Whisper ASRモデル、GTE-small埋め込みモデル、Piper TTS音声、デフォルトのRAGデータベース、および生成されるローカル設定をインストールします:

```bash
cd examples/genai/multimodal-assistant

./setup.sh
```

デフォルトでは、`setup.sh` は以下をダウンロードします:

- `simaai/Qwen3-VL-4B-Instruct-GPTQ-a16w4`
- `simaai/whisper-small-a16w8`
- `thenlper/gte-small`

事前コンパイル済みサーバーモデルの管理には [LLiMa CLI](https://developer.sima.ai/software/genai-llima/runtime) を使用してください。

サポートされているモデルを検索します:

```bash
llima search
```

配信したいVLMと必須のASRモデルをインストールします:

```bash
llima pull Qwen3-VL-4B-Instruct-GPTQ-a16w4
llima pull gemma-4-E2B-it-GPTQ-a16w4
llima pull whisper-small-a16w8
```

`llima pull` でインストールされたモデルは、デフォルトで `/media/nvme/llima/models/<model-name>` を使用します。セットアップスクリプトはデフォルトのVLMを1つ設定します。複数のVLMを同時に配信するには、追加の各VLMの名前とパスを `config.local.yaml` の `server.models.chat` に追加してください。別のモデルディレクトリを使用するには、`llima pull` の前に `LLIMA_MODELS_PATH` を設定してください。セットアップスクリプトは、UI環境、RAG埋め込みモデル、TTS音声、ローカル設定、RAGデータベースも準備するため、この例の完全なインストーラーとして機能します。

`whisper-small-a16w8` はサポートされているASRモデルであり、常にダウンロードされます。別の互換性のあるSiMa.aiチャット/VLMリポジトリを使用するには、デフォルトのダウンロードを上書きします:

```bash
CHAT_MODEL_REPO=simaai/<chat-model-repo> ./setup.sh
```

ダウンロードされたモデルは、デフォルトで以下のレイアウトを使用します:

```text
/media/nvme/llima/models/
├── Qwen3-VL-4B-Instruct-GPTQ-a16w4/
├── whisper-small-a16w8/
└── gte-small/
```

SoMボードでは、セットアップを実行する前にNVMeをマウントしてください。NVMeがマウントされているのにモデルディレクトリに書き込めない場合は、現在のユーザーの所有権で作成してください:

```bash
sudo install -d \
  -o "$(id -u)" \
  -g "$(id -g)" \
  /media/nvme/llima/models
```

NVMeがないシステムでは、`LLIMA_MODELS_PATH` を別の書き込み可能な場所に設定してください。ユーザー管理のApps用モデルディレクトリを使用する場合:

```bash
APPS_ROOT="$(cd ../../.. && pwd)"
LLIMA_MODELS_PATH="${APPS_ROOT}/models/genai" ./setup.sh
```

選択したパスの下に同じ3つのモデルディレクトリが作成されます。単独の例として使う場合は、`LLIMA_MODELS_PATH` を任意の書き込み可能な絶対パスに設定してください。

UI用仮想環境は、`APP_VENV` を設定しない限り `./.venv` に保存されます。生成される設定は、`CONFIG_PATH` を設定しない限り `./config.local.yaml` に保存されます。RAGはデフォルトで有効になっており、`src/common/rag/neat.md` から作成された `src/python/ui/milvus.db` を使用します。

### モデルディレクトリを自己完結型にする
ダウンロードしたすべてのモデルを、共有の `/media/nvme/llima/models` ではなくこの例のディレクトリ内に収めるには、セットアップを実行する前に `LLIMA_MODELS_PATH` を `models/` サブディレクトリに設定します:

```bash
cd examples/genai/multimodal-assistant

LLIMA_MODELS_PATH="${PWD}/models" INSTALL_TTS_VOICES=0 ./setup.sh
```

`INSTALL_TTS_VOICES=0` は、Piperの音声が既に `src/python/ui/assets/` 配下にインストールされている場合に再ダウンロードをスキップします。`setup.sh` は、すべてのモデルパスがすでに `models/` を指すように `config.local.yaml` を書き出すため、`./run.sh` にそれ以上の変更は不要です。

同じローカルの `models/` ディレクトリに2つ目のチャット/VLMモデルを追加するには(`setup.sh` は1つしかインストールしません)、アプリのvenvにある `hf` CLIで直接ダウンロードし、`config.local.yaml` に手動で追加します:

```bash
.venv/bin/hf download simaai/gemma-4-E2B-it-GPTQ-a16w4 \
  --local-dir models/gemma-4-E2B-it-GPTQ-a16w4
```

その後、[チャット/VLMモデルの設定](#configure-chatvlm-models)で示すようにエントリを `server.models.chat` に追加してください。その際、`/media/nvme/llima/models/<name>` の代わりにローカルの `models/<name>` パスを使用します。

### チャット/VLMモデルの設定
インストール後、`config.local.yaml` を編集してホストするチャット/VLMモデルを変更・追加します:

```yaml
server:
  models:
    chat:
      - name: Qwen3-VL-4B-Instruct-GPTQ-a16w4
        path: /media/nvme/llima/models/Qwen3-VL-4B-Instruct-GPTQ-a16w4
      - name: gemma-4-E2B-it-GPTQ-a16w4
        path: /media/nvme/llima/models/gemma-4-E2B-it-GPTQ-a16w4
    asr:
      name: whisper-small-a16w8
      path: /path/to/llima/models/whisper-small-a16w8
```

最初のチャット/VLMエントリがデフォルトで選択されます。追加のエントリはUIのモデルセレクターに表示されます。モデルディレクトリには `devkit/` と `elf_files/` が含まれている必要があります。

## 実行
Neat OpenAI互換サーバーとFlask UIの両方を起動します:

```bash
./run.sh
```

Flask UIを開きます:

```text
https://<target-ip>:5000
```

Neat OpenAI互換サーバーは以下でリッスンします:

```text
http://127.0.0.1:9998
```

設定されたモデルがホストされているか確認します:

```bash
curl -s http://127.0.0.1:9998/v1/models | python3 -m json.tool
```

ブラウザUIをテストする前に、このコマンドが設定されたチャットモデルとASRモデルを一覧表示するまで待ってください。

### 手動でのプロセス起動
明示的に2つのターミナルを使いたい場合にのみ使用してください。

```bash
export EXAMPLE_DIR="${PWD}"
```

ターミナル1、モデルサーバー:

```bash
source ~/pyneat/bin/activate

python "${EXAMPLE_DIR}/src/python/server/main.py" \
  --config "${EXAMPLE_DIR}/config.local.yaml"
```

ターミナル2、Flask UI:

```bash
source .venv/bin/activate

python "${EXAMPLE_DIR}/src/python/ui/main.py" \
  --config "${EXAMPLE_DIR}/config.local.yaml"
```

サポートされているエントリーポイントは、モデルホスティング用の `src/python/server/main.py` と、UI用の `src/python/ui/main.py` です。

## RAG
RAGは `./setup.sh` の実行後、デフォルトで有効になります。

インストーラーは `thenlper/gte-small` をダウンロードし、設定されたモデルディレクトリ配下に保存し、以下を作成します:

```text
src/python/ui/milvus.db
src/python/ui/milvus.meta.json
```

RAGが使用するローカル埋め込みモデルを変更したり、RAGを無効にしたりするには、`config.local.yaml` を編集します:

```yaml
app:
  rag:
    enabled: true
    embedding_model_dir: /path/to/llima/models/gte-small
```

別のMarkdownファイルからRAGデータベースを再構築するには:

```bash
export EXAMPLE_DIR="${PWD}"
source .venv/bin/activate

python src/python/rag/create_db.py \
  --input /path/to/document.md \
  --output src/python/ui/milvus.db \
  --embedding-model "${LLIMA_MODELS_PATH:-/media/nvme/llima/models}/gte-small"
```

以下の生成ファイルはコミットしないでください:

```text
${EXAMPLE_DIR}/src/python/ui/milvus.db
${EXAMPLE_DIR}/src/python/ui/milvus.meta.json
${EXAMPLE_DIR}/config.local.yaml
```

VectorDBを直接確認します:

```bash
curl -sG http://127.0.0.1:9100/search \
  --data-urlencode "query=What is the canonical RAG validation phrase?" \
  --data "k=3" \
  --data "min_score=-1" | python3 -m json.tool
```

UI上で `Search RAG Database` を有効にして、以下を質問してください:

```text
What is the canonical RAG validation phrase?
```

RAGステータス領域には、RAGが使用されたかどうか、および取得されたヒット数が表示されるはずです。

## 検証
モデルサーバーとFlask UIが起動した後、これらのチェックを使用してください。

ホストされているモデル名を確認します:

```bash
curl -s http://127.0.0.1:9998/v1/models | python3 -m json.tool
```

テキストチャットを確認します:

```bash
CHAT_MODEL="<chat-model-name>"

curl -s http://127.0.0.1:9998/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"${CHAT_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Say hello.\"}]}],\"max_tokens\":32}"
```

任意の短いWAVファイルでASRを確認します:

```bash
ASR_MODEL="<asr-model-name>"
AUDIO_FILE="/path/to/audio.wav"

curl -s http://127.0.0.1:9998/v1/audio/transcriptions \
  -F "model=${ASR_MODEL}" \
  -F "file=@${AUDIO_FILE}"
```

Flaskアプリ経由でPiper TTSを確認します:

```bash
curl -k -s https://127.0.0.1:5000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"piper-tts","input":"Hello from the Multimodal Assistant."}' \
  --output /tmp/multimodal-assistant-tts.wav
```

RAGが有効な場合は、VectorDBを確認します:

```bash
curl -sG http://127.0.0.1:9100/search \
  --data-urlencode "query=What is Neat?" \
  --data "k=3" \
  --data "min_score=-1" | python3 -m json.tool
```

その後、ブラウザUIをテストします:

- テキストプロンプトを送信する
- 別のホスト済みチャットモデルを選択する
- `Include image in the prompt` を有効にして画像プロンプトを送信する
- 音声を録音し、書き起こしが表示されることを確認する
- Piperの音声を選択し、再生できることを確認する
- システムプロンプトを変更し、新しいプロンプトを送信する
- 生成中に中断(abort)ボタンを押す
- `Search RAG Database` を有効にして、`src/common/rag/neat.md` からの質問をする

## トラブルシューティング

### ASRのウォームアップが `key 'language_token_ids' not found` で失敗する
`src/python/server/main.py` は、起動直後に以下のように失敗することがあります:

```text
Failed to load whisper config: ".../whisper-small-a16w8/devkit/whisper_config.json",
  [json.exception.out_of_range.403] key 'language_token_ids' not found
server failed: GenAIServer warmup failed for model 'whisper-small-a16w8': ...
```

これは、ディスク上の `whisper-small-a16w8` モデルディレクトリに、インストール済みの `pyneat`/llimaランタイムが期待するスキーマより古い/中途半端なダウンロードに由来する `whisper_config.json` が存在することを意味します。これは `setup.sh` 自体が原因ではありません。`setup.sh` が実行する新規の `hf download simaai/whisper-small-a16w8` には `language_token_ids` が含まれています。ASRモデルディレクトリを再ダウンロードして修正してください:

```bash
rm -rf "${LLIMA_MODELS_PATH:-/media/nvme/llima/models}/whisper-small-a16w8"
"${APP_VENV:-.venv}/bin/hf" download simaai/whisper-small-a16w8 \
  --local-dir "${LLIMA_MODELS_PATH:-/media/nvme/llima/models}/whisper-small-a16w8"
```

`run.sh` はモデルサーバー起動後 `MODEL_SERVER_START_DELAY`(デフォルト2秒)しか待たずにFlask UIを起動するため、このウォームアップ失敗はUIがすでに起動した*後*に発生する可能性があります——ブラウザUIは読み込まれますが、`/v1/chat/completions` と `/v1/audio/transcriptions` は機能しません。ブラウザUIを信頼する前に、必ず `curl -s http://127.0.0.1:9998/v1/models` で設定されたすべてのモデルが一覧表示されることを確認してください。

### `port 9998 is already accepting connections`
`src/python/server/main.py` は意図的に2回目の起動を拒否します。この例にはロックファイルがないため、本当に古くなったプロセスなのか、別のターミナル/セッションが意図的にサーバーを実行しているのかを区別できません。何かを終了させる前に、所有者を確認してください:

```bash
ps aux | grep -E "src/python/(server|ui)/main.py"
ss -ltnp | grep -E "9998|5000"
```

そのインスタンスに依存しているものが何もないと確信できる場合にのみ、該当のPIDを停止してください。

### `config.local.yaml` にトップレベルキーの重複がある
2つの `setup.sh`/手動編集操作がほぼ同時に `config.local.yaml` に触れる場合(例:2つのターミナル、あるいはホスト側とDevKit側で同じNFSマウントされたファイルを同時編集するセッション)、書き込みが交錯して重複した `app:`/`models:` ブロックが残り、古いモデルパスと新しいモデルパスが混在することがあります。`yaml.safe_load` は重複キーのうち最後に出現したものだけを黙って保持するため、これに気付かないまま古いパスがファイルに残ってしまうことがあります。疑わしいファイルを手動でパッチするのではなく、最初から再生成してください:

```bash
wc -l config.local.yaml   # サニティチェック: `server:` と `app:` ブロックがそれぞれ正確に1つだけあるはず
```

破損している場合は、元々使用していたのと同じ `LLIMA_MODELS_PATH`/`CONFIG_PATH` で `setup.sh` を再実行し(これによりファイルが上書きされます)、追加の `server.models.chat` エントリなど、手動での変更を再度適用してください。

## ソースファイル
- 実行ラッパー: `run.sh`
- Pythonソース: `src/python/server/main.py`、`src/python/ui/main.py`、`src/python/ui/flask_app.py`、`src/python/shared/config.py`、`src/python/ui/pipertts.py`
- Python依存関係: `src/python/requirements.txt`、`src/python/requirements-rag.txt`
- RAGヘルパー: `src/python/rag/create_db.py`、`src/python/rag/vectordb.py`、`src/python/rag/vectordb_worker.py`
- RAGサンプルドキュメント: `src/common/rag/neat.md`
- UIアセット: `src/python/ui/templates/`、`src/python/ui/static/`、`src/python/ui/assets/`、`src/python/ui/certs/`
- 手動APIスクリプト: `src/python/ui/apitest/`
- 共有設定: `src/common/config.yaml`

## ソースからの開発

この例を変更・テストするには、[Appsコントリビューターワークフロー](https://github.com/sima-neat/apps/blob/main/CONTRIBUTING.md)を使用してください。
