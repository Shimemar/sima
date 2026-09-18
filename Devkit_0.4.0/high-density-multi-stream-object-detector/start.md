# 起動方法(この環境での実績手順)

この環境(`/workspace` = DevKit と NFS 共有済み、Insight は `10.42.0.1:9900` で稼働中、
モデル・RTSPソースとも準備済み)で実際に動作確認した16ストリーム構成の起動手順。
詳細な背景・チューニングの説明は `README.md` を参照。

## 0. 前提の確認(初回のみ)

```bash
# DevKit 到達性
dk status

# Insight 稼働確認
insight-admin status
curl -sk https://127.0.0.1:9900/api/health

# モデルパッケージの有無(config.yaml の model.path が指す場所)
ls examples/object-detection/high-density-multi-stream-object-detector/src/common/models/

# RTSPソース(src1〜src16)が仕様(h264 / 1280x720 / 30fps)通りか
for i in $(seq 1 16); do
  echo -n "src$i: "
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,width,height,avg_frame_rate \
    -of default=noprint_wrappers=1:nokey=1 "rtsp://10.42.0.1:8554/src$i"
done
```

`neat --json` の `exposedPorts` で `videoUDP`(9000起点)/ `metadataUDP`(9100起点)/
`rtsp.tcp`(8554)が `config.yaml` の `output.insight.*` と一致していることも確認しておく。

## 1. 変数を設定

`prebuilt-apps/` ルートから実行する。

```bash
cd /workspace/prebuilt-apps
APP_DIR=examples/object-detection/high-density-multi-stream-object-detector
APP=./${APP_DIR}/src/cpp/pre-built/high-density-multi-stream-object-detector
CONFIG="$APP_DIR/src/common/config.yaml"   # 16ストリーム/720p/30FPS の既定プロファイル
```

他プロファイルを使う場合:

```bash
CONFIG="$APP_DIR/src/common/config-24x720p20fps.yaml"   # 24ストリーム/20FPS
CONFIG="$APP_DIR/src/common/config-48x720p10fps.yaml"   # 48ストリーム/10FPS
```

## 2. 設定だけ検証(RTSP/Insightは起動しない)

```bash
dk "$APP" --config "$CONFIG" --validate-config-only
```

`Config validated: ... (streams=16, ...)` が出れば OK。

## 3. 本起動

`dk` は ARM64 バイナリをリモート(DevKit)で実行するラッパー。
フォアグラウンドで動かし続けるアプリなので、ログを取りつつバックグラウンドで起動するのがおすすめ:

```bash
LOG=/tmp/hd16.log
dk "$APP" --config "$CONFIG" > "$LOG" 2>&1 &
```

起動ログで次を確認する:

```
[4/4] Graph ready (...)
[stream 0] rtsp=.../src1 stream=1280x720@30 insight=10.42.0.1 video=9000 metadata=9100
...
[stream 15] rtsp=.../src16 ... video=9015 metadata=9115
```

## 4. 稼働確認(Insight ingest 統計)

ブラウザを開かなくても、Insight の ingest 統計 API で 16 チャンネル全てが
期待レート(映像・メタデータとも約30fps)で動いているか確認できる:

```bash
curl -sk 'https://127.0.0.1:9900/api/ingest/stats?all=1&verbose=1' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
for ch in d['channels'][:16]:
    rtp=ch['rtp']; md=ch['metadata']
    print(f\"ch{ch['channel']:2d} active={ch['active']} video_pps={rtp['packet_rate_pps']:.0f} meta_active={md['active']} meta_mps={md['message_rate_mps']:.1f}\")
"
```

全チャンネルで `active=True` かつ `meta_mps` が選択プロファイルのFPS(16本構成なら約30)に
近ければ検出結果も流れている。

## 5. Insight で映像・検出結果を見る

ブラウザで `https://10.42.0.1:9900`(または実行マシンのIP)を開き、Video Viewer で
チャンネル 0〜15 を確認する。**ビューワーは同時に1つだけ**開くこと
(複数同時接続だとボックス表示が間欠的になる既知の制限があるため)。

## 6. 停止

フォアグラウンドで動かしている場合は起動したターミナルで `Ctrl-C`。

`dk` はローカル側で INT/TERM を受けるとリモートの対象プロセスを `pkill`
(TERM→1秒後KILL)で確実に止めるようになっている。バックグラウンドで起動した場合は、
その `dk` プロセス(上記の `&` で起動したジョブ)へ `SIGTERM`/`SIGINT` を送れば同様に
リモート側もクリーンに停止する。

```bash
# バックグラウンドジョブの PID を確認して送る例
kill -INT %1        # ジョブ番号指定の場合
# もしくは
kill -TERM <dkのPID>
```

## トラブル時

- チャンネルが起動しない/途中で消える → `README.md` の「Expected Result」の通り、
  publisher (RTSPソース) が先に起動しているか、選択したプロファイルとソース本数・解像度・FPSが
  一致しているか確認し、publisher再起動後にアプリを再起動する。
- ボックスが出ない/間欠的 → Insight ビューワーを1つだけにしているか確認。
