# AN1500 修改工具和定制固件

[README in English](README_en_US.md)

详情见 [https://cyberbus.net/post/40](https://cyberbus.net/post/40)

## 1. 直接使用修改版固件刷写

如果您不想自己动手修改，只想让 AN1500 支持 RAID0/1/JBOD 和 UEFI 菜单，可以直接运行：

```bash
./flash_fw.sh ./an1500_backup_20241107_mod.bin
```

## 2. 提取 AN1500 固件，修改 hbainfo 和 Option ROM

### 备份固件

```
./firmware_backup.sh an1500_backup.bin
```

您可以根据文章或视频说明将其裁剪为 7MB 大小的文件

### 刷写固件（使用 mnv_cli）

推荐使用此方法刷写固件

```
./flash_fw.sh an1500_backup.bin
```

### 强制刷写固件（使用 mvf）

如果您的设备无法使用 mnv_cli 刷写固件，您需要：

1. 在内核中启用 `iomem=relaxed` 
2. 使用 mvf 刷写固件

```
./force_flash_fw.sh an1500_backup.bin
```

## 3. 文件说明

* `crc_fix.py` - 用于修复 7MB 固件并计算校验和，您需要将计算出的校验和替换到 7MB 固件末尾
* `oprom_full.bin` - 这是一个功能完整的选项 ROM，用于替换 7MB 固件中 0x60_0000 位置的内容
* `hbainfo.bin` - 这是用于解锁 RAID0/1/JBOD 功能的 HBA 信息结构，用于替换 7MB 固件中 0x2_0000 位置的内容


## 4. an1500ctl (recommended runtime control tool)

`an1500ctl.py` talks to the controller with the same vendor NVMe admin commands used by `mnv_cli`,
but decodes replies directly and shows JBOD correctly.

Examples:

```
sudo ./an1500ctl.py list
sudo ./an1500ctl.py info hba
sudo ./an1500ctl.py create -r jbod -d 0 -y
sudo ./an1500ctl.py create -r raid0 -d 0,1 -b 256 -y
sudo ./an1500ctl.py delete -i 0 -y
sudo ./an1500ctl.py delete -i 0 --force -y
sudo ./an1500ctl.py tui
```

Safety notes:

* create/delete require confirmation unless `-y` is provided.
* delete checks mounts/swap/holders/openers and refuses by default when a volume is in use.
* `--force` bypasses in-use blocking checks and can corrupt active workloads.
* `--dry-run` prints the exact request payload(s) and does not send anything to the card.

See `docs/an1500ctl.md` for protocol details and validation notes.
