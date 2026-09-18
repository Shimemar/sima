#!/usr/bin/env python3
"""Start the card worker over SSH and serve the local browser UI."""
import argparse
import errno
import json
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from server import Server

ROOT = Path(__file__).resolve().parent


def bind_ui(port, upstream):
    """Reserve the actual server socket, including during model loading."""
    candidates = [port] if port is not None else range(8080, 8101)
    for candidate in candidates:
        try:
            return Server(('127.0.0.1', candidate), upstream)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            if port is not None:
                raise RuntimeError(f'ポート {port} は使用中です。--port を省略すると空きポートを自動選択します。') from exc
    raise RuntimeError('8080〜8100番ポートは使用中です。--port で別のポートを指定してください。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--card', default='10.0.0.2')
    parser.add_argument('--user', default='sima')
    parser.add_argument('--card-python', default='/home/sima/pyneat/bin/python')
    parser.add_argument('--card-workspace', default='/workspace')
    parser.add_argument('--port', type=int, help='UI port (default: first available port from 8080 to 8100)')
    parser.add_argument('--api-port', type=int, default=9998)
    parser.add_argument('--startup-timeout', type=int, default=240)
    parser.add_argument('--external-server', action='store_true', help='Use an already running card server')
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    upstream = f'http://{args.card}:{args.api_port}'
    remote = ui = log = None
    ui_thread = None
    try:
        ui = bind_ui(args.port, upstream)
        ui.on_shutdown = stop.set
        port = ui.server_port
        if args.port is None and port != 8080:
            print(f'8080番ポートは使用中のため、{port}番ポートを使用します。', flush=True)
        if not args.external_server:
            try:
                connection = socket.create_connection((args.card, args.api_port), timeout=2)
            except OSError:
                pass
            else:
                connection.close()
                raise RuntimeError('カードのAPIポートは使用中です。既存サーバーを使う場合は --external-server を指定してください。')
            log = (ROOT / 'card.log').open('w')
            command = shlex.join([args.card_python, '-u', args.card_workspace + '/llm/card_server.py',
                                  '--models', args.card_workspace + '/neat/models_genai',
                                  '--host', args.card, '--port', str(args.api_port), '--watch-stdin'])
            remote = subprocess.Popen(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                                       '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3',
                                       f'{args.user}@{args.card}', 'exec ' + command],
                                      stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            print(f'モデルをロードしています。ログ: {ROOT / "card.log"}', flush=True)
        deadline = time.monotonic() + args.startup_timeout
        while not stop.is_set():
            if remote and remote.poll() is not None:
                raise RuntimeError('カード側プロセスが終了しました。card.log を確認してください。')
            try:
                with urllib.request.urlopen(upstream + '/v1/models', timeout=2) as response:
                    names = {m['id'] for m in json.load(response)['data']}
                if {'gemma4', 'whisper'}.issubset(names):
                    # Allow a failed bind in the new worker to surface rather than attach to an old server.
                    if remote and stop.wait(.5):
                        break
                    if remote and remote.poll() is not None:
                        raise RuntimeError('カード側サーバーを起動できません。card.log を確認してください。')
                    break
            except (OSError, ValueError, KeyError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('モデル起動がタイムアウトしました。card.log を確認してください。')
            stop.wait(1)
        if stop.is_set():
            return 0
        ui_thread = threading.Thread(target=ui.serve_forever, daemon=True)
        ui_thread.start()
        print(f'ブラウザで http://localhost:{port} を開いてください。終了: Ctrl+C', flush=True)
        while not stop.wait(.5):
            if not ui_thread.is_alive():
                raise RuntimeError('UIサーバーが終了しました。')
            if remote and remote.poll() is not None:
                raise RuntimeError('カードとの接続が切断されました。card.log を確認してください。')
        return 0
    except Exception as exc:
        print(f'エラー: {exc}', file=sys.stderr)
        return 1
    finally:
        if ui:
            if ui_thread:
                ui.shutdown()
                ui_thread.join(timeout=5)
            ui.server_close()
        if remote:
            # EOF tells the worker to stop GenAIServer and release the models.
            if remote.stdin:
                remote.stdin.close()
            try:
                remote.wait(timeout=25)
            except subprocess.TimeoutExpired:
                remote.terminate()
                try:
                    remote.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    remote.kill()
                    remote.wait()
        if log:
            log.close()


if __name__ == '__main__':
    raise SystemExit(main())
