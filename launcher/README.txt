WHOLE LAUNCHER - mod manager for South Park: The Fractured But Whole
=====================================================================
by Coolman559654

Install mods with one click, switch whole overhauls on and off, and put the game back
exactly as it was whenever you want.

NEEDS
  - South Park: The Fractured But Whole (Steam or Ubisoft Connect, legit copy)
  - Python 3.8 or newer, free from https://www.python.org/downloads/
    (tick "Add python.exe to PATH" when installing)

HOW TO USE
  1. Unzip Whole Launcher anywhere (not inside the game folder).
  2. Double-click "Whole Launcher.bat". It finds the game by itself; if not, press Change...
     and pick the game folder (the one with SouthPark_TFBW.exe).
  3. Add mods: "Add mod (.zip)...", or put mod folders in the mods folder.
  4. Tick the mods you want. Lower in the list wins when two mods change the same thing.
  5. Close the game, press "Install mods", then "Play".
  To remove everything: "Uninstall all".

WHAT IT DOES TO YOUR GAME
  - Backs up the game's archive index once (camel\sdf\pc\data\sdf.sdftoc.before-mods).
  - Writes your mods into ONE new archive (camel\sdf\pc\data\sdf-C-2999.sdfdata) and points
    the index at it. The game's own archives are never modified.
  - Everything is built and verified in a temp folder before your game is touched.
  - Uninstall restores the original index and deletes the new archive.
  No DLLs, no injection: it only changes data files the game reads anyway.
  If something ever looks wrong, "Verify integrity of game files" in Steam fixes it too.

GOOD TO KNOW
  - Close the game before installing or uninstalling.
  - Some mods run small Python scripts at install time to build their changes from YOUR game
    files (so no game files are ever uploaded). The launcher shows which mods do this; the
    scripts are plain text in each mod's generators folder if you want to read them.
  - Overhauls: mods can belong to an overhaul and take over a class. Two overhauls can't own
    the same class; pick one in the "Overhaul" box and press "Use this overhaul".
  - FractureLoader: both tools change how the game loads files. Use one or the other for now.

FOR MODDERS
  See MODDING.md: tweak files that stack with other mods, text patches, overhaul slots and
  generator scripts.

Not affiliated with Ubisoft, Massive Entertainment, or South Park Studios.
Modding may break the game's EULA; use at your own risk.
