"""Graph recipes: the node patterns proven in-game, as reusable building blocks for mods.

Every recipe clones the game's own nodes (from the Shocked status script, which has all of
them) instead of building nodes from scratch, so each pin is fed exactly the way the game
feeds it. Proven in-game (TFBW, 2026-10-03):

  apply_status     ApplyStatusEffectByUID: pin0 impulse, pin1 target via Conversion/Explicit
                   (never Implicit: that stalled a script), pin2 applier, pin3 StatusEffectConstant
  enemy_gate       only continue when the script user's base faction is Thug (= an enemy),
                   the check the game's own Shocked-spread artifact uses
  for_each_nearby  EntityIterator over GetEntitiesInDamageArea(user, facing, <effect> area)

Typical use (a status script that makes enemies carry a second status):

    from tfbw import recipes
    toc = recipes.game_toc()
    g, root = recipes.load(toc, 'camel/skillscript/statuseffect_bleeding_onapply.mskillscriptgraph')
    kit = recipes.Kit(toc, g)
    go = kit.enemy_gate(kit.init(), 100)
    kit.apply_status(go, 'Marked', kit.user())
    recipes.save(root, MOD, path)
"""
import copy
import hashlib
import json
import os
import re

from . import bvm, graph, sdf

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_DATA = r"C:\Program Files (x86)\Steam\steamapps\common\South Park The Fractured But Whole\camel\sdf\pc\data"
SHOCKED = 'camel/skillscript/statuseffect_shocked.mskillscriptgraph'
# the Elementalist ultimate rolls a random status on each target: templates for Kit.changers_dice
RANDOM_STATUS = 'camel/skillscript/camel_elementalist4_random_status_effect_placeholder.mskillscriptgraph'
GIVE_SRC = 'camel/skillscript/onplayerteamdeath.mskillscriptgraph'      # #90 AddItemByUIDImpulse
CASH = '51C546C655D1AE500000000B076C26DD'                               # the Cash item, in cents
RANDOM_T = {356: 'GetTargetedEntities', 361: 'GameEntityListToHandle', 1194: 'RandomMultiSelectImpulse',
            1195: 'False'}


def game_toc():
    """the pristine game index (mods are built against vanilla files)"""
    backup = os.path.join(PROJECT, 'backup', 'sdf.sdftoc.original')
    toc = sdf.Toc(backup if os.path.exists(backup) else os.path.join(GAME_DATA, 'sdf.sdftoc'))
    toc.dir = GAME_DATA
    return toc


def load(toc, path):
    root = bvm.loads(toc.read(toc.get(path)))[0]
    return graph.Graph(root), root


def save(root, mod_dir, path):
    """write the graph as <mod>/<game path>.json, which the packer compiles back to BVM"""
    out = os.path.join(mod_dir, *path.split('/')) + '.json'
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(bvm.to_json(root), f, indent=1, ensure_ascii=False)
    return out


STATUS_TRACK_SRC = 'camel/baked/snowdrop/animation/anm_maincharacter_combat_cyborg_power03_right.manimevent'
STATUS_SCRIPT = 'camel/skillscript/camel_simple_statuseffect_application_script.mskillscriptjuice'
DAMAGE_SCRIPTS = ('camel_simple_damage_script', 'simple_charge_damage_script')


def _tracks(root):
    return root.get('Tracks').array


def add_status_event(toc, anim_path):
    """A power's tuning statuses (myStatusEffects) only land if its animation fires the
    game's status-application script. Pull, charge and some punch animations have no such
    event, so statuses added to them silently do nothing. This copies the "Status Application
    Event" track from Faraday Uncaged's animation and times it to the damage hit.
    Returns the new BVM root (or None if the animation already applies statuses)."""
    root = bvm.loads(toc.read(toc.get(anim_path)))[0]
    text = json.dumps(bvm.to_json(root))
    if 'statuseffect_application' in text.lower():
        return None
    damage_pos = None
    for t in _tracks(root):
        for ev in t.get('Events').array:
            script = str(ev.get('Event').get('SkillScript')) if isinstance(ev.get('Event'), bvm.Table) else ''
            if any(s in script.lower() for s in DAMAGE_SCRIPTS):
                damage_pos = ev.get('CyclePos')
    assert damage_pos is not None, '%s has no damage event to time the statuses to' % anim_path
    src = bvm.loads(toc.read(toc.get(STATUS_TRACK_SRC)))[0]
    track = next(t for t in _tracks(src) if STATUS_SCRIPT.split('/')[-1] in json.dumps(bvm.to_json(t)).lower())
    track = copy.deepcopy(track)
    ev = track.get('Events').array[0]
    ev.set('CyclePos', copy.deepcopy(damage_pos))
    # a fresh, stable event id derived from the animation path
    digest = hashlib.md5(('tfbw-status-event:' + anim_path).encode()).hexdigest()
    old = ev.get('UID')
    ev.set('UID', bvm.Tagged(old.tag, bytes.fromhex(digest) if isinstance(old.value, bytes) else digest))
    _tracks(root).append(track)
    return root


def juice_uid_to_bvm(uid):
    """juice writes uids as 86D64076...; graph files store the same 16 bytes reversed"""
    return bytes.fromhex(uid)[::-1].hex()


class Kit:
    """Adds cloned game nodes to graph `g` and wires them like the game does."""

    # Shocked script node ids used as templates (group #358 holds the artifact's spread logic)
    T = dict(user=72, conv_impl=89, conv_expl=356, facing=95, area=100, effect_enum=101,
             faction_targeting=88, in_area=82, iterator=71, select=150, equal=159, dead=325,
             active=286, base_faction=364, faction_equal=365, thug=366, apply=371, status=372,
             artifact_equipped=362, uid_const=370)

    def __init__(self, toc, g):
        self.g = g
        toc = getattr(toc, 'vanilla', toc)             # templates always from the untouched game
        self._toc = toc
        sh = graph.Graph(bvm.loads(toc.read(toc.get(SHOCKED)))[0])
        self.src = dict(sh.nodes)
        self.src.update(graph.Graph(self.src[358].get('graph')).nodes)
        for key, want in dict(iterator='EntityIterator', in_area='GetEntitiesInDamageArea', apply='ApplyStatusEffectByUID',
                              base_faction='GetCombatantBaseFaction', thug='Thug', status='StatusEffectConstant',
                              dead='IsDead', active='IsStatusEffectActive', select='ImpulseSelect',
                              artifact_equipped='IsArtifactEquipped').items():
            n = self.src[self.T[key]]
            assert want in str(n.get('type')) + str(n.get('value')), (key, want)

    # ---- raw cloning ---------------------------------------------------------
    def node(self, key, **fields):
        n = copy.deepcopy(self.src[self.T[key]])
        ed = n.get('editorData')
        if isinstance(ed, bvm.Table):            # don't point at Shocked's comment boxes
            ed.items = [(k, v) for k, v in ed.items if k != 'parentId']
        for k, v in fields.items():
            n.set(k, v)
        return self.g.add_node(n)

    def c(self, a, ap, b, bp):
        self.g.connect(a, ap, b, bp)

    # ---- finding things in the target graph ------------------------------------
    def find(self, type_name):
        hits = [i for i, n in self.g.nodes.items() if n.get('type') == type_name]
        assert len(hits) == 1, (type_name, hits)
        return hits[0]

    def init(self):
        return self.find('SkillScript:Init')

    # ---- values ------------------------------------------------------------------
    def user(self):
        """(node, pin) of the script's user: the unit carrying the status, or the caster"""
        return self.node('user'), 0

    def explicit(self, src):
        n = self.node('conv_expl')
        self.c(src[0], src[1], n, 1)
        return n, 0

    def implicit(self, src):
        n = self.node('conv_impl')
        self.c(src[0], src[1], n, 1)
        return n, 0

    # ---- flow --------------------------------------------------------------------
    def branch(self, impulse, cond):
        """ImpulseSelect: returns (true_out, false_out) as (node, pin)"""
        s = self.node('select')
        self.c(impulse[0], impulse[1], s, 0)
        self.c(cond[0], cond[1], s, 1)
        return (s, 2), (s, 3)

    def enemy_gate(self, src, pin):
        """continue only if the script user is an enemy (base faction Thug)"""
        u = self.user()
        base = self.node('base_faction')
        self.c(*self.explicit(u), base, 1)
        eq, thug = self.node('faction_equal'), self.node('thug')
        self.c(base, 0, eq, 1)
        self.c(*self.implicit((thug, 0)), eq, 2)
        return self.branch((src, pin), (eq, 0))[0]

    def artifact_gate(self, impulse, item_uid):
        """continue only while the player has the artifact `item_uid` (as written in juice,
        e.g. 86D6...) equipped: the check the game's own Lightning Rod artifact uses"""
        k = self.node('uid_const')
        self.g.nodes[k].set('value', bvm.Tagged(0x0f, juice_uid_to_bvm(item_uid)))
        ed = self.g.nodes[k].get('editorData')
        if isinstance(ed, bvm.Table):
            ed.items = [(a, b) for a, b in ed.items if a != 'nodeName']
        eq = self.node('artifact_equipped')
        self.c(k, 0, eq, 1)
        return self.branch(impulse, (eq, 0))[0]

    def apply_status(self, impulse, status, target, applier=None):
        """ApplyStatusEffectByUID <status> on target (node, pin) entity"""
        k = copy.deepcopy(self.src[self.T['status']])
        k.set('myName', bvm.Tagged(0x0e, status))
        k = self.g.add_node(k)
        a = self.node('apply')
        self.c(impulse[0], impulse[1], a, 0)
        self.c(*self.explicit(target), a, 1)
        app = applier or target
        self.c(app[0], app[1], a, 2)
        self.c(k, 0, a, 3)
        return a

    # ---- the random roll (Tzeentch's dice) ---------------------------------------------
    def _rand_src(self):
        """template nodes from the Elementalist ultimate's random-status script"""
        if not hasattr(self, '_rs'):
            rg = graph.Graph(bvm.loads(self._toc.read(self._toc.get(RANDOM_STATUS)))[0])
            self._rs = rg.nodes
            for nid, want in RANDOM_T.items():
                assert want in str(self._rs[nid].get('type')) + str(self._rs[nid].get('value')), (nid, want)
        return self._rs

    def _clone_rs(self, nid):
        n = copy.deepcopy(self._rand_src()[nid])
        ed = n.get('editorData')
        if isinstance(ed, bvm.Table):
            ed.items = [(k, v) for k, v in ed.items if k != 'parentId']
        return self.g.add_node(n)

    def for_each_target(self, impulse):
        """EntityIterator over the entities the running power targets (GetTargetedEntities of the
        script user), wired like the game's Elementalist ultimate. Returns (per-unit impulse,
        unit) as ((node, pin), (node, pin))."""
        tgt = self._clone_rs(356)                       # GetTargetedEntities(Explicit(user))
        self.c(*self.explicit(self.user()), tgt, 1)
        handle = self._clone_rs(361)                    # GameEntityListToHandle
        self.c(tgt, 0, handle, 1)
        it = self.node('iterator')
        self.c(*self.explicit((handle, 0)), it, 0)
        self.c(impulse[0], impulse[1], it, 50)
        return (it, 101), (it, 75)

    def unit_faction_gate(self, impulse, unit, enemies=True):
        """continue for a living unit of the wanted side (enemy = base faction Thug)"""
        alive = self.node('select')
        dead = self.node('dead')
        self.c(*self.implicit(unit), dead, 1)
        self.c(impulse[0], impulse[1], alive, 0)
        self.c(dead, 0, alive, 1)
        base = self.node('base_faction')
        self.c(*self.explicit(unit), base, 1)
        eq, thug = self.node('faction_equal'), self.node('thug')
        self.c(base, 0, eq, 1)
        self.c(*self.implicit((thug, 0)), eq, 2)
        yes, no = self.branch((alive, 3), (eq, 0))
        return yes if enemies else no

    def random_status(self, impulse, statuses, target, applier):
        """RandomMultiSelectImpulse: one of `statuses`, picked at random, on target"""
        r = self._clone_rs(1194)
        f = self._clone_rs(1195)                        # the game feeds pin1 a constant False
        self.c(impulse[0], impulse[1], r, 0)
        self.c(f, 0, r, 1)
        for k, s in enumerate(statuses):
            self.apply_status((r, 2 + k), s, target, applier)
        return r

    def give_item(self, impulse, item_uid, count=1):
        """AddItemByUIDImpulse: give the player `count` of item `item_uid` (as written in juice).
        Wired like the game hands out a Revive Potion in onplayerteamdeath (pin1 impulse, pin2
        item uid, pin3 count, pin4 False); proven in-game by the restore-artifacts mod. Cash is
        an item too (51C546C655D1AE500000000B076C26DD), counted in cents."""
        give_g = graph.Graph(bvm.loads(self._toc.read(self._toc.get(GIVE_SRC)))[0])
        t = give_g.nodes
        assert t[90].get('type') == 'CClient_Node_AddItemByUIDImpulse'

        def clone(n):
            n = copy.deepcopy(n)
            ed = n.get('editorData')
            if isinstance(ed, bvm.Table):
                ed.items = [(k, v) for k, v in ed.items if k not in ('parentId', 'nodeName')]
            return self.g.add_node(n)
        add, u, cnt, f = clone(t[90]), clone(t[92]), clone(t[91]), clone(t[298])
        self.g.nodes[u].set('value', bvm.Tagged(0x0f, juice_uid_to_bvm(item_uid)))
        old = self.g.nodes[cnt].get('value')
        self.g.nodes[cnt].set('value', bvm.Tagged(old.tag, count) if isinstance(old, bvm.Tagged) else count)
        self.c(impulse[0], impulse[1], add, 1)
        self.c(u, 0, add, 2)
        self.c(cnt, 0, add, 3)
        self.c(f, 0, add, 4)
        return add

    def changers_dice(self, impulse, statuses, enemies=True):
        """after `impulse`: every living unit of the wanted side the power hit gets one random
        status from `statuses` (applied by the caster)"""
        each, unit = self.for_each_target(impulse)
        go = self.unit_faction_gate(each, unit, enemies)
        return self.random_status(go, statuses, unit, self.user())


# ---- new items (artifacts, costumes, ...) ------------------------------------------
# Proven in-game 2026-10-04 with the Censer of the Plaguefather: a brand-new item is a
# .mitem (CRLF text!), a tuning block (price in cents) in the type's tuning file, a shop
# entry, "+GUID" loc strings, and its icon as baked/assets/ui/icons/items/icon_item_<name>.dds.

ITEMS = 'camel/game system data/juice/items/'
VENDORS = ITEMS + 'vendorconfig.mvendorconfig'
ICONS = 'camel/baked/assets/ui/icons/items/'


def crlf(text):
    """every juice text file in the game uses CRLF line breaks; a file with bare LF lines is
    dropped by the game (seen: all artifacts free, a new .mitem ignored)"""
    return text.replace('\r\n', '\n').replace('\n', '\r\n')


def write_file(mod_dir, game_path, data):
    """write bytes (or juice text, made CRLF) to mod_dir/<game path>"""
    if isinstance(data, str):
        data = crlf(data).encode('utf-8')
    out = os.path.join(mod_dir, *game_path.split('/'))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(data)
    return out


def loc_field(guid, text, max_len, comment):
    """a myUIName-style field; the in-game text comes from the "+GUID" loc line"""
    return ('"contextComment = \\"%s\\", description = \\"%s\\", enabled = true, guid = #%s, '
            'lineVersion = 0, maxLength = %d, text = \\"%s\\""' % (comment, comment, guid, max_len, text))


def _matching_brace(s, i):
    depth = 0
    for k in range(i, len(s)):
        if s[k] == '{':
            depth += 1
        elif s[k] == '}':
            depth -= 1
            if depth == 0:
                return k
    raise ValueError('unbalanced braces')


def add_to_shop(text, shop, item_uid, state='Day_1'):
    """append a VendorItem to shop `shop` (its name as written, quotes included) in a
    vendorconfig text; returns the new text (unchanged if the item is already sold there)"""
    start = text.index('VendorConfig %s' % shop)
    end = text.find('\nVendorConfig ', start + 1)
    end = len(text) if end < 0 else end
    block = text[start:end]
    if item_uid in block:
        return text
    n = max(int(x) for x in re.findall(r'VendorItem (\d+)', block)) + 1
    lst = block.index('myVendorItemList')
    close = _matching_brace(block, block.index('{', lst))
    line = block.rfind('\n', 0, close) + 1
    entry = '\n'.join(['\t\tVendorItem %d' % n, '\t\t{', '\t\t\tmyItem %s' % item_uid,
                       '\t\t\tmyGameState %s' % state, '\t\t}', ''])
    return text[:start] + block[:line] + entry + block[line:] + text[end:]


def append_block(toc, game_path, block, marker):
    """the game file's text with `block` appended (skipped if `marker` is already in it)"""
    text = toc.read(toc.get(game_path)).decode('utf-8')
    if marker not in text:
        text = text.rstrip('\r\n') + '\n' + block
    return text


class Artifact:
    """A brand-new artifact, the proven way (Censer, Golgfag's Purse): its .mitem, icon, tuning
    block and shop entries. The texts' "+GUID" lines go in the mod's loc/english/menus.tsv.
        slot: 'One' (Minor), 'Two' (Major) or 'Three' (Epic; only Epics show the effect line)
        stats: [(type, value)...] - Minor: Power + Guard/Zeal/Calm; Major: Power +
               Durability/Regeneration/Precision (the game's own pools)
        shops: VendorConfig names as written in vendorconfig, quotes included
        guids: (item uid, name, description, effect) - 32 hex digits each"""

    def __init__(self, name, title, effect, guids, slot, stats, price, shops, icon, mitem_file):
        self.name, self.title, self.effect = name, title, effect
        self.uid, self.name_guid, self.desc_guid, self.effect_guid = guids
        self.slot, self.stats, self.price, self.shops = slot, stats, price, shops
        self.icon, self.mitem_path = icon, ITEMS + mitem_file

    def mitem(self):
        return '\n'.join([
            'include ./itemincludes.juice', '',
            'ArtifactItem %s < uid=%s >' % (self.name, self.uid), '{',
            '\tmyUIName ' + loc_field(self.name_guid, self.title, 48, 'Name for ' + self.name),
            '\tmyUIDescription ' + loc_field(self.desc_guid, self.title, 185, 'Description for ' + self.name),
            '\tmyIcon "camel/ui/temp assets/items/tex_itemicon_placeholder.png"',     # ignored, as vanilla
            '\tmyEffectDescription', '\t{',
            '\t\tmyDescription ' + loc_field(self.effect_guid, self.effect, 123, self.name + '_Effect_Description'),
            '\t}', '}', ''])

    def tuning_block(self):
        stats = ''.join('\t\tStatValue %d\n\t\t{\n\t\t\tmyType %s\n\t\t\tmyValue %d\n\t\t}\n' % (i, t, v)
                        for i, (t, v) in enumerate(self.stats))
        return ('\nArtifactItemTuningData %s_Tuning\n{\n\tmySellPrice %.1f\n\tmyKeyItem FALSE\n\tmyItem %s\n'
                '\tmyType %s\n\tmyLevel 1\n\tmyStatValues\n\t{\n%s\t}\n}\n'
                % (self.name, self.price, self.uid, self.slot, stats))

    def write(self, toc, mod_dir, log=print):
        """the item, its icon, its stats (appended to artifacts.juice, layered over other mods)
        and its shop entries"""
        write_file(mod_dir, self.mitem_path, self.mitem())
        with open(self.icon, 'rb') as f:
            write_file(mod_dir, ICONS + 'icon_item_%s.dds' % self.name.lower(), f.read())
        tuning = ITEMS + 'artifacts.juice'
        write_file(mod_dir, tuning, append_block(toc, tuning, self.tuning_block(), self.uid))
        text = toc.read(toc.get(VENDORS)).decode('utf-8')
        for shop in self.shops:
            text = add_to_shop(text, shop, self.uid)
        write_file(mod_dir, VENDORS, text)
        log('%s: %s artifact, %s, $%.2f, sold at %s' % (
            self.title, {'One': 'Minor', 'Two': 'Major', 'Three': 'Epic'}[self.slot],
            ', '.join('%s %d' % s for s in self.stats), self.price / 100, ', '.join(s.strip('"') for s in self.shops)))


def status_rider(toc, mod_dir, script, item_uid, statuses, side='enemy', log=print):
    """while artifact `item_uid` is equipped, a unit that runs status script `script` (its
    on-apply / on-expire graph; the script user is the afflicted unit) also gets `statuses`:
    one status, or a list rolled at random (the Changer's dice). side='enemy' (base faction
    Thug: debuff riders) or 'ally' (anyone else: you and your party, for buff riders).
    Gated like the Censer (IsArtifactEquipped). Layers over other mods' edits to the script.
    `script` may be a list (e.g. every Regeneration variant): each gets the same rider."""
    if not isinstance(script, str):
        return [status_rider(toc, mod_dir, sc, item_uid, statuses, side, log) for sc in script]
    path = 'camel/skillscript/%s.mskillscriptgraph' % script
    g, root = load(toc, path)
    kit = Kit(toc, g)
    u = kit.user()
    base = kit.node('base_faction')
    kit.c(*kit.explicit(u), base, 1)
    eq, thug = kit.node('faction_equal'), kit.node('thug')
    kit.c(base, 0, eq, 1)
    kit.c(*kit.implicit((thug, 0)), eq, 2)
    yes, no = kit.branch((kit.init(), 100), (eq, 0))
    go = kit.artifact_gate(yes if side == 'enemy' else no, item_uid)
    if isinstance(statuses, str):
        kit.apply_status(go, statuses, kit.user())
    else:
        kit.random_status(go, statuses, kit.user(), kit.user())
    log('%s: %s also get %s' % (script, 'enemies' if side == 'enemy' else 'you and your allies',
                                statuses if isinstance(statuses, str) else ' / '.join(statuses)))
    return save(root, mod_dir, path)


DEATH = 'camel/skillscript/onanycombatantdeath.mskillscriptgraph'


def death_spread(toc, mod_dir, item_uid, statuses, roll=False, log=print):
    """While artifact `item_uid` is equipped: when an enemy dies, every living enemy on the 8
    tiles around it gets `statuses` (all of them, or one rolled at random with roll=True).
    The game's own Epic does this for Grossed Out (dlc3_crawl_artifact_epic3, group #699 of
    onanycombatantdeath); its area query is cloned from there (CenteredCircle 3x3 around the
    dead unit, facing-aware), the gates and the status application come from Kit (enemies =
    base faction Thug, alive). Layers over other mods' edits (Golgfag's Purse) to the script."""
    g, root = load(toc, DEATH)
    kit = Kit(toc, g)
    v = getattr(toc, 'vanilla', toc)
    dg = graph.Graph(bvm.loads(v.read(v.get(DEATH)))[0])
    D = dict(dg.nodes)
    D.update(graph.Graph(D[699].get('graph')).nodes)
    for sid, want in {702: 'GetEntitiesInDamageArea', 703: 'CenteredCircle', 711: 'GetFacingDirection',
                      713: 'EntityIterator', 728: 'GrossedOut'}.items():
        assert want in str(D[sid].get('type')) + str(D[sid].get('value')) + str(D[sid].get('myName')), (sid, want)

    def clone(sid):
        n = copy.deepcopy(D[sid])
        ed = n.get('editorData')
        if isinstance(ed, bvm.Table):
            ed.items = [(k, v) for k, v in ed.items if k not in ('parentId', 'nodeName')]
        return g.add_node(n)

    go = kit.artifact_gate(kit.enemy_gate(kit.init(), 100), item_uid)
    area = clone(702)
    for sid, pin in ((703, 25), (704, 26), (705, 27), (706, 28), (707, 29), (708, 30), (709, 50)):
        kit.c(clone(sid), 0, area, pin)
    kit.c(*kit.implicit(kit.user()), area, 10)
    facing = clone(711)
    kit.c(*kit.implicit(kit.user()), facing, 2)
    kit.c(facing, 1, area, 11)
    it = clone(713)
    kit.c(area, 100, it, 0)
    kit.c(go[0], go[1], it, 50)
    unit = (it, 75)
    yes = kit.unit_faction_gate((it, 101), unit, enemies=True)
    if roll:
        kit.random_status(yes, statuses, unit, unit)
    else:
        for s in statuses:
            kit.apply_status(yes, s, unit, unit)
    log('onanycombatantdeath: an enemy death gives the enemies around it %s'
        % ((' or ' if roll else ' + ').join(statuses)))
    return save(root, mod_dir, DEATH)
