"""Optional: run with playwright installed while ./run.sh is running.
Uses Chromium's fake camera/microphone and a supplied speech WAV.
"""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--wav', type=Path, required=True)
parser.add_argument('--url', default='http://localhost:8080')
args = parser.parse_args()
output = Path(__file__).resolve().parents[1] / 'test-results'
output.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True,
        args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
              f'--use-file-for-fake-audio-capture={args.wav.resolve()}'])
    page = browser.new_page(viewport={'width': 1400, 'height': 1000}, permissions=['camera', 'microphone'])
    errors = []
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.goto(args.url)
    page.wait_for_function("document.getElementById('health').className === 'ready'")
    page.locator('#prompt').fill('こんにちは。一文で挨拶してください。')
    page.locator('#prompt').dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
    assert page.locator('.message').count() == 0, 'IME composition must not submit'
    page.locator('#prompt').press('Enter')
    page.wait_for_function("!document.getElementById('send').disabled", timeout=120000)
    assert page.locator('.assistant .meta').count() == 1
    assert not page.locator('#notice').is_visible(), page.locator('#notice').inner_text()
    page.locator('#cameraToggle').click()
    page.wait_for_function("document.getElementById('video').videoWidth > 0")
    page.locator('#capture').click()
    assert page.locator('#snapshot').evaluate('(img) => img.naturalWidth') == 480
    page.locator('#prompt').fill('画像の色や模様を日本語で説明してください。')
    page.locator('#send').click()
    page.wait_for_function("!document.getElementById('send').disabled", timeout=120000)
    assert page.locator('.assistant .meta').count() == 2
    assert not page.locator('#notice').is_visible(), page.locator('#notice').inner_text()
    page.locator('#record').click()
    page.wait_for_function("document.getElementById('record').classList.contains('recording')")
    page.wait_for_timeout(4000)
    page.locator('#record').click()
    page.wait_for_function("!document.getElementById('send').disabled", timeout=120000)
    assert page.locator('#prompt').input_value().strip(), page.locator('#notice').inner_text()
    assert page.locator('#recordStatus').inner_text() == '文字起こし完了'
    page.screenshot(path=str(output / 'browser.png'), full_page=True)
    print('Text:', page.locator('.assistant').nth(0).inner_text())
    print('Image:', page.locator('.assistant').nth(1).inner_text())
    print('Audio:', page.locator('#prompt').input_value())
    page.locator('#cameraToggle').click()
    assert page.locator('#video').evaluate('(v) => v.srcObject === null')
    page.locator('#clear').click()
    assert page.locator('.message').count() == 0
    assert not errors, errors
    browser.close()
print('Browser smoke passed: IME, text, camera, image, microphone, ASR, reset; no JS errors.')
