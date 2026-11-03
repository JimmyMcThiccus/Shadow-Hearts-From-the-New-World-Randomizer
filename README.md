# Shadow Hearts: From the New World Randomizer

Randomizes Shadow Hearts: From the New World (USA, SLUS-213.26) and writes a new disc image plus a spoiler
log. The clean image is only read, never changed.

## Run

Download `ShadowHeartsFromTheNewWorldRandomizer.exe` from the Releases page and run it. You need your own
Shadow Hearts: From the New World (USA) disc image; settings are saved in `settings.json` beside the exe.

From source:

```bash
.venv/Scripts/pythonw.exe run_gui.pyw
```

or from the command line:

```bash
.venv/Scripts/python.exe -m sh3rando.cli "<clean image>.iso" --out <folder> [--code SH3-...] [--seed S] [--dry-run]
```

`--dry-run` writes only the spoiler log. Run from source, the settings file sits at the repo root.

Setup once: `C:/Python314/python.exe -m venv .venv` then `.venv/Scripts/python.exe -m pip install -r requirements.txt`.
Build the exe: `.venv/Scripts/python.exe build.py` (writes `dist/ShadowHeartsFromTheNewWorldRandomizer.exe`).

## What it changes

| Option | Where | Notes |
|---|---|---|
| Chests | `TRES` table in `FIELD/FIELD.DAT` | the chest scripts read a chest's item from its row, so the "You got X" message follows |
| Gifts | `GetItem(code, n)` calls in `FIELD/MPS/*.MSC` | items people and events hand over; the same event in variant maps gets the same item |
| Shops | `FIELD/SHOP/_SHOP.DAT` | same slots per shop; only items with a buy price are stocked; key items for sale stay |
| Drops | `MON_` records in `COMMON/SH3RDAT.NA` (+0x20/+0x22 item, +0x24/+0x25 chance) | both drop slots |
| Monster stats | `MON_` HP, MP, the six battle stats, EXP, cash | placeholder rows (1 HP) are skipped |
| Encounters (experimental) | `MON_` monster sets in `COMMON/SH3RDAT.NA` | only sets that maps roll as random encounters (map `MONSET` chunks) and no scripted battle (`BBTL`) uses; each monster becomes one of similar HP rank |

Never moved: key items, totem statues, inspirations, coins, Lottery Tickets, the Ring Fragment, the Third /
Fifth / Seventh / Eternal Keys, and every item a script checks or takes away by code (the crest spells, the
Legend Saber, the Hooligan Lantern ...). Magic and Stellar Charts only trade places with each other, so every
spell and chart stays obtainable.

The settings code (`SH3-<options>-<seed>`) reproduces the same image. Each category has its own random
stream, so turning one off never changes the others.

## How the image is written

The map scripts are stored `cx`-compressed; edited ones are recompressed by `sh3rando/cx.py` (an encoder for
the game's LZ format, checked against the game's decoder) and come out no larger than the originals, so every
changed file is written back in place. Each one is read back and compared.

Item numberings, table layouts and the script format are documented at the top of `sh3rando/gamedata.py`,
`sh3rando/scripts.py` and `sh3rando/cx.py`.
