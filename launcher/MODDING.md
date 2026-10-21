# Making mods for Whole Launcher

A mod is a folder in `mods/`. Everything in it is optional except a `manifest.ini`.

```
my-mod/
  manifest.ini
  something.tweak            value changes that stack with other mods
  loc/english/menus.tsv      text changes (names, descriptions)
  generators/my_script.py    script edits built from the player's own game
  camel/...                  whole-file replacements (FractureLoader style)
```

## manifest.ini

```
name=My Mod
author=you
version=1.0.0
description=One line shown in the launcher.
collection=warhammer-1      (optional) the overhaul this mod belongs to
class=brutalist             (optional) classes this mod takes over, comma separated
```

Two different collections can't take over the same class; the launcher refuses to install
that combination. Mods in the same collection may share a class.

## Tweaks (*.tweak): change single values

```
[camel/game system data/juice/powers/playerpowers.juice]
# Brutal Bash hits 3 wide
pwr_brick_power1_Tuning.myDamageArea.myWidth = 3
# add a status to a power (the {uid} becomes a fresh object id)
pwr_brick_power1_Tuning.myStatusEffects += StatusEffect StatusEffect {uid} = Bleeding
# load a definition the file doesn't load yet
include camel/game system data/juice/statuseffect/bleeding.juice
```

- A path is the object name, then keys: `Object.key.subkey`. Use `[n]` when several entries
  share a key, and quote names with spaces.
- Only that value changes, so two mods tweaking different values of one file both apply.
- `+=` adds a line inside a block, creating the block if it is missing.

## Text (loc/<language>/<package>.tsv)

```
51C546C65774D95E000000E34ACEB730	Axe of Khorne
```

The string id is in the juice file next to the text (`guid = #...`). Respect its
`maxLength`.

A brand-new string (for a new item, say) starts with `+`. Pick your own 32-hex-digit id
and use the same id in the juice file that shows it:

```
+57C3D0B1A5E14C29000000F2D4E8B6A3	Censer of the Plaguefather
```

## Generators (generators/*.py): edit node graphs without shipping game files

Graphs (`.mskillscriptgraph` and other BVM files) are whole game files, so instead of
uploading an edited copy, ship a script. At install time the launcher calls:

```python
def build(toc, mod_dir):
    ...
```

- `toc` is the player's game index (`toc.read(toc.get(path))` gives a file's bytes). It
  already includes what earlier mods' generators made (load order), so two mods can patch
  the same graph: read the file through `toc`, add your nodes, save. `toc.vanilla` is the
  untouched index, for copying template nodes.
- Write results into `mod_dir`, for example with `tfbw.recipes.save(root, mod_dir, game_path)`.
  That saves `mod_dir/<game path>.json`, which the packer compiles back to binary.
- Available modules: `tfbw.bvm` (read/write BVM), `tfbw.graph` (nodes and wires),
  `tfbw.recipes` (proven patterns: apply a status, enemies-only gate, artifact-equipped
  gate, status steps in animations), `tfbw.juice`, `tfbw.sdf`.
- A generator may also write new game files (a new item's `.mitem`, its icon PNG) and
  whole text files (append a block to `artifacts.juice`); other mods' tweaks still apply
  on top of a replaced text file.
- Copy the game's own nodes instead of building them from scratch. Every recipe does this.

## Lessons learned the hard way

- A power's statuses only land if its animation or script runs the game's status
  application step. Check the `.manimevent`, or use `recipes.add_status_event`.
- Turning a ranged power's area into a Centered one: set `myStartWidth` and `myStartLength`
  to 0, or the targeted tile is left out.
- A power's `mySelfStatusEffects` only applies one entry. Chain more through a status's
  OnApply script.
- Some objects exist in two files and only one is loaded. The packer warns about that.
- Juice text can be a placeholder. The real text is in `menus.locpackbin`.
