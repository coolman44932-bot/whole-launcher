"""Mod discovery, load order and merging.

A mod is a folder holding any of:
  manifest.ini          name=, author=, version=, description=   (FractureLoader format)
  camel/...             whole-file replacements, mirroring game paths; a BVM file
                        (node graph etc.) may be given as <name>.json from `tfbw bvm`
  loc/<lang>/<pkg>.tsv  string patches: GUID<TAB>text
  *.tweak               value patches for juice data, stackable across mods:
                          [camel/game system data/juice/powers/playerpowers.juice]
                          pwr_brick_power2_Tuning.myRange = 6
                          some_Tuning.myStatusEffects += StatusEffect StatusEffect {uid} = GrossedOut
                        (`+=` adds a line inside a block, creating the block if missing;
                         {uid} becomes a fresh object id;
                         `include <path>` loads a definition, e.g. a status effect, the file
                         doesn't include yet)

Mods live in mods/. A folder that is not itself a mod is searched a few levels
down, so an unzipped download like  X/FractureLoader/Mods/Y/manifest.ini  works.
mods/load_order.txt lists them top to bottom (later wins); '-' disables one.
"""
import json
import os
import re
from collections import defaultdict

from . import bvm, juice, locpack

LOC_DIR = 'camel/localization/packages'
# juice text formats: every one of these in the game uses Windows line breaks, and a file
# with bare LF lines is silently dropped (seen 2026-10-04: artifacts.juice -> free artifacts)
JUICE_TEXT = ('.juice', '.mitem', '.mpower', '.mvendorconfig', '.mnpc', '.mskillscriptjuice', '.fruit')


def crlf(data):
    """normalize a juice text file to CRLF line breaks"""
    return data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')


ORDER_FILE = 'load_order.txt'
ORDER_HEADER = ('# Load order: top loads first, later mods win conflicts.\n'
                '# Put "-" in front of a mod to disable it. Edit freely or use `python -m tfbw mods`.\n')


class Mod:
    def __init__(self, mod_id, root):
        self.id, self.root = mod_id, root
        self.enabled = True
        self.meta = {}
        ini = os.path.join(root, 'manifest.ini')
        if os.path.exists(ini):
            for ln in open(ini, encoding='utf-8-sig', errors='replace'):
                if '=' in ln and not ln.lstrip().startswith(('#', ';')):
                    k, v = ln.split('=', 1)
                    self.meta[k.strip().lower()] = v.strip()
        self.files, self.tweaks, self.loc = {}, [], {}
        self.new_loc = set()                       # string ids this mod adds (TSV "+GUID")
        self.errors = []
        gen_dir = os.path.join(root, 'generators')
        # generators/*.py: scripts with build(toc, mod_dir) that write graph edits (camel/....json)
        # from the player's own game at install time, so a mod never has to ship game files
        self.generators = sorted(os.path.join(gen_dir, n) for n in os.listdir(gen_dir)
                                 if n.endswith('.py') and not n.startswith('_')) if os.path.isdir(gen_dir) else []
        self._scan()

    @property
    def name(self):
        return self.meta.get('name') or self.id

    @property
    def classes(self):
        """class slots this mod takes over (manifest `class=brutalist, psychic`)"""
        return [c.strip().lower() for c in self.meta.get('class', '').split(',') if c.strip()]

    @property
    def collection(self):
        """the overhaul this mod belongs to (manifest `collection=warhammer-1`), or None"""
        return self.meta.get('collection', '').strip().lower() or None

    def _scan(self):
        for dirpath, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for n in names:
                full = os.path.join(dirpath, n)
                rel = os.path.relpath(full, self.root).replace('\\', '/')
                low = rel.lower()
                if low.startswith('camel/'):
                    self.files[low] = full
                elif low.startswith('loc/') and low.endswith('.tsv') and low.count('/') == 2:
                    _, lang, pkg = low.split('/')
                    self.loc[(lang, pkg[:-4])] = self._read_tsv(full)
                elif low.endswith('.tweak'):
                    self._read_tweak(full)
                elif '/' not in low and low not in ('manifest.ini',) and not low.endswith(('.md', '.txt', '.png', '.jpg')):
                    self.files[low] = full        # game-root file; kept only if the game has it

    def _read_tsv(self, path):
        patch = {}
        for i, line in enumerate(open(path, encoding='utf-8-sig').read().splitlines(), 1):
            if not line.strip() or line.startswith('#'):
                continue
            if '\t' not in line:
                self.errors.append('%s:%d: expected GUID<TAB>text' % (path, i)); continue
            guid, text = line.split('\t', 1)
            guid = guid.strip().upper()
            if guid.startswith('+'):                  # a brand-new string (new items etc.)
                guid = guid[1:].strip()
                self.new_loc.add(guid)
            patch[guid] = text.replace('\\n', '\n')
        return patch

    def _read_tweak(self, path):
        target = None
        for i, line in enumerate(open(path, encoding='utf-8-sig').read().splitlines(), 1):
            s = line.strip()
            if not s or s.startswith(('#', '//', ';')):
                continue
            m = re.fullmatch(r'\[(.+)\]', s)
            if m:
                target = m.group(1).strip().replace('\\', '/').lower()
                continue
            if target is not None and s.startswith('include '):
                inc = _strip_comment(s[8:].strip()).strip('"')
                self.tweaks.append((target, '', inc, '%s:%d' % (os.path.basename(path), i), 'include'))
                continue
            eq = _split_eq(s)
            if target is None or eq is None:
                self.errors.append('%s:%d: expected [file] header then path = value' % (path, i)); continue
            key, value = eq
            op = '+=' if key.endswith('+') else '='
            key = key.rstrip('+').strip()
            self.tweaks.append((target, key, value, '%s:%d' % (os.path.basename(path), i), op))

    def summary(self):
        parts = []
        if self.files: parts.append('%d files' % len(self.files))
        if self.tweaks: parts.append('%d tweaks' % len(self.tweaks))
        n = sum(len(p) for p in self.loc.values())
        if n: parts.append('%d strings' % n)
        if self.generators: parts.append('%d scripts' % len(self.generators))
        return ', '.join(parts) or 'empty'


def _loads_status(toc, files, text, gp, name, depth=0, seen=None):
    """does this juice text (or anything it includes) include the definition of status effect `name`?"""
    seen = set() if seen is None else seen
    want = 'statuseffect/%s.juice' % name.lower()
    for inc in re.findall(r'^[ \t]*include[ \t]+"?([^"\r\n]+?)"?[ \t]*\r?$', text, re.M):
        inc = inc.strip().lower().replace('\\', '/')
        if inc.startswith('./'):
            inc = gp.rsplit('/', 1)[0] + inc[1:]
        if inc.endswith(want):
            return True
        if depth < 4 and inc not in seen and inc.endswith('.juice'):
            seen.add(inc)
            data = files.get(inc)
            if data is None and toc.get(inc) is not None:
                data = toc.read(toc.get(inc))
            if data is not None and _loads_status(toc, files, data.decode('utf-8', 'replace'), inc, name, depth + 1, seen):
                return True
    return False


def _uid(*parts):
    """stable 32-hex object id for lines a tweak adds (the game gives every object its own)"""
    import hashlib
    return hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest().upper()


def _split_eq(s):
    q = False
    for i, ch in enumerate(s):
        if ch == '"':
            q = not q
        elif ch == '=' and not q:
            return s[:i].strip(), _strip_comment(s[i + 1:].strip())
    return None


def _strip_comment(v):
    """drop a trailing '  # comment' (a # after whitespace, outside quotes); '#FF00FF' stays"""
    q = False
    for i, ch in enumerate(v):
        if ch == '"':
            q = not q
        elif ch == '#' and not q and i > 0 and v[i - 1] in ' \t':
            return v[:i].rstrip()
    return v


def _is_mod_root(d):
    try:
        names = os.listdir(d)
    except OSError:
        return False
    low = [n.lower() for n in names]
    return ('manifest.ini' in low or 'camel' in low or 'loc' in low or 'generators' in low
            or any(n.endswith('.tweak') for n in low))


def discover(mods_dir):
    found = []
    for top in sorted(os.listdir(mods_dir)):
        path = os.path.join(mods_dir, top)
        if top.startswith(('.', '_')) or not os.path.isdir(path):
            continue
        if _is_mod_root(path):
            found.append(Mod(top, path))
            continue
        for dirpath, dirs, _ in os.walk(path):
            depth = os.path.relpath(dirpath, path).count(os.sep)
            if dirpath != path and _is_mod_root(dirpath):
                found.append(Mod(os.path.relpath(dirpath, mods_dir).replace('\\', '/'), dirpath))
                dirs[:] = []
            elif depth >= 4:
                dirs[:] = []
    return found


def ordered(mods_dir):
    """Discovered mods in load order. Syncs load_order.txt (new mods appended, gone ones dropped)."""
    mods = {m.id: m for m in discover(mods_dir)}
    path = os.path.join(mods_dir, ORDER_FILE)
    order = []
    if os.path.exists(path):
        for ln in open(path, encoding='utf-8'):
            s = ln.strip()
            if not s or s.startswith('#'):
                continue
            enabled = not s.startswith('-')
            mid = s.lstrip('-').strip()
            if mid in mods and mid not in [m.id for m in order]:
                mods[mid].enabled = enabled
                order.append(mods[mid])
    order += [m for mid, m in sorted(mods.items()) if m not in order]
    save_order(mods_dir, order)
    return order


def save_order(mods_dir, order):
    text = ORDER_HEADER + ''.join('%s%s\n' % ('' if m.enabled else '-', m.id) for m in order)
    path = os.path.join(mods_dir, ORDER_FILE)
    if not os.path.exists(path) or open(path, encoding='utf-8').read() != text:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)


class Merge:
    def __init__(self):
        self.files = {}          # game path -> final bytes
        self.conflicts = []      # human-readable lines
        self.notes = []
        self.warnings = []
        self.errors = []


def class_clashes(mods):
    """Two overhauls can't both own a class. Mods of the same collection may share a class
    (e.g. a class mod plus its status-effect mod); anything else claiming it is an error."""
    owners = defaultdict(dict)                  # class -> {owner: [mod ids]}
    for m in mods:
        for c in m.classes:
            owners[c].setdefault(m.collection or m.id, []).append(m.id)
    return ['class "%s" is taken over by %s: enable only one (python -m tfbw mods use @<collection>)'
            % (c, ' and '.join('%s (%s)' % (o, ', '.join(ids)) for o, ids in by.items()))
            for c, by in sorted(owners.items()) if len(by) > 1]


def merge(toc, order, index=None):
    """Combine enabled mods in order. Replacements first, then tweaks and string patches on top.
    `index` (tfbw.index.load) enables warnings about dead files and duplicate definitions."""
    out = Merge()
    mods = [m for m in order if m.enabled]
    for m in mods:
        out.errors += ['[%s] %s' % (m.id, e) for e in m.errors]
    out.errors += class_clashes(mods)

    # 1. whole-file replacements: last one wins
    owners = defaultdict(list)
    for m in mods:
        for gp, full in m.files.items():
            if '/' not in gp and toc.get(gp) is None:
                out.notes.append('[%s] ignored %s (not a game file)' % (m.id, gp))
                continue
            owners[gp].append((m, full))
    for gp, lst in list(owners.items()):
        data = open(lst[-1][1], 'rb').read()
        # x.mskillscriptgraph.json (or any BVM game file + .json) is compiled to binary
        if gp.endswith('.json') and toc.get(gp) is None and toc.get(gp[:-5]) is not None:
            try:
                data = bvm.dumps(bvm.from_json(json.loads(data.decode('utf-8-sig'))))
            except (ValueError, TypeError) as ex:
                out.errors.append('[%s] %s: %s' % (lst[-1][0].id, gp, ex))
                continue
            del owners[gp]
            gp = gp[:-5]
            owners[gp] = owners.get(gp, []) + lst
        out.files[gp] = data
        if len(lst) > 1:
            out.conflicts.append('%s replaced by %s -> %s wins' % (gp, ', '.join(x[0].id for x in lst), lst[-1][0].id))

    def base(gp):
        if gp in out.files:
            return out.files[gp]
        e = toc.get(gp)
        if e is None:
            raise KeyError(gp)
        return toc.read(e)

    # 2. tweaks
    by_file = defaultdict(list)
    for m in mods:
        for target, path, value, where, op in m.tweaks:
            by_file[target].append((m, path, value, where, op))
    for gp, edits in sorted(by_file.items()):
        try:
            doc = juice.Doc(base(gp))
        except KeyError:
            out.errors.append('tweak target %s is not a game file' % gp); continue
        except ValueError as ex:
            out.errors.append('%s cannot be tweaked: %s' % (gp, ex)); continue
        setters = defaultdict(list)
        for m, path, value, where, op in edits:
            try:
                if op == 'include':
                    doc.include(value)
                    continue
                if op == '+=':
                    doc.add(path, value.replace('{uid}', '< uid=%s >' % _uid(m.id, gp, path, value, where)))
                    continue
                doc.set(path, value)
                setters[path].append((m, value))
            except (KeyError, ValueError) as ex:
                out.errors.append('[%s] %s: %s' % (m.id, where, ex))
        for path, lst in setters.items():
            if len({id(x[0]) for x in lst}) > 1 and len({x[1] for x in lst}) > 1:
                out.conflicts.append('%s %s: %s -> %s wins' % (
                    gp, path, ', '.join('%s=%s' % (x[0].id, x[1]) for x in lst), lst[-1][0].id))
        for m, path, value, where, op in edits:
            ref = re.search(r'^StatusEffect\s.*=\s*(\w+)\s*$', value) if op == '+=' else None
            if ref and not _loads_status(toc, out.files, doc.text, gp, ref.group(1)):
                out.errors.append('[%s] %s: %s adds status effect %s, but %s never includes its definition; add '
                                  '"include camel/game system data/juice/statuseffect/%s.juice" to that section'
                                  % (m.id, where, path, ref.group(1), gp, ref.group(1).lower()))
        if index is not None:
            includers = index['includers'].get(gp.lower())
            edited = {juice.split_path(path)[0] for m, path, value, where, op in edits if path}
            copied = any(f != gp for name in edited for f in index['defs'].get(name, []))
            # an unincluded file is only suspicious when the objects it edits are defined in a
            # live file too (the dead dlc2 Netherborn copy); a file holding the only definition
            # (artifacts.juice) is loaded by the engine directly
            if not includers and gp.endswith('.juice') and copied:
                out.warnings.append('%s: nothing in the game includes this file, so edits to it probably do '
                                    'nothing (a dead copy?)' % gp)
            for name in sorted({juice.split_path(path)[0] for m, path, value, where, op in edits if path}):
                others = [f for f in index['defs'].get(name, []) if f != gp]
                if others:
                    live = [f for f in others if index['includers'].get(f.lower())]
                    out.warnings.append('%s is also defined in %s%s' % (
                        name, ', '.join(others), ' (which the game DOES include, edit that copy too)' if live else ''))
        replaced_by = owners.get(gp)
        if replaced_by and {x[0].id for x in edits} - {replaced_by[-1][0].id}:
            out.notes.append('%s: tweaks applied on top of %s\'s replacement' % (gp, replaced_by[-1][0].id))
        out.files[gp] = doc.dump()

    # 3. localization string patches
    by_pkg = defaultdict(list)
    for m in mods:
        for key, patch in m.loc.items():
            by_pkg[key].append((m, patch))
    for (lang, pkg), patches in sorted(by_pkg.items()):
        gp = '%s/%s/%s.locpackbin' % (LOC_DIR, lang, pkg)
        try:
            ver, rows = locpack.read_bin(base(gp))
        except KeyError:
            out.errors.append('no localization package %s' % gp); continue
        known = {g for g, _, _ in rows}
        final, who, added = {}, defaultdict(list), []
        for m, patch in patches:
            for g, t in patch.items():
                if g not in known and g in m.new_loc:
                    if not re.fullmatch(r'[0-9A-F]{32}', g):
                        out.errors.append('[%s] %s: bad new string id %s' % (m.id, gp, g)); continue
                    known.add(g)
                    added.append(g)
                elif g not in known:
                    out.errors.append('[%s] %s: unknown string id %s (new strings start with +)' % (m.id, gp, g))
                    continue
                final[g] = t
                who[g].append((m, t))
        for g, lst in who.items():
            if len({id(x[0]) for x in lst}) > 1 and len({x[1] for x in lst}) > 1:
                out.conflicts.append('%s string %s: %s -> %s wins' % (
                    gp, g, ', '.join('%s="%s"' % (x[0].id, x[1][:30]) for x in lst), lst[-1][0].id))
        rows = rows + [(g, 0, '') for g in added]
        out.files[gp] = locpack.write_bin([(g, v, final.get(g, t)) for g, v, t in rows], ver)
    for gp, data in out.files.items():
        if gp.endswith(JUICE_TEXT) and data.count(b'\n') != data.count(b'\r\n'):
            out.files[gp] = crlf(data)
            out.notes.append('%s: line breaks fixed to CRLF (the game ignores juice text without them)' % gp)
    return out
