# Magisk Patcher v4.2

A tool to patch boot / init_boot images with Magisk, running entirely on your desktop
(no need to flash through the phone).

> A static website alternative is also available:
> [circlecashteam.github.io/MagiskPatcher](https://circlecashteam.github.io/MagiskPatcher/)

---

## What's new in 4.2

- **`PREINITDEVICE` field in the UI** — add the preinit partition node (e.g. `sda20`)
  that Magisk 26.1+ needs to find its preinit partition. Leave blank for most devices
  (auto-detection works). See [PREINITDEVICE.md](./PREINITDEVICE.md) for details.
- **Per-option help text** — every patch option (keep verity, keep force-encrypt,
  patch vbmeta flag, recovery, legacy SAR, arch) now shows a one-line explanation
  inline.
- **Clickable "Read docs 📖" link** next to the preinit device field — opens
  `PREINITDEVICE.md` in your OS default handler.
- **Live status row** showing the selected Magisk APK and current architecture
  right above the Start Patch button.
- **Aligned output with the official Magisk app** — `.backup/.magisk` is now written
  with LF line endings and (optionally) the `PREINITDEVICE` field, matching what the
  app's `magiskboot cpio patch` emits.
- **AVB footer sanity check** — after repack we compare the new footer's
  `original_image_size` against the input image and warn if magiskboot ever changes
  the contract.
- **Robustness** — null/empty Magisk selection guarded in `start_patch`; bare
  `except:` narrowed to `requests.RequestException`; `getReleaseList` retries with
  exponential backoff; download chunk size 10 KiB → 256 KiB; cross-thread Tk widget
  access routed through a thread-safe queue.
- **Window 900×420 → 960×580** so the new row + status fit without cramping the log.

---

## Screenshots

| Home | Download | Other |
|---|---|---|
| ![](screenshots/home.png) | ![](screenshots/download.png) | ![](screenshots/other.png) |

## Usage

### Setup Python env

```bash
pip install -r requirements.txt
```

### Run

```bash
python magiskpatcher.py
```

Or on Windows: `run.bat`

### Workflow

1. Pick your stock `boot.img` (or `init_boot.img`).
2. Pick a Magisk APK (local file or fetched from the download tab).
3. Adjust options if needed (defaults are safe).
5. Optionally fill **Preinit device** (see [PREINITDEVICE.md](./PREINITDEVICE.md)).
6. Click **Start Patch**.
7. Output lands in your Downloads folder as `patched-<original-name>.img`.

## Theme

A very pretty tkinter theme built on top of
[CustomTkinter](https://customtkinter.tomschimansky.com).

## Magiskboot binary

- [magiskboot_on_mingw](https://github.com/svoboda18/magiskboot) (Windows)
- [magiskboot_build_on_all_target](https://github.com/ookiineko/magiskboot_build) (Linux)

## Thanks

- [ookiineko](https://github.com/ookiineko) — magiskboot multi-arch builds
- [svoboda18](https://github.com/svoboda18) — Windows mingw magiskboot
- [topjohnwu](https://github.com/topjohnwu/Magisk) — Magisk itself

## Donate me

![](bin/wechat.png) ![](bin/alipay.png) ![](bin/zfbhb.png)

## License

GPLv3. See [LICENSE](./LICENSE).