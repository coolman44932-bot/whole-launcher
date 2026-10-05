"""Build a modded TOC + one extra sdfdata holding every modded file.

The original archives are never touched: modded files are written into a free
part slot and their TOC entries are pointed at it. Uninstalling is restoring the
original sdf.sdftoc and deleting the extra part. What goes in comes from
mods.merge(); see mods.py for the mod layout.
"""
import copy
import struct
import zlib

from . import sdf


def compress_pages(data, level=9):
    pages, blob = [], bytearray()
    for i in range(0, max(len(data), 1), sdf.PAGE):
        page = data[i:i + sdf.PAGE]
        z = zlib.compress(page, level)
        if len(z) < len(page):
            blob += z
            pages.append(len(z))
        else:
            blob += page
            pages.append(len(page) & 0xffff)   # a raw full page is written as 0
    return bytes(blob), pages


def split_dds(data):
    if data[:4] != b'DDS ':
        raise ValueError('not a DDS file')
    hlen = 0x94 if data[84:88] == b'DX10' else 0x80
    return data[:hlen], data[hlen:]


def build(toc, files, part, log=print):
    """files: {game path: bytes}. Returns (toc bytes, part bytes)."""
    if not files:
        raise ValueError('nothing to pack')
    entries = {e.name: e for e in toc.entries}
    dds_headers = list(toc.dds_headers)
    blob = bytearray(b'BERG' + struct.pack('<I', 0x16) + toc.part_ids[next(
        i for i, s in enumerate(toc.part_sizes) if s)])
    for name in sorted(files):
        data = files[name]
        old = entries.get(name)
        e = copy.deepcopy(old) if old else sdf.Entry(name)
        if name.endswith('.dds'):
            header, data = split_dds(data)
            dds_headers.append(struct.pack('<I', len(header)) + header.ljust(sdf.DDS_SIZE - 4, b'\0'))
            e.dds = len(dds_headers) - 1
        # keep the original chunk split when the payload size is unchanged (texture
        # mip streaming relies on it); otherwise store it as one chunk
        sizes = [c.size for c in e.chunks] if old and old.size == len(data) else [len(data)]
        olds = e.chunks if len(sizes) == len(e.chunks) else []
        e.chunks = []
        pos = 0
        for i, n in enumerate(sizes):
            comp, pages = compress_pages(data[pos:pos + n])
            pos += n
            e.chunks.append(sdf.Chunk(
                size=n, csize=len(comp), offset=len(blob), part=part,
                pages=pages if len(pages) > 1 else [], fid=olds[i].fid if olds else 0))
            blob += comp
        if len(sizes) == 1 and old and len(old.chunks) > 1:
            log('warn %s: size changed, %d chunks merged into one' % (name, len(old.chunks)))
        entries[name] = e
        log('%s %-62s %9d bytes' % ('repl' if old else 'add ', name, len(data)))

    out = copy.copy(toc)
    out.dds_headers = dds_headers
    out.part_sizes = list(toc.part_sizes)
    out.part_ids = list(toc.part_ids)
    out.part_sizes[part] = len(blob)
    out.part_ids[part] = bytes(blob[8:8 + sdf.ID_SIZE])
    return out.serialize(list(entries.values())), bytes(blob)
