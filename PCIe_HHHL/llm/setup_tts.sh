#!/usr/bin/env bash
# Initial download needs network; subsequent runs can use the cached packages.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mkdir -p vendor/debs vendor/open-jtalk
for package in open-jtalk open-jtalk-mecab-naist-jdic hts-voice-nitech-jp-atr503-m001 libhtsengine1; do
  if ! compgen -G "vendor/debs/${package}_*.deb" >/dev/null; then
    (cd vendor/debs && apt-get download "$package")
  fi
  for archive in vendor/debs/"${package}"_*.deb; do
    dpkg-deb -x "$archive" vendor/open-jtalk
  done
done
printf '%s\n' 'オフライン日本語音声合成の準備ができました。'
