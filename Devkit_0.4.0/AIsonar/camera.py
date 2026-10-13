import atexit
import glob
import sys
import threading
import time

try:
    import cv2
except ImportError:
    # venvにcv2が無い場合はDevKitのシステムOpenCVを使う
    for p in glob.glob('/usr/lib/python3*/dist-packages'):
        sys.path.append(p)
    import cv2


class Camera:
    """USBカメラ(V4L2)から常に最新フレームを読み続ける。古いフレームのバッファ遅延を防ぐため専用スレッドで読む。"""

    def __init__(self, device='/dev/video0', width=1280, height=720, fourcc='MJPG'):
        self.device, self.width, self.height, self.fourcc = device, width, height, fourcc
        self._lock = threading.Lock()
        self._frame = None
        self._stamp = 0.0
        self._started = False
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        atexit.register(self.stop)

    def stop(self):
        # cv2.read()中のままインタプリタが終了すると "FATAL: exception not rethrown" で落ちるため、終了時に止める
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _open(self):
        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f'カメラ {self.device} を開けません（他のアプリが使用中の可能性があります）')
        return cap

    def _loop(self):
        cap = None
        while not self._stop.is_set():
            try:
                if cap is None:
                    cap = self._open()
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError(f'カメラ {self.device} からフレームを読めません')
                with self._lock:
                    self._frame, self._stamp, self.error = frame, time.time(), None
            except Exception as e:
                self.error = str(e)
                if cap is not None:
                    cap.release()
                    cap = None
                self._stop.wait(2)
        if cap is not None:
            cap.release()

    def latest(self, max_age=2.0):
        """最新フレーム(BGR)を返す。新しいフレームが無ければ例外。"""
        self.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            with self._lock:
                if self._frame is not None and time.time() - self._stamp < max_age:
                    return self._frame.copy()
            time.sleep(0.05)
        raise RuntimeError(self.error or 'カメラの映像が取得できません')

    def latest_stamped(self, max_age=2.0):
        """(フレーム, 取得時刻) を返す（コピーしない: 呼び出し側で書き換えないこと）。同じフレームの再処理を避ける用。"""
        self.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            with self._lock:
                if self._frame is not None and time.time() - self._stamp < max_age:
                    return self._frame, self._stamp
            time.sleep(0.005)
        raise RuntimeError(self.error or 'カメラの映像が取得できません')

    def jpeg(self, max_width=None, quality=80):
        frame = self.latest()
        if max_width and frame.shape[1] > max_width:
            scale = max_width / frame.shape[1]
            frame = cv2.resize(frame, (max_width, int(frame.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError('JPEGエンコードに失敗しました')
        return buf.tobytes()
