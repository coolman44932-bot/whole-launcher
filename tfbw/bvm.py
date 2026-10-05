"""Snowdrop BVM: the binary format of node graphs and most other "m*" data
(.mskillscriptgraph, .mscript, .mgraphobject, .mcompoundnode, .muigraph,
.manimevent, .mvar, .seqcam, .mparticles, ...).

  '\\xffBVM'  uvar count  { uvar len, bytes }[count]   string table
  value                                                 the root, usually a table

Integers are variable length. The top 2 bits of the first byte give the number of
extra bytes (0, 1, 3 or 4), the low 6 bits are the lowest bits, and extra bytes
follow little-endian. Values are two's complement over the total bit width;
lengths, counts and indices are unsigned.

Value tags (decoded into plain Python so a graph can be edited as JSON):
  00 nil  01 table(uvar nArray, uvar nHash, array values, key/value pairs)
  02 true 03 false 04 int 06 float32 09 float32x3 07 float32x4
  0b float32x2   08 int4 0a int3 0c int2   (tuples of signed ints)
  0d string   0e path string   0f uid(16 bytes) 10 guid(16 bytes) 11 id64(8 bytes)
  05 raw64 (8 bytes)
Anything else raises, naming the offset.
"""
import struct

EXTRA = (0, 1, 3, 4)


class Tagged:
    """A value whose tag can't be told from its Python type (paths, ids, int tuples)."""
    __slots__ = ('tag', 'value')
    NAMES = {0x08: 'int4', 0x0a: 'int3', 0x0b: 'float2', 0x0c: 'int2', 0x0e: 'path',
             0x0f: 'uid', 0x10: 'guid', 0x11: 'id64', 0x05: 'raw64', 0x09: 'float3', 0x07: 'float4'}

    def __init__(self, tag, value):
        self.tag, self.value = tag, value

    def __eq__(self, o):
        return isinstance(o, Tagged) and (self.tag, self.value) == (o.tag, o.value)

    def __hash__(self):
        return hash((self.tag, self.value))

    def __repr__(self):
        return '%s(%r)' % (self.NAMES.get(self.tag, hex(self.tag)), self.value)


class Float(float):
    """float32 (tag 06); kept distinct from ints so 1.0 survives a roundtrip."""


class Table:
    """Lua-style table: an array part plus ordered key/value pairs."""
    __slots__ = ('array', 'items')

    def __init__(self, array=None, items=None):
        self.array = array if array is not None else []
        self.items = items if items is not None else []

    def get(self, key, default=None):
        for k, v in self.items:
            if k == key:
                return v
        return default

    def set(self, key, value):
        for i, (k, _) in enumerate(self.items):
            if k == key:
                self.items[i] = (k, value)
                return
        self.items.append((key, value))

    def __repr__(self):
        return 'Table(%d array, %d items)' % (len(self.array), len(self.items))


# ----------------------------------------------------------------- reading
class _Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def raw(self, n):
        b = self.d[self.p:self.p + n]
        if len(b) != n:
            raise ValueError('unexpected end of data at 0x%x' % self.p)
        self.p += n
        return b

    def uvar(self):
        b = self.d[self.p]
        n = EXTRA[b >> 6]
        v = b & 0x3f
        for i in range(n):
            v |= self.d[self.p + 1 + i] << (6 + 8 * i)
        self.p += 1 + n
        return v, 6 + 8 * n

    def svar(self):
        v, bits = self.uvar()
        return v - (1 << bits) if v >> (bits - 1) else v

    def value(self, strs):
        at = self.p
        t = self.d[self.p]
        self.p += 1
        if t == 0x00:
            return None
        if t == 0x01:
            na, _ = self.uvar()
            nh, _ = self.uvar()
            arr = [self.value(strs) for _ in range(na)]
            items = []
            for _ in range(nh):
                k = self.value(strs)
                items.append((k, self.value(strs)))
            return Table(arr, items)
        if t == 0x02:
            return True
        if t == 0x03:
            return False
        if t == 0x04:
            return self.svar()
        if t == 0x06:
            return Float(struct.unpack('<f', self.raw(4))[0])
        if t in (0x0b, 0x09, 0x07):
            n = {0x0b: 2, 0x09: 3, 0x07: 4}[t]
            return Tagged(t, struct.unpack('<%df' % n, self.raw(4 * n)))
        if t in (0x08, 0x0a, 0x0c):
            n = {0x08: 4, 0x0a: 3, 0x0c: 2}[t]
            return Tagged(t, tuple(self.svar() for _ in range(n)))
        if t == 0x0d:
            return strs[self.uvar()[0]]
        if t == 0x0e:
            return Tagged(t, strs[self.uvar()[0]])
        if t in (0x0f, 0x10):
            return Tagged(t, self.raw(16).hex())
        if t in (0x05, 0x11):
            return Tagged(t, self.raw(8).hex())
        raise ValueError('unknown BVM tag 0x%02x at 0x%x' % (t, at))


def loads(data):
    """-> (root value, trailing bytes)"""
    if data[:4] != b'\xffBVM':
        raise ValueError('not a BVM file')
    r = _Reader(data)
    r.p = 4
    n, _ = r.uvar()
    strs = []
    for _ in range(n):
        ln, _ = r.uvar()
        strs.append(r.raw(ln).decode('utf-8', 'surrogateescape'))
    root = r.value(strs)
    return root, data[r.p:]


# ----------------------------------------------------------------- writing
def _uvar(v):
    for n, extra in enumerate(EXTRA):
        bits = 6 + 8 * extra
        if v < (1 << bits):
            return bytes([(n << 6) | (v & 0x3f)]) + (v >> 6).to_bytes(extra, 'little')
    raise ValueError('integer too large: %d' % v)


def _svar(v):
    for n, extra in enumerate(EXTRA):
        bits = 6 + 8 * extra
        if -(1 << (bits - 1)) <= v < (1 << (bits - 1)):
            u = v & ((1 << bits) - 1)
            return bytes([(n << 6) | (u & 0x3f)]) + (u >> 6).to_bytes(extra, 'little')
    raise ValueError('integer too large: %d' % v)


class _Writer:
    def __init__(self):
        self.strs, self.index = [], {}
        self.out = bytearray()

    def sref(self, s):
        i = self.index.get(s)
        if i is None:
            i = self.index[s] = len(self.strs)
            self.strs.append(s)
        return _uvar(i)

    def value(self, v):
        o = self.out
        if v is None:
            o.append(0x00)
        elif v is True:
            o.append(0x02)
        elif v is False:
            o.append(0x03)
        elif isinstance(v, Float):
            o.append(0x06); o += struct.pack('<f', v)
        elif isinstance(v, int):
            o.append(0x04); o += _svar(v)
        elif isinstance(v, float):
            o.append(0x06); o += struct.pack('<f', v)
        elif isinstance(v, str):
            o.append(0x0d); o += self.sref(v)
        elif isinstance(v, Table):
            o.append(0x01); o += _uvar(len(v.array)) + _uvar(len(v.items))
            for x in v.array:
                self.value(x)
            for k, x in v.items:
                self.value(k)
                self.value(x)
        elif isinstance(v, Tagged):
            t = v.tag
            o.append(t)
            if t in (0x0b, 0x09, 0x07):
                o += struct.pack('<%df' % len(v.value), *v.value)
            elif t in (0x08, 0x0a, 0x0c):
                for x in v.value:
                    o += _svar(x)
            elif t == 0x0e:
                o += self.sref(v.value)
            elif t in (0x05, 0x0f, 0x10, 0x11):
                o += bytes.fromhex(v.value)
            else:
                raise ValueError('cannot write tag 0x%02x' % t)
        else:
            raise TypeError('cannot write %r' % type(v))


# ------------------------------------------------------------------- JSON
# Lossless JSON form for editing. Tables become objects ("$array" for the array
# part, "$items": [[key, value], ...] when keys aren't all plain strings);
# tagged values become {"$path": "..."}, {"$int2": [x, y]}, {"$uid": "hex"}, ...
_BY_NAME = {v: k for k, v in Tagged.NAMES.items()}


def to_json(v):
    if isinstance(v, Table):
        out = {}
        if v.array:
            out['$array'] = [to_json(x) for x in v.array]
        keys = [k for k, _ in v.items]
        if all(isinstance(k, str) and not k.startswith('$') for k in keys) and len(set(keys)) == len(keys):
            for k, x in v.items:
                out[k] = to_json(x)
        elif v.items:
            out['$items'] = [[to_json(k), to_json(x)] for k, x in v.items]
        return out
    if isinstance(v, Tagged):
        return {'$' + Tagged.NAMES[v.tag]: list(v.value) if isinstance(v.value, tuple) else v.value}
    if isinstance(v, Float):
        return float(v)
    return v


def from_json(j):
    if isinstance(j, dict):
        if len(j) == 1:
            (k, x), = j.items()
            if k.startswith('$') and k[1:] in _BY_NAME:
                return Tagged(_BY_NAME[k[1:]], tuple(x) if isinstance(x, list) else x)
        t = Table()
        for k, x in j.items():
            if k == '$array':
                t.array = [from_json(y) for y in x]
            elif k == '$items':
                t.items += [(from_json(a), from_json(b)) for a, b in x]
            else:
                t.items.append((k, from_json(x)))
        return t
    if isinstance(j, float):
        return Float(j)
    if isinstance(j, list):
        raise ValueError('bare JSON list; tables use {"$array": [...]}')
    return j


def dumps(root, trailing=b''):
    w = _Writer()
    w.value(root)
    head = bytearray(b'\xffBVM') + _uvar(len(w.strs))
    for s in w.strs:
        b = s.encode('utf-8', 'surrogateescape')
        head += _uvar(len(b)) + b
    return bytes(head + w.out) + trailing
