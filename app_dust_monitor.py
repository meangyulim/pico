# =================================================================
# 🌫️ app_dust_monitor.py : 샤프 계열 미세먼지 센서 모니터링
# =================================================================
# 이 파일의 코드를 실수로 지우거나 문법 오타를 내더라도,
# main.py(시스템 코어)의 보호 기능 덕분에 웹서버와 Wi-Fi는 항상 살아있어
# 스마트폰 웹 에디터(/edit)로 언제든지 다시 수정할 수 있습니다.
#
# 하드웨어: LED GPIO27, ADC GPIO26, PWM 버저 GPIO20.
# /apps 화면에서 이 앱을 선택하면 활성화됩니다 (app_manager.py 참고).
# =================================================================
import machine
import utime
import json
import urequests
import gc

# 본인의 Google Apps Script 배포 URL로 바꿔서 사용하세요.
GAS_URL = "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec"

DISPLAY_UPDATE_INTERVAL_MS = 2000
CLOUD_SYNC_INTERVAL_MS = 60000
alert_threshold = 80.0
is_muted = False
cloud_sync_status = "대기 중"

dust_led = machine.Pin(27, machine.Pin.OUT)
dust_led.value(1)
dust_adc = machine.ADC(26)
buzzer = machine.PWM(machine.Pin(20))
buzzer.duty_u16(0)


def play_alert_beep():
    try:
        buzzer.freq(500)
        buzzer.duty_u16(1000)
        utime.sleep_ms(30)
        buzzer.duty_u16(0)
    except Exception:
        pass


def read_dust_sensor(num_samples=10):
    total_v = 0.0
    for _ in range(num_samples):
        dust_led.value(0)
        utime.sleep_us(280)
        raw = dust_adc.read_u16()
        utime.sleep_us(40)
        dust_led.value(1)
        utime.sleep_us(9680)
        total_v += (raw / 65535.0) * 3.3
    avg_v = total_v / num_samples
    density = (0.17 * avg_v - 0.1) * 1000.0
    return avg_v, max(0.0, density)


def get_status_info(density):
    if density <= 30:
        return "GOOD", "쾌적", "#22c55e"
    elif density <= 80:
        return "NORMAL", "보통", "#eab308"
    elif density <= 150:
        return "POOR", "나쁨", "#f97316"
    else:
        return "BAD", "최악", "#ef4444"


def sync_with_google_sheets(dust_val, volt_val, status_str):
    global is_muted, alert_threshold, cloud_sync_status
    try:
        payload = json.dumps({"dust": round(dust_val, 1), "voltage": round(volt_val, 2), "status": status_str})
        res = urequests.post(GAS_URL, data=payload, headers={"Content-Type": "application/json"}, timeout=10)
        if res.status_code in (301, 302, 303, 307):
            loc = res.headers.get("Location") or res.headers.get("location")
            res.close()
            if loc:
                res = urequests.get(loc, timeout=10)
        if res and res.status_code == 200:
            d = res.json()
            if "mute" in d:
                is_muted = bool(d["mute"])
            if "threshold" in d:
                alert_threshold = float(d["threshold"])
            cloud_sync_status = "연결 정상"
            res.close()
            return True
    except Exception as e:
        cloud_sync_status = f"오류: {e}"
    finally:
        gc.collect()
    return False
