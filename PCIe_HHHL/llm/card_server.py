#!/usr/bin/env python3
"""Run on Modalix using its installed pyneat environment."""
import argparse
import gc
from pathlib import Path
import signal
import socket
import sys
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, default=Path('/workspace/neat/models_genai'))
    parser.add_argument('--host', default='10.0.0.2')
    parser.add_argument('--port', type=int, default=9998)
    parser.add_argument('--watch-stdin', action='store_true')
    args = parser.parse_args()
    import pyneat

    paths = [('gemma4', args.models / 'gemma-4-E2B-it-GPTQ-a16w4'),
             ('whisper', args.models / 'whisper-small-a16w8')]
    for _, path in paths:
        if not (path / 'devkit').is_dir() or not (path / 'elf_files').is_dir():
            raise RuntimeError(f'Compiled model not found: {path}')
    with socket.socket() as probe:
        probe.bind((args.host, args.port))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    if args.watch_stdin:
        def watch():
            sys.stdin.buffer.read()
            stop.set()
        threading.Thread(target=watch, daemon=True).start()
    options = pyneat.GenAIServerOptions()
    options.host, options.port = args.host, args.port
    server = pyneat.GenAIServer(options)
    try:
        for name, path in paths:
            if stop.is_set():
                return
            print(f'Loading {name}: {path}', flush=True)
            server.add_model(str(path), name)
        if not stop.is_set():
            server.start()
            print(f'READY http://{args.host}:{args.port} {server.model_names()}', flush=True)
            stop.wait()
    finally:
        server.stop()
        del server
        gc.collect()


if __name__ == '__main__':
    main()
