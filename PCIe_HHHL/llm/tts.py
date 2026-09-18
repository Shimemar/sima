"""Japanese speech synthesis using bundled Open JTalk, without network access."""
import io
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import wave

ROOT = Path(__file__).resolve().parent / 'vendor' / 'open-jtalk'


def speech_text(data):
    text = data.get('text') if isinstance(data, dict) else None
    if not isinstance(text, str) or len(text) > 6000:
        raise ValueError('読み上げテキストは1〜6000文字で指定してください。')
    text = text.replace('*', '').strip()
    if not text or '\0' in text:
        raise ValueError('読み上げ可能なテキストがありません。')
    return text


class OfflineSpeech:
    def __init__(self):
        self.lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.process = None
        self.closed = False

    def synthesize(self, text):
        executable = ROOT / 'usr/bin/open_jtalk'
        dictionary = ROOT / 'var/lib/mecab/dic/open-jtalk/naist-jdic'
        voice = ROOT / 'usr/share/hts-voice/nitech-jp-atr503-m001/nitech_jp_atr503_m001.htsvoice'
        if not all(p.exists() for p in (executable, dictionary / 'sys.dic', voice)):
            raise RuntimeError('音声合成ファイルがありません。setup_tts.sh を実行してください。')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('音声合成中です。少し待ってから再試行してください。')
        try:
            with tempfile.TemporaryDirectory(prefix='gemma4-tts-') as folder:
                output = Path(folder) / 'speech.wav'
                env = dict(os.environ)
                env['LD_LIBRARY_PATH'] = str(ROOT / 'usr/lib/x86_64-linux-gnu') + os.pathsep + env.get('LD_LIBRARY_PATH', '')
                with self.state_lock:
                    if self.closed:
                        raise RuntimeError('アプリは終了処理中です。')
                    process = subprocess.Popen([str(executable), '-x', str(dictionary), '-m', str(voice),
                                                '-r', '1.1', '-ow', str(output)],
                                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.PIPE, env=env)
                    self.process = process
                try:
                    _, error = process.communicate(text.encode('utf-8'), timeout=60)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.communicate()
                    raise RuntimeError('音声合成がタイムアウトしました。') from exc
                finally:
                    with self.state_lock:
                        self.process = None
                if process.returncode:
                    raise RuntimeError('音声合成に失敗しました: ' + error.decode('utf-8', errors='replace')[-1000:])
                audio = output.read_bytes()
                with wave.open(io.BytesIO(audio), 'rb') as wav:
                    if wav.getnframes() == 0:
                        raise RuntimeError('音声が生成されませんでした。')
                return audio
        finally:
            self.lock.release()

    def close(self):
        with self.state_lock:
            self.closed = True
            if self.process is not None and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
