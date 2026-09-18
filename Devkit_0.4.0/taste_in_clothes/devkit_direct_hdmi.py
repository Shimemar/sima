#!/usr/bin/env python3
"""
DevKit直結HDMI出力 - USBカメラ(v4l2)の映像をModalix DevKit自身のHDMI出力にそのまま表示する。
マウスクリックで撮影し、コーデ辛口ジャッジの結果もすべてHDMI上のオーバーレイとして表示する
(Tkinter等の別ウィンドウは使わない -- webcam_hostgui.pyのHOSTPC GUI版と同じ機能を、
DevKit本体のHDMI出力+マウスだけで完結させたもの)。

pyneatのGraph/Modelは使わず、gst-python (`gi.repository.Gst`) で素のGStreamer
パイプラインを直接組み立てて実行する。pyneatにはカメラ入力ノードもローカル
ディスプレイ出力ノードも無い(core/include/nodes/groups/VideoSender.h はUDP RTP
配信専用、pyneat.Graphにはkmssink/ximagesink等のローカル表示シンクに相当する
ノードが存在しない)ため、このユースケースはpyneatの外で素のGStreamerを直接
使うのが正攻法。

── 実機(sima@10.42.0.76)で判明した制約と対策 ──────────────────────
1. DevKitのHDMI出力は起動時からXorg(lightdm)が握っている。素のGStreamerには
   ximagesink/xvimagesinkが入っておらず(利用可能なのはkmssink/waylandsink/
   fbdevsink/gtkwaylandsinkのみで、Waylandコンポジタは動いていない)、kmssinkで
   直接HDMIに描画するにはXorgにDRMマスターを明け渡してもらう必要がある。
   そのため本スクリプトは実行中だけ`sudo systemctl stop lightdm`でXorgを止め、
   終了時(正常終了/Ctrl-C/例外いずれでも)に必ず`systemctl start lightdm`で
   復帰させる。sima ユーザーはパスワード無しsudoが設定済みであることを実機で
   確認済み。
2. 搭載GPUはSiliconMotion smifb (PCI 126F:0768)で、kmssinkのドライバ自動判定に
   失敗し`Could not open DRM module`で落ちる。`driver-name=smifb`の明示が必須。
3. smifbのオーバーレイプレーンは`can-scale`プロパティ上はスケーリング対応を
   名乗るが実際には対応しておらず、カメラ解像度と出力解像度が食い違うと
   `drmModeSetPlane failed: Invalid argument`で落ちる。videoscaleを挟んで
   ディスプレイの実解像度に事前に(CPU側で)スケーリングしておく必要がある。
4. smifbはasync page flip / 従来のvblank ioctl (drmWaitVBlank)に対応しておらず、
   デフォルト設定のkmssinkはvsync待ちの失敗を致命的なフローエラーとして
   パイプライン全体を落とす(`skip-vsync=true`を付けないと1フレーム目で
   Internal data stream errorになる)。`skip-vsync=true`が必須。
5. 画面右端・上下中央に出すブランド文字「コーデ辛口ジャッジ」は日本語なので
   GStreamerの`textoverlay`(pangoベース)では出せない -- 実機にはtextoverlay
   プラグイン自体が入っておらず、しかも日本語フォントも一切入っていなかった
   (`fc-list`で確認、`fonts-noto-cjk`を別途`sudo apt-get install`して解決した)。
   そのためテキストはPillowで一度PNG(透過背景)にラスタライズし、
   `gdkpixbufoverlay`でカメラ映像の上に重ねる方式にした。撮影ボタン・
   マウスカーソル・カウントダウン・判定結果カードも全部同じ手法(Pillowで
   PNGを描いて`gdkpixbufoverlay`で重ねる)で実現している -- Tkinter等の別
   ウィンドウは一切使わない。
6. ブランド文字の向きは映像の回転(`--rotate`)とは切り離し、常に固定で
   右に90度回転させて表示する。そのため`gdkpixbufoverlay`は`videoflip`より
   **後**、最終的なディスプレイ解像度の座標系に置き、PNG自体をPillowの
   `Image.Transpose.ROTATE_270`(=時計回りに90度)で事前回転してから合成
   している。
7. マウスは物理マウス(evdev)から直接読む。Xorgを止めてkmssinkが直接HDMIに
   出しているため、X11/Waylandのようなクリック座標を教えてくれる仕組みが
   無い。`python3-evdev`(apt)を導入し、`sima`ユーザーを`input`グループに
   追加して`/dev/input/event*`をsudo無しで読めるようにした上で、相対移動
   イベント(REL_X/REL_Y)を自前で積算して仮想カーソル座標を作り、
   BTN_LEFTの押下イベントで「撮影ボタン」の矩形と当たり判定している。
8. 撮影した写真をコーデ判定サーバー(`fashion_judge_server.py`)に渡すのは
   HTTPのまま(`hostpc_gui.py`と同じプロトコル)だが、このスクリプトは
   DevKit本体上で動く(サーバーも同じDevKit上)ので既定の宛先は
   `http://127.0.0.1:8765`にしてある。

撮影用の静止画は、表示用のvideoscale(ディスプレイ解像度への引き伸ばし)より
**手前**、`videoflip`直後(回転はされているが、まだ引き伸ばされていない
カメラの自然なアスペクト比)の映像を`tee`で分岐してappsinkから取得する。
表示用に引き伸ばした後の映像を使うと、判定サーバーに送る写真が歪んで
しまうため。

Run (DevKit上、`dk`経由):
    cd /workspace/taste_in_clothes
    dk ./devkit_direct_hdmi.py
    dk ./devkit_direct_hdmi.py --camera-device /dev/video0 --width 1280 --height 720 --fps 30
    dk ./devkit_direct_hdmi.py --no-overlay          # ブランド文字オーバーレイなし
    dk ./devkit_direct_hdmi.py --overlay-text "べつのテキスト"
    dk ./devkit_direct_hdmi.py --rotate none         # 回転なし(既定は右に90度回転)
    dk ./devkit_direct_hdmi.py --no-judge            # 撮影/判定機能を無効化(単純パススルーのみ)
    dk ./devkit_direct_hdmi.py --server-url http://127.0.0.1:8765
    Ctrl-Cで終了(lightdmは自動的に再開する)。マウス左クリックで画面左端の
    「撮影する!」ボタンを押すと3・2・1カウントダウン後に撮影し、判定結果を
    画面左端(ボタンの右隣)にカード表示する。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

try:
    import evdev
except ImportError:  # pragma: no cover - reported to the user at startup instead
    evdev = None

DEFAULT_CAMERA_DEVICE = "/dev/video0"
DEFAULT_CAPTURE_WIDTH = 1280
DEFAULT_CAPTURE_HEIGHT = 720
DEFAULT_FPS = 30
DRM_DRIVER_NAME = "smifb"  # 実機のGPU(SiliconMotion, PCI 126F:0768)。他機種では要変更。
ROTATE_METHOD_DEFAULT = "clockwise"  # videoflipのmethod名。「右に90度回転」= clockwise。
DEFAULT_SERVER_URL = "http://127.0.0.1:8765"  # このスクリプト自体がDevKit上で動くのでlocalhost
DEFAULT_JUDGE_TIMEOUT = 60

# ブランド文字オーバーレイ。`fonts-noto-cjk`(apt)で入る実在のフォントファイル。
OVERLAY_TEXT_DEFAULT = "コーデ辛口ジャッジ"
OVERLAY_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
OVERLAY_RIGHT_MARGIN = 30  # 画面右端からのpx
# 文字の向きは映像の--rotateとは無関係に常にこれで固定(右に90度=時計回り)。
OVERLAY_ROTATE_TRANSPOSE = Image.Transpose.ROTATE_270

_TMP_PREFIX = f"/tmp/devkit_direct_hdmi_{os.getpid()}"
_OVERLAY_PNG_PATH = Path(f"{_TMP_PREFIX}_brand.png")
_BUTTON_PNG_PATH = Path(f"{_TMP_PREFIX}_button.png")
_CURSOR_PNG_PATH = Path(f"{_TMP_PREFIX}_cursor.png")
_COUNTDOWN_PNG_PATH = Path(f"{_TMP_PREFIX}_countdown.png")
_CARD_PNG_PATH = Path(f"{_TMP_PREFIX}_card.png")
_ALL_TMP_PATHS = [_OVERLAY_PNG_PATH, _BUTTON_PNG_PATH, _CURSOR_PNG_PATH,
                   _COUNTDOWN_PNG_PATH, _CARD_PNG_PATH]

COUNTDOWN_SECONDS = 3
BUTTON_LABEL_IDLE = "撮影する!"
BUTTON_LABEL_COUNTDOWN = "..."
BUTTON_LABEL_JUDGING = "審査中..."
BUTTON_LEFT_MARGIN = 30
CURSOR_SIZE = 32
MOUSE_POLL_MS = 33  # カーソル位置更新・クリック検出の周期(約30Hz)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="USBカメラ映像をDevKit自身のHDMI出力へ直接表示する")
    parser.add_argument("--camera-device", default=DEFAULT_CAMERA_DEVICE,
                         help="USBカメラのV4L2デバイス(既定: /dev/video0)")
    parser.add_argument("--width", type=int, default=DEFAULT_CAPTURE_WIDTH,
                         help="カメラからMJPEGで取り込む幅(既定1280)")
    parser.add_argument("--height", type=int, default=DEFAULT_CAPTURE_HEIGHT,
                         help="カメラからMJPEGで取り込む高さ(既定720)")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="キャプチャfps(既定30)")
    parser.add_argument("--display-width", type=int, default=None,
                         help="HDMI出力解像度の幅(省略時はkmssinkが検出した実解像度を使用)")
    parser.add_argument("--display-height", type=int, default=None,
                         help="HDMI出力解像度の高さ(省略時は自動検出)")
    parser.add_argument("--driver-name", default=DRM_DRIVER_NAME,
                         help="kmssinkに渡すDRMドライバ名(既定smifb)")
    parser.add_argument(
        "--rotate", default=ROTATE_METHOD_DEFAULT,
        choices=["none", "clockwise", "rotate-180", "counterclockwise",
                 "horizontal-flip", "vertical-flip",
                 "upper-left-diagonal", "upper-right-diagonal"],
        help="映像の回転方法(videoflipのmethod名、既定clockwise=右に90度回転)",
    )
    parser.add_argument("--no-lightdm-stop", action="store_true",
                         help="lightdmの停止/再開を行わない(既に手動で止めている場合用)")
    parser.add_argument("--overlay-text", default=OVERLAY_TEXT_DEFAULT,
                         help=f"画面右端に重ねるブランド文字(既定: {OVERLAY_TEXT_DEFAULT})")
    parser.add_argument("--no-overlay", action="store_true",
                         help="ブランド文字オーバーレイを出さない")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL,
                         help=f"fashion_judge_server.pyのURL(既定: {DEFAULT_SERVER_URL}、"
                              "このDevKit自身の上で動いているサーバーを指す)")
    parser.add_argument("--judge-timeout", type=int, default=DEFAULT_JUDGE_TIMEOUT,
                         help=f"判定リクエストのタイムアウト秒数(既定{DEFAULT_JUDGE_TIMEOUT})")
    parser.add_argument("--no-judge", action="store_true",
                         help="撮影/判定機能を無効化し、単純なカメラパススルーのみにする"
                              "(マウス・判定サーバーが無い環境向け)")
    parser.add_argument("--duration", type=float, default=0.0,
                         help="指定秒数だけ表示して自動終了する(既定0=無期限、Ctrl-Cで終了)。"
                              "`dk`経由のようにCtrl-Cが届かない実行環境での動作確認用。")
    return parser.parse_args(argv)


def stop_lightdm() -> None:
    subprocess.run(["sudo", "systemctl", "stop", "lightdm"], check=True)


def start_lightdm() -> None:
    # 終了処理なので、途中で失敗しても例外で他の後始末を止めない。
    subprocess.run(["sudo", "systemctl", "start", "lightdm"], check=False)


def probe_display_size(driver_name: str) -> tuple[int, int]:
    """kmssinkを一瞬READY状態にしてHDMI出力の実解像度を調べる。

    display-width/display-height はgst_kms_sink_start() (NULL->READY遷移)で
    埋まるプロパティで、実際に映像を流さなくても取得できる(実機で確認済み)。
    """
    probe = Gst.ElementFactory.make("kmssink", "probe")
    if probe is None:
        raise RuntimeError("kmssink要素が見つからない(gst-plugins-badが必要)")
    probe.set_property("driver-name", driver_name)
    probe.set_state(Gst.State.READY)
    probe.get_state(Gst.CLOCK_TIME_NONE)
    width = probe.get_property("display-width")
    height = probe.get_property("display-height")
    probe.set_state(Gst.State.NULL)
    if not width or not height:
        raise RuntimeError("HDMI出力の解像度を検出できなかった(モニタ未接続の可能性)")
    return width, height


# ============================================================
# オーバーレイ画像の生成(すべてPillowでPNGにラスタライズし、gdkpixbufoverlayで
# カメラ映像の上に重ねる。Tkinter等の別ウィンドウは使わない)
# ============================================================

def render_overlay_png(text: str, display_height: int, path: Path) -> tuple[int, int]:
    """ブランド文字を白地+黒縁取りで透過PNGにラスタライズし、pxサイズを返す。

    白文字+黒縁取りにしてあるのは、カメラ映像側の色や明るさに関わらず
    視認できるようにするため(単色文字だと背景次第で読めなくなる)。
    文字は横書きで描いてから`OVERLAY_ROTATE_TRANSPOSE`で右に90度回転する --
    映像の`--rotate`設定とは無関係に、文字の向きは常にこれで固定。
    """
    font_size = max(28, display_height // 18)
    font = ImageFont.truetype(OVERLAY_FONT_PATH, font_size)

    stroke_width = max(2, font_size // 15)
    scratch = Image.new("RGBA", (4000, font_size * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    draw.text((stroke_width, stroke_width), text, font=font, fill=(255, 255, 255, 255),
               stroke_width=stroke_width, stroke_fill=(0, 0, 0, 255))
    bbox = scratch.getbbox()
    if bbox is None:
        raise RuntimeError(f"オーバーレイ文字のラスタライズに失敗した(空の描画結果): {text!r}")
    pad = stroke_width
    crop_box = (
        max(0, bbox[0] - pad), max(0, bbox[1] - pad),
        min(scratch.width, bbox[2] + pad), min(scratch.height, bbox[3] + pad),
    )
    img = scratch.crop(crop_box).transpose(OVERLAY_ROTATE_TRANSPOSE)
    img.save(path)
    return img.width, img.height


def render_button_png(path: Path, width: int, height: int, label: str,
                       enabled: bool) -> None:
    """「撮影する!」ボタンをPNGにラスタライズする(色以外はhostpc_gui.pyのボタンを踏襲)。

    横書きで描いてから`OVERLAY_ROTATE_TRANSPOSE`で右に90度回転する --
    ブランド文字「コーデ辛口ジャッジ」と向きを揃えるため。保存されるPNGの
    実サイズは(height, width)に入れ替わる(呼び出し側はそれを踏まえて
    クリック判定矩形の縦横を入れ替えて使うこと)。
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bg = (255, 46, 147, 235) if enabled else (110, 110, 110, 200)  # 有効=PINK、無効=グレー
    draw.rounded_rectangle([2, 2, width - 3, height - 3], radius=height // 4,
                            fill=bg, outline=(255, 255, 255, 255), width=3)
    font = ImageFont.truetype(OVERLAY_FONT_PATH, height // 3)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((width - tw) / 2 - bbox[0], (height - th) / 2 - bbox[1]), label,
               font=font, fill=(255, 255, 255, 255))
    img.transpose(OVERLAY_ROTATE_TRANSPOSE).save(path)


def _write_transparent_placeholder(path: Path) -> None:
    """gdkpixbufoverlayが起動時に`location`を開けるよう、1x1の透明PNGを置く。"""
    Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(path)


def render_cursor_png(path: Path, size: int) -> None:
    """黒縁+黄色のクロスヘアカーソルをPNGにラスタライズする(常に1回だけ生成)。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = size // 2
    draw.line([(c, 0), (c, size)], fill=(0, 0, 0, 255), width=5)
    draw.line([(0, c), (size, c)], fill=(0, 0, 0, 255), width=5)
    draw.line([(c, 0), (c, size)], fill=(255, 233, 61, 255), width=2)
    draw.line([(0, c), (size, c)], fill=(255, 233, 61, 255), width=2)
    draw.ellipse([c - 4, c - 4, c + 4, c + 4], outline=(255, 46, 147, 255), width=2)
    img.save(path)


def render_countdown_png(path: Path, number: int, display_height: int) -> tuple[int, int]:
    """カウントダウンの数字1文字を画面中央に出す大きな白抜き文字としてラスタライズする。

    ブランド文字・ボタンと同じく`OVERLAY_ROTATE_TRANSPOSE`で右に90度回転する。
    """
    font_size = display_height // 3
    font = ImageFont.truetype(OVERLAY_FONT_PATH, font_size)
    text = str(number)
    stroke_width = max(4, font_size // 12)
    scratch = Image.new("RGBA", (font_size * 2, font_size * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    draw.text((stroke_width, stroke_width), text, font=font, fill=(255, 255, 255, 255),
               stroke_width=stroke_width, stroke_fill=(0, 0, 0, 255))
    bbox = scratch.getbbox()
    img = scratch.crop(bbox).transpose(OVERLAY_ROTATE_TRANSPOSE)
    img.save(path)
    return img.width, img.height


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                 max_width: int) -> list[str]:
    """1文字ずつ足しながらmax_widthに収まる行に折り返す(CJKは単語区切りが無いため)。"""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if draw.textlength(trial, font=font) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def render_card_png(
    path: Path, card_width: int, *,
    message: str | None = None,
    judge: dict | None = None,
) -> tuple[int, int]:
    """判定結果(または「審査中...」等のメッセージ)を1枚のカードPNGにラスタライズする。

    2パス方式: まず10x10のダミー画像でテキスト計測だけ行って必要な高さを求め、
    その高さぴったりの本番画像を作って半透明パネル→テキストの順に描く。
    """
    pad = 28
    line_gap = 10
    inner_width = card_width - 2 * pad
    title_font = ImageFont.truetype(OVERLAY_FONT_PATH, 42)
    score_font = ImageFont.truetype(OVERLAY_FONT_PATH, 100)
    body_font = ImageFont.truetype(OVERLAY_FONT_PATH, 34)
    small_font = ImageFont.truetype(OVERLAY_FONT_PATH, 27)

    measure = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    blocks: list[tuple] = []
    y = pad

    if message is not None:
        for text in _wrap_lines(measure, message, body_font, inner_width):
            blocks.append(("line", text, body_font, (255, 255, 255, 255)))
            y += body_font.size + line_gap
    else:
        judge = judge or {}
        score = judge.get("score")
        blocks.append(("score", f"{score if score is not None else '--'} / 100"))
        y += score_font.size + line_gap
        for text in _wrap_lines(measure, judge.get("verdict", ""), title_font, inner_width):
            blocks.append(("line", text, title_font, (255, 62, 147, 255)))
            y += title_font.size + line_gap
        for roast in judge.get("roasts", []) or []:
            for text in _wrap_lines(measure, f"・{roast}", body_font, inner_width):
                blocks.append(("line", text, body_font, (255, 255, 255, 255)))
                y += body_font.size + line_gap
        compliment = judge.get("compliment", "")
        if compliment:
            for text in _wrap_lines(measure, compliment, body_font, inner_width):
                blocks.append(("line", text, body_font, (255, 233, 90, 255)))
                y += body_font.size + line_gap
        closing = judge.get("closing", "")
        if closing:
            for text in _wrap_lines(measure, closing, small_font, inner_width):
                blocks.append(("line", text, small_font, (210, 200, 225, 255)))
                y += small_font.size + line_gap

    card_height = y + pad
    img = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, card_width - 1, card_height - 1], radius=24,
                            fill=(20, 8, 24, 225), outline=(255, 255, 255, 200), width=2)

    cy = pad
    for block in blocks:
        if block[0] == "score":
            draw.text((pad, cy), block[1], font=score_font, fill=(255, 233, 61, 255))
            cy += score_font.size + line_gap
        else:
            _kind, text, font, color = block
            draw.text((pad, cy), text, font=font, fill=color)
            cy += font.size + line_gap

    # ブランド文字・ボタン・カウントダウンと同じ向きに揃えるため90度回転する。
    img = img.transpose(OVERLAY_ROTATE_TRANSPOSE)
    img.save(path)
    return img.width, img.height


def render_message_card_png(path: Path, max_width: int, message: str) -> tuple[int, int]:
    """「審査中...」等の短いステータス文言用の、文字列の実際の幅に合わせた
    コンパクトなカードをラスタライズする。

    判定結果カード(`render_card_png`)は常に横幅固定(`card_width`)で
    多くの項目を並べるための設計だが、こちらは短い1〜2行のメッセージを
    画面中央に出すためのもので、無駄な余白が出ないようテキストの実際の
    幅に合わせて横幅を決める(`max_width`は長いメッセージ用の上限)。
    """
    pad = 24
    line_gap = 8
    font = ImageFont.truetype(OVERLAY_FONT_PATH, 34)
    measure = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    inner_max_width = max(1, max_width - 2 * pad)
    lines = _wrap_lines(measure, message, font, inner_max_width) or [""]
    content_width = max(int(measure.textlength(line, font=font)) for line in lines)

    card_width = content_width + 2 * pad
    card_height = len(lines) * font.size + (len(lines) - 1) * line_gap + 2 * pad

    img = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, card_width - 1, card_height - 1], radius=24,
                            fill=(20, 8, 24, 225), outline=(255, 255, 255, 200), width=2)
    cy = pad
    for line in lines:
        draw.text((pad, cy), line, font=font, fill=(255, 255, 255, 255))
        cy += font.size + line_gap

    img = img.transpose(OVERLAY_ROTATE_TRANSPOSE)
    img.save(path)
    return img.width, img.height


# ============================================================
# マウス入力(evdev。X11/Waylandが無いので相対移動を自前で積算する)
# ============================================================

class MouseTracker:
    def __init__(self, display_width: int, display_height: int) -> None:
        self.display_width = display_width
        self.display_height = display_height
        self._lock = threading.Lock()
        self.x = display_width // 2
        self.y = display_height // 2
        self.click_queue: "queue.Queue[tuple[int, int]]" = queue.Queue()

    def _find_devices(self) -> list["evdev.InputDevice"]:
        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            caps = dev.capabilities()
            keys = caps.get(evdev.ecodes.EV_KEY, [])
            if evdev.ecodes.EV_REL in caps and evdev.ecodes.BTN_LEFT in keys:
                devices.append(dev)
            else:
                dev.close()
        return devices

    def start(self) -> list[str]:
        devices = self._find_devices()
        if not devices:
            raise RuntimeError(
                "マウスデバイスが見つからない(/dev/input/event*にBTN_LEFT+相対移動を"
                "持つデバイスが無い)。マウスが接続されているか、`sima`ユーザーが"
                "`input`グループに入っているか確認してください。"
            )
        for dev in devices:
            threading.Thread(target=self._run, args=(dev,), daemon=True).start()
        return [dev.name for dev in devices]

    def _run(self, dev: "evdev.InputDevice") -> None:
        try:
            for event in dev.read_loop():
                if event.type == evdev.ecodes.EV_REL:
                    with self._lock:
                        if event.code == evdev.ecodes.REL_X:
                            self.x = max(0, min(self.display_width - 1, self.x + event.value))
                        elif event.code == evdev.ecodes.REL_Y:
                            self.y = max(0, min(self.display_height - 1, self.y + event.value))
                elif (event.type == evdev.ecodes.EV_KEY
                      and event.code == evdev.ecodes.BTN_LEFT and event.value == 1):
                    with self._lock:
                        pos = (self.x, self.y)
                    self.click_queue.put(pos)
        except OSError:
            pass  # デバイス切断時など。他のデバイスのスレッドは動き続ける。

    def get_position(self) -> tuple[int, int]:
        with self._lock:
            return self.x, self.y

    def poll_click(self) -> tuple[int, int] | None:
        try:
            return self.click_queue.get_nowait()
        except queue.Empty:
            return None


# ============================================================
# 判定サーバー呼び出し(バックグラウンドスレッド。hostpc_gui.pyと同じHTTPプロトコル)
# ============================================================

class JudgeError(Exception):
    pass


def judge_via_http(server_url: str, timeout_s: int, image_bytes: bytes) -> dict:
    req = urllib.request.Request(
        f"{server_url}/judge", data=image_bytes, method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
    except urllib.error.URLError as exc:
        raise JudgeError(
            f"{server_url} に接続できない: {exc.reason}\n"
            "先に `dk ./fashion_judge_server.py --port ...` 等でサーバーを"
            "起動済みか確認してください。"
        ) from exc
    except TimeoutError as exc:
        raise JudgeError(f"{timeout_s}秒待っても応答がなかった。") from exc

    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JudgeError(f"サーバーからの応答がJSONとして読めなかった: {body[:300]!r}") from exc

    if not data.get("ok"):
        raise JudgeError(data.get("error", "不明なエラー"))
    return data


class JudgeWorker:
    def __init__(self, server_url: str, timeout_s: int) -> None:
        self.server_url = server_url
        self.timeout_s = timeout_s
        self._queue: "queue.Queue[tuple[bool, object]]" = queue.Queue()

    def submit(self, image_bytes: bytes) -> None:
        thread = threading.Thread(target=self._run, args=(image_bytes,), daemon=True)
        thread.start()

    def _run(self, image_bytes: bytes) -> None:
        try:
            data = judge_via_http(self.server_url, self.timeout_s, image_bytes)
            self._queue.put((True, data))
        except JudgeError as exc:
            self._queue.put((False, str(exc)))

    def poll(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


# ============================================================
# パイプライン構築
# ============================================================

def build_pipeline_description(
    args: argparse.Namespace,
    display_width: int,
    display_height: int,
    overlay_size: tuple[int, int] | None,
    enable_judge: bool,
) -> str:
    brand_stage = ""
    if overlay_size is not None:
        _brand_w, brand_h = overlay_size
        offset_y = max(0, (display_height - brand_h) // 2)
        brand_stage = (
            f"gdkpixbufoverlay location={_OVERLAY_PNG_PATH} "
            f"offset-x=-{OVERLAY_RIGHT_MARGIN} offset-y={offset_y} ! "
        )

    source = (
        f"v4l2src device={args.camera_device} io-mode=mmap ! "
        f"image/jpeg,width={args.width},height={args.height},framerate={args.fps}/1 ! "
        "queue leaky=downstream max-size-buffers=2 ! "
        "jpegparse ! jpegdec ! "
        f"videoflip method={args.rotate} ! "
    )

    if not enable_judge:
        return source + (
            "videoconvert ! videoscale ! "
            f"video/x-raw,format=BGRx,width={display_width},height={display_height} ! "
            f"{brand_stage}"
            f"kmssink name=sink driver-name={args.driver_name} sync=false skip-vsync=true"
        )

    # 撮影ボタン・カーソル・カウントダウン・結果カードは常時パイプラインに存在
    # させ、`alpha`/`offset-x`/`offset-y`/`location`プロパティの変更
    # (いずれもcontrollable)だけで見た目を切り替える(パイプラインの再構築は
    # しない)。
    judge_overlay_stages = (
        f"gdkpixbufoverlay name=button_overlay location={_BUTTON_PNG_PATH} ! "
        f"gdkpixbufoverlay name=cursor_overlay location={_CURSOR_PNG_PATH} alpha=1 ! "
        f"gdkpixbufoverlay name=countdown_overlay location={_COUNTDOWN_PNG_PATH} alpha=0 ! "
        f"gdkpixbufoverlay name=card_overlay location={_CARD_PNG_PATH} alpha=0 ! "
    )
    # tee name=t ! の直後がteeの1本目の枝(表示用)、2本目は`t.`で参照する
    # (gst-launchの作法)。撮影用の静止画はvideoflip直後(=表示用videoscaleで
    # 引き伸ばされる前、かつ回転は済んでいる)の映像から取る -- 表示用に
    # 引き伸ばした後の映像を使うと判定サーバーに送る写真が歪んでしまうため。
    display_branch = (
        "queue leaky=downstream max-size-buffers=2 ! "
        "videoconvert ! videoscale ! "
        f"video/x-raw,format=BGRx,width={display_width},height={display_height} ! "
        f"{brand_stage}"
        f"{judge_overlay_stages}"
        f"kmssink name=sink driver-name={args.driver_name} sync=false skip-vsync=true"
    )
    capture_branch = (
        "t. ! queue leaky=downstream max-size-buffers=1 ! "
        "videoconvert ! video/x-raw,format=RGB ! "
        "appsink name=capture emit-signals=true sync=false max-buffers=1 drop=true"
    )
    return f"{source}tee name=t ! {display_branch} {capture_branch}"


# ============================================================
# 撮影/判定オーバーレイの制御(ボタン・カーソル・カウントダウン・結果カード)
# ============================================================

class JudgeOverlayController:
    def __init__(self, pipeline: Gst.Pipeline, args: argparse.Namespace,
                 display_width: int, display_height: int) -> None:
        self.display_width = display_width
        self.display_height = display_height

        self.button_el = pipeline.get_by_name("button_overlay")
        self.cursor_el = pipeline.get_by_name("cursor_overlay")
        self.countdown_el = pipeline.get_by_name("countdown_overlay")
        self.card_el = pipeline.get_by_name("card_overlay")
        self.appsink = pipeline.get_by_name("capture")

        # ボタンは横書きのテキストとして描いてから90度回転する
        # (render_button_png参照)。content_*は回転前の描画キャンバスの
        # 縦横、button_rectは実際に画面に出る回転後の footprint
        # (=content_height x content_width、縦横入れ替え)で、クリック
        # 判定・結果カードの配置基準の両方に使う。画面左端・上下中央に置く
        # (ブランド文字の右端・上下中央と左右対称の配置)。
        self.button_content_width = max(240, display_width // 6)
        self.button_content_height = max(80, display_height // 12)
        button_screen_w = self.button_content_height
        button_screen_h = self.button_content_width
        self.button_rect = (
            BUTTON_LEFT_MARGIN,
            (display_height - button_screen_h) // 2,
            button_screen_w, button_screen_h,
        )
        self.card_width = int(display_width * 0.55)

        self.worker = JudgeWorker(args.server_url, args.judge_timeout)
        self.mouse = MouseTracker(display_width, display_height)
        self.busy = False
        self.countdown_remaining = 0

        self._latest_sample = None
        self._sample_lock = threading.Lock()
        self.appsink.connect("new-sample", self._on_new_sample)

        render_cursor_png(_CURSOR_PNG_PATH, CURSOR_SIZE)
        self.cursor_el.set_property("location", str(_CURSOR_PNG_PATH))
        self._set_button_state(enabled=True, label=BUTTON_LABEL_IDLE)
        # countdown/cardはgdkpixbufoverlayがパイプライン起動時(NULL->READY)に
        # `location`のファイルを即座に開こうとするため、まだ何も表示しない間も
        # 実在する画像が要る(無いと `Could not load overlay image` で
        # パイプライン全体が起動失敗する -- 実機で確認済み)。alpha=0で
        # 見えなくしているので中身は1x1の透明PNGでよい。
        _write_transparent_placeholder(_COUNTDOWN_PNG_PATH)
        _write_transparent_placeholder(_CARD_PNG_PATH)
        self.countdown_el.set_property("alpha", 0.0)
        self.card_el.set_property("alpha", 0.0)

    def start_mouse(self) -> list[str]:
        names = self.mouse.start()
        self._update_cursor_position(*self.mouse.get_position())
        return names

    # -- appsink: 常に最新フレームを1枚保持しておく -- #
    def _on_new_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is not None:
            with self._sample_lock:
                self._latest_sample = sample
        return Gst.FlowReturn.OK

    # -- 毎ティック呼ばれる: カーソル位置更新・クリック検出 -- #
    def poll_tick(self) -> bool:
        x, y = self.mouse.get_position()
        self._update_cursor_position(x, y)
        click = self.mouse.poll_click()
        if click is not None:
            self._handle_click(click)
        return True

    def _update_cursor_position(self, x: int, y: int) -> None:
        ox = max(0, min(self.display_width - CURSOR_SIZE, x - CURSOR_SIZE // 2))
        oy = max(0, min(self.display_height - CURSOR_SIZE, y - CURSOR_SIZE // 2))
        self.cursor_el.set_property("offset-x", ox)
        self.cursor_el.set_property("offset-y", oy)

    def _handle_click(self, pos: tuple[int, int]) -> None:
        if self.busy:
            return
        x, y = pos
        bx, by, bw, bh = self.button_rect
        if bx <= x <= bx + bw and by <= y <= by + bh:
            self._start_shoot()

    # -- 撮影シーケンス -- #
    def _start_shoot(self) -> None:
        self.busy = True
        self._hide_card()
        self._set_button_state(enabled=False, label=BUTTON_LABEL_COUNTDOWN)
        self.countdown_remaining = COUNTDOWN_SECONDS
        self._show_countdown(self.countdown_remaining)
        GLib.timeout_add(1000, self._tick_countdown)

    def _tick_countdown(self) -> bool:
        self.countdown_remaining -= 1
        if self.countdown_remaining > 0:
            self._show_countdown(self.countdown_remaining)
            return True
        self._hide_countdown()
        self._capture_and_send()
        return False

    def _show_countdown(self, number: int) -> None:
        w, h = render_countdown_png(_COUNTDOWN_PNG_PATH, number, self.display_height)
        self.countdown_el.set_property("location", str(_COUNTDOWN_PNG_PATH))
        self.countdown_el.set_property("offset-x", max(0, (self.display_width - w) // 2))
        self.countdown_el.set_property("offset-y", max(0, (self.display_height - h) // 2))
        self.countdown_el.set_property("alpha", 1.0)

    def _hide_countdown(self) -> None:
        self.countdown_el.set_property("alpha", 0.0)

    def _capture_and_send(self) -> None:
        with self._sample_lock:
            sample = self._latest_sample
        if sample is None:
            self._show_message("カメラ映像を取得できなかった(まだ1フレームも来ていない)")
            self._finish_busy()
            return

        buf = sample.get_buffer()
        struct = sample.get_caps().get_structure(0)
        width = struct.get_value("width")
        height = struct.get_value("height")
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            self._show_message("撮影フレームの読み出しに失敗した")
            self._finish_busy()
            return
        try:
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)[: width * height * 3]
            arr = arr.reshape((height, width, 3))
            img = Image.fromarray(arr, mode="RGB")
        finally:
            buf.unmap(mapinfo)

        jpeg_buf = io.BytesIO()
        img.save(jpeg_buf, format="JPEG", quality=90)

        self._set_button_state(enabled=False, label=BUTTON_LABEL_JUDGING)
        self._show_message("審査中... 毒舌ギャルが吟味してるので待ってて♥")
        self.worker.submit(jpeg_buf.getvalue())

    # -- 判定ワーカーのポーリング -- #
    def poll_worker(self) -> bool:
        result = self.worker.poll()
        if result is not None:
            ok, payload = result
            if ok:
                self._show_result(payload.get("judge", {}))
            else:
                self._show_message(f"エラー: {payload}")
            self._finish_busy()
        return True

    def _finish_busy(self) -> None:
        self.busy = False
        self._set_button_state(enabled=True, label=BUTTON_LABEL_IDLE)

    # -- ボタン/カード表示 -- #
    def _set_button_state(self, *, enabled: bool, label: str) -> None:
        render_button_png(_BUTTON_PNG_PATH, self.button_content_width,
                           self.button_content_height, label, enabled)
        self.button_el.set_property("location", str(_BUTTON_PNG_PATH))
        bx, by, _bw, _bh = self.button_rect
        self.button_el.set_property("offset-x", bx)
        self.button_el.set_property("offset-y", by)
        self.button_el.set_property("alpha", 1.0)

    def _show_message(self, message: str) -> None:
        # 「審査中...」等の短いステータス文言は、文字列の幅に合わせた
        # コンパクトなウィンドウで画面中央に表示する(判定結果カードとは
        # 見た目・配置とも別物)。
        max_width = int(self.display_width * 0.7)
        w, h = render_message_card_png(_CARD_PNG_PATH, max_width, message)
        self._place_card_centered(w, h)

    def _show_result(self, judge: dict) -> None:
        w, h = render_card_png(_CARD_PNG_PATH, self.card_width, judge=judge)
        self._place_card_left_of_button(w, h)

    def _place_card_centered(self, w: int, h: int) -> None:
        ox = max(0, (self.display_width - w) // 2)
        oy = max(0, (self.display_height - h) // 2)
        self._set_card(ox, oy)

    def _place_card_left_of_button(self, w: int, h: int) -> None:
        # 画面左端から、ボタン1つ分(左マージン込みのfootprint幅)右に表示する
        # -- ボタンと重ならないように。上下方向は画面中央のまま。
        _bx, _by, button_footprint_w, _bh = self.button_rect
        ox = BUTTON_LEFT_MARGIN + button_footprint_w
        oy = max(0, (self.display_height - h) // 2)
        self._set_card(ox, oy)

    def _set_card(self, offset_x: int, offset_y: int) -> None:
        self.card_el.set_property("location", str(_CARD_PNG_PATH))
        self.card_el.set_property("offset-x", offset_x)
        self.card_el.set_property("offset-y", offset_y)
        self.card_el.set_property("alpha", 1.0)

    def _hide_card(self) -> None:
        self.card_el.set_property("alpha", 0.0)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    Gst.init(None)

    enable_judge = not args.no_judge
    if enable_judge and evdev is None:
        print("[warn] python3-evdev が見つからないため、撮影/判定機能を無効化します"
              "(`sudo apt-get install python3-evdev` で導入可能)。"
              "単純なカメラパススルーのみで続行します。", file=sys.stderr)
        enable_judge = False

    lightdm_stopped = False
    pipeline = None
    loop = GLib.MainLoop()
    exit_code = 0

    def request_stop(*_a: object) -> bool:
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, request_stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, request_stop)

    try:
        if not args.no_lightdm_stop:
            print("[info] lightdm(Xorg)を一時停止してHDMIのDRMマスターを取得します...",
                  file=sys.stderr)
            stop_lightdm()
            lightdm_stopped = True

        if args.display_width and args.display_height:
            display_width, display_height = args.display_width, args.display_height
        else:
            display_width, display_height = probe_display_size(args.driver_name)
        print(f"[info] HDMI出力解像度: {display_width}x{display_height}", file=sys.stderr)

        overlay_size = None
        if not args.no_overlay:
            overlay_size = render_overlay_png(args.overlay_text, display_height,
                                               _OVERLAY_PNG_PATH)
            print(f"[info] オーバーレイ文字: {args.overlay_text!r} ({overlay_size[0]}x"
                  f"{overlay_size[1]}px)", file=sys.stderr)

        description = build_pipeline_description(args, display_width, display_height,
                                                   overlay_size, enable_judge)
        print(f"[info] pipeline: {description}", file=sys.stderr)
        pipeline = Gst.parse_launch(description)

        controller = None
        if enable_judge:
            controller = JudgeOverlayController(pipeline, args, display_width, display_height)
            try:
                names = controller.start_mouse()
                print(f"[info] マウス検出: {names}", file=sys.stderr)
            except RuntimeError as exc:
                print(f"[warn] {exc} 撮影ボタンは反応しません。", file=sys.stderr)
            GLib.timeout_add(MOUSE_POLL_MS, controller.poll_tick)
            GLib.timeout_add(150, controller.poll_worker)

        bus = pipeline.get_bus()
        bus.add_signal_watch()

        def on_bus_message(_bus: Gst.Bus, message: Gst.Message) -> bool:
            nonlocal exit_code
            if message.type == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                print(f"[error] {err} ({debug})", file=sys.stderr)
                exit_code = 1
                loop.quit()
            elif message.type == Gst.MessageType.EOS:
                print("[info] EOS", file=sys.stderr)
                loop.quit()
            return True

        bus.connect("message", on_bus_message)

        if args.duration > 0:
            GLib.timeout_add(int(args.duration * 1000), request_stop)

        pipeline.set_state(Gst.State.PLAYING)
        print("[info] 表示中。Ctrl-Cで終了。"
              + ("マウス左クリックで撮影ボタンを押せます。" if enable_judge else ""),
              file=sys.stderr)
        loop.run()
        return exit_code
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        for tmp_path in _ALL_TMP_PATHS:
            tmp_path.unlink(missing_ok=True)
        if lightdm_stopped:
            print("[info] lightdmを再開します...", file=sys.stderr)
            start_lightdm()


if __name__ == "__main__":
    raise SystemExit(main())
