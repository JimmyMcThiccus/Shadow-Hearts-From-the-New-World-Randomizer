"""The game tables the randomizer touches, read from (and written back to) the US disc files.

COMMON/SH3RDAT.NA (SLUS_213.26 FUN_002a9120 loads "common/sh3rdat.na"; FUN_002a9680 = GetRegDat(section,
index)) is a container {u32 count, u32 total size, count x {tag4, u32 offset}} of 78 sections.  A tag appears up
to three times: first its 18-byte names, then its descriptions, then its records (MON_ has a fourth section, the
monster sets).  Every record starts {u16 id, s16 name index} (u8 / u8 in ARM_, KEYI and WPN_).

Inventory kinds (FIELD.CPX TreasureControl 0x435da0 -> FUN_002fa3c0 -> SLUS FUN_00348c50; the overlay's kind ->
section table at 0x520d88 names them):
  1 item, 2 weapon, 3 armor, 4 accessory, 5 magic, 6 stellar chart, 7 ring item, 8 inspiration (bit set),
  9 coin, 10 totem statue (bit set), 11 key item (bit set)
  script code (GetItem / TreasureControl(5, code, n)) = kind << 8 | id
Here an item is (kind, id).

FIELD/FIELD.DAT is a "DTCH" container; its TRES chunk is the treasure table that the field scripts index with
TreasureControl(1/2/4, n) (code / name / take), so a chest's "You got X" message follows its row.  Rows: u8 id,
u8 kind | flags (TreasureControl masks 0x3f; 0x40 is set on every item-like row and clear on the magic and
stellar chart rows), u16 count.  BBTL rows are the scripted battles: u16 ?, u16 BGM, u16 monster set, u16 flags.

FIELD/SHOP/_SHOP.DAT (FIELD.CPX FUN_0048f710 parses it): its first "dat" section is u32 0, u32 shop count, 8
bytes, then one 0x90-byte record per shop (+0x80 u32 goods count) and the goods of each shop in turn, 16 bytes
each {u32 kind, u32 id, 8 bytes 0}.  FUN_0048f860 reads the prices: buy / sell at ITEM +0x24 / +0x28, WPN_ and
ARM_ +0x0c / +0x10, ACS_ +0x20 / +0x24, JRCI +0x1c / +0x20, KEYI +0x04 / +0x08.
"""
import struct

KINDS = {1: "item", 2: "weapon", 3: "armor", 4: "accessory", 5: "magic", 6: "stellar chart", 7: "ring item",
         9: "coin"}
KIND_MAGIC, KIND_CHART, KIND_KEY = 5, 6, 11
# kind: (SH3RDAT tag, record size, buy price offset or None)
TABLES = {
    1: (b"ITEM", 0x34, 0x24),
    2: (b"WPN_", 0x34, 0x0C),
    3: (b"ARM_", 0x18, 0x0C),
    4: (b"ACS_", 0x2C, 0x20),
    5: (b"MAGI", 0x48, None),
    6: (b"SC_N", None, None),
    7: (b"JRCI", 0x28, 0x1C),
    8: (b"INSP", None, None),
    9: (b"COIN", None, None),
    10: (b"TOTE", None, None),
    11: (b"KEYI", 0x0C, 0x04),
}
BAD_NAMES = {"", "Unknown", "?", "x"}
THERA_LEAF = (1, 0)

MON_SIZE = 0x44
SET_SIZE = 0x20                                     # u16 id, u16 formation, s16 monsters[6], 12 bytes, u32 flags


class RegFile:
    """SH3RDAT container; sections are bytearray slices that keep their sizes."""

    def __init__(self, data):
        n, total = struct.unpack_from("<II", data, 0)
        assert total == len(data), "unexpected container size"
        self.raw = bytearray(data)
        self.tags = [bytes(data[8 + 8 * i:12 + 8 * i]) for i in range(n)]
        offs = [struct.unpack_from("<I", data, 12 + 8 * i)[0] for i in range(n)] + [total]
        self.spans = [(offs[i], offs[i + 1]) for i in range(n)]

    def section(self, tag, nth=0):
        """(offset, size) of the nth section with this tag; nth=-1 is the last (the records)."""
        i = [k for k, t in enumerate(self.tags) if t == tag][nth]
        a, b = self.spans[i]
        return a, b - a

    def names(self, tag):
        off, size = self.section(tag, 0)
        return [bytes(self.raw[off + 18 * i:off + 18 * i + 18]).split(b"\0")[0].decode("cp932", "replace")
                for i in range(size // 18)]

    def bytes(self):
        return bytes(self.raw)


# MON_ records (battle overlay FUN_00049ca0 keeps the record; FUN_0004c910 / FUN_0004cbc0 read HP / MP, the
# six stat functions FUN_0004def0 .. FUN_0004e8e0 read +0x0c .. +0x16).  Drops: SLUS FUN_0032d430 hands each
# dropped code to FUN_00348c50 as kind = code / 100, id = code % 100.
MON_MP, MON_HP, MON_STATS = 0x06, 0x08, 0x0C        # u16, u32, six u16
MON_DROP, MON_RATE = 0x20, 0x24                     # two s16 drop codes, two u8 chances (%)
MON_EXP, MON_CASH = 0x2C, 0x30                      # u32, u32


def drop_to_item(code):
    return None if code < 100 else (code // 100, code % 100)


def item_to_drop(it):
    kind, i = it
    assert 1 <= kind <= 11 and 0 <= i < 100, it
    return kind * 100 + i


def code_to_item(code):
    return (code >> 8, code & 0xFF)


class Monster:
    def __init__(self, gd, index):
        self.index, self.off = index, gd.mon_off + index * MON_SIZE
        r = gd.dat.raw
        nm = struct.unpack_from("<h", r, self.off + 2)[0]
        self.name = gd.mon_names[nm] if 0 <= nm < len(gd.mon_names) else ""
        self.mp = struct.unpack_from("<H", r, self.off + MON_MP)[0]
        self.hp = struct.unpack_from("<I", r, self.off + MON_HP)[0]
        self.stats = list(struct.unpack_from("<6H", r, self.off + MON_STATS))
        self.drops = list(struct.unpack_from("<2h", r, self.off + MON_DROP))
        self.rates = list(struct.unpack_from("<2B", r, self.off + MON_RATE))
        self.exp, self.cash = struct.unpack_from("<II", r, self.off + MON_EXP)

    @property
    def real(self):
        return self.hp > 1 and self.name not in BAD_NAMES


class Shop:
    def __init__(self, index, name, off, goods):
        self.index, self.name, self.off, self.goods = index, name, off, goods


class GameData:
    def __init__(self, rdat, field_dat, shop_dat):
        self.dat = RegFile(rdat)
        self.fdat = bytearray(field_dat)
        self.sdat = bytearray(shop_dat)
        self._read_tables()
        self.mon_names = self.dat.names(b"MON_")
        self.mon_off, n = self.dat.section(b"MON_", 1)
        self.monsters = [Monster(self, i) for i in range(n // MON_SIZE)]
        self.set_off, n = self.dat.section(b"MON_", 2)
        self.sets = [list(struct.unpack_from("<HH6h", self.dat.raw, self.set_off + i * SET_SIZE))
                     for i in range(n // SET_SIZE)]
        self._read_field_dat()
        self._read_shops()

    # --- names and records -------------------------------------------------------------------------
    def _read_tables(self):
        self.records = {}                     # kind -> [(name, buy price)]
        for kind, (tag, size, price_off) in TABLES.items():
            names = self.dat.names(tag)
            if size is None:
                self.records[kind] = [(nm, -1) for nm in names]
                continue
            off, total = self.dat.section(tag, -1)
            recs = []
            for i in range(total // size):
                o = off + i * size
                ni = i
                if kind == KIND_KEY:                       # u8 id, u8 name index (0xff: unnamed slot)
                    ni = -1 if self.dat.raw[o + 1] == 0xFF else self.dat.raw[o + 1]
                nm = names[ni] if 0 <= ni < len(names) else ""
                price = struct.unpack_from("<i", self.dat.raw, o + price_off)[0] if price_off is not None else -1
                recs.append((nm, price))
            self.records[kind] = recs

    def item_name(self, it):
        if it is None:
            return "(nothing)"
        kind, i = it
        recs = self.records.get(kind)
        if recs and 0 <= i < len(recs) and recs[i][0]:
            return recs[i][0]
        return "kind %d #%d" % (kind, i)

    def item_valid(self, it):
        kind, i = it
        recs = self.records.get(kind)
        return bool(recs) and 0 <= i < len(recs) and recs[i][0] not in BAD_NAMES

    def item_price(self, it):
        kind, i = it
        recs = self.records.get(kind)
        return recs[i][1] if recs and 0 <= i < len(recs) else -1

    def kind_name(self, kind):
        return KINDS.get(kind, "kind %d" % kind)

    # --- monsters ----------------------------------------------------------------------------------
    def write_monster(self, m):
        r, o = self.dat.raw, m.off
        clamp = lambda v, hi: max(0, min(hi, int(v)))
        struct.pack_into("<H", r, o + MON_MP, clamp(m.mp, 9999))
        struct.pack_into("<I", r, o + MON_HP, max(2, clamp(m.hp, 999999)))
        struct.pack_into("<6H", r, o + MON_STATS, *(clamp(s, 999) for s in m.stats))
        struct.pack_into("<2h", r, o + MON_DROP, *m.drops)
        struct.pack_into("<2B", r, o + MON_RATE, *(clamp(x, 100) for x in m.rates))
        struct.pack_into("<II", r, o + MON_EXP, clamp(m.exp, 99999999), clamp(m.cash, 99999999))

    def write_set(self, i):
        struct.pack_into("<HH6h", self.dat.raw, self.set_off + i * SET_SIZE, *self.sets[i])

    # --- FIELD.DAT: treasure and scripted battles --------------------------------------------------
    def _read_field_dat(self):
        d = self.fdat
        assert d[:4] == b"DTCH", "FIELD.DAT is not a DTCH container"
        self.chunks = {}
        p = 0x10
        for _ in range(struct.unpack_from("<H", d, 4)[0]):
            tag = bytes(d[p:p + 4])
            units, elem, n = struct.unpack_from("<HHH", d, p + 6)
            self.chunks[tag] = (p + 0x10, elem, n)
            p += 0x10 + units * 16
        off, elem, n = self.chunks[b"TRES"]
        assert elem == 4
        self.tres_off = off
        self.tres = [list(struct.unpack_from("<BBH", d, off + 4 * i)) for i in range(n)]
        off, elem, n = self.chunks[b"BBTL"]
        assert elem == 8
        self.bbtl_sets = {struct.unpack_from("<H", d, off + 8 * i + 4)[0] for i in range(n)}

    @staticmethod
    def tres_item(row):
        return (row[1] & 0x3F, row[0])

    def write_tres(self, i):
        struct.pack_into("<BBH", self.fdat, self.tres_off + 4 * i, *self.tres[i])

    # --- _SHOP.DAT: shops --------------------------------------------------------------------------
    def _read_shops(self):
        s = self.sdat
        n, total = struct.unpack_from("<II", s, 0)
        tags = [bytes(s[8 + 8 * i:12 + 8 * i]) for i in range(n)]
        offs = [struct.unpack_from("<I", s, 12 + 8 * i)[0] for i in range(n)] + [total]
        k = tags.index(b"dat\0")
        base, end = offs[k], offs[k + 1]
        count = struct.unpack_from("<I", s, base + 4)[0]
        p = base + 16 + count * 0x90
        self.shops = []
        for i in range(count):
            rec = base + 16 + i * 0x90
            name = bytes(s[rec:rec + 0x40]).split(b"\0")[0].decode("cp932", "replace")
            goods = [struct.unpack_from("<II", s, p + 16 * j)
                     for j in range(struct.unpack_from("<I", s, rec + 0x80)[0])]
            assert all(1 <= kk <= KIND_KEY for kk, _ in goods), "shop %d does not look like a goods list" % i
            self.shops.append(Shop(i, name, p, goods))
            p += 16 * len(goods)
        assert p == end, "_SHOP.DAT goods do not end with the section"

    def write_shop(self, shop):
        for i, (k, g) in enumerate(shop.goods):
            struct.pack_into("<II", self.sdat, shop.off + 16 * i, k, g)
