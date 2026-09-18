#!/usr/bin/env python3
"""HOSTPC側の専用GUIアプリ(Webcam版) - コーデ辛口ジャッジ・サーバー版のフロントエンド。

`hostpc_gui.py`(ファイル選択で写真を渡す版)の姉妹アプリ。こちらはファイルを
選ぶ代わりに、**このHOSTPCに接続されたWebcamからその場で撮影**して判定に
かける。判定サーバーへの接続・結果表示は`hostpc_gui.py`と全く同じ(そのHTTP
クライアント部分をそのままimportして再利用し、重複させていない)。

DevKit本体ではなく**HOSTPC側で**、`dk`を使わずに直接実行する:

    python3 webcam_hostgui.py

処理の流れ:
  1. 起動時に接続されているWebcamを `/dev/video*` から自動検出し、ドロップ
     ダウンで選択できるようにする(`multillm/vlm_movie.py`と同じ
     `cv2.VideoCapture(device, cv2.CAP_V4L2)`の作法)
  2. 選んだカメラのライブ映像を(元の写真選択版と同じ)縦横2倍のプレビュー欄
     に表示し続ける(別スレッドで継続的にフレームを取得、Tkinterの
     メインループは`root.after`のポーリングでブロックしない)
  3. 「撮影する!」ボタンを押すと、その場で前回の判定結果をクリアし、
     プレビュー上に3→2→1のカウントダウンを表示。0になった瞬間にその時点の
     フレームを1枚キャプチャしてJPEGにエンコードし、ライブ映像の更新を止めて
     撮った写真を静止画として表示したまま、サーバーに送信して判定を待つ
  4. スコア・毒舌コメント・褒めポイント・締めの一言を`hostpc_gui.py`と
     同じレイアウトでウィンドウに表示する

前提(HOSTPC側):
  - `python3-tk` (GUI)
  - `Pillow` (`pip install Pillow`、プレビュー表示・送信前リサイズ用)
  - `opencv-python` (`pip install opencv-python`、Webcam撮影用。
    `multillm/vlm_movie.py`と同じ依存)
  - DevKit上で `fashion_judge_server.py` が起動していて、HOSTPCからその
    ポート(既定8765)にHTTPで到達できること
"""

from __future__ import annotations

import argparse
import glob
import io
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

import hostpc_gui as hg

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - reported to the user at startup instead
    Image = None
    ImageTk = None

try:
    import cv2
except ImportError:  # pragma: no cover - reported to the user at startup instead
    cv2 = None

PINK = hg.PINK
PURPLE = hg.PURPLE
YELLOW = hg.YELLOW
INK = hg.INK
BG = hg.BG
PREVIEW_WIDTH = hg.PREVIEW_WIDTH
PREVIEW_HEIGHT = hg.PREVIEW_HEIGHT

COUNTDOWN_SECONDS = 3

# カメラ入力を-90度回転する(縦置き等でカメラが横向きに映る場合の補正)。
# cv2の回転コード名で指定(逆向きに見える場合は "ROTATE_90_CLOCKWISE" に変更)。
CAMERA_ROTATE_CODE_NAME = "ROTATE_90_COUNTERCLOCKWISE"


@dataclass
class WebcamGuiConfig:
    server_url: str = hg.DEFAULT_SERVER_URL
    judge_timeout_s: int = 60
    camera_device: str | None = None  # 起動時に指定されればこれを初期選択にする


def parse_args(argv: list[str] | None) -> WebcamGuiConfig:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url", default=hg.DEFAULT_SERVER_URL,
                         help=f"fashion_judge_server.pyのURL(既定: {hg.DEFAULT_SERVER_URL})")
    parser.add_argument("--judge-timeout", type=int, default=60,
                         help="1回の判定リクエストのタイムアウト秒数(既定60)")
    parser.add_argument("--camera-device", default=None,
                         help="起動時に選択するカメラデバイス(例: /dev/video0)。"
                              "省略時は検出された最初のカメラ")
    args = parser.parse_args(argv)
    return WebcamGuiConfig(
        server_url=args.server_url.rstrip("/"),
        judge_timeout_s=args.judge_timeout,
        camera_device=args.camera_device,
    )


# ============================================================
# カメラ検出・キャプチャ
# ============================================================

def list_camera_devices() -> list[str]:
    """`/dev/video*` を実際に開いて1フレーム読めるものだけを一覧にする。

    (V4L2は1つの物理カメラに複数のデバイスノードを作ることがあるため、単に
    パスが存在するだけでなく実際にフレームが読めるかまで確認する。)
    """
    if cv2 is None:
        return []
    devices = []
    for path in sorted(glob.glob("/dev/video*")):
        cap = cv2.VideoCapture(path, cv2.CAP_V4L2)
        try:
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    devices.append(path)
        finally:
            cap.release()
    return devices


class CameraStream:
    """バックグラウンドスレッドでWebcamから継続的にフレームを取得する。

    `/workspace/vlm_ngen_demo/viewer.py`の`VideoReceiver`と同じ「専用スレッド
    + ロック付き最新フレーム保持」の作法。ソースがgstプロセスかcv2かの違いのみ。
    """

    def __init__(self, device: str) -> None:
        self.device = device
        self.cap: "cv2.VideoCapture | None" = None
        self.latest_frame = None  # BGR numpy array (uint8 HWC)
        self._lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: "threading.Thread | None" = None

    def start(self) -> bool:
        self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = None
            return False
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True

    def _run(self) -> None:
        rotate_code = getattr(cv2, CAMERA_ROTATE_CODE_NAME)
        while not self.stop_event.is_set():
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame = cv2.rotate(frame, rotate_code)
            with self._lock:
                self.latest_frame = frame
        self.cap.release()

    def get_latest(self):
        with self._lock:
            return None if self.latest_frame is None else self.latest_frame.copy()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)


# ============================================================
# 判定リクエスト(バックグラウンドスレッド、hostpc_gui.judge_via_httpを再利用)
# ============================================================

class CaptureJudgeWorker:
    """撮影したフレーム(JPEGバイト列)をサーバーに送り、結果をキューで渡す。

    ファイル選択版の`hg.JudgeWorker`と同じ形だが、入力がファイルパスではなく
    メモリ上のJPEGバイト列である点だけが違う(撮影した画像は一時ファイルに
    落とさず、そのまま送信する)。
    """

    def __init__(self, cfg: WebcamGuiConfig) -> None:
        self.cfg = cfg
        import queue
        self._queue: "queue.Queue[tuple[bool, object]]" = queue.Queue()

    def submit(self, image_bytes: bytes) -> None:
        thread = threading.Thread(target=self._run, args=(image_bytes,), daemon=True)
        thread.start()

    def _run(self, image_bytes: bytes) -> None:
        try:
            data = hg.judge_via_http(self.cfg, image_bytes)
            self._queue.put((True, data))
        except hg.JudgeError as exc:
            self._queue.put((False, str(exc)))

    def poll(self):
        import queue
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


# ============================================================
# GUI
# ============================================================

class WebcamJudgeGuiApp:
    def __init__(self, root: tk.Tk, cfg: WebcamGuiConfig) -> None:
        self.root = root
        self.cfg = cfg
        self.worker = CaptureJudgeWorker(cfg)
        self.camera: "CameraStream | None" = None
        self.preview_photo: "ImageTk.PhotoImage | None" = None
        self.busy = False
        self.live_preview_active = False
        self.countdown_remaining = 0

        root.title("コーデ辛口ジャッジ (Webcam HOSTPC GUI)")
        root.configure(bg=BG)
        root.geometry("1180x980")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        title = tk.Label(root, text="コーデ辛口ジャッジ", font=("Sans", 20, "bold"),
                          fg=PINK, bg=BG)
        title.pack(pady=(14, 2))
        subtitle = tk.Label(root, text=f"サーバー: {cfg.server_url}",
                             font=("Sans", 10), fg=PURPLE, bg=BG)
        subtitle.pack(pady=(0, 10))

        body = tk.Frame(root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14)

        # --- 左: カメラ選択・プレビュー・撮影 ---
        left = tk.Frame(body, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 14))

        cam_row = tk.Frame(left, bg=BG)
        cam_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(cam_row, text="カメラ:", font=("Sans", 10, "bold"),
                  fg=PURPLE, bg=BG).pack(side=tk.LEFT)
        self.camera_var = tk.StringVar(value="")
        self.camera_combo = ttk.Combobox(cam_row, textvariable=self.camera_var,
                                          state="readonly", width=16)
        self.camera_combo.pack(side=tk.LEFT, padx=(6, 6))
        self.camera_combo.bind("<<ComboboxSelected>>", self.on_camera_selected)
        tk.Button(cam_row, text="更新", command=self.refresh_cameras,
                  font=("Sans", 9), bg="#fff", fg=PURPLE).pack(side=tk.LEFT)

        # 固定ピクセルサイズの外枠でプレビュー領域のサイズを決める(hostpc_gui.py
        # と同じ理由・同じ定数PREVIEW_WIDTH/HEIGHTを使う)。
        preview_frame = tk.Frame(left, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT,
                                  bg="#fbeaf7", relief=tk.RIDGE, bd=2)
        preview_frame.pack_propagate(False)
        preview_frame.pack()
        self.preview_label = tk.Label(
            preview_frame, text="カメラ準備中...", bg="#fbeaf7", fg=PURPLE,
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        # カウントダウン数字はプレビューの上に重ねて表示する(place()で同じ親に
        # 重ね配置。既定は非表示)。
        self.countdown_label = tk.Label(
            preview_frame, text="", font=("Sans", 96, "bold"),
            fg="#fff", bg=PINK,
        )

        self.shoot_btn = tk.Button(
            left, text="撮影する!", command=self.on_shoot,
            font=("Sans", 13, "bold"), bg=PINK, fg="#fff",
            activebackground=PURPLE, relief=tk.RAISED, bd=2, state=tk.DISABLED,
        )
        self.shoot_btn.pack(fill=tk.X, pady=(10, 4))

        self.status_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self.status_var, font=("Sans", 10, "bold"),
                  fg=PURPLE, bg=BG, wraplength=PREVIEW_WIDTH, justify=tk.LEFT).pack(
            pady=(6, 0))

        # --- 右: 結果表示(hostpc_gui.pyと同じレイアウト) ---
        right = tk.Frame(body, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        score_row = tk.Frame(right, bg=BG)
        score_row.pack(fill=tk.X)
        self.score_canvas = tk.Canvas(score_row, width=110, height=110, bg=BG,
                                       highlightthickness=0)
        self.score_canvas.pack(side=tk.LEFT)
        self._draw_stamp(None)

        self.verdict_var = tk.StringVar(value="")
        tk.Label(score_row, textvariable=self.verdict_var, font=("Sans", 15, "bold"),
                  fg=PINK, bg=BG, wraplength=480, justify=tk.LEFT).pack(
            side=tk.LEFT, padx=12, anchor="center")

        tk.Label(right, text="辛口ポイント💅", font=("Sans", 11, "bold"),
                  fg=PURPLE, bg=BG, anchor="w").pack(fill=tk.X, pady=(14, 2))
        self.roasts_text = tk.Text(right, height=6, wrap="word", bg="#fff",
                                    fg=INK, relief=tk.SOLID, bd=1, state=tk.DISABLED)
        self.roasts_text.pack(fill=tk.X)

        tk.Label(right, text="本音の褒めポイント💗", font=("Sans", 11, "bold"),
                  fg=PURPLE, bg=BG, anchor="w").pack(fill=tk.X, pady=(10, 2))
        self.compliment_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.compliment_var, font=("Sans", 11),
                  fg=INK, bg="#f4ebff", wraplength=560, justify=tk.LEFT,
                  padx=10, pady=8).pack(fill=tk.X)

        self.closing_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.closing_var, font=("Sans", 10, "italic"),
                  fg="#8a6ba3", bg=BG, wraplength=560, justify=tk.LEFT).pack(
            fill=tk.X, pady=(10, 0))

        tk.Label(root, text="実行ログ:", font=("Sans", 9, "bold"),
                  fg=PURPLE, bg=BG, anchor="w").pack(fill=tk.X, padx=14, pady=(10, 0))
        self.log_text = tk.Text(root, height=4, wrap="word", bg="#fff", fg="#666",
                                 relief=tk.SOLID, bd=1, state=tk.DISABLED)
        self.log_text.pack(fill=tk.X, padx=14, pady=(0, 12))

        if Image is None:
            messagebox.showwarning(
                "Pillowが見つからない",
                "プレビュー表示にはPillowが必要: pip install Pillow",
            )
        if cv2 is None:
            messagebox.showwarning(
                "OpenCVが見つからない",
                "Webcam撮影にはopencv-pythonが必要: pip install opencv-python",
            )
            self.preview_label.configure(text="opencv-pythonが未インストール")

        if cv2 is not None:
            self.refresh_cameras(initial_device=cfg.camera_device)

        self._poll_worker()
        self._update_preview_loop()

    # -- ログ -- #
    def _log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip("\n") + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- スタンプ描画 -- #
    def _draw_stamp(self, score: "int | None") -> None:
        c = self.score_canvas
        c.delete("all")
        c.create_oval(6, 6, 104, 104, fill=YELLOW, outline=INK, width=4)
        label = str(score) if score is not None else "--"
        c.create_text(55, 46, text=label, font=("Sans", 26, "bold"), fill=INK)
        c.create_text(55, 74, text="/ 100", font=("Sans", 10, "bold"), fill=INK)

    # -- 結果クリア -- #
    def _clear_result(self) -> None:
        self._draw_stamp(None)
        self.verdict_var.set("")
        self.roasts_text.configure(state="normal")
        self.roasts_text.delete("1.0", "end")
        self.roasts_text.configure(state="disabled")
        self.compliment_var.set("")
        self.closing_var.set("")

    # -- カメラ選択 -- #
    def refresh_cameras(self, initial_device: "str | None" = None) -> None:
        devices = list_camera_devices()
        self.camera_combo.configure(values=devices)
        if not devices:
            self.camera_var.set("")
            self.preview_label.configure(text="カメラが見つかりません", image="")
            self.shoot_btn.configure(state=tk.DISABLED)
            self._log(f"[{time.strftime('%H:%M:%S')}] カメラが検出されなかった")
            return

        chosen = initial_device if initial_device in devices else devices[0]
        if chosen != self.camera_var.get() or self.camera is None:
            self.camera_var.set(chosen)
            self._start_camera(chosen)

    def on_camera_selected(self, _event=None) -> None:
        self._start_camera(self.camera_var.get())

    def _start_camera(self, device: str) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera = None
        self.camera = CameraStream(device)
        if self.camera.start():
            self.live_preview_active = True
            self.shoot_btn.configure(state=tk.NORMAL)
            self.status_var.set("")
            self._log(f"[{time.strftime('%H:%M:%S')}] カメラ開始: {device}")
        else:
            self.camera = None
            self.live_preview_active = False
            self.preview_label.configure(text=f"{device} を開けなかった", image="")
            self.shoot_btn.configure(state=tk.DISABLED)
            self._log(f"[{time.strftime('%H:%M:%S')}] カメラを開けなかった: {device}")

    # -- ライブプレビュー更新 -- #
    def _update_preview_loop(self) -> None:
        if self.live_preview_active and self.camera is not None:
            frame_bgr = self.camera.get_latest()
            if frame_bgr is not None and Image is not None:
                self._show_frame(frame_bgr)
        self.root.after(33, self._update_preview_loop)

    def _show_frame(self, frame_bgr) -> None:
        import cv2 as _cv2
        rgb = _cv2.cvtColor(frame_bgr, _cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img.thumbnail((PREVIEW_WIDTH, PREVIEW_HEIGHT))
        self.preview_photo = ImageTk.PhotoImage(img)
        self.preview_label.configure(image=self.preview_photo, text="")

    # -- 撮影 -- #
    def on_shoot(self) -> None:
        if self.busy or self.camera is None:
            return
        self._clear_result()  # ボタンが押された時点で前回の結果を消す
        self.busy = True
        self.shoot_btn.configure(state=tk.DISABLED)
        self.camera_combo.configure(state=tk.DISABLED)
        self.countdown_remaining = COUNTDOWN_SECONDS
        self.countdown_label.place(relx=0.5, rely=0.5, anchor="center")
        self._tick_countdown()

    def _tick_countdown(self) -> None:
        if self.countdown_remaining > 0:
            self.countdown_label.configure(text=str(self.countdown_remaining))
            self.status_var.set(f"あと{self.countdown_remaining}秒で撮影...")
            self.countdown_remaining -= 1
            self.root.after(1000, self._tick_countdown)
        else:
            self.countdown_label.place_forget()
            self._capture_and_send()

    def _capture_and_send(self) -> None:
        frame_bgr = self.camera.get_latest() if self.camera is not None else None
        if frame_bgr is None:
            self.status_var.set("撮影に失敗した(フレームが取得できなかった)")
            self._log(f"[{time.strftime('%H:%M:%S')}] 撮影失敗: フレームなし")
            self._reset_after_judge()
            return

        # ライブ更新を止め、撮った瞬間の1枚を静止画として表示し続ける。
        self.live_preview_active = False
        self._show_frame(frame_bgr)

        import cv2 as _cv2
        ok, buf = _cv2.imencode(".jpg", frame_bgr)
        if not ok:
            self.status_var.set("撮影した画像のエンコードに失敗した")
            self._reset_after_judge()
            return
        image_bytes = buf.tobytes()

        self.status_var.set("審査中... 毒舌ギャルが吟味してるので待ってて💭")
        self._log(f"[{time.strftime('%H:%M:%S')}] 撮影した画像を送信...")
        self.worker.submit(image_bytes)

    def _reset_after_judge(self) -> None:
        self.busy = False
        self.live_preview_active = self.camera is not None
        self.shoot_btn.configure(state=tk.NORMAL if self.camera is not None else tk.DISABLED)
        self.camera_combo.configure(state="readonly")

    def _poll_worker(self) -> None:
        result = self.worker.poll()
        if result is not None:
            ok, payload = result
            if ok:
                self.status_var.set("")
                self._render_result(payload)
                self._log(f"[{time.strftime('%H:%M:%S')}] 判定完了")
            else:
                self.status_var.set("ジャッジに失敗した(下のログ参照)")
                self._log(f"[{time.strftime('%H:%M:%S')}] エラー: {payload}")
            self._reset_after_judge()
        self.root.after(150, self._poll_worker)

    def _render_result(self, data: dict) -> None:
        judge = data.get("judge", {})
        score = judge.get("score")
        self._draw_stamp(score)
        self.verdict_var.set(judge.get("verdict", ""))

        self.roasts_text.configure(state="normal")
        self.roasts_text.delete("1.0", "end")
        for roast in judge.get("roasts", []):
            self.roasts_text.insert("end", f"💅 {roast}\n")
        self.roasts_text.configure(state="disabled")

        self.compliment_var.set(judge.get("compliment", ""))
        self.closing_var.set(judge.get("closing", ""))

    def on_close(self) -> None:
        if self.camera is not None:
            self.camera.stop()
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv if argv is not None else sys.argv[1:])
    print(f"サーバー: {cfg.server_url} (先にDevKit側で fashion_judge_server.py "
          f"を起動しておくこと。--server-urlで変更可)", file=sys.stderr)
    root = tk.Tk()
    WebcamJudgeGuiApp(root, cfg)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
