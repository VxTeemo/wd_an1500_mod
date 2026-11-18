import sys, re, subprocess
P = "/home/pynvme/src/wd_an1500_mod/mnv_cli/mnv_cli"
data = open(P, "rb").read()
secs = []
out = subprocess.run(["readelf", "-S", "-W", P], capture_output=True, text=True).stdout
for m in re.finditer(r"\]\s+(\S+)\s+\S+\s+([0-9a-f]{16})\s+([0-9a-f]+)\s+([0-9a-f]+)", out):
    secs.append((m.group(1), int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16)))
def va2off(addr):
    for name, va, off, sz in secs:
        if va and va <= addr < va + sz and name != ".bss":
            return off + addr - va
def off2va(o):
    for name, va, off, sz in secs:
        if va and off <= o < off + sz and name != ".bss":
            return va + o - off
if sys.argv[1] == "str":
    for a in sys.argv[2:]:
        o = va2off(int(a, 16))
        print(a, repr(data[o:o+60].split(b"\0")[0]))
elif sys.argv[1] == "find":
    # find string, then functions referencing its address
    asm = open("/tmp/mnv.asm").read().split("\n")
    for s in sys.argv[2:]:
        for m in re.finditer(re.escape(s.encode()), data):
            va = off2va(m.start())
            if va is None: continue
            # back up to string start
            st = m.start()
            while data[st-1] != 0: st -= 1
            va0 = off2va(st)
            refs = []
            cur = None
            for l in asm:
                mm = re.match(r"^[0-9a-f]+ <(\S+)>:$", l)
                if mm: cur = mm.group(1)
                if ("# %x " % va0) in l or ("# %x " % va) in l or ("0x%x" % va0) in l:
                    refs.append((cur, l.split(":")[0].strip()))
            print(repr(s), hex(va0), refs[:12])
