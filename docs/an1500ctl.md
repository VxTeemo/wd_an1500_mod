# an1500ctl.py：新控制程序

文件：[an1500ctl.py](../an1500ctl.py)，单文件，只用 Python 标准库，兼容 Python 3.8（`sudo python3` 是 3.8.10）。需要 root。

目的：替代 `mnv_cli` 的查看、创建、删除功能，并正确显示 JBOD（mnv_cli 把它显示成 RAID0，见 [protocol.md](protocol.md) 第 1 节）。

## 1. 用法

```
sudo ./an1500ctl.py                      # 等同于 list
sudo ./an1500ctl.py list                 # 卷和物理盘的汇总表
sudo ./an1500ctl.py info [hba|vd|pd|ns]  # 详细信息
sudo ./an1500ctl.py --json list          # JSON 输出（list / info 都支持）
sudo ./an1500ctl.py create -r jbod  -d 0 [-n 名称] [-y] [--dry-run]
sudo ./an1500ctl.py create -r raid0 -d 0,1 [-b 128|256|512] [-i quick|none]
sudo ./an1500ctl.py create -r raid1 -d 0,1
sudo ./an1500ctl.py delete -i <VD编号> [-y] [--force] [--dry-run]
sudo ./an1500ctl.py tui                  # 全屏界面
```

- 全局选项 `--device /dev/nvmeX`（默认自动找 `1b4b:2241`）、`--debug`（把每条 admin 命令打印到 stderr）要写在子命令之前。
- `create` / `delete` 默认要求输入 `yes` 确认；`-y` 跳过；标准输入不是终端又没有 `-y` 时直接拒绝。
- `--dry-run` 只打印将要发送的请求（十六进制），不发送。
- TUI 按键：上/下选卷，`c` 创建，`d` 删除，`r` 刷新，`q` 退出；每 3 秒自动刷新；确认操作要按大写 `Y`。

## 2. 结构

| 部分 | 内容 |
|---|---|
| 协议常量与 `build_*` / `parse_*` 函数 | 请求编码和应答解码，纯函数，可以脱离硬件测试 |
| `Card` | 打开 `/dev/nvmeX`，`simple()`（0xE2 读命令）、`pair()`（0xCB/0xCE 命令对）、`snapshot()`（一次读全控制器、盘、卷、命名空间）、`create_vd()`、`delete_vd()` |
| `check_create()`、`vd_usage()` | CLI 和 TUI 共用的校验：创建参数是否合法；卷是否被挂载、作 swap、被 device-mapper 等占用，是否有分区或非空数据 |
| `cmd_*` | CLI 子命令 |
| `Tui` | curses 界面和对话框 |

设计上的几个决定：

- 读用 0xE2 简单命令，写用 0xCB/0xCE 命令对：这两种都是抓到 mnv_cli 实际在这张卡上成功执行过的。
- 创建流程和 mnv_cli 一样：先发"只查询容量"的请求，卡接受后再发真正的创建。
- 删除请求的后 5 个字节照抄 mnv_cli 实际发出的值（它没初始化这些字节）。
- RAID0 要求两块盘；单盘请用 JBOD（mnv_cli 会把单盘 RAID0 悄悄变成 JBOD）。
- JBOD 不受"支持的模式"位限制（0x04 位的含义是推断的）；RAID0/RAID1 必须在卡报告的支持列表里。
- 卷被挂载、作 swap 或被占用时拒绝删除，除非 `--force`；有分区或前 1 MiB 非空时只警告。
- 创建/删除之后写一次 `/sys/class/nvme/nvmeX/rescan_controller` 并等 1 秒（内核本来也会自动更新）。
- 没有实现 mnv_cli 每次启动都发的"设置时间"命令。

## 3. 测试情况（2026-11-18，在这张卡上）

### 已实测通过

| 项目 | 方法 |
|---|---|
| 请求编码 | [tools/verify_req.py](tools/verify_req.py)：10 种请求与厂商工具发出的字节逐一比对，全部相同（删除；JBOD、RAID0、RAID1 的创建和容量查询；自定义名称；条带 256/512；init none） |
| `list`、`info`、`info ns`、`--json`、`--debug` | 直接运行，与 `mnv_cli info` 的数值对照一致 |
| 无 root 运行 | 提示 "permission denied (run with sudo)"，返回码 2 |
| `delete -i N -y` | 删除 VD 1、VD 0（JBOD），以及测试用的 RAID0、RAID1 卷；之后 `mnv_cli info -o vd` 确认卷已不存在 |
| `create -r raid0 -d 0,1 -n stripe_test -b 256 -y` | 显示 RAID0、1907.6 GiB、条带 256K；`mnv_cli` 也显示 RAID0 / 256K |
| `create -r raid1 -d 0,1 -y` | 显示 RAID1、953.8 GiB；`mnv_cli` 也显示 RAID1 |
| `create -r jbod -d 0 -y` | 显示 JBOD；gdb 读到原始模式值 0x0F |
| `delete --dry-run`、各种拒绝情况 | 卷数已满、VD 不存在、非终端且没有 `-y` |
| TUI 启动、主界面、创建对话框打开和取消、无卷时按 `d` 的提示、`q` 退出 | [tools/tui_drive.py](tools/tui_drive.py) 在伪终端里驱动，无异常，退出码 0 |
| TUI 创建 JBOD（PD 1） | 同上，按键序列 `c Enter Enter Enter Y`，界面显示 "Created JBOD volume 'VD_1'"，之后 `list` 确认 |

### 没有实测

- `create --dry-run` 的输出（试的时候卷数已满，先被拒绝了）。
- TUI 删除卷（确认对话框和实际删除）。
- TUI 创建 RAID0/RAID1（自动选两块盘、选条带大小）；多选对话框（这张卡只有两块盘，走不到）。
- 卷被挂载或占用时拒绝删除、`--force`、分区和非空数据的警告（测试时卷都是空的）。
- 交互式输入 `yes` 的确认路径。
- TUI 在真实终端里的显示和颜色（只用 `TERM=vt100` 跑过）；终端很窄或很矮时的表现。
- 卷状态不是 Functional、有后台任务、物理盘故障等情况的显示（没遇到过，解码来自反汇编）。
- 其他固件版本或其他 88NR2241 卡。

## 4. 继续测试的方法

1. 先确认两个卷仍然是空的、没人在用：`docs/tools/chkdisk.sh nvme0n1`、`docs/tools/chkdisk.sh nvme0n2`，并看 `/sys/class/nvme/nvme0/nvme0c0n*/stat` 的写入计数是 0。**如果用户已经开始使用这些盘，就不能再做删除测试。**
2. 改了请求编码之后跑 `python3 docs/tools/verify_req.py single` 和 `python3 docs/tools/verify_req.py pair`，应输出 `ALL IDENTICAL`。
3. TUI 测试：`python3 docs/tools/tui_drive.py vt100 <按键步骤…>`，步骤可用 `UP`、`DOWN`、`ENTER`、`ESC`、`SPACE`，`步骤@秒数` 指定等待时间。例如删除当前选中的卷：`python3 docs/tools/tui_drive.py vt100 d Y@5 q`（**会真的删除**）。
4. 测试后恢复现状：`sudo ./an1500ctl.py create -r jbod -d 0 -y`，再 `sudo ./an1500ctl.py create -r jbod -d 1 -y`（先建 PD 0，这样 VD 0 对应 `nvme0n1`）。

## 5. 可以继续做的事

- 在 README 里加上 `an1500ctl.py` 的用法。
- 补上第 3 节里没有实测的项目。
- 需要的话实现改名（`mnv_cli set -o vd -i N -n 名称`，对应 `MNV_VD_SetConfig`，协议还没抓）、后台任务控制、导入。抓协议的方法见 [tools/README.md](tools/README.md)。
