import struct
base = "/home/pynvme/src/wd_an1500_mod/"
files = {"orig(pre-flash)": base + "an1500_backup_20261118.bin", "now": "/tmp/an1500_now.bin",
         "old_other_card": base + "an1500_backup.bin"}
for n, p in files.items():
    d = open(p, "rb").read()
    print("==", n)
    for off in (0x20000, 0x220000, 0x820000, 0xa20000):
        b = d[off:off+0x200]
        print("  %07x sig=%s f28=%s f3c=%s 140=%s 1c8=%s" % (off, b[:8], b[0x28:0x2c].hex(), b[0x3c:0x40].hex(), b[0x140:0x150].hex(), b[0x1c8:0x1d0].hex()))
o = open(files["orig(pre-flash)"], "rb").read(); w = open(files["now"], "rb").read()
def ranges(a, b):
    r = []; i = 0
    while i < len(a):
        if a[i] != b[i]:
            j = i
            while j < len(a) and a[j] != b[j]: j += 1
            r.append((i, j)); i = j
        else: i += 1
    return r
def merge(r, gap=0x1000):
    out = []
    for a, b in r:
        if out and a - out[-1][1] < gap: out[-1] = (out[-1][0], b)
        else: out.append((a, b))
    return out
print("orig vs now diffs:", [(hex(a), hex(b)) for a, b in merge(ranges(o, w))])
