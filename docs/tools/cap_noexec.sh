#!/bin/bash
# cap.sh <tag> <mnv_cli args...>
# Run the vendor tool under the tracer in NOEXEC mode: every NVMe admin command is logged,
# and the first command that is not a known read is logged and the process is killed BEFORE
# the ioctl runs, so the card is not changed. Log: /tmp/cap_<tag>.log
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
TAG=$1; shift
cd "$ROOT"
sudo MNV_NOEXEC=1 MNV_LOG=/tmp/cap_$TAG.log gdb -q -batch -x "$HERE/mnvtrace.py" --args ./mnv_cli/mnv_cli "$@" > /tmp/capg_$TAG.txt 2>&1
echo "== $TAG: $*"
python3 "$HERE/lastblk.py" /tmp/cap_$TAG.log
grep -a -i -E "success|fail|error|invalid|not " /tmp/capg_$TAG.txt | head -5
