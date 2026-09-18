#!/usr/bin/env python3
"""
Modalix DevKit - OLED (SSD1306 128x32) にIPアドレスを表示
"""
import socket
import subprocess
import time

from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont

# ==== 設定 ====
I2C_PORT = 1        # /dev/i2c-1 の場合は 1
I2C_ADDRESS = 0x3C  # i2cdetectで確認したアドレス
UPDATE_INTERVAL = 5  # 秒。IPアドレスの再取得・再描画間隔


def get_ip_addresses():
    """
    ネットワークインターフェースごとのIPv4アドレスを取得
    戻り値: [(interface, ip), ...] のリスト
    """
    result = []
    try:
        # ip addr show の出力を解析（Ubuntu標準）
        output = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"],
            encoding="utf-8"
        )
        for line in output.splitlines():
            parts = line.split()
            iface = parts[1]
            ip = parts[3].split("/")[0]
            if iface == "lo":
                continue  # ループバックは除外
            result.append((iface, ip))
    except Exception as e:
        result.append(("error", str(e)))

    if not result:
        result.append(("N/A", "No IP"))

    return result


def get_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def main():
    serial = i2c(port=I2C_PORT, address=I2C_ADDRESS)
    device = ssd1306(serial, width=128, height=32)
    font = ImageFont.load_default()

    hostname = get_hostname()
    last_ips = None
    display_index = 0

    try:
        while True:
            ips = get_ip_addresses()

            # 表示するIPを一定間隔でローテーション（複数インターフェースがある場合）
            iface, ip = ips[display_index % len(ips)]
            display_index += 1

            with canvas(device) as draw:
                draw.text((0, 0), f"Host: {hostname}", font=font, fill="white")
                draw.text((0, 12), f"{iface}:", font=font, fill="white")
                draw.text((0, 22), f"{ip}", font=font, fill="white")

            if ips != last_ips:
                print(f"[{time.strftime('%H:%M:%S')}] IP一覧: {ips}")
                last_ips = ips

            time.sleep(UPDATE_INTERVAL)

    except KeyboardInterrupt:
        print("\n終了します")
        device.clear()


if __name__ == "__main__":
    main()
