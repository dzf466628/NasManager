# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""生成 ICO：所有尺寸（16~256）统一以用户源图为准缩放，比例完全一致"""
import struct
from pathlib import Path
from PIL import Image

BASE = Path(__file__).resolve().parent
src = str(BASE / '1787711107140.png')
out = str(BASE / 'app_multi.ico')

img = Image.open(src).convert('RGBA')
print('统一源图:', img.size)

sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
images = []
offset = 6 + 16 * len(sizes)
for (w, h) in sizes:
    im = img.resize((w, h), Image.LANCZOS)
    px = im.load()
    hdr = struct.pack('<IiiHHIIiiII', 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
    pixel = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = px[x, y]
            pixel += bytes((b, g, r, a))
    and_stride = ((w + 31) // 32) * 4
    and_mask = bytes(and_stride * h)
    dib = hdr + bytes(pixel) + and_mask
    images.append((w, h, dib, offset))
    offset += len(dib)

with open(out, 'wb') as f:
    f.write(struct.pack('<HHH', 0, 1, len(images)))
    for (w, h, dib, off) in images:
        f.write(struct.pack('<BBBBHHII',
                            w if w < 256 else 0, h if h < 256 else 0,
                            0, 0, 1, 32, len(dib), off))
    for (w, h, dib, off) in images:
        f.write(dib)
print('ICO 统一生成:', out, len(images), 'images')

# 同步运行资源（标题栏/任务栏）
import shutil
shutil.copyfile(out, str(BASE / 'assets' / 'app.ico'))
print('runtime icon updated')

from PyInstaller.utils.win32 import icon
fi = icon.IconFile(out)
print('IconFile images:', len(fi.images))
