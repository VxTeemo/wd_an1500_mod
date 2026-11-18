"""Drive an1500ctl's TUI in a pseudo-terminal and print the rendered screens.
usage: tui_drive.py <term> <step> [<step> ...]   step = keys (use \\n for Enter, \\e for Esc, UP/DOWN words) 
"""
import fcntl, os, pty, re, select, struct, sys, termios, time

ROWS, COLS = 34, 118

class Screen:
    def __init__(self):
        self.g = [[" "] * COLS for _ in range(ROWS)]
        self.r = self.c = 0
    def feed(self, data):
        i = 0
        s = data.decode("latin-1")
        while i < len(s):
            ch = s[i]
            if ch == "\x1b":
                m = re.match(r"\x1b\[([0-9;?]*)([A-Za-z@])", s[i:])
                if m:
                    self.csi(m.group(1), m.group(2)); i += m.end(); continue
                m = re.match(r"\x1b[()][A-Za-z0-9]|\x1b[=>78DME]|\x1b#\d", s[i:])
                if m:
                    i += m.end(); continue
                i += 1; continue
            if ch == "\r": self.c = 0
            elif ch == "\n": self.r = min(ROWS - 1, self.r + 1)
            elif ch == "\b": self.c = max(0, self.c - 1)
            elif ch in "\x0e\x0f\x07\x00": pass
            elif ch >= " ":
                if self.c < COLS and self.r < ROWS:
                    self.g[self.r][self.c] = ch
                self.c += 1
            i += 1
    def csi(self, args, cmd):
        n = [int(x) if x.isdigit() else 0 for x in args.split(";")] if args else [0]
        a = n[0] or 1
        if cmd in "Hf":
            self.r = min(ROWS - 1, (n[0] or 1) - 1); self.c = min(COLS - 1, (n[1] if len(n) > 1 and n[1] else 1) - 1)
        elif cmd == "A": self.r = max(0, self.r - a)
        elif cmd == "B": self.r = min(ROWS - 1, self.r + a)
        elif cmd == "C": self.c = min(COLS - 1, self.c + a)
        elif cmd == "D": self.c = max(0, self.c - a)
        elif cmd == "G": self.c = min(COLS - 1, a - 1)
        elif cmd == "d": self.r = min(ROWS - 1, a - 1)
        elif cmd == "J":
            if n[0] == 2:
                self.g = [[" "] * COLS for _ in range(ROWS)]
            else:
                for c in range(self.c, COLS): self.g[self.r][c] = " "
                for r in range(self.r + 1, ROWS): self.g[r] = [" "] * COLS
        elif cmd == "K":
            for c in range(self.c, COLS): self.g[self.r][c] = " "
        elif cmd == "X":
            for c in range(self.c, min(COLS, self.c + a)): self.g[self.r][c] = " "
        elif cmd == "b":
            prev = self.g[self.r][self.c - 1] if self.c else " "
            for _ in range(a):
                if self.c < COLS: self.g[self.r][self.c] = prev
                self.c += 1
    def dump(self, title):
        print("=" * 20, title)
        lines = ["".join(row).rstrip() for row in self.g]
        while lines and not lines[-1]: lines.pop()
        for l in lines:
            print("|" + re.sub(r"[qxlkmjtuvwn]{3,}", lambda m: "-" * len(m.group()), l) if False else "|" + l)

term = sys.argv[1]
steps = sys.argv[2:]
pid, fd = pty.fork()
if pid == 0:
    os.environ["TERM"] = term
    os.environ["LINES"] = str(ROWS); os.environ["COLUMNS"] = str(COLS)
    os.chdir("/home/pynvme/src/wd_an1500_mod")
    os.execvp("sudo", ["sudo", "TERM=" + term, "./an1500ctl.py", "tui"])
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
scr = Screen()
raw = b""
def pump(t):
    global raw
    end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                d = os.read(fd, 65536)
            except OSError:
                return False
            if not d: return False
            raw += d; scr.feed(d)
    return True
pump(2.5)
scr.dump("start")
KEYS = {"UP": "\x1b[A", "DOWN": "\x1b[B", "ENTER": "\r", "ESC": "\x1b", "SPACE": " ", "BS": "\x7f"}
for step in steps:
    wait = 1.2
    if "@" in step:
        step, w = step.rsplit("@", 1); wait = float(w)
    for tok in step.split("+"):
        os.write(fd, KEYS.get(tok, tok).encode())
        time.sleep(0.15)
    alive = pump(wait)
    scr.dump("after " + step)
    if not alive: break
time.sleep(0.3)
try:
    _, status = os.waitpid(pid, os.WNOHANG)
except ChildProcessError:
    status = None
print("exit status:", status, "| traceback in output:", b"Traceback" in raw)
if b"Traceback" in raw:
    print(raw[raw.find(b"Traceback"):].decode("latin-1")[:1500])
