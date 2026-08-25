# Magisk Patcher

A desktop tool for patching `boot.img` / `init_boot.img` with Magisk, running
entirely on your PC. No need to flash through the phone or use the Magisk app.

Built on top of the official [magiskboot](https://github.com/topjohnwu/Magisk)
binary and a CustomTkinter UI.

> Looking for the old name "Magisk Patcher v4" by affggh? That's this project,
> just renamed and cleaned up.

---

## Highlights

- **One-click patch** — pick a stock `boot.img` / `init_boot.img`, pick a Magisk
  APK, click a button. Done.
- **Multi-arch** — `arm64`, `arm`, `x86_64`, `x86`.
- **Multi-language UI** — English and Simplified Chinese.
- **Magisk v26.1+ aware** — exposes a *Preinit device* field so you can supply
  `sda20` etc. when the auto-detection isn't enough. See
  [PREINITDEVICE.md](./PREINITDEVICE.md).
- **.backup/.magisk output matches the official Magisk app** — same fields,
  same line endings, same SHA1 handling. Tested against `magisk_patched-30700`.
- **Cross-platform** — Windows (prebuilt magiskboot.exe), macOS (per-OS-version
  prebuilt binaries), Linux (magiskboot is extracted from the APK itself).
- **Downloadable from GitHub Releases** — Windows / Linux / macOS binaries
  built and uploaded by GitHub Actions on every tag.

---

## Quick Start

### Download a release

Grab the latest from
[Releases](https://github.com/affggh/magisk_patcher/releases) — `MagiskPatcher-windows.exe`,
`MagiskPatcher-linux`, or `MagiskPatcher-macos.zip`. Double-click, done.

### Run from source

```bash
git clone https://github.com/affggh/magisk_patcher
cd magisk_patcher
pip install -r requirements.txt
python magiskpatcher.py
```

On Windows you can also use `run.bat`.

### Workflow

1. **Home tab** → click *Choose file* and pick a stock (never-patched) `boot.img`
   or `init_boot.img`.
2. **Select and Download tab** → either enable *Use local file* and drop a
   Magisk `.apk` in the `prebuilt/` directory, or disable it and pick a version
   to download from the GitHub releases list.
3. Adjust the options if needed (defaults are safe for most devices):
   - **Keep verity** — preserve `dm-verity`. Off on most modern devices.
   - **Keep force-encrypt** — preserve force-encrypt. Off if you want FBE.
   - **Patch vbmeta flag** — disable AVB verification flags. Needed when the
     device refuses to boot with a modified boot image.
   - **Recovery** — patch a recovery image instead of a boot image.
   - **Legacy SAR** — enable for non-dynamic system-as-root devices.
   - **Preinit device** — optional. Magisk v26.1+ may need this; leave blank if
     unsure. See [PREINITDEVICE.md](./PREINITDEVICE.md).
4. Click **Start Patch**.
5. The patched image lands in your `Downloads` folder as
   `patched-<original-name>.img`.

---

## Documentation

- [PREINITDEVICE.md](./PREINITDEVICE.md) — what the *Preinit device* field does,
  when to fill it, and how to find the right value.

## Screenshots

| Home | Download | Other |
|---|---|---|
| ![](screenshots/home.png) | ![](screenshots/download.png) | ![](screenshots/other.png) |

---

## Project layout

```
magisk_patcher/
├── magiskpatcher.py        # entry point (creates the Tk root window)
├── requirements.txt
├── run.bat                 # Windows launcher (sets UTF-8 code page)
├── README.md               # this file
├── PREINITDEVICE.md        # optional-input documentation
├── LICENSE                 # GPLv3
├── mp/
│   ├── ui.py               # CustomTkinter UI
│   ├── boot_patch.py       # boots magiskboot, patches ramdisk/AVB/cmdline
│   ├── utils.py            # Magisk download / extract / network
│   ├── lang.py             # zh_CN / en_US string tables
│   ├── magisk_logo.py      # fallback logo bytes (used if bin/logo.png missing)
│   └── splash.py           # optional splash module (PyInstaller hook)
├── bin/
│   ├── windows/x86_64/magiskboot.exe
│   ├── macos/{11,12,13}/x86_64/magiskboot
│   └── logo.{png,ico}, splash.png, donation QR codes
├── screenshots/
└── .github/workflows/
    ├── build-desktop.yml   # multi-platform build (Win/Linux/macOS)
    └── build-windows.yml   # legacy, deprecated
```

## How it works

`mp/boot_patch.py` shells out to the `magiskboot` binary. The flow:

1. **unpack** — extract kernel, ramdisk, dtb, etc. from the input image.
2. **cpio test** — decide whether the input is stock (status 0), already
   Magisk-patched (status 1, restore backup first), or unsupported (status 2).
3. **Backup** — copy the original init to `/.backup/init.xz` (xz-compressed).
4. **Add Magisk components** — `magiskinit`, `magisk.xz`, `stub.xz`,
   `init-ld.xz` into the ramdisk.
5. **Patch ramdisk** — `magiskboot cpio patch` modifies init to load magiskinit.
6. **Patch kernel** (boot-only) — `hexpatch` to disable Samsung PROCA and
   patch `skip_initramfs` for legacy SAR.
7. **Patch dtb fstab** — strip verity/forceencrypt if the option is off.
8. **Write `.backup/.magisk`** — config file (LF-only) with the patch
   options, VENDORBOOT flag, optional PREINITDEVICE, and the SHA1 of the
   original boot image.
9. **repack** — `magiskboot repack` rebuilds the boot image, preserving the
   original AVB footer & vbmeta blob (only `vbmeta_offset` in the footer is
   updated to point to the relocated vbmeta).
10. **Save** — copy `new-boot.img` to `~/Downloads/patched-<orig-name>.img`.

The output matches what the official Magisk app's `magiskboot cpio patch`
emits, byte for byte for all Magisk components.

---

## Development

### Requirements

- Python ≥ 3.10
- `pip install -r requirements.txt`

### Smoke-testing a patch without a phone

If you have a previously-patched `boot.img` from the official Magisk app,
you can use it as a test input — the patcher will detect the
"already-magisk-patched" status and restore-then-repatch it:

```bash
python -c "
import sys, os, shutil
sys.path.insert(0, '.')
os.makedirs('prebuilt', exist_ok=True)
shutil.copy('path/to/Magisk-v30.7.apk', 'prebuilt/')
os.chdir('.cowork-test'); os.makedirs('prebuilt', exist_ok=True)
shutil.copy('../prebuilt/Magisk-v30.7.apk', 'prebuilt/')
from mp import utils
from mp.boot_patch import BootPatcher
utils.parseMagiskApk('prebuilt/Magisk-v30.7.apk', arch='arm64')
class L:
    def write(self, *a): print(*a)
    def flush(self): pass
p = BootPatcher(
    magiskboot='bin/windows/x86_64/magiskboot.exe',
    keep_verity=True, keep_forceencrypt=True,
    progress=None, log=L(),
    preinit_device='sda20',
)
shutil.copy('path/to/official_magisk_patched-30700.img', 'input-boot.img')
assert p.patch('input-boot.img')
"
```

The resulting `new-boot.img` should be byte-identical (modulo timestamps in
vbmeta) to what the Magisk app produces.

### Building binaries locally

Same flags as the GitHub Actions workflow:

```bash
pyinstaller --noconfirm --clean --onefile --windowed \
  --name MagiskPatcher \
  --icon bin/logo.ico \
  --collect-all customtkinter \
  --add-data "bin/windows/x86_64/magiskboot.exe;bin/windows/x86_64" \
  --add-data "bin/logo.ico;bin" \
  --add-data "bin/logo.png;bin" \
  --add-data "bin/splash.png;bin" \
  --add-data "bin/alipay.png;bin" \
  --add-data "bin/wechat.png;bin" \
  --add-data "bin/zfbhb.png;bin" \
  --add-data "PREINITDEVICE.md;." \
  magiskpatcher.py
```

Output: `dist/MagiskPatcher.exe` (~34 MB, onefile).

### CI

Tagged pushes (`v*`) trigger [.github/workflows/build-desktop.yml](.github/workflows/build-desktop.yml)
to build all three platforms and publish a GitHub Release. Manual dispatch
via *Run workflow* → enter a version → it tags and releases.

---

## Troubleshooting

**"magiskboot binary not found"**
On Linux the magiskboot is extracted from the Magisk APK. Make sure you've
selected/downloaded a Magisk APK first. On Windows / macOS the binary is
bundled; if missing, reinstall from a release tarball.

**"Cannot find preinit partition" at boot**
Fill the **Preinit device** field with your device's preinit partition node
(usually a letter-and-number ID like `sda20`, or `mmcblk0p45`).
See [PREINITDEVICE.md](./PREINITDEVICE.md).

**Device won't boot after flashing the patched image**
- Make sure you flashed `boot.img` to the right partition (`boot` or
  `init_boot`). Modern devices with `init_boot` need patching that partition,
  not `boot`.
- Verify the device's bootloader is unlocked.
- For AVB-locked devices, either also patch `vbmeta.img` to set the disable
  flag, or enable the *Patch vbmeta flag* option.

**GitHub API download fails**
Try enabling the *Use proxy* option (default address is `127.0.0.1:7890`,
clash-style) or *Use jsdelivr* mirror.

---

## Credits & License

- [magiskboot](https://github.com/topjohnwu/Magisk) — bootimg kernel, by topjohnwu
- [ookiineko](https://github.com/ookiineko) — multi-target magiskboot builds
- [svoboda18](https://github.com/svoboda18) — Windows mingw magiskboot
- [CustomTkinter](https://customtkinter.tomschimansky.com) — pretty Tkinter

GPLv3. See [LICENSE](./LICENSE).

## Donate

Scan the QR codes in `bin/`:

- WeChat: ![](bin/wechat.png)
- Alipay: ![](bin/alipay.png)
- Alipay Red Packet: ![](bin/zfbhb.png)