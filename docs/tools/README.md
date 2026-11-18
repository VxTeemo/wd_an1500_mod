# 分析工具、抓包记录和数据片段

这些是分析过程中写的一次性脚本，从 `/tmp` 保存过来，质量是"能用"而不是"整洁"。部分脚本里写死了路径，用之前看一眼。

## tools/

### 抓 mnv_cli 的协议

| 文件 | 用途 |
|---|---|
| `mnvtrace.py` | gdb 的 Python 脚本：在 `ioctl` 上下断点，记录每条 `NVME_IOCTL_ADMIN_CMD` 的操作码、CDW、调用栈和收发数据。日志路径由环境变量 `MNV_LOG` 指定。`MNV_NOEXEC=1` 时，遇到第一条不在"已知只读命令"白名单里的命令，先记录、再在 ioctl 执行前杀掉进程 |
| `cap_noexec.sh <标签> <mnv_cli 参数…>` | 用 NOEXEC 模式运行 mnv_cli，打印被拦下的那条命令。日志写到 `/tmp/cap_<标签>.log`。**卡不会被改动** |
| `lastblk.py`、`trfilt.py` | 从日志里取最后一条命令 / 去掉初始化阶段的重复命令 |
| `mnv.gdb` | 打印 `RaidModeStr()` 收到的原始模式值和控制器的"支持模式"字。用法：`sudo gdb -q -batch -x docs/tools/mnv.gdb --args ./mnv_cli/mnv_cli info -o vd`（或 `-o hba`） |

真正执行并记录（不拦截）的用法：

```
sudo MNV_LOG=/tmp/trace.log gdb -q -batch -x docs/tools/mnvtrace.py --args ./mnv_cli/mnv_cli info -o vd
```

注意：

- NOEXEC 白名单在 `mnvtrace.py` 里：identify（0x06）、0xE2 的 0xE0/0xE1/0xE2/0xE5/0x2E3/0x3E0、0xCE，以及动作为 0 的 0xCB 查询。抓新命令（比如改名）时，被拦下的就是第一条新命令。
- 创建请求会在"查询容量"那一步被拦下（它和真正的创建只差负载 0x68 处的一个字节），所以 `cap_*.log` 里的创建请求都是 0x68 = 1 的版本。
- 日志里的十六进制只显示到最后一个非零字节。

### 反汇编 mnv_cli

先生成反汇编文本：`objdump -d -M intel --no-show-raw-insn mnv_cli/mnv_cli > /tmp/mnv.asm`。

| 文件 | 用途 |
|---|---|
| `fn.py <函数名…>` | 从 `/tmp/mnv.asm` 里取出函数，并在引用字符串的行尾标出字符串内容 |
| `st.py str <地址…>` | 读某个地址上的字符串 |
| `st.py find <文本…>` | 找字符串的地址和引用它的函数 |

两个脚本里 mnv_cli 的路径写死为 `/home/pynvme/src/wd_an1500_mod/mnv_cli/mnv_cli`。

### 测试 an1500ctl

| 文件 | 用途 |
|---|---|
| `verify_req.py single` / `pair` | 把 an1500ctl 生成的请求与 `traces/` 里厂商工具的请求逐字节比较，不接触硬件 |
| `tui_drive.py <TERM> <步骤…>` | 在伪终端里运行 `sudo ./an1500ctl.py tui`，按步骤发送按键，每步之后打印屏幕内容。**按键会真的操作卡** |
| `chkdisk.sh <块设备名>` | 检查一个块设备是否没人用且首尾 64MB 全零。其中 `/proc/diskstats` 的读写计数在这台机器上恒为 0，写入计数要看 `/sys/class/nvme/nvme0/nvme0c0nX/stat` 的第 7 列 |

### 分析 flash 转储

这些脚本里的转储路径写死为 `/tmp/an1500_now.bin`、`/tmp/an1500_cur2.bin`（已不保证存在），需要先重新转储（`sudo ./firmware_backup.sh /tmp/xxx.bin`）并改路径。

| 文件 | 用途 |
|---|---|
| `an1500_f28b.py` | 穷举找出 HBA info 0x28 字段的算法（结果：对 0x40–0x200 做 CRC32，不做最终取反） |
| `an1500_copies.py` | 列出各份 HBA info 的关键字段，并对比两份转储的差异区间 |
| `an1500_find.py`、`an1500_str.py`、`an1500_base.py` | 在转储里找子系统 ID / 签名、找相关字符串、猜装载基址和非空区域 |
| `dis.sh <文件> <偏移> <长度> <arm\|thumb>` | 用 `arm-none-eabi-objdump` 反汇编转储的一段 |
| `extract_regions.sh` | 生成 `data/` 里那些片段用的脚本 |

早期的两个脚本（比较镜像差异、验证 512 字节求和为 0）在 `/tmp` 里丢失了，没有保存下来；它们的结论已写进 [../flash-layout.md](../flash-layout.md)。

## traces/

gdb 截获的 mnv_cli 命令记录。`tr_*` 是**真正执行了**的，`cap_*` 是 NOEXEC 模式下**被拦下、没有执行**的。

| 文件 | mnv_cli 命令 | 当时的卡状态 |
|---|---|---|
| `tr_hba.log`、`tr_vd.log`、`tr_pd.log` | `info -o hba` / `vd` / `pd` | 两个 JBOD 卷 |
| `tr_ns.log` | `ns --list` | 两个 JBOD 卷 |
| `tr_del.log` | `delete -i 1 --waiveconfirmation` | 删除了 VD 1 |
| `tr_cre.log` | `create -r JBOD -d 1` | 重新建了 VD 1 |
| `cap_jbod1.log`、`cap_r0_1.log` | `create -r JBOD -d 1`、`create -r 0 -d 1` | 只有 VD 0；两者请求相同（单盘 RAID0 被当作 JBOD） |
| `cap_jbod1_name.log` | `create -r JBOD -d 1 -n "My Disk"` | 同上 |
| `cap_r0_1_b256.log`、`cap_r0_1_b512_none.log` | `create -r 0 -d 1 -b 256`；`… -b 512 -i none` | 同上 |
| `cap_r0_01.log`、`cap_r1_01.log` | `create -r 0 -d 0,1`、`create -r 1 -d 0,1` | 没有卷 |
| `cap_r0_01_b256.log` | `create -r 0 -d 0,1 -b 256 -n fast_1` | 没有卷 |
| `cap_jbod0.log` | `create -r JBOD -d 0` | 没有卷 |

日志格式：每条命令以 `ADMIN opc=…` 开头，接着是调用栈（`bt:`）、发出的数据（`data out`）、返回值和收到的数据（`data in`）。0xCE 命令的 `data out` 是 mnv_cli 没清空的旧缓冲区，没有意义。

## data/

从 flash 转储里截出来的小片段，含义见 [../flash-layout.md](../flash-layout.md)。

| 文件 | 内容 |
|---|---|
| `hbainfo_0x20000_1_before_flash.bin` | 刷写前生效的 HBA info（13:58） |
| `hbainfo_0x20000_2_after_bios_raid1.bin` | 用户在 BIOS 建 RAID1 之后（14:46），子系统 ID 已变回 WD |
| `hbainfo_0x20000_3_after_cli_jbod.bin` | 命令行建两个 JBOD 之后（14:57） |
| `hbainfo_0x220000_default.bin` | 0x220000 处的默认副本 |
| `hbainfo_0x820000_slotB.bin` | 槽 B 的副本，与仓库的 `hbainfo.bin` 相同 |
| `oemdata_0x7d0000.bin` | OEM 数据块（0x90 字节） |
| `eventlog_0x750000.txt` | 事件日志文本（14:57） |

刷入解锁镜像后、重启前的 0x20000 内容就是仓库的 `hbainfo.bin`，没有单独保存。
