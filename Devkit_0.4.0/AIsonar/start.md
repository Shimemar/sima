# 起動方法

## 1. 事前確認（ホスト PC の Neat SDK シェルで）

```bash
cd /workspace/AIsonar
dk status                      # "SSH status : reachable" であること
insight-admin status           # Insight が Running であること
```

webcam が DevKit の USB に挿さっていることを確認する。
挿し直した直後や映らないときは、デバイス番号を確認する:

```bash
dk ./tools/probe_camera.py     # "== /dev/videoN (MJPEG capable)" の N を確認
```

`/dev/video0` 以外になっていたら、`config/default.conf` の `camera_device=` を書き換える
（または起動時に `--camera-device /dev/videoN` を付ける）。

## 2. 起動

まず短時間（300 フレーム、約 10 秒）で動作確認する:

```bash
dk ./main.py --frames 300
```

問題なければ連続運転:

```bash
dk ./main.py
```

次の行が出れば起動完了（モデルの読み込みは 1 秒未満）:

```text
Video: 1280x720@30 H.264/RTP -> udp://10.42.0.1:9000
[150] out=31.0fps depth=19.0fps infer=7.7ms render=15.7ms sent=150 dropped=0
```

よく使うオプション:

| オプション | 内容 |
|---|---|
| `--frames N` | N フレームで終了（0 = 無限。30 フレーム ≒ 1 秒） |
| `--camera-device /dev/videoN` | カメラを指定 |
| `--udp-port 9001` | Insight のチャンネルを変更（チャンネル = ポート − 9000） |
| `--snapshot output/sonar.png` | 終了時に最後の画面を PNG 保存 |
| `--calibrate` | 距離換算の校正用に、画面中央の逆深度を 1 秒ごとに表示（README「距離の扱いと校正」） |
| `--no-video` | Insight へ送らない（描画と計測のみ） |

## 3. Insight で見る

ブラウザで Insight の Video Viewer を開き、**チャンネル 0** を表示する:

```text
https://<ホストPCのIP>:8081/static/viewer.html?mode=light&src=0
```

（Insight UI `https://<ホストPCのIP>:9900` → Video Viewer からでも可）

映らないときは、映像が Insight に届いているか確認する:

```bash
curl -sk https://127.0.0.1:9900/api/ingest/stats
```

`channels` に channel 0 があり、`rtp.packets_received` が増えていれば届いている
（届いていればブラウザの再読み込みを試す）。

## 4. 画面の見方

- 右: ソナー（PPI）。下中央が自分（カメラ）、真上が正面。扇の開き = カメラの水平画角（65°）
  - 緑の点・弧 = エコー（物体）。中心から遠いほど遠い。リングは 1 m ごと（目安）
  - スイープ線が左右に往復し、通過した方位のエコーが光ってからゆっくり消える
  - `TGT` の枠 = 最も近い物体。`alert_range_m`（既定 0.7 m）より近づくと赤くなり `PROXIMITY ALERT` が点滅
- 左上: カメラ映像、左中: 深度マップ（明るいほど近い）。緑の横枠が距離を拾っている帯
- 左下: 出力 fps、深度更新 fps、MLA 推論時間、最近目標の方位・距離

試し方: カメラの前で左右に動くと、エコーと `TGT` 枠が同じ方位に動く。カメラに近づくと中心へ寄り、
0.7 m より近いとアラートが出る。

## 5. 停止

`dk` で起動した場合は Ctrl-C で停止する。最後に `Done: ... frames` が出れば正常に終了している。

Ctrl-C で止まらない、または DevKit 側にプロセスが残ったときは（残っていると次回起動時にカメラ・MLA を使えない）:

```bash
dk ./tools/stop.py             # "stopped" または "main.py is not running" と出れば OK
```

## 困ったとき

| 症状 | 対処 |
|---|---|
| `カメラ /dev/video0 を開けません` / 左に `WAITING FOR CAMERA...` | 他のアプリがカメラを使っていないか確認（`dk ./tools/probe_camera.py` の python processes）。デバイス番号も確認 |
| Insight に映らない | 3 の `ingest/stats` を確認。他アプリが同じチャンネルに送っていたら `--udp-port 9001` など |
| 起動が途中で止まる | 前回のプロセスが残っていないか確認して停止する（5 を参照） |
| 編集した設定やコードが反映されない | `rm -rf __pycache__` を実行してから再起動（NFS のキャッシュ） |
| 深度の fps が 12〜19 しか出ない | 正常。カメラ（Anker C200）自体の上限。出力は 30 fps で動く |
| 机や床がいつも最近目標になる | `config/default.conf` の `band_top` / `band_bottom` を小さく（帯を上へ） |
| 距離の数字が実際と合わない | README「距離の扱いと校正」の手順で `depth_scale` / `depth_shift` を合わせる |
