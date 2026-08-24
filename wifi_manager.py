# =================================================================
# wifi_manager.py : Wi-Fi 연결/AP 모드/설정 저장
# =================================================================
import json
import network
import utime

from console_log import log_error

try:
    import watchdog
except ImportError:  # OTA로 아직 전달되지 않은 새 모듈 — 없어도 동작해야 함
    class watchdog:
        @staticmethod
        def feed():
            pass

CONFIG_FILE = "wifi_config.json"
AP_SSID = "Pico-Dust-Setup"     # 피코 단독 핫스팟(AP) 이름
AP_PASS = ""                    # 비밀번호 (빈칸 = 공개 오픈 AP)
AP_IP = "192.168.4.1"
AP_RETRY_INTERVAL_MS = 60 * 1000  # AP(오프라인) 모드에서 저장된 Wi-Fi를 재시도하는 주기


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


def load_wifi_networks():
    """저장된 Wi-Fi 목록을 돌려줍니다 (여러 개 저장 가능 — 휴대폰처럼).

    예전 형식({"ssid":..,"password":..} 단일 네트워크)으로 저장된 파일도
    그대로 읽어 한 개짜리 목록으로 바꿔줍니다. 기존 사용자가 새 코드로
    업데이트했을 때 저장된 Wi-Fi 정보를 다시 입력할 필요가 없게 하기
    위함입니다.
    """
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict) and "networks" in data:
        return [n for n in data["networks"] if n.get("ssid")]
    if isinstance(data, dict) and data.get("ssid"):
        return [{"ssid": data["ssid"], "password": data.get("password", "")}]
    return []


def _save_wifi_networks(networks):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({"networks": networks}, f)
        return True
    except Exception as e:
        log_error("WiFi 설정 저장", e)
        return False


def save_wifi_network(ssid, password):
    """새 네트워크를 목록에 추가합니다. 이미 저장된 SSID면 비밀번호만 갱신합니다."""
    networks = [n for n in load_wifi_networks() if n["ssid"] != ssid]
    networks.append({"ssid": ssid, "password": password})
    return _save_wifi_networks(networks)


def remove_wifi_network(ssid):
    networks = [n for n in load_wifi_networks() if n["ssid"] != ssid]
    return _save_wifi_networks(networks)


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


def _pick_candidates(networks, scan_raw):
    """저장된 네트워크 중 지금 스캔에 실제로 잡힌 것만, 신호가 강한 순으로
    골라냅니다. 하드웨어를 건드리지 않는 순수 로직이라 데스크톱에서
    그대로 테스트할 수 있습니다 (tests/test_logic.py)."""
    saved = {}
    for n in networks:
        ssid = n.get("ssid")
        if ssid:
            saved[ssid] = n.get("password", "")

    best_rssi = {}
    for item in scan_raw:
        try:
            ssid = item[0].decode('utf-8', 'ignore').strip()
        except Exception:
            continue
        if ssid not in saved:
            continue
        rssi = item[3] if len(item) > 3 else 0
        if ssid not in best_rssi or rssi > best_rssi[ssid]:
            best_rssi[ssid] = rssi

    order = sorted(best_rssi.items(), key=lambda kv: kv[1], reverse=True)
    return [(ssid, saved[ssid]) for ssid, _ in order]


def _scan_raw(sta):
    watchdog.feed()
    try:
        raw = sta.scan()
    except Exception as e:
        log_error("WiFi 스캔", e)
        raw = []
    watchdog.feed()
    return raw


def connect_sta_wifi(networks, timeout_sec=8, lcd_ref=None, attempts_per_network=3,
                      scan_rounds=2):
    """저장된 네트워크 중 지금 주변에서 잡히는 것을 신호가 강한 순으로
    골라 접속을 시도합니다 (휴대폰처럼 여러 곳을 저장해두고 그때그때
    잡히는 곳에 자동으로 붙습니다). 신호가 순간적으로 불안정해서 첫
    시도/첫 스캔이 실패하는 경우가 흔해서, 두 단계로 재시도합니다:
    한 후보에 대해 같은 정보로 몇 번(기본 3회) 접속을 재시도하고,
    스캔 자체에 아무 후보도 안 잡히면 다시 스캔합니다(기본 2라운드) —
    그래도 하나도 못 잡으면 포기하고 호출한 쪽이 AP(핫스팟) 모드로
    넘어갑니다.
    """
    if not networks:
        return False, None

    ap = network.WLAN(network.AP_IF)
    ap.active(False)

    sta = network.WLAN(network.STA_IF)
    sta.active(False)
    utime.sleep_ms(200)
    sta.active(True)
    disable_wifi_power_save(sta)

    for scan_round in range(1, scan_rounds + 1):
        candidates = _pick_candidates(networks, _scan_raw(sta))
        if not candidates:
            print(f"📡 저장된 Wi-Fi 중 주변에서 잡히는 곳이 없습니다. "
                  f"(스캔 {scan_round}/{scan_rounds})")
            continue

        for ssid, password in candidates:
            for attempt in range(1, attempts_per_network + 1):
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

                print(f"⏳ Wi-Fi [{ssid}] 접속 시도 중... ({attempt}/{attempts_per_network})")
                if lcd_ref:
                    lcd_ref.display_2lines(f"WiFi try {attempt}/{attempts_per_network}", ssid[:16])

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

                print(f"❌ Wi-Fi [{ssid}] 연결 실패 (시도 {attempt}/{attempts_per_network})")

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
