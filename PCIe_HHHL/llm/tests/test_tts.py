import io
from pathlib import Path
import sys
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tts import OfflineSpeech, ROOT, speech_text


class SpeechTests(unittest.TestCase):
    def test_ignore_asterisks(self):
        self.assertEqual(speech_text({'text': '**こんにちは**。*項目*'}), 'こんにちは。項目')
        for value in [None, {}, {'text': '***'}, {'text': 'x' * 6001}, {'text': '\0'}]:
            with self.assertRaises(ValueError):
                speech_text(value)

    @unittest.skipUnless((ROOT / 'usr/bin/open_jtalk').exists(), 'Run setup_tts.sh first')
    def test_real_local_synthesis_without_http(self):
        engine = OfflineSpeech()
        try:
            with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
                data = engine.synthesize(speech_text({'text': '**オフライン**で音声を再生します。'}))
            with wave.open(io.BytesIO(data)) as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertGreater(audio.getnframes(), audio.getframerate())
                self.assertTrue(any(audio.readframes(audio.getnframes())))
        finally:
            engine.close()
        with self.assertRaisesRegex(RuntimeError, '終了'):
            engine.synthesize('こんにちは')
