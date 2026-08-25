import customtkinter as ctk
from tkinter.ttk import Progressbar
from PIL import Image
from io import BytesIO
from os import getcwd, makedirs, walk, listdir
import os.path as op
from os import name as osname
from shutil import copyfile
from sys import version as pyversion
from sys import argv
import sys
import subprocess
import webbrowser
import logging
from multiprocessing.dummy import DummyProcess
from queue import Queue, Empty
from threading import Event

from . import utils
from . import boot_patch

# Logo source: prefer bundled bin/logo.png on disk (lighter source), fall
# back to the inline PNG bytes if the file is missing (e.g. unusual setups).
try:
    from .magisk_logo import rawdata as _fallback_logodata
except Exception:
    _fallback_logodata = None

def _load_logo_bytes():
    """Load the bundled Magisk logo PNG from disk instead of inlining 228 KB into source."""
    candidates = [
        op.join(bundle_dir(), "bin", "logo.png"),
        op.join(getcwd(), "bin", "logo.png"),
    ]
    for p in candidates:
        if op.isfile(p):
            try:
                with open(p, "rb") as f:
                    return f.read()
            except OSError:
                continue
    return _fallback_logodata

# multi lang support
from .lang import Language

if osname == 'nt':
    import ctypes

if osname == 'nt':
    EXT = ".exe"
else:
    EXT = ""

class _DualProgress:
    """Adapter that mirrors a download progress value into two tk.Variable
    objects: a numeric bar (0-100) and a percent text label. Lets downloadFile
    stay agnostic about how the UI exposes progress.
    """
    __slots__ = ("_bar", "_text")
    def __init__(self, bar, text):
        self._bar = bar
        self._text = text
    def set(self, pct: int):
        try:
            pct = int(pct)
        except (TypeError, ValueError):
            return
        if pct < 0: pct = 0
        elif pct > 100: pct = 100
        self._bar.set(pct)
        self._text.set(f"{pct}%")

class _QueueLogger:
    """File-like sink that pushes strings onto a thread-safe Queue instead of
    writing directly to Tk widgets. The main thread drains via _drain_log_queue.
    """
    __slots__ = ("_q",)
    def __init__(self, q: Queue):
        self._q = q
    def write(self, *args):
        try:
            self._q.put(" ".join(str(a) for a in args))
        except Exception:
            pass
    def flush(self):
        pass

VERSION = "4.2.0"
AUTHOR = "affggh"
TITLE = "Magisk Patcher v%s by %s" % (VERSION, AUTHOR)
WIDTH = 960
HEIGHT = 580
OS, REL, ARCH = utils.retTypeAndMachine()
LICENSE = "GPLv3"
INTRODUCE = """\
- Native OS    \t: %s
- Native Arch  \t: %s
- Version      \t: %s
- Author       \t: %s
- License      \t: %s
- PythonVersion : %s
- Work Dir     \t: %s
- 介绍：
\tMagisk Patcher 是由 %s 开发的用来在桌面系统上修补手机magisk的一个简单的小程序，基于官方的magiskboot
- 感谢:
\t- magiskboot on mingw32 from https://github.com/svoboda18/magiskboot
\t- customtkinter ui界面库，有一说一确实好看
""" %(f'{OS} ({REL})' if REL else OS, ARCH, VERSION, AUTHOR, LICENSE, pyversion, getcwd(), AUTHOR)
def bundle_dir() -> str:
    # Locate bundled resources: _MEIPASS when frozen by PyInstaller,
    # otherwise the directory of this script
    if getattr(sys, 'frozen', False):
        return op.abspath(getattr(sys, '_MEIPASS', op.dirname(argv[0])))
    return op.abspath(op.dirname(argv[0]))

if OS == 'windows':
    prebuilt_magiskboot = op.abspath(op.join(bundle_dir(), "bin", OS, ARCH, "magiskboot" + EXT))
elif OS == 'macos':
    # macOS version directories are named 11/12/13...; fall back to the
    # highest available version when the running macOS is not bundled.
    mac_dir = op.join(bundle_dir(), "bin", OS, REL, ARCH)
    base_mac = op.join(bundle_dir(), "bin", OS)
    if not op.isfile(op.join(mac_dir, "magiskboot" + EXT)):
        cands = []
        try:
            for d in listdir(base_mac):
                if op.isfile(op.join(base_mac, d, ARCH, "magiskboot" + EXT)):
                    cands.append(d)
        except OSError:
            pass
        if cands:
            cands.sort(key=lambda s: [int(p) for p in s.split('.') if p.isdigit()] or [0])
            mac_dir = op.join(base_mac, cands[-1], ARCH)
    prebuilt_magiskboot = op.abspath(op.join(mac_dir, "magiskboot" + EXT))
else:
    # Linux: magiskboot is extracted from the APK (statically linked
    # host-arch binary) into the current working dir, see parseMagiskApk.
    prebuilt_magiskboot = op.abspath(op.join(getcwd(), "bin", "magiskboot" + EXT))

def visit_customtkinter_website(event):
    webbrowser.open("https://customtkinter.tomschimansky.com")

def visit_magisk_website(event):
    webbrowser.open("https://github.com/topjohnwu/Magisk")

class MagiskPatcherUI(ctk.CTk):
    def __init__(self, *args):
        super().__init__(*args)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.lang = ctk.StringVar(value=Language.supports[0])
        self.lang_dict = getattr(Language, self.lang.get())

        self.logo = ctk.CTkImage(Image.open(BytesIO(_load_logo_bytes() or b''), "r"), size=(220, 90))
        self.bootimg = ctk.StringVar()
        self.arch = ctk.StringVar()
        self.magisk_select = ctk.StringVar(value=self.langget('magisk is not select'))
        self.magisk_select_int = ctk.StringVar()

        self.keep_verity = ctk.BooleanVar(value=True)
        self.keep_forceencrypt = ctk.BooleanVar(value=True)
        self.patchvbmeta_flag = ctk.BooleanVar(value=False)
        self.recoverymode = ctk.BooleanVar(value=False)
        self.legacysar = ctk.BooleanVar(value=False)

        # Optional Magisk v26.1+ preinit device hint, e.g. "sda20". When left
        # blank the line is omitted from .backup/. magisk and magiskinit falls
        # back to auto-detecting the preinit partition. See PREINITDEVICE.md.
        self.preinit_device = ctk.StringVar(value="")

        self.progress = ctk.DoubleVar(value=0)
        self.progress_text = ctk.StringVar(value="0%")
        self.loglevel = ctk.IntVar(value=logging.WARNING)
        # Cross-thread log queue: workers put() messages; main thread drains.
        self._log_queue = Queue()
        # Background patch worker state.
        self._patch_done = Event()
        self._patch_ok = [False]  # single-element list for thread-safe write

        # download
        self.isproxy = ctk.BooleanVar(value=False)
        self.proxy = ctk.StringVar(value="127.0.0.1:7890") # default clash

        self.ismirror = ctk.BooleanVar(value=False)
        self.mirror = ctk.StringVar(value="")

        self.isjsdelivr = ctk.BooleanVar(value=False)

        self.uselocal = ctk.BooleanVar(value=True)
        self.usedeltamagisk = ctk.BooleanVar(value=False)

        # magisk list
        self.magisk_list = []

        self.__setup_widgets()

        # Live-update the arch status label when the user picks a different arch.
        self.arch.trace_add('write', lambda *_: self.arch_status_label.configure(
            text=self.langget('arch status') % (self.arch.get() or 'arm64')))

        # Start the queue-drain loop so workers can safely log to the UI.
        self.after(100, self._drain_log_queue)

        # initial

        # log
        logging.basicConfig(level=logging.DEBUG)
        self.log = logging.getLogger()
        self.loghandler = logging.StreamHandler(self)
        logformatter = logging.Formatter("%(asctime)s - %(filename)s[line: %(lineno)d] - %(levelname)s: \n\t%(message)s")
        self.loghandler.setFormatter(logformatter)
        self.loghandler.setLevel(logging.DEBUG)
        self.log.addHandler(self.loghandler)

        self.log.setLevel(logging.WARN)

        print("- Detect env:", file=self)
        if REL:
            print(f"\tOS \t: {OS} ({REL})", file=self)
        else:
            print(f"\tOS \t: {OS}", file=self)
        print(f"\tARCH\t: {ARCH}", file=self)
        print(f"\tCurrent Dir\t: {getcwd()}", file=self)
        if OS in ['windows', 'macos']:
            print(f"- Windows/macOS Use prebuilt magiskboot.", file=self)
            print(f"\tFile should be here: {prebuilt_magiskboot}", file=self)
            if not op.isfile(prebuilt_magiskboot):
                print("- Error: Cannot find prebuilt magiskboot.", file=self)
                print("\tFix this to patch boot image correctly.", file=self)
        elif OS == 'linux':
            print(f"- Linux use magisk.apk inner magiskboot (host arch).", file=self)
            print(f"\tIt is extracted to: {prebuilt_magiskboot}", file=self)
            print("\tIt will extract when patching a boot image.", file=self)
        
    # as stdout, you can print(..., file=self)
    def write(self, *args):
        msg = " ".join(str(a) for a in args)
        if not hasattr(self, "_log_buf"):
            self._log_buf = []
        self._log_buf.append(msg)
        # Coalesce high-frequency log writes (one Tk update per ~50ms)
        try:
            self.after(50, self._flush_log)
        except Exception:
            # 'after' fails if called from a non-main thread; flush inline
            self._flush_log()

    def _flush_log(self):
        buf = getattr(self, "_log_buf", None)
        if not buf:
            return
        try:
            self.textbox.insert('end', "".join(buf))
            # Cap to ~1000 lines to bound memory in long sessions
            line_count = int(self.textbox.index('end-1c').split('.')[0])
            if line_count > 2000:
                self.textbox.delete('1.0', f'{line_count - 2000}.0')
            self.textbox.yview('end')
        finally:
            self._log_buf.clear()

    def _drain_log_queue(self):
        """Drain messages enqueued from background workers (thread-safe)."""
        try:
            while True:
                msg = self._log_queue.get_nowait()
                if msg is None:
                    break
                self._log_buf.append(msg)
        except Empty:
            pass
        if self._log_buf:
            self._flush_log()
        # Reschedule
        try:
            self.after(100, self._drain_log_queue)
        except Exception:
            pass

    def flush(self): # void flush function
        pass

    def langget(self, key: str) -> str:
        if self.lang_dict.get(key):
            return self.lang_dict.get(key)
        else: return key

    def __setup_widgets(self):
        self.navigation_frame = ctk.CTkFrame(self, corner_radius=0)
        self.navigation_label = ctk.CTkLabel(
            self.navigation_frame, image=self.logo, compound="left", text=""
        )
        self.navigation_label.bind("<Button-1>", visit_magisk_website)
        self.navigation_label.pack(side="top", fill="x")

        self.patcher_frame_button = ctk.CTkButton(
            self.navigation_frame,
            height=30,
            text=self.langget('Home'),
            fg_color="transparent",
            text_color=("gray10", "gray90"),
            hover_color=("gray70", "gray30"),
            corner_radius=0,
            anchor="w",
            border_spacing=10,
            font=ctk.CTkFont(size=20),
            command=self.change_frame_patcher,
        )
        self.patcher_frame_button.pack(side="top", fill="x")
        self.download_frame_button = ctk.CTkButton(
            self.navigation_frame,
            height=30,
            text=self.langget('Select and Download'),
            fg_color="transparent",
            text_color=("gray10", "gray90"),
            hover_color=("gray70", "gray30"),
            corner_radius=0,
            anchor="w",
            border_spacing=10,
            font=ctk.CTkFont(size=20),
            command=self.change_frame_download,
        )
        self.download_frame_button.pack(side="top", fill="x")
        self.other_frame_button = ctk.CTkButton(
            self.navigation_frame,
            height=30,
            text=self.langget('Other'),
            fg_color="transparent",
            text_color=("gray10", "gray90"),
            hover_color=("gray70", "gray30"),
            corner_radius=0,
            anchor="w",
            border_spacing=10,
            font=ctk.CTkFont(size=20),
            command=self.change_frame_other,
        )
        self.other_frame_button.pack(side="top", fill="x")

        self.theme_select_button = ctk.CTkSegmentedButton(
            self.navigation_frame,
            corner_radius=0,
            values=["dark", "light", "system"],
            command=self.change_theme,
        )
        self.theme_select_button.set("system")
        self.theme_select_button.pack(side="bottom", fill="x")

        lang_frame = ctk.CTkFrame(self.navigation_frame, corner_radius=0)
        lang_label = ctk.CTkLabel(lang_frame, text="🌏", font=ctk.CTkFont(size=25), anchor='center', compound='center')
        lang_combo = ctk.CTkComboBox(lang_frame, corner_radius=0, values=Language.supports, variable=self.lang)
        lang_button = ctk.CTkButton(lang_frame, text="Confirm", command=self.refresh_widgets, width=80)
        lang_label.pack(side='left')
        lang_combo.pack(side='left', fill='x', expand='yes', padx=5)
        lang_button.pack(side='left')
        lang_frame.pack(side='bottom', pady=5, fill='x')

        base_on_label = ctk.CTkLabel(self.navigation_frame, text=self.langget('Based on CustomTkinter'), text_color=('blue', 'light blue'), font=ctk.CTkFont(underline=True), anchor='w')
        base_on_label.pack(side="bottom", fill="x", padx=5, pady=5)
        base_on_label.bind("<Button-1>", visit_customtkinter_website)

        self.navigation_frame.grid(row=0, rowspan=2, column=0, sticky="nsew")

        self.patcher_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.download_frame = ctk.CTkFrame(
            self, corner_radius=0, fg_color="transparent"
        )
        self.other_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        file_select_frame = ctk.CTkFrame(self.patcher_frame, corner_radius=5)
        file_select_label = ctk.CTkLabel(file_select_frame, text=self.langget('boot img'))
        file_select_entry = ctk.CTkEntry(file_select_frame, textvariable=self.bootimg)
        file_select_button = ctk.CTkButton(
            file_select_frame, text=self.langget('choose file'), command=self.file_choose_dialog
        )
        file_select_label.pack(side="left", padx=5, pady=5)

        file_select_entry.pack(side="left", fill="x", expand="yes")
        file_select_button.pack(side="right", padx=5, pady=5)
        file_select_frame.pack(side="top", fill="x", padx=5, pady=5)

        config_frame = ctk.CTkFrame(self.patcher_frame, corner_radius=5)

        # Smaller font for inline help text under each switch.
        help_font = ctk.CTkFont(size=10)
        help_color = ("gray50", "gray70")

        def _labeled_switch(parent, text, variable, help_key, row, col):
            """Create a switch with a one-line help label below it."""
            switch = ctk.CTkSwitch(parent, text=text, variable=variable)
            switch.grid(column=col, row=row, padx=5, pady=(5, 0), sticky='w')
            help_lbl = ctk.CTkLabel(parent, text=self.langget(help_key),
                                    text_color=help_color, font=help_font,
                                    wraplength=140, justify='left')
            help_lbl.grid(column=col, row=row+1, padx=(20, 5), pady=(0, 5), sticky='w')
            return switch

        arch_select_label = ctk.CTkLabel(config_frame, text=self.langget('arch') + ':',
                                        font=ctk.CTkFont(weight='bold'))
        arch_select_button = ctk.CTkSegmentedButton(
            config_frame,
            values=["arm64", "arm", "x86_64", "x86"],
            corner_radius=50,
            variable=self.arch,
        )
        arch_select_button.set("arm64")
        arch_select_label.grid(column=0, row=0, padx=5, pady=5, sticky='e')
        arch_select_button.grid(column=1, columnspan=4, row=0, sticky="nsew", padx=5, pady=5)

        # Row 1: primary switches
        _labeled_switch(config_frame, self.langget('keep verity'), self.keep_verity,
                       'help keep verity', 1, 1)
        _labeled_switch(config_frame, self.langget('keep encypt'), self.keep_forceencrypt,
                       'help keep forceencrypt', 1, 2)
        _labeled_switch(config_frame, self.langget('patch vbmeta flag'), self.patchvbmeta_flag,
                       'help patch vbmeta flag', 1, 3)
        _labeled_switch(config_frame, self.langget('recovery'), self.recoverymode,
                       'help recovery', 1, 4)

        # Row 3: legacy sar spans width with help
        _labeled_switch(config_frame, self.langget('legacy sar'), self.legacysar,
                       'help legacy sar', 3, 1)

        # Row 5: optional preinit device (Magisk v26.1+).
        preinit_device_label = ctk.CTkLabel(config_frame, text=self.langget('preinit device'),
                                            font=ctk.CTkFont(weight='bold'))
        preinit_device_label.grid(column=1, row=5, padx=5, pady=5, sticky='e')
        preinit_device_entry = ctk.CTkEntry(config_frame,
                                            textvariable=self.preinit_device,
                                            placeholder_text=self.langget('preinit device placeholder'),
                                            width=140)
        preinit_device_entry.grid(column=2, row=5, sticky='w', padx=5, pady=5)
        preinit_device_hint = ctk.CTkLabel(config_frame,
                                           text=self.langget('preinit device hint'),
                                           text_color=('gray50', 'gray70'),
                                           font=ctk.CTkFont(size=11),
                                           wraplength=380,
                                           justify='left')
        preinit_device_hint.grid(column=3, row=5, columnspan=2, sticky='w', padx=5, pady=5)
        # "Read docs" link label - opens PREINITDEVICE.md in OS default handler.
        preinit_doc_link = ctk.CTkLabel(config_frame,
                                        text=self.langget('open doc') + " \U0001F4D6",
                                        text_color=('blue', 'light blue'),
                                        cursor='hand2',
                                        font=ctk.CTkFont(size=11, underline=True))
        preinit_doc_link.grid(column=3, row=6, columnspan=2, sticky='w', padx=5, pady=(0, 5))
        preinit_doc_link.bind("<Button-1>",
                              lambda _e: self._open_preinit_doc())

        config_frame.pack(side="top", fill="x", expand="no", padx=5, pady=5)

        confirm_frame = ctk.CTkFrame(self.patcher_frame, corner_radius=5)
        # Left side: status panel (selected APK, arch, preinit hint).
        confirm_left = ctk.CTkFrame(confirm_frame, fg_color='transparent')
        self.apk_status_label = ctk.CTkLabel(confirm_left,
                                             text=self.langget('no apk selected'),
                                             text_color=('gray40', 'gray60'),
                                             font=ctk.CTkFont(size=12),
                                             anchor='w')
        self.apk_status_label.pack(side='top', fill='x', padx=5, pady=(5, 0))
        self.arch_status_label = ctk.CTkLabel(confirm_left,
                                              text=self.langget('arch status') % (self.arch.get() or 'arm64'),
                                              text_color=('gray40', 'gray60'),
                                              font=ctk.CTkFont(size=11),
                                              anchor='w')
        self.arch_status_label.pack(side='top', fill='x', padx=5, pady=(0, 5))
        confirm_left.pack(side='left', fill='x', expand='yes')

        # Right side: action buttons.
        textbox_clear_button = ctk.CTkButton(confirm_frame, text=self.langget('clean'),
                                              command=lambda: self.textbox.delete('1,0', 'end'))
        textbox_clear_button.pack(side='right', padx=5, pady=5)
        confirm_button = ctk.CTkButton(
            confirm_frame, text=self.langget('start patch'),
            fg_color='green', hover_color='dark green',
            width=140, height=36,
            command=self.start_patch,
        )
        confirm_button.pack(side='right', anchor='e', padx=5, pady=5)

        confirm_frame.pack(side='top', fill='x', padx=5, pady=5)

        progress_frame = ctk.CTkFrame(self, corner_radius=0)
        self.progress_label = ctk.CTkLabel(progress_frame, text=self.langget('progress')+":")
        #progress_bar = ctk.CTkProgressBar(progress_frame, variable=self.progress)
        #progress_bar._determinate_value = 100
        progress_bar = Progressbar(progress_frame, variable=self.progress, maximum=100)

        self.progress_label.pack(side='left', padx=5)
        progress_bar.pack(side='left', expand='yes', padx=5, fill='x')


        progress_process = ctk.CTkLabel(progress_frame, textvariable=self.progress_text, width=40, anchor='e')
        progress_process.pack(side='left', padx=5)

        progress_frame.grid(row=1, column=1, sticky='ew')

        # keep_verity_checkbox = ctk.CTkCheckBox(self.patcher)

        self.textbox = ctk.CTkTextbox(self.patcher_frame, border_width=0, corner_radius=20,
                                  font=ctk.CTkFont(family='Consolas', size=11))
        self.textbox.pack(side='top', fill='both', padx=5, pady=5, expand='yes')

        textbox_clear_button = ctk.CTkButton(confirm_frame, text=self.langget('clean'), command=lambda: self.textbox.delete(1.0, 'end'))
        textbox_clear_button.pack(side='right', padx=5, pady=5)

        # Download Frame
        self.download_list_frame = ctk.CTkScrollableFrame(self.download_frame, corner_radius=5, label_text=self.langget('available magisk list'))
        download_config_frame = ctk.CTkFrame(self.download_frame, corner_radius=5)

        download_setting_label = ctk.CTkButton(download_config_frame, state='disable', text=self.langget('settints'), fg_color=('grey78', 'grey23'), text_color=('black', 'grey85'), width=200)
        download_setting_label.pack(side='top', fill='x', padx=5, pady=5)

        download_proxy_frame = ctk.CTkFrame(download_config_frame)
        download_proxy_checkbox = ctk.CTkSwitch(download_proxy_frame, text=self.langget('use proxy'), variable=self.isproxy)
        download_proxy_checkbox.pack(side='top', fill='x', anchor='w', padx=5, pady=5)
        # bind if proxy is not allow, then forget proxy url label
        download_proxy_checkbox.bind("<Button-1>", self.update_proxy_widgets)

        self.download_proxy_url = ctk.CTkEntry(download_proxy_frame, textvariable=self.proxy)
        #self.download_proxy_url.pack(side='top', fill='x', anchor='w', padx=5, pady=5)

        download_proxy_frame.pack(side='top', fill='x', padx=5, pady=5, expand='no')
        
        
        # mirror source list
        download_mirror_frame = ctk.CTkFrame(download_config_frame)
        download_mirror_checkbox = ctk.CTkSwitch(download_mirror_frame, text=self.langget('github mirror'), variable=self.ismirror)
        download_mirror_checkbox.pack(side='top', fill='x', padx=5, pady=5, expand='no')
        download_mirror_checkbox.bind("<Button-1>", self.update_mirror_widgets)

        self.download_mirror_label = ctk.CTkEntry(download_mirror_frame, placeholder_text=self.langget('input your git mirror here'))

        download_mirror_frame.pack(side='top', fill='x', padx=5, pady=5, expand='no')

        # jsdelivr
        downlaod_jsdelivr = ctk.CTkSwitch(download_config_frame, text=self.langget('use jsdelivr'), variable=self.isjsdelivr)
        downlaod_jsdelivr.pack(side='top', padx=10, pady=5, fill='x')

        download_config_local_frame = ctk.CTkFrame(download_config_frame)
        download_use_local_checkbox = ctk.CTkSwitch(download_config_local_frame, text=self.langget('use local file'), variable=self.uselocal)
        download_use_local_checkbox.bind("<Button-1>", self.update_local_widgets)
        download_use_local_checkbox.pack(side='top', padx=5, pady=5, fill='x')

        # delta switch
        self.download_delta_magisk = ctk.CTkSwitch(download_config_local_frame, text=self.langget('use delta magisk'), variable=self.usedeltamagisk)
        download_config_local_frame.pack(side='top', fill='x', padx=5, pady=5)

        download_refresh_button = ctk.CTkButton(download_config_frame, text=self.langget('refresh list'), command=self.refresh_magisk)
        download_refresh_button.pack(side='bottom', fill='x', padx=10, pady=5)

        download_upload_button = ctk.CTkButton(download_config_frame, text=self.langget('upload local apk'), command=self.upload_local_apk)
        download_upload_button.pack(side='bottom', fill='x', padx=10, pady=5)

        download_config_frame.pack(side='left', fill='both', padx=5, pady=5, expand='no')
        self.download_list_frame.pack(side='left', fill='both', padx=5, pady=5, expand='yes')

        # other frame
        other_frame = ctk.CTkFrame(self.other_frame)
        other_introduce_label = ctk.CTkButton(other_frame, state='disable', text=self.langget('introduce'), fg_color=('grey78', 'grey23'), text_color=('black', 'grey85'))
        other_introduce_label.pack(side='top', padx=5, pady=5, fill='x')
        other_introduce_logo = ctk.CTkLabel(other_frame, text="        Magisk Patcher", font=ctk.CTkFont(size=30, weight='bold'), image=ctk.CTkImage(Image.open(BytesIO(_load_logo_bytes() or b'')), size=(240,100)), compound='left', anchor='sw')
        other_introduce_logo.pack(side='top', fill='x', anchor='w')
        other_introduce_full = ctk.CTkTextbox(other_frame, font=ctk.CTkFont("console"), height=160)
        other_introduce_full.insert('end', INTRODUCE)
        other_introduce_full.configure(state='disable')
        other_introduce_full.pack(side='top', padx=5, pady=5, fill='both', anchor='w', expand='yes')
        #other_introduce_longlabel = ctk.CTkLabel()
        other_button_frame = ctk.CTkFrame(other_frame)
        other_visit_button = ctk.CTkButton(other_button_frame, text=self.langget('vist github'), command=lambda: webbrowser.open("https://github.com/affggh/magisk_patcher"))
        other_visit_button.grid(column=0, row=0, padx=5, pady=5)

        loglevel_label = ctk.CTkLabel(other_button_frame, text=self.langget('log level'))
        loglevel_label.grid(column=1, row=0, padx=5, pady=5)

        loglevel_slide_bar = ctk.CTkSlider(other_button_frame, from_=logging.DEBUG, to=logging.CRITICAL, number_of_steps=4, variable=self.loglevel, command=self.set_log_level)
        loglevel_slide_bar.grid(column=2, row=0, padx=5, pady=5)
        loglevel_slide_bar.set(logging.WARN) # Default loglevel
        ctk.CTkLabel(other_button_frame, textvariable=self.loglevel).grid(column=3, row=0, padx=5, pady=5)

        ctk.CTkLabel(other_button_frame, text=self.langget('scaling')+":").grid(column=4, row=0, padx=(5,0), pady=5)
        scaling_bar = ctk.CTkOptionMenu(other_button_frame, values=["0.75", "0.8", "1.0", "1.25", "1.5", "2"], command=self.ui_scaling_event)
        scaling_bar.set("1.0")
        scaling_bar.grid(column=5, row=0, padx=5, pady=5)
        other_button_frame.pack(side='top', padx=5, pady=5, fill='x', expand='no')

        other_frame.pack(side='top', padx=5, pady=5, fill='both', expand='yes')

        self._change_frame_byname("patcher")

    def start_patch(self):
        if not op.isfile(self.bootimg.get()):
            print(self.langget('please select a exist boot image'), file=self)
            return

        apk_name = self.magisk_select_int.get()
        if not apk_name:
            print(self.langget('please select a valid magisk apk'), file=self)
            return
        apk_path = op.join("prebuilt", apk_name)
        if not op.isfile(apk_path):
            print(self.langget('please select a valid magisk apk'), file=self)
            return

        # Reset completion state.
        self._patch_done.clear()
        self._patch_ok[0] = False

        def do_patch():
            # Use a queue for log output to avoid cross-thread Tk widget access.
            qlog = _QueueLogger(self._log_queue)
            try:
                magisk_version = utils.getMagiskApkVersion(apk_path)
                qlog(f"{self.langget('detect select magisk version is')} [{str(utils.convertVercode2Ver(magisk_version))}]")

                utils.parseMagiskApk(apk_path, arch=self.arch.get(), log=qlog)

                patcher = boot_patch.BootPatcher(prebuilt_magiskboot,
                                                 self.keep_verity.get(),
                                                 self.keep_forceencrypt.get(),
                                                 self.patchvbmeta_flag.get(),
                                                 self.recoverymode.get(),
                                                 self.legacysar.get(),
                                                 self.progress,
                                                 qlog,
                                                 preinit_device=self.preinit_device.get().strip())
                ok = patcher.patch(self.bootimg.get())
                result_key = 'done' if ok else 'faild to repack boot image'
                qlog(f"\n*** {self.langget(result_key)} ***")
                self._patch_ok[0] = ok
            except Exception as e:
                qlog(f"\n!!! patch error: {e}")
                self._patch_ok[0] = False
            finally:
                self._patch_done.set()

        DummyProcess(target=do_patch).start()
        # Poll for completion on the main thread.
        self.after(200, self._check_patch_complete)

    def _check_patch_complete(self):
        if not self._patch_done.is_set():
            try:
                self.after(200, self._check_patch_complete)
            except Exception:
                pass
            return
        ok = self._patch_ok[0]
        self.title(TITLE + ("  \u2713" if ok else "  \u2717"))
        self.progress_text.set("0%")
        self.progress.set(0)

    def upload_local_apk(self):
        fname = ctk.filedialog.askopenfilename(
            title=self.langget('select local magisk apk'),
            filetypes=[("APK files", "*.apk"), ("All files", "*.*")])
        if not fname:
            return
        makedirs("prebuilt", exist_ok=True)
        dest = op.join("prebuilt", op.basename(fname))
        if op.abspath(fname) != op.abspath(dest):
            copyfile(fname, dest)
        self.magisk_select_int.set(op.basename(fname))
        self.magisk_select.set(f"- {self.langget('current magisk')} [{op.basename(fname)}]")
        self.apk_status_label.configure(text=self.langget('apk status') % op.basename(fname))
        print(f"- {self.langget('upload local apk done')}[{dest}]", file=self)
        self.change_frame_patcher()

    def refresh_magisk(self):
        def download(magisk: str):
            if not self.uselocal.get():
                if not op.isdir(op.join("prebuilt")):
                    makedirs("prebuilt", exist_ok=True)

                if not op.isfile(op.join("prebuilt", magisk)):
                    print(f"{self.langget('file not exist, ready to download')} [{magisk}]", file=self)
                    if not self.isjsdelivr.get() and self.ismirror.get():
                        print(self.langget('use mirror download'), file=self)
                        url = magisk_list[magisk].replace(self.mirror.get().rstrip('/'), "https://github.com")
                    else:
                        url = magisk_list[magisk]
                    utils.thdownloadFile(url, 
                                         op.join("prebuilt", magisk), 
                                         self.isproxy.get(), 
                                         self.proxy.get(), 
                                         _DualProgress(self.progress, self.progress_text), 
                                         _QueueLogger(self._log_queue))
                else:
                    print(self.langget('file exist, no need download'), file=self)
            self.magisk_select.set(f"- {self.langget('current magisk')} [{magisk}]")
            self.apk_status_label.configure(text=self.langget('apk status') % magisk)
            self.change_frame_patcher()

        print(self.langget('refresh magisk list'), file=self)

        # delete widgets
        for i in self.magisk_list:
            i.destroy()
        self.magisk_list = []

        if self.uselocal.get():
            print(self.langget('use from local prebuilt dir'), file=self)
            if not op.isdir("prebuilt"):
                print(self.langget('no magisk in prebuilt, please downloadn and place'), file=self)
                print(f"\t{self.langget('work dir')}: {getcwd()}", file=self)
                makedirs("prebuilt", exist_ok=True)
                
            magisk_list = []
            
            for root, dirs, files in walk("prebuilt"):
                for file in files:
                    if ".apk" in file:
                        magisk_list.append(file)

            if magisk_list.__len__() == 0:
                print(self.langget('cannot find any apk, please download and put them into prebuilt dir'), file=self)
                self.change_frame_patcher()

            for index, current in enumerate(magisk_list):
                self.magisk_list.append(
                    ctk.CTkRadioButton(self.download_list_frame, 
                                       text=current, 
                                       command=lambda x=current: download(x),
                                       value=current, 
                                       variable=self.magisk_select_int)
                )
    
        else:
            magisk_list = utils.getReleaseList(url=utils.DEFAULT_MAGISK_API_URL if not self.usedeltamagisk.get() else utils.DELTA_MAGISK_API_URL,
                                               isproxy=self.isproxy.get(),
                                               proxyaddr=self.proxy.get(),
                                               isjsdelivr=self.isjsdelivr.get(),
                                               log=self.log)
            for index, current in enumerate(magisk_list):
                self.magisk_list.append(
                    ctk.CTkRadioButton(self.download_list_frame, 
                                       text=current, 
                                       command=lambda x=current: download(x), 
                                       value=current, 
                                       variable=self.magisk_select_int)
                )

        # place all widgets
        for i in self.magisk_list:
            i.pack(side='top', fill='x', anchor='w', padx=10, pady=5)

    def change_theme(self, theme: str):
        ctk.set_appearance_mode(theme)

    def file_choose_dialog(self):
        fname = ctk.filedialog.askopenfilename(title=self.langget('select a boot image'), initialdir=getcwd())
        self.bootimg.set(fname)

    def _open_preinit_doc(self):
        """Open PREINITDEVICE.md in the OS default handler (browser or editor)."""
        # Locate the doc file (works both in dev mode and inside a PyInstaller bundle).
        candidates = [
            op.join(bundle_dir(), 'PREINITDEVICE.md'),
            op.join(getcwd(), 'PREINITDEVICE.md'),
        ]
        for p in candidates:
            if op.isfile(p):
                try:
                    if osname == 'nt':
                        os.startfile(p)  # type: ignore[attr-defined]
                    elif sys.platform == 'darwin':
                        subprocess.Popen(['open', p])
                    else:
                        subprocess.Popen(['xdg-open', p])
                    return
                except Exception as e:
                    print(f"Cannot open {p}: {e}", file=self)
                    return
        print("PREINITDEVICE.md not found", file=self)
    
    def set_progress(self, value: int):
        # Backwards-compatible wrapper; updates both bar and percent text.
        self._update_progress(value)

    def _update_progress(self, pct: int):
        if pct < 0: pct = 0
        elif pct > 100: pct = 100
        self.progress.set(pct)
        self.progress_text.set(f"{pct}%")

    def set_log_level(self, value):
        self.log.setLevel(int(value))

    def update_local_widgets(self, event):
        if not self.uselocal.get():
            self.download_delta_magisk.pack(side='top', fill='x', anchor='w', padx=5, pady=5)
        else:
            self.download_delta_magisk.pack_forget()

    def update_proxy_widgets(self, event):
        if self.isproxy.get():
            self.download_proxy_url.pack(side='top', fill='x', anchor='w', padx=5, pady=5)
        else:
            self.download_proxy_url.pack_forget()

    def update_mirror_widgets(self, event):
        if self.ismirror.get():
            self.download_mirror_label.pack(side='top', fill='x', anchor='w', padx=5, pady=5)
        else:
            self.download_mirror_label.pack_forget()

    def _change_frame_byname(self, name: str):
        self.patcher_frame_button.configure(
            fg_color=("gray75", "gray25") if name == "patcher" else "transparent"
        )
        self.download_frame_button.configure(
            fg_color=("gray75", "gray25") if name == "download" else "transparent"
        )
        self.other_frame_button.configure(
            fg_color=("gray75", "gray25") if name == "other" else "transparent"
        )

        if name == "patcher":
            self.patcher_frame.grid(row=0, column=1, sticky="nsew")
        else:
            self.patcher_frame.grid_forget()

        if name == "download":
            self.download_frame.grid(row=0, column=1, sticky="nsew")
        else:
            self.download_frame.grid_forget()

        if name == "other":
            self.other_frame.grid(row=0, column=1, sticky="nsew")
        else:
            self.other_frame.grid_forget()

    def change_frame_patcher(self):
        self._change_frame_byname("patcher")

    def change_frame_download(self):
        self._change_frame_byname("download")

    def change_frame_other(self):
        self._change_frame_byname("other")

    def ui_scaling_event(self, value):
        ctk.set_widget_scaling(float(value))
        ctk.set_window_scaling(float(value))

    def refresh_widgets(self):
        for child in self.winfo_children():
            child.destroy()
        
        Language.select = self.lang.get()
        self.lang_dict = getattr(Language, self.lang.get())
        self.magisk_select.set(self.langget('magisk is not select'))
        for i in self.magisk_list:
            i.destroy()
        self.magisk_list = []
        self.__setup_widgets()

def centerWindow(parent: ctk.CTk):
    width, height = parent.winfo_screenwidth(), parent.winfo_screenheight()
    parent.geometry("+%d+%d" % ((width / 2) - (WIDTH / 2), (height / 2) - (HEIGHT / 2)))

if __name__ == "__main__":
    root = MagiskPatcherUI()
    root.title(TITLE)
    root.geometry("%dx%d" % (WIDTH, HEIGHT))

    # Fix high dpi
    if osname == 'nt':
        # Tell system using self dpi adapt
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # Get screen resize scale factor
        scalefactor = ctypes.windll.shcore.GetScaleFactorForDevice(0)
        root.tk.call('tk', 'scaling', scalefactor/75)

    root.update()
    centerWindow(root)
    #ctk.set_window_scaling(1.25)

    root.mainloop()
