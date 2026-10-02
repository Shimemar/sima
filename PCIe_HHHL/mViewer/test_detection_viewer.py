"""Run with /usr/bin/python3 -m unittest discover -s mViewer -v."""
import json
import socket
import time
import unittest

from detection_viewer import (
    Channel, DetectionViewer, Frame, GLib, Gst, GstRtp, Gtk,
    SyncBuffer, arguments, bind_udp, channels, parse_metadata,
)


def envelope(channel, stamp, objects=None):
    return json.dumps({
        'type': 'object-detection', 'stream_index': channel,
        'stream_id': f'stream{channel}', 'rtp_timestamp': stamp,
        'data': {'objects': objects if objects is not None else [
            {'bbox': [200, 120, 400, 300], 'confidence': 0.9, 'label': 'person'}]},
    }).encode()


class ProtocolTests(unittest.TestCase):
    def test_envelope_and_rejection(self):
        stamp, objects = parse_metadata(envelope(3, 0xffffffff), 3)
        self.assertEqual(stamp, 0xffffffff)
        self.assertEqual(objects[0], ([200, 120, 400, 300], 0.9, 'person'))
        self.assertEqual(parse_metadata(envelope(3, 0, []), 3), (0, []))
        for raw in (b'bad', envelope(2, 0), envelope(3, -1), envelope(3, 2**32),
                    envelope(3, 0, [{'bbox': [0, 0, -1, 5], 'confidence': .5, 'label': 'x'}])):
            with self.assertRaises((ValueError, KeyError, TypeError)):
                parse_metadata(raw, 3)

    def test_exact_sync_delay_expiration_and_bounds(self):
        buf = SyncBuffer(350)
        frame = Frame(10, 0xffffffff, b'', 1, 1, 3)
        buf.add_frame(frame)
        buf.add_metadata(0xffffffff, ['correct'], now=10)
        buf.add_metadata(0, ['other frame'], now=10)
        self.assertIsNone(buf.select(10.34))
        self.assertIs(buf.select(10.36), frame)
        self.assertEqual(buf.matching(frame.stamp), ['correct'])
        self.assertIsNone(buf.matching(123))
        buf.select(16)
        self.assertIsNone(buf.matching(frame.stamp))
        for n in range(1000):
            buf.add_metadata(n, [])
            buf.add_frame(frame)
        self.assertEqual(len(buf.metadata), 256)
        self.assertLessEqual(len(buf.frames), buf.frames.maxlen)

    def test_channels_and_exclusive_bind(self):
        self.assertEqual(channels('0-3,7,3'), [0, 1, 2, 3, 7])
        with bind_udp('127.0.0.1', 0) as sock:
            with self.assertRaises(OSError):
                bind_udp('127.0.0.1', sock.getsockname()[1])


class IntegrationTests(unittest.TestCase):
    def test_two_channel_rtp_json_overlay_and_cleanup(self):
        Gst.init(None)
        if not Gtk.init_check()[0]:
            self.skipTest('GUI display unavailable')
        # Reserve two video and two metadata ports without touching production.
        held = []
        for base in range(29000, 30000, 4):
            try:
                held = [bind_udp('127.0.0.1', base)]
                for offset in (1, 100, 101):
                    held.append(bind_udp('127.0.0.1', base + offset))
                break
            except OSError:
                for sock in held:
                    sock.close()
                held = []
        self.assertEqual(len(held), 4)
        for sock in held:
            sock.close()
        args = arguments(['--channels', '0-1', '--bind', '127.0.0.1',
                          '--video-port-base', str(base), '--metadata-port-base', str(base+100)])
        window = DetectionViewer(args)
        window.show_all()
        self.assertEqual(len(window.receivers), 2, window.status.get_text())
        sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        senders, delayed_ids = [], set()
        snapshots = []
        failures = []
        metadata_mode = ['detections']

        def send_metadata(payload, port, timer_id):
            delayed_ids.discard(timer_id[0])
            sender_socket.sendto(payload, ('127.0.0.1', port))
            return False

        def capture(_pad, info, channel):
            ok, rtp = GstRtp.RTPBuffer.map(info.get_buffer(), Gst.MapFlags.READ)
            if ok:
                marker, stamp = rtp.get_marker(), rtp.get_timestamp()
                rtp.unmap()
                if marker:
                    payload = envelope(channel, stamp, [] if metadata_mode[0] == 'empty' else None)
                    timer_id = [None]
                    timer_id[0] = GLib.timeout_add(150, send_metadata, payload, base+100+channel, timer_id)
                    delayed_ids.add(timer_id[0])
            return Gst.PadProbeReturn.OK

        try:
            for channel in (0, 1):
                pipeline = Gst.parse_launch(
                    'videotestsrc is-live=true pattern=black '
                    '! video/x-raw,width=1280,height=720,framerate=10/1 '
                    '! x264enc tune=zerolatency key-int-max=10 '
                    '! rtph264pay name=pay pt=96 config-interval=1 timestamp-offset=0 '
                    f'! udpsink host=127.0.0.1 port={base+channel} sync=false async=false')
                pipeline.get_by_name('pay').get_static_pad('src').add_probe(
                    Gst.PadProbeType.BUFFER, capture, channel)
                senders.append(pipeline)
                self.assertNotEqual(pipeline.set_state(Gst.State.PLAYING), Gst.StateChangeReturn.FAILURE)
            sender_socket.sendto(b'invalid JSON', ('127.0.0.1', base+100))
            # A valid envelope on the wrong channel must also be rejected.
            sender_socket.sendto(envelope(1, 123), ('127.0.0.1', base+100))

            def check():
                try:
                    for receiver in window.receivers:
                        self.assertGreater(receiver.frame_count, 5)
                        self.assertGreater(receiver.meta_count, 5)
                        self.assertGreater(receiver.match_count, 3)
                        self.assertIsNotNone(receiver.frame.stamp)
                        self.assertEqual(receiver.frame.width, 1280)
                    self.assertEqual(window.receivers[0].bad_count, 2)
                    self.assertIs(window.selected_receiver, window.receivers[0])
                    window.thumbnails.select_row(window.receivers[1].get_parent())
                    self.assertIs(window.selected_receiver, window.receivers[1])
                    import cairo
                    receiver = window.receivers[1]
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                                 window.detail_area.get_allocated_width(),
                                                 window.detail_area.get_allocated_height())
                    window.draw_detail(window.detail_area, cairo.Context(surface))
                    surface.flush()
                    # Black input plus bright green pixels proves the boxes were drawn.
                    self.assertGreater(sum(surface.get_data()[1::4]), 10000)
                    surface.write_to_png('/tmp/mviewer-detection-overlay.png')
                    snapshots.append('overlay')
                    metadata_mode[0] = 'empty'
                except Exception as error:
                    failures.append(error)
                return False

            def finish():
                try:
                    for receiver in window.receivers:
                        self.assertEqual(receiver.buffer.matching(receiver.frame.stamp), [])
                    snapshots.append('empty detection clears boxes')
                except Exception as error:
                    failures.append(error)
                window.destroy()
                return False

            GLib.timeout_add_seconds(3, check)
            GLib.timeout_add_seconds(5, finish)
            Gtk.main()
        finally:
            for pipeline in senders:
                pipeline.set_state(Gst.State.NULL)
            for timer_id in list(delayed_ids):
                GLib.source_remove(timer_id)
            sender_socket.close()
            for receiver in window.receivers:
                receiver.close()
        if failures:
            raise failures[0]
        self.assertEqual(len(snapshots), 2)
        for offset in (0, 1, 100, 101):
            with bind_udp('127.0.0.1', base+offset):
                pass


if __name__ == '__main__':
    unittest.main()
