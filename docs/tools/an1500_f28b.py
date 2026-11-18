import zlib, struct
base = "/home/pynvme/src/wd_an1500_mod/"
samples = {
    "orig": open(base + "an1500_backup_20261118.bin", "rb").read()[0x20000:0x20200],
    "mod": open(base + "hbainfo.bin", "rb").read(),
    "now": open("/tmp/an1500_now.bin", "rb").read()[0x20000:0x20200],
}
def s(d): return sum(struct.unpack("<%dI" % (len(d)//4), d)) & 0xffffffff
def crcs(b):
    c = zlib.crc32(b) & 0xffffffff
    return {"crc32": c, "crc32_noxor": c ^ 0xffffffff, "sum": s(b), "negsum": (-s(b)) & 0xffffffff, "bytesum": sum(b)}
for n, d in samples.items():
    f28 = struct.unpack("<I", d[0x28:0x2c])[0]
    f28be = struct.unpack(">I", d[0x28:0x2c])[0]
    z = bytearray(d); z[0x28:0x2c] = b"\0"*4; z[0x3c:0x40] = b"\0"*4
    z = bytes(z)
    hits = []
    for st in range(0, 0x200, 4):
        for en in range(st + 4, 0x201, 4):
            for k, v in crcs(z[st:en]).items():
                if v in (f28, f28be):
                    hits.append((k, hex(st), hex(en)))
    print(n, "f28=%08x" % f28, len(hits), hits[:15])
