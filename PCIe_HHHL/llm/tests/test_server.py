import io
import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Server, audio_multipart, chat_payload


class PayloadTests(unittest.TestCase):
    def test_single_current_image_and_text_history(self):
        data = chat_payload({'text': '画像を説明', 'image': 'data:image/jpeg;base64,/9j/2Q==',
                             'history': [{'role': 'user', 'content': '質問'}, {'role': 'assistant', 'content': '回答'}]})
        self.assertEqual(data['model'], 'gemma4')
        self.assertFalse(data['enable_thinking'])
        self.assertEqual(data['messages'][-1]['content'][1]['type'], 'image_url')
        self.assertIsInstance(data['messages'][1]['content'], str)

    def test_reject_bad_requests(self):
        for data in [[], {}, {'text': ' '}, {'text': 'x', 'image': 'http://example.com/a.jpg'},
                     {'text': 'x', 'history': [{'role': 'system', 'content': 'x'}]},
                     {'text': 'x', 'history': [{'role': 'user', 'content': 'x'}]},
                     {'text': 'x' * 6001}, {'text': 'x', 'image': 'data:image/jpeg;base64,???'}]:
            with self.subTest(data=str(data)[:100]), self.assertRaises(ValueError):
                chat_payload(data)

    def test_wav_contract(self):
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\0' * 32000)
        body, mime = audio_multipart(raw.getvalue())
        self.assertIn(b'name="file"', body)
        self.assertIn(b'\r\nja\r\n', body)
        self.assertTrue(mime.startswith('multipart/form-data; boundary='))
        for bad in [b'not a wave', raw.getvalue()[:100]]:
            with self.assertRaises(ValueError):
                audio_multipart(bad)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(('127.0.0.1', 0), 'http://127.0.0.1:1')
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_ui_and_path_allowlist(self):
        with urllib.request.urlopen(self.url) as r:
            self.assertIn('Gemma4', r.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url + '/../card_server.py')
        self.assertEqual(cm.exception.code, 404)

    def test_unavailable_card(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url + '/api/health')
        self.assertEqual(cm.exception.code, 503)

    def test_foreign_origin_rejected(self):
        req = urllib.request.Request(self.url + '/api/chat', data=b'{"text":"x"}',
                                     headers={'Origin': 'http://unrelated.example'})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)

    def test_invalid_json(self):
        req = urllib.request.Request(self.url + '/api/chat', data=b'{')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
