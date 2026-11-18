import sys
# print last ADMIN block of a trace log (the killed command), without backtrace
t = open(sys.argv[1], errors="replace").read()
blocks = t.split("ADMIN ")
last = blocks[-1]
if "NOEXEC" not in last:
    print("  (no command was killed; last block follows)")
print("ADMIN " + "\n".join(l for l in last.rstrip().split("\n") if "bt:" not in l))
