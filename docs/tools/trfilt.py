import sys, re
# print trace blocks, skipping identify (0x06) and init-phase duplicates
for path in sys.argv[1:]:
    print("=====", path)
    blocks = open(path).read().split("ADMIN ")[1:]
    seen_main = False
    for b in blocks:
        head = b.split("\n")[0]
        bt = b.split("\n")[1] if "\n" in b else ""
        if "MNV_Init" in bt or "opc=0x06" in head and "MNV_Init" in bt:
            continue
        print("ADMIN " + b.rstrip())
