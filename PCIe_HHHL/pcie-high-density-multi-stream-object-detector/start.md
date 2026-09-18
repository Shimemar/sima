# 起動手順（現在の環境）

以下のコマンドはホスト `shinko@simapc` で実行します。

## 1. 起動前の確認

- PCIe カード: `sima@10.0.0.2`
- SSH 秘密鍵: `~/.ssh/sima_neat_pcie_ed25519`
- ホストバイナリ: `/home/shinko/pcie_work/apps/build-host-pcie/pcie-high-density-multi-stream-object-detector-host`
- カードバイナリ: `/home/sima/prebuilt-apps/examples/object-detection/pcie-high-density-multi-stream-object-detector/src/cpp/pre-built/pcie-high-density-multi-stream-object-detector`
- 設定: `src/common/config.local.yaml`

RTSP 配信と Insight を先に起動しておきます。現在の設定は 48 ストリーム、1280×720、10 FPS で、`rtsp://10.0.0.1:8554/src1` ～ `src16` をそれぞれ 3 回登録しています。Insight の送信先はホスト側の `127.0.0.1`、映像ポートの起点は `9000`、メタデータポートの起点は `9100` です。

## 2. モデルの配置（初回のみ・現在は配置済み）

`model.path` は **カード上のファイルの絶対パス** です。`run.sh` が転送するのは設定 YAML のみで、モデルは自動転送されません。

現在の `config.local.yaml` では次のパスを使用します。

```yaml
model:
  path: /home/sima/models/yolo26n-det-int8-b1.tar.gz
```

カードにモデルがない場合は、ホストから転送します。

```bash
ssh -i ~/.ssh/sima_neat_pcie_ed25519 sima@10.0.0.2 \
  'mkdir -p /home/sima/models'

scp -i ~/.ssh/sima_neat_pcie_ed25519 \
  /home/shinko/pcie_work/models/yolo26n-det-int8-b1.tar.gz \
  sima@10.0.0.2:/home/sima/models/yolo26n-det-int8-b1.tar.gz
```

配置を確認します。

```bash
ssh -i ~/.ssh/sima_neat_pcie_ed25519 sima@10.0.0.2 \
  'ls -lh /home/sima/models/yolo26n-det-int8-b1.tar.gz'
```

## 3. 起動

```bash
cd /home/shinko/pcie_work/apps
APP_DIR=examples/object-detection/pcie-high-density-multi-stream-object-detector

./${APP_DIR}/run.sh \
  --config "${APP_DIR}/src/common/config.local.yaml"
```

ホスト設定の検証、設定のカードへの転送、カード設定の検証、カードアプリの起動が順に実行されます。カードの `Graph ready` を待ってからホストアプリが起動します（待機上限は既定で 120 秒）。

次のメッセージがホストアプリ起動の目印です。

```text
Card graph is ready. Starting host application...
Press Ctrl+C to stop the host and card applications.
```

## 4. 停止

起動したターミナルで `Ctrl+C` を押します。ホストアプリ、カードアプリの順に停止します。再起動する場合も、この手順で停止してから `run.sh` を再実行します。

## 5. 起動に失敗した場合

カードログは停止後も残ります。

```bash
ssh -i ~/.ssh/sima_neat_pcie_ed25519 sima@10.0.0.2 \
  'tail -n 100 /home/sima/tmp/pcie-high-density/card.log'
```

`archive path does not exist or is not a regular file` と表示された場合は、`model.path` がカード上の実在するファイルを指しているか確認してください。今回の `/workspace/models/yolo26n-det-int8-b1.tar.gz` に関するエラーは、そのパスにカード上のモデルがなかったことが原因です。現在は上記の `/home/sima/models/` に配置し、設定を修正済みです。

ホストバイナリがない場合は、依存パッケージを導入した環境でビルドします。依存パッケージについては [README.md](README.md) を参照してください。

```bash
cd /home/shinko/pcie_work/apps
APP_DIR=examples/object-detection/pcie-high-density-multi-stream-object-detector
cmake -S "${APP_DIR}/src/host" -B build-host-pcie -DCMAKE_BUILD_TYPE=Release
cmake --build build-host-pcie --parallel
```

接続先やバイナリパスなどの変更オプションは、次のコマンドで確認できます。

```bash
/home/shinko/pcie_work/apps/examples/object-detection/pcie-high-density-multi-stream-object-detector/run.sh --help
```
