#!/usr/bin/env bash
# Display Insight's video and metadata using its existing WebRTC viewer.
set -euo pipefail
channel="${1:-0}"
host="${2:-127.0.0.1}"
if [[ ! "$channel" =~ ^[0-9]+$ ]] || (( 10#$channel > 47 )); then
    echo 'チャネルは0〜47で指定してください（現在のInsight設定）。' >&2
    exit 1
fi
if [[ ! "$host" =~ ^[a-zA-Z0-9.-]+$ ]]; then
    echo 'InsightホストのIPv4アドレスまたはホスト名を指定してください。' >&2
    exit 1
fi
url="https://${host}:8081/static/viewer.html?mode=light&src=${channel}&max_channels=48"
for browser in google-chrome chromium chromium-browser; do
    if command -v "$browser" >/dev/null 2>&1; then
        exec "$browser" --app="$url"
    fi
done
exec xdg-open "$url"
