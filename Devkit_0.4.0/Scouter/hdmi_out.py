"""Local HDMI output on the Modalix DevKit: BGR frames -> appsrc -> kmssink.

pyneat has no local-display node (VideoSender is UDP/RTP only), so this is plain
gst-python, following the on-device findings in taste_in_clothes/devkit_direct_hdmi.py:
  1. Xorg (lightdm) owns the HDMI DRM master from boot; kmssink can only draw after
     `sudo systemctl stop lightdm` (sima has passwordless sudo). close() always restarts
     it -- but a SIGKILLed process leaves the desktop stopped (`sudo systemctl start
     lightdm` by hand).
  2. GPU is SiliconMotion smifb: kmssink auto-detection fails, `driver-name=smifb` needed.
  3. smifb's plane claims can-scale but can't: frames must already be display-sized
     (`drmModeSetPlane failed: Invalid argument` otherwise). Scaling is done here with
     cv2.resize (multi-threaded) rather than GStreamer videoscale (single-threaded).
  4. smifb has no vblank ioctl: `skip-vsync=true` or the first frame errors out.
Frames are letterboxed to keep the camera aspect ratio on any display mode.
"""

from __future__ import annotations

import subprocess
import sys

import cv2
import numpy as np

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
except Exception:  # noqa: BLE001 - reported by HdmiOutput.start()
    Gst = None


def _systemctl(action: str) -> None:
    subprocess.run(["sudo", "-n", "systemctl", action, "lightdm"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class HdmiOutput:
    def __init__(self, driver_name: str = "smifb", width: int = 0, height: int = 0,
                 stop_lightdm: bool = True) -> None:
        """width/height = 0: use the display's current mode. Non-zero: kmssink
        force-modesetting switches the monitor to that mode (e.g. 1280x720, so camera
        frames need no upscaling and each frame is 3.7 MB instead of 8.3 MB over PCIe)."""
        self.driver_name = driver_name
        self.force_mode = bool(width and height)
        self.width, self.height = width, height
        self.stop_lightdm = stop_lightdm
        self._lightdm_stopped = False
        self._pipeline = None
        self._src = None
        self._bus = None
        self._canvas = None
        self._layout = None   # (src_w, src_h) -> (x, y, w, h) letterbox placement
        self.frames = 0
        self.error: str | None = None

    def start(self) -> tuple[int, int]:
        if Gst is None:
            raise RuntimeError("gst-python (gi.repository.Gst) is not available")
        Gst.init(None)
        if self.stop_lightdm:
            _systemctl("stop")
            self._lightdm_stopped = True
        if not (self.width and self.height):
            self.width, self.height = self._probe_size()
        desc = (
            f"appsrc name=src is-live=true format=time do-timestamp=true block=false "
            f"caps=video/x-raw,format=BGRx,width={self.width},height={self.height},framerate=0/1 ! "
            f"queue leaky=downstream max-size-buffers=2 ! "
            f"kmssink driver-name={self.driver_name} sync=false skip-vsync=true"
            + (" force-modesetting=true" if self.force_mode else "")
        )
        self._pipeline = Gst.parse_launch(desc)
        self._src = self._pipeline.get_by_name("src")
        self._bus = self._pipeline.get_bus()
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("HDMI pipeline failed to start (is a monitor connected?)")
        self._canvas = np.zeros((self.height, self.width, 4), np.uint8)
        return self.width, self.height

    def _probe_size(self) -> tuple[int, int]:
        """kmssink fills display-width/height on NULL->READY (needs the DRM master)."""
        probe = Gst.ElementFactory.make("kmssink", "probe")
        if probe is None:
            raise RuntimeError("kmssink element not found")
        probe.set_property("driver-name", self.driver_name)
        probe.set_state(Gst.State.READY)
        probe.get_state(Gst.CLOCK_TIME_NONE)
        w, h = probe.get_property("display-width"), probe.get_property("display-height")
        probe.set_state(Gst.State.NULL)
        if not w or not h:
            raise RuntimeError("could not detect HDMI resolution (no monitor connected?)")
        return int(w), int(h)

    def push(self, bgr) -> None:
        """Letterbox `bgr` onto the display-sized BGRx canvas and hand it to kmssink."""
        if self._src is None or self.error:
            return
        msg = self._bus.pop_filtered(Gst.MessageType.ERROR)
        if msg is not None:
            err, debug = msg.parse_error()
            self.error = f"{err.message} ({debug})"
            print(f"[ERR] HDMI output stopped: {self.error}", file=sys.stderr, flush=True)
            return
        sh, sw = bgr.shape[:2]
        if self._layout is None or self._layout[0] != (sw, sh):
            scale = min(self.width / sw, self.height / sh)
            w, h = int(sw * scale) & ~1, int(sh * scale) & ~1
            self._layout = ((sw, sh), ((self.width - w) // 2, (self.height - h) // 2, w, h))
            self._canvas[:] = 0
        x, y, w, h = self._layout[1]
        resized = bgr if (w, h) == (sw, sh) else cv2.resize(bgr, (w, h), interpolation=cv2.INTER_LINEAR)
        cv2.cvtColor(resized, cv2.COLOR_BGR2BGRA, dst=self._canvas[y:y + h, x:x + w])
        buf = Gst.Buffer.new_wrapped(self._canvas.tobytes())
        self._src.emit("push-buffer", buf)
        self.frames += 1

    def close(self) -> None:
        try:
            if self._pipeline is not None:
                self._pipeline.set_state(Gst.State.NULL)
        finally:
            self._pipeline = self._src = None
            if self._lightdm_stopped:
                _systemctl("start")
                self._lightdm_stopped = False
