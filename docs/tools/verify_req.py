"""Compare an1500ctl request builders with bytes captured from the vendor tool."""
import re, sys
import os
HERE = os.path.dirname(os.path.abspath(__file__))
TR = os.path.join(HERE, "..", "traces") + "/"
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import an1500ctl as a

def cap_bytes(path, which=-1, want=None):
    """data-out bytes of ADMIN 0xcb blocks in a trace log; pick block index or the one in function `want`."""
    blocks = open(path, errors="replace").read().split("ADMIN ")[1:]
    outs = []
    for b in blocks:
        if not b.startswith("opc=0xcb"):
            continue
        if want and want not in b.split("\n")[1]:
            continue
        m = re.search(r"data out \(len \d+\):\n((?:    [0-9a-f]{4}: .*\n)+)", b)
        raw = bytearray()
        for line in m.group(1).splitlines():
            raw += bytes.fromhex(line.split(": ", 1)[1].replace(" ", ""))
        outs.append(bytes(raw).rstrip(b"\0"))
    return outs[which]

def req_create(mode, pds, name, stripe=128, init="quick", query=False):
    return a.build_pair_request(a.REQ_VD, a.ACT_VD_CREATE,
                                a.build_create_payload(mode, pds, name, stripe, init, query)).rstrip(b"\0")

def check(label, mine, theirs):
    ok = mine == theirs
    print("%-58s %s" % (label, "IDENTICAL" if ok else "DIFFERENT"))
    if not ok:
        print("   mine  :", mine.hex()); print("   vendor:", theirs.hex())
    return ok

J, R0, R1 = a.MODE_JBOD, a.MODE_RAID0, a.MODE_RAID1
res = []
which = sys.argv[1] if len(sys.argv) > 1 else "single"
if which == "single":
    res.append(check("delete VD 1 (executed by vendor)",
                     a.build_pair_request(a.REQ_VD, a.ACT_VD_DELETE, a.build_delete_payload(1)).rstrip(b"\0"),
                     cap_bytes(TR + "tr_del.log", want="PairCMDDevice_sendCommand < MNV_VD_Delete")))
    res.append(check("create JBOD PD1: size query (executed by vendor)",
                     req_create(J, [1], "VD_1", query=True), cap_bytes(TR + "tr_cre.log", want="MNV_VD_QuerySize")))
    res.append(check("create JBOD PD1: create (executed by vendor)",
                     req_create(J, [1], "VD_1"), cap_bytes(TR + "tr_cre.log", want="PairCMDDevice_sendCommand < MNV_VD_Create")))
    res.append(check("create JBOD PD1 named 'My Disk' (query, captured)",
                     req_create(J, [1], "My Disk", query=True), cap_bytes(TR + "cap_jbod1_name.log")))
    res.append(check("create PD1 stripe 512 init none (query, captured)",
                     req_create(J, [1], "VD_1", 512, "none", query=True), cap_bytes(TR + "cap_r0_1_b512_none.log")))
    res.append(check("create PD1 stripe 256 (query, captured)",
                     req_create(J, [1], "VD_1", 256, query=True), cap_bytes(TR + "cap_r0_1_b256.log")))
else:
    res.append(check("create RAID0 PD0,1 (query, captured)",
                     req_create(R0, [0, 1], "VD_0", query=True), cap_bytes(TR + "cap_r0_01.log")))
    res.append(check("create RAID1 PD0,1 (query, captured)",
                     req_create(R1, [0, 1], "VD_0", query=True), cap_bytes(TR + "cap_r1_01.log")))
    res.append(check("create RAID0 PD0,1 stripe 256 named 'fast_1' (query, captured)",
                     req_create(R0, [0, 1], "fast_1", 256, query=True), cap_bytes(TR + "cap_r0_01_b256.log")))
    res.append(check("create JBOD PD0 (query, captured)",
                     req_create(J, [0], "VD_0", query=True), cap_bytes(TR + "cap_jbod0.log")))
print("ALL IDENTICAL" if all(res) else "MISMATCH")
