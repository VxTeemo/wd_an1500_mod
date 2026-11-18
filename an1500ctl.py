#!/usr/bin/env python3
"""an1500ctl - control tool for the WD_BLACK AN1500 (Marvell 88NR2241 NVMe RAID).

Talks to the card directly with the same vendor NVMe admin commands mnv_cli
uses, but decodes the replies itself. mnv_cli prints a JBOD volume (RAID mode
0x0F) as "RAID0" and never lists JBOD under "Supported RAID Mode"; this tool
shows the real values.

CLI:  an1500ctl.py list | info [hba|vd|pd|ns] | create ... | delete ...
TUI:  an1500ctl.py tui

Needs root (NVMe admin passthrough). Python 3.8+, standard library only.
"""

import argparse
import ctypes
import curses
import fcntl
import glob
import json
import mmap
import os
import re
import struct
import sys
import time

VERSION = "1.0"

# ---------------------------------------------------------------------------
# Wire protocol (taken from traces of mnv_cli 1.0.20.1040 on firmware 1.0.27.1043)
# ---------------------------------------------------------------------------

NVME_IOCTL_ADMIN_CMD = 0xC0484E41
PAGE = 4096

OP_SIMPLE = 0xE2            # vendor read: CDW12 = sub command, CDW14 = object id
SUB_ADAPTER = 0xE0
SUB_PD = 0xE1
SUB_VD = 0xE2
SUB_VD_BGA = 0x2E3
SUB_NS = 0xE5

OP_PAIR_SEND = 0xCB         # vendor request buffer (host -> card)
OP_PAIR_RECV = 0xCE         # vendor response buffer (card -> host)
PAIR_CDW10 = 0x400
PAIR_CDW15 = 0x564E4D       # "MNV"
PAIR_VERSION = 0x1002
REQ_VD = 0xE2
ACT_VD_CREATE = 3
ACT_VD_DELETE = 4
CREATE_PAYLOAD_LEN = 0x270

MODE_RAID0 = 0x00
MODE_RAID1 = 0x01
MODE_JBOD = 0x0F
MODE_NAMES = {
    0x00: "RAID0", 0x01: "RAID1", 0x10: "RAID10", 0x05: "RAID5",
    0x50: "RAID50", 0x06: "RAID6", 0x60: "RAID60", 0x0F: "JBOD",
}
MODE_ARGS = {"jbod": MODE_JBOD, "raid0": MODE_RAID0, "0": MODE_RAID0,
             "raid1": MODE_RAID1, "1": MODE_RAID1}

# Bits of the "supported RAID mode" word. mnv_cli names every bit except 0x04;
# it is taken to be JBOD because that is the only mode left and the card
# accepts JBOD volumes.
SUPPORTED_MODE_BITS = [
    (0x01, "RAID0"), (0x02, "RAID1"), (0x04, "JBOD"), (0x08, "RAID10"),
    (0x10, "RAID5"), (0x20, "RAID50"), (0x40, "RAID6"), (0x80, "RAID60"),
]
BGA_FEATURE_BITS = [(0x01, "Initialization"), (0x02, "Rebuild"), (0x04, "Sync"),
                    (0x08, "Migrate"), (0x10, "MediaPatrol")]
STRIPE_BITS = [(0x01, 128), (0x02, 256), (0x04, 512)]
FEATURE_BITS = [(0x02, "Import"), (0x04, "RAID"), (0x08, "Namespace"),
                (0x10, "Dump"), (0x20, "NMIC")]
VD_STATUS = {
    0: "Functional", 1: "Degrade", 2: "Deleted", 3: "Missing", 4: "Offline",
    5: "Partially Optimal", 6: "Foreign", 7: "Importable", 8: "Not Importable",
    9: "Initialization", 10: "Rebuilding", 11: "Error Handling",
}
BGA_ACTIONS = {1: "Initializing", 2: "Initializing", 3: "Rebuilding",
               4: "Synchronizing", 5: "Migrating", 7: "Media Patrol"}
STRIPE_SECTORS = {128: 0x100, 256: 0x200, 512: 0x400}
INIT_MODES = {"quick": 0, "none": 3}
PCIE_SPEED = {1: "2.5Gb/s", 2: "5Gb/s", 3: "8Gb/s", 4: "16Gb/s"}
NAME_RE = re.compile(r"^[A-Za-z0-9 _]{1,16}$")


class CtlError(Exception):
    pass


class NvmeAdminCmd(ctypes.Structure):
    _fields_ = [
        ("opcode", ctypes.c_uint8), ("flags", ctypes.c_uint8), ("rsvd1", ctypes.c_uint16),
        ("nsid", ctypes.c_uint32), ("cdw2", ctypes.c_uint32), ("cdw3", ctypes.c_uint32),
        ("metadata", ctypes.c_uint64), ("addr", ctypes.c_uint64),
        ("metadata_len", ctypes.c_uint32), ("data_len", ctypes.c_uint32),
        ("cdw10", ctypes.c_uint32), ("cdw11", ctypes.c_uint32), ("cdw12", ctypes.c_uint32),
        ("cdw13", ctypes.c_uint32), ("cdw14", ctypes.c_uint32), ("cdw15", ctypes.c_uint32),
        ("timeout_ms", ctypes.c_uint32), ("result", ctypes.c_uint32),
    ]


def _cstr(raw):
    return raw.split(b"\0")[0].decode("ascii", "replace").strip()


def _bits(value, table):
    return [name for bit, name in table if value & bit]


def mode_name(mode):
    return MODE_NAMES.get(mode, "unknown(0x%02x)" % mode)


def fmt_size(nbytes):
    gib = nbytes / float(1 << 30)
    if gib >= 1:
        return "%.1f GiB" % gib
    return "%.1f MiB" % (nbytes / float(1 << 20))


def build_pair_request(opcode, action, payload):
    return b"MNV\0" + struct.pack("<HHHHI", PAIR_VERSION, len(payload), opcode, action, 0) + payload


def build_create_payload(mode, pd_ids, name, stripe_kb=128, init="quick", query=False):
    p = bytearray(CREATE_PAYLOAD_LEN)
    p[0] = mode
    p[1] = len(pd_ids)
    struct.pack_into("<H", p, 0x04, STRIPE_SECTORS[stripe_kb])
    struct.pack_into("<I", p, 0x08, INIT_MODES[init])
    for i, pd in enumerate(pd_ids):
        struct.pack_into("<H", p, 0x0C + 2 * i, pd)
    raw = name.encode("ascii")
    p[0x4C:0x4C + len(raw)] = raw
    p[0x68] = 1 if query else 0     # 1 = only report the resulting size
    return bytes(p)


def build_delete_payload(vd_id):
    # u16 VD id, u8 0. The last five bytes are uninitialised in mnv_cli; these
    # are the values it actually sent when the card accepted the command.
    return struct.pack("<HBB", vd_id, 0, 0) + b"\x02\x00\x00\x00"


def parse_adapter(p):
    fw = struct.unpack_from("<4H", p, 0x08)
    vid, svid, did, sdid = struct.unpack_from("<4H", p, 0x10)
    rev = p[0x18]
    letter = "A" if rev & 0x10 else "B" if rev & 0x20 else "?"
    features = struct.unpack_from("<I", p, 0x30)[0]
    links = []
    off = 0x50
    while off + 4 <= len(p) and p[off] != 0xFF and len(links) < 16:
        kind, idx, speed, width = p[off:off + 4]
        links.append({"type": "Root Complex" if kind == 1 else "End Point", "id": idx,
                      "speed": PCIE_SPEED.get(speed, "gen%d" % speed), "width": width})
        off += 4
    modes_raw = struct.unpack_from("<H", p, 0x2A)[0]
    return {
        "firmware": "%d.%d.%d.%04d" % fw,
        "vid": vid, "svid": svid, "did": did, "sdid": sdid,
        "revision": "%s%d%s" % (letter, rev & 0x0F, {0: "V", 1: "D", 2: "B", 3: "A"}.get(p[0x2F], "")),
        "port_count": p[0x19], "max_pd_per_vd": p[0x1A], "max_vd": p[0x1B],
        "max_pd": p[0x1C], "max_ns_per_vd": p[0x1D], "max_ns": p[0x1E],
        "supported_modes_raw": modes_raw,
        "supported_modes": _bits(modes_raw, SUPPORTED_MODE_BITS),
        "cache": {0: "Off", 1: "On"}.get(p[0x2C], "Unknown"),
        "bga_features": _bits(p[0x2D], BGA_FEATURE_BITS),
        "stripe_sizes_kb": _bits(p[0x2E], STRIPE_BITS),
        "features": _bits(features, FEATURE_BITS),
        "raid_capable": bool(features & 0x04),
        "serial": _cstr(p[0x38:0x4C]),
        "links": links,
    }


def parse_pd(p):
    flags = p[2]
    sector = struct.unpack_from("<I", p, 0x48)[0]
    lba = struct.unpack_from("<Q", p, 0x58)[0]
    return {
        "id": struct.unpack_from("<H", p, 0)[0],
        "model": _cstr(p[0x0C:0x34]), "serial": _cstr(p[0x34:0x48]),
        "firmware": _cstr(p[0x4C:0x54]),
        "sector_size": sector, "sectors": lba, "bytes": sector * lba,
        "slot": struct.unpack_from("<H", p, 0x60)[0],
        "backend_nsid": struct.unpack_from("<H", p, 0x62)[0],
        "status": "Fail" if flags & 0x10 else "Media Patrol" if flags & 0x02 else "Idle",
        "assigned": bool(flags & 0x01), "importable": bool(flags & 0x08),
        "failed": bool(flags & 0x10), "smart_warning": bool(flags & 0x20),
    }


def parse_vd(p):
    count = p[5]
    sector = struct.unpack_from("<I", p, 0x54)[0]
    sectors = struct.unpack_from("<Q", p, 0x08)[0]
    stripe = struct.unpack_from("<H", p, 0x52)[0]
    pds = [x for x in struct.unpack_from("<%dH" % count, p, 0x10) if x != 0xFFFF]
    return {
        "id": struct.unpack_from("<H", p, 0)[0],
        "name": _cstr(p[0x58:0x68]),
        "status": VD_STATUS.get(p[2], "N/A(%d)" % p[2]),
        "importable": p[3] == 1,
        "mode": p[4], "mode_name": mode_name(p[4]),
        "pd_count": count, "pds": pds, "parity_disks": p[0x51],
        "stripe_kb": stripe * 512 // 1024 if stripe else 0,
        "sector_size": sector, "sectors": sectors, "bytes": sector * sectors,
    }


def parse_bga(p):
    if p[4] not in (1, 2):
        return None
    return {"action": BGA_ACTIONS.get(p[1], "unknown(%d)" % p[1]),
            "percent": p[3], "state": "running" if p[4] == 1 else "paused"}


def parse_namespaces(p, max_ns):
    out = []
    for i in range(max_ns):
        e = p[i * 0x60:(i + 1) * 0x60]
        if len(e) < 0x60 or e[4] == 0:
            continue
        sector = struct.unpack_from("<I", e, 0x08)[0]
        sectors = struct.unpack_from("<Q", e, 0x10)[0]
        out.append({
            "nsid": e[0], "parent_type": "VD" if e[1] == 1 else "PD",
            "parent_id": struct.unpack_from("<H", e, 2)[0],
            "valid": bool(e[4] & 0x01), "active": bool(e[4] & 0x04),
            "health": "Unhealthy" if e[5] in (1, 10) else "Healthy",
            "sector_size": sector, "sectors": sectors, "bytes": sector * sectors,
        })
    return out


# ---------------------------------------------------------------------------
# Device access
# ---------------------------------------------------------------------------

def find_controllers():
    """Return [(dev_path, pci_address)] of Marvell 88NR2241 NVMe controllers."""
    found = []
    for ctrl in sorted(glob.glob("/sys/class/nvme/nvme*")):
        try:
            with open(os.path.join(ctrl, "device", "vendor")) as f:
                vendor = int(f.read().strip(), 16)
            with open(os.path.join(ctrl, "device", "device")) as f:
                device = int(f.read().strip(), 16)
        except (OSError, ValueError):
            continue
        if (vendor, device) == (0x1B4B, 0x2241):
            pci = os.path.basename(os.path.realpath(os.path.join(ctrl, "device")))
            found.append(("/dev/" + os.path.basename(ctrl), pci))
    return found


class Card:
    def __init__(self, path=None, debug=False):
        if path is None:
            ctrls = find_controllers()
            if not ctrls:
                raise CtlError("no Marvell 88NR2241 (AN1500) NVMe controller found")
            if len(ctrls) > 1:
                raise CtlError("several controllers found (%s); choose one with --device"
                               % ", ".join(c[0] for c in ctrls))
            path = ctrls[0][0]
        self.path = path
        self.name = os.path.basename(path)
        self.debug = debug
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError:
            raise CtlError("cannot open %s: permission denied (run with sudo)" % path)
        except OSError as e:
            raise CtlError("cannot open %s: %s" % (path, e.strerror))
        self._mm = mmap.mmap(-1, PAGE)      # page-aligned data buffer
        self._buf = (ctypes.c_uint8 * PAGE).from_buffer(self._mm)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def _admin(self, opcode, data=b"", cdw10=0, cdw12=0, cdw14=0, cdw15=0):
        ctypes.memset(self._buf, 0, PAGE)
        if data:
            ctypes.memmove(self._buf, data, len(data))
        cmd = NvmeAdminCmd(opcode=opcode, addr=ctypes.addressof(self._buf), data_len=PAGE,
                           cdw10=cdw10, cdw12=cdw12, cdw14=cdw14, cdw15=cdw15)
        if self.debug:
            sys.stderr.write("admin opc=0x%02x cdw10=0x%x cdw12=0x%x cdw14=0x%x cdw15=0x%x\n"
                             % (opcode, cdw10, cdw12, cdw14, cdw15))
            if data:
                sys.stderr.write("  out: %s\n" % bytes(data).rstrip(b"\0").hex())
        try:
            rc = fcntl.ioctl(self.fd, NVME_IOCTL_ADMIN_CMD, cmd)
        except OSError as e:
            raise CtlError("NVMe admin command 0x%02x failed: %s" % (opcode, e.strerror))
        if rc:
            raise CtlError("NVMe admin command 0x%02x returned status 0x%x" % (opcode, rc))
        resp = bytes(self._buf)
        if self.debug:
            sys.stderr.write("  in:  %s\n" % resp.rstrip(b"\0").hex())
        return resp

    @staticmethod
    def _unwrap(resp, what):
        if resp[:3] != b"MNV":
            raise CtlError("%s: unexpected reply (not an AN1500 / 88NR2241 controller?)" % what)
        return struct.unpack_from("<I", resp, 4)[0], resp[8:]

    def simple(self, sub, obj_id=0):
        """Vendor read command. Returns (status, payload)."""
        return self._unwrap(self._admin(OP_SIMPLE, cdw12=sub, cdw14=obj_id), "query 0x%x" % sub)

    def pair(self, opcode, action, payload):
        """Vendor request/response command pair. Returns (status, payload)."""
        self._admin(OP_PAIR_SEND, data=build_pair_request(opcode, action, payload),
                    cdw10=PAIR_CDW10, cdw15=PAIR_CDW15)
        resp = self._admin(OP_PAIR_RECV, cdw10=PAIR_CDW10, cdw15=PAIR_CDW15)
        return self._unwrap(resp, "request 0x%x/%d" % (opcode, action))

    # -- queries ------------------------------------------------------------

    def _sysfs(self, attr):
        try:
            with open("/sys/class/nvme/%s/%s" % (self.name, attr)) as f:
                return f.read().strip()
        except OSError:
            return ""

    def block_devices(self):
        """Map NVMe namespace id -> block device name for this controller."""
        out = {}
        base = "/sys/class/nvme/%s" % self.name
        try:
            entries = os.listdir(base)
        except OSError:
            return out
        for entry in entries:
            m = re.match(r"^(nvme\d+)(c\d+)?(n\d+)$", entry)
            if not m:
                continue
            blk = m.group(1) + m.group(3)
            for src in (os.path.join(base, entry, "nsid"), "/sys/block/%s/nsid" % blk):
                try:
                    with open(src) as f:
                        out[int(f.read().strip())] = blk
                    break
                except (OSError, ValueError):
                    continue
        return out

    def snapshot(self):
        status, p = self.simple(SUB_ADAPTER)
        if status:
            raise CtlError("controller info query failed (status 0x%x)" % status)
        adapter = parse_adapter(p)
        pds = []
        for i in range(adapter["max_pd"]):
            status, p = self.simple(SUB_PD, i)
            if status == 0:
                pds.append(parse_pd(p))
        status, p = self.simple(SUB_NS)
        namespaces = parse_namespaces(p, adapter["max_ns"]) if status == 0 else []
        blk = self.block_devices()
        for ns in namespaces:
            ns["block_device"] = blk.get(ns["nsid"])
        vds = []
        for i in range(adapter["max_vd"]):
            status, p = self.simple(SUB_VD, i)
            if status:
                continue
            vd = parse_vd(p)
            status, p = self.simple(SUB_VD_BGA, i)
            vd["bga"] = parse_bga(p) if status == 0 else None
            vd["namespaces"] = [ns["nsid"] for ns in namespaces
                                if ns["parent_type"] == "VD" and ns["parent_id"] == vd["id"]]
            vd["block_devices"] = [ns["block_device"] for ns in namespaces
                                   if ns["parent_type"] == "VD" and ns["parent_id"] == vd["id"]
                                   and ns["block_device"]]
            vds.append(vd)
        return {
            "device": self.path,
            "pci": os.path.basename(os.path.realpath("/sys/class/nvme/%s/device" % self.name)),
            "model": self._sysfs("model"), "serial": self._sysfs("serial"),
            "adapter": adapter, "pds": pds, "vds": vds, "namespaces": namespaces,
        }

    # -- changes ------------------------------------------------------------

    def rescan(self):
        try:
            with open("/sys/class/nvme/%s/rescan_controller" % self.name, "w") as f:
                f.write("1")
        except OSError:
            pass
        time.sleep(1.0)

    def create_vd(self, mode, pd_ids, name, stripe_kb=128, init="quick"):
        """Create a volume. Returns the size in bytes the card reported."""
        status, p = self.pair(REQ_VD, ACT_VD_CREATE,
                              build_create_payload(mode, pd_ids, name, stripe_kb, init, query=True))
        if status:
            raise CtlError("the card rejected this volume layout (status 0x%x)" % status)
        size = struct.unpack_from("<I", p, 4)[0] * struct.unpack_from("<Q", p, 8)[0]
        status, _ = self.pair(REQ_VD, ACT_VD_CREATE,
                              build_create_payload(mode, pd_ids, name, stripe_kb, init, query=False))
        if status:
            raise CtlError("create failed (status 0x%x)" % status)
        self.rescan()
        return size

    def delete_vd(self, vd_id):
        status, _ = self.pair(REQ_VD, ACT_VD_DELETE, build_delete_payload(vd_id))
        if status:
            raise CtlError("delete failed (status 0x%x)" % status)
        self.rescan()


# ---------------------------------------------------------------------------
# Validation shared by CLI and TUI
# ---------------------------------------------------------------------------

def free_pds(snap):
    return [pd for pd in snap["pds"] if not pd["assigned"] and not pd["failed"]]


def next_vd_id(snap):
    used = set(vd["id"] for vd in snap["vds"])
    for i in range(snap["adapter"]["max_vd"]):
        if i not in used:
            return i
    return None


def check_create(snap, mode, pd_ids, name, stripe_kb):
    """Return an error string, or None if the request is acceptable."""
    ad = snap["adapter"]
    if next_vd_id(snap) is None:
        return "the card already has its maximum of %d volumes" % ad["max_vd"]
    if len(set(pd_ids)) != len(pd_ids):
        return "a physical disk is listed twice"
    by_id = dict((pd["id"], pd) for pd in snap["pds"])
    for pd in pd_ids:
        if pd not in by_id:
            return "physical disk %d does not exist" % pd
        if by_id[pd]["assigned"]:
            return "physical disk %d already belongs to a volume" % pd
        if by_id[pd]["failed"]:
            return "physical disk %d is in Fail state" % pd
    if mode == MODE_JBOD and len(pd_ids) != 1:
        return "JBOD takes exactly 1 physical disk (one JBOD volume per disk)"
    if mode == MODE_RAID0 and len(pd_ids) < 2:
        return "RAID0 needs at least 2 physical disks (use JBOD for a single disk)"
    if mode == MODE_RAID1 and len(pd_ids) != 2:
        return "RAID1 needs exactly 2 physical disks"
    if len(pd_ids) > ad["max_pd_per_vd"]:
        return "at most %d physical disks per volume" % ad["max_pd_per_vd"]
    if mode != MODE_JBOD and mode_name(mode) not in ad["supported_modes"]:
        return "%s is not supported by this card" % mode_name(mode)
    if not NAME_RE.match(name):
        return "name must be 1-16 letters, digits, spaces or underscores"
    if stripe_kb not in STRIPE_SECTORS:
        return "stripe size must be 128, 256 or 512"
    return None


def check_create_local(mode, pd_ids, name, stripe_kb):
    """Local syntax checks for dry-run request preview."""
    if len(set(pd_ids)) != len(pd_ids):
        return "a physical disk is listed twice"
    if mode == MODE_JBOD and len(pd_ids) != 1:
        return "JBOD takes exactly 1 physical disk (one JBOD volume per disk)"
    if mode == MODE_RAID0 and len(pd_ids) < 2:
        return "RAID0 needs at least 2 physical disks (use JBOD for a single disk)"
    if mode == MODE_RAID1 and len(pd_ids) != 2:
        return "RAID1 needs exactly 2 physical disks"
    if not NAME_RE.match(name):
        return "name must be 1-16 letters, digits, spaces or underscores"
    if stripe_kb not in STRIPE_SECTORS:
        return "stripe size must be 128, 256 or 512"
    return None


def vd_usage(vd):
    """Describe how the OS is using a volume. Returns (blocking, warnings)."""
    blocking, warnings = [], []
    for blk in vd["block_devices"]:
        dev_re = re.compile(r"^/dev/%s(p\d+)?$" % re.escape(blk))
        for table, what in (("/proc/mounts", "mounted"), ("/proc/swaps", "used as swap")):
            try:
                with open(table) as f:
                    for line in f:
                        fields = line.split()
                        if fields and dev_re.match(fields[0]):
                            where = " on " + fields[1] if what == "mounted" else ""
                            blocking.append("%s is %s%s" % (fields[0], what, where))
            except OSError:
                pass
        parts = [d for d in glob.glob("/sys/block/%s/%s*" % (blk, blk)) if os.path.isdir(d)]
        for node in ["/sys/block/%s" % blk] + parts:
            holders = glob.glob(node + "/holders/*")
            if holders:
                blocking.append("/dev/%s is held by %s" % (
                    os.path.basename(node), ", ".join(os.path.basename(h) for h in holders)))
        # Also catch userspace processes keeping the block node open.
        opener_hits = []
        for proc in glob.glob("/proc/[0-9]*"):
            pid = os.path.basename(proc)
            fd_dir = os.path.join(proc, "fd")
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue
            matched = False
            for fd in fds:
                try:
                    target = os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
                if dev_re.match(target):
                    matched = True
                    break
            if not matched:
                continue
            comm = "?"
            try:
                with open(os.path.join(proc, "comm")) as f:
                    comm = f.read().strip() or "?"
            except OSError:
                pass
            opener_hits.append("%s(%s)" % (pid, comm))
            if len(opener_hits) >= 6:
                opener_hits.append("...")
                break
        if opener_hits:
            blocking.append("/dev/%s is open by process(es): %s" % (blk, ", ".join(opener_hits)))
        if parts:
            warnings.append("/dev/%s has %d partition(s)" % (blk, len(parts)))
        try:
            with open("/dev/" + blk, "rb") as f:
                if any(f.read(1 << 20)):
                    warnings.append("/dev/%s is not empty (data in its first MiB)" % blk)
        except OSError:
            pass
    return blocking, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def table(headers, rows):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join("%%-%ds" % w for w in widths)
    lines = [(fmt % tuple(headers)).rstrip()]
    for row in rows:
        lines.append((fmt % tuple(str(c) for c in row)).rstrip())
    return lines


def vd_rows(snap):
    rows = []
    for vd in snap["vds"]:
        striped = vd["mode"] not in (MODE_JBOD, MODE_RAID1)
        bga = vd["bga"]
        rows.append((vd["id"], vd["name"], vd["mode_name"], vd["status"], fmt_size(vd["bytes"]),
                     ",".join(str(x) for x in vd["pds"]),
                     "%dK" % vd["stripe_kb"] if striped else "-",
                     ",".join("/dev/" + b for b in vd["block_devices"]) or "-",
                     "%s %d%% (%s)" % (bga["action"], bga["percent"], bga["state"]) if bga else "-"))
    return rows


def pd_rows(snap):
    owner = {}
    for vd in snap["vds"]:
        for pd in vd["pds"]:
            owner[pd] = vd["id"]
    return [(pd["id"], pd["model"], pd["serial"], pd["firmware"], fmt_size(pd["bytes"]),
             pd["status"], "VD %d" % owner[pd["id"]] if pd["id"] in owner else
             ("yes" if pd["assigned"] else "free"), "WARNING" if pd["smart_warning"] else "ok")
            for pd in snap["pds"]]


VD_HEADERS = ("VD", "Name", "Mode", "Status", "Size", "PDs", "Stripe", "Block device", "Background")
PD_HEADERS = ("PD", "Model", "Serial", "FW", "Size", "Status", "Used by", "SMART")


def hba_lines(snap):
    ad = snap["adapter"]
    rows = [
        ("Device", "%s (PCI %s)" % (snap["device"], snap["pci"])),
        ("Model", snap["model"]), ("Serial", ad["serial"] or snap["serial"]),
        ("Firmware", ad["firmware"]), ("Revision", ad["revision"]),
        ("PCI ID", "%04x:%04x  subsystem %04x:%04x" % (ad["vid"], ad["did"], ad["svid"], ad["sdid"])),
        ("Supported modes", " ".join(ad["supported_modes"]) or "none"),
        ("Stripe sizes", " ".join("%dK" % k for k in ad["stripe_sizes_kb"])),
        ("Background tasks", " ".join(ad["bga_features"])),
        ("Features", " ".join(ad["features"])), ("Cache", ad["cache"]),
        ("Limits", "%d VD, %d PD, %d PD per VD, %d NS, %d NS per VD" % (
            ad["max_vd"], ad["max_pd"], ad["max_pd_per_vd"], ad["max_ns"], ad["max_ns_per_vd"])),
    ]
    for link in ad["links"]:
        rows.append(("%s %d" % (link["type"], link["id"]), "x%d, %s" % (link["width"], link["speed"])))
    return ["%-18s %s" % (k + ":", v) for k, v in rows]


def ns_lines(snap):
    rows = [(ns["nsid"], "%s %d" % (ns["parent_type"], ns["parent_id"]), fmt_size(ns["bytes"]),
             "yes" if ns["valid"] else "no", "yes" if ns["active"] else "no", ns["health"],
             "/dev/" + ns["block_device"] if ns["block_device"] else "-") for ns in snap["namespaces"]]
    return table(("NS", "Parent", "Size", "Valid", "Active", "Health", "Block device"), rows)


def cmd_list(card, args):
    snap = card.snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0
    ad = snap["adapter"]
    print("%s  %s  fw %s  modes: %s" % (snap["device"], snap["model"], ad["firmware"],
                                        " ".join(ad["supported_modes"])))
    print()
    print("\n".join(table(VD_HEADERS, vd_rows(snap))) if snap["vds"] else "No virtual disks.")
    print()
    print("\n".join(table(PD_HEADERS, pd_rows(snap))))
    return 0


def cmd_info(card, args):
    snap = card.snapshot()
    if args.json:
        key = {"hba": "adapter", "vd": "vds", "pd": "pds", "ns": "namespaces"}.get(args.object)
        print(json.dumps(snap[key] if key else snap, indent=2))
        return 0
    sections = []
    if args.object in ("all", "hba"):
        sections.append(hba_lines(snap))
    if args.object in ("all", "vd"):
        sections.append(table(VD_HEADERS, vd_rows(snap)) if snap["vds"] else ["No virtual disks."])
    if args.object in ("all", "pd"):
        sections.append(table(PD_HEADERS, pd_rows(snap)))
    if args.object in ("all", "ns"):
        sections.append(ns_lines(snap) if snap["namespaces"] else ["No namespaces."])
    print("\n\n".join("\n".join(s) for s in sections))
    return 0


def confirm(prompt, assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise CtlError("refusing to continue without confirmation; pass -y to confirm")
    return input(prompt + " Type 'yes' to continue: ").strip().lower() == "yes"


def cmd_create(card, args):
    snap = card.snapshot()
    mode = MODE_ARGS[args.raid.lower()]
    try:
        pd_ids = [int(x) for x in args.disks.split(",")]
    except ValueError:
        raise CtlError("-d takes physical disk ids separated by commas, e.g. 0,1")
    next_id = next_vd_id(snap)
    name = args.name if args.name is not None else ("VD_%s" % next_id if next_id is not None else "VD_0")
    what = "%s volume '%s' on PD %s" % (mode_name(mode), name, ",".join(str(x) for x in pd_ids))
    if args.dry_run:
        err = check_create_local(mode, pd_ids, name, args.stripe)
        if err:
            raise CtlError(err)
        req_query = build_pair_request(
            REQ_VD, ACT_VD_CREATE,
            build_create_payload(mode, pd_ids, name, args.stripe, args.init, query=True))
        req_create = build_pair_request(
            REQ_VD, ACT_VD_CREATE,
            build_create_payload(mode, pd_ids, name, args.stripe, args.init, query=False))
        print("dry run, nothing sent. Would create %s:" % what)
        print("query-size request: %s" % req_query.rstrip(b"\0").hex())
        print("create request:     %s" % req_create.rstrip(b"\0").hex())
        strict_err = check_create(snap, mode, pd_ids, name, args.stripe)
        if strict_err:
            print("note: with current card state, actual create would fail: %s" % strict_err)
        return 0
    err = check_create(snap, mode, pd_ids, name, args.stripe)
    if err:
        raise CtlError(err)
    if not confirm("Create %s? Existing data on these disks will be lost." % what, args.yes):
        print("Cancelled.")
        return 1
    size = card.create_vd(mode, pd_ids, name, args.stripe, args.init)
    print("Created %s (%s)." % (what, fmt_size(size)))
    return 0


def cmd_delete(card, args):
    snap = card.snapshot()
    vd = next((v for v in snap["vds"] if v["id"] == args.id), None)
    if vd is None:
        raise CtlError("virtual disk %d does not exist" % args.id)
    what = "VD %d '%s' (%s, %s%s)" % (vd["id"], vd["name"], vd["mode_name"], fmt_size(vd["bytes"]),
                                     "".join(", /dev/" + b for b in vd["block_devices"]))
    blocking, warnings = vd_usage(vd)
    for line in blocking + warnings:
        print("warning: " + line)
    if blocking and not args.force:
        raise CtlError("%s is in use; unmount/stop it first, or pass --force" % what)
    if args.dry_run:
        req = build_pair_request(REQ_VD, ACT_VD_DELETE, build_delete_payload(vd["id"]))
        print("dry run, nothing sent. Would delete %s:\n%s" % (what, req.rstrip(b"\0").hex()))
        return 0
    if not confirm("Delete %s? All data on it will be lost." % what, args.yes):
        print("Cancelled.")
        return 1
    card.delete_vd(vd["id"])
    print("Deleted %s." % what)
    return 0


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

class Tui:
    REFRESH_MS = 3000

    def __init__(self, card):
        self.card = card
        self.snap = None
        self.error = None
        self.message = ""
        self.sel = 0

    def run(self, scr):
        self.scr = scr
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.colors = {}
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            for i, (name, fg) in enumerate((("ok", curses.COLOR_GREEN), ("warn", curses.COLOR_YELLOW),
                                            ("bad", curses.COLOR_RED), ("head", curses.COLOR_CYAN)), 1):
                curses.init_pair(i, fg, -1)
                self.colors[name] = curses.color_pair(i)
        scr.timeout(self.REFRESH_MS)
        self.reload()
        while True:
            self.draw()
            ch = scr.getch()
            if ch == -1:
                self.reload()
            elif ch in (ord("q"), ord("Q")):
                return
            elif ch in (curses.KEY_UP, ord("k")):
                self.sel = max(0, self.sel - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.sel += 1
            elif ch in (ord("r"), ord("R")):
                self.reload()
                self.message = "Refreshed."
            elif ch in (ord("c"), ord("C")):
                self.guard(self.create)
            elif ch in (ord("d"), ord("D"), curses.KEY_DC):
                self.guard(self.delete)

    def reload(self):
        try:
            self.snap = self.card.snapshot()
            self.error = None
        except CtlError as e:
            self.error = str(e)
        if self.snap:
            self.sel = max(0, min(self.sel, len(self.snap["vds"]) - 1))

    def guard(self, action):
        self.message = ""
        try:
            action()
        except CtlError as e:
            self.alert("Error", [str(e)])
        self.reload()

    # -- drawing ------------------------------------------------------------

    def color(self, name):
        return self.colors.get(name, 0)

    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h and x < w - 1:
            try:
                self.scr.addnstr(y, x, text, w - 1 - x, attr)
            except curses.error:
                pass

    def draw(self):
        scr = self.scr
        scr.erase()
        h, w = scr.getmaxyx()
        self.put(0, 0, (" an1500ctl %s " % VERSION).ljust(w - 1), curses.A_REVERSE)
        y = 2
        if self.snap is None:
            self.put(y, 1, "Cannot read the card: %s" % self.error, self.color("bad"))
        else:
            snap, ad = self.snap, self.snap["adapter"]
            self.put(y, 1, "%s  %s  S/N %s" % (snap["device"], snap["model"], ad["serial"] or snap["serial"]),
                     curses.A_BOLD)
            self.put(y + 1, 1, "Firmware %s   PCI %04x:%04x subsystem %04x:%04x   Cache %s" % (
                ad["firmware"], ad["vid"], ad["did"], ad["svid"], ad["sdid"], ad["cache"]))
            self.put(y + 2, 1, "Supported modes: %s   (max %d volumes)" % (
                " ".join(ad["supported_modes"]) or "none", ad["max_vd"]))
            y += 4
            self.put(y, 1, "Virtual disks", self.color("head") | curses.A_BOLD)
            y += 1
            if snap["vds"]:
                lines = table(VD_HEADERS, vd_rows(snap))
                self.put(y, 3, lines[0], curses.A_UNDERLINE)
                for i, line in enumerate(lines[1:]):
                    vd = snap["vds"][i]
                    attr = self.color("ok") if vd["status"] == "Functional" else self.color("warn")
                    if i == self.sel:
                        attr = curses.A_REVERSE
                    self.put(y + 1 + i, 1, ("> " if i == self.sel else "  ") + line, attr)
                y += 1 + len(snap["vds"])
            else:
                self.put(y, 3, "none - press c to create one")
                y += 1
            y += 1
            self.put(y, 1, "Physical disks", self.color("head") | curses.A_BOLD)
            lines = table(PD_HEADERS, pd_rows(snap))
            self.put(y + 1, 3, lines[0], curses.A_UNDERLINE)
            for i, line in enumerate(lines[1:]):
                pd = snap["pds"][i]
                bad = pd["failed"] or pd["smart_warning"]
                self.put(y + 2 + i, 3, line, self.color("bad") if bad else 0)
            y += 2 + len(snap["pds"])
            if self.error:
                self.put(y + 1, 1, "Refresh failed: %s" % self.error, self.color("bad"))
        if self.message:
            self.put(h - 3, 1, self.message, curses.A_BOLD)
        self.put(h - 1, 0, " Up/Down select   c create   d delete   r refresh   q quit ".ljust(w - 1),
                 curses.A_REVERSE)
        scr.refresh()

    # -- dialogs ------------------------------------------------------------

    def dialog(self, title, lines, footer):
        h, w = self.scr.getmaxyx()
        width = min(w - 2, max([len(title) + 4, len(footer) + 4] + [len(s) + 4 for s in lines]))
        height = min(h, len(lines) + 4)
        win = curses.newwin(height, max(width, 4), max(0, (h - height) // 2), max(0, (w - width) // 2))
        win.keypad(True)
        win.erase()
        win.box()
        try:
            win.addnstr(0, 2, " %s " % title, max(0, width - 4), curses.A_BOLD)
            win.addnstr(height - 1, 2, " %s " % footer, max(0, width - 4))
        except curses.error:
            pass
        return win, width, height

    def choose(self, title, options, multi=0):
        """Pick one option (multi=0) or exactly `multi` options. None = cancelled."""
        pos, marked = 0, set()
        footer = "Space mark, Enter accept, Esc cancel" if multi else "Enter select, Esc cancel"
        while True:
            lines = [("[%s] " % ("x" if i in marked else " ") if multi else "") + opt
                     for i, opt in enumerate(options)]
            win, width, height = self.dialog(title, lines, footer)
            for i, line in enumerate(lines[:height - 4]):
                try:
                    win.addnstr(2 + i, 2, line.ljust(width - 4), width - 4,
                                curses.A_REVERSE if i == pos else 0)
                except curses.error:
                    pass
            ch = win.getch()
            if ch == 27:
                return None
            if ch in (curses.KEY_UP, ord("k")):
                pos = (pos - 1) % len(options)
            elif ch in (curses.KEY_DOWN, ord("j")):
                pos = (pos + 1) % len(options)
            elif ch == ord(" ") and multi:
                marked.symmetric_difference_update([pos])
            elif ch in (10, 13, curses.KEY_ENTER):
                if not multi:
                    return pos
                if len(marked) == multi:
                    return sorted(marked)

    def ask_text(self, title, prompt, default):
        text = default
        while True:
            win, width, _ = self.dialog(title, [prompt, text + "_" + " " * 16], "Enter accept, Esc cancel")
            try:
                win.addnstr(2, 2, prompt, width - 4)
                win.addnstr(3, 2, text + "_", width - 4, curses.A_BOLD)
            except curses.error:
                pass
            ch = win.getch()
            if ch == 27:
                return None
            if ch in (10, 13, curses.KEY_ENTER):
                return text
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                text = text[:-1]
            elif 32 <= ch < 127 and len(text) < 16:
                text += chr(ch)

    def alert(self, title, lines, footer="press any key"):
        win, width, height = self.dialog(title, lines, footer)
        for i, line in enumerate(lines[:height - 4]):
            try:
                win.addnstr(2 + i, 2, line, width - 4)
            except curses.error:
                pass
        return win.getch()

    def confirm(self, title, lines):
        return self.alert(title, lines, "Y (capital) = confirm, any other key = cancel") == ord("Y")

    def confirm_force(self, title, lines):
        return self.alert(title, lines, "F (capital) = force delete, any other key = cancel") == ord("F")

    # -- actions ------------------------------------------------------------

    def create(self):
        snap = self.card.snapshot()
        ad = snap["adapter"]
        free = free_pds(snap)
        if next_vd_id(snap) is None:
            raise CtlError("The card already has its maximum of %d volumes." % ad["max_vd"])
        if not free:
            raise CtlError("No free physical disk. Delete a volume first.")
        modes = [(MODE_JBOD, "JBOD   - one disk, passed through on its own")]
        if len(free) >= 2 and "RAID0" in ad["supported_modes"]:
            modes.append((MODE_RAID0, "RAID0  - stripe two disks (no redundancy)"))
        if len(free) >= 2 and "RAID1" in ad["supported_modes"]:
            modes.append((MODE_RAID1, "RAID1  - mirror two disks"))
        pick = self.choose("Create volume: mode", [m[1] for m in modes])
        if pick is None:
            return
        mode = modes[pick][0]
        labels = ["PD %d  %s  %s  %s" % (pd["id"], pd["model"], pd["serial"], fmt_size(pd["bytes"]))
                  for pd in free]
        if mode == MODE_JBOD:
            pick = self.choose("Create JBOD: disk", labels)
            if pick is None:
                return
            pd_ids = [free[pick]["id"]]
        elif len(free) == 2:
            pd_ids = [pd["id"] for pd in free]
        else:
            pick = self.choose("Create %s: mark 2 disks" % mode_name(mode), labels, multi=2)
            if pick is None:
                return
            pd_ids = [free[i]["id"] for i in pick]
        stripe = 128
        if mode == MODE_RAID0 and len(ad["stripe_sizes_kb"]) > 1:
            pick = self.choose("Stripe block size", ["%dK" % k for k in ad["stripe_sizes_kb"]])
            if pick is None:
                return
            stripe = ad["stripe_sizes_kb"][pick]
        name = self.ask_text("Volume name", "1-16 letters, digits, spaces, underscores:",
                             "VD_%d" % next_vd_id(snap))
        if name is None:
            return
        err = check_create(snap, mode, pd_ids, name, stripe)
        if err:
            raise CtlError(err[0].upper() + err[1:] + ".")
        lines = ["Mode:   %s" % mode_name(mode), "Disks:  PD %s" % ", ".join(str(x) for x in pd_ids),
                 "Name:   %s" % name]
        if mode == MODE_RAID0:
            lines.append("Stripe: %dK" % stripe)
        lines += ["", "Existing data on these disks will be lost."]
        if not self.confirm("Create this volume?", lines):
            self.message = "Create cancelled."
            return
        size = self.card.create_vd(mode, pd_ids, name, stripe)
        self.message = "Created %s volume '%s' (%s)." % (mode_name(mode), name, fmt_size(size))

    def delete(self):
        snap = self.card.snapshot()
        if not snap["vds"]:
            raise CtlError("There is no volume to delete.")
        vd = snap["vds"][max(0, min(self.sel, len(snap["vds"]) - 1))]
        blocking, warnings = vd_usage(vd)
        force = False
        if blocking:
            lines = ["VD %d '%s' appears in use:" % (vd["id"], vd["name"])]
            lines.extend("- " + s for s in blocking[:6])
            if len(blocking) > 6:
                lines.append("- ...")
            lines += ["", "Force delete can cause data loss/corruption in active users."]
            if not self.confirm_force("Volume in use", lines):
                self.message = "Delete cancelled."
                return
            force = True
        lines = ["VD %d  '%s'" % (vd["id"], vd["name"]),
                 "%s, %s, PD %s" % (vd["mode_name"], fmt_size(vd["bytes"]),
                                    ", ".join(str(x) for x in vd["pds"])),
                 "Block device: %s" % (", ".join("/dev/" + b for b in vd["block_devices"]) or "-"), ""]
        lines += ["Warning: " + s for s in warnings]
        if force:
            lines.append("Warning: force mode enabled.")
        lines.append("All data on this volume will be lost.")
        if not self.confirm("Delete this volume?", lines):
            self.message = "Delete cancelled."
            return
        self.card.delete_vd(vd["id"])
        self.message = "Deleted VD %d '%s'." % (vd["id"], vd["name"])


def cmd_tui(card, args):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise CtlError("the TUI needs an interactive terminal")
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(Tui(card).run)
    return 0


# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="an1500ctl", description="Control tool for the WD_BLACK AN1500 (Marvell 88NR2241).")
    ap.add_argument("--device", help="controller device, e.g. /dev/nvme0 (default: auto-detect)")
    ap.add_argument("--json", action="store_true", help="machine-readable output for list/info")
    ap.add_argument("--debug", action="store_true", help="print every NVMe admin command to stderr")
    ap.add_argument("--version", action="version", version="an1500ctl " + VERSION)
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("list", help="summary of volumes and physical disks (default)")
    p = sub.add_parser("info", help="detailed information")
    p.add_argument("object", nargs="?", default="all", choices=("all", "hba", "vd", "pd", "ns"))
    p = sub.add_parser("create", help="create a volume")
    p.add_argument("-r", "--raid", required=True, type=str.lower, choices=sorted(MODE_ARGS),
                   help="volume mode: jbod, raid0 or raid1")
    p.add_argument("-d", "--disks", required=True, help="physical disk ids, e.g. 0 or 0,1")
    p.add_argument("-n", "--name", help="volume name (default VD_<id>)")
    p.add_argument("-b", "--stripe", type=int, default=128, choices=(128, 256, 512),
                   help="stripe block size in KB for RAID0 (default 128)")
    p.add_argument("-i", "--init", default="quick", choices=("quick", "none"),
                   help="quick (default) clears the first 64 KB of the volume")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--dry-run", action="store_true", help="show the request without sending it")
    p = sub.add_parser("delete", help="delete a volume")
    p.add_argument("-i", "--id", required=True, type=int, help="virtual disk id")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--force", action="store_true", help="delete even if the volume is mounted or held")
    p.add_argument("--dry-run", action="store_true", help="show the request without sending it")
    sub.add_parser("tui", help="interactive full-screen interface")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    handler = {None: cmd_list, "list": cmd_list, "info": cmd_info, "create": cmd_create,
               "delete": cmd_delete, "tui": cmd_tui}[args.command]
    try:
        card = Card(args.device, debug=args.debug)
        try:
            return handler(card, args)
        finally:
            card.close()
    except CtlError as e:
        sys.stderr.write("an1500ctl: %s\n" % e)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
