import sys, re, subprocess
# usage: fn.py name [name...] ; prints disassembly of functions, annotating string refs
asm = open("/tmp/mnv.asm").read().split("\n")
data = open("/home/pynvme/src/wd_an1500_mod/mnv_cli/mnv_cli", "rb").read()
# section mapping via readelf
secs = []
out = subprocess.run(["readelf", "-S", "-W", "/home/pynvme/src/wd_an1500_mod/mnv_cli/mnv_cli"], capture_output=True, text=True).stdout
for m in re.finditer(r"\]\s+(\S+)\s+\S+\s+([0-9a-f]{16})\s+([0-9a-f]+)\s+([0-9a-f]+)", out):
    secs.append((m.group(1), int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16)))
def rd(addr, n=80):
    for name, va, off, sz in secs:
        if va and va <= addr < va + sz and name != ".bss":
            b = data[off + addr - va: off + addr - va + n]
            return b
    return None
def cstr(addr):
    b = rd(addr)
    if not b: return None
    s = b.split(b"\0")[0]
    if len(s) >= 2 and all(32 <= c < 127 or c in (9, 10) for c in s):
        return s.decode()
    return None
idx = {}
for i, l in enumerate(asm):
    m = re.match(r"^[0-9a-f]+ <(\S+)>:$", l)
    if m: idx[m.group(1)] = i
for name in sys.argv[1:]:
    i = idx[name]
    print(asm[i])
    j = i + 1
    while j < len(asm) and asm[j].strip() != "":
        l = asm[j]
        note = ""
        for m in re.finditer(r"(?:0x|# )([0-9a-f]{6,7})\b", l):
            s = cstr(int(m.group(1), 16))
            if s: note += '  ; "%s"' % s[:70].replace("\n", "\\n")
        print(l + note)
        j += 1
    print()
