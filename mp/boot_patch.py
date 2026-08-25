from sys import stderr
import os
import subprocess
from os import unlink
from os import name as osname
from os.path import isfile, isdir, basename, join, expanduser
import logging
from shutil import copyfile, rmtree

from .lang import Language, langget

def get_download_dir():
    """Return the system download directory, fallback to home."""
    home = expanduser("~")
    for d in ("Downloads", "下载"):
        p = join(home, d)
        if isdir(p):
            return p
    return home

def cp(src, dest):
    if isfile(src):
        copyfile(src, dest)

def rm(*files):
    for i in files:
        if isdir(i):
            rmtree(i)
        else:
            if isfile(i):
                unlink(i)

def grep_prop(key, file) -> str:
    with open(file, 'r') as f:
        for i in iter(f.readline, ""):
            if key in i:
                return i.split("=")[1].rstrip("\n")
    return ""

class BootPatcher(object):
    def __init__(
        self,
        magiskboot,
        keep_verity: bool = True,
        keep_forceencrypt: bool = True,
        patchvbmeta_flag: bool = False,
        recovery_mode: bool = False,
        legacysar: bool = False,
        progress=None,
        log=stderr,
        preinit_device: str = "",
    ):
        self.magiskboot = magiskboot

        self.keep_verity = keep_verity
        self.keep_forceencrypt = keep_forceencrypt
        self.patchvbmeta_flag = patchvbmeta_flag
        self.recovery_mode = recovery_mode
        self.legacysar = legacysar
        self.progress = progress
        # Optional PREINITDEVICE hint (e.g. "sda20"). When empty the line is
        # omitted from .backup/.magisk; magiskinit v26.1+ then skips the
        # preinit partition mirror mount and falls back to auto-detection.
        self.preinit_device = preinit_device

        self.log = log

        self.vendor_boot = False

        self.__check()
        self.__prepare_env()

    def __check(self):
        if not isfile(self.magiskboot):
            print(langget('cannot initilazed with magiskboot'), file=self.log)
            return False

    def __prepare_env(self):
        bool2str = lambda x: "true" if x else "false"
        self.env = {
            "KEEPVERITY": bool2str(self.keep_verity),
            "KEEPFORCEENCRYPT": bool2str(self.keep_forceencrypt),
            "PATCHVBMETAFLAG": bool2str(self.patchvbmeta_flag),
            "RECOVERYMODE": bool2str(self.recovery_mode),
            "LEGACYSAR": bool2str(self.legacysar),
            # Ignore win case sensitive
           "MAGISKBOOT_WINSUP_NOCASE": "1"
        }

    def __execv(self, cmd:list, timeout:int=600):
        """
        Run magiskboot command, already include magiskboot
        return returncode and output
        """
        full = [
            self.magiskboot,
            *cmd
        ]

        if osname == 'nt':
            # SEM_FAILCRITICALERRORS prevents the "DLL not found" system
            # dialog from popping up and hanging the patch indefinitely.
            creationflags = subprocess.CREATE_NO_WINDOW | 0x00008000
        else:
            creationflags = 0
        # Merge flags on top of the real system environment. Replacing the
        # whole env (as done previously) breaks magiskboot on Windows where
        # it needs SystemRoot/PATH to load its runtime DLLs.
        full_env = os.environ.copy()
        full_env.update(self.env)
        logging.info("Run command : \n"+ " ".join(full))
        try:
            ret = subprocess.run(full,
                                stderr=subprocess.STDOUT,
                                stdout=subprocess.PIPE,
                                shell=False,
                                env=full_env,
                                creationflags=creationflags,
                                timeout=timeout,
                                )
        except subprocess.TimeoutExpired:
            msg = langget('command timeout') % " ".join(full)
            logging.error(msg)
            print(msg, file=self.log)
            return -1, "timeout"
        except OSError as e:
            msg = langget('cannot run magiskboot') % (self.magiskboot, str(e))
            logging.error(msg)
            print(msg, file=self.log)
            return -1, str(e)
        out = ret.stdout.decode(encoding="utf-8", errors="replace")
        logging.info(out)
        return ret.returncode, out
    
    def __find_ramdisk(self):
        """
        Locate the ramdisk within unpacked boot image, matching
        the official boot_patch.sh search order.
        return (path, status, skip_backup)
        """
        for path in (
            "ramdisk.cpio",
            "vendor_ramdisk/init_boot.cpio",
            "vendor_ramdisk/ramdisk.cpio",
        ):
            if isfile(path):
                err, _ = self.__execv(["cpio", path, "test"])
                return path, err, ""
        # No ramdisk found, create one from scratch
        return "ramdisk.cpio", 0, "#"

    def patch(self, bootimg:str) -> bool:
        # Check bootimg exist
        if not isfile(bootimg):
            print(langget('boot image does not exist'), file=self.log)
            return False
        
        # Unpack bootimg
        print(langget('unpack boot image'), file=self.log)
        err, ret = self.__execv(["unpack", bootimg])
        logging.info(ret)

        match err:
            case 0: pass
            case 2:
                print(langget('chromeos format boot'), file=self.log)
                print(langget('not support yet'))
                return False
            case 3:
                print(langget('vendor boot image detected'), file=self.log)
                self.vendor_boot = True
            case _:
                msg = langget('unable to unpack boot')
                if ret and "timeout" not in ret:
                    msg += "\n" + ret.strip()
                print(msg, file=self.log)
                return False

        print(langget('check ramdisk status'), file=self.log)
        ramdisk, status, skip_backup = self.__find_ramdisk()

        sha = ""
        match (status & 3):
            case 0: # Stock boot
                print(langget('detect original boot'), file=self.log)
                err, ret = self.__execv(["sha1", bootimg])
                if err == 0:
                    lines = [l.strip() for l in ret.splitlines() if l.strip()]
                    sha = lines[0] if lines else ""
                cp(bootimg, "stock_boot.img")
                cp(ramdisk, "ramdisk.cpio.orig")
            case 1: # Magisk patched
                print(langget('detect magisk patched boot'), file=self.log)
                err, ret = self.__execv([
                    "cpio", ramdisk, "extract .backup/.magisk config.orig", "restore"
                ])
                cp(ramdisk, "ramdisk.cpio.orig")
                rm("stock_boot.img")
            case 2: # Unsupported
                print(langget('boot patched by unknow program'), file=self.log)
                print(langget('please resotre original boot'), file=self.log)
                return False

        if isfile("config.orig"):
            sha = grep_prop("SHA1", "config.orig")
            rm("config.orig")
        
        print(langget('patch ramdisk'), file=self.log)

        if not isfile("magisk"):
            print(langget('magisk binary not found'), file=self.log)
            return False

        # Compress to save precious ramdisk space
        self.__execv(["compress=xz", "magisk", "magisk.xz"])
        self.__execv(["compress=xz", "stub.apk", "stub.xz"])
        if isfile("init-ld"):
            self.__execv(["compress=xz", "init-ld", "init-ld.xz"])
        
        # newline='' forces LF only, matching the official Magisk app output.
        with open("config", 'w', newline='') as config:
            config.write(
                f"KEEPVERITY={self.env['KEEPVERITY']}" + "\n" +
                f"KEEPFORCEENCRYPT={self.env['KEEPFORCEENCRYPT']}" + "\n" +
                f"RECOVERYMODE={self.env['RECOVERYMODE']}" + "\n" +
                f"VENDORBOOT={'true' if self.vendor_boot else 'false'}" + "\n")
            if self.preinit_device:
                config.write(f"PREINITDEVICE={self.preinit_device}\n")
            if sha != "":
                config.write(f"SHA1={sha}\n")
        
        cpio_cmds = [
            "add 0750 init magiskinit",
            "mkdir 0750 overlay.d",
            "mkdir 0750 overlay.d/sbin",
            "add 0644 overlay.d/sbin/magisk.xz magisk.xz",
            "add 0644 overlay.d/sbin/stub.xz stub.xz",
        ]
        if isfile("init-ld.xz"):
            cpio_cmds.append("add 0644 overlay.d/sbin/init-ld.xz init-ld.xz")
        cpio_cmds += [
            "patch",
            f"{skip_backup} backup ramdisk.cpio.orig",
            "mkdir 000 .backup",
            "add 000 .backup/.magisk config",
        ]

        err, _ = self.__execv(["cpio", ramdisk, *cpio_cmds])
        if err != 0:
            print(langget('unable to patch ramdisk'), file=self.log)
            return False
        
        rm("ramdisk.cpio.orig", "config", "magisk.xz", "stub.xz", "init-ld.xz")
        
        for dt in "dtb", "kernel_dtb", "extra":
            if isfile(dt):
                err, _ = self.__execv([
                    "dtb", dt, "test"
                ])
                if err != 0:
                    print(langget('boot image patched by old magisk') %dt, file=self.log)
                    print(langget('please try again with original boot'), file=self.log)
                    return False
                err, _ = self.__execv([
                    "dtb", dt, "patch"
                ])
                if err == 0:
                    print(langget('patch boot image fstab') %dt)
        
        if isfile("kernel"):
            patchedkernel = False
            err, _ = self.__execv([
                "hexpatch", "kernel",
                "49010054011440B93FA00F71E9000054010840B93FA00F7189000054001840B91FA00F7188010054",
                "A1020054011440B93FA00F7140020054010840B93FA00F71E0010054001840B91FA00F7181010054"
            ])
            if err == 0: patchedkernel = True
            err, _ = self.__execv([
                "hexpatch", "kernel", "821B8012", "E2FF8F12"
            ])
            if err == 0: patchedkernel = True
            # Disable Samsung PROCA
            # proca_config -> proca_magisk
            err, _ = self.__execv([
                "hexpatch", "kernel",
                "70726F63615F636F6E66696700",
                "70726F63615F6D616769736B00"
            ])
            if err == 0: patchedkernel = True
            if self.legacysar:
                err, _ = self.__execv([
                    "hexpatch", "kernel",
                    "736B69705F696E697472616D667300",
                    "77616E745F696E697472616D667300"
                ])
                if err == 0: patchedkernel = True
            if not patchedkernel: rm("kernel")

        print(langget('repack boot image'), file=self.log)
        err, _ = self.__execv([
            "repack", bootimg
        ])
        if err != 0:
            print(langget('faild to repack boot image'), file=self.log)
            return False

        # Sanity-check the AVB footer: magiskboot copies the original
        # vbmeta/footer from <bootimg> into new-boot.img and updates only
        # vbmeta_offset; the original_image_size field must still describe
        # the *original* image size (not the patched one). If a future
        # magiskboot changes this behaviour, we want to know.
        self.__verify_footer(bootimg)

        self.cleanup()
        print(langget('done'), file=self.log)
        try:
            dest = join(get_download_dir(), "patched-" + basename(bootimg))
            copyfile("new-boot.img", dest)
            print(langget('saved to download dir') % dest, file=self.log)
        except Exception as e:
            print(langget('save to download dir failed') % str(e), file=self.log)
        return True

    def __verify_footer(self, orig_bootimg: str) -> None:
        """Compare the AVB footer of new-boot.img against orig_bootimg. The
        original_image_size field should describe the ORIGINAL image size, not
        the patched one; warn if a future magiskboot changes that contract.
        """
        try:
            import struct
            orig = open(orig_bootimg, 'rb').read()
            new = open("new-boot.img", 'rb').read()
            # AVB footer: magic(4) ver_major(4) ver_minor(4) orig_size(8) vbmeta_off(8) vbmeta_size(8) - big-endian
            o_footer = struct.unpack_from('>4sIIQQQ', orig, len(orig) - 64)
            n_footer = struct.unpack_from('>4sIIQQQ', new, len(new) - 64)
            if o_footer[0] != b'AVBf' or n_footer[0] != b'AVBf':
                return  # not AVB-signed, nothing to check
            if o_footer[3] != n_footer[3]:
                print(langget('footer verify failed') % (o_footer[3], n_footer[3]),
                      file=self.log)
        except Exception:
            # Verification is best-effort; never fail patch because of it
            pass

    def cleanup(self):
        rmlist = [
        "magisk", "magisk.xz", "magiskinit", "stub.apk", "stub.xz", "init-ld", "init-ld.xz"
        ]
        rm(*rmlist)
        print(langget('cleanup'), file=self.log)
        self.__execv(["cleanup"])

if __name__ == "__main__":
    print(grep_prop("B", "config"))
