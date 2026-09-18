#!/usr/bin/env python3
"""HOSTPC側の専用GUIアプリ(コーデ辛口ジャッジ・サーバー版のフロントエンド)。

DevKit本体ではなく**HOSTPC側で**、`dk`を使わずに直接実行する
(/workspace/vlm_ngen_demo/viewer.py と同じ「HOSTPC-side viewer」の作法を踏襲):

    python3 hostpc_gui.py

このアプリ自体はpyneatに依存しない。サーバークライアント方式(2026-09-08〜):
DevKit上で `fashion_judge_server.py` を常駐させ、HOSTPC側のこのGUIはHTTP
経由で画像を送って判定結果を受け取るだけ。処理の流れ:

  1. 写真ファイルを選ぶ(ローカルのファイル選択ダイアログ)
  2. 選んだ写真を(必要なら長辺2000pxにリサイズして)JPEGバイト列にする
  3. `POST {server_url}/judge` でDevKit上の常駐サーバーに送信し、JSON結果を
     受け取る(バックグラウンドスレッドから。Tkinterのメインループをブロック
     しないよう、`viewer.py`と同じ「ワーカースレッド + `root.after`ポーリング」
     の作法)
  4. スコア・毒舌コメント・褒めポイント・締めの一言をウィンドウに表示する

**このアプリを使う前にDevKit側でサーバーを起動しておくこと:**
    cd /workspace/taste_in_clothes    # DevKit側
    dk ./fashion_judge_server.py --port 8765

以前はSSH経由でDevKit上の`fashion_judge_edge.py`を毎回リモート実行していたが、
(1) SSH認証設定(鍵の登録)がHOSTPCごとに必要、(2) HOSTPC上の実パスとDevKit
側パスの不一致、(3) 毎回YOLO26・VLM両モデルを再ロードするコスト、という
3つの問題があった。サーバー方式ではDevKit側にモデルを常駐させておくため、
HOSTPC側はHTTPを話せればよいだけになり、この3つが全て解消される。

前提(HOSTPC側):
  - `python3-tk` (GUI) と `Pillow` (`pip install Pillow`、写真プレビュー表示・
    送信前リサイズ用)
  - DevKit上で `fashion_judge_server.py` が起動していて、HOSTPCからその
    ポート(既定8765)にHTTPで到達できること(同じLAN上にあればよく、SSH
    鍵の設定は不要)
"""

from __future__ import annotations

import argparse
import io
import json
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover - reported to the user at startup instead
    Image = None
    ImageTk = None

DEFAULT_SERVER_URL = "http://10.42.0.76:8765"
MAX_UPLOAD_DIM = 2000  # 送信前にこの長辺(px)を超える場合のみ縮小する
PREVIEW_WIDTH = 520   # 写真プレビュー欄のサイズ(px)。元の260x340から縦横2倍
PREVIEW_HEIGHT = 680

PINK = "#FF2E93"
PURPLE = "#7B2FF7"
YELLOW = "#FFE93D"
INK = "#2B0A2E"
BG = "#fff6fb"


@dataclass
class GuiConfig:
    server_url: str = DEFAULT_SERVER_URL
    judge_timeout_s: int = 60  # サーバー常駐でモデルロード済みなので短くてよい


def parse_args(argv: list[str] | None) -> GuiConfig:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL,
                         help=f"fashion_judge_server.pyのURL(既定: {DEFAULT_SERVER_URL})。"
                              "先にDevKit側で `dk ./fashion_judge_server.py --port 8765` "
                              "等でサーバーを起動しておくこと")
    parser.add_argument("--judge-timeout", type=int, default=60,
                         help="1回の判定リクエストのタイムアウト秒数(既定60。"
                              "サーバー起動直後でモデルロードがまだ終わっていない"
                              "場合はもっとかかることがある)")
    args = parser.parse_args(argv)
    return GuiConfig(server_url=args.server_url.rstrip("/"), judge_timeout_s=args.judge_timeout)


# ============================================================
# サーバー呼び出し(バックグラウンドスレッド)
# ============================================================

class JudgeError(Exception):
    pass


def _resize_for_upload(local_image_path: Path) -> bytes:
    """送信前に長辺MAX_UPLOAD_DIMpxまで縮小してJPEGバイト列を作る。

    Pillowが無い場合はファイルをそのまま送る(サーバー側は任意サイズを
    受け付けるので機能上は問題ない。単に転送量が増えるだけ)。
    """
    if Image is None:
        return local_image_path.read_bytes()

    img = Image.open(local_image_path).convert("RGB")
    if max(img.size) > MAX_UPLOAD_DIM:
        img.thumbnail((MAX_UPLOAD_DIM, MAX_UPLOAD_DIM))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def judge_via_http(cfg: GuiConfig, image_bytes: bytes) -> dict:
    """HTTP POST /judge でDevKit上の常駐サーバーに画像を送り、結果のdictを返す。"""
    req = urllib.request.Request(
        f"{cfg.server_url}/judge",
        data=image_bytes,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.judge_timeout_s) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        # サーバーはエラー時も {"ok": false, "error": ...} のJSONをボディに返す。
        body = exc.read()
    except urllib.error.URLError as exc:
        raise JudgeError(
            f"{cfg.server_url} に接続できない: {exc.reason}\n"
            "DevKit側で `dk ./fashion_judge_server.py --port ...` を起動済みか、"
            "--server-url が正しいか確認してください。"
        ) from exc
    except TimeoutError as exc:
        raise JudgeError(
            f"{cfg.judge_timeout_s}秒待っても応答がなかった。サーバー起動直後で"
            "モデルロード中の可能性がある。しばらく待つか --judge-timeout を"
            "増やしてください。"
        ) from exc

    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JudgeError(f"サーバーからの応答がJSONとして読めなかった: {body[:300]!r}") from exc

    if not data.get("ok"):
        raise JudgeError(data.get("error", "不明なエラー"))
    return data


class JudgeWorker:
    """1回分の判定リクエストをバックグラウンドスレッドで実行し、結果をキューで渡す。"""

    def __init__(self, cfg: GuiConfig) -> None:
        self.cfg = cfg
        self._queue: "queue.Queue[tuple[bool, object]]" = queue.Queue()

    def submit(self, local_image_path: Path) -> None:
        thread = threading.Thread(target=self._run, args=(local_image_path,), daemon=True)
        thread.start()

    def _run(self, local_image_path: Path) -> None:
        try:
            image_bytes = _resize_for_upload(local_image_path)
            data = judge_via_http(self.cfg, image_bytes)
            self._queue.put((True, data))
        except JudgeError as exc:
            self._queue.put((False, str(exc)))
        except OSError as exc:
            self._queue.put((False, f"写真の読み込みに失敗した: {exc}"))

    def poll(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


# ============================================================
# GUI
# ============================================================

class FashionJudgeGuiApp:
    def __init__(self, root: tk.Tk, cfg: GuiConfig) -> None:
        self.root = root
        self.cfg = cfg
        self.worker = JudgeWorker(cfg)
        self.selected_path: Path | None = None
        self.preview_photo: "ImageTk.PhotoImage | None" = None
        self.busy = False

        root.title("コーデ辛口ジャッジ (HOSTPC GUI)")
        root.configure(bg=BG)
        root.geometry("1180x980")

        title = tk.Label(root, text="コーデ辛口ジャッジ", font=("Sans", 20, "bold"),
                          fg=PINK, bg=BG)
        title.pack(pady=(14, 2))
        subtitle = tk.Label(root, text=f"サーバー: {cfg.server_url}",
                             font=("Sans", 10), fg=PURPLE, bg=BG)
        subtitle.pack(pady=(0, 10))

        body = tk.Frame(root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=14)

        # --- 左: 写真選択・プレビュー ---
        left = tk.Frame(body, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 14))

        # 固定ピクセルサイズの外枠でプレビュー領域のサイズを決める。tk.Labelの
        # width/height はテキスト表示時は文字単位、image表示時はピクセル単位と
        # 解釈が変わるため、Label自体にwidth/heightを指定すると「プレースホルダー
        # 文字列用の32×16」がそのまま「画像用の32×16px」に化けて表示が潰れる
        # (実機のスクリーンショットで確認した実際の不具合)。外側のFrameで
        # pack_propagate(False)によりサイズを固定し、中のLabelはテキスト/画像
        # どちらでもそのサイズいっぱいに表示されるようにする。
        preview_frame = tk.Frame(left, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT,
                                  bg="#fbeaf7", relief=tk.RIDGE, bd=2)
        preview_frame.pack_propagate(False)
        preview_frame.pack()
        self.preview_label = tk.Label(
            preview_frame, text="写真未選択", bg="#fbeaf7", fg=PURPLE,
        )
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        pick_btn = tk.Button(left, text="写真を選ぶ", command=self.on_pick_image,
                              font=("Sans", 12, "bold"), bg="#fff", fg=PURPLE,
                              relief=tk.RAISED, bd=2)
        pick_btn.pack(fill=tk.X, pady=(10, 4))

        self.judge_btn = tk.Button(
            left, text="ジャッジしてもらう!", command=self.on_judge,
            font=("Sans", 13, "bold"), bg=PINK, fg="#fff",
            activebackground=PURPLE, relief=tk.RAISED, bd=2, state=tk.DISABLED,
        )
        self.judge_btn.pack(fill=tk.X, pady=4)

        self.status_var = tk.StringVar(value="")
        tk.Label(left, textvariable=self.status_var, font=("Sans", 10, "bold"),
                  fg=PURPLE, bg=BG, wraplength=240, justify=tk.LEFT).pack(pady=(6, 0))

        # --- 右: 結果表示 ---
        right = tk.Frame(body, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.score_var = tk.StringVar(value="--")
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
                "写真プレビュー・送信前リサイズにはPillowが必要: pip install Pillow\n"
                "(未インストールでも判定リクエスト自体はファイルをそのまま送るので動作する)",
            )

        self._poll_worker()

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

    # -- 写真選択 -- #
    def on_pick_image(self) -> None:
        path = filedialog.askopenfilename(
            title="全身写真を選ぶ",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp"), ("All files", "*.*")],
        )
        if not path:
            return
        self.selected_path = Path(path)
        self._show_preview(self.selected_path)
        self._clear_result()  # 前回の判定結果が新しい写真に残らないようにする
        self.judge_btn.configure(state=tk.NORMAL)
        self.status_var.set("")

    def _show_preview(self, path: Path) -> None:
        if Image is None:
            self.preview_label.configure(text=f"選択済み:\n{path.name}", image="")
            return
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((PREVIEW_WIDTH, PREVIEW_HEIGHT))
            self.preview_photo = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=self.preview_photo, text="")
        except Exception as exc:  # noqa: BLE001 - show any decode failure to the user
            self.preview_label.configure(text=f"プレビュー失敗:\n{exc}", image="")

    # -- 判定 -- #
    def on_judge(self) -> None:
        if self.busy or self.selected_path is None:
            return
        self.busy = True
        self.judge_btn.configure(state=tk.DISABLED)
        self.status_var.set("審査中... 毒舌ギャルが吟味してるので待ってて💭")
        self._log(f"[{time.strftime('%H:%M:%S')}] {self.selected_path.name} を送信...")
        self.worker.submit(self.selected_path)

    def _poll_worker(self) -> None:
        result = self.worker.poll()
        if result is not None:
            ok, payload = result
            self.busy = False
            self.judge_btn.configure(state=tk.NORMAL if self.selected_path else tk.DISABLED)
            if ok:
                self.status_var.set("")
                self._render_result(payload)
                self._log(f"[{time.strftime('%H:%M:%S')}] 判定完了")
            else:
                self.status_var.set("ジャッジに失敗した(下のログ参照)")
                self._log(f"[{time.strftime('%H:%M:%S')}] エラー: {payload}")
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


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv if argv is not None else sys.argv[1:])
    print(f"サーバー: {cfg.server_url} (先にDevKit側で fashion_judge_server.py "
          f"を起動しておくこと。--server-urlで変更可)", file=sys.stderr)
    root = tk.Tk()
    FashionJudgeGuiApp(root, cfg)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
