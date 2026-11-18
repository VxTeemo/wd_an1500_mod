import re
d = open("/tmp/an1500_now.bin", "rb").read()
for pat in (bytes.fromhex("961b0040"), bytes.fromhex("4b1b4122961b0040"), bytes.fromhex("4b1b41224b1b4122"), b"NEVOHBAF"):
    print(pat.hex(), [hex(m.start()) for m in re.finditer(re.escape(pat), d)][:20])
