import re, struct
d = open("/tmp/an1500_cur2.bin", "rb").read()
print("hdr words:", [hex(x) for x in struct.unpack("<24I", d[0x20:0x80])])
# strings mentioning raid/jbod in first 7MB
for m in re.finditer(rb"[ -~]{4,}", d[:0x700000]):
    s = m.group()
    if re.search(rb"(?i)jbod|raid ?mode|oem|svid|ssid|subsys", s):
        print(hex(m.start()), s[:100])
