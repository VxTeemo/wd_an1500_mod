import gdb, struct, os

NOEXEC = os.environ.get("MNV_NOEXEC", "0") == "1"   # kill before vendor write cmds reach the device
LOG = open(os.environ.get("MNV_LOG", "/tmp/mnv_trace.log"), "w")
NVME_ADMIN = 0xC0484E41
FMT = "<BBHIIIQQIIIIIIIIII"
inf = None

def out(s):
    LOG.write(s + "\n"); LOG.flush()

def hexdump(b, limit=0x400):
    lines = []
    b = b[:limit]
    # trim trailing zeros
    n = len(b)
    while n > 0 and b[n-1] == 0: n -= 1
    n = (n + 15) // 16 * 16
    for i in range(0, n, 16):
        lines.append("    %04x: %s" % (i, b[i:i+16].hex(" ")))
    return "\n".join(lines) if lines else "    (all zero)"

class Fin(gdb.FinishBreakpoint):
    def __init__(self, frame, cmdaddr, f):
        super().__init__(frame, internal=True)
        self.cmdaddr = cmdaddr; self.f = f
    def stop(self):
        i = gdb.selected_inferior()
        raw = bytes(i.read_memory(self.cmdaddr, 72))
        f = struct.unpack(FMT, raw)
        out("  -> ret=%s result=0x%x" % (self.return_value, f[17]))
        if self.f[9] and self.f[7]:
            out("  data in (len %d):" % self.f[9])
            out(hexdump(bytes(i.read_memory(self.f[7], self.f[9]))))
        return False

class Brk(gdb.Breakpoint):
    def stop(self):
        req = int(gdb.parse_and_eval("$rsi")) & 0xffffffff
        if req != NVME_ADMIN:
            return False
        i = gdb.selected_inferior()
        addr = int(gdb.parse_and_eval("$rdx"))
        raw = bytes(i.read_memory(addr, 72))
        f = struct.unpack(FMT, raw)
        out("ADMIN opc=0x%02x nsid=0x%x cdw2=0x%x cdw3=0x%x dlen=%d cdw10=0x%x cdw11=0x%x cdw12=0x%x cdw13=0x%x cdw14=0x%x cdw15=0x%x tmo=%d" %
            (f[0], f[3], f[4], f[5], f[9], f[10], f[11], f[12], f[13], f[14], f[15], f[16]))
        try:
            bt = []
            fr = gdb.newest_frame().older()
            k = 0
            while fr is not None and k < 7:
                bt.append(fr.name() or hex(fr.pc())); fr = fr.older(); k += 1
            out("  bt: " + " < ".join(bt))
        except Exception as e:
            out("  bt err %s" % e)
        if f[9] and f[7]:
            out("  data out (len %d):" % f[9])
            out(hexdump(bytes(i.read_memory(f[7], f[9]))))
        if NOEXEC:
            # allow only commands already observed as pure reads; kill before anything else executes
            ok = False
            if f[0] == 0x06:
                ok = True
            elif f[0] == 0xe2 and f[12] in (0xe0, 0xe1, 0xe2, 0xe5, 0x2e3):
                ok = True
            elif f[0] == 0xe2 and (f[12] & 0xffff) == 0x3e0:
                ok = True   # time sync, sent by every mnv_cli start
            elif f[0] == 0xce:
                ok = True   # fetch response of a pair command
            elif f[0] == 0xcb and f[9] and f[7]:
                req = bytes(i.read_memory(f[7], 16))
                # pair request: opcode e0/e2/e3 with sub 0 (get info) or e3/2 (BGA status)
                if req[8:12] in (b"\xe0\x00\x00\x00", b"\xe2\x00\x00\x00", b"\xe1\x00\x00\x00", b"\xe3\x00\x02\x00", b"\xe5\x00\x00\x00"):
                    ok = True
            if not ok:
                out("  ** NOEXEC: killed before this command was executed")
                LOG.flush()
                os.kill(i.pid, 9)      # tracee is stopped at the breakpoint; SIGKILL ends it before the ioctl runs
                os._exit(0)
        try:
            Fin(gdb.newest_frame(), addr, f)
        except Exception as e:
            out("  fin err %s" % e)
        return False

gdb.execute("set pagination off")
gdb.execute("set confirm off")
gdb.execute("set breakpoint pending on")
Brk("ioctl")
gdb.execute("run")
gdb.execute("quit")
