# =================================================================
# wifi_manager.py : Wi-Fi 연결/AP 모드/설정 저장
# =================================================================
import json
import network
import utime

from console_log import log_error

CONFIG_FILE = "wifi_config.json"
AP_SSID = "Pico-Dust-Setup"     # 피코 단독 핫스팟(AP) 이름
AP_PASS = ""                    # 비밀번호 (빈칸 = 공개 오픈 AP)
AP_IP = "192.168.4.1"
AP_RETRY_INTERVAL_MS = 3 * 60 * 1000  # AP(오프라인) 모드에서 저장된 Wi-Fi를 재시도하는 주기


def disable_wifi_power_save(wlan_obj):
    """Wi-Fi 칩셋 절전 모드 강제 해제 (0xa11142)"""
    try:
        wlan_obj.config(pm=0xa11142)
    except Exception as e:
        log_error("WiFi PowerSave", e)


def set_custom_dns(wlan_obj, dns_ip="8.8.8.8"):
    """DNS 서버(구글 DNS 8.8.8.8) 강제 지정으로 학교/공공 Wi-Fi 도메인 해석 보정"""
    try:
        ip, subnet, gateway, _ = wlan_obj.ifconfig()
        wlan_obj.ifconfig((ip, subnet, gateway, dns_ip))
        print(f"🌐 [DNS 설정 완료] IP: {ip}, DNS: {dns_ip}")
    except Exception as e:
        log_error("DNS 설정", e)


def load_wifi_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def save_wifi_config(ssid, password):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"ssid": ssid, "password": password}, f)
        return True
    except Exception as e:
        log_error("WiFi 설정 저장", e)
        return False


def scan_nearby_wifis():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    try:
        raw_list = sta.scan()
        ssids = []
        for item in raw_list:
            ssid = item[0].decode('utf-8', 'ignore').strip()
            if ssid and ssid not in ssids and ssid != AP_SSID:
                ssids.append(ssid)
        return ssids
    except Exception as e:
        log_error("WiFi 스캔", e)
        return []


def connect_sta_wifi(ssid, password="", timeout_sec=8, lcd_ref=None, attempts=3):
    """
    저장된 SSID/비밀번호로 접속을 시도합니다. 신호가 순간적으로 불안정해서
    첫 시도가 실패하는 경우가 흔해서, 포기하고 AP 모드로 넘어가기 전에
    같은 정보로 여러 번(기본 3회) 재시도합니다.
    """
    if not ssid:
        return False, None

    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    sta = network.WLAN(network.STA_IF)

    for attempt in range(1, attempts + 1):
        sta.active(False)
        utime.sleep_ms(200)
        sta.active(True)
        disable_wifi_power_save(sta)

        if password:
            sta.connect(ssid, password)
        else:
            sta.connect(ssid)

        print(f"⏳ Wi-Fi [{ssid}] 접속 시도 중... ({attempt}/{attempts})")
        if lcd_ref:
            lcd_ref.display_2lines(f"WiFi try {attempt}/{attempts}", ssid[:16])

        t = timeout_sec
        while t > 0:
            if sta.isconnected():
                disable_wifi_power_save(sta)
                set_custom_dns(sta, "8.8.8.8")
                ip = sta.ifconfig()[0]
                print(f"✅ Wi-Fi 연결 성공! IP: {ip}")
                if lcd_ref:
                    lcd_ref.display_2lines("WiFi Connected!", ip[:16])
                    utime.sleep(1.5)
                return True, ip
            utime.sleep(1)
            t -= 1

        print(f"❌ Wi-Fi 연결 실패 (시도 {attempt}/{attempts})")

    return False, None


def start_ap_mode(lcd_ref=None):
    sta = network.WLAN(network.STA_IF)
    sta.active(False)

    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    utime.sleep_ms(200)
    ap.active(True)
    disable_wifi_power_save(ap)

    if AP_PASS and len(AP_PASS) >= 8:
        ap.config(essid=AP_SSID, password=AP_PASS)
    else:
        ap.config(essid=AP_SSID, security=0)

    print(f"📡 [오프라인 AP 모드] 핫스팟: {AP_SSID} (http://{AP_IP})")

    if lcd_ref:
        lcd_ref.display_2lines("AP: " + AP_SSID, "IP: " + AP_IP)
        utime.sleep(1.5)

    return AP_IP
