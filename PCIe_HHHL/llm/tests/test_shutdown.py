"""Exercise GUI shutdown's HTTP entry point and launcher-owned SSH cleanup."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ShutdownTests(unittest.TestCase):
    def test_shutdown_releases_owned_worker_and_ui(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            port, api_port = free_port(), free_port()
            marker = path / 'worker-stopped'
            # Substitute SSH only; exercise the real launcher, HTTP server, and stdin EOF lifecycle.
            ssh = path / 'ssh'
            ssh.write_text(f'''#!{sys.executable}
import http.server, json, os, sys, threading
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  data = json.dumps({{"data":[{{"id":"gemma4"}},{{"id":"whisper"}}]}}).encode()
  self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
server = http.server.ThreadingHTTPServer(("127.0.0.1", {api_port}), Handler)
thread = threading.Thread(target=server.serve_forever); thread.start()
sys.stdin.buffer.read()
server.shutdown(); server.server_close(); thread.join()
open({str(marker)!r}, "w").write("closed")
''')
            ssh.chmod(0o755)
            env = dict(os.environ, PATH=str(path) + os.pathsep + os.environ['PATH'])
            entry = (f'import sys; sys.path.insert(0, {str(ROOT)!r}); import launch; '
                     f'from pathlib import Path; launch.ROOT = Path({str(path)!r}); '
                     'raise SystemExit(launch.main())')
            proc = subprocess.Popen([sys.executable, '-u', '-c', entry, '--card', '127.0.0.1',
                                     '--port', str(port), '--api-port', str(api_port), '--startup-timeout', '10'],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            url = f'http://127.0.0.1:{port}'
            try:
                deadline = time.monotonic() + 15
                while True:
                    self.assertIsNone(proc.poll(), 'Launcher exited before startup')
                    try:
                        with urllib.request.urlopen(url + '/api/health', timeout=.5) as response:
                            if json.load(response)['ready']:
                                break
                    except (OSError, ValueError):
                        pass
                    if time.monotonic() > deadline:
                        self.fail('Launcher startup timeout')
                    time.sleep(.1)
                # An unrelated web page must not be able to trigger shutdown.
                for headers in [{}, {'Origin': 'http://unrelated.example', 'X-Gemma4-Action': 'shutdown'}]:
                    req = urllib.request.Request(url + '/api/shutdown', data=b'', headers=headers)
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(req, timeout=2)
                    self.assertEqual(caught.exception.code, 403)
                    self.assertIsNone(proc.poll())
                req = urllib.request.Request(url + '/api/shutdown', data=b'',
                                             headers={'Origin': url, 'X-Gemma4-Action': 'shutdown'})
                with urllib.request.urlopen(req, timeout=2) as response:
                    self.assertEqual(response.status, 202)
                    self.assertEqual(json.load(response)['status'], 'stopping')
                self.assertEqual(proc.wait(timeout=8), 0)
                self.assertEqual(marker.read_text(), 'closed')
                for closed_port in [port, api_port]:
                    with socket.socket() as sock:
                        self.assertNotEqual(sock.connect_ex(('127.0.0.1', closed_port)), 0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
                proc.stdout.close()


if __name__ == '__main__':
    unittest.main()
