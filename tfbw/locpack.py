"""Localization packages.

.locpackbin (what the game reads):
  u32 version (1), u32 count,
  count x { u8 guid[16], u32 variant, u16 byteLen, utf8 text[byteLen] }
  The guid is two little-endian u64s; the text form prints them high word first.

.locpack (source text form, shipped alongside):
  "1,,\\r\\n<count>,,\\r\\n" then one "<GUID32>,<variant>,<text>" line per string.
"""
import struct


def guid_to_str(g):
    a, b = struct.unpack('<QQ', g)
    return '%016X%016X' % (a, b)


def str_to_guid(s):
    return struct.pack('<QQ', int(s[:16], 16), int(s[16:32], 16))


def read_bin(data):
    ver, n = struct.unpack_from('<II', data)
    p = 8
    rows = []
    for _ in range(n):
        g = data[p:p + 16]
        var, ln = struct.unpack_from('<IH', data, p + 16)
        p += 22
        rows.append((guid_to_str(g), var, data[p:p + ln].decode('utf-8')))
        p += ln
    if p != len(data):
        raise ValueError('trailing bytes in locpackbin')
    return ver, rows


def write_bin(rows, ver=1):
    out = bytearray(struct.pack('<II', ver, len(rows)))
    for guid, var, text in rows:
        b = text.encode('utf-8')
        if len(b) > 0xffff:
            raise ValueError('string too long: %s' % guid)
        out += str_to_guid(guid) + struct.pack('<IH', var, len(b)) + b
    return bytes(out)


def write_text(rows, ver=1):
    lines = ['%d,,' % ver, '%d,,' % len(rows)]
    lines += ['%s,%d,%s' % r for r in rows]
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')


def read_text(data):
    lines = data.decode('utf-8').split('\r\n')
    ver = int(lines[0].split(',')[0])
    n = int(lines[1].split(',')[0])
    rows = []
    for ln in lines[2:2 + n]:
        guid, var, text = ln.split(',', 2)
        rows.append((guid, int(var), text))
    return ver, rows
