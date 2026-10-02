#!/usr/bin/python3
"""Receive the PCIe detector's RTP/H.264 video and JSON detection datagrams."""
import argparse
from collections import OrderedDict, deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import socket
import sys
import threading
import time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
gi.require_version('GstRtp', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gtk, Gst, GstRtp, GstVideo, Gio, GLib, Gdk, GdkPixbuf, Pango
import yaml


def channels(value):
    result = []
    try:
        for part in value.split(','):
            bounds = part.strip().split('-')
            if len(bounds) == 1:
                result.append(int(bounds[0]))
            elif len(bounds) == 2 and 0 <= int(bounds[0]) <= int(bounds[1]) < 80:
                result.extend(range(int(bounds[0]), int(bounds[1]) + 1))
            else:
                raise ValueError()
        if not result or len(result) > 80 or any(n < 0 or n >= 80 for n in result):
            raise ValueError()
    except ValueError:
        raise argparse.ArgumentTypeError('チャネルは0〜79、例: 0-3 または 0,4,8')
    return list(dict.fromkeys(result))


def parse_metadata(raw, channel):
    """Validate the envelope emitted by apps/support/object_detection/detection_egress.h."""
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('type') != 'object-detection':
        raise ValueError('Unsupported metadata type')
    if value.get('stream_index') != channel:
        raise ValueError('Wrong stream')
    stamp = value.get('rtp_timestamp')
    if type(stamp) is not int or not 0 <= stamp <= 0xffffffff:
        raise ValueError('Missing RTP timestamp')
    objects = value['data']['objects']
    if not isinstance(objects, list) or len(objects) > 1000:
        raise ValueError('Invalid objects')
    validated = []
    for obj in objects:
        bbox = obj['bbox']
        score = obj['confidence']
        if (not isinstance(bbox, list) or len(bbox) != 4
                or not all(type(x) in (int, float) and math.isfinite(x) for x in bbox)
                or bbox[2] < 0 or bbox[3] < 0
                or type(score) not in (int, float) or not math.isfinite(score)
                or not 0 <= score <= 1 or not isinstance(obj['label'], str)):
            raise ValueError('Invalid detection')
        label = ''.join(c for c in obj['label'][:120] if ord(c) >= 32)
        validated.append((bbox, score, label))
    return stamp, validated


@dataclass
class Frame:
    received: float
    stamp: object
    pixels: bytes
    width: int
    height: int
    stride: int


class SyncBuffer:
    """Bounded caches; display only detections with an exact RTP timestamp match."""
    def __init__(self, delay_ms):
        self.delay = delay_ms / 1000
        self.frames = deque(maxlen=max(8, min(180, int(self.delay * 60) + 8)))
        self.metadata = OrderedDict()
        self.lock = threading.Lock()

    def add_frame(self, frame):
        with self.lock:
            self.frames.append(frame)

    def add_metadata(self, stamp, objects, now=None):
        with self.lock:
            self.metadata[stamp] = (time.monotonic() if now is None else now, objects)
            self.metadata.move_to_end(stamp)
            while len(self.metadata) > 256:
                self.metadata.popitem(last=False)

    def select(self, now):
        with self.lock:
            for stamp, (received, _) in list(self.metadata.items()):
                if now - received > 5:
                    del self.metadata[stamp]
            selected = None
            while self.frames and self.frames[0].received <= now - self.delay:
                selected = self.frames.popleft()
            return selected

    def matching(self, stamp):
        with self.lock:
            record = self.metadata.get(stamp)
            return None if record is None else record[1]


def bind_udp(address, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        # Exclusive bind: never steal packets from Insight or another viewer.
        sock.bind((address, port))
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        raise


class VideoArea(Gtk.DrawingArea):
    """Thumbnails request a height derived from their allocated width."""
    def __init__(self, width, height):
        super().__init__()
        self.fit_width = False
        self.ratio = height / width

    def do_get_request_mode(self):
        if self.fit_width:
            return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH
        return Gtk.DrawingArea.do_get_request_mode(self)

    def do_get_preferred_width(self):
        if self.fit_width:
            return 180, 200
        return Gtk.DrawingArea.do_get_preferred_width(self)

    def do_get_preferred_height(self):
        if self.fit_width:
            return self.do_get_preferred_height_for_width(180)
        return Gtk.DrawingArea.do_get_preferred_height(self)

    def do_get_preferred_height_for_width(self, width):
        if self.fit_width:
            height = max(1, round(width * self.ratio))
            return height, height
        return Gtk.DrawingArea.do_get_preferred_height_for_width(self, width)

    def set_video_size(self, width, height):
        ratio = height / width
        if self.ratio != ratio:
            self.ratio = ratio
            if self.fit_width:
                self.queue_resize()


class Channel(Gtk.Box):
    def __init__(self, index, args):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.index, self.args = index, args
        self.buffer = SyncBuffer(args.delay)
        self.frame = self.pixbuf = None
        self.pts_to_rtp = OrderedDict()
        self.pts_lock = threading.Lock()
        self.frame_count = self.meta_count = self.bad_count = self.match_count = 0
        self.last_video = self.last_metadata = 0
        self.error = ''
        self.closed = False
        self.pipeline = self.video_socket = self.metadata_socket = None
        self.watch = self.bus = None
        self.label = Gtk.Label(label=f'CH {index} — 受信待ち')
        self.label.set_xalign(0)
        self.pack_start(self.label, False, False, 0)
        self.area = VideoArea(args.width, args.height)
        self.area.set_size_request(240, 135)
        self.area.connect('draw', self.draw)
        self.pack_start(self.area, True, True, 0)
        try:
            self.metadata_socket = bind_udp(args.bind, args.metadata_port_base + index)
            video = bind_udp(args.bind, args.video_port_base + index)
            self.video_socket = Gio.Socket.new_from_fd(video.detach())
            self.pipeline = Gst.parse_launch(
                'udpsrc name=input close-socket=false '
                'caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" '
                f'! rtpjitterbuffer latency={args.jitter} drop-on-latency=true '
                '! rtph264depay name=depay wait-for-keyframe=true '
                '! h264parse ! avdec_h264 max-threads=2 '
                '! videoconvert ! video/x-raw,format=RGB '
                '! appsink name=frames emit-signals=true sync=false max-buffers=2 drop=true')
            self.pipeline.get_by_name('input').set_property('socket', self.video_socket)
            self.pipeline.get_by_name('depay').get_static_pad('sink').add_probe(
                Gst.PadProbeType.BUFFER, self.capture_stamp)
            self.pipeline.get_by_name('frames').connect('new-sample', self.on_sample)
            self.bus = self.pipeline.get_bus()
            self.bus.add_signal_watch()
            self.bus.connect('message', self.on_message)
            self.watch = GLib.io_add_watch(self.metadata_socket.fileno(), GLib.PRIORITY_DEFAULT,
                                          GLib.IO_IN, self.read_metadata)
            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('映像パイプラインを起動できません')
        except Exception:
            self.close()
            raise

    def capture_stamp(self, _pad, info):
        buffer = info.get_buffer()
        if buffer and buffer.pts != Gst.CLOCK_TIME_NONE:
            ok, rtp = GstRtp.RTPBuffer.map(buffer, Gst.MapFlags.READ)
            if ok:
                stamp = rtp.get_timestamp()
                rtp.unmap()
                with self.pts_lock:
                    self.pts_to_rtp[buffer.pts] = stamp
                    while len(self.pts_to_rtp) > 256:
                        self.pts_to_rtp.popitem(last=False)
        return Gst.PadProbeReturn.OK

    def on_sample(self, sink):
        sample = sink.emit('pull-sample')
        if not sample or self.closed:
            return Gst.FlowReturn.FLUSHING
        buffer = sample.get_buffer()
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        with self.pts_lock:
            stamp = self.pts_to_rtp.get(buffer.pts)
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if ok:
            try:
                pixels = bytes(mapped.data[info.offset[0]:])
            finally:
                buffer.unmap(mapped)
            now = time.monotonic()
            self.buffer.add_frame(Frame(now, stamp, pixels, info.width, info.height, info.stride[0]))
            self.frame_count += 1
            self.last_video = now
        return Gst.FlowReturn.OK

    def read_metadata(self, _fd, condition):
        if self.closed:
            return False
        # Bound each dispatch so many channels cannot starve the GTK main loop.
        for _ in range(64):
            try:
                raw, _ = self.metadata_socket.recvfrom(65535)
            except BlockingIOError:
                break
            except OSError:
                return False
            try:
                stamp, objects = parse_metadata(raw, self.index)
                self.buffer.add_metadata(stamp, objects)
                self.meta_count += 1
                self.last_metadata = time.monotonic()
            except (ValueError, TypeError, KeyError, UnicodeError, OverflowError):
                self.bad_count += 1
        return True

    def on_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, _ = message.parse_error()
            self.error = error.message
        elif message.type == Gst.MessageType.EOS:
            self.error = '映像ストリームが終了しました'

    def tick(self, now):
        frame = self.buffer.select(now)
        if frame:
            self.frame = frame
            self.area.set_video_size(frame.width, frame.height)
            self.pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(frame.pixels), GdkPixbuf.Colorspace.RGB, False,
                8, frame.width, frame.height, frame.stride)
            if self.buffer.matching(frame.stamp) is not None:
                self.match_count += 1
        self.area.queue_draw()
        state = '受信待ち' if not self.last_video else ('映像停止' if now - self.last_video > 2 else '受信中')
        matched = self.frame is not None and self.buffer.matching(self.frame.stamp) is not None
        detail = self.error or ('同期済み' if matched else '対応する検出結果なし')
        self.label.set_text(f'CH {self.index} | {state} | {detail} | 映像 {self.frame_count} / 結果 {self.meta_count}')
        self.label.set_tooltip_text(f'不正JSON: {self.bad_count}, 表示時同期: {self.match_count}\n'
                                   f'UDP {self.args.video_port_base + self.index} / {self.args.metadata_port_base + self.index}')

    def draw(self, area, cr):
        cr.set_source_rgb(0.04, 0.04, 0.05)
        cr.paint()
        if self.pixbuf is None:
            return
        width, height = area.get_allocated_width(), area.get_allocated_height()
        scale = min(width / self.frame.width, height / self.frame.height)
        cr.save()
        cr.translate((width - self.frame.width * scale) / 2, (height - self.frame.height * scale) / 2)
        cr.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        objects = self.buffer.matching(self.frame.stamp)
        if objects is not None and time.monotonic() - self.last_video < 2:
            cr.rectangle(0, 0, self.frame.width, self.frame.height)
            cr.clip()
            cr.set_line_width(2 / scale)
            cr.set_font_size(14 / scale)
            sx, sy = self.frame.width / self.args.width, self.frame.height / self.args.height
            for (x, y, w, h), score, label in objects:
                if score < self.args.threshold:
                    continue
                x, y, w, h = x * sx, y * sy, w * sx, h * sy
                cr.set_source_rgb(0.2, 1, 0.4)
                cr.rectangle(x, y, w, h)
                cr.stroke()
                text = f'{label} {score:.0%}'
                _, _, tw, _, _, _ = cr.text_extents(text)
                label_y = max(18 / scale, y)
                cr.set_source_rgba(0, 0, 0, 0.8)
                cr.rectangle(x, label_y - 18 / scale, tw + 8 / scale, 20 / scale)
                cr.fill()
                cr.set_source_rgb(0.2, 1, 0.4)
                cr.move_to(x + 4 / scale, label_y - 3 / scale)
                cr.show_text(text)
        cr.restore()

    def close(self):
        self.closed = True
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        if self.bus:
            self.bus.remove_signal_watch()
            self.bus = None
        if self.video_socket:
            self.video_socket.close()
            self.video_socket = None
        if self.metadata_socket:
            self.metadata_socket.close()
            self.metadata_socket = None


class DetectionViewer(Gtk.Window):
    def __init__(self, args):
        super().__init__(title='mViewer — PCIe Detection Viewer')
        self.args, self.receivers = args, []
        self.selected_receiver = None
        self.scroll_direction = 1
        self.last_scroll_time = time.monotonic()
        self.set_default_size(1280, 800)
        self.connect('destroy', self.shutdown)
        self.connect('key-press-event', self.key)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(8)
        self.add(root)
        toolbar = Gtk.Box(spacing=8)
        root.pack_start(toolbar, False, False, 0)
        toolbar.pack_start(Gtk.Label(label='チャネル'), False, False, 0)
        self.selection = Gtk.Entry()
        self.selection.set_text(','.join(map(str, args.channels)))
        toolbar.pack_start(self.selection, False, False, 0)
        start = Gtk.Button(label='受信開始 / チャネル変更')
        start.connect('clicked', self.start)
        toolbar.pack_start(start, False, False, 0)
        stop = Gtk.Button(label='停止')
        stop.connect('clicked', lambda _: self.stop())
        toolbar.pack_start(stop, False, False, 0)
        toolbar.pack_start(Gtk.Label(label='検出しきい値'), False, False, 0)
        threshold = Gtk.SpinButton.new_with_range(0, 1, 0.05)
        threshold.set_digits(2)
        threshold.set_value(args.threshold)
        threshold.connect('value-changed', lambda w: setattr(args, 'threshold', w.get_value()))
        toolbar.pack_start(threshold, False, False, 0)
        fullscreen = Gtk.Button(label='全画面')
        fullscreen.connect('clicked', lambda _: self.fullscreen())
        toolbar.pack_end(fullscreen, False, False, 0)
        if args.layout == 'sidebar':
            body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            root.pack_start(body, True, True, 0)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_size_request(210, -1)
            self.thumbnail_scroll = scroll
            self.auto_scroll = Gtk.CheckButton(label='ゆっくり自動スクロール')
            self.scroll_speed = Gtk.SpinButton.new_with_range(5, 100, 5)
            self.scroll_speed.set_value(20)
            sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            sidebar.pack_start(self.auto_scroll, False, False, 0)
            speed_row = Gtk.Box(spacing=4)
            speed_row.pack_start(Gtk.Label(label='速度 (px/秒)'), False, False, 0)
            speed_row.pack_start(self.scroll_speed, False, False, 0)
            sidebar.pack_start(speed_row, False, False, 0)
            sidebar.pack_start(scroll, True, True, 0)
            self.thumbnails = Gtk.ListBox()
            self.thumbnails.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.thumbnails.connect('row-selected', self.select_thumbnail)
            self.auto_scroll.connect('toggled', self.follow_top_thumbnail)
            scroll.get_vadjustment().connect('value-changed', self.follow_top_thumbnail)
            scroll.add(self.thumbnails)
            body.pack1(sidebar, resize=False, shrink=False)
            detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            detail.set_border_width(8)
            self.detail_label = Gtk.Label(label='左のサムネイルを選択してください。')
            self.detail_label.set_xalign(0)
            self.detail_label.set_ellipsize(Pango.EllipsizeMode.END)
            detail.pack_start(self.detail_label, False, False, 0)
            self.detail_area = Gtk.DrawingArea()
            self.detail_area.set_size_request(320, 180)
            self.detail_area.connect('draw', self.draw_detail)
            detail.pack_start(self.detail_area, True, True, 0)
            body.pack2(detail, resize=True, shrink=False)
            body.set_position(220)
        else:
            scroll = Gtk.ScrolledWindow()
            root.pack_start(scroll, True, True, 0)
            self.grid = Gtk.Grid(column_spacing=8, row_spacing=8)
            self.grid.set_row_homogeneous(True)
            self.grid.set_column_homogeneous(True)
            scroll.add(self.grid)
        self.status = Gtk.Label()
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        root.pack_start(self.status, False, False, 0)
        self.timer = GLib.timeout_add(33, self.tick)
        self.start()

    def key(self, _window, event):
        if event.keyval == 65307:
            self.unfullscreen()

    def select_thumbnail(self, _list, row):
        self.selected_receiver = row.get_child() if row else None
        self.refresh_detail()

    def follow_top_thumbnail(self, *_):
        if not self.auto_scroll.get_active():
            return
        position = self.thumbnail_scroll.get_vadjustment().get_value()
        for row in self.thumbnails.get_children():
            allocation = row.get_allocation()
            # A partially visible row at the upper edge is still the top channel.
            if allocation.y + allocation.height > position:
                if self.thumbnails.get_selected_row() is not row:
                    self.thumbnails.select_row(row)
                return

    def refresh_detail(self):
        if self.args.layout == 'sidebar':
            receiver = self.selected_receiver
            self.detail_label.set_text(receiver.label.get_text() if receiver else '受信を停止しました。')
            self.detail_area.queue_draw()

    def draw_detail(self, area, cr):
        if self.selected_receiver:
            # Share the decoded frame and matching detections with the thumbnail.
            self.selected_receiver.draw(area, cr)
        else:
            cr.set_source_rgb(0.04, 0.04, 0.05)
            cr.paint()

    def start(self, *_):
        try:
            selected = channels(self.selection.get_text())
            if any(c >= self.args.stream_count for c in selected):
                raise ValueError(f'設定には{self.args.stream_count}チャネルしかありません')
        except (ValueError, argparse.ArgumentTypeError) as error:
            self.status.set_text(str(error))
            return
        previous = self.selected_receiver.index if self.selected_receiver else None
        self.stop()
        try:
            for index in selected:
                receiver = Channel(index, self.args)
                self.receivers.append(receiver)
                position = len(self.receivers) - 1
                if self.args.layout == 'sidebar':
                    receiver.set_border_width(2)
                    receiver.area.fit_width = True
                    receiver.area.set_size_request(-1, -1)
                    receiver.label.set_ellipsize(Pango.EllipsizeMode.END)
                    receiver.label.set_max_width_chars(24)
                    self.thumbnails.add(receiver)
                else:
                    self.grid.attach(receiver, position % self.args.columns, position // self.args.columns, 1, 1)
                    receiver.set_hexpand(True)
                    receiver.set_vexpand(True)
        except (OSError, GLib.Error, RuntimeError) as error:
            self.stop()
            self.status.set_text(f'受信開始に失敗: {error}。送信先設定とポート競合を確認してください。')
            return
        if self.args.layout == 'sidebar':
            self.thumbnails.show_all()
            receiver = next((r for r in self.receivers if r.index == previous), self.receivers[0])
            self.thumbnails.select_row(receiver.get_parent())
        else:
            self.grid.show_all()
        self.status.set_text(f'直接受信: {self.args.bind} | 映像UDP {self.args.video_port_base}+CH / '
                             f'検出JSON UDP {self.args.metadata_port_base}+CH | 表示待ち {self.args.delay} ms')

    def stop(self):
        self.selected_receiver = None
        for receiver in self.receivers:
            receiver.close()
        if self.args.layout == 'sidebar':
            for row in self.thumbnails.get_children():
                row.destroy()
        else:
            for receiver in self.receivers:
                self.grid.remove(receiver)
                receiver.destroy()
        self.receivers.clear()
        self.refresh_detail()
        self.status.set_text('受信を停止しました。')

    def tick(self):
        now = time.monotonic()
        for receiver in self.receivers:
            receiver.tick(now)
        self.refresh_detail()
        self.advance_scroll(now)
        return True

    def advance_scroll(self, now):
        elapsed = max(0, min(now - self.last_scroll_time, 0.1))
        self.last_scroll_time = now
        if self.args.layout != 'sidebar' or not self.auto_scroll.get_active():
            return
        self.follow_top_thumbnail()
        adjustment = self.thumbnail_scroll.get_vadjustment()
        lower = adjustment.get_lower()
        upper = max(lower, adjustment.get_upper() - adjustment.get_page_size())
        if upper <= lower:
            return
        position = adjustment.get_value() + self.scroll_direction * self.scroll_speed.get_value() * elapsed
        if position >= upper:
            position, self.scroll_direction = upper, -1
        elif position <= lower:
            position, self.scroll_direction = lower, 1
        adjustment.set_value(position)

    def shutdown(self, *_):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None
        self.stop()
        Gtk.main_quit()


def arguments(argv=None):
    parser = argparse.ArgumentParser(description='PCIe検出アプリのRTP映像とJSON検出結果を直接表示')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.detector.yaml'))
    parser.add_argument('--channels', type=channels, default=channels('0-31'))
    parser.add_argument('--layout', choices=('sidebar', 'grid'), default='sidebar',
                        help='左サムネイル＋右拡大表示（既定）、または分割表示')
    parser.add_argument('--columns', type=int, default=2)
    parser.add_argument('--bind', default='0.0.0.0', help='受信するローカルIPv4アドレス')
    parser.add_argument('--video-port-base', type=int)
    parser.add_argument('--metadata-port-base', type=int)
    parser.add_argument('--delay', type=int, default=350, help='デコード後に検出結果を待つ時間 ms')
    parser.add_argument('--jitter', type=int, default=100, help='RTPジッターバッファ ms')
    parser.add_argument('--threshold', type=float, default=0.3)
    args = parser.parse_args(argv)
    try:
        config = yaml.safe_load(args.config.read_text())
        if not isinstance(config, dict):
            raise ValueError('設定はYAMLマッピングで指定してください')
        if config['output'].get('video_enabled') is not True:
            raise ValueError('output.video_enabled: true が必要です')
        output = config['output']['insight']
        args.video_port_base = args.video_port_base if args.video_port_base is not None else int(output['video_port_base'])
        args.metadata_port_base = args.metadata_port_base if args.metadata_port_base is not None else int(output['metadata_port_base'])
        args.width, args.height = int(config['input']['width']), int(config['input']['height'])
        args.stream_count = len(config['streams'])
        socket.inet_aton(args.bind)
        if (not 1 <= args.columns <= 16 or not 0 <= args.delay <= 2000
                or not 0 <= args.jitter <= 2000 or not 0 <= args.threshold <= 1
                or args.width <= 0 or args.height <= 0 or not 1 <= args.stream_count <= 80):
            raise ValueError('表示設定の値が範囲外です')
        video_ports = set(range(args.video_port_base, args.video_port_base + args.stream_count))
        metadata_ports = set(range(args.metadata_port_base, args.metadata_port_base + args.stream_count))
        if min(video_ports | metadata_ports) < 1 or max(video_ports | metadata_ports) > 65535 or video_ports & metadata_ports:
            raise ValueError('UDPポート範囲が不正、または重複しています')
        if any(c >= args.stream_count for c in args.channels):
            raise ValueError('選択チャネルが設定のストリーム数を超えています')
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        parser.error(f'設定エラー: {error}')
    return args


def main():
    args = arguments()
    Gst.init(None)
    if not Gtk.init_check()[0]:
        sys.exit('UbuntuのGUIセッションから実行してください。')
    window = DetectionViewer(args)
    window.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
