# Yolo_panoptic — 室内パノプティックセグメンテーション (Modalix向け)

USB webcamの映像から、家の中の **物(インスタンス)** と **壁・床・天井・ドア(領域)** を画素単位で認識する。
最終ターゲットは SiMa.ai Modalix DevKit(Palette / Neat SDK)。

**起動方法だけ知りたい場合は [start.md](start.md) を参照。**

---

## 1. 方式

YOLOはパノプティックセグメンテーションを直接出力しないため、2モデルの出力を後段で統合する。

```
USB webcam ──┬─> YOLO11n-seg (COCO)      ──> things: 物のインスタンスマスク
             │
             └─> LRASPP-MobileNetV3 (生徒) ──> stuff: 壁/床/天井/ドア
                                                   │
                         パノプティック統合 <──────┘
                  (物を信頼度順に配置 → 残りを stuff で埋める)
```

| 役割 | モデル | 備考 |
|---|---|---|
| things | YOLO11n-seg | COCO学習済み(椅子・ソファ・テーブル・TV・人・犬猫など) |
| stuff | LRASPP-MobileNetV3-Large | 自宅映像の擬似ラベルで5クラス学習(蒸留) |
| 教師 | UperNet-ConvNeXt-small (ADE20K) | 擬似ラベル生成専用。PC上でのみ使用 |

### 方式選定の理由
- 既存の学習済みモデルを使え、アノテーション作業が不要
- 両モデルとも素直なCNNで、Paletteでの量子化・コンパイルに向く
- モデルを個別に検証・差し替えでき、切り分けが容易

### クラス定義(stuff)

| ID | クラス | 教師(ADE20K)の対応ラベル |
|---|---|---|
| 0 | other | 上記以外すべて(家具、窓、白飛びなど) |
| 1 | wall | wall |
| 2 | floor | floor, rug |
| 3 | ceiling | ceiling |
| 4 | door | door, screen door |
| 255 | ignore | 教師の最大確率 < 0.5 の画素(学習時のみ) |

パノプティック出力は `pan_cls`(stuff: 0〜 / thing: 100 + COCOクラスID / その他: -1)と `pan_inst`(インスタンスID、stuffは0)の2枚。

---

## 2. 環境

- マシン: `simapc`(Ubuntu、CPUのみ: AMD Ryzen AI 7 350)
- 作業ディレクトリ: `~/neat_2.1.2/Yolo_panoptic`(Python venv: `.venv`)
- 主なライブラリ: torch 2.14, ultralytics 8.4.162, transformers, onnx 1.23.0, onnxruntime, onnxsim, opencv-python

```bash
pip install ultralytics transformers opencv-python numpy torch onnx onnxruntime onnxsim
```

---

## 3. ファイル構成

```
Yolo_panoptic/
├── start.md                # 起動方法だけをまとめたクイックスタート
├── panoptic_webcam.py      # Step 1  PC検証 (webcam + YOLO-seg + 教師 + 統合 + 可視化)
├── record_frames.py        # Step 2-1 学習用フレーム録画
├── make_pseudo_labels.py   # Step 2-2 教師による擬似ラベル生成
├── train_student.py        # Step 2-3 生徒モデル学習
├── export_onnx.py          # Step 2-4 ONNXエクスポート + 検証 + キャリブ画像出力
├── yolo_cut_head.py        # Step 2-4b YOLOヘッド切断 + CPUデコード検証
├── quantize_compile.py     # Step 2-5 Palette量子化・コンパイル(--fixed-batch対応版)
├── attention_surgery.py    # Step 2-5 C2PSA MatMul->Einsum置換(YOLO単一MLAセグメント化)
├── eval_quantized.py       # Step 2-5 量子化後 mIoU / 検出誤差の実測
├── main.py                 # Step 3  Modalixパイプライン本体(実USBカメラで動作確認済み)
├── probe_camera.py         # Step 3  USBカメラ検出・config反映ヘルパー(DevKit上で実行)
├── config/default.conf     # Step 3  カメラ/モデル/しきい値/UDP出力の設定
├── config/no_camera_test.conf  # Step 3  USBカメラなしでのスモークテスト用(静止画ループ)
├── dataset/
│   ├── images/<room>/*.jpg
│   ├── labels/<room>/*.png   # 擬似ラベル (uint8)
│   ├── preview/<room>/*.jpg  # [入力 | 擬似ラベル] 確認用
│   ├── train.txt / val.txt
├── runs/
│   ├── lraspp_20260925_201325/     # ★採用 (best.pt)
│   └── deeplabv3_20260925_211220/  # 比較用
└── export/
    ├── student_lowres.onnx   # ★Modalix用 生徒モデル
    ├── student_full.onnx     # 比較用 (モデル内でアップサンプル)
    ├── yolo11n-seg.onnx           # 元のYOLO (output0/output1)
    ├── yolo11n-seg_attnfix.onnx   # C2PSA MatMul->Einsum置換後(頭部切断前)
    ├── yolo11n-seg_cut.onnx       # (旧版、頭部切断のみ)3MLAセグメントで実機ブロッカーあり — 不使用
    ├── yolo11n-seg_attnfix_cut.onnx  # ★Modalix用 YOLO (attention Einsum化 + ヘッド切断)
    ├── yolo11n-seg_attnfix_cut.json  # ↑の出力名 / スケール対応表
    ├── calib_student/        # 量子化用 50枚 (512x384)
    ├── calib_yolo/           # 量子化用 50枚 (640x640 レターボックス)
    └── build/                # Step 2-5 量子化・コンパイル成果物
        ├── student_lowres/student_lowres_mpk.tar.gz          # ★Modalix実行可能(6.0MB)
        └── yolo11n-seg_attnfix_cut/yolo11n-seg_attnfix_cut_mpk.tar.gz  # ★Modalix実行可能・単一MLAセグメント(11MB)
```

---

## 4. 工程記録

### Step 1: PC検証(教師モデルでの方式確認)

```bash
python panoptic_webcam.py --camera 0
```

- things: YOLO11n-seg / stuff: UperNet-ConvNeXt-small (ADE20K)
- キー: `q` 終了、`s` 保存(PNG + npy)、`v` 表示切替

**結果**(廊下シーン)

| 項目 | 結果 |
|---|---|
| 床/壁の境界 | 巾木に沿って正確。フローリングの反射・映り込みにも崩れない |
| 物のマスク | 輪郭までほぼ正確、影・映り込みは床側に残る |
| 白飛び領域 | 「その他」として扱われる(正しい挙動) |
| 処理時間 (CPU) | YOLO 84ms / 教師 **4140ms** / 統合 2ms |

**判明した課題と対応**
- 教師が重すぎる → 軽量モデルへ蒸留する(Step 2)
- ドアが壁に含まれる → **ドアを別クラスにする**
- COCOにない物のクラス名が不正確(置物が "cat")→ マスクは正確なので、名前は参考扱い

### Step 2-1: 学習データの録画

```bash
python record_frames.py --list-cameras               # カメラ一覧
python record_frames.py --room hallway --camera 0     # r: 録画開始/停止
```

- 0.5秒間隔で保存、ブレ画像(ラプラシアン分散 < 60)は自動除外
- `--camera` は番号 / `/dev/videoN` / `/dev/v4l/by-id/...` で指定可
- 録画方針: 実運用と同じカメラ高さ、照明バリエーション、ドア開閉の両方

**結果**: 220枚

### Step 2-2: 擬似ラベル生成

```bash
python make_pseudo_labels.py
```

- ADE20K 150クラスの確率を5クラスに合算 → 最大確率 < 0.5 は ignore
- 所要時間: 220枚で約16分(CPU)

**結果**

| クラス | 画素比率 |
|---|---|
| other | 14.5% |
| wall | 38.4% |
| floor | 32.2% |
| ceiling | 0.8% |
| door | 11.8% |
| ignore | 2.3% |

split: train 199 / val 21。ceiling が少ないのはカメラ位置が低いため(学習時にクラス重みで補正)。

### Step 2-3: 生徒モデル学習

```bash
python train_student.py                   # LRASPP (デフォルト)
python train_student.py --arch deeplabv3  # 比較
```

- 入力 384×512 固定(webcamと同じ4:3)、60エポック、AdamW + Cosine
- 拡張: スケール0.75〜1.25、クロップ、左右反転、明るさ/コントラスト/ガンマ
- 損失: CrossEntropy(ignore除外、1/√頻度 のクラス重み: ceiling 2.75 など)

**結果**

| モデル | val mIoU | CPU推論 | best epoch |
|---|---|---|---|
| **LRASPP-MobileNetV3** | **0.879** | **57.5 ms** | 30 |
| DeepLabV3-MobileNetV3 | 0.872 | 157.7 ms | 50 |

→ **LRASPPを採用**(精度・速度とも上回り、構造もシンプル)。教師4140msから約70倍の高速化。

※ mIoUは教師の擬似ラベルとの一致度であり、真値に対する精度ではない。

### Step 2-4: ONNXエクスポート

```bash
python export_onnx.py --ckpt runs/lraspp_20260925_201325/best.pt
```

**生徒モデル**
- PyTorch vs ONNX 最大誤差: **1.67e-05**
- ops: `Add, Conv, GlobalAveragePool, HardSigmoid, HardSwish, Mul, Relu, Resize, Sigmoid`
- 正規化(mean/std)はモデル外

| 後処理方式 | val mIoU |
|---|---|
| full(モデル内 bilinear) | 0.879 |
| **lowres + CPU bilinear** | **0.879** ← 採用 |
| lowres + argmax→nearest | 0.870(CPU負荷が問題になった場合の代替) |

→ `student_lowres.onnx`(出力 1×5×48×64)を採用し、拡大はCPU側で行う。

**YOLO11n-seg**
- 入力 `images` 1×3×640×640 / 出力 `output0` 1×116×8400、`output1` 1×32×160×160

### Step 2-4b: YOLOヘッド切断

```bash
python yolo_cut_head.py --onnx export/yolo11n-seg.onnx
```

`output0` は bbox座標(0〜640px)、sigmoidスコア(0〜1)、マスク係数を1本に連結しており、INT8量子化に不利。そのため、デコード直前のConv出力で切断した。

| 出力 | shape(S = 80 / 40 / 20) | 元ノード |
|---|---|---|
| box | 1×64×S×S(DFLロジット 4辺×16) | `/model.23/cv2.{0,1,2}/cv2.*.2/Conv_output_0` |
| cls | 1×80×S×S(sigmoid前) | `/model.23/cv3.{0,1,2}/cv3.*.2/Conv_output_0` |
| mc | 1×32×S×S | `/model.23/cv4.{0,1,2}/cv4.*.2/Conv_output_0` |
| proto | 1×32×160×160 | `output1` |

- 切断後の ops: `Add, Concat, Conv, ConvTranspose, MatMul, MaxPool, Mul, Reshape, Resize, Sigmoid, Softmax, Split, Transpose`(残りはバックボーン由来。MatMul/Softmax は C2PSA のattention)
- CPUデコード(DFL → アンカー+0.5 → stride倍、sigmoid)と元 `output0` の最大誤差:
  **box 1.53e-04 px / score 1.78e-07 / mc 0 / proto 0 → 一致**
- `yolo_cut_head.py` の `decode()` は、Modalixの後処理でそのまま流用する

**追記(Step 3の実機検証で判明)**: この段階の`yolo11n-seg_cut.onnx`はC2PSA attentionが
原因でMLAが3セグメントに分割されてしまい、実機で`pyneat.Model()`構築自体が失敗する
(詳細はStep 3節参照)。実際にModalixへデプロイする際は、`yolo_cut_head.py`を
`export/yolo11n-seg.onnx`に直接ではなく、**先に`attention_surgery.py`でC2PSAの
MatMulをEinsumへ置換した`export/yolo11n-seg_attnfix.onnx`に対して**実行し、
`yolo11n-seg_attnfix_cut.onnx`を生成する(コマンドはStep 3節を参照)。

### Step 2-5: Palette(Neat 2.1.2)で量子化・コンパイル

```bash
activate-model-compiler   # 未セットアップの場合は下記「環境メモ」参照
python quantize_compile.py --model_path export/student_lowres.onnx \
  --model_format onnx --model_layout NCHW \
  --input_names input --input_shapes 1,3,384,512 --output_names logits \
  --device modalix --build_dir export/build \
  --real_data --dataset_images export/calib_student --num_calib_samples 50 \
  --calib_method mse --mean 0.485 0.456 0.406 --std 0.229 0.224 0.225 --verify

# YOLOはStep 2-4bの追記の通り、attention_surgery.py適用後の *_attnfix_cut.onnx を使う
python quantize_compile.py --model_path export/yolo11n-seg_attnfix_cut.onnx \
  --model_format onnx --model_layout NCHW \
  --input_names images --input_shapes 1,3,640,640 \
  --device modalix --build_dir export/build --fixed-batch \
  --real_data --dataset_images export/calib_yolo --num_calib_samples 50 \
  --calib_method mse --mean 0 0 0 --std 1 1 1 --verify
```

`quantize_compile.py` はSwinIR変換時のスクリプト(`SuperReslution/swinir-test/SwinIR/compile_swinir.py`)をベースにした本プロジェクト用コピー(スキルの共有スクリプトに `--fixed-batch` を追加)。

**環境メモ(このNeat SDKコンテナ固有の問題)**: `/sdk-extensions/model-compiler` は元々 `libLLVM.so.18.1` / `libopenblas.so.0` が欠けており `import afe` が失敗した。`sudo apt-get install libllvm18 libopenblas0` で解消(パッケージ追加のみ、プロジェクトファイルへの影響なし)。

**C2PSA(MatMul/Softmax)の扱い**: YOLOのC2PSAブロックはSwinIRのwindow attentionと同じ理由で
最初は失敗した —— attention内のreshapeがバッチ軸をヘッド軸に畳み込むため、フレームワークの
「柔軟バッチサイズ」再検証がリテラルなバッチ値と衝突する。`load_model(...,
flexible_batch_size=False)` (`--fixed-batch`)で量子化自体は回避できるが、その上で
C2PSAの`MatMul`/`Softmax`自体はMLAに割り当てられず(`Cannot assign node ... to MLA:
Unsupported`)、EV74/A65のプラグインとして実行され、結果としてMPKがMLAセグメント
3つに分割されてしまう。この分割構成は**実機の`pyneat.Model()`構築を失敗させる**ことが
Step 3の実機検証で判明したため、最終的に`attention_surgery.py`でC2PSAの`MatMul`を
`Einsum`に置換して単一MLAセグメントに収める(詳細・実機での修正確認はStep 3節を参照)。

**コンパイル結果**

| モデル | Plugin distribution | 出力 | サイズ |
|---|---|---|---|
| 生徒(LRASPP) | MLA:1 / EV74:4 / A65:0 | `export/build/student_lowres/student_lowres_mpk.tar.gz` | 6.0MB |
| YOLO(ヘッド切断のみ、旧版・不使用) | MLA:3 / EV74:54 / A65:2 | `export/build/yolo11n-seg_cut/...` | 11MB |
| YOLO(attention Einsum化 + ヘッド切断、★採用) | **MLA:1 / EV74:24 / A65:0** | `export/build/yolo11n-seg_attnfix_cut/yolo11n-seg_attnfix_cut_mpk.tar.gz` | 11MB |

**量子化後の精度(`eval_quantized.py`、val 21枚、量子化はcompileと同じ設定)**

| モデル | 指標 | FP32(ONNX) | INT8(量子化) | 劣化 |
|---|---|---|---|---|
| 生徒(LRASPP) | mIoU | 0.879 | 0.863 | -0.016 |

クラス別: other 0.81→0.79 / wall 0.85→0.84 / floor 0.99→0.98 / ceiling 0.90→0.89 / door 0.83→0.81。
量子化ログには「Saturation was detected for integer convolution. Weights were set to zero」という警告がbackboneの深い層で多数出る(int8のダイナミックレンジ超過分の重みをゼロに丸めている)が、実測のmIoU劣化は0.016に留まった。以降アーキ変更や再学習をする場合はこの警告件数の変化を確認すること。

YOLO(attention Einsum化後)はスコア0.25閾値でのFP32/INT8検出をIoU>0.5でマッチングすると
box誤差 平均2.3px/最大6.4px、score誤差 平均0.25/最大0.43、マッチしたペア中3件でクラスが変化した
——attention手術前(box平均1.9px/score平均0.25)とほぼ同水準で、Einsum置換による量子化精度への
悪影響は見られない。**ただし** 現在のval画像(`hallway`のみ、家具などのCOCO物体が映っていない)
ではFP32でもスコアが0.1〜0.3程度の弱い検出しかなく、この比較はほぼノイズ床での不安定性を
見ているに過ぎない(NMSも未適用の生アンカー比較)。YOLOの量子化劣化を意味のある形で検証するには、
実際にCOCO物体(椅子・ソファ・人など)が映った画像でのval追加が必要 — Step 4の
「録画データの追加」と合わせて対応する。

### Step 3: Modalix パイプライン(実USBカメラで動作確認済み)

```bash
dk ./probe_camera.py                                        # まずカメラを検出
dk ./probe_camera.py --device /dev/videoNN --apply          # config/default.conf へ device/解像度/fps を書き込む
dk ./main.py --config ./config/default.conf --frames 300    # 必ず最初に有限フレームで
# USBカメラが手元にない場合:
dk ./main.py --config ./config/no_camera_test.conf --frames 30
```

**構成**(`main.py`、CLAUDE.mdの4段パイプラインを2モデル用に拡張):

```
USB webcam (v4l2, MJPEG) ──> RGBフレーム(Python: NV12→RGB) ──┬──> YOLO Run (push/pull, Block queue) ──> CPU: DFLデコード+NMS+マスク合成
                                                              └──> Student Run (push/pull, Block queue) ──> CPU: bilinear拡大+argmax
                                                                                                                      │
                                        パノプティック統合(信頼度順にthing配置→残りをstuffで埋める) <──────────────────┘
                                                              │
                                          NV12へ変換 → H.264/RTP UDP送信 (VideoSenderOptions.h264_rtp_udp_from_raw)
```

- 2モデルは同じ物理MLAを**フレームごとに逐次** push/pull する(スレッド分離やダブルバッファリングはしない)。
  正しさ優先の初版で、パイプライン深化による高速化はStep 4に回す(`student_interval`設定は
  そのための足場としてもう入れてあるが、デフォルト1=毎フレーム)。
- リサイズ・色変換・正規化は **Neat側(CVU)ではなく全てPython側**で行う。理由(実機検証で判明、後述):
  `quantize_compile.py`(Step 2-5)は `/255`(+生徒はImageNet mean/std)を**量子化アフィン変換そのものに
  折り込む**形でキャリブレーションしており、コンパイル済みMPKの先頭プラグイン(`quantize_0`)は
  「正規化済みのfloatテンソル」を直接要求する。プロダクト提供モデル(YOLO26mなど、他のsibling appが使う)
  のように「Neatが色変換・リサイズ・量子化までCVU上でやってくれる」契約(`opt.preprocess.kind=Image`
  + resize + normalize preset)とは異なる。そのため `opt.preprocess.kind = InputKind.Tensor`
  (パススルー)にし、`export_onnx.py`/`train_student.py`と同じレターボックス/ストレッチ+正規化を
  Pythonで行った float32 テンソルを直接pushする。
- 出力側は両モデルとも量子化時にオンデバイスdecode_typeを設定していない(生の生徒logits、YOLOはヘッド
  切断済み)ため、`model.preprocess() + model.inference() + nodes.detess_dequant()` の分解形でグラフを
  組む(`apps/examples/segmentation/yolov8-instance-segmenter`と同じパターン)。
- YOLOのDFLデコード・sigmoid・NMS・マスク係数×proto合成は全てCPU(numpy、`yolo_cut_head.py`の
  `decode()`と同じ数式)。テンソルはHWC(channel-last)であることを実機の`0_process_mla.json`等の
  manifest(`detessellate`ステージの`slice_shape`がHWC順)で確認済み。
- カメラ取り込みは`demo-neat/apps/usb-camera-yolo26m`のv4l2 custom()フラグメント(MJPEG固定 + jpegdec)を踏襲。

#### YOLOモデルのMLA単一セグメント化(C2PSA attention のEinsum置換)

Step 2-5でコンパイルしたYOLOモデルは当初、C2PSA(`model.10`)のself-attention
(MatMul/Softmax)がMLAに割り当てられず(`Cannot assign node ... to MLA: Unsupported`
「Reshape affecting the batch axis is not supported」)、**3つのMLAセグメント**
(`MLA_0`(attention前)→ EV74/A65 → `MLA_11` → EV74/A65 → `MLA_18`(head))に
分割されていた。この構成のMPKは実機で`pyneat.Model()`構築自体が
```
RuntimeError: preprocess planner: MPK contract is missing an MLA stage for pre route selection.
```
で失敗し(`opt`の内容に関係なく再現、生徒モデル(MLAセグメント1個)は同じコード経路で問題なし
――詳細な再現手順は本セクション末尾の「調査記録」参照)、起動すらできなかった。

**修正**: `attention_surgery.py`で、C2PSA内の2つの`MatMul`ノードを、この形状に対して
数学的に完全に等価な`Einsum`ノードへその場で置換する(重みの再学習は不要)。
```bash
python attention_surgery.py --onnx export/yolo11n-seg.onnx --out export/yolo11n-seg_attnfix.onnx
#   MatMul  [1,2,400,32]x[1,2,32,400]->[1,2,400,400]  == Einsum "bhnc,bhck->bhnk"
#   MatMul_1[1,2,64,400]x[1,2,400,400]->[1,2,64,400]  == Einsum "bhcn,bhnm->bhcm"
#   [verify] 数値誤差 1.25e-03(浮動小数点の演算順序差程度、ランダム入力+実画像で検証)
python yolo_cut_head.py --onnx export/yolo11n-seg_attnfix.onnx --out export/yolo11n-seg_attnfix_cut.onnx
#   [verify] box 1.53e-04px / score 1.78e-07 / mc 0 / proto 0 → 手術前と同じ精度で一致
python quantize_compile.py --model_path export/yolo11n-seg_attnfix_cut.onnx ... --fixed-batch ...
```
このEinsum形式は`sima-model-surgery`スキルの`supported_operators.json`にMLA対応と
明記されている(`demo-neat/apps/pcb-defect-detection-yolo26n`が構造的に同一の
YOLO26 C2PSAブロックに対して同じ手法で単一MLAセグメント化に成功済み――既存の実証済みレシピを移植)。

**結果**: プラグイン構成が `MLA:3/EV74:54/A65:2` → **`MLA:1/EV74:24/A65:0`** に、
"Cannot assign to MLA"警告も0件に。量子化後精度は手術前とほぼ同一(`eval_quantized.py`で再検証、
box/score誤差・検出数とも同水準、劣化なし)。

#### 実機検証の結果(2026-09-26、DevKit実機で`dk`経由)

**モデル構築・グラフビルドは両モデルとも成功**。手術後のYOLO MPKで`pyneat.Model()`構築 →
グラフ構築(`model.preprocess()+inference()+detess_dequant()+output()`)→ `graph.build()` →
push/pull まで確認、出力テンソル形状は想定通りHWC(box: `[1,80,80,64]`など、student: `[1,48,64,5]`)。

**実物のUSBカメラ(Anker PowerConf C200)を接続してフル動作確認済み**。当初このDevKitには
カメラが物理接続されておらず(`lsusb`でUSBハブのみ検出)、`source_override`(静止画ループ、
`config/no_camera_test.conf`)でカメラ以外の経路のみ検証していたが、後日カメラが接続されたため
`probe_camera.py`(下記)経由で検出・設定し、実カメラでのフルパイプラインを確認した。

```bash
dk ./probe_camera.py                          # lsusb + v4l2-ctl でUSB webcamを検出(内部ISPノードは除外)
dk ./probe_camera.py --device /dev/video96    # 対応フォーマット/解像度/fpsを一覧
dk ./probe_camera.py --device /dev/video96 --apply   # config/default.conf に反映
dk ./main.py --config ./config/default.conf --frames 30
```

- カメラは`Anker PowerConf C200`、MJPG最大2560x1440@30。`probe_camera.py`はUSB2帯域制約
  (`usb-camera-yolo26m`のLEARNING.mdと同じ理由でMJPG優先)を踏まえ最高解像度を推奨するが、
  実測FPSを見て**1280x720@30に調整**(2560x1440では2モデル逐次push/pullのCPU側前処理が
  重く0.5fps程度、1280x720では1.8fps程度——正しさ優先の初版としては許容範囲、高速化はStep 4)。
- 実際に人物などの実物体を検出(`things=1`前後を安定して継続)。hallwayデータセットと違い
  実際にCOCO物体が映るシーンでの動作を確認できた。
- `frame=30 fps=1.8 things=1 dets=1`のように、カメラ→2モデル推論→CPUデコード→パノプティック統合→
  H.264/RTP送信の全経路が実機で継続動作することを確認。
- **出力画像も目視確認済み**(`main.py --save-every N --save-dir DIR`でパノプティック合成前後の
  JPGを保存するデバッグ機能を追加、NFS越しにPC側から直接確認できる)。デスク上のクローズアップ
  シーンで検証した結果、マスク自体は物体境界に沿っておおむね妥当に付いている一方、
  クラス名はズレる(コート→"dog"、MacBook端→"mouse"等)——COCO学習済みnanoモデルを
  近距離・非典型アングルの物体に当てた際の想定内の挙動。wall/floorのstuff領域もおおむね
  妥当な位置に付く。

**実機で確認できたその他の項目**:
- テンソルのレイアウトはHWC(channel-last)——`main.py`の実装はこの前提で正しく動作。
- `opt.preprocess.input_max_depth=3`(`InputKind.Tensor`使用時)で両モデルとも問題なし。
- `camera_fragment()`(`usb-camera-yolo26m`のv4l2 custom()フラグメントを踏襲)はそのまま実カメラで動作。

<details>
<summary>調査記録: YOLOモデルが単一MLAセグメントでなかった際の診断ログ(解決済み、参考用)</summary>

- **`opt`の内容に関係なく**再現していた(デフォルト`Model(path)`のみでも失敗)。生徒モデルと
  同じコード経路で、モデルパスを変えただけで再現したため、`main.py`側のバグではなくコンパイル済み
  YOLO MPK自体の構造に起因すると判断した。
- `--any_shape_on_mla`付きで再コンパイルしても同じだった(プラグイン構成 MLA:3/EV74:54/A65:2
  は変化なし)。
- `opt.verbose.planner=True`で詳細ログを見ると、MPK契約グラフ自体は74本のedgeが全て
  `candidates=1`(曖昧さなし)で解決しきっていた——にもかかわらず後段の「preprocess planner」が
  失敗していた。生徒モデルはMLAプラグインが1個(`MLA_0`のみ)なのに対し、YOLOはC2PSA attention
  部分の分割により3つのMLAセグメントに分かれていたことが原因と推定し、上記のEinsum置換で解消した。

</details>

### Step 4: 最適化(実測ベース、fps 1.8 → 約6.9に改善)

推測で最適化する前に、まず`main.py`にステージ別の処理時間計測(`StageTimer`、`profile_interval`
ごとに `ms(capture=... yolo_infer=... yolo_decode=... student_infer=... student_decode=...
merge_draw=... encode=...)` の形でログ出力)を追加し、実機・実カメラで計測した。

**最適化前(1280x720@30、実USBカメラ)の内訳**:

| ステージ | 処理時間 |
|---|---|
| capture(v4l2フレーム取得+NV12→RGB) | 2.0ms |
| yolo_infer(push/pull、MLA推論そのもの) | 20.7ms |
| yolo_decode(DFL+sigmoid+NMS) | 63.7ms |
| student_infer(push/pull、MLA推論そのもの) | 21.2ms |
| student_decode(bilinear拡大+argmax) | **77.5ms** |
| **merge_draw(パノプティック統合+描画)** | **~340〜400ms** |
| encode(NV12変換+送信) | 3.6ms |
| **合計 / fps** | **~530ms / 1.8fps** |

意外な結果として、**MLA推論そのもの(yolo_infer/student_infer)は20ms前後と十分高速**で、
ボトルネックは全て**CPU側の後処理**(特にmerge_draw)にあった。フルHD相当の解像度で
numpy/OpenCVのフルフレーム配列演算を何度も行うと、このボード(組み込みARM CPU)では
1回あたり数十〜数百msかかることが実測で判明した。

**適用した最適化(いずれも実測で効果を確認、精度への影響は目視で許容範囲)**:

1. **`instance_mask()`: マスクをフルフレームへ拡大→bboxサイズへ拡大に変更**
   検出ごとにproto(160x160)をカメラ解像度いっぱいまで`cv2.resize`していたのを、
   該当bboxのサイズだけに拡大するよう修正(該当領域以外は最初からゼロなので不要な計算だった)。
2. **パノプティック統合を低解像度で実行(`merge_downscale`設定、既定6)**
   `stuff_map`のデコード(`decode_student`)と`merge_and_draw`のピクセル単位演算
   (pan_cls/pan_inst配列、stuff着色、alpha blend、輪郭検出)を全て
   `frame_w/N x frame_h/N`で行い、最後に1回だけ`cv2.resize`で実解像度へ拡大する。
   → **student_decode: 77.5ms→3.1ms、merge_draw: ~370ms→~41ms**
   (マスク・輪郭の境界はやや粗くなるが、目視では許容範囲——`captures/`で確認済み)
   - **注意点(実装時にハマった罠)**: 文字ラベル・凡例を低解像度側で描画すると、
     最終的な拡大時にテキストボックスごと巨大化・ブロック化して壊れる。
     `merge_and_draw()`は色・輪郭のみ描画して各セグメントの重心座標を返すに留め、
     `draw_annotations()`という別関数で**拡大後の実解像度**に対してラベル・凡例を描画するよう分離した。
3. **YOLOのNMS: sigmoidの遅延評価**
   `decode_yolo_raw()`が8400アンカー×80クラス全てにsigmoidをかけていたのを、
   argmax/しきい値判定はロジット空間のまま行い(sigmoidは単調関数なので大小関係は不変)、
   実際にしきい値を超えた少数の候補にのみsigmoidを適用するよう変更。
   → **yolo_decode: 63.7ms→42.4ms**

**最適化後(同条件)の内訳**:

| ステージ | 処理時間 |
|---|---|
| capture | 2.0ms |
| yolo_infer | 20.2ms |
| yolo_decode | 42.4ms |
| student_infer | 21.0ms |
| student_decode | 3.1ms |
| merge_draw | 40.9ms |
| encode | 3.5ms |
| **合計 / fps** | **~133ms / 6.9fps** |

**残っている高速化の余地(未着手)**:
- [ ] YOLO・生徒モデルのpush/pullを別スレッドに分離し、片方のMLA推論待ちの間にもう片方の
  CPUデコードを進める(パイプライン深化)。yolo_infer+student_infer=約41msがほぼ完全に
  直列化されているため、うまく重ねられれば理論上さらに数fps向上の余地がある。
- [ ] `yolo_decode`のDFLデコード(3スケール分のsoftmax、8400アンカー全件)も、
  低解像度化と同様に「クラス信頼度のargmax/しきい値判定を先に行い、通過したアンカーの
  DFL座標だけ復元する」よう二段階化すれば、さらに削減できる可能性がある(未実施)。
- [ ] `student_interval`(現在1=毎フレーム)を上げて生徒モデルの実行頻度を間引く選択肢も残っている。

---

## 5. Modalix向け 前処理・後処理まとめ

| | 生徒モデル | YOLO |
|---|---|---|
| 入力 | 1×3×384×512 | 1×3×640×640 |
| リサイズ | 640×480 → 512×384 | レターボックス(パディング値 114) |
| 正規化 | RGB, /255, ImageNet mean/std | RGB, /255 のみ |
| CPU後処理 | 5ch bilinear拡大 → argmax | DFLデコード、sigmoid、NMS、マスク復元(係数×proto) |

キャリブ画像(`export/calib_*`)は **正規化前のBGR JPEG**。量子化時に上記の前処理を適用すること。

---

## 6. 決定事項

- 方式A(YOLO-seg + 別セマンティックモデルを後段統合)を採用
- ドアは独立クラス(wall / floor / ceiling / door / other)
- stuffモデルは LRASPP-MobileNetV3(DeepLabV3 は不採用)
- 生徒モデルは lowres 出力 + CPU bilinear 拡大
- YOLO はヘッド切断版を使用し、デコード以降は CPU で処理
- 量子化は両モデルとも INT8(activation/weight とも)、`calib_method=mse`、実データ50枚
- YOLOのC2PSA(MatMul/Softmax)は`attention_surgery.py`でEinsumに置換し、単一MLAセグメントで実行
  (置換前はMLA 3セグメントに分割され実機で`pyneat.Model()`構築が失敗するため必須の対応)

---

## 7. 次の工程

- [x] **Step 2-5**: Palette(Neat 2.1.2)で量子化・コンパイル
  - C2PSA(MatMul/Softmax)は当初 MLA に載らず EV74/A65 実行 → MPKが3 MLAセグメントに分割され
    実機の`pyneat.Model()`構築が失敗する不具合を招いた。`attention_surgery.py`でMatMul→Einsum
    置換して解消(単一MLAセグメント化、詳細は4章参照)
  - 量子化後の生徒 mIoU: 0.879 → 0.863(-0.016)。YOLOはスコア/マスクにquantizationノイズあり
    だが、現行val(hallwayのみ)ではFP32でも弱検出しかなく実質未検証 — 下記の録画データ追加後に再評価
  - 生成物: `export/build/{student_lowres,yolo11n-seg_attnfix_cut}/*_mpk.tar.gz`
- [x] **Step 3**: Modalix パイプライン(`main.py`)実装、**実物のUSBカメラで動作確認済み**
  - `probe_camera.py`でUSBカメラ(Anker PowerConf C200)を検出・設定し、カメラ→2モデル
    push/pull→CPUデコード→パノプティック統合→H.264/RTP送信の全経路を実機で継続動作確認
    (1280x720@30、実測 ~1.8fps、実物体の検出も確認)。詳細は4章のStep 3節を参照
  - fps ~1.8はStep 3としては許容範囲(正しさ優先の初版)。高速化はStep 4で対応
- [x] **Step 4**: 最適化 — **fps 1.8 → 約6.9に改善**(1280x720@30、実測)
  - ステージ別計測(`StageTimer`)でボトルネックを特定 → 実はMLA推論(各20ms)ではなく
    CPU側後処理(特にmerge_draw、~370ms)が支配的だった
  - 適用: instance_maskのbboxサイズ拡大化、パノプティック統合の低解像度化(`merge_downscale`)、
    YOLO NMSのsigmoid遅延評価。詳細・内訳表は4章のStep 4節を参照
  - [ ] (任意・未着手)2モデルpush/pullのパイプライン深化(スレッド分離)、DFLデコードの
    二段階化、`student_interval`の間引き——4章Step 4末尾の「残っている高速化の余地」参照
- [ ] 録画データの追加(家具など実際のCOCO物体を含む部屋、照明バリエーション)、val の拡充
  — YOLOの量子化精度をhallway以外のシーンで再評価するために必須
- [ ] (任意)生徒モデル量子化時の「Weights were set to zero」警告(saturation)の原因調査
  — 現状mIoU劣化は小さい(-0.016)が、モデル変更時は再確認
