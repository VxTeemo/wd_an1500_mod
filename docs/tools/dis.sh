#!/bin/bash
# usage: dis.sh <file> <start_hex> <len_hex> <arm|thumb> ; prints disassembly with file offsets
F=$1; S=$2; L=$3; M=$4
dd if="$F" of=/tmp/_dis.bin bs=1 skip=$((S)) count=$((L)) status=none
if [ "$M" == "thumb" ]; then OPT="-M force-thumb"; else OPT=""; fi
arm-none-eabi-objdump -D -b binary -marm $OPT --adjust-vma=$S /tmp/_dis.bin
