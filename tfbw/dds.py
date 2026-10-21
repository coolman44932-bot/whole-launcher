"""Write DXT5 (BC3) .dds textures, the simplest compressed format the game's UI uses.

Item icons are found by name, not by the .mitem's myIcon: the shop and artifact screens load
camel/baked/assets/ui/icons/items/icon_item_<item name, lower case>.dds. The game's own icons
are BC7, which needs a heavy encoder; a few UI textures are DXT5 (for example
ui/icons/save_load/loading_center_bg.dds), so the engine takes it for UI. The header below
copies that file's header field for field (no mipmaps).

Pure Python (stdlib); write_dxt5 takes a Pillow image (dev time only: mods ship the .dds).
"""
import struct

_FLAGS = 0x81007            # CAPS | HEIGHT | WIDTH | PIXELFORMAT | LINEARSIZE, as the game's DXT5 UI files
_CAPS = (0x1000, 0xFE000000, 0, 0)


def header(width, height):
    h = struct.pack('<4sIIIIIII', b'DDS ', 124, _FLAGS, height, width,
                    max(1, width // 4) * max(1, height // 4) * 16, 0, 0)
    h += b'\0' * 44                                              # reserved
    h += struct.pack('<II4s5I', 32, 4, b'DXT5', 0, 0, 0, 0, 0)   # pixel format
    h += struct.pack('<4I', *_CAPS) + b'\0' * 4
    assert len(h) == 128
    return h


def _to565(c):
    r, g, b = (max(0, min(255, int(round(v)))) for v in c)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _from565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _principal_axis(px, mean):
    """dominant direction of the block's colours (power iteration on the covariance)"""
    cov = [[0.0] * 3 for _ in range(3)]
    for p in px:
        d = [p[i] - mean[i] for i in range(3)]
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    v = [1.0, 1.0, 1.0]
    for _ in range(8):
        v = [sum(cov[i][j] * v[j] for j in range(3)) for i in range(3)]
        n = sum(x * x for x in v) ** .5
        if n < 1e-9:
            return [0.57735] * 3
        v = [x / n for x in v]
    return v


def _color_block(px):
    """px: 16 (r, g, b) -> 8 bytes of BC1 colour (4-colour mode)"""
    mean = [sum(p[i] for p in px) / 16 for i in range(3)]
    axis = _principal_axis(px, mean)
    proj = [sum((p[i] - mean[i]) * axis[i] for i in range(3)) for p in px]
    hi = [mean[i] + axis[i] * max(proj) for i in range(3)]
    lo = [mean[i] + axis[i] * min(proj) for i in range(3)]
    c0, c1 = _to565(hi), _to565(lo)
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:
        return struct.pack('<HHI', c0, c1, 0)
    e0, e1 = _from565(c0), _from565(c1)
    pal = [e0, e1,
           tuple((2 * a + b) / 3 for a, b in zip(e0, e1)),
           tuple((a + 2 * b) / 3 for a, b in zip(e0, e1))]
    bits = 0
    for i, p in enumerate(px):
        best = min(range(4), key=lambda k: sum((p[c] - pal[k][c]) ** 2 for c in range(3)))
        bits |= best << (2 * i)
    return struct.pack('<HHI', c0, c1, bits)


def _alpha_block(a):
    """a: 16 alphas -> 8 bytes of BC3 alpha (8-value mode)"""
    a0, a1 = max(a), min(a)
    if a0 == a1:
        return struct.pack('<BB', a0, a1) + b'\0' * 6
    pal = [a0, a1] + [((7 - i) * a0 + i * a1) / 7 for i in range(1, 7)]
    bits = 0
    for i, v in enumerate(a):
        best = min(range(8), key=lambda k: abs(v - pal[k]))
        bits |= best << (3 * i)
    return struct.pack('<BB', a0, a1) + bits.to_bytes(6, 'little')


def encode_dxt5(pixels, width, height):
    """pixels: flat list of (r, g, b, a), row-major; width/height multiples of 4"""
    assert width % 4 == 0 and height % 4 == 0, 'size must be a multiple of 4'
    out = bytearray()
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            blk = [pixels[(by + y) * width + bx + x] for y in range(4) for x in range(4)]
            out += _alpha_block([p[3] for p in blk])
            # fully transparent pixels shouldn't pull the colour endpoints around
            opaque = [p[:3] for p in blk if p[3] > 8]
            fill = tuple(sum(p[i] for p in opaque) / len(opaque) for i in range(3)) if opaque else (0, 0, 0)
            out += _color_block([p[:3] if p[3] > 8 else fill for p in blk])
    return bytes(out)


def encode_dxt1(pixels, width, height):
    """pixels: flat list of (r, g, b[, a]) row-major (opaque); width/height >= 1. Blocks that
    stick out past a small mip level repeat its edge pixels, as BC1 needs whole 4x4 blocks."""
    out = bytearray()
    for by in range(0, max(height, 4), 4):
        for bx in range(0, max(width, 4), 4):
            blk = [pixels[min(by + y, height - 1) * width + min(bx + x, width - 1)][:3]
                   for y in range(4) for x in range(4)]
            out += _color_block(blk)
    return bytes(out)


def mip_sizes(width, height, block_bytes, count):
    """byte size of each mip level of a block-compressed texture"""
    sizes = []
    for _ in range(count):
        sizes.append(max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_bytes)
        width, height = max(1, width // 2), max(1, height // 2)
    return sizes


def write_dxt5(image, path):
    """image: a Pillow image; writes a complete .dds file, returns its size"""
    img = image.convert('RGBA')
    w, h = img.size
    data = header(w, h) + encode_dxt5(list(img.getdata()), w, h)
    with open(path, 'wb') as f:
        f.write(data)
    return len(data)


def decode_dxt1(data, width, height):
    """for checking: DXT1 block data -> flat list of (r, g, b, a)"""
    out = [None] * (width * height)
    p = 0
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            c0, c1, cbits = struct.unpack_from('<HHI', data, p)
            e0, e1 = _from565(c0), _from565(c1)
            if c0 > c1:
                cpal = [e0 + (255,), e1 + (255,), tuple((2 * a + b) // 3 for a, b in zip(e0, e1)) + (255,),
                        tuple((a + 2 * b) // 3 for a, b in zip(e0, e1)) + (255,)]
            else:
                cpal = [e0 + (255,), e1 + (255,), tuple((a + b) // 2 for a, b in zip(e0, e1)) + (255,), (0, 0, 0, 0)]
            for i in range(16):
                out[(by + i // 4) * width + bx + i % 4] = cpal[(cbits >> (2 * i)) & 3]
            p += 8
    return out


def decode_dxt5(data, width, height):
    """for checking: DXT5 block data -> flat list of (r, g, b, a)"""
    out = [None] * (width * height)
    p = 0
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            a0, a1 = data[p], data[p + 1]
            abits = int.from_bytes(data[p + 2:p + 8], 'little')
            if a0 > a1:
                apal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                apal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
            c0, c1, cbits = struct.unpack_from('<HHI', data, p + 8)
            e0, e1 = _from565(c0), _from565(c1)
            cpal = [e0, e1, tuple((2 * a + b) // 3 for a, b in zip(e0, e1)),
                    tuple((a + 2 * b) // 3 for a, b in zip(e0, e1))]
            for i in range(16):
                out[(by + i // 4) * width + bx + i % 4] = cpal[(cbits >> (2 * i)) & 3] + (apal[(abits >> (3 * i)) & 7],)
            p += 16
    return out
