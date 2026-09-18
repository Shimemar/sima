#!/usr/bin/env python3
"""
AMG8833 (0x68) + OLED SSD1306 (128x32, 0x3C) 統合サンプル
人検知結果をOLEDに状態表示する
"""
import time
import smbus2

from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306
from PIL import ImageFont

# ==== 設定 ====
I2C_BUS = 1
AMG_ADDR = 0x68
OLED_ADDR = 0x3C

PRESENCE_THRESHOLD = 3.0   # 環境温度からの差(℃) これを超えたら「検知」
UPDATE_INTERVAL = 0.3      # センサー読み取り間隔(秒)
OLED_REFRESH_EVERY = 5     # 何サイクルごとにOLED更新するか（負荷軽減用、通常は1でOK）

# AMG8833レジスタ
REG_PCTL = 0x00
REG_RST = 0x01
REG_FPSC = 0x02
REG_TTHL = 0x0E
PIXEL_BASE = 0x80


class AMG8833:
    def __init__(self, bus=I2C_BUS, address=AMG_ADDR):
        self.bus = smbus2.SMBus(bus)
        self.address = address
        self._init_sensor()

    def _write_byte(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def _init_sensor(self):
        self._write_byte(REG_PCTL, 0x00)
        time.sleep(0.05)
        self._write_byte(REG_RST, 0x3F)
        time.sleep(0.05)
        self._write_byte(REG_FPSC, 0x00)
        time.sleep(0.05)

    def read_pixels(self):
        raw_bytes = bytearray()
        for chunk in range(4):
            offset = PIXEL_BASE + (chunk * 32)
            data = self.bus.read_i2c_block_data(self.address, offset, 32)
            raw_bytes.extend(data)

        pixels = []
        for i in range(64):
            lo = raw_bytes[i * 2]
            hi = raw_bytes[i * 2 + 1]
            raw = lo | (hi << 8)
            if raw & 0x800:
                raw = -((raw ^ 0xFFF) + 1)
            pixels.append(raw * 0.25)
        return pixels


class StatusDisplay:
    def __init__(self, port=I2C_BUS, address=OLED_ADDR):
        serial = i2c(port=port, address=address)
        self.device = ssd1306(serial, width=128, height=32)
        self.font = ImageFont.load_default()

    def show(self, detected, tmax, tavg, ambient):
        with canvas(self.device) as draw:
            # 1行目: 状態（大きめに見せるため太字風に2重描画）
            status_text = "DETECTED" if detected else "CLEAR"
            draw.text((0, 0), f"STATUS: {status_text}", font=self.font, fill="white")

            # 2行目: 最大温度と差分
            diff = tmax - ambient
            draw.text((0, 12), f"Max:{tmax:.1f}C Diff:{diff:+.1f}C", font=self.font, fill="white")

            # 3行目: 平均温度とベースライン
            draw.text((0, 22), f"Avg:{tavg:.1f}C Base:{ambient:.1f}C", font=self.font, fill="white")

    def clear(self):
        self.device.clear()


def calibrate_baseline(sensor, samples=5, interval=0.2):
    """起動時の環境温度ベースラインを取得"""
    readings = []
    for _ in range(samples):
        pixels = sensor.read_pixels()
        readings.append(sum(pixels) / len(pixels))
        time.sleep(interval)
    return sum(readings) / len(readings)


def main():
    print("初期化中...")
    sensor = AMG8833()
    display = StatusDisplay()

    print("環境温度を計測中...")
    ambient = calibrate_baseline(sensor)
    print(f"環境温度ベースライン: {ambient:.1f}C\n")

    cycle = 0
    try:
        while True:
            pixels = sensor.read_pixels()
            tmax = max(pixels)
            tavg = sum(pixels) / len(pixels)
            detected = (tmax - ambient) > PRESENCE_THRESHOLD

            # ターミナルにも簡易ログ
            status = "DETECTED" if detected else "clear"
            print(f"[{time.strftime('%H:%M:%S')}] {status:9s} max={tmax:5.1f}C avg={tavg:5.1f}C diff={tmax-ambient:+.1f}C")

            if cycle % OLED_REFRESH_EVERY == 0:
                display.show(detected, tmax, tavg, ambient)

            cycle += 1
            time.sleep(UPDATE_INTERVAL)

    except KeyboardInterrupt:
        print("\n終了します")
        display.clear()


if __name__ == "__main__":
    main()
