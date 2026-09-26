# 起動方法

詳細な設計・工程記録は [README.md](README.md) を参照。ここでは実行手順だけをまとめる。
すべて `/workspace/Yolo_panoptic` で、`dk`(DevKit実行ヘルパー)経由で実行する。

## 1. 初回のみ: USBカメラの検出・設定

```bash
dk ./probe_camera.py                                 # USBカメラを検出(内蔵ISPノードは除外)
dk ./probe_camera.py --device /dev/videoNN            # 対応フォーマット/解像度/fpsを一覧
dk ./probe_camera.py --device /dev/videoNN --apply    # config/default.conf に device/解像度/fps を反映
```

`config/default.conf` にはすでに動作確認済みの設定(`/dev/video96`, 1280x720@30)が入っているため、
カメラの配線を変えていなければこの手順は不要。

## 2. 本番起動

```bash
dk ./main.py --config ./config/default.conf --frames 300   # 有限フレームでのスモークテスト(推奨・まずこちら)
dk ./main.py --config ./config/default.conf                # frames=0 (config既定) で無制限実行、Ctrl-Cで停止
```

## 3. 映像を見る

`config/default.conf` の `udp_host` に指定したPC側で:

```bash
gst-launch-1.0 -v udpsrc port=5206 \
  caps="application/x-rtp,media=video,encoding-name=H264,payload=96" \
  ! rtpjitterbuffer ! rtph264depay ! h264parse ! avdec_h264 \
  ! videoconvert ! autovideosink sync=false
```

(このコマンドは `main.py` 起動時にも標準出力に表示される)

## カメラが手元にない場合

静止画をループ入力して、カメラ以外の全経路(2モデル推論→パノプティック統合→映像送信)を検証する:

```bash
dk ./main.py --config ./config/no_camera_test.conf --frames 30
```

## 出力画像を直接JPGで確認したい場合

```bash
dk ./main.py --config ./config/default.conf --frames 30 --save-every 10 --save-dir ./captures
```

`captures/` は NFS 共有ディレクトリなので、DevKit 側で生成された画像を PC 側からそのまま開ける。
`frame_NNNNN_raw.jpg`(カメラ生画像)と `frame_NNNNN_panoptic.jpg`(パノプティック合成後)が保存される。

## 主要な設定項目(`config/default.conf`)

| キー | 内容 |
|---|---|
| `camera_device` / `width` / `height` / `fps` | カメラデバイスと解像度/fps |
| `yolo_score_threshold` / `yolo_nms_iou` | YOLO検出のしきい値 |
| `student_interval` | stuffモデルの実行間隔(1=毎フレーム) |
| `merge_downscale` | パノプティック統合の縮小率(既定6、大きいほど高速・粗い) |
| `udp_host` / `udp_port` | 映像出力先 |
| `frames` | 処理フレーム数(0=無制限) |

実測パフォーマンス(1280x720@30、実USBカメラ): **約6.9fps**。詳細は README.md の Step 4 節を参照。
