#!/bin/bash
# safety gate: is /dev/$1 unused and empty?  prints facts, exit 0 if looks unused
D=$1
echo "== $D"
grep -E " $D( |$)" /proc/diskstats | awk '{print "reads="$4" sectors_read="$6" writes="$8" sectors_written="$10" in_flight="$12}'
echo "mounts: $(grep -c "/dev/$D" /proc/mounts)  swaps: $(grep -c "/dev/$D" /proc/swaps)  holders: $(ls /sys/block/$D/holders 2>/dev/null | wc -l)  parts: $(ls /sys/block/$D | grep -c "^$D")"
echo "openers: $(sudo lsof /dev/$D 2>/dev/null | tail -n +2 | wc -l)"
echo "signatures: $(sudo wipefs -n /dev/$D | wc -l)"
if sudo cmp -s -n $((64*1024*1024)) /dev/$D /dev/zero; then echo "first64M: zero"; else echo "first64M: NONZERO"; fi
SZ=$(sudo blockdev --getsz /dev/$D)
if sudo dd if=/dev/$D bs=512 skip=$((SZ-131072)) count=131072 status=none | cmp -s -n $((64*1024*1024)) - /dev/zero; then echo "last64M: zero"; else echo "last64M: NONZERO"; fi
