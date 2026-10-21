# Whole Launcher

A mod manager for **South Park: The Fractured But Whole**, by Coolman559654 (Nexus Mods).
This repository is the complete source of the Whole Launcher download.

## There is nothing compiled

The download is plain Python source plus a `.bat` file. It contains no `.exe`, `.dll` or
other binary. What you download from Nexus is these same files, copied into a folder and zipped
by `build_release.py`.

| In the download | Source in this repo |
|---|---|
| `Whole Launcher.bat` | `launcher/Whole Launcher.bat` |
| `files/whole_launcher.py` (the window) | `launcher/whole_launcher.py` |
| `files/tfbw/*.py` (the toolkit it uses) | `tfbw/*.py` |
| `README.txt`, `MODDING.md` | `launcher/README.txt`, `launcher/MODDING.md` |
| `mods/PUT MODS HERE.txt` | `launcher/mods_README.txt` |

## Building the download

Requirements: Python 3.8 or newer. Only the standard library is used, so there is nothing to
install.

```
git clone https://github.com/coolman44932-bot/whole-launcher
cd whole-launcher
python build_release.py
```

This writes `dist/Whole Launcher <version>.zip`. The version comes from `VERSION` in
`launcher/whole_launcher.py`. Each tag rebuilds that version's file on Nexus with byte-identical contents (`v1.0.0`,
`v1.1.0`).

## Running it from source

```
python launcher/whole_launcher.py
```

Or build the zip, unzip it anywhere and double-click `Whole Launcher.bat`. The `.bat` only looks
for an installed Python (`py -3` or `pythonw`) and starts `files/whole_launcher.py` with it. If
there is no Python, it prints where to get one.

## What it does to your PC

- **No network access.** The code makes no internet connections and downloads nothing.
- **No code injection.** No DLLs, no hooks and no memory patching. It only edits data files
  the game reads anyway.
- **In the game folder** (`camel/sdf/pc/data/`):
  - It backs up the game's archive index `sdf.sdftoc` once, as `sdf.sdftoc.before-mods`.
  - It writes all mods into one new archive, `sdf-C-2999.sdfdata`, and points the index at it.
  - The game's own archives are never modified.
  - Uninstall restores the original index and deletes the new archive.
- **Finding the game:** it reads the Steam and Ubisoft Connect install paths from the
  registry. This is read-only; it never writes the registry.
- **Is the game running?** Before installing, it checks whether the game is running. It reads
  the process list through the Windows API (`CreateToolhelp32Snapshot` via `ctypes`, read-only),
  falling back to `tasklist`.
- **Play:** a Steam copy starts through `steam://rungameid/...`, otherwise it runs the game's
  own exe. "Mods folder" and "Open mod folder" open the folder in Explorer.
- **Mods with generator scripts:** mods can include small Python scripts
  (`<mod>/generators/*.py`). The launcher runs them at install time to build the mod's changes
  from the player's own game files, so no game files are ever redistributed. The launcher lists
  which mods have them, and they are plain-text files anyone can read.

## Layout

- `launcher/whole_launcher.py`: the tkinter window (mod list, install, uninstall, play).
- `tfbw/sdf.py`: reads and writes the game's archive index and data files.
- `tfbw/bvm.py`, `tfbw/graph.py`: the game's binary script format and its node graphs.
- `tfbw/juice.py`, `tfbw/locpack.py`: the game's text data and string tables.
- `tfbw/mods.py`, `tfbw/pack.py`, `tfbw/engine.py`: mod loading, merging, packing, installing.
- `tfbw/gamefind.py`: finds the game folder (Steam or Ubisoft Connect).
- `tfbw/recipes.py`: helpers that mod generator scripts can use (status effects, gates, new
  artifacts with icons, shop entries).
- `tfbw/dds.py`: writes DDS textures (item icons) for generator scripts.

## Changes

### 1.1.0

- **Mods can build on each other.** Generator scripts now see the files earlier mods'
  generators made (in load order), so two mods can add to the same game script instead of
  one replacing the other. `toc.vanilla` still gives the untouched game files.
- **New strings.** A mod's `loc/*.tsv` can add brand-new text (for a new item) with a `+` in
  front of the string id. Unknown ids without `+` are still reported as errors.
- **Line-break fix.** The game silently ignores its text data files (`.juice`, `.mitem` and
  others) when they have Unix line breaks. The launcher now converts them to Windows line
  breaks while packing and says so in the log.
- **New helpers for mod makers** in `tfbw/recipes.py`: brand-new artifacts (item, stats,
  icon, shop listings), artifact-equipped and enemy-only gates, statuses that trigger other
  statuses, and effects when an enemy dies. `tfbw/dds.py` is new for item icons.
- `Whole Launcher.bat` also works when it sits next to `whole_launcher.py`.
- `MODDING.md` documents new strings and generators that build on earlier mods.

### 1.0.0

- First release.
