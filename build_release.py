"""Build the Whole Launcher download (the zip uploaded to Nexus Mods) from this source tree.

    python build_release.py            -> dist/Whole Launcher <version>.zip

There is nothing to compile. The download is these same plain-text files: the launcher window
(launcher/whole_launcher.py), the toolkit it uses (tfbw/*.py), a .bat that starts it with the
user's own Python, and the readmes. This script only copies them into the folder layout of the
download and zips it. The version comes from VERSION in launcher/whole_launcher.py.

Requires Python 3.8+ and nothing else (standard library only).
"""
import os
import re
import shutil
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, 'dist')

# source file -> path inside the download
LAYOUT = {
    'launcher/whole_launcher.py': 'files/whole_launcher.py',
    'launcher/Whole Launcher.bat': 'Whole Launcher.bat',
    'launcher/README.txt': 'README.txt',
    'launcher/MODDING.md': 'MODDING.md',
    'launcher/mods_README.txt': 'mods/PUT MODS HERE.txt',
}


def version():
    src = open(os.path.join(ROOT, 'launcher', 'whole_launcher.py'), encoding='utf-8').read()
    return re.search(r"VERSION = '([^']+)'", src).group(1)


def files():
    out = dict(LAYOUT)
    for n in sorted(os.listdir(os.path.join(ROOT, 'tfbw'))):
        if n.endswith('.py'):
            out['tfbw/' + n] = 'files/tfbw/' + n
    return out


def main():
    ver = version()
    stage = os.path.join(DIST, 'Whole Launcher')
    if os.path.exists(stage):
        shutil.rmtree(stage)
    for src, dst in files().items():
        target = os.path.join(stage, *dst.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, *src.split('/')), target)
    zpath = os.path.join(DIST, 'Whole Launcher %s.zip' % ver)
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, names in os.walk(stage):
            for n in sorted(names):
                full = os.path.join(dirpath, n)
                z.write(full, os.path.relpath(full, DIST))
    print('built', os.path.relpath(zpath, ROOT))


if __name__ == '__main__':
    main()
