"""Command line: python -m sh3rando.cli <clean image.iso> [--out DIR] [--code SH3-...] [--seed S] [--dry-run]"""
import argparse
import os
import random
import sys

from . import randomize as R
from .disc import Disc


def safe_seed(seed):
    return "".join(ch for ch in seed if ch.isalnum() or ch in "-_") or "seed"


def out_name(seed):
    return "Shadow Hearts - From the New World (USA) [Rando %s].iso" % safe_seed(seed)


def spoiler_name(seed):
    return "Shadow Hearts - From the New World (USA) [Rando %s] spoiler.txt" % safe_seed(seed)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Shadow Hearts: From the New World (USA) randomizer")
    ap.add_argument("iso", help="clean Shadow Hearts: From the New World (USA) disc image")
    ap.add_argument("--out", default=".", help="folder for the randomized image and the spoiler log")
    ap.add_argument("--code", help="settings code from the GUI or a spoiler log")
    ap.add_argument("--seed", help="seed (default: random)")
    ap.add_argument("--dry-run", action="store_true", help="only write the spoiler log, no disc image")
    a = ap.parse_args(argv)
    s = R.Settings.from_code(a.code) if a.code else R.Settings()
    if a.seed:
        s.seed = a.seed
    if not s.seed:
        s.seed = "%08d" % random.randrange(10 ** 8)
    R.identify(a.iso)
    os.makedirs(a.out, exist_ok=True)
    last = [None]

    def progress(f, msg):
        line = "%3d%% %s" % (f * 100, msg)
        if line != last[0]:
            print("\r%-40s" % line, end="", flush=True)
            last[0] = line

    rz = R.Randomizer(s, progress)
    if a.dry_run:
        d = Disc(a.iso)
        try:
            rz.load(d)
        finally:
            d.close()
        rz.randomize()
        log = rz.log.text("Shadow Hearts: From the New World Randomizer (dry run)\nCode: %s\nFiles changed: %s\n" % (
            s.to_code(), ", ".join(rz.changed_paths()) or "none"))
        out = None
    else:
        out = os.path.join(a.out, out_name(s.seed))
        if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(a.iso)):
            sys.exit("the output would overwrite the clean image")
        log = rz.run(a.iso, out)
        print()
    spoiler = os.path.join(a.out, spoiler_name(s.seed))
    with open(spoiler, "w", encoding="utf-8") as f:
        f.write(log)
    print("\nwrote", ", ".join(([out] if out else []) + [spoiler]))
    print("code", s.to_code())


if __name__ == "__main__":
    sys.exit(main())
