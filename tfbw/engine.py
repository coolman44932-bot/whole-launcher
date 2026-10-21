"""Install engine shared by Whole Launcher (the window) and the command line.

A game is modded like this, and only like this:
  - the original index is backed up once, inside the game: camel/sdf/pc/data/sdf.sdftoc.before-mods
  - enabled mods are copied to a scratch folder; each mod's generators/*.py builds its graph
    edits from the player's own game files (so mods never ship game data)
  - everything is merged (load order, tweaks, text), packed into ONE new archive
    (sdf-C-2999.sdfdata) and read back to verify, before the game folder is touched
  - a rebuilt index pointing at that archive replaces the live one
Uninstall restores the backup and deletes the new archive. Original archives are never changed.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile

from . import bvm, gamefind, mods, pack, sdf

PART = 2999
BACKUP_NAME = 'sdf.sdftoc.before-mods'
STATE_NAME = 'whole-launcher-installed.txt'
EXE = 'SouthPark_TFBW.exe'
STEAM_APP = 488790


class GameError(Exception):
    pass


def _process_running(exe):
    """True if a process named `exe` exists (Windows process snapshot via ctypes)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    k32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        raise OSError('process snapshot failed')
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe.lower():
                return True
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return False
    finally:
        k32.CloseHandle(snap)


class Game:
    def __init__(self, path, legacy_backup=None):
        if not gamefind.is_game(path):
            raise GameError('not a South Park: The Fractured But Whole folder: %s' % path)
        self.path = os.path.normpath(path)
        self.data = os.path.dirname(gamefind.toc_path(self.path))
        self.live = gamefind.toc_path(self.path)
        self.backup = os.path.join(self.data, BACKUP_NAME)
        self.state = os.path.join(self.data, STATE_NAME)
        self.part = os.path.join(self.data, sdf.part_name(PART))
        # the command-line toolkit kept its backup in the project folder; adopt it if present
        if legacy_backup and os.path.exists(legacy_backup) and not os.path.exists(self.backup):
            self.legacy_backup = legacy_backup
        else:
            self.legacy_backup = None

    @classmethod
    def find(cls, given=None, legacy_backup=None):
        p = gamefind.find(given)
        return cls(p, legacy_backup) if p else None

    # ---- state ------------------------------------------------------------------
    def running(self):
        """is the game running? Asks Windows directly (milliseconds); `tasklist` took ~2 s and
        made every click in the launcher feel slow"""
        try:
            return _process_running(EXE)
        except (OSError, AttributeError, ImportError):
            try:
                out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq ' + EXE], capture_output=True, text=True,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
                return EXE.lower() in out.lower()
            except OSError:
                return False

    def installed(self):
        """(name line, [mod ids]) of what is installed now, or None"""
        if not os.path.exists(self.state):
            return None
        lines = open(self.state, encoding='utf-8').read().splitlines()
        return lines[0] if lines else '', [ln[4:] for ln in lines if ln.startswith('mod ')]

    def modded(self):
        """True when the live index differs from the backed-up original (mods are in), also for
        installs made by the command-line toolkit, which writes no launcher state file"""
        src = self.backup if os.path.exists(self.backup) else self.legacy_backup
        if not src:
            return os.path.exists(self.state)
        if os.path.getsize(src) != os.path.getsize(self.live):
            return True
        with open(src, 'rb') as a, open(self.live, 'rb') as b:
            return a.read() != b.read()

    def pristine(self):
        """path of the unmodded index"""
        for p in (self.backup, self.legacy_backup):
            if p and os.path.exists(p):
                return p
        sizes = sdf.part_sizes(self.live)          # header only: no 10 MB parse
        if len(sizes) > PART and sizes[PART]:
            raise GameError('the game index is already modded but no backup of the original was found '
                            '(%s). Verify the game files in Steam/Ubisoft Connect, then try again.' % BACKUP_NAME)
        return self.live

    def toc(self):
        """the parsed original index; parsed once and reused (it holds ~490,000 entries)"""
        path = self.pristine()
        stamp = (path, os.path.getmtime(path), os.path.getsize(path))
        if getattr(self, '_toc_stamp', None) != stamp:
            self._toc = sdf.Toc(path)
            self._toc.dir = self.data
            self._toc_stamp = stamp
        return self._toc

    def other_loaders(self):
        hits = []
        for name in os.listdir(self.path):
            low = name.lower()
            if 'fractureloader' in low or low in ('dinput8.dll', 'version.dll', 'winmm.dll', 'dxgi.dll'):
                hits.append(name)
        if os.path.exists(self.part) and os.path.getsize(self.part) > 5 and not os.path.exists(self.state) \
                and not self.legacy_backup:
            hits.append(os.path.basename(self.part))
        return hits

    def play(self):
        """start the game through Steam when it's a Steam copy, else the exe directly"""
        if 'steamapps' in self.path.lower():
            os.startfile('steam://rungameid/%d' % STEAM_APP)
        else:
            subprocess.Popen([os.path.join(self.path, EXE)], cwd=self.path)


# ---- building ---------------------------------------------------------------------
class LayeredToc:
    """The game index as generators see it: game files, with the files that earlier mods'
    generators produced (in load order) laid on top. So two mods can patch the same graph,
    e.g. Nurgle's Rot (contagion) and an artifact that adds to the same Grossed Out script.
    `vanilla` is the untouched index (node templates should come from there)."""

    def __init__(self, base):
        self.vanilla = base
        self.files = {}                        # game path (lower) -> bytes

    def get(self, name):
        low = name.lower()
        if low in self.files:
            return ('layer', low)
        return self.vanilla.get(name)

    def read(self, entry, *args, **kw):
        if isinstance(entry, tuple) and entry[:1] == ('layer',):
            return self.files[entry[1]]
        return self.vanilla.read(entry, *args, **kw)

    def __getattr__(self, name):
        return getattr(self.vanilla, name)

    def add_mod_output(self, mod_dir):
        """register what a mod's generators wrote (camel/... files, .json compiled)"""
        added = []
        camel = os.path.join(mod_dir, 'camel')
        for dirpath, _, names in os.walk(camel):
            for n in names:
                full = os.path.join(dirpath, n)
                gp = os.path.relpath(full, mod_dir).replace(os.sep, '/').lower()
                data = open(full, 'rb').read()
                if gp.endswith('.json') and self.vanilla.get(gp) is None:
                    gp = gp[:-5]
                    data = bvm.dumps(bvm.from_json(json.loads(data.decode('utf-8-sig'))))
                self.files[gp] = data
                added.append(gp)
        return added


def _generated(mod_dir):
    """camel/ files under mod_dir, as {game path (lower): mod-relative key}"""
    out = {}
    for dirpath, _, names in os.walk(os.path.join(mod_dir, 'camel')):
        for n in names:
            key = os.path.relpath(os.path.join(dirpath, n), mod_dir).replace(os.sep, '/').lower()
            out[key[:-5] if key.endswith('.json') else key] = key
    return out


def prepare(toc, order, work, log=print):
    """Copy the enabled mods into `work` and run their generators there. Returns Mod objects
    (same order and enabled flags) that point at the prepared copies.

    Generators see a LayeredToc: a graph an earlier mod generated is handed to later
    generators already patched, and the later (combined) copy replaces the earlier one."""
    out = []
    layered = LayeredToc(getattr(toc, 'vanilla', toc))
    owner = {}                                  # game path -> (prepared Mod, its file key)
    for m in order:
        if not m.enabled:
            out.append(m)
            continue
        dst = os.path.join(work, 'mods', m.id.replace('/', '__'))
        shutil.copytree(m.root, dst)
        for i, gen in enumerate(m.generators):
            log('  %s: running %s' % (m.id, os.path.basename(gen)))
            spec = importlib.util.spec_from_file_location('fl_gen_%d_%s' % (i, os.path.basename(gen)[:-3]), gen)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, 'build'):
                raise GameError('%s: %s has no build(toc, mod_dir) function' % (m.id, os.path.basename(gen)))
            module.build(layered, dst)
        copy = mods.Mod(m.id, dst)
        copy.meta, copy.enabled = m.meta, True
        layered.add_mod_output(dst)
        for gp, key in _generated(dst).items():
            # a generator read the earlier mod's version through `layered`, so its copy holds
            # both mods' edits; a plain shipped file is a real replacement and stays a conflict
            if gp in owner and m.generators:
                prev, prev_key = owner[gp]
                prev.files.pop(prev_key, None)
                log('  %s builds on %s\'s %s' % (m.id, prev.id, gp.rsplit('/', 1)[-1]))
            owner[gp] = (copy, key)
        out.append(copy)
    return out


def build(game, order, work, index=None, log=print):
    """Merge + pack into `work`. Returns (merge result, toc path, part path); raises on errors."""
    toc = game.toc()
    prepared = prepare(toc, order, work, log)
    result = mods.merge(toc, prepared, index)
    if result.errors:
        raise GameError('the mods could not be built:\n  ' + '\n  '.join(result.errors))
    log('Packing %d modded files...' % len(result.files))
    toc_bytes, part_bytes = pack.build(toc, result.files, PART, log=lambda *a: None)
    toc_out = os.path.join(work, 'sdf.sdftoc')
    part_out = os.path.join(work, sdf.part_name(PART))
    with open(toc_out, 'wb') as f:
        f.write(toc_bytes)
    with open(part_out, 'wb') as f:
        f.write(part_bytes)
    check = sdf.Toc(toc_out)
    bad = [gp for gp, d in result.files.items() if check.read(check.get(gp), data_dir=work) != d]
    if bad:
        raise GameError('verification failed for %d files; nothing was changed' % len(bad))
    return result, toc_out, part_out


def install(game, order, index=None, log=print, label='Whole Launcher'):
    if game.running():
        raise GameError('the game is running. Close it first.')
    enabled = [m for m in order if m.enabled]
    clashes = mods.class_clashes(enabled)
    if clashes:
        raise GameError('\n'.join(clashes))
    with tempfile.TemporaryDirectory(prefix='wholelauncher-') as work:
        result, toc_out, part_out = build(game, order, work, index, log)
        if not os.path.exists(game.backup):
            shutil.copy2(game.toc().path, game.backup)
            log('Backed up the original game index -> ' + BACKUP_NAME)
        shutil.copy2(part_out, game.part)
        shutil.copy2(toc_out, game.live)
    with open(game.state, 'w', encoding='utf-8') as f:
        f.write(label + '\n' + ''.join('mod %s\n' % m.id for m in enabled))
    log('Installed %d mod(s).' % len(enabled))
    return result


def uninstall(game, log=print):
    if game.running():
        raise GameError('the game is running. Close it first.')
    src = game.backup if os.path.exists(game.backup) else game.legacy_backup
    if not src:
        raise GameError('no backup of the original game index found; nothing to uninstall')
    shutil.copy2(src, game.live)
    log('Restored the original game index.')
    # the backup has done its job; a stale one would be mistaken for the original if the game
    # is ever patched, so the next install backs up whatever the game has then
    for p in (game.part, game.state, game.backup):
        if os.path.exists(p):
            os.remove(p)
    log('Removed the mod archive. The game is back to normal.')
