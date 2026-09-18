# コーデ辛口ジャッジ

全身写真を入力すると、毒舌ギャル系の審査員キャラがファッションコーディネートを
100点満点でジョーク(毒舌)混じりに評価する判定エンジン。
Web版(クラウドAPI利用)、Modalix DevKit向けのエッジ版(CLI単発実行)、
DevKit上に常駐するサーバー版、そのサーバーをHOSTPCから呼び出す専用GUI
(ファイル選択版・Webcam版の2種類)、そしてDevKit本体のHDMI出力+マウスだけで
完結する`devkit_direct_hdmi.py`(HOSTPC不要のキオスク版)を用意している。

`devkit_direct_hdmi.py`はもともとコーデ判定とは無関係の単体ツール(DevKitの
USBカメラ映像をDevKit自身のHDMIにそのまま表示するだけ)として作ったが、
2026-09-10にwebcam_hostgui.pyと同等の撮影→判定→結果表示機能を、Tkinter等の
別ウィンドウを使わずすべてHDMI上のオーバーレイとして統合した。詳細は末尾の
「DevKit直結HDMI出力」節を参照。

## 作業まとめ(2026-09-11追記)

### べた褒めバージョン (`fashion_judge_edge_rave.py`) を新規作成
`fashion_judge_edge.py`(毒舌ギャル系)の派生版として、`JUDGE_PROMPT`の中身
だけを「とにかく全力でべた褒め」に差し替えたバージョンを作成した。批判・
指摘は一切禁止、スコアは基本90〜100点の高得点にする、という指示をVLMに
与えている。YOLO26人物検出・VLM呼び出し・JSON出力スキーマ
(score/verdict/roasts/compliment/closing)は`fashion_judge_edge.py`と
完全に同じで、`fashion_judge_server.py`等から`import fashion_judge_edge`の
代わりに`import fashion_judge_edge_rave`に差し替えるだけで使えるように
互換性を保っている。

実機で`dk ./fashion_judge_edge_rave.py --image 03_m.jpg --json`を実行し、
実際のYOLO26+VLM推論を通して動作確認した。結果はscore=99、verdict
「これ優勝!優勝のオーラ全開の最高傑作!」、roasts/compliment/closingも
すべて批判要素の無い全力の称賛コメントになっていることを実際の出力で
確認した。

### サーバー版にも同様の派生 (`fashion_judge_server_rave.py`) を作成
`fashion_judge_server.py`の派生版として、importするロジックを
`fashion_judge_edge`から`fashion_judge_edge_rave`に差し替えただけの
HTTPサーバーを追加した。エンドポイント・グローバルロック・ウォームアップの
仕組みは元のサーバーと完全に同じ。既定ポートは毒舌版(8765)と衝突しない
よう8766にしてあり、同じDevKit上で両方を同時起動して使い分けられる。

実機で実際に起動し(`dk ./fashion_judge_server_rave.py --port 8766`)、
`GET /health`が`{"ok": true, "yolo_loaded": true, "vlm_loaded": true}`を
返すこと、`POST /judge`に実写真(`03_m.jpg`)をLAN経由(`10.42.0.76:8766`)で
送って実際にYOLO26+VLM推論を通した判定結果(score=99、全力の称賛コメント)
が返ってくることを確認した。あわせて毒舌版サーバー(8765)も引き続き
正常に応答することを確認し、2つのサーバーが同一DevKit上で共存できる
ことを確認済み。

### DevKit直結HDMI出力にも同様の派生 (`devkit_direct_hdmi_rave.py`) を作成
`devkit_direct_hdmi.py`の派生版として、接続先の判定サーバーを毒舌版
(`fashion_judge_server.py`、既定ポート8765)からべた褒め版
(`fashion_judge_server_rave.py`、既定ポート8766)に差し替えた。差分は
`DEFAULT_SERVER_URL`とブランド文字の既定値(「コーデ辛口ジャッジ」→
「コーデべた褒めジャッジ」)、および「審査中...」表示のメッセージ文言のみで、
GStreamerパイプライン・マウス入力・オーバーレイ描画の仕組みはすべて
`devkit_direct_hdmi.py`と同じ。

実機で`--duration`付きのフルパイプライン実行がexit=0で成功することを
確認した上で、撮影シーケンスをタイマーで自動起動する検証専用スクリプトで
`args.server_url`が実際に`http://127.0.0.1:8766`になっていること、実際の
判定結果カード(score=98、「これもう優勝!最高にエモくて神ってる!」等の
全力称賛コメント)が生成されることを確認した。既存の毒舌版
(`devkit_direct_hdmi.py`)や両判定サーバー(8765/8766)にも影響がないことを
確認済み。

## 作業まとめ(2026-09-10追記)

### `devkit_direct_hdmi.py`にwebcam_hostgui.py相当の撮影/判定機能をオーバーレイで統合
DevKit本体のHDMI出力にマウスだけで操作できる「撮影する!」ボタン・3→2→1の
カウントダウン・判定結果カードを全部映像上のオーバーレイとして追加した
(Tkinter等の別ウィンドウは一切使わない、という要望に沿って全部
`gdkpixbufoverlay`でのPNG合成で実現)。HOSTPCもTkinterも不要になり、
DevKit本体+モニタ+USBカメラ+マウスだけでコーデ判定が完結するようになった。

- **マウス入力はevdevで直接読む**: Xorgを止めてkmssinkが直接HDMIに描画する
  構成のため、X11/Waylandのようなクリック座標通知が無い。`sudo apt-get
  install python3-evdev`と`sudo usermod -a -G input sima`を実機で実施し
  (どちらも実機での確認込みで導入)、相対移動イベント(REL_X/REL_Y)を
  自前で積算して仮想カーソル座標を作り、BTN_LEFT押下イベントで「撮影
  ボタン」の矩形と当たり判定している。マウスは実機にLogitech USB Receiver
  Mouseとして実際に接続されており、`evdev.list_devices()`による自動検出で
  実機上で検出できることを確認済み。
- **すべての可変表示はgdkpixbufoverlayの多段チェーン + controllableプロパティ
  変更で実現**: ブランド文字・撮影ボタン・マウスカーソル・カウントダウン・
  判定結果カードの5段を最初からパイプラインに組み込んでおき、`alpha`/
  `offset-x`/`offset-y`/`location`プロパティを実行時に変更するだけで見た目を
  切り替える(パイプラインの再構築はしない)。カーソルは`offset-x/y`だけを
  高頻度(約30Hz)で更新し、`location`(PNGファイル)自体は固定なのでディスク
  I/Oは発生しない。
- **撮影用の静止画は表示用のvideoscaleより手前でteeから取る**: 表示は
  ディスプレイ解像度に(アスペクト比を保たず)引き伸ばすが、判定サーバーに
  送る写真がそれで歪むと困るので、`videoflip`直後(回転済み・引き伸ばし前)
  の映像を`tee`で分岐し、`appsink`(`emit-signals=true max-buffers=1
  drop=true`)で常に最新1フレームだけを保持しておいて、撮影時にそれを
  JPEGエンコードして送る。
- **判定サーバーの宛先は既定でlocalhost**: このスクリプト自体がDevKit本体上
  で動き、`fashion_judge_server.py`も同じDevKit上で動くため、
  `hostpc_gui.py`のようにLAN越しのIPを指定する必要が無く、既定を
  `http://127.0.0.1:8765`にした(`--server-url`で変更可)。
- **実機で発見した不具合**: `countdown_overlay`/`card_overlay`の
  `gdkpixbufoverlay`は、`alpha=0`で見えない設定にしていても`location`に
  指定したファイルをパイプライン起動時(NULL→READY)に即座に開こうとする
  ため、最初はまだ存在しない画像を指していて`Could not load overlay
  image`でパイプライン全体が起動失敗した。1x1の透明PNGを起動時に先に
  書いておくことで解決した。
- **実機での検証**: `dk`経由で本番の`main()`を`--duration`付きで複数回
  実行し、tee+appsink+5段のgdkpixbufoverlay込みのパイプラインがexit=0で
  安定して起動・終了することを確認済み。さらに、`devkit_direct_hdmi`を
  importして`JudgeOverlayController._start_shoot()`をタイマーで直接
  起動する検証専用スクリプトを実機で実行し、カウントダウン→実カメラ
  映像の撮影→JPEGエンコード→実際に稼働中の`fashion_judge_server.py`への
  HTTPリクエスト→本物のYOLO26+VLM判定結果の受信→結果カードPNGの生成
  までを一気通貫で実行し、生成されたカードPNGを実際に画像として目視
  確認した(スコア・辛口コメント・褒めポイント・締めの一言が正しく
  カード状にレイアウトされていることを確認)。撮影ボタン・カーソル・
  カウントダウン数字のPNGも個別に生成して見た目を確認済み。
- **未検証**: 実際に物理マウスでボタン位置をクリックする操作そのもの
  (`_handle_click`の当たり判定を含む一連)は、この開発環境からマウスを
  物理的に操作できないため未検証。実機で実際にクリックして撮影が
  始まるかを確認すること。HDMIモニタでの最終的な見た目(ボタン・
  カーソル・カウントダウン・結果カードの位置関係や可読性)も同様に
  未確認。

### 続き: 回転・レイアウトの調整(同日、統合直後のフィードバックを反映)
上記の初回統合の直後、実機での見た目に関する要望を4段階に分けて反映した。
いずれも実機で`--duration`付きのフルパイプライン実行、または
`videotestsrc`合成による静止画検証で確認しながら進めた。

1. **ボタン・カウントダウン・結果カードもブランド文字と同じ向きに回転**:
   最初の統合時点ではブランド文字だけが右に90度回転していて、ボタン等は
   横書きのままだったため、向きを合わせた。`render_button_png`/
   `render_countdown_png`/`render_card_png`いずれも横書きで描いてから
   `OVERLAY_ROTATE_TRANSPOSE`で回転する形に統一。ボタンは回転後に縦長の
   見た目になるため、クリック判定矩形(`button_rect`)は描画キャンバスの
   縦横を入れ替えたサイズ(回転後のfootprint)で持つように変更した。
   実機で有効/無効ボタン・カウントダウン数字「3」・結果カードそれぞれの
   PNGを生成して画像として目視確認し、いずれもブランド文字と同じ向きに
   なっていることを確認した。
2. **撮影ボタンの位置を画面下部中央→左端・上下中央に変更**:
   ブランド文字(右端・上下中央)と左右対称になるレイアウトにした。
   これに伴い、それまで「ボタンの上」を基準にしていた結果カードの配置も
   単純な画面中央基準に変更した。`videotestsrc`合成の静止画でボタンが
   左端・上下中央に正しく配置されていることを確認した。
3. **判定結果カードの配置を画面中央→画面左端からボタン1つ分右に変更**:
   `offset-x = ボタンの左マージン + ボタンのfootprint幅`(=ボタンの右端)
   で計算し、ボタンに重ならない位置に表示するようにした。実機で撮影
   シーケンスを自動起動して実際の判定結果カードPNGを取得し、ボタンPNGと
   一緒に静止画として合成してボタンのすぐ右に重ならず配置されている
   ことを目視確認した。
4. **「審査中...」表示を画面中央・文字幅にフィットしたコンパクトな
   ウィンドウに変更**: それまでは判定結果カードと同じ固定幅・同じ配置
   (ボタン右)を使い回していたが、短いステータス文言用に
   `render_message_card_png`を新設し、テキストの実際の描画幅
   (`ImageDraw.textlength`)からウィンドウの横幅を決めるようにした
   (無駄な余白が出ない)。配置も画面中央固定にした。実機で撮影
   シーケンスを自動起動し、「審査中...」表示中の`card_overlay`の実際の
   `offset-x`/`offset-y`/`alpha`プロパティを読み取って
   `offset=(919,157) alpha=1.0`(1920x1080画面・カードサイズ82x766の
   ときの理論値`((1920-82)/2, (1080-766)/2)`と一致)を確認し、その後の
   判定結果カードは従来通り`offset=(120,12)`(ボタン右)に切り替わる
   ことも確認した。

## 作業まとめ(2026-09-09追記)

### Webcam版HOSTPC GUI (`webcam_hostgui.py`) を新規作成
`hostpc_gui.py`(ファイル選択版)の姉妹アプリとして、HOSTPCに接続された
Webcamでその場で撮影して判定にかけるGUIを追加した。判定サーバーへの
接続・エラーハンドリング・結果表示部分は`hostpc_gui.py`をそのままimportして
再利用し(`hg.judge_via_http` / `hg.JudgeError` / 色・サイズ定数)、重複させて
いない。カメラ撮影自体は`multillm/vlm_movie.py`と同じ
`cv2.VideoCapture(device, cv2.CAP_V4L2)`の作法を踏襲。

- **`/dev/video*`を自動検出してドロップダウンで選択できるようにした。**
  存在するだけでなく実際に1フレーム読めるデバイスだけを候補にする
  (`list_camera_devices()`)。「更新」ボタンで後から挿したWebcamも再検出できる。
- **ライブプレビュー(縦横2倍、`hostpc_gui.py`と同じ`PREVIEW_WIDTH/HEIGHT`定数
  を共用)** を別スレッドで継続的に更新(`viewer.py`の`VideoReceiver`と同じ
  「専用スレッド+ロック付き最新フレーム保持」の作法をcv2向けに踏襲)。
- **「撮影する!」ボタン**: 押した瞬間に前回の判定結果をクリアし
  (`_clear_result()`、ファイル選択版と同じ挙動)、プレビュー上に3→2→1の
  カウントダウンをオーバーレイ表示。0になった瞬間のフレームを1枚キャプチャ
  してライブ更新を止め、その静止画を表示したままJPEGエンコードしてサーバーに
  送信する。
- **実機なしでの検証:** この開発ホストには物理Webcamが無いため、`cv2`を
  `pip install opencv-python-headless`で導入した上で、実際のカメラ機器の
  代わりに実写真を返す偽の`CameraStream`に差し替え、Xvfb上で本物のTkinter
  ウィジェット・本物のHTTPリクエスト(稼働中のDevKitサーバーへ)を使って
  一連の流れ(カメラ検出→ライブプレビュー→撮影ボタン→カウントダウン
  オーバーレイ→キャプチャ→送信→結果表示→次回撮影時の結果クリア)を
  実際に動かして確認した。スクリーンショットでカウントダウンのオーバーレイ
  表示も視覚的に確認済み。
- **検証の限界:** 実際の物理Webcamでの動作(カメラ列挙・実映像のプレビュー
  品質・撮影タイミングの体感)は未検証。V4L2前提(`cv2.CAP_V4L2`)なので
  Linux HOSTPC専用(Windows/Macでは`cv2.VideoCapture(device)`の指定方法や
  デバイス列挙方法を変える必要がある)。

### DevKit直結HDMI出力 (`devkit_direct_hdmi.py`) を新規作成
コーデ判定とは別の単体ツールとして、DevKitに繋いだUSBカメラの映像を
DevKit自身のHDMI出力にそのまま(推論なしで)映すスクリプトを追加した
(元は`/workspace/devkit_direct_hdmi.py`として作成し、その後この
ディレクトリに移動)。詳細は下記「DevKit直結HDMI出力」節を参照。
画面右端・上下中央に「コーデ辛口ジャッジ」の文字を縦書き
(右に90度回転)でブランドオーバーレイとして重ねる機能も追加している
(日本語フォントが実機に無かったため`fonts-noto-cjk`をaptで導入した上で
Pillowでラスタライズし、
`gdkpixbufoverlay`で合成)。

## 作業まとめ(2026-09-08時点)

### 追記: サーバークライアント方式に変更(SSH方式から移行)
HOSTPC GUIは当初、リクエストのたびにSSH経由でDevKit上の`fashion_judge_edge.py`
をワンショット実行する方式だった。しかしこれには (1) HOSTPCごとにSSH鍵の
登録が必要、(2) HOSTPC上の実パスとDevKit側パスが食い違う環境がある、
(3) 毎回YOLO26・VLM両モデルを一からロードするので1リクエストに約10〜30秒
かかる、という3つの問題があった。そこで **DevKit上でモデルをロードしたまま
常駐するHTTPサーバー (`fashion_judge_server.py`) を新設し、HOSTPC GUIはSSHを
一切使わずHTTPで画像を送るだけ**、という構成に変更した。

- **新規ファイル `fashion_judge_server.py`。** `fashion_judge_edge.py`の
  `detect_person_bbox()` / `judge_fashion()` をそのままimportして使う薄い
  HTTPラッパー(`http.server.ThreadingHTTPServer`、標準ライブラリのみ、
  Flask等の追加依存なし)。`GET /health`、`POST /judge`(画像バイナリを
  そのままボディに乗せる)の2エンドポイント。起動時にYOLO26・VLM両モデルを
  即座にロードしておくので、以後のリクエストはモデルロード待ちなしで
  応答できる。MLA(推論アクセラレータ)はボードに1つしかない共有リソース
  なので、リクエストはグローバルロックで直列化している。
- **`hostpc_gui.py`をHTTPクライアントに全面書き換え。** SSH関連のコード
  (`ssh_target`/`ssh_identity`/pyneat venv活性化ロジック/HOSTPC-DevKit間の
  パス変換)を全て削除し、`urllib.request`で`POST {server_url}/judge`に
  画像バイナリを送るだけのシンプルな作りにした。送信前にPillowで長辺2000px
  まで縮小するようにもした(転送量を抑えるため)。
- **実機テストで2つの重要な問題を発見・修正(サーバー側の実装で):**
  1. **モデルの入力サイズが最初のリクエストの画像サイズに固定される
     不具合。** `ModelOptions.preprocess.input_max_width/height`に大きい
     上限(例: 4096)を設定してモデルを構築しておけば、以後どの解像度の
     画像でもそのまま使い回せると想定していたが、実機テストで誤りと判明:
     最初に実際にpush(=run)した画像の実サイズで容量が確定してしまい、
     次に来たそれと異なる解像度の画像で
     `input height N exceeds configured capacity M`(Mは最初の画像の高さ)
     というエラーになった。「起動時に大きいダミー画像で一度runして容量を
     確保する」対策も試したが、これは逆にパイプライン全体が恒常的に
     タイムアウトする状態を引き起こし悪化させただけだった(この対策は撤回
     済み)。最終的に、CLI版(`fashion_judge_edge.py`単発実行)と同じ
     「その画像の実サイズでビルドする」方式を採用し、**画像サイズが変わる
     たびに`detect_person_bbox()`が透過的にモデルを再ビルドする**ように
     修正(再ビルド自体は約400msと軽量で、実質的なコストはほぼ無い)。
     3種類の解像度が異なる画像を交互に計6回送って全て成功することを
     実機で確認済み。
  2. 上記の発見自体、`people.jpg`(427px高)で複数回成功した後に別の
     解像度の画像(`images.jpeg`, 501px高)に切り替えた瞬間に初めて
     再現した — 同じ画像を繰り返すだけのテストでは見つからなかった問題
     だった。
- **性能: SSH方式(約28秒/リクエスト、モデル毎回ロード)からサーバー方式
  (約10秒/リクエスト、モデル常駐)に改善。** 短縮分はほぼ丸ごとモデル
  ロード時間の削減。
- **実機での完全な統合テスト済み(2026-09-08):** DevKit上で
  `fashion_judge_server.py`を起動し、`curl`での直接HTTPリクエスト、および
  `hostpc_gui.py`をXvfb上で実際に起動してのボタン操作の両方で、
  複数画像・複数回の判定が正しく完走することを確認した(詳細は各セクション
  の「動作確認」)。
- SSH方式時代の詳細な調査記録(pyneat venv活性化・SSH鍵登録・パス取り違え
  の3つの実機不具合とその修正)は、今後同種の統合を行う際の参考として
  下記「(過去の記録) SSH方式のHOSTPC GUI」に残してある。

### (過去の記録) SSH方式時代のHOSTPC GUI追加
エッジ版 (`fashion_judge_edge.py`) をHOSTPC側から呼び出す専用のTkinter GUI
アプリを追加した。`/workspace/vlm_ngen_demo/viewer.py`(HOSTPC側でpyneat非依存
で動く映像+ログビューアー)を最も近い既存アプリとして踏襲している。

- **`fashion_judge_edge.py` に `--json` フラグを追加。** 元々ANSIカラー付き
  ターミナル表示専用だったのを、GUI等の別プロセスから呼び出せるよう
  `--json` 指定時は結果を1行のJSONとしてstdoutに出す(進捗ログは全てstderrに
  移動)モードを追加。成功/失敗どちらも `{"ok": true/false, ...}` の1行JSONで
  返すようにした。
- **実機テストで重要な問題を発見・修正:** GUIから素の `ssh sima@10.42.0.76
  python3 fashion_judge_edge.py ...` を直接叩くと `pyneat が見つからない` で
  失敗することが実機確認で判明した。原因は `dk`/`devkit-run`(このワーク
  スペース専用のラッパー関数で、HOSTPC実機には無い)が実行前にpyneat用のvenv
  を`source`しているため。`type devkit-run` でその候補パスリストと優先順位を
  確認し、`hostpc_gui.py` 内で同じロジック(`/media/nvme/pyneat/bin/activate`
  等を順に探してsource)を再現することで解決した。
- **実機での完全な統合テスト済み:** モックではなく実際に
  `sima@10.42.0.76` へ素の`ssh`(`dk`不使用)で接続し、venv有効化 →
  `fashion_judge_edge.py --json` 実行 → stdout末尾のJSON行を抽出、という
  一連の流れをHOSTPC GUI側のコードと同一の関数で実行して成功を確認した
  (結果は`dk`経由の実行と完全一致)。
- **GUIウィンドウそのものも実際に起動して操作を検証済み。** この開発ホストには
  元々tkinterが無かったが、`sudo apt install python3-tk python3-pil
  python3-pil.imagetk xvfb` で導入し、`Xvfb`(仮想ディスプレイ)上で
  `hostpc_gui.py` を本物のTkinterウィンドウとして起動。ファイル選択ダイアログ
  だけは自動化できないため写真選択後の状態を直接セットして代替したが、それ
  以降(ジャッジボタン押下 → バックグラウンドスレッド → 実際のDevKitへの
  SSH推論 → `root.after`ポーリングでの結果反映)は本物のUIコード・本物の
  ウィジェットで実行し、スタンプの数字・吹き出しテキスト・褒めポイント・
  締めの一言の各ウィジェットに正しい値が反映されることを確認した(詳細は
  下記「動作確認」)。

### 当初の作業まとめ

このREADMEに書かれていた仕様と実際のファイル状態を突き合わせ、以下を実施した。

- **Web版 (`fashion_judge.html`) を新規作成。** READMEにはUI/処理フロー/JSON
  スキーマの仕様が詳しく書かれていたが、ファイル自体が存在しなかった。
  プリクラ風UI・washi tapeポラロイド枠・回転スタンプバッジ・吹き出しステッカー
  など仕様通りに実装し、ブラウザから直接Anthropic API (`/v1/messages`) を叩く
  単一HTMLファイルとして作成した。APIキーは画面上部の入力欄から受け取り
  `localStorage` にのみ保存(サーバー送信なし)、CORS制限は
  `anthropic-dangerous-direct-browser-access: true` ヘッダーで回避している
  (Anthropic TypeScript SDKの実装を確認して採用)。
- **エッジ版 (`fashion_judge_edge.py`) のダミー実装を実際のpyneat API呼び出し
  に差し替え。** `detect_person_bbox()` と `judge_fashion()` が固定値/画像全体
  bboxを返すだけのダミーだったのを、隣接する実機検証済みアプリ
  `vlm_ngen_demo` と同じ構成(`pyneat.Model` + `Model.run()` によるYOLO26人物
  検出、`pyneat.genai.VisionLanguageModel` + `GenerationRequest` によるVLM
  判定)に置き換えた。YOLO26モデルアーカイブ
  (`yolo26m-det-bf16-mla_tess-b1.tar.gz`)も `vlm_ngen_demo/assets/` から
  `assets/` にコピーして同梱した。
- **README記載の誤りを修正。**
  - Web版のモデルIDが古い `claude-sonnet-4-6` になっていたのを、現行の
    推奨デフォルト `claude-opus-5` に修正。
  - エッジ版のVLMパスは一度 `gemma4-E4B-it` に「修正」したが、これは
    `vlm_ngen_demo` の設定デフォルト値を鵜呑みにした誤りだった。実際に
    `dk` 経由でDevKit上の `/media/nvme/llima/models/` を `ls` して確認した
    ところ `gemma-4-E2B-it-GPTQ-a16w4` と `gemma-4-E4B-it-GPTQ-a16w4` の
    両方が存在し、元のREADMEにあった「Gemma 4 E2B」表記の方が正しかった。
    最終的に軽量な E2B (`gemma-4-E2B-it-GPTQ-a16w4`) を使う実装に確定。
- **エッジ版をDevKit実機で実際に実行し、動作確認済み。** `dk` 経由で
  `/workspace/core/tests/images/people.jpg` を使い、YOLO26人物検出→VLM判定
  →ターミナル表示まで一連の流れをエラーなく通した(詳細は下記「動作確認」)。
- **検証の限界:** Web版はこの開発ホストにブラウザ実行環境がないため、
  HTML/JSの構文・構造チェックのみで、ブラウザでの実操作は未検証。

## 構成

| ファイル | 内容 |
|---|---|
| `fashion_judge.html` | Web版。ブラウザ完結のインタラクティブなHTMLアプリ |
| `fashion_judge_edge.py` | エッジ版。Modalix DevKit上で動かすPythonパイプライン(CLI単発実行、`--json`で機械可読出力も可)。検出/判定ロジック自体はここに実装されており、サーバー版もこれをimportして使う |
| `fashion_judge_edge_rave.py` | エッジ版の派生・べた褒めバージョン。`JUDGE_PROMPT`だけを毒舌からべた褒めに差し替えたもので、JSON出力スキーマは`fashion_judge_edge.py`と完全互換(`import fashion_judge_edge_rave as fje`で差し替え可能) |
| `fashion_judge_server.py` | サーバー版。`fashion_judge_edge.py`のロジックをDevKit上でHTTPサーバーとして常駐させる(モデル常駐でリクエストごとのロード待ちなし) |
| `fashion_judge_server_rave.py` | サーバー版の派生・べた褒めバージョン。`fashion_judge_edge_rave.py`をimportする以外は`fashion_judge_server.py`と同じ仕組み。既定ポートは8766(毒舌版8765と衝突しないよう分けてあり、同時起動も可) |
| `hostpc_gui.py` | HOSTPC GUI(ファイル選択版)。サーバー版にHTTPで画像を送って結果を表示する専用Tkinterアプリ |
| `webcam_hostgui.py` | HOSTPC GUI(Webcam版)。`hostpc_gui.py`の姉妹アプリ。HOSTPCのWebcamでその場で撮影して判定する |
| `devkit_direct_hdmi.py` | DevKit本体単体で完結するキオスク版。USBカメラ映像をHDMIにそのまま表示し、マウスクリック→カウントダウン→撮影→判定→結果カード表示までを全部映像上のオーバーレイで行う(HOSTPC・Tkinter不要) |
| `devkit_direct_hdmi_rave.py` | `devkit_direct_hdmi.py`の派生・べた褒めバージョン。接続先を`fashion_judge_server_rave.py`(既定ポート8766)に差し替え、ブランド文字も「コーデべた褒めジャッジ」にしたもの。それ以外の仕組みは完全に同じ |

---

## Web版 (`fashion_judge.html`)

### 概要
- 全身写真をアップロードすると、Claude(Anthropic API, vision機能)に画像を渡して
  ファッション評価をJSON形式で取得し、結果を装飾したUIで表示する
- 審査員キャラは「毒舌ギャル系」。辛口だが人格否定はせず、最後に一言フォローが入る
- 評価対象は服・色使い・バランス・小物・トレンド感のみ。体型や顔立ちなど外見への
  言及は禁止する指示をプロンプトに明記している

### UI/デザイン
- プリクラ(写真シール機)風の見た目。写真は washi tape 付きのポラロイド風フレームに
  セットする
- 判定後はスコアを回転したスタンプバッジで表示、毒舌コメントは吹き出しステッカー
  風に並べる
- カラー: マゼンタピンク `#FF2E93` / パープル `#7B2FF7` / イエロー `#FFE93D`
  (スタンプ用) / プラムブラック `#2B0A2E`(文字)
- フォント: 「M PLUS Rounded 1c」の丸ゴシック一本

### 処理フロー
1. 画像をファイル選択 → Canvas で長辺1024pxにリサイズしてJPEG化(base64化)
2. 「ジャッジしてもらう!」クリックで Anthropic API (`/v1/messages`, `claude-opus-5`)
   に画像+プロンプトを送信
3. レスポンス(JSON文字列)をパースし、スコア/一言評価/毒舌コメント配列/褒めポイント/
   締めの一言を画面に反映

**注意:** 当初「claude-sonnet-4-6」と書いていたが、これは古いモデルID。現行の
推奨デフォルトである `claude-opus-5` に修正済み。

### APIキーの扱い
バックエンドを持たない単一HTMLファイルなので、ブラウザからAnthropic APIへ直接
リクエストを送る(`anthropic-dangerous-direct-browser-access: true` ヘッダーで
CORS制限を回避)。初回アクセス時に画面上部でAPIキーの入力を求め、
`localStorage` にのみ保存する(サーバーへの送信は一切なし)。

**セキュリティ上の注意:** ブラウザで直接APIキーを扱う都合上、開発者ツールの
ネットワークタブ等からキーが見える。個人の検証用途を想定しており、他人と共有
するPC・端末での利用は避けること(利用後は「キーを削除」で消せる)。

### 出力JSONスキーマ
```json
{
  "score": 0-100の整数,
  "verdict": "全体評価の一言キャッチコピー",
  "roasts": ["毒舌コメント1", "毒舌コメント2", "..."],
  "compliment": "本音の褒めポイント",
  "closing": "締めの一言"
}
```

### 使い方
ブラウザでファイルを開くだけで動作する(単一HTMLファイル、外部ビルド不要)。

### 動作確認
HTML/JS の構文と div タグの対応、DOMを持たない状態でのJS構文チェックのみ
確認済み。ブラウザでの実動作(ファイル選択→リサイズ→API呼び出し→表示)は
未検証 — 下記TODO参照。

---

## エッジ版 (`fashion_judge_edge.py`)

### 概要
Web版と同じ判定ロジックを、Modalix DevKit上でオフライン・エッジ推論として動かす
パイプライン。既存の隣接アプリ [[vlm_ngen_demo]](webcam版YOLO26人物検出+VLM
コメント)と同じ pyneat API を踏襲し、YOLO26による人物検出・クロップ →
`pyneat.genai.VisionLanguageModel` 経由の Gemma 4 (E2B) による判定、という
2段構成にしている。

### 処理フロー
1. 静止画ファイルを読み込み
2. YOLO26 (`assets/yolo26m-det-bf16-mla_tess-b1.tar.gz`, COCO class 0 = person)
   で人物を検出し、bbox に余白(12%)を付けてクロップ
3. クロップ画像を Gemma 4 (E2B, `pyneat.genai.VisionLanguageModel` 経由) に渡し、
   Web版と同じ毒舌ギャル系プロンプトで判定JSONを取得
4. JSONをパースし、ANSIカラー付きでターミナルに表示

### 実装状況
`detect_person_bbox()` / `judge_fashion()` とも実際の pyneat API 呼び出しに
差し替え済み(隣接する `vlm_ngen_demo` で実機検証済みのモデル・オプションを
そのまま流用)、かつ下記の通りDevKit実機で動作確認済み:

- `detect_person_bbox()` — `pyneat.Model` + `Model.run()` によるYOLO26の同期
  一発推論(`Model.build([seed], ...)` はこのデバイスでタイムアウトするため
  不使用)。人物が見つからない場合は画像全体をbboxとして返す。
- `judge_fashion()` — `pyneat.genai.VisionLanguageModel` + `GenerationRequest`
  によるGemma 4 (E2B) 呼び出し。VLMモデルディレクトリは
  `/media/nvme/llima/models/gemma-4-E2B-it-GPTQ-a16w4`
  (DevKit上で `ls /media/nvme/llima/models` を実行して実在を確認したパス。
  同じ場所に `gemma-4-E4B-it-GPTQ-a16w4` も存在するので、より高精度な判定が
  欲しい場合は `--vlm-model-dir` でそちらに切り替えられる)。

### 起動方法
DevKitへは `/workspace` がNFS共有されているので、このホスト側から `dk`
経由でリモート実行する。実行ファイルに実行権限(`chmod +x`)は不要。

```bash
# 1. DevKitへの疎通を確認(reachable になっていることを確認する)
dk status

# 2. taste_in_clothes ディレクトリに移動してから実行する
#    (dkはカレントディレクトリを基準に相対パスを解決するため)
cd /workspace/taste_in_clothes

# 3. まずヘルプで引数だけ確認(モデルロードなしで一瞬で返る、疎通確認用)
dk ./fashion_judge_edge.py --help

# 4. 実画像で実行(初回はYOLO26ロード+VLMロードで数十秒〜数分かかる)
dk ./fashion_judge_edge.py --image /workspace/core/tests/images/people.jpg

# モデルパスを差し替える場合
dk ./fashion_judge_edge.py --image path/to/photo.jpg \
    --model-path ./assets/yolo26m-det-bf16-mla_tess-b1.tar.gz \
    --vlm-model-dir /media/nvme/llima/models/gemma-4-E4B-it-GPTQ-a16w4

# --json: ANSI表示の代わりに結果を1行のJSONでstdoutに出す
# (進捗ログは全てstderrに出るので、stdoutは常にJSON1行だけ拾えばよい。
#  HOSTPC GUI (hostpc_gui.py) はこのモードで呼び出している)
dk ./fashion_judge_edge.py --image /workspace/core/tests/images/people.jpg --json
```

DevKit上のpyneat環境に直接入って実行したい場合(`dk shell` で入った後、または
DevKit本体にログインした状態)は `python fashion_judge_edge.py --image ...`
と直接呼べる。このホスト(開発ワークスペース側)には `pyneat` が入っていない
ため、`python fashion_judge_edge.py` をこのホストで直に実行してもエラーになる
— 必ず `dk` 経由か、DevKit上のpython環境から実行すること。

### 動作確認(実機で確認済み)
`dk ./fashion_judge_edge.py --image /workspace/core/tests/images/people.jpg`
をDevKit実機で実行し、以下を確認した(2026-09-08):

- YOLO26人物検出: `bbox=(247,94,373,244) conf=0.94` — 正しく人物を検出
- VLM判定: `gemma-4-E2B-it-GPTQ-a16w4` ロード成功
  (`accepts_image=True`)、206トークン生成・22.5 tok/s・TTFT 0.93秒
- 判定JSONのパース・ANSIカラー付きターミナル表示まで一連の流れがエラーなく
  完走(スコア65、辛口コメント4件、褒めポイント、締めの一言まで正常に出力)

### 入出力方式(確定事項)
- 入力: 事前に用意した画像ファイル(ライブカメラ撮影ではない)
- 検出前処理: YOLOで人物検出・クロップを行ってからVLMに渡す
- 出力: ターミナル表示のみ(HDMIオーバーレイやWebダッシュボードへの送信はなし)

---

## サーバー版 (`fashion_judge_server.py`)

### 概要
エッジ版(`fashion_judge_edge.py`)は「1プロセス=1画像」のCLI単発実行なので、
リクエストのたびにYOLO26・VLM両モデルのロードが発生し重い。サーバー版は
DevKit上でこの2つのモデルをロードしたまま常駐し、HOSTPC等のクライアントから
HTTPで画像を受け取ってJSON判定結果を返す。検出/判定ロジック自体は
`fashion_judge_edge.py`をそのままimportして使い、重複させていない。
標準ライブラリの`http.server.ThreadingHTTPServer`のみを使い、Flask等の
追加依存はない。

### エンドポイント
| メソッド/パス | 内容 |
|---|---|
| `GET /health` | `{"ok": true, "yolo_loaded": bool, "vlm_loaded": bool}` |
| `POST /judge` | ボディに画像バイナリ(JPEG/PNG等、Content-Typeは問わない)をそのまま乗せる。応答は`fashion_judge_edge.py --json`と同じスキーマ: `{"ok": true, "bbox": {...}, "judge": {...}}` または `{"ok": false, "error": "..."}` |

MLA(推論アクセラレータ)は1枚のボードに1つしかない共有リソースなので、
リクエストはグローバルロックで直列化している(同時に複数の推論を投げると
不安定になりうる、`vlm_ngen_demo`側の教訓を踏襲)。

### 起動方法
```bash
cd /workspace/taste_in_clothes
dk ./fashion_judge_server.py --port 8765
```
起動時にYOLO26・VLM両モデルを即座にロードする(数十秒程度かかる)。
`dk`はフォアグラウンドでプロセスを保持するので、常駐させ続けたい場合は
ターミナルを閉じないか、`dk shell`で入って`tmux`/`screen`等の中で動かすか、
バックグラウンド実行にすること。Ctrl+Cで終了できる。

モデルパスを差し替える場合や、起動時のウォームアップサイズを変える場合:
```bash
dk ./fashion_judge_server.py --port 8765 \
    --vlm-model-dir /media/nvme/llima/models/gemma-4-E4B-it-GPTQ-a16w4 \
    --warmup-width 1280 --warmup-height 960
```

### 実装上の重要な注意点(実機で発見・修正した不具合)
**「大きい上限を設定してモデルを1回だけビルドし使い回す」方式は実機で
不安定だった。** 当初、複数の異なる解像度の画像を継続して処理できるよう、
`ModelOptions.preprocess.input_max_width/height`に十分大きい値(4096)を
設定してモデルを1回だけビルドし、起動時にダミー画像で一度runして
「容量を確保」しておく設計にしていた。しかし実機テストで以下が判明:

- この状態で複数の異なる解像度の画像を交互に送ると
  `input height N exceeds configured capacity M` というエラーになった
  (Mは最初に実際にrunした画像の高さ — 設定した上限4096ではなく、実際に
  最初にpushされた画像の実サイズで容量が確定してしまうらしい)。
- 「起動時に4096×4096のダミー画像で一度runしておけば容量が4096に確定する
  はず」という追加の対策も試したが、これは効果がないどころか**パイプライン
  全体がその後ずっとタイムアウトし続ける壊れた状態**を引き起こし、むしろ
  悪化させた(このダミーrunによる対策は撤回済み)。

**最終的な解決策:** CLI版と同じ「その画像の実サイズでモデルをビルドする」
方式を採用し、`detect_person_bbox()`が画像サイズの変化を検知して透過的に
再ビルドするようにした(`_yolo_model_size`でビルド済みサイズを記憶し、
一致しなければ再ビルド)。再ビルド自体は約400msと軽量なので、サーバーの
起動時ウォームアップは「よくある写真サイズ」の目安(既定1280x960)で
一度ビルドしておくだけの、初回リクエスト短縮のための最適化に留めている。

### 動作確認(実機、2026-09-08)
`dk ./fashion_judge_server.py --port 8765`で起動し、`curl`で直接HTTP
リクエストを送って以下を確認した:

- `GET /health` → `{"ok": true, "yolo_loaded": true, "vlm_loaded": true}`
- 解像度の異なる3枚の画像(427px高・501px高・368px高)を交互に計6回
  `POST /judge`し、全て`"ok": true`で正しい判定結果(スコア・毒舌コメント等)
  が返ることを確認(1回目に発見した上記の不具合を修正した後の再検証)
- 1リクエストあたり約10秒(SSH方式では約28秒だった — モデルロード分が
  丸ごと短縮された)

---

## HOSTPC GUI (`hostpc_gui.py`)

### 概要
エッジ版/サーバー版のロジックはDevKit上でしか動かない(pyneat/MLAが必要)
ため、写真選択や結果表示といったUI操作を**HOSTPC側**で行い、実際の推論だけ
DevKitに投げる専用アプリ。`/workspace/vlm_ngen_demo/viewer.py`(HOSTPC側で
pyneatに依存せず動くビューアー)と同じ「HOSTPC-side app」の作法を踏襲して
いる。**2026-09-08にSSH方式からHTTP方式に変更済み**(下記「(過去の記録)」
参照) — SSH鍵の設定やパスのすり合わせは一切不要で、DevKit側でサーバーが
起動していてHTTPで到達できさえすればよい。

### 動作の仕組み
1. HOSTPC上でファイル選択ダイアログから写真を選ぶ(プレビュー表示にPillowを
   使用)
2. 必要ならPillowで長辺2000pxまで縮小してJPEGバイト列にする
3. `POST {server_url}/judge`(既定 `http://10.42.0.76:8765/judge`)でDevKit
   上の常駐サーバー(`fashion_judge_server.py`)にバックグラウンドスレッド
   から送信し、JSON結果を受け取る(Tkinterのメインループをブロックしない
   よう、`viewer.py`と同じ「ワーカースレッド + `root.after`ポーリング」の
   作法)
4. スコア・毒舌コメント・褒めポイント・締めの一言をウィンドウに表示

### 使い方
```bash
# 1. DevKit側でサーバーを起動しておく(まだの場合)
cd /workspace/taste_in_clothes    # DevKit側
dk ./fashion_judge_server.py --port 8765

# 2. HOSTPC側で(DevKitでも、このNeat開発ワークスペース側でもない)
sudo apt install python3-tk        # 初回のみ(Tkinter)
pip install Pillow                 # 初回のみ(写真プレビュー表示・送信前リサイズ用)

cd /workspace/taste_in_clothes    # ← HOSTPC上でこのプロジェクトが実際にある場所
python3 hostpc_gui.py
```

サーバーのURLやタイムアウトはCLI引数で変更できる:
```bash
python3 hostpc_gui.py --server-url http://10.42.0.76:8765 --judge-timeout 60
```

`実行ログ`欄に「〜に接続できない」と出る場合は、DevKit側でサーバーが
起動しているか(`curl http://10.42.0.76:8765/health` が通るか)、
`--server-url` のホスト・ポートが合っているかを確認すること。HTTP方式に
なったため、以前のSSH方式で必要だった鍵登録やパス設定は一切不要。

### 動作確認(GUI実物 + サーバー実機、2026-09-08)
`Xvfb :99` 上で新しい(HTTPクライアント版)`hostpc_gui.py` を実際に起動し、
実際に稼働中の `fashion_judge_server.py` に対してボタン操作から一連の流れを
検証した:

- 写真選択(プレビュー画像セット)→ ジャッジボタン押下 → バックグラウンド
  スレッドが `POST /judge` を実際のDevKitサーバーへ送信 → 約9秒で応答 →
  スタンプ(スコア)・評価コピー・辛口ポイント4件・褒めポイント・締めの
  一言の各ウィジェットに正しく反映されることを確認
- サブタイトルに接続先サーバーURL(`サーバー: http://10.42.0.76:8765`)が
  正しく表示されることも確認
- (ファイル選択ダイアログ自体はGUI操作を自動化できないため、選択後の状態を
  直接セットして代替。それ以外は本物のTkinterウィジェット・本物のHTTP
  リクエストでの確認)

### (過去の記録) SSH方式時代の実機不具合と修正
以下は2026-09-08にサーバークライアント方式へ移行する前、SSH経由で
`fashion_judge_edge.py`を毎回リモート実行していた時代に、実機フィード
バックを受けて発見・修正した3つの不具合の記録。**現在のHTTP方式では
SSH自体を使わないため、これらの問題はいずれも構造的に発生しない**が、
今後似た「HOSTPC↔DevKit間でファイル/コマンドをやり取りするツール」を
作る際の参考として残す。

1. **写真プレビューが潰れる表示崩れ。** `tk.Label`の`width`/`height`は
   テキスト表示時は文字単位、画像表示時はピクセル単位と解釈が変わる。
   プレースホルダー文字列「写真未選択」用に`width=32, height=16`(文字単位
   のつもり)を指定していたが、写真を読み込んで`image=`を設定した瞬間に
   同じ数値が「32×16ピクセル」として再解釈され、プレビューが極小の壊れた
   表示になっていた。固定ピクセルサイズの外側`Frame`
   (`pack_propagate(False)`)でサイズを決め、中の`Label`はテキスト/画像
   どちらでもそのサイズいっぱいに表示する構成に変更して解決(この修正は
   現行版にもそのまま引き継がれている)。
2. **SSH認証エラー(`Permission denied (publickey,password)`)。** 当初
   「ssh-agentの`SSH_AUTH_SOCK`が引き継がれていないのでは」と推測したが
   誤りで、実際の原因はDevKit側`~/.ssh/authorized_keys`に登録されている
   鍵が全て`devkit-sync@ghcr-io-sima-neat-sdk-*`(このNeat SDK開発ワーク
   スペースが起動のたびに自動登録する専用鍵)で、**HOSTPC実機はこの自動
   登録の対象外で、そもそも鍵が1つも登録されていなかった**こと。HOSTPCで
   新規に鍵を作成し、その公開鍵をDevKit側の`authorized_keys`に追記して
   解決した。
3. **HOSTPC上の実パスとDevKit側パスの取り違え。** SSH認証を直しても
   `python3: can't open file '/home/shimizu/neat_2.1.2/taste_in_clothes/
   fashion_judge_edge.py'`で失敗。原因は「自分自身の`__file__`から求めた
   パス」を、HOSTPC上でのファイルコピー先とDevKit側へのSSHコマンド引数の
   **両方**に使い回していたこと(この開発ワークスペースでは両者がたまたま
   同じ`/workspace/...`だったため見つからなかったが、実際のHOSTPCでは
   `~/neat_2.1.2/taste_in_clothes`とDevKit側`/workspace/taste_in_clothes`
   で別物だった)。HOSTPC上の実パスとDevKit側パスを明確に分離する対応で
   解決したが、根本的にはサーバー方式への移行(HOSTPCがファイルパスを
   DevKit側に伝える必要自体をなくす)でこの種の問題が構造的に起きなく
   なった。

---

## Webcam HOSTPC GUI (`webcam_hostgui.py`)

### 概要
`hostpc_gui.py`(ファイル選択版)の姉妹アプリ。写真ファイルを選ぶ代わりに、
HOSTPCに接続されたWebcamでその場で撮影して判定にかける。判定サーバーへの
HTTPクライアント部分(`hg.judge_via_http` / `hg.JudgeError`)や配色・
プレビューサイズの定数(`hg.PINK`等 / `hg.PREVIEW_WIDTH/HEIGHT`)は
`hostpc_gui.py`をそのままimportして再利用しており、重複させていない。
Webcam撮影自体は`multillm/vlm_movie.py`と同じ
`cv2.VideoCapture(device, cv2.CAP_V4L2)`の作法を踏襲している。

### 動作の仕組み
1. 起動時に`/dev/video*`を実際に開いてフレームが読めるものだけを検出し、
   ドロップダウンで選択できるようにする(「更新」ボタンで再検出も可能)
2. 選んだカメラのライブ映像を(取得した各フレームに`-90`度回転を適用した
   上で)、ファイル選択版と同じ縦横2倍のプレビュー欄
   (`PREVIEW_WIDTH`×`PREVIEW_HEIGHT`)に別スレッドから継続的に表示する
   (`vlm_ngen_demo/viewer.py`の`VideoReceiver`と同じ「専用スレッド+
   ロック付き最新フレーム保持」の作法。ソースがgstプロセスかcv2かの違いのみ)。
   回転は`CameraStream._run()`内、フレーム取得直後の1箇所だけで行っており、
   ライブプレビューと実際に送信するキャプチャ画像の両方に一貫して適用される。
   回転方向は`CAMERA_ROTATE_CODE_NAME`(既定`"ROTATE_90_COUNTERCLOCKWISE"`)
   で決めており、実機で向きが逆に見える場合は`"ROTATE_90_CLOCKWISE"`に
   書き換えるだけでよい(「-90度」の回転方向自体はソフトウェアや文脈により
   時計回り/反時計回りの解釈が分かれるため、実際の物理カメラの向きに
   合わせて調整する前提)
3. 「撮影する!」ボタンを押すと、その瞬間に前回の判定結果をクリアし
   (ファイル選択版と同じ`_clear_result()`)、プレビュー上に3→2→1の
   カウントダウンを重ねて表示する(`place()`で同じ親フレームに重ね配置)
4. カウントダウンが0になった瞬間のフレームを1枚キャプチャし、ライブ更新を
   止めてその静止画を表示したままJPEGエンコード、サーバーに送信する
5. 結果はファイル選択版と全く同じレイアウト(スタンプ・辛口ポイント・
   褒めポイント・締めの一言)で表示する

### 使い方
```bash
# HOSTPC側で
sudo apt install python3-tk        # 初回のみ(Tkinter)
pip install Pillow                 # 初回のみ
pip install opencv-python          # 初回のみ(Webcam撮影用)

cd /workspace/taste_in_clothes
python3 webcam_hostgui.py
```

サーバーURLや起動時に選ぶカメラはCLI引数で変更できる:
```bash
python3 webcam_hostgui.py --server-url http://10.42.0.76:8765 \
    --camera-device /dev/video2
```

### 動作確認(2026-09-09、実機Webcamなしでの検証)
この開発ホストには物理Webcamが無いため、`opencv-python-headless`を導入した
上で、実際のカメラ機器の代わりに実写真を返す偽の`CameraStream`に差し替え、
Xvfb(仮想ディスプレイ)上で本物のTkinterウィジェットを使って以下を確認した:

- カメラ検出→ドロップダウン表示→ライブプレビュー(縦横2倍)の自動開始
- 「撮影する!」押下 → その場で結果表示がクリアされる(スタンプ`--`、
  辛口ポイント欄が空になる等)ことを、2回目の撮影クリック直後に確認
- カウントダウン("2"等)がプレビュー中央にピンクのオーバーレイで正しく
  表示されることをスクリーンショットで視覚確認
- カウントダウン終了後、実際に稼働中のDevKitサーバーへ本物のHTTPリクエスト
  を送信し、スコア・評価コピー・辛口ポイント4件が正しくウィジェットに
  反映されることを確認(判定完了まで約9秒)
- 判定完了後、ライブプレビューとボタン操作が正常に再開されることを確認

**検証の限界:** 実際の物理Webcamでの動作(カメラ列挙・実映像の見え方・
撮影タイミングの体感)は未検証。`cv2.CAP_V4L2`前提のためLinux HOSTPC専用。

---

## DevKit直結HDMI出力 (`devkit_direct_hdmi.py`)

### 概要
DevKitに繋いだUSBカメラの映像を、DevKit自身のHDMI出力に表示するツール。
他の全コンポーネントがUDP RTP配信(離れたPC等で`gst-launch-1.0 udpsrc ...
! autovideosink`で見る)またはHOSTPC側のHTTPクライアント(`hostpc_gui.py`/
`webcam_hostgui.py`)なのに対し、これはDevKit本体に繋いだモニタとマウスだけ
で完結するキオスク版で、HOSTPCもTkinterも不要。もとは単純なカメラ
パススルー表示だけだったが、2026-09-10に`webcam_hostgui.py`相当の撮影/判定
機能をHDMI映像上のオーバーレイとして統合した(下記「撮影/判定機能」参照)。

pyneatにはローカルディスプレイ出力ノードが無い(`VideoSenderOptions`は
UDP RTP配信専用)ため、pyneatの`Graph`/`Model`は使わず、素のGStreamer
(`gi.repository.Gst`)でパイプラインを直接組み立てて実行している。

### 実機で判明した制約と対策
DevKit(sima@10.42.0.76)で実際に動かして初めて分かった制約が4つある:

1. **HDMI出力は起動時からXorg(lightdm)が握っている。** 素のGStreamerには
   `ximagesink`/`xvimagesink`が入っておらず(入っているのは`kmssink`/
   `waylandsink`/`fbdevsink`/`gtkwaylandsink`のみで、Waylandコンポジタは
   動いていない)、`kmssink`で直接HDMIに描くにはXorgにDRMマスターを
   明け渡してもらう必要がある。そのため本スクリプトは実行中だけ
   `sudo systemctl stop lightdm`でXorgを止め、終了時(正常終了/Ctrl-C/
   `--duration`満了/例外いずれでも)に必ず`systemctl start lightdm`で
   復帰させる。`sima`ユーザーはパスワード無しsudoが設定済みであることを
   実機で確認済み。
2. **搭載GPUがSiliconMotion smifb (PCI 126F:0768)** で、`kmssink`のドライバ
   自動判定に失敗し`Could not open DRM module`で落ちる。`driver-name=smifb`
   の明示が必須(他機種のDevKitでは値が変わりうるので`--driver-name`で
   変更可能にしてある)。
3. **smifbのオーバーレイプレーンは実際にはスケーリング非対応。** `kmssink`の
   `can-scale`プロパティは対応を名乗るが、カメラ解像度と出力解像度が
   食い違うと`drmModeSetPlane failed: Invalid argument`で落ちる。
   `videoscale`を挟んでディスプレイの実解像度に事前に(CPU側で)
   スケーリングしておく必要がある。
4. **smifbはasync page flip / 従来のvblank ioctl (`drmWaitVBlank`)に非対応。**
   デフォルト設定の`kmssink`はvsync待ちの失敗を致命的なフローエラーとして
   パイプライン全体を落とす(`skip-vsync=true`を付けないと1フレーム目で
   `Internal data stream error`になる)。`skip-vsync=true`が必須。

さらに、画面右端・上下中央に縦書きで出すブランド文字「コーデ辛口ジャッジ」
は日本語なのでGStreamerの`textoverlay`(pangoベース)では出せない --
実機には`textoverlay`プラグイン自体が入っておらず、しかも日本語フォントも
一切入っていなかった(`fc-list`で確認)。`sudo apt-get install -y
fonts-noto-cjk`で日本語フォントを導入した上で、Pillowでテキストを
透過PNG(白文字+黒縁取り、背景色を問わず視認できるように)にラスタライズし、
`gdkpixbufoverlay`でカメラ映像の上に合成している。

文字の向きは映像の回転(`--rotate`)とは切り離してあり、常に固定で右に
90度回転して表示する(映像を`--rotate none`にしても文字は回転したまま)。
そのため`gdkpixbufoverlay`は`videoflip`より**後**、最終的なディスプレイ
解像度の座標系に置き、PNG自体をPillowの`Image.Transpose.ROTATE_270`
(=時計回りに90度)で事前回転してから合成している。配置は画面右端
(`OVERLAY_RIGHT_MARGIN`px、`gdkpixbufoverlay`の`offset-x`に負値を渡すと
「右端からのpx」になる仕様を利用)・上下方向は中央
(`offset-y = (display_height - overlay_height) / 2`)。

### 撮影/判定機能(`webcam_hostgui.py`相当をHDMIオーバーレイで統合)
`--no-judge`を付けない限り常に有効。画面左端・上下中央の「撮影する!」
ボタン(ブランド文字と左右対称の配置)をマウスでクリックすると、3→2→1の
カウントダウン(画面中央に大きく表示)→撮影→「審査中...」表示(画面中央、
文字列の実際の幅に合わせたコンパクトなウィンドウ)→判定結果カード表示
(画面左端から、ボタン1つ分右。ボタンに重ならない位置)、という一連の
流れが`hostpc_gui.py`/`webcam_hostgui.py`と同じ流れで進む。すべて
`gdkpixbufoverlay`によるPNGオーバーレイで表示し、Tkinter等の別ウィンドウは
一切使わない。「審査中...」やエラー時の短いメッセージは
`render_message_card_png`(文字列の幅にフィットする専用のコンパクトな
ウィンドウ、画面中央固定)、判定結果本体は`render_card_png`(固定幅で
スコア・辛口コメント等を並べる、ボタン右固定)と、見た目・配置とも
別のロジックを使い分けている。

- **マウス入力**: Xorgを止めてkmssinkが直接HDMIに描画しているため、
  X11/Waylandのようなクリック座標通知が無い。`python3-evdev`(apt)を導入し
  `sima`ユーザーを`input`グループに追加した上で、マウスの相対移動イベント
  (`REL_X`/`REL_Y`)を自前で積算して仮想カーソル座標を作り、`BTN_LEFT`の
  押下イベントで「撮影ボタン」の矩形と当たり判定している
  (`MouseTracker`クラス)。カーソル自体も黄色いクロスヘアとして
  オーバーレイ表示する。
- **常時5段のgdkpixbufoverlay**: ブランド文字・撮影ボタン・マウスカーソル・
  カウントダウン・判定結果カードの5段を最初からパイプラインに組み込んでおき、
  `alpha`(表示/非表示)・`offset-x`/`offset-y`(位置)・`location`(画像
  ファイル)といういずれも実行時変更可能(controllable)なプロパティを
  変更するだけで見た目を切り替える。パイプライン自体は起動後一度も
  再構築しない。
- **ボタン・カウントダウン・結果カードもブランド文字と同じ向きに回転**:
  いずれも横書きで描いてから`OVERLAY_ROTATE_TRANSPOSE`(右に90度)で
  回転して保存する(マウスカーソルは形が対称なので回転していない)。
  ボタンは元々横長のテキストボタンとして描き、回転後は縦長の見た目に
  なるため、クリック判定矩形(`button_rect`)は描画キャンバスの縦横を
  入れ替えたサイズで持つ(`button_content_width/height`が回転前の描画
  サイズ、`button_rect`が実際に画面に出る回転後のfootprint)。
  カウントダウン・結果カードは`render_countdown_png`/`render_card_png`が
  回転後の実サイズを返すので、配置側は特に意識せずそのまま中央寄せに
  使える。
- **撮影用の静止画は表示用のvideoscaleより手前で取る**: 表示はディスプレイ
  解像度に(アスペクト比を保たず)引き伸ばすが、判定サーバーに送る写真が
  それで歪むと困るので、`videoflip`直後(回転済み・引き伸ばし前)の映像を
  `tee`で分岐し、`appsink`(`emit-signals=true max-buffers=1 drop=true`)
  で常に最新1フレームだけをキャッシュしておき、撮影時にそれをJPEGエンコード
  して送る。
- **判定サーバーの宛先**: このスクリプト自体がDevKit本体上で動き、
  `fashion_judge_server.py`も同じDevKit上で動くため、既定で
  `http://127.0.0.1:8765`(`--server-url`で変更可)。事前に
  `dk ./fashion_judge_server.py --port 8765`等でサーバーを起動しておく
  必要があるのは他のGUI版と同じ。
- **`--no-judge`**: 撮影/判定機能を丸ごと無効化し、単純なカメラパススルー
  表示のみにする(マウスや判定サーバーが無い環境向け、またはevdevが
  入っていない場合は自動的にこのモードにフォールバックする)。

### 使い方(DevKit上、`dk`経由)
```bash
cd /workspace/taste_in_clothes
dk ./devkit_direct_hdmi.py                                          # 既定設定で起動
dk ./devkit_direct_hdmi.py --camera-device /dev/video0 --width 1280 --height 720 --fps 30
dk ./devkit_direct_hdmi.py --no-overlay                              # ブランド文字なし
dk ./devkit_direct_hdmi.py --overlay-text "べつのテキスト"
dk ./devkit_direct_hdmi.py --rotate none                             # 回転なし(既定は右に90度回転)
dk ./devkit_direct_hdmi.py --no-judge                                # 撮影/判定機能を無効化(単純パススルーのみ)
dk ./devkit_direct_hdmi.py --server-url http://127.0.0.1:8765        # 判定サーバーの宛先(既定localhost)
dk ./devkit_direct_hdmi.py --duration 10                             # 10秒だけ表示して自動終了(動作確認用)
```
判定機能を使う場合は事前にDevKit上で`fashion_judge_server.py`を起動して
おくこと(`dk ./fashion_judge_server.py --port 8765`)。マウスで画面下部の
「撮影する!」ボタンをクリックするとカウントダウン後に撮影し、結果カードが
画面に表示される。
映像の回転は`videoflip`の`method`プロパティで行っている(既定
`clockwise`=右に90度回転)。`--rotate`で`none`/`clockwise`/`rotate-180`/
`counterclockwise`/`horizontal-flip`/`vertical-flip`/
`upper-left-diagonal`/`upper-right-diagonal`に変更可能。カメラは
横長(既定1280x720)で撮っているため、90度回転すると縦長の映像になり、
それを横長のディスプレイ解像度(既定1920x1080)へ`videoscale`で
引き伸ばして表示する点に注意(アスペクト比を保ったレターボックス表示には
していない)。
Ctrl-Cで終了(lightdmは自動的に再開する)。`--duration`は`dk`経由のように
Ctrl-Cが届きにくい実行環境での動作確認用に用意した時間指定の自動終了。

### 動作確認(実機、2026-09-09)
- `dk ./devkit_direct_hdmi.py --duration 6`を実行し、HDMI解像度自動検出
  (1920x1080)→パイプライン起動→6秒後自動終了→lightdm自動復帰まで
  exit=0で確認済み。
- 実際のUSBカメラ(Anker PowerConf C200, `/dev/video0`)からの映像で
  同一パイプラインが動作することを確認済み。
- ブランド文字オーバーレイ: `fonts-noto-cjk`導入後、Pillowで実際に
  「コーデ辛口ジャッジ」をラスタライズしたPNGを取得し、日本語グリフが
  正しく描画されることを画像として目視確認済み(導入前は文字化けではなく
  完全に無描画になることも実機で確認した)。オーバーレイ込みの
  パイプラインも`--duration`付きで実機実行しexit=0を確認済み。
- 一時PNG(`/tmp/devkit_direct_hdmi_overlay_<pid>.png`)は終了時に
  確実に削除されることを確認済み。
- `--rotate`既定(`clockwise`、右に90度回転)込みのパイプラインを
  `--duration`付きで実機実行しexit=0を確認済み。
- ブランド文字を映像の回転とは切り離し、常に右90度固定で表示する構成に
  変更した後も、`--duration`付きで実機実行しexit=0を確認済み。合成後の
  オーバーレイPNGの実ピクセルサイズがログ上で横長→縦長(例:
  543x75→75x543)に変わっていること、`gdkpixbufoverlay`が
  `videoflip`より後(最終ディスプレイ解像度側)に来ていることを
  パイプライン文字列で確認済み。
- 配置を「画面上部中央」→「画面右端・上下中央」に変更した際、実機上で
  `kmssink`の代わりに`videotestsrc ! ... ! gdkpixbufoverlay ... !
  videoconvert ! pngenc ! filesink`という別パイプラインを組んで実際に
  1920x1080のPNG静止画として書き出し、**実際の合成結果を画像として
  目視確認した**(HDMIモニタは見えないが、`gdkpixbufoverlay`が実際に
  合成するピクセルはこの方法で検証できる)。結果、文字が画面右端に
  正しく寄っており、上下方向でも中央に来ていること、かつ文字が
  上から下に向かって読める向き(=時計回りに90度回転)になっていることを
  確認できた。
- **撮影/判定機能(2026-09-10)**: `python3-evdev`のapt導入・`sima`ユーザーの
  `input`グループ追加も実機で実施済み。`evdev.list_devices()`による自動
  検出で実機接続の「Logitech USB Receiver Mouse」を実際に検出できることを
  確認済み。tee+appsink+5段の`gdkpixbufoverlay`込みのフルパイプラインを
  `dk`経由の本番`main()`で`--duration`付きで複数回実行し、exit=0で安定して
  起動・終了することを確認済み。さらに、`devkit_direct_hdmi`をimportして
  `JudgeOverlayController._start_shoot()`をタイマーで直接起動する検証専用
  スクリプトを実機で実行し、カウントダウン→実カメラ映像の撮影→JPEG
  エンコード→実際に稼働中の`fashion_judge_server.py`へのHTTPリクエスト→
  本物のYOLO26+VLM判定結果の受信→結果カードPNGの生成、という一連を実際に
  実行して、生成されたカードPNGを画像として目視確認した(スコア・辛口
  コメント・褒めポイント・締めの一言が正しくレイアウトされていることを
  確認)。撮影ボタン(有効/無効状態)・マウスカーソル・カウントダウン数字の
  PNGも個別に生成して見た目を確認済み。countdown/card用の
  `gdkpixbufoverlay`が起動時に`location`のファイルを即座に開こうとして
  `Could not load overlay image`で落ちる不具合を実機で発見し、1x1透明PNGの
  事前生成で修正した。
- **ボタン・カウントダウン・結果カードの回転(2026-09-10)**: タイマーで
  撮影シーケンスを自動起動する実機検証スクリプトで、有効/無効ボタン・
  カウントダウン数字「3」・結果カードそれぞれのPNGを実際に生成し画像として
  目視確認した。いずれもブランド文字「コーデ辛口ジャッジ」と同じ向き
  (時計回りに90度)で回転しており、ボタンのクリック判定矩形
  (`button_rect`)も回転後のfootprint(縦長)で正しく計算されていることを
  ログ(`button_rect: (915, 710, 90, 320)`、1920x1080の画面で幅90×高さ320)
  で確認済み。フルパイプライン(tee+appsink+5段オーバーレイ)も
  `--duration`付きで実機実行しexit=0を確認済み。
- **ボタン位置を画面下部中央→左端・上下中央に変更(2026-09-10)**:
  結果カードの配置も(それまでは「ボタンの上」を基準にしていたが)単純に
  画面中央基準に変更した。`kmssink`の代わりに`videotestsrc ! ... !
  gdkpixbufoverlay ... ! pngenc ! filesink`で実際に合成した静止画を目視し、
  ボタンが画面左端・上下中央に(ブランド文字の右端・上下中央と左右対称に)
  正しく配置されていることを確認済み。
- **結果カードの配置を画面中央→左端からボタン1つ分右に変更(2026-09-10)**:
  `offset-x = ボタンの左マージン + ボタンのfootprint幅`(=ボタンの右端)で
  計算し、上下方向は変更前と同じ画面中央のまま。実機で撮影シーケンスを
  タイマーで自動起動し、実際の判定結果カードPNGを取得した上で、それを
  ボタンPNGと一緒に`videotestsrc`合成の静止画として書き出し、カードが
  ボタンのすぐ右に重ならず配置されていることを目視確認した。
- **「審査中...」を画面中央・文字幅フィットのコンパクトなウィンドウに変更
  (2026-09-10)**: 新設した`render_message_card_png`は`render_card_png`と
  違い、テキストの実際の描画幅(`ImageDraw.textlength`)からカードの横幅を
  決めるため、無駄な余白が出ない。実機で撮影シーケンスを自動起動し、
  「審査中...」表示中に`card_overlay`の実際の`offset-x`/`offset-y`/`alpha`
  プロパティを読み取ったところ`offset=(919,157) alpha=1.0`(1920x1080画面で
  カードサイズ82x766のとき理論値である`((1920-82)/2, (1080-766)/2)`と一致)
  となり、画面中央に正しく配置されていることを確認した。その後の判定結果
  カードは従来通り`offset=(120,12)`(ボタン右)に変わることも確認済み。
  生成されたコンパクトなカードPNG自体も画像として目視確認した。

### 未検証・注意点
- 上記の静止画検証によりオーバーレイの位置・回転方向の**合成ロジック自体**
  は確認できたが、実際にHDMIモニタの画面を目視できる環境がこの開発環境には
  無いため、実機のモニタでの最終的な見た目(文字の大きさが見やすいか、
  映像の回転方向(右90度)が意図通りか、撮影ボタン・カーソル・カウント
  ダウン・結果カードの位置関係や可読性)はまだ確認できていない。実機の
  モニタで見た目を確認すること(映像の向きが逆の場合は
  `--rotate counterclockwise`に変更。文字の向きを変えたい場合は
  `devkit_direct_hdmi.py`の`OVERLAY_ROTATE_TRANSPOSE`定数を変更)。
- **物理マウスで実際にボタンをクリックする操作自体は未検証**(この開発
  環境からマウスを物理的に操作できないため)。撮影シーケンス自体
  (カウントダウン→撮影→送信→結果表示)はタイマーで直接起動する形で
  実機検証済みだが、`_handle_click`のクリック位置とボタン矩形の当たり
  判定は実際にクリックして確認する必要がある。
- `driver-name=smifb`・GPU固有の制約(スケーリング非対応・vblank非対応)は
  このDevKit個体のGPU(SiliconMotion smifb)に特有の可能性がある。別の
  DevKit個体やGPUでは`--driver-name`の変更や、場合によっては制約自体が
  異なる可能性がある。
- `lightdm`を止めている間はDevKit本体のデスクトップ/ログイン画面が
  使えなくなる。他の作業でXorgセッションを使う可能性がある場合は
  タイミングに注意。

## 判定トーン設定(共通)
Web版・エッジ版とも同じ方針:

- 毒舌度: かなり辛口・毒舌
- スコア表示: 100点満点
- 審査員キャラ: 毒舌ギャル風
- 体型・外見への言及は禁止し、服・スタイリングの評価に限定

## 今後のTODO
- [ ] Web版をブラウザで実際に開き、写真選択→リサイズ→API呼び出し→結果表示の
      一連を実APIキーで通す(このホストではブラウザを起動して確認していない)
- [x] ~~DevKit実機での動作検証~~ → 完了(2026-09-08、上記「動作確認」参照)
- [ ] 複数枚の実際の全身写真で判定JSONの安定度(自由記述に流れてJSON崩れ
      しないか)・YOLO26のスコア閾値(現状0.40)が人物を安定して拾えるかを
      1枚だけでなく数パターン試して確認
- [ ] Gemma 4 E2B と E4B (`--vlm-model-dir` で切り替え可能)で判定の質・速度を
      比較し、どちらをデフォルトにするか決める
- [x] ~~HOSTPC GUI (`hostpc_gui.py`) の起動・操作確認~~ → 完了(2026-09-08、
      Xvfb上で実際にウィンドウを起動し実DevKit推論まで確認済み。上記
      「動作確認」参照。ファイル選択ダイアログ自体のみ自動化の都合で未検証)
- [x] ~~サーバークライアント方式への移行~~ → 完了(2026-09-08、
      `fashion_judge_server.py`新設・`hostpc_gui.py`をHTTPクライアントに
      全面書き換え。モデル入力サイズの固定不具合を発見・修正し、実機で
      複数解像度・複数回のリクエストが成功することを確認済み)
- [ ] HOSTPC実機(本物のディスプレイ・本物のファイル選択ダイアログ、かつ
      新しいHTTPクライアント版)での最終確認 — Xvfbでの検証は済んでいるが、
      実際のデスクトップ環境での見た目・操作感はまだ未確認
- [ ] `fashion_judge_server.py`の常駐運用方法を詰める(現状`dk`はフォア
      グラウンド実行なので、電源断・再起動後も自動で立ち上がるようにする
      には systemd サービス化やDevKit起動スクリプトへの組み込みが必要)
- [ ] サーバーへの同時多重リクエスト(複数人が同時にHOSTPC GUIを使う等)
      が来た場合の挙動を確認 — 現状はグローバルロックで直列化しているので
      壊れはしないはずだが、待ち時間が積み重なる体感は未検証
- [x] ~~Webcam版HOSTPC GUI (`webcam_hostgui.py`) の新規作成~~ → 完了
      (2026-09-09、偽カメラ+Xvfbで一連の流れを検証済み。上記「動作確認」参照)
- [ ] `webcam_hostgui.py`を実際の物理Webcamで動作確認する(このホストには
      Webcamが無いため未検証。カメラ列挙・実映像のプレビュー品質・撮影
      タイミングの体感を確認すること)
- [x] ~~DevKit直結HDMI出力 (`devkit_direct_hdmi.py`) の新規作成~~ → 完了
      (2026-09-09、実機で`--duration`付き実行・実USBカメラでの動作・
      日本語ブランド文字オーバーレイのラスタライズを確認済み。上記
      「DevKit直結HDMI出力」節「動作確認」参照)
- [ ] `devkit_direct_hdmi.py`を実際のHDMIモニタで目視確認する(この開発
      環境には画面を見る手段が無いため未検証。映像が正しく映るか、
      オーバーレイの位置・文字サイズが見やすいか、既定の90度回転
      (`--rotate clockwise`)の向きが意図通りか(逆向きなら
      `--rotate counterclockwise`に変更)を確認すること)
- [x] ~~`devkit_direct_hdmi.py`にwebcam_hostgui.py相当の撮影/判定機能を
      HDMIオーバーレイで統合~~ → 完了(2026-09-10、python3-evdev導入・
      inputグループ追加・実マウス検出・tee+appsink+5段gdkpixbufoverlayの
      実機パイプライン起動・タイマー直接起動での撮影→実judge→結果カード
      生成までの一気通貫実行を確認済み。上記「撮影/判定機能」「動作確認」
      参照)
- [ ] `devkit_direct_hdmi.py`で実際に物理マウスをクリックして撮影ボタンが
      反応するかを確認する(この開発環境からマウスを物理操作できないため
      未検証。`_handle_click`の当たり判定・カウントダウン・結果カード
      表示までの一連が実際のクリック操作で動くことを確認すること)
