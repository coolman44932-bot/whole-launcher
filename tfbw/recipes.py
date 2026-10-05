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

from . import bvm, graph, sdf

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_DATA = r"C:\Program Files (x86)\Steam\steamapps\common\South Park The Fractured But Whole\camel\sdf\pc\data"
SHOCKED = 'camel/skillscript/statuseffect_shocked.mskillscriptgraph'


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


class Kit:
    """Adds cloned game nodes to graph `g` and wires them like the game does."""

    # Shocked script node ids used as templates (group #358 holds the artifact's spread logic)
    T = dict(user=72, conv_impl=89, conv_expl=356, facing=95, area=100, effect_enum=101,
             faction_targeting=88, in_area=82, iterator=71, select=150, equal=159, dead=325,
             active=286, base_faction=364, faction_equal=365, thug=366, apply=371, status=372)

    def __init__(self, toc, g):
        self.g = g
        sh = graph.Graph(bvm.loads(toc.read(toc.get(SHOCKED)))[0])
        self.src = dict(sh.nodes)
        self.src.update(graph.Graph(self.src[358].get('graph')).nodes)
        for key, want in dict(iterator='EntityIterator', in_area='GetEntitiesInDamageArea', apply='ApplyStatusEffectByUID',
                              base_faction='GetCombatantBaseFaction', thug='Thug', status='StatusEffectConstant',
                              dead='IsDead', active='IsStatusEffectActive', select='ImpulseSelect').items():
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
