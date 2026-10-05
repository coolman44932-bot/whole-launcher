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

## Generators (generators/*.py): edit node graphs without shipping game files

Graphs (`.mskillscriptgraph` and other BVM files) are whole game files, so instead of
uploading an edited copy, ship a script. At install time the launcher calls:

```python
def build(toc, mod_dir):
    ...
```

- `toc` is the player's original game index (`toc.read(toc.get(path))` gives a file's bytes).
- Write results into `mod_dir`, for example with `tfbw.recipes.save(root, mod_dir, game_path)`.
  That saves `mod_dir/<game path>.json`, which the packer compiles back to binary.
- Available modules: `tfbw.bvm` (read/write BVM), `tfbw.graph` (nodes and wires),
  `tfbw.recipes` (proven patterns: apply a status, enemies-only gate, status steps in
  animations), `tfbw.juice`, `tfbw.sdf`.
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
