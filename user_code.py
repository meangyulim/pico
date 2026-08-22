# =================================================================
# 🛠️ user_code.py : 사용자가 자유롭게 수정할 수 있는 커스텀 로직
# =================================================================
# 이 파일의 코드를 실수로 지우거나 문법 오타를 내더라도,
# main.py(시스템 코어)의 보호 기능 덕분에 웹서버와 Wi-Fi는 항상 살아있어
# 스마트폰 웹 에디터(/edit)로 언제든지 다시 수정할 수 있습니다.
# =================================================================

import machine
import utime
import json
import urequests
import gc

# -----------------------------------------------------------------
# [1] 기본 설정값 (사용자 임의 변경 가능)
# -----------------------------------------------------------------
# Google Apps Script (GAS) Web App 배포 URL
GAS_URL = "https://script.google.com/macros/s/AKfycbz0mxLfgUU8_3x96wTcjCaD11LrN_eW9oP5eeelkpMxDhKit6_jaKIDM19x8abxKXLt/exec"

DISPLAY_UPDATE_INTERVAL_MS = 2000  # 센서 측정 및 LCD 갱신 주기 (2초)
CLOUD_SYNC_INTERVAL_MS = 60000     # 구글 시트 동기화 주기 (1분 = 60,000ms)

alert_threshold = 80.0             # 경보 기준 미세먼지 농도 (ug/m3)
is_muted = False                   # 버저 음소거 여부
cloud_sync_status = "대기 중"       # 클라우드 동기화 상태

# -----------------------------------------------------------------
# [2] 하드웨어 핀 초기화 (미세먼지 센서 & PWM 버저)
# -----------------------------------------------------------------
dust_led = machine.Pin(27, machine.Pin.OUT)
dust_led.value(1)  # LED OFF (Active LOW)
dust_adc = machine.ADC(26)

buzzer = machine.PWM(machine.Pin(20))
buzzer.duty_u16(0)

# -----------------------------------------------------------------
# [3] 버저 경보음 함수
# -----------------------------------------------------------------
def play_alert_beep():
    """교무실/실내용 저소음 경고음"""
    try:
        buzzer.freq(500)
        buzzer.duty_u16(1000)
        utime.sleep_ms(30)
        buzzer.duty_u16(0)
    except Exception:
        pass

# -----------------------------------------------------------------
# [4] 미세먼지 센서 정격 타이밍 측정 및 수식 계산
# -----------------------------------------------------------------
def read_dust_sensor(num_samples=10):
    """샤프 센서 정격 10ms 주기(100Hz) 10회 연속 측정 후 평균 계산"""
    total_v = 0.0
    for _ in range(num_samples):
        dust_led.value(0)       # LED ON
        utime.sleep_us(280)     # 280us 대기
        raw = dust_adc.read_u16()
        utime.sleep_us(40)      # 40us 대기
        dust_led.value(1)       # LED OFF
        utime.sleep_us(9680)    # 9.68ms 대기 (총 10ms 주기)
        
        total_v += (raw / 65535.0) * 3.3
        
    avg_v = total_v / num_samples
    density = (0.17 * avg_v - 0.1) * 1000.0
    if density < 0.0:
        density = 0.0
        
    return avg_v, density

# -----------------------------------------------------------------
# [5] 미세먼지 농도별 상태 및 색상 판정
# -----------------------------------------------------------------
def get_status_info(density):
    if density <= 30:
        return "GOOD", "쾌적", "#22c55e"
    elif density <= 80:
        return "NORMAL", "보통", "#eab308"
    elif density <= 150:
        return "POOR", "나쁨", "#f97316"
    else:
        return "BAD", "최악", "#ef4444"

# -----------------------------------------------------------------
# [6] 구글 앱스 스크립트(GAS) 양방향 클라우드 통신
# -----------------------------------------------------------------
def sync_with_google_sheets(dust_val, volt_val, status_str):
    """
    구글 시트(GAS Web App)와 1분 주기로 양방향 데이터 통신
    - Push: 현재 미세먼지 수치, 전압, 상태 전송
    - Pull: 구글 시트의 mute(음소거), threshold(경보 임계치) 설정 수신
    """
    global is_muted, alert_threshold, cloud_sync_status
    
    payload = json.dumps({
        "dust": round(dust_val, 1),
        "voltage": round(volt_val, 2),
        "status": status_str
    })
    headers = {"Content-Type": "application/json"}
    
    print(f"☁️ [GAS 클라우드 전송 중... (OTA 테스트 반영됨)] Dust: {dust_val:.1f} ug/m3")
    res = None
    try:
        # 1. POST 전송
        res = urequests.post(GAS_URL, data=payload, headers=headers)
        
        # 2. GAS 302/303 리다이렉트 자동 추적
        if res.status_code in (301, 302, 303, 307):
            redirect_url = res.headers.get("Location") or res.headers.get("location")
            res.close()
            if redirect_url:
                res = urequests.get(redirect_url)
        
        # 3. 응답 파싱 및 원격 제어값 반영
        if res and res.status_code == 200:
            resp_data = res.json()
            print(f"📥 [GAS 수신 완료]: {resp_data}")
            
            if "mute" in resp_data:
                is_muted = bool(resp_data["mute"])
            
            if "threshold" in resp_data:
                try:
                    alert_threshold = float(resp_data["threshold"])
                except (ValueError, TypeError):
                    pass
            
            cloud_sync_status = f"연결 정상 (Mute:{is_muted}, Thresh:{alert_threshold:.0f})"
            return True
        else:
            cloud_sync_status = f"응답 코드: {res.status_code if res else 'None'}"
    except OSError as oe:
        if "-2" in str(oe):
            print("⚠️ DNS 도메인 해석 실패 (-2): 스마트폰 핫스팟을 이용해 보세요.")
            cloud_sync_status = "DNS 실패(-2): 핫스팟 권장"
        else:
            print("⚠️ 소켓 통신 오류:", oe)
            cloud_sync_status = f"통신 오류 ({oe})"
    except Exception as e:
        print("⚠️ GAS 통신 오류:", e)
        cloud_sync_status = f"오류 발생 ({e})"
    finally:
        if res:
            try:
                res.close()
            except Exception:
                pass
        gc.collect()
        
    return False
