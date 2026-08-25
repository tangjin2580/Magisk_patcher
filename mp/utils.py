from os import name as osname
from sys import stderr
import subprocess
import time
import requests
import zipfile
from multiprocessing.dummy import DummyProcess
from concurrent.futures import ThreadPoolExecutor
import platform
from os import chmod, makedirs

from .lang import Language, langget

DEFAULT_MAGISK_API_URL = "https://api.github.com/repos/topjohnwu/Magisk/releases"
DELTA_MAGISK_API_URL = "https://api.github.com/repos/HuskyDG/magisk-files/releases"

def retTypeAndMachine():
    # Detect machine and ostype
    ostype = platform.system().lower()
    if ostype.find("cygwin") >= 0:  # Support cygwin x11
        ostype = "windows"
    rel = ''
    if ostype == "darwin":
        ostype = "macos"
        v, _, _ = platform.mac_ver()
        rel = v.split('.', 1)[0]
    machine = platform.machine().lower()
    if machine == 'aarch64_be' \
        or machine == 'armv8b' \
        or machine == 'armv8l':
        machine = 'aarch64'
    if machine == 'i386' or machine == 'i686':
        machine = 'x86'
    if machine == "amd64":
        machine = 'x86_64'
    if machine in ("riscv64", "riscv64gc"):
        machine = 'riscv64'
    return ostype, rel, machine

def runcmd(cmd):
    if osname == 'posix':
        creationflags = 0
    elif osname == 'nt':  # if on windows ,create a process with hidden console
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        creationflags = 0
    ret = subprocess.run(cmd,
                                   shell=False,
                                   #stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   creationflags=creationflags
                                )
    return (ret.returncode, ret.stdout.decode('utf-8'))

def checkUrl(url: str, timeout: int = 5) -> bool:
    """HEAD check whether a url is reachable (follow redirects)."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.ok
    except requests.RequestException:
        return False

def getReleaseList(url: str = DEFAULT_MAGISK_API_URL, isproxy: bool=False, proxyaddr: str="127.0.0.1:7890", isjsdelivr:bool=True, log=stderr):
    buf = url.split('/')
    user = buf[-3]
    repo = buf[-2]

    proxies = {
        'http': f"{proxyaddr}",
        'https': f"{proxyaddr}",
    }
    # Retry with exponential backoff to survive transient network blips
    r = None
    for attempt in range(3):
        try:
            r = requests.get(url,
                             proxies=proxies if isproxy else None,
                             timeout=10)
            if r.ok:
                break
        except requests.RequestException:
            r = None
        time.sleep(2 ** attempt)
    if r is None or not r.ok:
        print(langget('get version faild, please check net or add proxy'), file=log)
        return {}
    data = r.json()
    dlink = {}

    if url == DEFAULT_MAGISK_API_URL:
        # Official Magisk: always use the GitHub release asset direct link
        for i in data:
            tag_name = i['tag_name']
            for j in i['assets']:
                if j['name'].startswith("Magisk") and j['name'].endswith(r".apk"):
                    if "Manager" in j['name']: continue # skip magisk manager apk
                    dlink.update({j['name'] : j['browser_download_url']})
    else: # maybe delta magisk
        def pick(name, js_url, fallback_url):
            # jsdelivr only mirrors magisk-files up to a certain version;
            # fall back to the GitHub release asset url when unavailable
            if isjsdelivr and checkUrl(js_url):
                return (name, js_url)
            return (name, fallback_url)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = []
            for i in data:
                tag_name = i['tag_name']
                for j in i['assets']:
                    if j['name'].endswith(r".apk") and j['name'].startswith('app'):
                        js_url = magiskTag2jsdelivr(user, repo, tag_name, j['name'])
                        name = tag_name + j['name'].lstrip('app')
                        futures.append(ex.submit(pick, name, js_url, j['browser_download_url']))
            for f in futures:
                k, v = f.result()
                dlink[k] = v
    return dlink

def magiskTag2jsdelivr(user, repo, tag, fname):
    '''
    input link and tag get jsdilivr link
    '''
    if user == "topjohnwu": # official magisk
        return f"https://cdn.jsdelivr.net/gh/{user}/magisk-files@{tag.lstrip('v')}/app-release.apk"
    return f"https://cdn.jsdelivr.net/gh/{user}/{repo}@{tag}/{fname}"

def getMagiskApkVersion(fname: str) -> str | None:
    '''
    Give magisk apk file and return version code
    '''
    valid_flag = False
    magisk_ver_code = "00000"
    with zipfile.ZipFile(fname, 'r') as z:
        for i in z.filelist:
            if "util_functions.sh" in i.filename:
                valid_flag = True
                for line in z.read(i).splitlines():
                    if b"MAGISK_VER_CODE" in line:
                        magisk_ver_code = line.split(b"=")[1]
        if not valid_flag: return None
    return magisk_ver_code

def convertVercode2Ver(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    return value[0:2] + "." + value[2:3]

def downloadFile(url: str, to: str, isproxy: bool = False, proxy:str="127.0.0.1:7890", progress=None, log=stderr):
    """
    Can accept a ttk.ProgressBar as progress (or any object with a .set(value) method)
    The value is set as an integer percentage (0-100).
    """
    proxies = {
        'http': proxy,
        'https': proxy,
    }
    p = lambda now, total: int((now/total)*100) if total > 0 else 0
    chunk_size = 262144  # 256 KiB - reduces write() syscalls ~25x vs 10 KiB
    try:
        session = requests.Session()
        r = session.get(url, stream=True, allow_redirects=True,
                        proxies=proxies if isproxy else None,
                        timeout=15)
    except requests.RequestException:
        print(langget('internet connect faild or cannot connect target url'), file=log)
        return False
    if not r.ok:
        print(langget('download faild'), file=log)
        return False
    print(f"- {langget('start download')}[{url}] -> [{to}]", file=log)
    total_size = int(r.headers.get('content-length', 0))
    now = 0
    try:
        with open(to, 'wb') as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    now += len(chunk)
                    if progress is not None:
                        progress.set(p(now, total_size))
    finally:
        r.close()
        session.close()
    print(langget('download complete'), file=log)
    if progress is not None:
        progress.set(0)
    return True

def thdownloadFile(*args):
    dp = DummyProcess(target=downloadFile, args=[*args, ])
    dp.start()

def parseMagiskApk(apk: str, arch:["arm64", "arm", "x86", "x86_64"]="arm64", log=stderr):
    """
    This function will extract useful file from magisk.apk
    """
    def archconv(a):
        ret = a
        match a:
            case "arm64":
                ret = "arm64-v8a"
            case "arm":
                ret = "armeabi-v7a"
        return ret

    def saveto(bytes, path):
        with open(path, 'wb') as f:
            f.write(bytes)

    print(langget('start decompress needed'), file=log)
    arch = archconv(arch)
    _, _, machine = retTypeAndMachine()
    host_abi = "x86_64"
    if machine == "aarch64":
        host_abi = "arm64-v8a"
    elif machine == "arm":
        host_abi = "armeabi-v7a"
    with zipfile.ZipFile(apk) as z:
        for l in z.filelist:
            # 26.0+: stub is bundled under assets/
            if l.filename == "assets/stub.apk":
                saveto(z.read(l), "stub.apk")
            # Save a platform magiskboot into bin if linux
            if osname != 'nt':
                if f"lib/{host_abi}/libmagiskboot.so" == l.filename:
                    makedirs("bin", exist_ok=True)
                    saveto(z.read(l), "bin/magiskboot")
                    chmod("bin/magiskboot", 0o755)

            if f"lib/{arch}/libmagiskinit.so" == l.filename:
                saveto(z.read(l), "magiskinit")
            # Single unified magisk binary (v26.0+)
            if f"lib/{arch}/libmagisk.so" == l.filename:
                saveto(z.read(l), "magisk")
            if f"lib/{arch}/libinit-ld.so" == l.filename:
                saveto(z.read(l), "init-ld")

if __name__ == '__main__':
    print(getReleaseList(url=DELTA_MAGISK_API_URL))
