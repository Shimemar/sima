"""Optional Playwright test: shutdown during recording, without real hardware."""
from pathlib import Path
import socket
import subprocess
import sys
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
        browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
                                    args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        page = browser.new_page(permissions=['camera', 'microphone'])
        errors, requests = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: requests.append(r.url))
        page.goto(f'http://localhost:{port}')
        page.locator('#cameraToggle').click()
        page.wait_for_function("document.getElementById('video').videoWidth > 0")
        page.locator('#record').click()
        page.wait_for_function("document.getElementById('record').classList.contains('recording')")
        page.evaluate('window.testTracks = [...cameraStream.getTracks(), ...recording.stream.getTracks()]')
        page.locator('#shutdown').click()
        page.wait_for_function("document.getElementById('shutdown').textContent === '終了要求済み'")
        assert proc.wait(timeout=5) == 0
        assert page.evaluate('window.testTracks.every(t => t.readyState === "ended")')
        assert page.locator('#send').is_disabled()
        assert page.locator('#record').is_disabled()
        assert not any(url.endswith('/api/transcribe') for url in requests)
        assert not errors, errors
        browser.close()
    print('PASS: GUI shutdown during recording; camera/mic released; no ASR sent; server process exited.')
finally:
    if proc.poll() is None:
        proc.terminate(); proc.wait(timeout=5)
    proc.stdout.close(); proc.stderr.close()
