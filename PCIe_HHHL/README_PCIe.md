# PCIe 作業記録

最終確認日: 2026-09-15  
作業ディレクトリ: `/home/shinko/pcie_work`

## 概要

Modalix PCIe Card を Ubuntu ホストから利用するため、SiMa Neat PCIe Host 0.4.0 の導入、Python/C++ チュートリアルの確認、および PCIe 高密度マルチストリーム物体検出アプリの構築・実行を試行した。

本書はシェル履歴、配置済みファイル、インストール済みパッケージ、および現在のホスト状態から作業内容を整理したものである。実行ログが残っていない操作については、成功済みと断定せず「試行」と記載する。

## 1. PCIe Host パッケージの準備と導入

以下の PCIe Host 0.4.0 関連成果物を取得・配置した。

- `sima-pcie-host_0.4.0_amd64.deb`: ランタイムパッケージ
- `sima-pcie-host-dev_0.4.0_amd64.deb`: C++ 開発パッケージ
- `pyneatpcie-0.4.0-cp310-cp310-linux_x86_64.whl`: Python 3.10 バインディング
- `sima-pcie-host-0.4.0-Linux-amd64-extras.tar.gz`: チュートリアル、テスト、サンプルを含む追加パッケージ

`sima-cli neat install core/pciehost/ubuntu22/amd64@v0.4.0` および extras の導入を複数回試行した。また、ローカル成果物から一括導入できる `install_pciehost.sh` を配置した。このスクリプトは次の処理に対応している。

- runtime/dev パッケージのインストール
- Python 仮想環境の作成と `pyneatpcie` wheel の導入
- `pcie-setup.sh` の実行またはスキップ
- Neat PCIe アプリケーションビルダースキルの導入

現在の確認結果:

- `sima-pcie-host` 0.4.0: インストール済み
- `sima-pcie-host-dev` 0.4.0: インストール済み
- `nlohmann-json3-dev` 3.10.5-2: インストール済み
- Python 仮想環境: `/home/shinko/pyneatpcie`
- `pyneatpcie` の import: 成功

Python 環境を利用する場合:

```bash
source /home/shinko/pyneatpcie/bin/activate
python3 -c "import pyneatpcie; print('pyneatpcie ready')"
```

## 2. PCIe カードと DevKit の確認

`lspci` を複数回実行して PCIe デバイスの認識を確認した。現在は次のデバイスが見えている。

```text
01:00.0 Processing accelerators [1200]: Device [1f06:0123] (rev 01)
```

DevKit `10.0.0.2` に対して以下も実施した。

- `sima-cli sdk setup --devkit 10.0.0.2`
- `ssh sima@10.0.0.2` による接続確認
- `prebuilt-apps` の DevKit への `scp` 転送試行

また、Neat SDK/Insight 環境を起動し、Insight のビデオチャネル数を 48 に設定した。

```bash
sima-cli sdk setup --insight-video-channels 48
sima-cli sdk neat
```

## 3. モデルの取得

Model Zoo またはダウンロード機能を使い、次のモデルを取得した。

- YOLOv8s のコンパイル済みモデル: `sima-pcie-host-0.4.0-Linux-amd64-extras/yolo_v8s_mpk.tar.gz`
- YOLO26n INT8 batch 1: `models/yolo26n-det-int8-b1.tar.gz`
- ResNet-50: `sima-cli modelzoo get resnet_50` を実行

YOLOv8s モデルは PCIe チュートリアルから参照できる位置にもコピーした。

## 4. PCIe チュートリアル

展開済み extras の次のチュートリアルを対象に、Python 実行および C++ ターゲットのビルド・実行を試行した。

作業場所:
```text
/home/shinko/pcie_work/sima-pcie-host-0.4.0-Linux-amd64-extras
```

実施した内容:

- Tutorial 024: tensor mode
  - Python: `run_tensor_mode.py`
  - C++: `tutorial_024_run_tensor_mode`
- Tutorial 024: image mode
  - C++ ターゲットをビルド
- Tutorial 024: image mode + box decode
  - C++ ターゲットをビルド・実行
- Tutorial 025: 非同期 PCIe 推論
  - Python: `run_pcie_inference_async.py`
  - C++: `tutorial_025_run_pcie_inference_async`
- Tutorial 026: 複数モデル実行用のサンプルとバイナリを確認

代表的なコマンド:

```bash
cd /home/shinko/pcie_work/sima-pcie-host-0.4.0-Linux-amd64-extras
./build.sh --list-targets
./build.sh --target tutorial_024_run_tensor_mode
./build.sh --target tutorial_024_run_image_mode
./build.sh --target tutorial_024_run_image_boxdecode
./build.sh --target tutorial_025_run_pcie_inference_async

source /home/shinko/pyneatpcie/bin/activate
python3 share/sima-pcie-host/tutorials/024_run_your_first_model_over_pcie/run_tensor_mode.py
python3 share/sima-pcie-host/tutorials/025_run_pcie_inference_async/run_pcie_inference_async.py
```

配布済みの実行バイナリは `lib/sima-pcie-host/tutorials/` に存在する。ただし、各推論の出力結果や性能値を示す保存ログは確認できなかったため、カード上での推論成功および性能は本書では未確認とする。

## 5. PCIe 高密度マルチストリーム物体検出

Neat Apps を取得し、`pcie-high-density-multi-stream-object-detector` のホストアプリを対象に作業した。

実施した内容:

1. `sima-cli neat install apps` および GitHub からの apps 取得を試行
2. CMake による Release ビルドを試行
3. ビルド依存関係として `nlohmann-json3-dev` を導入
4. `run.sh` からローカル設定ファイルを指定して実行を試行
5. 48チャネル用に Neat Insight を設定

使用した主なコマンド:

```bash
APP_DIR=examples/object-detection/pcie-high-density-multi-stream-object-detector
cmake -S "$APP_DIR/src/host" -B build-host-pcie -DCMAKE_BUILD_TYPE=Release
cmake --build build-host-pcie --parallel

./examples/object-detection/pcie-high-density-multi-stream-object-detector/run.sh \
  --config examples/object-detection/pcie-high-density-multi-stream-object-detector/src/common/config.local.yaml
```

現在配置されている設定は `48x720p10fps` プロファイルで、主な値は次のとおり。

- 入力: H.264、1280x720、10 FPS、48ストリーム想定
- PCIe card ID / queue: `0 / 0`
- PCIe queue size: 256
- PCIe buffer size: 4 MiB
- 全体の最大 in-flight 数: 8
- 推論 queue depth: 16
- スコア閾値: 0.30
- NMS IoU: 0.60
- 最大検出数: 50

ただし、現状の設定には次の未設定項目が残っている。

- `model.path` が `<model-path>` のまま
- `streams` が空で、必要な48個の URL が未登録
- Insight の `host` が `<insight-host-ip>` のまま
- 現在の作業ツリーには `build-host-pcie` ビルドディレクトリが見当たらない

このため、48ストリーム構成の最終的な動作成功は未確認である。

## 6. Codex 用 PCIe アプリケーションビルダースキル

Neat Core リポジトリを `/home/shinko/pcie_work/core` に clone し、以下のスキルを Codex のスキル領域へコピーした。

```text
core/pcie_host/skills/neat-pcie-application-builder
```

このスキルでは、ホスト側アプリケーションに以下の API を使用する方針になっている。

- C++: `simaai::neat::pcie::Model`
- Python: `pyneatpcie.Model`
- 同期推論: `run()`
- パイプライン推論: `push()` / `pull()`

DevKit 内の通常の Neat API や `pcie::Runtime` ではなく、インストール済み PCIe Host の公開 `Model` API を使用する。

## 7. 現在の状態と次に必要な作業

ホスト側の PCIe runtime/dev パッケージ、Python バインディング、および PCIe デバイス認識までは確認できている。一方、実推論結果の保存ログと48ストリームアプリの完成済み設定は確認できない。

次回は以下を実施すると作業を継続できる。

1. `config.local.yaml` にコンパイル済みモデルの絶対パスを設定する。
2. 48本の有効な RTSP URL を `streams` に登録する。
3. Insight ホストの IP アドレスを設定する。
4. ホストアプリを再ビルドする。
5. PCIe カード接続状態で実行し、標準出力・エラー出力・FPS・検出結果をログへ保存する。
6. Tutorial 024/025 も再実行し、カード上での成功結果をログとして残す。

## 注意事項

- 本書には認証情報や秘密鍵の内容は記載していない。
- シェル履歴だけではコマンドの終了コードや推論結果を復元できないため、結果ログがないものは「試行」扱いとしている。
- 実際のモデル推論を成功と判断するには、PCIe カード接続下での実行結果を改めて確認する必要がある。
