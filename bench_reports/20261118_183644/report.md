# AN1500 RAID0/RAID1/JBOD 测速报告

- 测试时间: 2026-11-18
- 控制器: WD_BLACK AN1500 (/dev/nvme0)
- 工具: an1500ctl.py + fio
- 说明: 所有测试均为原始块设备 direct I/O，测试会覆盖目标设备数据。

## 1. 模式切换结果

- RAID0: 创建成功，单卷 1907.6 GiB（PD 0,1）
- RAID1: 创建成功，单卷 953.8 GiB（PD 0,1）
- JBOD: 创建成功，双卷 953.8 GiB + 953.8 GiB（PD 0 / PD 1）

证据文件:
- raid0_layout.txt
- raid1_layout.txt
- jbod_layout.txt
- 当前状态已通过 an1500ctl.py list 复核并恢复为双 JBOD

## 2. 测试参数

- fio 通用参数: ioengine=libaio, direct=1, group_reporting=1, randrepeat=0, norandommap=1
- 主测试时长: 20s（RAID0/RAID1/JBOD 单盘）
- JBOD 双盘并发补测时长: 10s
- 负载:
  - 顺序读: rw=read, bs=1M, iodepth=32, numjobs=1
  - 顺序写: rw=write, bs=1M, iodepth=32, numjobs=1
  - 随机读: rw=randread, bs=4k, iodepth=32, numjobs=4
  - 随机写: rw=randwrite, bs=4k, iodepth=32, numjobs=4

## 3. 测试结果（关键指标）

### 3.1 RAID0（20s）

| 项目 | 带宽 | IOPS |
|---|---:|---:|
| seq_read_1m | 6719 MiB/s | 6718 |
| seq_write_1m | 5047 MiB/s | 5046 |
| rand_read_4k | 4934 MiB/s | 1263k |
| rand_write_4k | 2872 MiB/s | 735k |

### 3.2 RAID1（20s）

| 项目 | 带宽 | IOPS |
|---|---:|---:|
| seq_read_1m | 6633 MiB/s | 6632 |
| seq_write_1m | 2508 MiB/s | 2508 |
| rand_read_4k | 5002 MiB/s | 1281k |
| rand_write_4k | 1318 MiB/s | 337k |

### 3.3 JBOD 单盘（/dev/nvme0n1, 20s）

| 项目 | 带宽 | IOPS |
|---|---:|---:|
| seq_read_1m | 3241 MiB/s | 3241 |
| seq_write_1m | 3206 MiB/s | 3206 |
| rand_read_4k | 2961 MiB/s | 758k |
| rand_write_4k | 1331 MiB/s | 341k |

### 3.4 JBOD 双盘并发聚合（10s 补测）

| 项目 | 带宽 | IOPS |
|---|---:|---:|
| seq_read_1m | 6157 MiB/s | 6157 |
| seq_write_1m | 5006 MiB/s | 5006 |
| rand_read_4k | 5352 MiB/s | 1370k |
| rand_write_4k | 2964 MiB/s | 759k |

## 4. 结论

- 顺序读: RAID0 ≈ RAID1 > JBOD 单盘；JBOD 双盘并发接近 RAID0。
- 顺序写: RAID0 ≈ JBOD 双盘并发 > JBOD 单盘 > RAID1。
- 随机读: RAID1 与 RAID0 接近，JBOD 双盘并发最高，JBOD 单盘明显较低。
- 随机写: RAID0 与 JBOD 双盘并发接近，RAID1 显著较低（镜像写入开销）。

## 5. 原始数据位置

- 汇总表: summary.tsv
- 详细日志: raw/*.txt
