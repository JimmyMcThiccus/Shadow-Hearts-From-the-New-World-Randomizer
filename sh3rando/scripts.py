"""Field scripts (FIELD/MPS/<map>.MSC, cx-compressed on the disc): item gifts and the items quests check.

FtNW keeps Covenant's script VM.  MSC image (Sh2 Decomp notes/E/script_vm.md 1.2, dc_patch/tools/msc.py):
header 0x20 (+1 u8 header units, +6 u16 directory / code / data units, +0xc u16 bss / start / end), EVNT
directory, code, data.  The code is a stack machine: pushes (0x0a u8, 0x0c u16, 0x0e u32 ...), CField calls
(0xc2 sub n) and calls of script functions in the same file (0x1b u16 address, u8 argc).

Every map links the same script library.  Its GetItem(code, count) (two arguments in FtNW; it first handles
the cash codes) adds the item through TreasureControl(5, code, count) and builds "You got <name>" from the
code, so changing the pushed code also changes the message.  It is the library function holding
    push 5; push local0; push local1; CField.TreasureControl n=3
A gift is `push u16 code; push n; callu GetItem n=2` with all three instructions back to back.
"""
import struct

OP_CALLU, OP_CFIELD = 0x1B, 0xC2
CF_TREASURE = 0x79
GETITEM_SIG = bytes.fromhex("0a05190024190424c27903")


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


class Msc:
    def __init__(self, path, raw):
        self.path = path
        self.raw = bytearray(raw)
        nsec = raw[1]
        dir_units, code_units = struct.unpack_from("<HH", raw, 6)
        self.start, self.end = struct.unpack_from("<HH", raw, 0xE)
        self.code0 = nsec * 16 + dir_units * 16
        self.code = bytes(raw[self.code0:self.code0 + code_units * 16])
        self.events = []
        p = nsec * 16
        while p < self.code0:
            magic, tbl, units, _kind, cnt = struct.unpack_from("<IHHHH", raw, p)
            if magic == 0x544E5645:                                  # "EVNT"
                self.events += [_u16(raw, p + tbl + i * 8 + 6) for i in range(cnt)]
            if units == 0:
                break
            p += units * 16
        self._walk()

    @property
    def name(self):
        return self.path.rsplit("/", 1)[-1][:-4]

    def _walk(self):
        """Visit every reachable instruction: self.insns = {ip: length} (a port of msc.Msc.walk)."""
        code = self.code
        funcs = {self.start} | set(self.events)
        if self.end != 0xFFFF:
            funcs.add(self.end)
        done, self.insns = set(), {}
        while funcs - done:
            f = min(funcs - done)
            done.add(f)
            work, visited = [f + 2], set()
            while work:
                ip = work.pop()
                stack, last = [], None
                while ip not in visited and ip < len(code):
                    visited.add(ip)
                    b = code[ip]
                    at = ip
                    ip += 1
                    if b >= 0xA0:
                        if b > 0xBB:
                            ip += 1
                        n = code[ip] if ip < len(code) else 0
                        ip += 1
                        del stack[len(stack) - min(n, len(stack)):]
                        stack.append(None)
                    elif b >= 0x90:
                        stack.clear()
                        if b == 0x90:
                            work.append(_u16(code, ip))
                            ip += 2
                            self.insns[at] = ip - at
                            break
                        elif b in (0x91, 0x92):
                            work.append(_u16(code, ip))
                            ip += 2
                        elif b == 0x94:
                            p = _u16(code, ip)
                            h = _u16(code, p)
                            p += 2
                            kt = (h >> 12) & 7
                            for _ in range(h & 0xFFF):
                                p += {2: 4, 1: 2}.get(kt, 1)
                                work.append(_u16(code, p))
                                p += 2
                            if h & 0x8000:
                                work.append(_u16(code, p))
                                self.insns[at] = 3
                                break
                            ip += 2
                            self.insns[at] = ip - at
                            ip = p
                            continue
                        elif b in (0x98, 0x99):
                            self.insns[at] = 1
                            break
                    elif b >= 0x20:
                        if b in (0x8E, 0x8F):
                            del stack[-3:]
                            stack.append(None)
                        elif b == 0x8D:
                            ip += 2
                            stack[-1:] = []
                        elif b == 0x8C:
                            n = stack.pop() if stack else None
                            n = n if n is not None else (last or 0)
                            ip += min(n, 0x1000)
                        elif b == 0x8B:
                            ip += 1
                            stack[-1:] = []
                        elif b == 0x8A:
                            stack[-1:] = []
                        elif 0x80 <= b <= 0x89 or 0x60 <= b <= 0x63:
                            s = code[ip]
                            ip += 1
                            if s & 0xC0 == 0xC0:
                                ip += 1
                            if b >= 0x80:
                                del stack[-2:]
                            else:
                                stack[-1:] = []
                            stack.append(None)
                        elif 0x70 <= b <= 0x7A or 0x67 <= b <= 0x6D:
                            del stack[-2:]
                            stack.append(None)
                        else:
                            stack[-1:] = []
                            stack.append(None)
                    else:
                        if b == 0x00:
                            self.insns[at] = 1
                            break
                        elif b == 0x01:
                            stack.clear()
                        elif b in (0x04, 0x05, 0x06, 0x07, 0x08, 0x09):
                            last = None
                            stack.append(None)
                        elif b in (0x0A, 0x0B):
                            last = code[ip]
                            stack.append(last)
                            ip += 1
                        elif b in (0x0C, 0x0D):
                            last = _u16(code, ip)
                            stack.append(last)
                            ip += 2
                        elif b in (0x0E, 0x0F):
                            last = struct.unpack_from("<I", code, ip)[0]
                            stack.append(last)
                            ip += 4
                        elif 0x10 <= b <= 0x13 or b == 0x19:
                            ip += 1
                            stack.append(None)
                        elif b in (0x14, 0x16, 0x18, 0x1A, 0x15):
                            ip += 2
                            stack.append(None)
                        elif b == 0x1B:
                            funcs.add(_u16(code, ip))
                            n = code[ip + 2]
                            ip += 3
                            del stack[len(stack) - min(n, len(stack)):]
                            stack.append(None)
                    self.insns[at] = ip - at
        self.order = sorted(self.insns)

    def imm(self, ip):
        """Value of an immediate push at ip, else None."""
        b = self.code[ip]
        if b == 0x0A:
            return self.code[ip + 1]
        if b == 0x0B:
            return -self.code[ip + 1]
        if b == 0x0C:
            return _u16(self.code, ip + 1)
        if b == 0x0D:
            return -_u16(self.code, ip + 1)
        if b == 0x0E:
            return struct.unpack_from("<i", self.code, ip + 1)[0]
        return {0x05: -1, 0x06: 0, 0x07: 1, 0x08: 2, 0x09: 4}.get(b)

    def _runs(self, n):
        """(k, [ip_{k-n} .. ip_k]) for every run of n+1 back-to-back instructions."""
        o = self.order
        for k in range(n, len(o)):
            ips = o[k - n:k + 1]
            if all(a + self.insns[a] == b for a, b in zip(ips, ips[1:])):
                yield ips

    def functions(self):
        """Start addresses of the script functions (call targets, events, start / end)."""
        c = self.code
        out = {_u16(c, ip + 1) for ip in self.order if c[ip] == OP_CALLU} | {self.start} | set(self.events)
        if self.end != 0xFFFF:
            out.add(self.end)
        return sorted(out)

    def function_of(self, ip, funcs=None):
        funcs = funcs or self.functions()
        return max((f for f in funcs if f <= ip), default=None)

    def getitem_target(self):
        """Address of the library GetItem function in this file, or None."""
        at = self.code.find(GETITEM_SIG)
        if at < 0:
            return None
        f = self.function_of(at)
        argc = {self.code[ip + 3] for ip in self.order if self.code[ip] == OP_CALLU and _u16(self.code, ip + 1) == f}
        return f if argc == {2} else None


class Gift:
    """One `push code; push 1; push x; callu GetItem` in one script."""

    def __init__(self, script, ip, code):
        self.script, self.ip, self.code = script, ip, code
        self.item = (code >> 8, code & 0xFF)

    @property
    def file_off(self):
        return self.script.code0 + self.ip + 1                    # the u16 operand of the 0x0c push

    def set_item(self, it):
        struct.pack_into("<H", self.script.raw, self.file_off, it[0] << 8 | it[1])


def gifts(script):
    t = script.getitem_target()
    if t is None:
        return []
    out = []
    code = script.code
    for a, b, c in script._runs(2):
        if (code[c] == OP_CALLU and _u16(code, c + 1) == t and code[c + 3] == 2 and code[a] == 0x0C
                and script.imm(b) is not None and script.imm(b) > 0):
            out.append(Gift(script, a, script.imm(a)))
    return out


def _call_args(script, k, n):
    """The n arguments of the call at script.order[k]: ("imm", value), ("par", index) for `op19 off; op24`
    (the caller's own parameter off / 4) or None for anything else."""
    o, code = script.order, script.code
    args, i = [], k - 1
    while len(args) < n and i >= 0:
        ip = o[i]
        if ip + script.insns[ip] != o[i + 1]:
            break
        if code[ip] == 0x24 and i >= 1 and code[o[i - 1]] == 0x19 and o[i - 1] + 2 == ip:
            args.append(("par", code[o[i - 1] + 1] // 4))
            i -= 2
        elif script.imm(ip) is not None:
            args.append(("imm", script.imm(ip)))
            i -= 1
        else:
            break                                                 # an expression: the rest is unknown
    args += [None] * (n - len(args))
    return list(reversed(args))


def chest_rows(script):
    """TRES rows this script opens with an immediate row number (for the spoiler log only).  Chest code passes
    the row down a chain of library helpers to TreasureControl(mode, param, 0), `op19 off; op24; push 0`;
    the parameter is followed back up the chain to the callers that push a constant.  Most chests are map
    objects whose event receives the row from the map data, so they are not found here."""
    code = script.code
    funcs = script.functions()
    params = {}                                                   # function -> parameter indices
    for a, b, c, d in script._runs(3):
        if code[d] == OP_CFIELD and code[d + 1] == CF_TREASURE and code[a] == 0x19 and code[b] == 0x24                 and script.imm(c) == 0:
            f = script.function_of(d, funcs)
            if f is not None:
                params.setdefault(f, set()).add(code[a + 1] // 4)
    rows = set()
    changed = True
    while changed:
        changed = False
        for k, ip in enumerate(script.order):
            if code[ip] != OP_CALLU or _u16(code, ip + 1) not in params:
                continue
            n = code[ip + 3]
            args = _call_args(script, k, n)
            g = script.function_of(ip, funcs)
            for j in params[_u16(code, ip + 1)]:
                arg = args[j] if j < n else None
                if arg is None:
                    continue
                if arg[0] == "imm" and arg[1] >= 0:
                    rows.add(arg[1])
                elif arg[0] == "par" and g is not None and arg[1] not in params.setdefault(g, set()):
                    params[g].add(arg[1])
                    changed = True
    return rows


def quest_items(scripts):
    """Items a script checks or takes away by code: TreasureControl(5, code, n) with immediate operands."""
    out = set()
    for s in scripts:
        for a, b, c, d in s._runs(3):
            if s.code[d] == OP_CFIELD and s.code[d + 1] == CF_TREASURE and s.code[d + 2] == 3 and s.imm(a) == 5:
                code = s.imm(b)
                if code is not None and 0x100 <= code < 0x1000:
                    out.add((code >> 8, code & 0xFF))
    return out
