# PREINITDEVICE 选项说明

## 背景

从 **Magisk v26.1** 开始，magiskinit 在第一阶段启动时需要访问一个 **"preinit" 分区**
（通常就是 `/data` 所在的 userdata 分区，或者设备厂商单独划的一块小分区），用来在
`/data` 还没挂载之前就能读写 magisk 的运行时配置。

为了让 magiskinit 找到这个分区，社区版/官方 app 在打包 boot 镜像时会在
`/.backup/.magisk` 配置里写一行：

```
PREINITDEVICE=sda20
```

告诉 magiskinit："设备节点 sda20 就是 preinit 分区，去挂载它"。

## 不写会会

如果 `.backup/.magisk` 里**没有** `PREINITDEVICE=...` 行（也就是这个字段为空），
v26.1 之后的 magiskinit（我们用的是 v30.7）会执行：

```cpp
void MagiskInit::mount_preinit_dir() noexcept {
    if (preinit_dev.empty()) return;   // <-- 留空 → 直接返回，啥也不做
    ...
}
```

也就是说 **完全跳过** preinit 分区的提前挂载。对于以下：

- 非 FBE（File-Based Encryption）设备
- userdata 在常规块设备上、能被 magiskinit 通过其他线索找到的设备

magisk 仍然可以正常启动并取得 root，只是失去了"在 /data mount 之前就拿到 magisk 配置"
的能力。这对绝大多数设备（包括你们手上这种 Xiaomi pandora）**没有可见影响**。

只有在以下少数场景才会出问题：
- FBE 设备 + magiskinit 在 /data mount 之前必须访问 /data/adb（例如为了模块/zygisk 早期加载）
- 部分 OEM 的特殊分区布局（dynamic partition / vendor 分区需要 preinit 中转）

## UI 行为

- **留空**（默认）：打包产物里没有 `PREINITDEVICE` 行，magiskinit 自动跳过 preinit 镜像挂载。绝大多数设备可用。
- **填一个值**（例如 `sda20`）：打包产物写入 `PREINITDEVICE=sda20`，magiskinit 会用这个节点去挂载 preinit 分区。

## 怎么知道要不要填、填什么

绝大多数用户**留空就行**。

只有遇到以下情况，才考虑手动填：
- 刷完之后启动卡死在 Magisk 启动阶段，logcat 里有 `Cannot find preinit` 字样
- 你已经知道自己的 preinit 分区设备节点（少数小米/三星特殊机型）

设备节点填法示例：
- `/dev/block/sda20` → 填 `sda20`
- `/dev/block/mmcblk0p45` → 填 `mmcblk0p45`

## 对应到官方 Magisk App

官方 Magisk App 在 Android 上跑 patcher 时，能直接从 `/proc/cmdline` 读出当前设备
的 partname，然后自动写入 `PREINITDEVICE=<partname>`。这是 Magisk_Patcher 这种桌面
工具**没办法自动获取**的信息（它跑在 PC 上，不知道你的手机），所以只能手动留空或
手动填。

参考源码（Magisk v30.7，`native/src/init/mount.cpp`）：

```cpp
void MagiskInit::mount_preinit_dir() noexcept {
    if (preinit_dev.empty()) return;
    auto dev = find_block(preinit_dev.c_str());
    ...
}
```

`preinit_dev` 由 `parse_config_file()`（`native/src/init/rootdir.rs`）从
`/data/.backup/.magisk`（ramdisk 里 `/.backup/.magisk` 的拷贝）里读
`PREINITDEVICE` key 填充。

## 相关

- `mp/boot_patch.py` 的 `BootPatcher.__init__(..., preinit_device="")` 参数控制这个字段
- `mp/ui.py` 里的 "Preinit device" 输入框是 UI 入口
- 没有这个字段时产物的 `.backup/.magisk` 仍包含其他所有必要字段（`KEEPVERITY` /
  `KEEPFORCEENCRYPT` / `RECOVERYMODE` / `VENDORBOOT` / `SHA1`），不刷坏 boot