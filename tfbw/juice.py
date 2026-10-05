"""Path-addressed edits of Snowdrop "juice" text data (.juice, .mpower, .mitem, ...).

    CombatPowerTuningData pwr_brick_power2_Tuning < uid=... >
    {
        myRange 4
        myDamageArea < uid=... >
        {
            myLength 1
        }
    }

is addressed as  pwr_brick_power2_Tuning.myDamageArea.myLength. A segment matches a
node's key (first word) or its name (second word, quotes stripped); add [n] to pick
the n-th match when several share a key, and quote segments containing dots or
spaces: "[BuddyPower] Shove".myIsPassive

Only the value text of the addressed line changes; every other byte is kept.
"""
import re

QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')


def _code(line):
    """line with quoted strings blanked and // comments removed"""
    t = QUOTED.sub(lambda m: '"' + ' ' * (len(m.group()) - 2) + '"', line)
    i = t.find('//')
    return t[:i] if i >= 0 else t


class Node:
    __slots__ = ('line', 'key', 'name', 'children', 'end')

    def __init__(self, line, key, name):
        self.line, self.key, self.name = line, key, name
        self.children = None       # list for blocks
        self.end = None            # index of closing brace line


class Doc:
    def __init__(self, data):
        self.text = data.decode('utf-8', 'surrogateescape')
        self.lines = self.text.splitlines(keepends=True)
        self.root = Node(-1, '', '')
        self.root.children = []
        self._parse()

    def _parse(self):
        stack = [self.root]
        last = None
        for i, ln in enumerate(self.lines):
            t = _code(ln).strip()
            if not t:
                continue
            if t == '{':
                if last is None or last.children is not None:
                    raise ValueError('line %d: "{" without a header' % (i + 1))
                last.children = []
                stack.append(last)
                continue
            if t == '}':
                if len(stack) == 1:
                    raise ValueError('line %d: unbalanced "}"' % (i + 1))
                stack.pop().end = i
                continue
            key = ln.split()[0]
            rest = ln.strip()[len(key):].strip()
            if rest.startswith('"'):
                q = QUOTED.match(rest)
                name = q.group()[1:-1] if q else ''
            else:
                name = rest.split()[0] if rest else ''
                if name in ('<', '='):
                    name = ''
            last = Node(i, key, name)
            stack[-1].children.append(last)
        if len(stack) != 1:
            raise ValueError('unclosed block')

    # ---- addressing ----------------------------------------------------
    def find(self, path):
        node = self.root
        for seg in split_path(path):
            m = re.fullmatch(r'(.*?)\[(\d+)\]', seg)
            idx = int(m.group(2)) if m else None
            seg = m.group(1) if m else seg
            kids = node.children or []
            ids = lambda c: (c.key, c.name) if c.children is not None else (c.key,)
            hits = [c for c in kids if seg in ids(c)] or \
                   [c for c in kids if seg.lower() in [x.lower() for x in ids(c)]]
            if not hits:
                raise KeyError('%s: no "%s" (have: %s)' % (
                    path, seg, ', '.join(sorted({ids(c)[-1] or c.key for c in kids}))[:300]))
            if idx is None and len(hits) > 1:
                raise KeyError('%s: "%s" matches %d entries, add [0]..[%d]' % (path, seg, len(hits), len(hits) - 1))
            if idx is not None and idx >= len(hits):
                raise KeyError('%s: "%s" has only %d entries' % (path, seg, len(hits)))
            node = hits[idx or 0]
        return node

    def _value_span(self, node):
        ln = self.lines[node.line]
        body = ln.rstrip('\r\n')
        code = _code(body)
        if node.children is not None:
            raise ValueError('line %d is a block, not a value' % (node.line + 1))
        eq = code.find('> =')
        if eq >= 0:                                   # key < uid=... > = value
            start = eq + 3
            while start < len(body) and body[start] == ' ':
                start += 1
        else:                                         # key value
            m = re.match(r'\s*\S+\s+', body)
            if not m:
                raise ValueError('line %d has no value' % (node.line + 1))
            start = m.end()
        end = len(code.rstrip())
        return start, end

    def get(self, path):
        node = self.find(path)
        if node.children is not None:
            return ''.join(self.lines[node.line:node.end + 1])
        s, e = self._value_span(node)
        return self.lines[node.line][s:e]

    def set(self, path, value):
        node = self.find(path)
        s, e = self._value_span(node)
        ln = self.lines[node.line]
        self.lines[node.line] = ln[:s] + value + ln[e:]

    def add(self, path, line):
        """Append `line` inside the block at `path`. If the block is missing, create it at
        the end of its parent block (fields are matched by name, so order doesn't matter)."""
        segs = split_path(path)
        try:
            node = self.find(path)
        except KeyError:
            node = None
        if node is not None:
            if node.children is None:
                raise ValueError('%s is a value, not a block' % path)
            at, indent, nl = node.end, self._indent(node) + '\t', self._nl(node.line)
            new = [indent + line + nl]
        else:
            parent = self.find(join_path(segs[:-1])) if len(segs) > 1 else self.root
            if parent is not self.root and parent.children is None:
                raise ValueError('%s is a value, not a block' % join_path(segs[:-1]))
            if parent is self.root:
                at, indent, nl = len(self.lines), '', self._nl(0)
            else:
                at, indent, nl = parent.end, self._indent(parent) + '\t', self._nl(parent.line)
            new = [indent + segs[-1] + nl, indent + '{' + nl, indent + '\t' + line + nl, indent + '}' + nl]
        self.lines[at:at] = new
        self._reparse()

    def include(self, path):
        """Add `include "path"` after the existing includes (no-op if already there). A
        definition such as a status effect has to be included before data can refer to it."""
        want = 'include "%s"' % path.strip('"')
        incs = [i for i, ln in enumerate(self.lines) if ln.lstrip().startswith('include ')]
        if any(self.lines[i].strip().replace("'", '"') in (want, 'include ' + path.strip('"')) for i in incs):
            return False
        at = incs[-1] + 1 if incs else 0
        self.lines[at:at] = [want + self._nl(incs[-1] if incs else 0)]
        self._reparse()
        return True

    def _indent(self, node):
        ln = self.lines[node.line]
        return ln[:len(ln) - len(ln.lstrip(' \t'))]

    def _nl(self, i):
        ln = self.lines[i] if i < len(self.lines) else ''
        return '\r\n' if ln.endswith('\r\n') or (not ln and '\r\n' in self.text) else '\n'

    def _reparse(self):
        self.text = ''.join(self.lines)
        self.root = Node(-1, '', '')
        self.root.children = []
        self._parse()

    def dump(self):
        return ''.join(self.lines).encode('utf-8', 'surrogateescape')


def join_path(segs):
    return '.'.join('"%s"' % s if ('.' in s or ' ' in s) else s for s in segs)


def split_path(path):
    segs, cur, q = [], '', False
    for ch in path:
        if ch == '"':
            q = not q
        elif ch == '.' and not q:
            segs.append(cur); cur = ''
        else:
            cur += ch
    segs.append(cur)
    return [s.strip() for s in segs if s.strip()]
