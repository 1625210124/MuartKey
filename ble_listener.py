import asyncio
from bleak import BleakScanner
import pyautogui
import string

TARGET_NAME = "M5Key_Beacon"
last_counter = -1

pyautogui.FAILSAFE = False

def get_key_str(cmd_id):
    # Medya (100+)
    if cmd_id == 100: return 'printscreen'
    if cmd_id == 101: return 'volumeup'
    if cmd_id == 102: return 'volumedown'
    if cmd_id == 103: return 'volumemute'
    if cmd_id == 104: return 'VOLUME_MAX' # Özel mantık
    if cmd_id == 105: return 'playpause'
    if cmd_id == 106: return 'nexttrack'
    if cmd_id == 107: return 'prevtrack'
    
    # Harfler ve Rakamlar (200 - 235)
    if 200 <= cmd_id <= 225:
        return string.ascii_lowercase[cmd_id - 200]
    if 226 <= cmd_id <= 235:
        return str(cmd_id - 226)

    # Özel Tuşlar (300+)
    special_map = {300: 'enter', 301: 'tab', 302: 'esc', 303: 'space', 
                   304: 'backspace', 305: 'capslock', 306: 'printscreen', 
                   307: 'pause', 308: 'apps'}
    if cmd_id in special_map: return special_map[cmd_id]

    # Navigasyon (400+)
    nav_map = {400: 'up', 401: 'down', 402: 'left', 403: 'right',
               404: 'home', 405: 'end', 406: 'pageup', 407: 'pagedown', 408: 'delete'}
    if cmd_id in nav_map: return nav_map[cmd_id]

    # Modifiers (500+)
    mod_map = {500: 'ctrl', 501: 'shift', 502: 'alt', 503: 'win'}
    if cmd_id in mod_map: return mod_map[cmd_id]

    # Fonksiyon Tuşları (600 - 623)
    if 600 <= cmd_id <= 623:
        return f"f{cmd_id - 599}"

    return None

def on_ble_packet(device, advertisement_data):
    global last_counter
    
    if advertisement_data.local_name == TARGET_NAME or device.name == TARGET_NAME:
        m_data = advertisement_data.manufacturer_data
        
        if 65535 in m_data: # 0xFFFF (M5Stack Header)
            raw = m_data[65535]
            if len(raw) >= 4:
                counter = raw[0]
                key_count = raw[1]
                
                # Aynı paketin tekrarını yoksay
                if counter != last_counter:
                    last_counter = counter
                    
                    # Paket içindeki tuşları çözümle
                    keys_to_press = []
                    for i in range(key_count):
                        idx = 2 + (i * 2)
                        cmd = raw[idx] | (raw[idx+1] << 8)
                        key_str = get_key_str(cmd)
                        if key_str:
                            keys_to_press.append(key_str)

                    print(f"[YAKALANDI Sinyal {counter}] -> {keys_to_press}")

                    # Tuşları Uygula
                    if not keys_to_press:
                        pass
                    elif len(keys_to_press) == 1:
                        if keys_to_press[0] == 'VOLUME_MAX':
                            for _ in range(50): pyautogui.press('volumeup')
                        else:
                            pyautogui.press(keys_to_press[0])
                    else:
                        # Birden fazla tuş varsa Kombinasyon olarak algıla (Örn: Alt + Tab)
                        pyautogui.hotkey(*keys_to_press)

async def main():
    print("=== MKey Gizli Mod Dinleyicisi Baslatildi ===")
    print("Arka planda gizlice dinleniyor, bağlantı kopma sorunu yok!\n")
    
    scanner = BleakScanner(detection_callback=on_ble_packet)
    await scanner.start()
    
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDinleyici kapatıldı.")