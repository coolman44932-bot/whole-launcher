"""Snowdrop SDF archive reader/writer for South Park: The Fractured But Whole.

Layout (TOC version 0x16, "WEST"):
  header      magic 'WEST', version, decompSize, compSize, firstPart, partCount, ddsCount
  id          'massive\\0' + 32 bytes + 'ubisoft\\0'   (0x30)
  sigFlag     u8, followed by 0x140 signature bytes when nonzero
  partSizes   u32[partCount]        size of each sdf-X-NNNN.sdfdata
  partIds     id[partCount]         copy of each data file's BERG header id
  ddsHeaders  (u32 used, u8[0x94])[ddsCount]   DDS headers stripped from .dds files
  fileTable   zlib(trie)            names + where every file lives
  endId       id

File-table trie, one byte decides each node:
  0x01-0x1F   that many name characters follow
  'A'-'Z'     file entry; (ch-'A')&7 = chunk count, bit 3 = trailing extra pairs
  anything    branch on that character: u32 offset of the >= subtree, < subtree follows
"""
import bisect
import operator
import os
import struct
import zlib
from dataclasses import dataclass, field

PAGE = 0x10000
ID_SIZE = 0x30
DDS_SIZE = 0x98


def part_name(part):
    return 'sdf-%s-%04d.sdfdata' % ('ABCD'[part // 1000], part)


_U16 = struct.Struct('<H').unpack_from
_U32 = struct.Struct('<I').unpack_from


def _uint(buf, pos, n):
    return int.from_bytes(buf[pos:pos + n], 'little')


def _nbytes(v):
    n = 0
    while v:
        n += 1
        v >>= 8
    return n


def part_sizes(path):
    """the per-archive size table from an index's header, without parsing the file table"""
    with open(path, 'rb') as f:
        head = f.read(0x1c + ID_SIZE + 1)
        magic, version, dsize, csize, first, count, ddsc = struct.unpack_from('<4sIIIIII', head, 0)
        if magic != b'WEST':
            raise ValueError('not a sdftoc: %r' % magic)
        if head[-1]:
            f.seek(0x140, 1)
        return list(struct.unpack('<%dI' % count, f.read(4 * count)))


@dataclass
class Chunk:
    size: int          # decompressed size
    csize: int         # compressed size (0 = stored)
    offset: int        # offset inside the part file
    part: int          # sdfdata index
    pages: list        # per-64KB compressed sizes (only when csize and > 1 page)
    fid: int           # per-chunk id (purpose unknown, preserved)
    flags: int = 0     # original high bits of the chunk descriptor byte


@dataclass
class Entry:
    name: str
    sid: int = 0           # u32 per file, purpose unknown (preserved)
    hibits: int = 0        # upper 6 bits of the dds byte (preserved)
    dds: int = None        # index into ddsHeaders when the file is a headerless .dds
    chunks: list = field(default_factory=list)
    extra: bytes = None    # trailing (count, pairs) block when present

    @property
    def size(self):
        return sum(c.size for c in self.chunks)


class Toc:
    def __init__(self, path):
        self.path = path
        self.dir = os.path.dirname(path)
        d = open(path, 'rb').read()
        (magic, self.version, self.dsize, self.csize, self.first_part,
         self.part_count, self.dds_count) = struct.unpack_from('<4sIIIIII', d, 0)
        if magic != b'WEST':
            raise ValueError('not a sdftoc: %r' % magic)
        if self.version != 0x16:
            raise ValueError('unsupported sdftoc version 0x%x' % self.version)
        p = 0x1c
        self.id = d[p:p + ID_SIZE]; p += ID_SIZE
        self.sig_flag = d[p]; p += 1
        self.signature = b''
        if self.sig_flag:
            self.signature = d[p:p + 0x140]; p += 0x140
        self.part_sizes = list(struct.unpack_from('<%dI' % self.part_count, d, p)); p += 4 * self.part_count
        self.part_ids = [d[p + i * ID_SIZE:p + (i + 1) * ID_SIZE] for i in range(self.part_count)]
        p += ID_SIZE * self.part_count
        self.dds_headers = [d[p + i * DDS_SIZE:p + (i + 1) * DDS_SIZE] for i in range(self.dds_count)]
        p += DDS_SIZE * self.dds_count
        self.table = zlib.decompress(d[p:p + self.csize])
        if len(self.table) != self.dsize:
            raise ValueError('file table size mismatch')
        p += self.csize
        self.end_id = d[p:p + ID_SIZE]
        self.entries = parse_table(self.table)
        self._by_name = None

    # ---- lookup -------------------------------------------------------
    def by_name(self):
        if self._by_name is None:
            self._by_name = {e.name.lower(): e for e in self.entries}
        return self._by_name

    def get(self, name):
        return self.by_name().get(name.lower().replace('\\', '/'))

    # ---- reading ------------------------------------------------------
    def read(self, entry, with_dds_header=True, data_dir=None):
        data_dir = data_dir or self.dir
        out = bytearray()
        if entry.dds is not None and with_dds_header:
            h = self.dds_headers[entry.dds]
            used = struct.unpack_from('<I', h)[0]
            out += h[4:4 + used]
        handles = {}
        try:
            for c in entry.chunks:
                f = handles.get(c.part)
                if f is None:
                    f = handles[c.part] = open(os.path.join(data_dir, part_name(c.part)), 'rb')
                f.seek(c.offset)
                if not c.csize:
                    out += f.read(c.size)
                    continue
                left = c.size
                for cs in (c.pages or [c.csize]):
                    n = min(left, PAGE)
                    if cs == 0 or cs == n:
                        out += f.read(n)
                    else:
                        out += zlib.decompress(f.read(cs))
                    left -= n
        finally:
            for f in handles.values():
                f.close()
        return bytes(out)

    # ---- writing ------------------------------------------------------
    def serialize(self, entries=None, table_level=6):
        entries = self.entries if entries is None else entries
        table = build_table(entries)
        comp = zlib.compress(table, table_level)
        out = bytearray()
        out += struct.pack('<4sIIIIII', b'WEST', self.version, len(table), len(comp),
                           self.first_part, self.part_count, len(self.dds_headers))
        out += self.id
        out.append(self.sig_flag)
        out += self.signature
        out += struct.pack('<%dI' % self.part_count, *self.part_sizes)
        for i in self.part_ids:
            out += i
        for h in self.dds_headers:
            out += h
        out += comp
        out += self.end_id
        return bytes(out)


# ---------------------------------------------------------------- trie read
def parse_table(t):
    entries = []
    append = entries.append
    frombytes = int.from_bytes
    stack = [(0, '')]
    pop, push = stack.pop, stack.append
    while stack:
        pos, name = pop()
        while True:
            ch = t[pos]
            pos += 1
            if ch == 0:
                raise ValueError('bad node at 0x%x' % (pos - 1))
            if ch <= 0x1f:
                name += t[pos:pos + ch].decode('latin1')
                pos += ch
                continue
            if 0x41 <= ch <= 0x5a:
                c = ch - 0x41
                e = Entry(name)
                n = c & 7
                if n:
                    e.sid = _U32(t, pos)[0]
                    b = t[pos + 4]
                    pos += 5
                    bc = b & 3
                    e.hibits = b >> 2
                    if bc:
                        e.dds = frombytes(t[pos:pos + bc], 'little')
                        pos += bc
                    chunks = e.chunks
                    for _ in range(n):
                        d3 = t[pos]
                        pos += 1
                        sb = (d3 & 3) + 1
                        ob = (d3 >> 2) & 7
                        comp = (d3 >> 5) & 1
                        size = frombytes(t[pos:pos + sb], 'little')
                        pos += sb
                        csize = 0
                        if comp:
                            csize = frombytes(t[pos:pos + sb], 'little')
                            pos += sb
                        off = frombytes(t[pos:pos + ob], 'little')
                        pos += ob
                        part = _U16(t, pos)[0]
                        pos += 2
                        pages = []
                        if comp:
                            pc = (size + PAGE - 1) >> 16
                            if pc > 1:
                                pages = list(struct.unpack_from('<%dH' % pc, t, pos))
                                pos += 2 * pc
                        fid = _U32(t, pos)[0]
                        pos += 4
                        chunks.append(Chunk(size, csize, off, part, pages, fid, d3 & 0xc0))
                if c & 8:
                    k = t[pos]
                    e.extra = t[pos:pos + 1 + 2 * k]
                    pos += 1 + 2 * k
                append(e)
                break
            push((_U32(t, pos)[0], name))
            pos += 4
    return entries


# --------------------------------------------------------------- trie write
def _encode_entry(e):
    out = bytearray()
    c = len(e.chunks) | (8 if e.extra is not None else 0)
    out.append(0x41 + c)
    if e.chunks:
        out += struct.pack('<I', e.sid)
        bc = 0 if e.dds is None else max(1, _nbytes(e.dds))
        out.append((e.hibits << 2) | bc)
        if bc:
            out += e.dds.to_bytes(bc, 'little')
        for ch in e.chunks:
            comp = 1 if ch.csize else 0
            sb = max(1, _nbytes(ch.size), _nbytes(ch.csize))
            ob = _nbytes(ch.offset)
            out.append(ch.flags | (comp << 5) | (ob << 2) | (sb - 1))
            out += ch.size.to_bytes(sb, 'little')
            if comp:
                out += ch.csize.to_bytes(sb, 'little')
            out += ch.offset.to_bytes(ob, 'little')
            out += ch.part.to_bytes(2, 'little')
            if comp and ((ch.size + PAGE - 1) >> 16) > 1:
                for p in ch.pages:
                    out += p.to_bytes(2, 'little')
            out += struct.pack('<I', ch.fid)
    if e.extra is not None:
        out += e.extra
    return out


def build_table(entries):
    """Same trie as sdf.build_table. Names are sorted, so every branch of the trie is a
    contiguous run; runs per character are found by binary search instead of a full scan."""
    items = sorted(((e.name.encode('latin1'), e) for e in entries), key=operator.itemgetter(0))
    names = [k for k, _ in items]
    for a, b in zip(names, names[1:]):
        if a == b:
            raise ValueError('duplicate name %r' % a)
    out = bytearray()
    stack = [('node', 0, len(items), 0)]
    bl = bisect.bisect_left
    while stack:
        job = stack.pop()
        if job[0] == 'patch':
            struct.pack_into('<I', out, job[1], len(out))
            continue
        _, lo, hi, depth = job
        first, last = names[lo], names[hi - 1]
        n = 0
        lim = min(len(first), len(last)) - depth
        while n < lim and first[depth + n] == last[depth + n]:
            n += 1
        while n:
            k = min(n, 0x1f)
            out.append(k)
            out += first[depth:depth + k]
            depth += k
            n -= k
        if hi - lo == 1:
            out += _encode_entry(items[lo][1])
            continue
        # runs of equal character at `depth` (a name ending here sorts first, as -1)
        prefix = first[:depth]
        runs = []
        i = lo
        if len(names[i]) == depth:
            runs.append((-1, i))
            i += 1
        while i < hi:
            v = names[i][depth]
            runs.append((v, i))
            i = bl(names, prefix + bytes([v + 1]), i, hi) if v < 255 else hi
        c, mid = runs[max(1, len(runs) // 2)]
        if c <= 0x1f or 0x41 <= c <= 0x5a:
            raise ValueError('cannot branch on byte 0x%02x' % c)
        out.append(c)
        at = len(out)
        out += b'\0\0\0\0'
        stack.append(('node', mid, hi, depth))
        stack.append(('patch', at))
        stack.append(('node', lo, mid, depth))
    return bytes(out)
