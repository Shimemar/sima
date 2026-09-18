"""Test local WAV playback while blocking non-local browser requests."""
import json
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
proc = subprocess.Popen([sys.executable, '-u', str(ROOT / 'server.py'), '--port', str(port),
                         '--upstream', 'http://127.0.0.1:1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    assert b'Gemma4' in proc.stdout.readline()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True)
        page = browser.new_page()
        external, speech_requests, errors = [], [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        answer = '**こんにちは**。インターネットに接続せず音声を再生します。'
        def route(request):
            url = request.request.url
            if urlparse(url).hostname not in {'localhost', '127.0.0.1'}:
                external.append(url); request.abort(); return
            if url.endswith('/api/chat'):
                event = {'choices': [{'delta': {'content': answer}, 'finish_reason': 'stop'}]}
                request.fulfill(content_type='text/event-stream', body='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
            elif url.endswith('/api/speech'):
                speech_requests.append(request.request.post_data_json)
                request.continue_()
            else:
                request.continue_()
        page.route('**/*', route)
        page.add_init_script("speechSynthesis.speak = () => { throw new Error('Browser TTS forbidden'); };")
        page.goto(f'http://localhost:{port}')
        page.locator('#speak').check()
        page.locator('#prompt').fill('挨拶してください。')
        page.locator('#send').click()
        page.wait_for_function("document.getElementById('speechAudio').currentTime > 0", timeout=15000)
        assert '**こんにちは**' in page.locator('.assistant').inner_text()
        assert speech_requests and '*' not in speech_requests[0]['text']
        assert page.locator('#speechAudio').evaluate('(a) => a.duration > 1')
        page.locator('#speak').uncheck()
        assert page.locator('#speechAudio').is_hidden()
        assert page.locator('#speechAudio').evaluate('(a) => a.paused && !a.getAttribute("src")')
        page.locator('#shutdown').click()
        assert proc.wait(timeout=5) == 0
        assert not external, external
        assert not errors, errors
        browser.close()
    print('PASS: local WAV generated and played; no external browser requests; asterisks omitted; off/shutdown stops playback.')
finally:
    if proc.poll() is None:
        proc.terminate(); proc.wait(timeout=5)
    proc.stdout.close(); proc.stderr.close()
