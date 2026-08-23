# =================================================================
# wifi_manager.py : Wi-Fi 연결/AP 모드/설정 저장
# =================================================================
import json
import network
import utime

from console_log import log_error
import watchdog

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


def sync_ntp_time():
    """
    피코엔 배터리 RTC가 없어서 매 부팅마다 시계가 리셋됩니다. Wi-Fi 연결
    성공 직후 NTP로 한 번 맞춰두면, OTA 업데이트 적용 시각처럼 사람이
    읽을 수 있는 실제 날짜/시간을 기록할 수 있습니다. 실패해도(NTP 서버
    차단 등) 치명적이지 않으므로 조용히 무시합니다.
    """
    try:
        import ntptime
        # 기본값이 없거나 너무 길면 여기서 오래 멈출 수 있어 짧게 지정합니다
        # (워치독이 켜진 뒤라면 8초를 넘기는 순간 강제 재부팅됨).
        try:
            ntptime.timeout = 3
        except Exception:
            pass
        watchdog.feed()
        ntptime.settime()
        watchdog.feed()
        print("🕒 NTP 시간 동기화 완료")
    except Exception as e:
        log_error("NTP 시간 동기화", e)


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
        watchdog.feed()  # scan()은 수 초간 블로킹됨
        raw_list = sta.scan()
        watchdog.feed()
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
        watchdog.feed()
        sta.active(False)
        utime.sleep_ms(200)
        sta.active(True)
        disable_wifi_power_save(sta)
        watchdog.feed()

        if password:
            sta.connect(ssid, password)
        else:
            sta.connect(ssid)

        print(f"⏳ Wi-Fi [{ssid}] 접속 시도 중... ({attempt}/{attempts})")
        if lcd_ref:
            lcd_ref.display_2lines(f"WiFi try {attempt}/{attempts}", ssid[:16])

        t = timeout_sec
        while t > 0:
            watchdog.feed()  # 이 대기 루프만으로도 워치독 한도(8초)에 근접함
            if sta.isconnected():
                disable_wifi_power_save(sta)
                ip = sta.ifconfig()[0]
                print(f"✅ Wi-Fi 연결 성공! IP: {ip}")
                sync_ntp_time()
                if lcd_ref:
                    lcd_ref.display_2lines("WiFi Connected!", ip[:16])
                    utime.sleep(1.5)
                return True, ip
            utime.sleep(1)
            t -= 1

        print(f"❌ Wi-Fi 연결 실패 (시도 {attempt}/{attempts})")

    return False, None


def start_ap_mode(lcd_ref=None):
    watchdog.feed()
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
