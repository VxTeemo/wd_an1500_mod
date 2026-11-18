import re, struct, collections, sys
d = open("/tmp/an1500_cur2.bin", "rb").read()
lo, hi = 0x0, 0x100000
strs = set()
for m in re.finditer(rb"(?<=\x00)[ -~]{8,}\x00", d[lo:hi]):
    strs.add(lo + m.start())
words = collections.defaultdict(list)
for off in range(lo, hi, 4):
    w = struct.unpack_from("<I", d, off)[0]
    words[w].append(off)
cnt = collections.Counter()
for w in words:
    if w < 0x1000: continue
    for s in ():
        pass
# candidate bases: page-aligned delta
for w, offs in words.items():
    for s in (0xabfe8, 0xa9c6c, 0xabb12, 0xabba2, 0xae38d):
        delta = w - s
        if delta & 0xfff == 0 and delta >= 0:
            cnt[delta] += 1
print([(hex(k), v) for k, v in cnt.most_common(8)])
best = cnt.most_common(1)[0][0]
hits = sum(1 for s in strs if (s + best) in words)
print("base", hex(best), "string xrefs", hits, "of", len(strs))
for s in (0xabfe8, 0xa9c6c):
    print(hex(s), "referenced at", [hex(o) for o in words.get(s + best, [])])
# layout: find boundaries of non-FF regions (64KB granularity)
reg = []
for off in range(0, 0x1000000, 0x10000):
    blk = d[off:off+0x10000]
    e = not (set(blk) <= {0xff})
    reg.append(e)
out = []; st = None
for i, e in enumerate(reg + [False]):
    if e and st is None: st = i
    if not e and st is not None:
        out.append((hex(st*0x10000), hex(i*0x10000))); st = None
print("non-FF regions:", out)
