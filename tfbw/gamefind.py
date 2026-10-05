"""Find a South Park: The Fractured But Whole install on a player's PC.

Checks, in order: a path given by the player, every Steam library (registry + libraryfolders.vdf),
Ubisoft Connect installs (registry), then common folders on every drive. A folder counts as the
game when it holds camel/sdf/pc/data/sdf.sdftoc.
"""
import os
import re
import string

FOLDER = 'South Park The Fractured But Whole'


def toc_path(game):
    return os.path.join(game, 'camel', 'sdf', 'pc', 'data', 'sdf.sdftoc')


def is_game(path):
    return bool(path) and os.path.isfile(toc_path(path))


def _reg(root, key, value):
    try:
        import winreg
        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None
    except ImportError:
        return None


def _steam_libraries():
    try:
        import winreg
    except ImportError:
        return []
    roots = [_reg(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
             _reg(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
             r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam']
    libs = []
    for r in filter(None, roots):
        r = os.path.normpath(r)
        libs.append(r)
        vdf = os.path.join(r, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            text = open(vdf, encoding='utf-8', errors='replace').read()
            libs += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    return list(dict.fromkeys(libs))


def _ubisoft_installs():
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for base in (r'SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs', r'SOFTWARE\Ubisoft\Launcher\Installs'):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    d = _reg(winreg.HKEY_LOCAL_MACHINE, base + '\\' + sub, 'InstallDir')
                    if d:
                        out.append(os.path.normpath(d))
                    i += 1
        except OSError:
            pass
    return out


def candidates():
    for lib in _steam_libraries():
        yield os.path.join(lib, 'steamapps', 'common', FOLDER)
    yield from _ubisoft_installs()
    for drive in string.ascii_uppercase:
        root = drive + ':\\'
        if not os.path.exists(root):
            continue
        for sub in (r'Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games', r'Program Files\Ubisoft\Ubisoft Game Launcher\games',
                    r'SteamLibrary\steamapps\common', r'Steam\steamapps\common', r'Games', r'Program Files\Epic Games', ''):
            yield os.path.join(root, sub, FOLDER)


def find(given=None):
    """The game folder, or None. `given` may be the game folder or any folder inside it."""
    if given:
        p = os.path.abspath(given.strip('" '))
        while p and not is_game(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        if is_game(p):
            return p
    for c in candidates():
        if is_game(c):
            return os.path.normpath(c)
    return None
