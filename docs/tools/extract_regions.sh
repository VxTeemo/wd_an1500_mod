#!/bin/bash
# Extract the small flash regions analysed in docs/flash-layout.md from full 16MB dumps.
# usage: extract_regions.sh <before_flash.bin> <after_bios_raid1.bin> <after_cli_jbod.bin> <outdir>
set -e
B=$1; N=$2; C=$3; OUT=$4
X() { dd if="$1" of="$OUT/$2" bs=1 skip=$(($3)) count=$(($4)) status=none; }
X "$B" hbainfo_0x20000_1_before_flash.bin 0x20000 512
X "$N" hbainfo_0x20000_2_after_bios_raid1.bin 0x20000 512
X "$C" hbainfo_0x20000_3_after_cli_jbod.bin 0x20000 512
X "$C" hbainfo_0x220000_default.bin 0x220000 512
X "$C" hbainfo_0x820000_slotB.bin 0x820000 512
X "$C" oemdata_0x7d0000.bin 0x7d0000 0x90
dd if="$C" bs=4096 skip=$((0x750)) count=2 status=none | tr '\0' '\n' | grep -a SEQ > "$OUT/eventlog_0x750000.txt" || true
