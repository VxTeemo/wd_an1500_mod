set pagination off
set confirm off
break *0x439bef
commands
  silent
  printf "RAIDMODE_RAW=0x%x\n", $rdi & 0xff
  continue
end
break *0x40533d
commands
  silent
  printf "SUPPORTED_RAID_WORD=0x%x  flags30=0x%x\n", $rbx & 0xffff, *(unsigned int*)($rbp+0x30)
  x/64xb $rbp
  continue
end
run
quit
