#!/usr/bin/env python3
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from luma.core.render import canvas
from PIL import ImageFont
import time

# I2Cバス番号とアドレスは環境に合わせて変更
serial = i2c(port=1, address=0x3C)
device = ssd1306(serial, width=128, height=32)

font = ImageFont.load_default()

def show_status(line1, line2=""):
    with canvas(device) as draw:
        draw.text((0, 0), line1, font=font, fill="white")
        if line2:
            draw.text((0, 16), line2, font=font, fill="white")

if __name__ == "__main__":
    # 動作確認
    show_status("System OK", "CH1-4: Running")
    time.sleep(5)
