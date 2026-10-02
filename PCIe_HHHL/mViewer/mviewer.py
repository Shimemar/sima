#!/usr/bin/python3
"""Ubuntu RTSP viewer using GTK 3 and GStreamer."""
import argparse
import sys
from urllib.parse import urlsplit

try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gst', '1.0')
    from gi.repository import Gtk, Gst, GLib
except (ImportError, ValueError):
    sys.exit('GTK/GStreamerが必要です。README.mdのインストール手順を実行してください。')


class Viewer(Gtk.Window):
    def __init__(self, url='', transport='tcp', latency=200):
        super().__init__(title='mViewer — RTSP Viewer')
        self.set_default_size(1100, 720)
        self.connect('destroy', self.on_destroy)
        self.retry_id = 0
        self.active = False
        self.player = Gst.ElementFactory.make('playbin', 'player')
        sink = Gst.ElementFactory.make('gtksink', 'video')
        if self.player is None or sink is None:
            raise RuntimeError('playbin / gtksinkがありません。README.mdの依存パッケージをインストールしてください。')
        self.player.set_property('video-sink', sink)
        audio_sink = Gst.ElementFactory.make('fakesink', 'audio')
        self.player.set_property('audio-sink', audio_sink)
        self.player.connect('source-setup', self.source_setup)
        self.bus = self.player.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message', self.on_message)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        self.add(box)
        bar = Gtk.Box(spacing=8)
        box.pack_start(bar, False, False, 0)
        self.url = Gtk.Entry()
        self.url.set_placeholder_text('rtsp://192.168.1.100:554/stream')
        self.url.set_text(url)
        self.url.set_visibility(False)
        self.url.connect('activate', self.connect_stream)
        bar.pack_start(self.url, True, True, 0)
        reveal = Gtk.CheckButton(label='URLを表示')
        reveal.connect('toggled', lambda w: self.url.set_visibility(w.get_active()))
        bar.pack_start(reveal, False, False, 0)
        self.start_button = Gtk.Button(label='接続')
        self.start_button.connect('clicked', self.connect_stream)
        bar.pack_start(self.start_button, False, False, 0)
        self.stop_button = Gtk.Button(label='切断')
        self.stop_button.connect('clicked', self.disconnect_stream)
        self.stop_button.set_sensitive(False)
        bar.pack_start(self.stop_button, False, False, 0)

        options = Gtk.Box(spacing=10)
        box.pack_start(options, False, False, 0)
        options.pack_start(Gtk.Label(label='転送方式'), False, False, 0)
        self.transport = Gtk.ComboBoxText()
        for mode in ('TCP', 'UDP'):
            self.transport.append_text(mode)
        self.transport.set_active(0 if transport == 'tcp' else 1)
        options.pack_start(self.transport, False, False, 0)
        options.pack_start(Gtk.Label(label='バッファ (ms)'), False, False, 0)
        self.latency = Gtk.SpinButton.new_with_range(0, 5000, 50)
        self.latency.set_value(latency)
        options.pack_start(self.latency, False, False, 0)
        self.reconnect = Gtk.CheckButton(label='自動再接続（3秒後）')
        self.reconnect.set_active(True)
        options.pack_start(self.reconnect, False, False, 0)
        fullscreen = Gtk.Button(label='全画面')
        fullscreen.connect('clicked', lambda _: self.fullscreen())
        options.pack_end(fullscreen, False, False, 0)
        self.connect('key-press-event', self.on_key)
        box.pack_start(sink.get_property('widget'), True, True, 0)
        self.status = Gtk.Label(label='RTSP URLを入力して「接続」を押してください。')
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)

    def on_key(self, _window, event):
        if event.keyval == 65307:  # Escape
            self.unfullscreen()

    def source_setup(self, _player, source):
        # source-setup can run on a streaming thread: use saved values, not GTK.
        for name, value in [('protocols', self.protocol), ('latency', self.buffer_ms),
                            ('drop-on-latency', True), ('tcp-timeout', 10000000),
                            ('timeout', 10000000)]:
            if source.find_property(name):
                source.set_property(name, value)

    def connect_stream(self, *_args):
        if self.active:
            return
        uri = self.url.get_text().strip()
        try:
            parts = urlsplit(uri)
            valid = parts.scheme.lower() in ('rtsp', 'rtsps') and bool(parts.hostname)
            _ = parts.port
        except ValueError:
            valid = False
        if not valid:
            self.status.set_text('有効なRTSP URLを入力してください。例: rtsp://host:554/stream')
            return
        self.protocol = 4 if self.transport.get_active() == 0 else 1
        self.buffer_ms = self.latency.get_value_as_int()
        self.uri = uri
        self.active = True
        self.set_controls(True)
        self.play()

    def set_controls(self, connected):
        for widget in (self.url, self.transport, self.latency, self.start_button):
            widget.set_sensitive(not connected)
        self.stop_button.set_sensitive(connected)

    def play(self):
        self.retry_id = 0
        if not self.active:
            return False
        self.bus.set_flushing(True)
        self.player.set_state(Gst.State.NULL)
        self.bus.set_flushing(False)
        self.player.set_property('uri', self.uri)
        self.status.set_text('接続中…')
        if self.player.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.failed()
        return False

    def failed(self):
        self.player.set_state(Gst.State.NULL)
        if self.active and self.reconnect.get_active():
            self.status.set_text('映像を受信できません。URL・認証・ネットワークを確認してください。3秒後に再接続します。')
            if not self.retry_id:
                self.retry_id = GLib.timeout_add_seconds(3, self.play)
        else:
            self.disconnect_stream()
            self.status.set_text('映像を受信できません。URL・認証・ネットワークを確認してください。')

    def on_message(self, _bus, message):
        if not self.active:
            return
        if message.type in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
            # Backend errors can contain credentials; do not display or log them.
            self.failed()
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src == self.player:
            _, state, _ = message.parse_state_changed()
            if state == Gst.State.PLAYING:
                self.status.set_text('映像を表示中（音声なし）')

    def disconnect_stream(self, *_args):
        self.active = False
        if self.retry_id:
            GLib.source_remove(self.retry_id)
            self.retry_id = 0
        self.player.set_state(Gst.State.NULL)
        self.set_controls(False)
        self.status.set_text('切断しました。')

    def on_destroy(self, *_args):
        self.disconnect_stream()
        self.bus.remove_signal_watch()
        Gtk.main_quit()


def main():
    parser = argparse.ArgumentParser(description='Ubuntu RTSP映像ビューアー')
    parser.add_argument('url', nargs='?', default='', help='RTSP URL（GUIからの入力も可能）')
    parser.add_argument('--transport', choices=('tcp', 'udp'), default='tcp')
    parser.add_argument('--latency', type=int, default=200, help='受信バッファ ms (0–5000)')
    args = parser.parse_args()
    if not 0 <= args.latency <= 5000:
        parser.error('--latencyは0〜5000です')
    Gst.init(None)
    if not Gtk.init_check()[0]:
        sys.exit('GUIディスプレイに接続できません。Ubuntuのデスクトップ上で実行してください。')
    try:
        window = Viewer(args.url, args.transport, args.latency)
    except RuntimeError as error:
        sys.exit(str(error))
    window.show_all()
    if args.url:
        GLib.idle_add(window.connect_stream)
    Gtk.main()


if __name__ == '__main__':
    main()
