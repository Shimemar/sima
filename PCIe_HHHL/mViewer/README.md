# mViewer

![mViewer screenshot](PCIe_HHHL/mViewer/Screenshot%20from%202026-10-02%2021-37-51.png)

PCIe検出アプリの出力を直接表示する場合は、下記の「検出結果を直接受信する」を参照してください。

UbuntuのデスクトップでRTSP映像を受信・表示するGTK 3 / GStreamerアプリです。
音声は再生しません。PCIeカードやNeat SDKは不要です。

## インストール

```bash
sudo apt update
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-gstreamer-1.0 \
  python3-gi-cairo python3-cairo python3-yaml gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-gtk3 gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-libav
```

UbuntuのシステムPythonを使用します。pipや仮想環境の設定は不要です。

## 検出結果を直接受信する

`detection_viewer.py` は `pcie-high-density-multi-stream-object-detector` の
ホストアプリが出力するRTP/H.264映像とJSON検出結果を直接受信します。
GTK/Cairoで映像、検出枠、ラベル、信頼度を描画します。InsightのWebビューアーは使用しません。
RTSPの入力元映像を再取得する方式ではなく、検出アプリから出力された映像を使用します。

まずビューアーを起動します。

```bash
cd /home/shinko/pcie_work/mViewer
./run_detection.sh
```

別のターミナルで、**検出アプリも同じ設定ファイルを指定して**起動します。
実行中の検出アプリがあれば、そのアプリを起動したターミナルでCtrl+Cを押して停止してから実行してください。
PCIeカードとRTSP入力の配信は、対象アプリの通常の起動手順と同じです。
この環境ではInsightのMedia SourceがRTSP入力を配信しているので、その配信は継続してください。

```bash
cd /home/shinko/pcie_work/apps
./examples/object-detection/pcie-high-density-multi-stream-object-detector/run.sh \
  --config /home/shinko/pcie_work/mViewer/config.detector.yaml
```

付属の `config.detector.yaml` は既存の `config.local.yaml` を元に作成した48ストリーム設定です。
送信先は127.0.0.1、映像ポートを19000〜19047、JSONポートを19100〜19147に設定し、
ラベルのパスはホスト上の絶対パスにしています。モデルのパスはカード上のパスです。
元の設定ファイルは変更していません。

| 出力 | 送信形式 | チャネル0 | チャネルn |
| --- | --- | --- | --- |
| 映像 | H.264 RTP/UDP、payload type 96、90kHz | 19000 | 19000+n |
| 検出結果 | `object-detection` JSON/UDP | 19100 | 19100+n |

設定名の `output.insight` は対象アプリが使用する送信先設定の名称です。
その送信先を本ビューアーに指定します。この設定で送られる結果はInsightの受信ポートには届きません。

### 操作と表示設定

- 既定では左側にチャネル0〜31のサムネイルを縦に並べ、選択したチャネルを右側に大きく表示します。
  サムネイルをクリックして切り替えます。上下キーでの選択にも対応します。
  ウィンドウが小さい場合は左側をスクロールできます。中央の境界をドラッグして幅を調整できます。
  サムネイルの高さは映像の縦横比を維持して横幅から自動計算します。
- 「ゆっくり自動スクロール」をオンにするとサムネイル一覧が上下に往復します。
  速度は既定20px/秒、5〜100px/秒で調整できます。オフにすると止まります。
  自動スクロール中は一覧の一番上に見えているチャネルを右側に表示します。
  上端に一部だけ見えているチャネルも対象です。オフにするとクリックで選択できます。
- サムネイルと拡大映像は同じ受信・デコード結果を使用し、どちらにも検出枠を描画します。
- チャネルは0始まりです。`0-3`、`0,4,8`、`0-47`のように指定できます。
- GUIのチャネル入力欄と「受信開始 / チャネル変更」で受信対象を変更します。
- 「停止」でソケットとデコーダーを解放します。「全画面」で拡大、Escで解除します。
- しきい値で描画する検出結果を絞り込めます。推論側ですでに除外された結果は復元できません。
- デコード後350ms待ち、**同一RTPタイムスタンプの結果だけ**を描画します。
  対応する結果がないフレームには枠を表示しません。空の検出結果は枠を消します。
- 推論結果が遅れる場合は `--delay 700` などで表示待ち時間を増やします（上限2000ms）。
- 推論されなかったフレームには枠が出ません。推論の間引きがある場合、枠が断続的に表示されます。
- CH欄に映像・結果の受信数と同期状態を表示し、ツールチップに不正JSON数を表示します。
- 最初は送信側のウォームアップ30結果とH.264キーフレームを待つ必要があります。
- CPUでデコードします。48チャネル設定に対応していますが、同時表示できる数はCPUとメモリに依存します。
  負荷が高い場合は `--channels 0-3` で受信数を減らしてください。指定したチャネルのみデコードします。

```bash
./run_detection.sh --channels 0-7
./run_detection.sh --channels 16-19 --delay 700
./run_detection.sh --layout grid --channels 0-15 --columns 4
```

別PCでビューアーを動かす場合は、検出アプリに渡す設定の `output.insight.host` を
ビューアーPCのIPアドレスに変更し、受信側のUDPポートを利用可能にしてください。
`--bind` はローカルの受信アドレスです。既定値は `0.0.0.0` です。

既存の9000/9100番台で受信することもできますが、Insightの受信ポートと競合しない環境が必要です。
ポートを共有してパケットを取り合うことを避けるため、本ビューアーは排他的にバインドします。

```bash
./run_detection.sh \
  --config /home/shinko/pcie_work/apps/examples/object-detection/pcie-high-density-multi-stream-object-detector/src/common/config.local.yaml \
  --channels 0-3
```

### 検証

```bash
cd /home/shinko/pcie_work
/usr/bin/python3 -m unittest discover -s mViewer -v
```

プロトコル検証、キャッシュ上限、RTPタイムスタンプ一致、結果の期限切れ、ポート競合を検証します。
GUIセッションでは2チャネルのH.264 RTPと150ms遅延JSONを送信し、
映像デコード、実際の枠描画、空の検出結果による枠消去、停止後のポート解放も検証します。
テスト画像は `/tmp/mviewer-detection-overlay.png` に生成されます。
PCIe実機を使用するテストではありません。

## 起動

```bash
cd /home/shinko/pcie_work/mViewer
./run.sh
```

URLを入力して「接続」を押します。認証がある場合は
`rtsp://user:password@192.168.1.100:554/stream` の形式で指定します。
ユーザー名・パスワードの特殊文字はURLエンコードしてください。
URLは初期状態では非表示で、保存しません。

コマンドラインから接続することもできます（URLがシェル履歴やプロセス一覧に残るため、認証付きURLはGUI入力を推奨）。

```bash
./run.sh rtsp://192.168.1.100:554/stream
./run.sh --transport udp --latency 100 rtsp://192.168.1.100:554/stream
```

- TCPが既定値です。UDPはネットワークや配信側に応じて選択してください。
- バッファは0〜5000ms、既定値は200msです。小さくすると遅延が減りますが映像が乱れる場合があります。
- エラーや配信終了時は3秒後に自動再接続します。チェックを外すと次のエラーで停止します。
- 「切断」で受信・再接続を停止します。設定変更は切断後に行います。
- 「全画面」で全画面表示、Escで解除します。
- H.264 / H.265など、インストール済みのGStreamerデコーダーで処理できる映像に対応します。

## 接続できない場合

配信側のRTSP URL、認証情報、配信状態、ファイアウォールを確認してください。
SSHのみの環境では表示できません。UbuntuのGUIセッションから起動してください。

## 現在のInsightの映像を表示する

この環境のInsightは映像と検出メタデータをWebRTCで配信します。
検出枠などを含む表示には、Insight自身のビューアーをアプリウィンドウで開きます。
Chrome / Chromiumがある場合はアプリモード、その他の場合は既定ブラウザーで開きます。

```bash
./run_insight.sh 0
./run_insight.sh 1
./run_insight.sh 0 192.168.1.100
```

チャネル番号は0〜47です。第2引数はInsightホスト（既定: 127.0.0.1）。
Insightと映像送信側のアプリを起動しておく必要があります。
初回に証明書確認が表示されたら、Insightの証明書を通常の手順で信頼してください。
このモードでは映像・検出枠の描画をInsightのWebビューアーが行います。

入力元のRTSP映像だけを表示する場合は以下を使用します。
現在、src1〜src16の配信が起動しています。検出枠は含まれません。

```bash
./run.sh rtsp://127.0.0.1:8554/src1
```

RTSPのsrc番号とInsight出力チャネル番号は別です。
現在の48ストリーム設定ではsrc1〜src16が3回登録されています。
