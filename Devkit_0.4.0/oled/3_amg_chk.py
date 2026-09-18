#!/usr/bin/env python3
"""
AMG8833 8x8サーマルセンサー サンプル (I2Cアドレス 0x68)
ターミナルにASCIIヒートマップとして表示 + 簡易人検知
"""
import time
import smbus2

AMG_ADDR = 0x68
I2C_BUS = 1  # /dev/i2c-1 の場合

# レジスタ定義（AMG88xxデータシート準拠）
REG_PCTL = 0x00   # Power Control
REG_RST = 0x01    # Reset
REG_FPSC = 0x02   # Frame Rate
REG_TTHL = 0x0E   # Thermistor温度
PIXEL_BASE = 0x80  # 画素データ開始アドレス（0x80〜0xFF, 128byte）

PRESENCE_THRESHOLD = 3.0  # 環境温度からの差(℃) これを超えたら「検知」とみなす


class AMG8833:
    def __init__(self, bus=I2C_BUS, address=AMG_ADDR):
        self.bus = smbus2.SMBus(bus)
        self.address = address
        self._init_sensor()

    def _write_byte(self, reg, value):
        self.bus.write_byte_data(self.address, reg, value)

    def _init_sensor(self):
        self._write_byte(REG_PCTL, 0x00)   # ノーマルモード
        time.sleep(0.05)
        self._write_byte(REG_RST, 0x3F)    # 初期リセット
        time.sleep(0.05)
        self._write_byte(REG_FPSC, 0x00)   # 10fps
        time.sleep(0.05)

    def read_thermistor(self):
        """センサー基板自体の温度(参考値)"""
        data = self.bus.read_i2c_block_data(self.address, REG_TTHL, 2)
        raw = data[0] | (data[1] << 8)
        if raw & 0x800:
            raw = -(raw & 0x7FF)
        else:
            raw = raw & 0x7FF
        return raw * 0.0625

    def read_pixels(self):
        """
        8x8=64画素の温度(℃)を1次元リスト(長さ64)で返す。
        SMBusの1回読み出し上限32byteのため、32byte x 4回に分割して読む。
        """
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
            # 12bit, bit11が符号
            if raw & 0x800:
                raw = -((raw ^ 0xFFF) + 1)
            temp = raw * 0.25  # 画素は0.25℃刻み
            pixels.append(temp)
        return pixels


def render_ascii_heatmap(pixels):
    """8x8温度データをANSIカラーでターミナルに描画"""
    chars = " .:-=+*#%@"
    tmin, tmax = min(pixels), max(pixels)
    span = max(tmax - tmin, 0.1)

    lines = []
    for row in range(8):
        line = ""
        for col in range(8):
            t = pixels[row * 8 + col]
            ratio = (t - tmin) / span
            idx = int(ratio * (len(chars) - 1))
            # 青(低温)→赤(高温)の簡易グラデーション
            r = int(255 * ratio)
            b = int(255 * (1 - ratio))
            line += f"\033[38;2;{r};0;{b}m{chars[idx]}{chars[idx]}\033[0m"
        lines.append(line)
    return "\n".join(lines)


def main():
    sensor = AMG8833()
    print("AMG8833 起動完了。Ctrl+Cで終了します。\n")
    time.sleep(1)

    # 起動直後の数フレームを環境温度のベースラインとして使う
    baseline_samples = []
    for _ in range(5):
        pixels = sensor.read_pixels()
        baseline_samples.append(sum(pixels) / len(pixels))
        time.sleep(0.2)
    ambient = sum(baseline_samples) / len(baseline_samples)
    print(f"環境温度ベースライン: {ambient:.1f}℃\n")

    try:
        while True:
            pixels = sensor.read_pixels()
            tmax = max(pixels)
            tavg = sum(pixels) / len(pixels)
            thermistor = sensor.read_thermistor()

            # 画面クリア
            print("\033[H\033[J", end="")
            print(f"基板温度: {thermistor:.1f}℃ | 画素平均: {tavg:.1f}℃ | 画素最大: {tmax:.1f}℃")
            print(render_ascii_heatmap(pixels))

            detected = (tmax - ambient) > PRESENCE_THRESHOLD
            status = "🔴 検知 (Person Detected)" if detected else "🟢 クリア (Clear)"
            print(f"\n状態: {status}  (閾値差: {tmax - ambient:+.1f}℃ / 閾値: {PRESENCE_THRESHOLD}℃)")

            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n終了します")


if __name__ == "__main__":
    main()
