"""Node graphs stored as BVM (.mskillscriptgraph, .mscript, .mcompoundnode, .muigraph, ...).

  nodesById        node id -> node table  (type, constants, pins, editorData)
  connectionsById  int4 (fromNode, fromPin, toNode, toPin)
  lastNodeId       highest id handed out

Compound nodes ("internal:Compound") carry a nested graph under "graph".
"""
from . import bvm

HIDDEN = {'IsEnabled', 'editorData', 'pins', 'lastId', 'type', 'graph'}


class Graph:
    def __init__(self, root):
        self.root = root

    @property
    def nodes(self):
        t = self.root.get('nodesById')
        out = {i: n for i, n in enumerate(t.array) if n is not None} if t else {}
        if t:
            out.update((k, n) for k, n in t.items)
        return out

    @property
    def connections(self):
        t = self.root.get('connectionsById')
        if not t:
            return []
        return [c.value for c in list(t.array) + [v for _, v in t.items] if isinstance(c, bvm.Tagged)]

    # ---- editing ------------------------------------------------------
    def new_id(self):
        nid = self.root.get('lastNodeId', 0) + 1
        self.root.set('lastNodeId', nid)
        return nid

    def add_node(self, node):
        nid = self.new_id()
        self.root.get('nodesById').items.append((nid, node))
        return nid

    def connect(self, src, src_pin, dst, dst_pin):
        self.root.get('connectionsById').array.append(bvm.Tagged(0x08, (src, src_pin, dst, dst_pin)))

    def find(self, type_contains=None, **fields):
        hits = []
        for nid, n in self.nodes.items():
            if type_contains and type_contains.lower() not in str(n.get('type', '')).lower():
                continue
            if all(_plain(n.get(k)) == v for k, v in fields.items()):
                hits.append(nid)
        return hits

    # ---- reading ------------------------------------------------------
    def summary(self, grep=None, indent=''):
        nodes, conns = self.nodes, self.connections
        ins, outs = {}, {}
        for a, ap, b, bp in conns:
            ins.setdefault(b, []).append((bp, a, ap))
            outs.setdefault(a, []).append((ap, b, bp))
        lines = [] if indent else ['%d nodes, %d connections' % (len(nodes), len(conns))]
        for nid in sorted(nodes):
            n = nodes[nid]
            fields = ' '.join('%s=%s' % (k, _show(v)) for k, v in n.items
                              if k not in HIDDEN and not isinstance(v, bvm.Table))
            head = '%s#%-5d %s  %s' % (indent, nid, n.get('type'), fields)
            if grep and grep.lower() not in head.lower():
                continue
            lines.append(head.rstrip())
            if ins.get(nid):
                lines.append(indent + '        in : ' + '  '.join(
                    'pin%s<-#%d.%s' % (p, a, ap) for p, a, ap in sorted(ins[nid], key=lambda x: x[0])))
            if outs.get(nid):
                lines.append(indent + '        out: ' + '  '.join(
                    'pin%s->#%d.%s' % (p, b, bp) for p, b, bp in sorted(outs[nid], key=lambda x: x[0])))
            sub = n.get('graph')
            if isinstance(sub, bvm.Table) and sub.get('nodesById') is not None and not grep:
                lines.append(indent + '        nested graph:')
                lines += Graph(sub).summary(indent=indent + '          ')
        return lines


def _plain(v):
    return v.value if isinstance(v, bvm.Tagged) else v


def _show(v):
    v = _plain(v)
    if isinstance(v, str):
        return repr(v) if (' ' in v or not v) else v
    if isinstance(v, tuple):
        return '(' + ','.join('%g' % x if isinstance(x, float) else str(x) for x in v) + ')'
    return str(v)
