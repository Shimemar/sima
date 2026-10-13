# AI SONAR: Webカメラ単眼深度推定によるソナー風表示（Modalix DevKit）

`SRD.md` の実装。DevKit の USB カメラ映像から MiDaS v2.1 small で深度推定（MLA）し、
画像の列 = 方位角、列ごとの最近距離 = レンジ として潜水艦ソナー（PPI）風に描画、
H.264/RTP(UDP) でホストPCの Insight に送って表示する。

> **検証状況（2026-10-13, Modalix DevKit / NEAT 2.1 / Model Compiler 2.1, Anker PowerConf C200）:**
> 以下の数値はすべて実機で測ったもの。

## 実行

手順の詳細（事前確認・Insightでの見方・停止・トラブル対処）は [start.md](start.md)。

```bash
cd /workspace/AIsonar
dk ./main.py --frames 300            # 有限フレームでの動作確認（まずこれ）
dk ./main.py                         # 連続実行
dk ./main.py --frames 200 --snapshot output/sonar.png   # 最後のフレームをPNG保存
dk ./main.py --calibrate --frames 900                   # 距離換算の校正（後述）
```

表示: Insight の Video Viewer でチャンネル 0（`https://<ホストIP>:8081/static/viewer.html?mode=light&src=0`）。
送り先は `config/default.conf` の `udp_host=10.42.0.1` / `udp_port=9000`（= Insight チャンネル0）。
他アプリとチャンネルが重なるときは `--udp-port 9001` などで変える（Insight が受けるのは 9000〜9047）。

`dk` は pty なしで動くので Ctrl-C でリモート側が残ることがある。その場合は
`dk ./tools/stop.py`。アプリは SIGINT/SIGTERM で正常終了する。

### なぜ RTSP ではなく RTP/UDP か
pyneat の公開APIには毎フレーム更新されるRTSPサーバ出力が無い（`Graph.run_rtsp()` は開始時の1枚を
ループ送出するだけ）。Insight は UDP 9000+ch で H.264/RTP を受けてブラウザに WebRTC で出すので、
このワークスペースの他のアプリと同じく `VideoSenderOptions.h264_rtp_udp_from_raw` で送っている。

## 構成

```text
USBカメラ /dev/video0 (640x360 MJPG, camera.py が最新1枚を保持)
  → [depth スレッド] カメラの新フレームごと (FR-01)
      前処理 256x256 + ImageNet正規化 (CPU, 約8 ms)
      → MiDaS v2.1 small int8 (MLA, 約7 ms)  … 相対逆深度 256x256
      → 中央の水平帯の各方位ビンで最近距離 (90パーセンタイル) → 距離[m] 換算 → EMA (FR-03〜06)
  → [main スレッド] out_fps=30 の一定周期 (推論と非同期なのでスイープが滑らか)
      PPI描画: 扇形・距離リング・方位線・往復スイープ・エコー残光・ターゲットロック (FR-07〜11)
      + 左側にカメラ/深度の小窓と計測値 → NV12 → H.264/RTP UDP → Insight
```

| ファイル | 内容 |
|---|---|
| `main.py` | 設定読込、スレッド構成、映像送出、計測ログ |
| `depth.py` | 前処理と MLA 推論ラッパ（`MidasMla`） |
| `sonar.py` | 距離プロファイル抽出（`RangeProfile`）と PPI 描画（`SonarRenderer`） |
| `camera.py` | V4L2 カメラ読み取りスレッド（`../AvatarCreator/AnimeGAN` から流用 + `latest_stamped()`） |
| `config/default.conf` | 全パラメータ（各値の理由をコメントに記載） |
| `tools/mla_surgery.py` | MiDaS ONNX → MLA 向け ONNX（開発PC） |
| `tools/collect_calib.py` | 量子化キャリブレーション画像をカメラから収集（DevKit） |
| `tools/mla_infer.py` | MLA版と CPU(ORT)版の精度・速度比較（DevKit） |
| `tools/camera_fps.py` | カメラの実fpsと前処理時間の計測（DevKit） |
| `tools/stop.py` / `tools/probe_camera.py` | 残ったプロセスの停止 / カメラのデバイス番号確認（DevKit） |

## 実測性能（DevKit）

| 項目 | 値 |
|---|---|
| MLA 推論（MiDaS small 256, int8） | 平均 7.2 ms（p95 8.3 ms）。CPU(ORT) は 223 ms → 約30倍 |
| 深度スレッド 1フレーム（前処理込み） | 約18 ms |
| 深度更新レート | 15〜19 fps（**カメラ律速**: C200 は 640x360 で約18fps、1280x720 で約13fps） |
| 描画 + NV12変換 | 平均 17 ms（p95 22 ms） |
| 出力 | 1280x720 @ 30 fps, 約4 Mbps, ドロップ 0 |

NFR-03 の目標値: 出力 30fps / 深度 15fps 以上（カメラの上限）を達成。C200 のフレームレート上限は
ファームウェア側の制限で、ソフトでは上げられないことを `../Scouter/README.md` で確認済み。

## モデル（Step 2）

MiDaS v2.1 small 公式ONNX（`https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx`、
入力 1x3x256x256）をそのまま使うと、入力正規化・非対称Pad・Squeeze が入っているので書き換えてから
コンパイルした。結果は **全ノードMLA（MLA:1, EV74:4 前後処理, A65:0）**。

```bash
# 1. 書き換え（ORTで元モデルとの差 0.0 を自己検証）
source /sdk-extensions/model-compiler/bin/activate
python3 tools/mla_surgery.py assets/midas_v21_small_256.onnx assets/midas_v21_small_256_mla.onnx
# 2. キャリブレーション画像（DevKitカメラ、256x256 に引き伸ばし。本番の前処理と同じ）
dk ./tools/collect_calib.py --count 60 --interval 0.3
# 3. 量子化・コンパイル（int8、約3分）
python3 ~/.claude/skills/sima-model-quantize-compile/scripts/quantize_compile.py \
  --model_path $PWD/assets/midas_v21_small_256_mla.onnx --model_format onnx \
  --input_names input --input_shapes 1,3,256,256 --output_names depth \
  --device modalix --build_dir $PWD/build \
  --real_data --dataset_images $PWD/calib --num_calib_samples 60 \
  --mean 0.485 0.456 0.406 --std 0.229 0.224 0.225
cp build/midas_v21_small_256_mla/midas_v21_small_256_mla_mpk.tar.gz assets/
# 4. 実機で CPU 版と比較
dk ./tools/mla_infer.py
```

| 書き換え | 理由 |
|---|---|
| 入力直後の `Sub(mean)/Div(std)` を削除 | 正規化はホスト側（`depth.preprocess`）とキャリブレーションの `--mean/--std` で扱う |
| 定数 `Pad` ×5 → 直後の `Conv` の `pads` 属性に統合 | EfficientNet系の TF-SAME 非対称パディング。Pad を独立ノードで残さない |
| 末尾 `Squeeze` を削除 | 出力を 4D (1,1,256,256) に |
| opset 10 → 13、`Resize` に `coordinate_transformation_mode=asymmetric` を明示 | **変換だけだと既定の half_pixel になり出力が最大187ずれる**（opset10の Resize は暗黙に asymmetric） |

量子化後の精度（`tools/mla_infer.py`、カメラ画像5枚）: CPU版との相関 0.990〜0.995、
平均絶対差は出力レンジの 3.3〜4.5%。深度マップを並べても目視で差は分からない（`output/depth_compare.png`）。
int8 で十分だったので bf16 は試していない。

ランタイムは前処理なしの Tensor 入力（float32 HWC RGB、ImageNet正規化済み）、出力は float32 で
`reshape(256, 256)` して使う。

## 距離の扱いと校正

MiDaS の出力は **相対逆深度**（大きいほど近い、スケールとシフトが不定）。距離は
`range[m] = depth_scale / (inv_depth + depth_shift)` で近似している。既定値（1250 / 245）は
この部屋の映像から目分量で合わせたもので、メートル表示は目安（SRD §6 のとおりソナー「風」の演出としては可）。

合わせ直す場合:
1. `dk ./main.py --calibrate --frames 900` を実行し、画面中央に物（人や箱）を置く
2. 1秒ごとの `[CAL] center inv_depth=...` を、既知距離 2 か所（例 r1=1 m, r2=3 m）で読む（d1, d2）
3. `depth_shift = (r1*d1 - r2*d2) / (r2 - r1)`、`depth_scale = r1 * (d1 + depth_shift)` を config に書く

MiDaS はフレームごとにスケールが多少変わるので、正確な測距には向かない（必要なら Metric 版モデルを検討）。

## 表示の調整ポイント（`config/default.conf`）

| 症状 | 調整 |
|---|---|
| 机・床がいつも最近目標になる | `band_top/band_bottom` を上へ（カメラ小窓の緑枠が抽出に使っている帯） |
| エコーがちらつく | `ema_alpha` を下げる / `nearest_percentile` を下げる |
| 方位がずれる | `camera_hfov_deg` を実カメラの水平画角に |
| 残像が長すぎ/短すぎ | `echo_half_life_s`, `sweep_period_s` |
| アラートが鳴りっぱなし | `alert_range_m` |

## SRD との対応

| 要件 | 実装 |
|---|---|
| FR-01, 02 | カメラの新フレームごとに MLA で推論（`DepthWorker`） |
| FR-03, 04 | `RangeProfile.update`：水平帯 × 64方位ビンの 90 パーセンタイル + EMA |
| FR-05, 06 | ピンホールモデルで列→方位（`atan`）、逆深度→距離換算 |
| FR-07〜11 | `SonarRenderer`：扇形PPI、往復スイープ、エコー残光（半減期指定）、距離リング+ラベル、緑基調 |
| NFR-01 | カメラ入力から描画・エンコードまで DevKit 単体（ホストは表示だけ） |
| NFR-02 | 描画は OpenCV のベクトル演算のみ（約17 ms/フレーム、1コア相当未満） |
| Step 4 | ターゲットロック枠（最近目標、ロック時に枠が縮む）・PROXIMITY ALERT（`alert_range_m` 未満で点滅）。**ピング音は未実装**（RTP映像のみで音声経路が無い） |

Step 1（PCプロトタイプ）は省略し、DevKit 上で直接開発した（カメラが DevKit にあり、MLA版とCPU版の比較も実機で取れるため）。

## 実装作業の記録（2026-10-13）

実際に行った順に記録する。同じ所で迷わないよう、途中で起きた問題と判断の理由も残す。

### 1. 調査・方針決定

| 確認したこと | 結果 → 判断 |
|---|---|
| ワークスペース内の深度推定モデル | MiDaS 等のコンパイル済み MPK は無し → 公式 ONNX から自前でコンパイルする |
| 参考にする既存アプリ | `../Scouter`（DevKit の USB カメラ → H.264/RTP UDP → Insight）、`../AvatarCreator/AnimeGAN`（自前コンパイルした画像→画像モデルを Tensor 入力で MLA 実行）。この2つの構成を組み合わせた |
| 出力方式 | ご要望は RTSP だが、pyneat に毎フレーム更新の RTSP 出力が無いため、他アプリと同じ H.264/RTP(UDP) で Insight に送る（上の「なぜ RTSP ではなく RTP/UDP か」） |
| DevKit / ネットワーク | `dk status` で到達可、GitHub からのダウンロード可 |

### 2. モデルの入手と MLA 向け書き換え（`tools/mla_surgery.py`）

1. 公式 `model-small.onnx`（66 MB、opset 10、入力 1x3x256x256、出力 1x256x256）をダウンロード
2. `model_surgery_guard.py audit-model --dtype int8` で演算子を監査 → 非対応 0（BatchNormalization / Constant / Squeeze は「不明」扱い、BN は onnxsim で畳まれる）
3. グラフを確認すると、入力直後に ImageNet の `Sub/Div`、EfficientNet 由来の非対称 `Pad` ×5、末尾に `Squeeze` があった → 「モデル（Step 2）」の表のとおり書き換え
4. **問題:** 書き換え後の自己検証で元モデルとの差が最大 187（出力レンジ 477〜1204）。
   opset 変換を外すと差 0 になったため原因を opset 変換に特定。opset 10 の `Resize` は暗黙に asymmetric 座標だが、
   変換後は属性未指定で half_pixel 扱いになる → `coordinate_transformation_mode=asymmetric` を明示して差 0.0

### 3. キャリブレーションと量子化・コンパイル

1. `tools/collect_calib.py` で DevKit のカメラから 60 枚収集（1280x720 → 256x256 に引き伸ばし。本番の前処理と同じ）。
   カメラは `/dev/video0`、1280x720 で取得できることをスナップショットで確認
2. Model Compiler 2.1 の `quantize_compile.py` で int8 コンパイル（約3分、ログ `logs/compile_int8.log`）。
   `--mean/--std` に ImageNet の値を渡し、ホスト側の正規化と一致させた
3. 結果: **MLA:1 / EV74:4 / A65:0**（CPU フォールバック無し）。この時点で書き換えが十分だったので bf16 は不要と判断

### 4. 実機での精度・速度確認（`tools/mla_infer.py`, `depth.py`）

- pyneat の `Model` を Tensor 入力（前処理 Off、float32 HWC）で作る `MidasMla` ラッパを `depth.py` に実装
  （AnimeGAN の `MlaCartoonizer` と同じ方式。出力は `reshape(256, 256)` でそのまま使えた）
- CPU 版との比較には DevKit に onnxruntime が無いため `../AvatarCreator/AnimeGAN/vendor` のものを借用
- 結果: 相関 0.990〜0.995、MLA 7.2 ms vs CPU 223 ms。比較画像 `output/depth_compare.png` で目視でも差が無いことを確認

### 5. アプリ本体の実装（`main.py`, `sonar.py`, `config/default.conf`）

設計上の判断:
- **推論と描画を別スレッドにした**: 深度はカメラのフレームごと（約13〜19 fps）、描画は 30 fps の一定周期。
  スイープ線の動きがカメラ・推論の揺らぎに影響されず滑らかになる
- **距離抽出**: 中央の水平帯を 64 方位ビンに分け、各ビンの 90 パーセンタイル（最大値だと1画素のノイズで跳ねる）→ 距離換算 → EMA
- **方位**: 列位置を線形に角度へ割り当てず、ピンホールモデル（`atan`）で変換（画角の端で正確）
- **残光**: float32 のエコー用・スイープ尾用バッファを半減期指定で毎フレーム減衰させ、緑の燐光 LUT で色付け
- OpenCV のアンチエイリアス描画は 8bit 画像専用なので、float バッファへの描画は `LINE_8` にした
- 映像送出は Scouter と同じ NV12 → `h264_rtp_udp_from_raw`。push は `try_push()`（`push()` は 5 秒詰まると Run が止まるため）
- 設定はすべて `config/default.conf` に出し、各値の理由をコメントに書いた

### 6. 動作確認とチューニング

| 手順 | 結果 | 対応 |
|---|---|---|
| `--no-video --frames 200` で描画のみ確認 | 出力 30 fps、ソナー表示は想定どおり。ただし深度が 11.5 fps（MLA は 7 ms なのに1フレーム 30 ms） | 原因調査へ |
| `tools/camera_fps.py` でカメラ単体を計測 | C200 自体が 1280x720 で **12.8 fps**。露出コントロールに fps を上げる項目は無く、`../Scouter/README.md` でもファームウェア側の制限と結論済み | 解像度を変えて再計測 |
| 640x360 / 640x480 で計測 | 18.2 / 19.0 fps。前処理も 20 ms → 11 ms | モデル入力は 256px なので **640x360 を既定に** |
| 前処理の実装比較（numpy / cv2 スカラー演算 / LUT） | 10.8 / 8.4 / 10.7 ms（縮小だけで 6 ms） | cv2 スカラー演算版を採用 |
| Insight へ送出して 450 フレーム | `/api/ingest/stats` で channel 0 に H.264 RTP 受信（約4.6 Mbps、SPS/PPS/IDR あり）。出力 29.9 fps、深度 15.6 fps、ドロップ 0 | — |
| 実行ログの警告 `use_simaai_pool is deprecated` | — | `InputOptions.memory_policy = InputMemoryPolicy.SystemMemory` に置き換え、警告が消えたことを確認 |
| `tools/stop.py` で連続実行中のアプリを停止 | SIGTERM で `stopped`、アプリ側も `Done:` を出して正常終了 | — |

最終スナップショットでは、カメラ左寄り約 1.2 m の人物と 2.5〜3.5 m の奥の壁がエコーとして表示され、最近目標に `TGT` 枠が付くことを確認した。

### 7. 未完了・今後の課題

- **距離の校正**: `depth_scale` / `depth_shift` は映像からの目分量。巻尺で測った2点で `--calibrate` の手順を実施すると良い
- **カメラの水平画角**: `camera_hfov_deg=65` は一般的な Web カメラの値で、C200 の実測ではない
- **ピング音**（Step 4）: RTP 映像のみで音声経路が無いため未実装。HDMI 出力 + DevKit のスピーカー、またはブラウザ側で鳴らす方式が考えられる
- **長時間運転**: 確認したのは最長 15 秒程度。連続運転時の温度・安定性は未確認

## 注意

- `calib/` はカメラで撮った室内・人物の画像。再コンパイルしないなら削除してよい。
- `build/`（約105MB）はコンパイル中間生成物。必要なのは `assets/*_mpk.tar.gz` だけ。
- 編集直後に `dk` で古いコードが動くことがある（NFSキャッシュ）。`rm -rf __pycache__` して再実行。
