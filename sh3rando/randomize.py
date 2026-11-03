"""Seeded randomization of the Shadow Hearts: From the New World (USA) disc.

Each category draws from its own RNG (seed + category name), so switching one category on or off never
changes what another produces.  Key items and the other one-off bit-set kinds (inspirations, totem statues),
coins, Lottery Tickets, the numbered keys and every item a script checks or takes away are never moved.  Magic
and stellar charts are one-offs that only trade places with each other's slots: every spell and chart stays
obtainable as often as before.
"""
import base64
import collections
import dataclasses
import hashlib
import random
import re
import struct

from . import cx
from . import gamedata as G
from . import scripts as S
from .disc import Disc, copy_image

VERSION = 1
SLUS = "SLUS_213.26"
ONE_OFF = (G.KIND_MAGIC, G.KIND_CHART)  # trade places among their own slots, never drawn at random
STACKABLE = (1, 7)                      # items and ring items keep a chest's count; the rest are 1
# Never moved (besides the items scripts check, found in the scripts themselves)
KEEP_NAMES = re.compile(r"^(Lottery Ticket|Ring Fragment|Third Key|Fifth Key|Seventh Key|Eternal Key)$")
RDAT, FDAT, SHOP = "COMMON/SH3RDAT.NA", "FIELD/FIELD.DAT", "FIELD/SHOP/_SHOP.DAT"


@dataclasses.dataclass
class Settings:
    seed: str = ""
    # chests and gifts
    chests: bool = True
    chest_mode: str = "shuffle"        # shuffle: swap existing contents | random: any item
    chest_mix_kinds: bool = True       # a weapon chest may hold an item, and so on
    gifts: bool = True                 # items scripts hand over (NPCs, events) join the chest pool
    # shops
    shops: bool = True
    shop_mode: str = "kind"            # kind: same kind per slot | any: any kind
    shop_keep_healing: bool = True     # every shop that sold Thera Leaf still does
    # monster drops
    drops: bool = True
    drop_mix_kinds: bool = True
    drop_fill_empty: bool = False      # monsters that dropped nothing get a drop too
    drop_rate: int = 100               # percent of the original chance
    # monster stats
    stats: bool = False
    stat_variance: int = 20            # +/- percent on HP, MP and the six battle stats
    cash_rate: int = 100               # percent of the original cash
    exp_rate: int = 100                # percent of the original EXP
    # encounters (experimental)
    encounters: bool = False
    encounter_spread: int = 10         # how far (in the monster strength ranking) a swap may reach

    # The code packs every option: version, a flag word, then the numbers.
    _FLAGS = ("chests", "chest_mix_kinds", "gifts", "shops", "shop_keep_healing", "drops", "drop_mix_kinds",
              "drop_fill_empty", "stats", "encounters")
    _CHOICES = (("chest_mode", ("shuffle", "random")), ("shop_mode", ("kind", "any")))
    _NUMBERS = (("drop_rate", "H"), ("stat_variance", "B"), ("cash_rate", "H"), ("exp_rate", "H"),
                ("encounter_spread", "B"))
    _FMT = "<BH" + "".join(f for _, f in _NUMBERS)

    def to_code(self):
        bits = 0
        for i, k in enumerate(self._FLAGS):
            bits |= bool(getattr(self, k)) << i
        for j, (k, opts) in enumerate(self._CHOICES):
            bits |= opts.index(getattr(self, k)) << (len(self._FLAGS) + j)
        blob = struct.pack(self._FMT, VERSION, bits, *(getattr(self, k) for k, _ in self._NUMBERS))
        return "SH3-%s-%s" % (base64.urlsafe_b64encode(blob).decode().rstrip("="), self.seed)

    @classmethod
    def from_code(cls, code, allow_old=False):
        """allow_old: accept an older version's code for its options only; used to restore the last
        settings, never to reproduce an old seed."""
        m = re.fullmatch(r"SH3-([A-Za-z0-9_-]{15})-(.*)", code.strip())
        if not m:
            raise ValueError("not a Shadow Hearts: From the New World randomizer code")
        b64 = m.group(1)
        try:
            vals = struct.unpack(cls._FMT, base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
        except (struct.error, ValueError):
            raise ValueError("the code is damaged")
        if vals[0] != VERSION and not (allow_old and 1 <= vals[0] < VERSION):
            raise ValueError("code is from randomizer version %s, this is %s" % (vals[0], VERSION))
        s = cls(seed=m.group(2))
        for i, k in enumerate(cls._FLAGS):
            setattr(s, k, bool(vals[1] >> i & 1))
        for j, (k, opts) in enumerate(cls._CHOICES):
            setattr(s, k, opts[vals[1] >> (len(cls._FLAGS) + j) & 1])
        for (k, _), v in zip(cls._NUMBERS, vals[2:]):
            setattr(s, k, v)
        return s


def rng(settings, category):
    h = hashlib.sha256(("%s/%s" % (settings.seed, category)).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


class Log:
    def __init__(self):
        self.sections = collections.OrderedDict()

    def add(self, section, line):
        self.sections.setdefault(section, []).append(line)

    def text(self, header):
        out = [header, ""]
        for k, v in self.sections.items():
            out += ["== %s ==" % k] + v + [""]
        return "\n".join(out)


class Slot:
    """One place an item comes from: a TRES row, a script gift group or a monster drop."""

    def __init__(self, item, where, apply):
        self.item, self.where, self.apply = item, where, apply


class Script:
    """A map script as stored on the disc (raw, maybe cx-compressed) and decoded (msc)."""

    def __init__(self, path, raw):
        self.path, self.raw, self.packed = path, raw, cx.is_cx(raw)
        self.msc = S.Msc(path, cx.decompress(raw) if self.packed else raw)
        self.plain = bytes(self.msc.raw)

    def changed(self):
        return bytes(self.msc.raw) != self.plain

    def disc_bytes(self):
        return cx.compress(bytes(self.msc.raw)) if self.packed else bytes(self.msc.raw)


class Randomizer:
    def __init__(self, settings, progress=None):
        self.s = settings
        self.progress = progress or (lambda frac, msg: None)
        self.log = Log()

    # -- reading -------------------------------------------------------------------------------------
    def load(self, disc):
        self.gd = G.GameData(disc.read(RDAT), disc.read(FDAT), disc.read(SHOP))
        paths = sorted(x for x in disc.files if x.startswith("FIELD/MPS/") and x.endswith(".MSC"))
        self.scripts = []
        for n, p in enumerate(paths):
            self.progress(0.01 * n / len(paths), "Reading the map scripts")
            self.scripts.append(Script(p, disc.read(p)))
        self.encounter_maps = collections.defaultdict(set)      # monster set -> maps that roll it
        if self.s.encounters:
            maps = sorted(x for x in disc.files if x.startswith("FIELD/MPS/") and x.endswith(".MPS"))
            for n, p in enumerate(maps):
                self.progress(0.01 + 0.01 * n / len(maps), "Reading the maps")
                raw = disc.read(p)
                data = cx.decompress(raw) if cx.is_cx(raw) else raw
                for st, _w in map_encounters(data):
                    self.encounter_maps[st].add(p.rsplit("/", 1)[-1][:-4])
        gd = self.gd
        self.fixed = {it for it in S.quest_items([s.msc for s in self.scripts]) if it[0] in G.KINDS} | {
            (k, i) for k in G.KINDS for i, (nm, _p) in enumerate(gd.records[k]) if KEEP_NAMES.match(nm)}
        self.originals = {RDAT: gd.dat.bytes(), FDAT: bytes(gd.fdat), SHOP: bytes(gd.sdat)}

    def movable(self, it):
        return it is not None and it[0] in G.KINDS and self.gd.item_valid(it) and it not in self.fixed

    def _pools(self, slots_items):
        """Per kind, every movable item the vanilla game hands out somewhere (chests, gifts, shops, drops),
        magic and charts excepted: they are never drawn at random."""
        gd = self.gd
        seen = set(slots_items)
        seen |= {gd.tres_item(r) for r in gd.tres}
        seen |= {g for sh in gd.shops for g in sh.goods}
        seen |= {G.drop_to_item(c) for m in gd.monsters for c in m.drops}
        pools = collections.defaultdict(list)
        for it in sorted(x for x in seen if self.movable(x) and x[0] not in ONE_OFF):
            pools[it[0]].append(it)
        return pools

    def _draw(self, r, pools, kind, mix, avoid=()):
        kinds = [k for k in sorted(pools) if pools[k]]
        if mix or kind not in kinds:
            kind = r.choices(kinds, weights=[len(pools[k]) for k in kinds])[0]
        for _ in range(50):
            it = r.choice(pools[kind])
            if it not in avoid:
                return it
        return it

    def _assign(self, r, slots, mode, mix, pools):
        """New items for slots: magic and charts trade places among their own slots; the rest are shuffled
        (optionally within their kind) or drawn from the pools."""
        one = [s for s in slots if s.item[0] in ONE_OFF]
        other = [s for s in slots if s.item[0] not in ONE_OFF]
        new = {}
        os_ = [s.item for s in one]
        r.shuffle(os_)
        new.update(zip(map(id, one), os_))
        if mode == "shuffle":
            items = [s.item for s in other]
            if mix:
                r.shuffle(items)
                new.update(zip(map(id, other), items))
            else:
                by_kind = collections.defaultdict(list)
                for it in items:
                    by_kind[it[0]].append(it)
                for v in by_kind.values():
                    r.shuffle(v)
                new.update((id(s), by_kind[s.item[0]].pop()) for s in other)
        else:
            new.update((id(s), self._draw(r, pools, s.item[0], mix)) for s in other)
        return [new[id(s)] for s in slots]

    # -- categories ----------------------------------------------------------------------------------
    def do_chests(self):
        gd, r = self.gd, rng(self.s, "chests")
        where = collections.defaultdict(set)
        for sc in self.scripts:
            for row in S.chest_rows(sc.msc):
                where[row].add(sc.msc.name)
        slots, kept = [], []
        for i, row in enumerate(gd.tres):
            it = gd.tres_item(row)
            if not it[0]:
                continue                                         # empty row
            maps = " ".join(sorted(where.get(i, ()))) or "map object"
            if self.movable(it):
                slots.append(Slot(it, "chest %3d  %s" % (i, maps), ("tres", i)))
            else:
                kept.append("chest %3d  %-22s %s" % (i, gd.item_name(it), maps))
        if self.s.gifts:
            groups = collections.OrderedDict()                   # (code, ip): the same event in variant maps
            for sc in self.scripts:
                for g in S.gifts(sc.msc):
                    groups.setdefault((g.code, g.ip), []).append(g)
            for (code, ip), gs in groups.items():
                it = gs[0].item
                maps = " ".join(sorted({g.script.name for g in gs}))
                if self.movable(it):
                    slots.append(Slot(it, "gift %05x %s" % (ip, maps), ("gifts", gs)))
                else:
                    kept.append("gift %05x %-22s %s" % (ip, gd.item_name(it), maps))
        pools = self._pools([s.item for s in slots])
        new = self._assign(r, slots, self.s.chest_mode, self.s.chest_mix_kinds, pools)
        for s, it in zip(slots, new):
            what, ref = s.apply
            if what == "tres":
                row = gd.tres[ref]
                # the 0x40 flag sits on every item-like row and on no magic or chart row; keep that pairing
                row[0], row[1] = it[1], (0 if it[0] in ONE_OFF else 0x40) | it[0]
                if it[0] not in STACKABLE:
                    row[2] = 1
                gd.write_tres(ref)
            else:
                for g in ref:
                    g.set_item(it)
            self.log.add("Chests and gifts" if self.s.gifts else "Chests",
                         "%-22s -> %-22s %s" % (gd.item_name(s.item), gd.item_name(it), s.where))
        for line in kept:
            self.log.add("Left as they were (key, quest and one-off items)", line)

    def do_shops(self):
        gd, r = self.gd, rng(self.s, "shops")
        sold = lambda it: self.movable(it) and it[0] not in ONE_OFF and gd.item_price(it) > 0
        pools = collections.defaultdict(list)
        for it in sorted({g for sh in gd.shops for g in sh.goods} | {(k, i) for k in G.KINDS
                                                                     for i in range(len(gd.records[k]))}):
            if sold(it):
                pools[it[0]].append(it)
        for shop in gd.shops:
            old = list(shop.goods)
            keep = self.s.shop_keep_healing and G.THERA_LEAF in old
            used = set(o for o in old if not sold(o))
            new = []
            for it in old:
                if not sold(it) or (keep and it == G.THERA_LEAF):
                    new.append(it)
                    continue
                avoid = used | {G.THERA_LEAF} if keep else used
                pick = self._draw(r, pools, it[0], self.s.shop_mode == "any", avoid)
                used.add(pick)
                new.append(pick)
            if self.s.shop_mode == "any":
                new.sort(key=lambda it: (it[0], it[1]))            # the shop lists goods grouped by kind
            shop.goods = new
            gd.write_shop(shop)
            self.log.add("Shops", "shop %2d: %s" % (shop.index, ", ".join(gd.item_name(it) for it in new)))

    def do_drops(self):
        gd, r = self.gd, rng(self.s, "drops")
        slots = []
        for m in gd.monsters:
            if not m.real:
                continue
            for k in (0, 1):
                it = G.drop_to_item(m.drops[k])
                if self.movable(it):
                    slots.append(Slot(it, (m, k), None))
                elif it is None and k == 0 and self.s.drop_fill_empty and m.exp > 0:
                    slots.append(Slot(None, (m, k), None))
        filled = [s for s in slots if s.item is not None]
        pools = self._pools([s.item for s in filled])
        new = dict(zip(map(id, filled), self._assign(r, filled, "random", self.s.drop_mix_kinds, pools)))
        for s in slots:
            m, k = s.where
            it = new.get(id(s)) or self._draw(r, pools, 1, self.s.drop_mix_kinds)
            m.drops[k] = G.item_to_drop(it)
            rate = m.rates[k] if s.item is not None else 10
            m.rates[k] = max(1, min(100, rate * self.s.drop_rate // 100))
            gd.write_monster(m)
            self.log.add("Drops", "%-18s %-22s -> %-22s %3d%%" % (
                m.name, gd.item_name(s.item), gd.item_name(it), m.rates[k]))

    def do_stats(self):
        gd, r = self.gd, rng(self.s, "stats")
        v = self.s.stat_variance / 100.0
        f = lambda x: int(round(x * r.uniform(1 - v, 1 + v))) if x else x
        for m in gd.monsters:
            if not m.real:
                continue
            m.hp, m.mp = f(m.hp), f(m.mp)
            m.stats = [f(x) for x in m.stats]
            m.cash = m.cash * self.s.cash_rate // 100
            m.exp = m.exp * self.s.exp_rate // 100
            gd.write_monster(m)
            self.log.add("Monster stats", "%-18s HP %6d MP %4d stats %s EXP %d cash %d" % (
                m.name, m.hp, m.mp, [min(x, 999) for x in m.stats], m.exp, m.cash))

    def do_encounters(self):
        gd, r = self.gd, rng(self.s, "encounters")
        rolled = [i for i in sorted(self.encounter_maps) if i < len(gd.sets) and i not in gd.bbtl_sets]
        normal = {x for i in rolled for x in gd.sets[i][2:] if x >= 0}
        normal = {x for x in normal if x < len(gd.monsters) and gd.monsters[x].real}
        ranked = sorted(normal, key=lambda x: (gd.monsters[x].hp, x))
        rank = {x: i for i, x in enumerate(ranked)}
        spread = max(1, self.s.encounter_spread)
        for i in rolled:
            row = gd.sets[i]
            ids = [x for x in row[2:] if x >= 0]
            if not ids or any(x not in rank for x in ids):
                continue
            swap = {}
            for x in dict.fromkeys(ids):
                lo, hi = max(0, rank[x] - spread), min(len(ranked) - 1, rank[x] + spread)
                swap[x] = r.choice(ranked[lo:hi + 1])
            row[2:] = [swap.get(x, x) if x >= 0 else x for x in row[2:]]
            gd.write_set(i)
            self.log.add("Encounters", "set %3d: %s -> %s   (%s)" % (
                i, ", ".join(gd.monsters[x].name for x in ids), ", ".join(gd.monsters[swap[x]].name for x in ids),
                " ".join(sorted(self.encounter_maps[i]))))

    # -- the whole run -------------------------------------------------------------------------------
    def randomize(self):
        s = self.s
        for on, fn in ((s.chests, self.do_chests), (s.shops, self.do_shops), (s.drops, self.do_drops),
                       (s.stats, self.do_stats), (s.encounters, self.do_encounters)):
            if on:
                fn()

    def _tables(self):
        gd = self.gd
        now = {RDAT: gd.dat.bytes(), FDAT: bytes(gd.fdat), SHOP: bytes(gd.sdat)}
        return {p: b for p, b in now.items() if b != self.originals[p]}

    def changed_paths(self):
        return sorted(list(self._tables()) + [sc.path for sc in self.scripts if sc.changed()])

    def changes(self):
        """{disc path: new bytes as stored on the disc} of every file that changed."""
        out = self._tables()
        changed = [sc for sc in self.scripts if sc.changed()]
        for n, sc in enumerate(changed):
            self.progress(0.02 + 0.08 * n / max(1, len(changed)), "Packing the map scripts")
            out[sc.path] = sc.disc_bytes()
        return out

    def original(self, path):
        return self.originals.get(path) or next(sc.raw for sc in self.scripts if sc.path == path)

    def run(self, iso, out):
        """Copies the clean image to out, patches the copy and checks it."""
        s = self.s
        self.progress(0.0, "Reading the game data")
        d = Disc(iso)
        try:
            self.load(d)
        finally:
            d.close()
        self.randomize()
        files = self.changes()
        copy_image(iso, out, lambda f: self.progress(0.1 + 0.8 * f, "Copying the disc image"))
        self.progress(0.92, "Writing the disc image")
        d = Disc(out, writable=True)
        try:
            for p, data in files.items():
                if d.read(p) != self.original(p):
                    raise ValueError("%s on the image is not the one the randomizer read; use a clean US image" % p)
                d.write(p, data)
            d.commit()
        finally:
            d.close()
        self.progress(0.97, "Checking the disc image")
        self.verify(out, files)
        self.progress(1.0, "Done")
        header = "Shadow Hearts: From the New World Randomizer\nCode: %s\nFiles changed: %s\n" % (
            s.to_code(), ", ".join(sorted(files)) or "none")
        return self.log.text(header)

    @staticmethod
    def verify(out_iso, files):
        """Re-open the written image and check every file we changed reads back as written (and that each
        repacked script still unpacks to the edited script)."""
        d = Disc(out_iso)
        try:
            for p, data in files.items():
                got = d.read(p)
                assert got == data, "%s did not read back" % p
        finally:
            d.close()


def map_encounters(data):
    """Random-encounter monster sets of one (unpacked) map file, as [(set, weight %)].  SBD container (Sh2
    Decomp notes/G/mps_format.md): +0x1c LINKHDR offset; LINKHDR +8 chunk count, +0xc first entry; 16-byte
    entries whose +0xc is the chunk header's offset from the entry; chunk header = tag[8], record count, data
    offset.  MONSET records: {u16 set, u8 weight%, u8}."""
    if data[:4] != b"SBD ":
        return []
    lh = struct.unpack_from("<I", data, 0x1C)[0]
    n, first = struct.unpack_from("<II", data, lh + 8)
    out = []
    for i in range(n):
        e = lh + first + 16 * i
        h = e + struct.unpack_from("<I", data, e + 12)[0]
        if data[h:h + 8].rstrip(b" \0") == b"MONSET":
            cnt, do = struct.unpack_from("<II", data, h + 8)
            out += [struct.unpack_from("<HB", data, h + do + 4 * k) for k in range(cnt)]
    return out


def identify(path):
    """Raise ValueError unless path is a clean-looking US image."""
    d = Disc(path)
    try:
        ok = d.slus == SLUS
    finally:
        d.close()
    if not ok:
        raise ValueError("%s is not a Shadow Hearts: From the New World (USA) disc" % path)
