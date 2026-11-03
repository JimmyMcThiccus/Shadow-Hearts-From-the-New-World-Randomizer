"""FtNW 'cx' compression: decoder and an encoder whose output the game decodes byte for byte.

Format (SLUS_213.26: FUN_002dfbf0 header, FUN_002dfdd0 / FUN_002e0190 chunk decoder; Sh2 Decomp
SH3FtNW/tools/cx_decompress.py):
  header  u16 0x7863 "cx", u8 flags (0 in every file the randomizer touches: no huffman channels, 8-byte
          header), u8 chunk count (low byte), u32 decompressed size
  chunks  u8 flags, u8 0, u16 payload size - 1, u16 output size - 1, payload.  Output size is 0x8000 except in
          the last chunk, whose flags have bit 7 set.  Flag bits 3..6 clear (0x06): stored, each byte XORed
          with ((i + 1) * 11 & 0xff) ^ 0xdd (FUN_002dfab0).  0x0e: LSB-first bit stream of
            1, u8 literal                    or
            0, u8 length - 3, u14 distance - 1   (length 3..258, distance 1..16384)
          The game writes all chunks into one buffer, so a match may reach back into earlier chunks, but it
          must not run past the end of its own chunk (FUN_002e0190 fails the file).
The game decompresses in place, from the tail of the destination buffer, so a new file is kept no larger than
the one it replaces (Disc.write relocates it when it is, which the loader does not mind).
"""
import struct

CHUNK = 0x8000
MAXLEN, MAXDIST = 258, 16384
LIT_BITS, MATCH_BITS = 9, 23

_KEY = bytes((((i + 1) * 11) & 0xFF) ^ 0xDD for i in range(CHUNK))


def is_cx(data):
    return len(data) >= 8 and data[:2] == b"cx" and data[2] == 0


def _scramble(b):
    n = len(b)
    return (int.from_bytes(b, "little") ^ int.from_bytes(_KEY[:n], "little")).to_bytes(n, "little")


def decompress(data):
    if not is_cx(data):
        raise ValueError("not a plain cx stream")
    want = struct.unpack_from("<I", data, 4)[0]
    out = bytearray()
    pos = 8
    while len(out) < want:
        flags = data[pos]
        plen = struct.unpack_from("<H", data, pos + 2)[0] + 1
        olen = struct.unpack_from("<H", data, pos + 4)[0] + 1
        p = pos + 6
        if (flags & 0x7F) >> 3 == 0:
            out += _scramble(data[p:p + olen])
        else:
            acc = nb = 0
            end = len(out) + olen
            while len(out) < end:
                while nb < 23:
                    acc |= (data[p] if p < len(data) else 0) << nb
                    p += 1
                    nb += 8
                if acc & 1:
                    out.append(acc >> 1 & 0xFF)
                    acc >>= 9
                    nb -= 9
                else:
                    n = (acc >> 1 & 0xFF) + 3
                    src = len(out) - ((acc >> 9 & 0x3FFF) + 1)
                    acc >>= 23
                    nb -= 23
                    if src < 0 or len(out) + n > end:
                        raise ValueError("bad cx match")
                    for _ in range(n):
                        out.append(out[src])
                        src += 1
        pos += 6 + plen
    return bytes(out[:want])


def _longest_matches(data, depth):
    """Per position, the longest match (length, distance) inside the position's chunk, via 3-byte hash chains."""
    n = len(data)
    heads = {}
    lens = [0] * n
    dists = [0] * n
    for i in range(n - 2):
        key = data[i:i + 3]
        chain = heads.get(key)
        if chain is None:
            heads[key] = [i]
            continue
        lim = min(MAXLEN, min(n, (i // CHUNK + 1) * CHUNK) - i)
        best = bd = 0
        if lim >= 3:
            for p in reversed(chain[-depth:]):
                d = i - p
                if d > MAXDIST:
                    break
                if best and (best >= lim or data[p + best] != data[i + best]):
                    continue
                k = 3
                while k < lim and data[p + k] == data[i + k]:
                    k += 1
                if k > best:
                    best, bd = k, d
                    if k == lim:
                        break
        lens[i], dists[i] = best, bd
        chain.append(i)
        if len(chain) > 4 * depth:
            del chain[:-depth]
    return lens, dists


def _parse(lens, a, b):
    """Cheapest token list for data[a:b] under the fixed bit costs: [length or 0 (literal)]."""
    m = b - a
    cost = [0] * (m + 1)
    pick = [0] * m
    size = 1
    while size < m + 1:
        size *= 2
    inf = 1 << 60
    tree = [inf] * (2 * size)                     # range minimum over cost[]

    def put(j, v):
        j += size
        tree[j] = v
        j >>= 1
        while j:
            tree[j] = min(tree[2 * j], tree[2 * j + 1])
            j >>= 1

    def lowest(lo, hi):
        lo += size
        hi += size + 1
        r = inf
        while lo < hi:
            if lo & 1:
                r = min(r, tree[lo])
                lo += 1
            if hi & 1:
                hi -= 1
                r = min(r, tree[hi])
            lo >>= 1
            hi >>= 1
        return r

    put(m, 0)
    for k in range(m - 1, -1, -1):
        best, choice = LIT_BITS + cost[k + 1], 0
        n = min(lens[a + k], m - k)
        if n >= 3:
            if MATCH_BITS + cost[k + n] < best:
                best, choice = MATCH_BITS + cost[k + n], n
            if n > 3:
                r = lowest(k + 3, k + n - 1)
                if MATCH_BITS + r < best:
                    best = MATCH_BITS + r
                    choice = next(j for j in range(3, n) if cost[k + j] == r)
        cost[k], pick[k] = best, choice
        put(k, best)
    return pick


def compress(data, depth=64):
    n = len(data)
    lens, dists = _longest_matches(data, depth)
    count = (n + CHUNK - 1) // CHUNK
    out = bytearray(struct.pack("<2sBBI", b"cx", 0, count & 0xFF, n))
    for c in range(count):
        a, b = c * CHUNK, min(n, (c + 1) * CHUNK)
        pick = _parse(lens, a, b)
        bits = bytearray()
        acc = nb = 0
        k = a
        while k < b:
            ln = pick[k - a]
            if ln:
                acc |= ((ln - 3) << 1 | (dists[k] - 1) << 9) << nb
                nb += MATCH_BITS
                k += ln
            else:
                acc |= (1 | data[k] << 1) << nb
                nb += LIT_BITS
                k += 1
            while nb >= 8:
                bits.append(acc & 0xFF)
                acc >>= 8
                nb -= 8
        if nb:
            bits.append(acc & 0xFF)
        last = 0x80 if c == count - 1 else 0
        if len(bits) < b - a:
            out += struct.pack("<BBHH", 0x0E | last, 0, len(bits) - 1, b - a - 1) + bits
        else:
            out += struct.pack("<BBHH", 0x06 | last, 0, b - a - 1, b - a - 1) + _scramble(data[a:b])
    return bytes(out)
