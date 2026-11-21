#!/usr/bin/env python3
"""
MKey BLE dinleyicisi - Windows ve Linux (Pardus) uyumlu.

Giriste gerekli kutuphaneleri kontrol eder, eksik olanlari kurmayi dener:
  - Ortak   : bleak
  - Windows : pyautogui
  - Linux   : evdev (uinput ile sanal klavye; X11 ve Wayland'da calisir)
              + ses/medya icin playerctl ve pactl/wpctl/amixer
"""
import asyncio
import getpass
import importlib
import importlib.util
import os
import queue
import shutil
import site
import string
import subprocess
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TARGET_NAME = "M5Key_Beacon"
last_counter = -1
IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform.startswith("win")
IN_VENV = sys.prefix != getattr(sys, "base_prefix", sys.prefix)

# Kutuphaneler giris kontrolunden sonra yuklenir (load_modules)
BleakScanner = None
UInput = None
ec = None
pyautogui = None

# (import adi, pip adi, apt adi)
REQUIRED = [("bleak", "bleak", "python3-bleak")]
if IS_LINUX:
    REQUIRED.append(("evdev", "evdev", "python3-evdev"))
else:
    REQUIRED.append(("pyautogui", "pyautogui", None))


# ===============================================================
# Kurulum yardimcilari
# ===============================================================
_sudo_ok = None


def _sudo_prefix():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    return ["sudo"] if shutil.which("sudo") else None


def sudo_allowed():
    """Sistem duzeyindeki adimlar icin tek seferlik onay."""
    global _sudo_ok
    if _sudo_ok is not None:
        return _sudo_ok
    if _sudo_prefix() is None:
        print("[UYARI] sudo bulunamadi, sistem kurulumlari atlaniyor.")
        _sudo_ok = False
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print("[UYARI] Etkilesimli terminal yok, sudo gerektiren kurulumlar atlaniyor.")
        _sudo_ok = False
        return False
    ans = input("Eksikler icin sudo kullanilacak (apt kurulumu / uinput izni). Devam? [E/h]: ").strip().lower()
    _sudo_ok = ans in ("", "e", "evet", "y", "yes")
    return _sudo_ok


def sudo_run(cmd, input_text=None, quiet=False):
    pre = _sudo_prefix()
    if pre is None:
        return False
    try:
        r = subprocess.run(
            pre + cmd,
            input=input_text.encode() if input_text else None,
            stdout=subprocess.DEVNULL if quiet else None,
        )
        return r.returncode == 0
    except Exception:
        return False


def apt_install(pkgs):
    if not shutil.which("apt-get") or not sudo_allowed():
        return False
    if sudo_run(["apt-get", "install", "-y"] + pkgs):
        return True
    print("apt-get update calistiriliyor...")
    sudo_run(["apt-get", "update"])
    return sudo_run(["apt-get", "install", "-y"] + pkgs)


def have_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def refresh_paths():
    importlib.invalidate_caches()
    try:
        us = site.getusersitepackages()
        if os.path.isdir(us) and us not in sys.path:
            site.addsitedir(us)
    except Exception:
        pass


def pip_available():
    return subprocess.run([sys.executable, "-m", "pip", "--version"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def ensure_pip():
    if pip_available():
        return True
    print("[EKSIK] pip kuruluyor...")
    if IS_LINUX and not IN_VENV:
        apt_install(["python3-pip"])
    else:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"])
    return pip_available()


def pip_install(pkg):
    base = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if IS_LINUX and not IN_VENV:
        base.append("--user")
    attempts = [base + [pkg]]
    if IS_LINUX and not IN_VENV:
        # Debian/Pardus 'externally-managed-environment' (PEP 668) korumasi
        attempts.append(base + ["--break-system-packages", pkg])
    for cmd in attempts:
        if subprocess.run(cmd).returncode == 0:
            return True
    return False


def install_module(imp, pip_name, apt_name):
    methods = ["pip", "apt"]
    if IS_LINUX and imp == "evdev":
        methods = ["apt", "pip"]  # evdev pip ile derleme ister, apt paketi daha sorunsuz
    for m in methods:
        if m == "pip":
            if ensure_pip():
                pip_install(pip_name)
        elif apt_name and IS_LINUX and not IN_VENV:
            apt_install([apt_name])
        refresh_paths()
        if have_module(imp):
            return True
    return False


def ensure_python_modules():
    missing = []
    for imp, pip_name, apt_name in REQUIRED:
        if have_module(imp):
            print(f"[OK] {imp}")
            continue
        print(f"[EKSIK] {imp} kuruluyor...")
        if install_module(imp, pip_name, apt_name):
            print(f"[OK] {imp} kuruldu")
        else:
            print(f"[HATA] {imp} kurulamadi")
            missing.append(imp)
    return missing


def ensure_linux_tools():
    need = []
    if not shutil.which("playerctl"):
        need.append("playerctl")
    if not any(shutil.which(t) for t in ("wpctl", "pactl", "amixer")):
        need.append("pulseaudio-utils")
    if need:
        print(f"[EKSIK] ses/medya araclari kuruluyor: {', '.join(need)}")
        if apt_install(need):
            print("[OK] ses/medya araclari kuruldu")
        else:
            print(f"[UYARI] Elle kur: sudo apt install {' '.join(need)}")
    else:
        print("[OK] ses/medya araclari (playerctl, pactl/wpctl/amixer)")


UDEV_RULE = 'KERNEL=="uinput", GROUP="input", MODE="0660"\n'


def ensure_uinput_access():
    """/dev/uinput yazma izni: kalici (udev + input grubu) ve bu oturum icin (ACL)."""
    if os.path.exists("/dev/uinput") and os.access("/dev/uinput", os.W_OK):
        print("[OK] /dev/uinput erisimi")
        return True
    print("[EKSIK] /dev/uinput izni ayarlaniyor...")
    if not sudo_allowed():
        print("        Elle: sudo modprobe uinput ; sudo usermod -aG input $USER ; oturumu kapat-ac")
        return False

    if not os.path.exists("/dev/uinput"):
        sudo_run(["modprobe", "uinput"])
    sudo_run(["tee", "/etc/modules-load.d/uinput.conf"], "uinput\n", quiet=True)
    sudo_run(["tee", "/etc/udev/rules.d/99-uinput.rules"], UDEV_RULE, quiet=True)
    sudo_run(["udevadm", "control", "--reload-rules"])
    sudo_run(["udevadm", "trigger"])
    user = getpass.getuser()
    sudo_run(["usermod", "-aG", "input", user])

    if not os.access("/dev/uinput", os.W_OK):
        # Grup uyeligi ancak yeniden girisle gecerli olur; bu oturum icin ACL ver
        if not shutil.which("setfacl"):
            apt_install(["acl"])
        if shutil.which("setfacl"):
            sudo_run(["setfacl", "-m", f"u:{user}:rw", "/dev/uinput"])

    if os.access("/dev/uinput", os.W_OK):
        print("[OK] /dev/uinput erisimi hazir")
        return True
    print("[UYARI] Izin icin oturumu kapatip tekrar acman gerekiyor, sonra betigi yeniden calistir.")
    return False


def load_modules():
    global BleakScanner, UInput, ec, pyautogui
    from bleak import BleakScanner as _BleakScanner
    BleakScanner = _BleakScanner
    if IS_LINUX:
        try:
            from evdev import UInput as _UInput, ecodes as _ec
            UInput, ec = _UInput, _ec
        except Exception as e:
            print(f"[UYARI] evdev yuklenemedi: {e}")
    else:
        try:
            import pyautogui as _pg
            _pg.FAILSAFE = False
            _pg.PAUSE = 0.02
            pyautogui = _pg
        except Exception as e:
            print(f"[UYARI] pyautogui yuklenemedi: {e}")


# ===============================================================
# Komut kodu -> tus adi
# ===============================================================
def get_key_str(cmd_id):
    # Medya (100+)
    if cmd_id == 100: return 'printscreen'
    if cmd_id == 101: return 'volumeup'
    if cmd_id == 102: return 'volumedown'
    if cmd_id == 103: return 'volumemute'
    if cmd_id == 104: return 'VOLUME_MAX'  # Ozel mantik
    if cmd_id == 105: return 'playpause'
    if cmd_id == 106: return 'nexttrack'
    if cmd_id == 107: return 'prevtrack'

    # Harfler ve Rakamlar (200 - 235)
    if 200 <= cmd_id <= 225:
        return string.ascii_lowercase[cmd_id - 200]
    if 226 <= cmd_id <= 235:
        return str(cmd_id - 226)

    # Ozel Tuslar (300+)
    special_map = {300: 'enter', 301: 'tab', 302: 'esc', 303: 'space',
                   304: 'backspace', 305: 'capslock', 306: 'printscreen',
                   307: 'pause', 308: 'apps'}
    if cmd_id in special_map: return special_map[cmd_id]

    # Navigasyon (400+)
    nav_map = {400: 'up', 401: 'down', 402: 'left', 403: 'right',
               404: 'home', 405: 'end', 406: 'pageup', 407: 'pagedown', 408: 'delete'}
    if cmd_id in nav_map: return nav_map[cmd_id]

    # Modifiers (500+)
    mod_map = {500: 'ctrl', 501: 'shift', 502: 'alt', 503: 'winleft'}
    if cmd_id in mod_map: return mod_map[cmd_id]

    # Fonksiyon Tuslari (600 - 623)
    if 600 <= cmd_id <= 623:
        return f"f{cmd_id - 599}"

    return None


# ===============================================================
# Linux: sistem komutlari (ses/medya), xdotool, uinput
# ===============================================================
def _run(cmd):
    if not shutil.which(cmd[0]):
        return False
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _first_ok(chain):
    for cmd in chain:
        if _run(cmd):
            return True
    return False


LINUX_MEDIA = {
    'volumeup': [
        ["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "5%+"],
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"],
        ["amixer", "-q", "sset", "Master", "5%+"],
    ],
    'volumedown': [
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"],
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"],
        ["amixer", "-q", "sset", "Master", "5%-"],
    ],
    'volumemute': [
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
        ["amixer", "-q", "sset", "Master", "toggle"],
    ],
    'playpause': [["playerctl", "play-pause"]],
    'nexttrack': [["playerctl", "next"]],
    'prevtrack': [["playerctl", "previous"]],
}


def linux_volume_max():
    ok = _first_ok([
        ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.0"],
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"],
        ["amixer", "-q", "sset", "Master", "100%"],
    ])
    _first_ok([
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
        ["amixer", "-q", "sset", "Master", "unmute"],
    ])
    return ok


XKEY = {
    'enter': 'Return', 'tab': 'Tab', 'esc': 'Escape', 'space': 'space',
    'backspace': 'BackSpace', 'capslock': 'Caps_Lock', 'printscreen': 'Print',
    'pause': 'Pause', 'apps': 'Menu',
    'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
    'home': 'Home', 'end': 'End', 'pageup': 'Prior', 'pagedown': 'Next', 'delete': 'Delete',
    'ctrl': 'Control_L', 'shift': 'Shift_L', 'alt': 'Alt_L', 'winleft': 'Super_L',
    'volumeup': 'XF86AudioRaiseVolume', 'volumedown': 'XF86AudioLowerVolume',
    'volumemute': 'XF86AudioMute', 'playpause': 'XF86AudioPlay',
    'nexttrack': 'XF86AudioNext', 'prevtrack': 'XF86AudioPrev',
}


def to_xkey(k):
    if k in XKEY:
        return XKEY[k]
    if len(k) > 1 and k[0] == 'f' and k[1:].isdigit():
        return k.upper()
    return k


EVDEV_NAMES = {
    'enter': 'KEY_ENTER', 'tab': 'KEY_TAB', 'esc': 'KEY_ESC', 'space': 'KEY_SPACE',
    'backspace': 'KEY_BACKSPACE', 'capslock': 'KEY_CAPSLOCK', 'printscreen': 'KEY_SYSRQ',
    'pause': 'KEY_PAUSE', 'apps': 'KEY_COMPOSE',
    'up': 'KEY_UP', 'down': 'KEY_DOWN', 'left': 'KEY_LEFT', 'right': 'KEY_RIGHT',
    'home': 'KEY_HOME', 'end': 'KEY_END', 'pageup': 'KEY_PAGEUP', 'pagedown': 'KEY_PAGEDOWN',
    'delete': 'KEY_DELETE',
    'ctrl': 'KEY_LEFTCTRL', 'shift': 'KEY_LEFTSHIFT', 'alt': 'KEY_LEFTALT', 'winleft': 'KEY_LEFTMETA',
    'volumeup': 'KEY_VOLUMEUP', 'volumedown': 'KEY_VOLUMEDOWN', 'volumemute': 'KEY_MUTE',
    'playpause': 'KEY_PLAYPAUSE', 'nexttrack': 'KEY_NEXTSONG', 'prevtrack': 'KEY_PREVIOUSSONG',
}
_uinput = None


def _evdev_code(k):
    if ec is None:
        return None
    if k in EVDEV_NAMES:
        name = EVDEV_NAMES[k]
    elif len(k) == 1 and k.isalnum():
        name = 'KEY_' + k.upper()
    elif len(k) > 1 and k[0] == 'f' and k[1:].isdigit():
        name = 'KEY_F' + k[1:]
    else:
        return None
    return getattr(ec, name, None)


def init_uinput():
    global _uinput
    if UInput is None:
        return False
    names = list(EVDEV_NAMES) + list(string.ascii_lowercase) + list(string.digits) \
        + [f"f{i}" for i in range(1, 25)]
    codes = sorted({c for c in (_evdev_code(n) for n in names) if c})
    try:
        _uinput = UInput({ec.EV_KEY: codes}, name="MKey Virtual Keyboard")
        time.sleep(0.5)  # masaustu yeni aygiti tanisin
        return True
    except Exception as ex:
        print(f"[UYARI] uinput acilamadi: {ex}")
        return False


def uinput_press(keys):
    if _uinput is None:
        return False
    codes = [_evdev_code(k) for k in keys]
    if any(c is None for c in codes):
        return False
    try:
        for c in codes:
            _uinput.write(ec.EV_KEY, c, 1)
        _uinput.syn()
        time.sleep(0.03)
        for c in reversed(codes):
            _uinput.write(ec.EV_KEY, c, 0)
        _uinput.syn()
        return True
    except Exception as ex:
        print(f"[HATA] uinput: {ex}")
        return False


# ===============================================================
# Tus gonderme (platforma gore)
# ===============================================================
def press_keys(keys):
    if len(keys) == 1:
        k = keys[0]
        if k == 'VOLUME_MAX':
            if IS_LINUX:
                if not linux_volume_max():
                    print("[HATA] Ses ayarlanamadi (wpctl/pactl/amixer bulunamadi)")
            elif pyautogui:
                for _ in range(50):
                    pyautogui.press('volumeup')
            return
        if IS_LINUX and k in LINUX_MEDIA and _first_ok(LINUX_MEDIA[k]):
            return

    if IS_LINUX:
        if uinput_press(keys):
            return
        if shutil.which("xdotool"):  # sadece X11
            combo = "+".join(to_xkey(k) for k in keys)
            if _run(["xdotool", "key", "--clearmodifiers", combo]):
                return
        print(f"[HATA] Tus gonderilemedi: {keys} (uinput izni yok, xdotool yok)")
        return

    # Windows / diger
    if pyautogui is None:
        print(f"[HATA] Tus gonderilemedi: {keys} (pyautogui yok)")
        return
    keys = [k for k in keys if k != 'VOLUME_MAX']
    if len(keys) == 1:
        pyautogui.press(keys[0])
    elif keys:
        pyautogui.hotkey(*keys)  # Kombinasyon (Ornek: Alt + Tab)


# Komutlar sirayla islensin (BLE dongusunu bloklamasin)
cmd_queue = queue.Queue()


def worker():
    while True:
        keys = cmd_queue.get()
        try:
            press_keys(keys)
        except Exception as e:
            print(f"[HATA] {keys}: {e}")


# ===============================================================
# BLE
# ===============================================================
def on_ble_packet(device, advertisement_data):
    global last_counter

    if advertisement_data.local_name == TARGET_NAME or device.name == TARGET_NAME:
        raw = advertisement_data.manufacturer_data.get(0xFFFF)  # M5Stack basligi
        if not raw or len(raw) < 2:
            return

        counter = raw[0]
        key_count = raw[1]
        if len(raw) < 2 + key_count * 2:
            return

        # Ayni paketin tekrarini yoksay
        if counter == last_counter:
            return
        last_counter = counter

        keys_to_press = []
        for i in range(key_count):
            idx = 2 + (i * 2)
            cmd = raw[idx] | (raw[idx + 1] << 8)
            key_str = get_key_str(cmd)
            if key_str:
                keys_to_press.append(key_str)

        print(f"[YAKALANDI Sinyal {counter}] -> {keys_to_press}")

        if keys_to_press:
            cmd_queue.put(keys_to_press)


async def scan_loop():
    print("\n=== MKey Gizli Mod Dinleyicisi Baslatildi ===")
    print("Arka planda gizlice dinleniyor, baglanti kopma sorunu yok!\n")
    scanner = BleakScanner(detection_callback=on_ble_packet)
    try:
        await scanner.start()
    except Exception as e:
        print(f"[HATA] Bluetooth taramasi baslatilamadi: {e}")
        if IS_WINDOWS:
            print("        Windows Ayarlari > Bluetooth'u ac ve tekrar dene.")
        else:
            print("        sudo systemctl start bluetooth ; rfkill unblock bluetooth")
        return

    while True:
        await asyncio.sleep(1)


def main():
    print("=== MKey Dinleyicisi: giris kontrolleri ===")
    print(f"Sistem: {'Windows' if IS_WINDOWS else 'Linux' if IS_LINUX else sys.platform} | Python {sys.version.split()[0]}")

    missing = ensure_python_modules()
    if "bleak" in missing or (not IS_LINUX and missing):
        print("\n[HATA] Zorunlu kutuphaneler eksik, program durduruldu.")
        if IS_LINUX and IN_VENV:
            print("       Sanal ortamdasin: pip install bleak evdev")
        sys.exit(1)

    load_modules()

    if IS_LINUX:
        ensure_linux_tools()
        ensure_uinput_access()
        if init_uinput():
            print("[OK] uinput aktif: tuslar Wayland/X11 fark etmeden calisir.")
        else:
            session = os.environ.get("XDG_SESSION_TYPE", "bilinmiyor")
            print(f"[UYARI] uinput kullanilamiyor (oturum: {session}).")
            if session.lower() == "wayland":
                print("        Wayland'da xdotool calismaz; uinput izni sart (oturumu kapat-ac).")
            elif not shutil.which("xdotool"):
                print("        Yedek olarak: sudo apt install xdotool (sadece X11)")
            print("        Ses/medya komutlari yine de calisir.")

    threading.Thread(target=worker, daemon=True).start()

    try:
        asyncio.run(scan_loop())
    except KeyboardInterrupt:
        print("\nDinleyici kapatildi.")


if __name__ == "__main__":
    main()
