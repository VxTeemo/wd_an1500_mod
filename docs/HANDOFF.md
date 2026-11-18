# 交接说明（HANDOFF）

最后更新：2026-11-18 15:45（系统时间）。接手的 agent 请先读完本文件，再按需阅读同目录的其他文档。

| 文档 | 内容 |
|---|---|
| [protocol.md](protocol.md) | 卡的厂商 NVMe 命令协议、各结构体字段、mnv_cli 的显示错误 |
| [flash-layout.md](flash-layout.md) | SPI flash 布局、HBA info 结构与校验、解锁刷写及被还原的经过 |
| [an1500ctl.md](an1500ctl.md) | 新控制程序 `an1500ctl.py` 的设计、测试情况、未完成项 |
| [tools/README.md](tools/README.md) | 分析用脚本、抓包记录（`traces/`）、flash 片段（`data/`）的说明 |

文中每条结论都标了可信度：**已验证**（在这张卡上实际执行并观察到）、**推断**（有证据但没有直接验证）、**未验证**。

## 1. 硬件与环境

- 卡：WD_BLACK AN1500，主控 Marvell 88NR2241，PCI `01:00.0`，ID `1b4b:2241`，子系统 ID `1b96:4000`（WD 原厂）。
- 固件 1.0.27.1043，序列号 WUBT22230375，控制器设备 `/dev/nvme0`。
- 两块物理盘：KIOXIA KXG7AZNV1T02（PD 0 序列号 62MZZ014EG96，PD 1 序列号 62MZZ016EG96），各 2000409264 扇区。
- 主机：`TestMachine`，内核 5.15.0-139，系统盘是 `sda`（与本卡无关）。`sudo` 免密码。
- `sudo python3` 是 **Python 3.8.10**（`/usr/bin/python3`）；普通用户的 `python3` 是 3.13。需要 root 的脚本必须兼容 3.8。
- 可用工具：`gdb`（带 Python）、`arm-none-eabi-objdump`、`strace 5.5`（不解码 NVMe ioctl）、`xxd`。没有 capstone。

## 2. 卡的当前状态（已验证，15:45）

| 卷 | 名称 | 模式（原始值） | 物理盘 | 容量 | 系统设备 |
|---|---|---|---|---|---|
| VD 0 | VD_0 | JBOD（0x0F） | PD 0 | 2000277504 扇区（953.8 GiB） | `/dev/nvme0n1` |
| VD 1 | VD_1 | JBOD（0x0F） | PD 1 | 2000277504 扇区（953.8 GiB） | `/dev/nvme0n2` |

- 两个卷都是空的：没有分区和文件系统，创建后从未被写入（控制器路径统计 `sectors_written=0`）。
- 固件内容等同于刷写前：见 [flash-layout.md](flash-layout.md) 第 4 节。卡仍是 WD 原厂子系统 ID。
- **未验证**：重启后这两个 JBOD 卷是否保持（本次会话没有重启过）。

## 3. 用户的四次请求与结果

1. **备份固件、启用 UEFI 菜单、切换 JBOD**
   - 备份完成：`an1500_backup_20261118.bin`（16MB，两次读取一致）。
   - 按 README 刷入了解锁镜像 `an1500_20261118_mod.bin`（与仓库自带的 `an1500_backup_20241107_mod.bin` 完全相同），回读一致。
   - 卡原有的 option ROM 本来就与 `oprom_full.bin` 相同，UEFI 菜单刷写前就存在。
2. **用户在 BIOS 里建了 RAID1，要求确认并改成 JBOD**
   - 确认是 RAID1 后删除，用 `mnv_cli create -r JBOD -d <pd>` 每盘建一个卷。
   - 发现解锁已被还原（HBA info 里的子系统 ID 又变回 WD）。
   - **当时的错误结论**：因为 `mnv_cli info` 显示 "RAID0"，我说它们是"单盘 RAID0，不是真 JBOD"。这是错的。
3. **用户要求"真正的 JBOD"**
   - 反汇编 mnv_cli 后发现是它的显示错误：模式值 0x0F（JBOD）被打印成 "RAID0"。用 gdb 读到两个卷的原始模式值都是 0x0F。
   - 结论：原厂 ID 下 JBOD 就能创建，不需要解锁，也就没有再刷固件。
4. **写一个新的控制程序，消除显示错误，支持 CLI 和 TUI**
   - 写了 [an1500ctl.py](../an1500ctl.py)，测试情况见 [an1500ctl.md](an1500ctl.md)。
   - 测试时把两个空卷删除并重建过多次，最后恢复成上表的状态。

## 4. 未完成的工作

按优先级：

1. **TUI 创建 RAID0/RAID1 路径没有实测**（包括条带选择、多选分支；这张卡只有两盘，仍有分支覆盖不足）。
2. **分区/非空数据告警分支没有实测**（当前卷是空盘）。
3. **TUI 只在伪终端里用 `TERM=vt100` 跑过**（无颜色），没有在真实终端里看过效果。
4. **重启后配置是否保持**没有验证。
5. **解锁被还原的原因没有查清**（两个假设见 [flash-layout.md](flash-layout.md) 第 5 节）。因为 JBOD 不需要解锁，这一项目前没有实际需求。
6. an1500ctl 没有实现的功能：改名、后台任务控制（初始化/重建/巡检）、导入外来卷、刷固件、时间同步。

本轮已完成更新（2026-11-18 晚）：

- README 已加入 `an1500ctl.py` 用法。
- TUI 删除（普通路径）已实测通过：`d -> Y`。
- 卷占用检查与 `--force` 已实测通过（CLI）：默认拒绝，`--force` 可继续。
- TUI 强制删除路径已实测通过：`d -> F -> Y`（会先显示 in-use 风险提示）。
- CLI 交互式确认已实测通过：`create` / `delete` 均手动输入 `yes` 成功。

## 5. 操作安全规则

- **刷固件有变砖风险**。卡如果不再出现在 PCI 总线上，`mvf` 也救不回来，只能用编程器。现在没有任何需求要再刷。
- **不要刷 `an1500_backup.bin` / `an1500_backup.bak.bin` / `an1500_backup_7MB.bin`**：它们是工作区里原有的文件（2024-10-30），内容与这张卡不同，来源不明。这张卡的备份是 `an1500_backup_20261118.bin`。
- **不要同时运行两个 `mvf`**，也不要在 `mnv_cli flash` 运行期间做别的操作。`mnv_cli flash` 大约要 2 分钟，中途没有输出。
- **删除卷之前先确认卷是空的、没人在用**：用 [tools/chkdisk.sh](tools/chkdisk.sh)，并看 `/sys/class/nvme/nvme0/nvme0c0n*/stat` 的写入计数（`/sys/block/nvme0n*/stat` 和 `/proc/diskstats` 在这台机器上恒为 0，不能用）。
- **想知道厂商工具会发什么命令而又不想执行**：用 [tools/cap_noexec.sh](tools/cap_noexec.sh)，它在写命令的 ioctl 执行前杀掉进程。
- 用户明确说过不想进 BIOS 操作。

## 6. 这个环境里的坑

- 终端工具经常返回 "Failed to retrieve command output"：把输出重定向到文件再 `cat`。命令实际上已经执行了，不要盲目重跑有副作用的命令。
- 一条命令没返回时，后面的命令会在同一个终端里排队，可能和前一条并发（曾因此让两个 `mvf` 同时写同一个文件）。先用 `pgrep` 确认前一条已经结束。
- 终端里的多行 heredoc 会出问题：把脚本写成文件再执行。
- 文件读取工具有时读不到终端刚在 `/tmp` 里生成的文件：改用 `cat`。
- `/tmp` 里的东西会丢：本次会话的两个早期脚本已经不见了。要保留的东西放进 `docs/`。
- `cp` 在这台机器上是 `advcp` 的别名，任何一个源文件不存在就整体失败。
- 在 gdb 的断点回调里执行 `kill` 会让 gdb 卡住：用 `os.kill(pid, 9)` 加 `os._exit(0)`（[tools/mnvtrace.py](tools/mnvtrace.py) 已这样处理）。

## 7. 工作区文件清单

| 文件 | 说明 | md5 |
|---|---|---|
| `an1500_backup_20261118.bin` | **这张卡刷写前的完整 16MB 备份** | fd96439ba12a908e539d9ad5d1bf87a0 |
| `an1500_backup_20261118_7MB.bin` | 上面的前 7MB | 5044c78413b36fef918d0cc396e16b2b |
| `an1500_20261118_mod.bin` | 用这张卡的备份做的解锁镜像（已刷入过） | 3d2c2e22a961aee6f79ffd9b628a95ed |
| `an1500_backup_20241107_mod.bin` | 仓库自带的解锁镜像，与上一个完全相同 | 3d2c2e22a961aee6f79ffd9b628a95ed |
| `an1500_backup.bin`、`an1500_backup.bak.bin` | 原有文件，**不是这张卡的内容**，来源不明 | 364b202cbb1dee925f0a9fa75104afbe |
| `an1500_backup_7MB.bin` | 上一个的前 7MB | 5a9a4b80e849eae20b65a5d1c8b45de0 |
| `hbainfo.bin`、`oprom_full.bin` | 仓库自带的解锁 HBA info 和 option ROM | e0909b7d…、10ad4e06… |
| `an1500ctl.py` | 新控制程序（未提交） | - |
| `firmware_backup.sh` | 已修改：原来忽略参数、总是写 `an1500_backup.bin`，现在写到指定文件 | - |

git 状态：`firmware_backup.sh` 有内容改动；三个 `.sh` 的可执行位改动在会话开始前就有；`an1500ctl.py`、`docs/` 和各 `.bin` 未跟踪。没有做过任何提交。
