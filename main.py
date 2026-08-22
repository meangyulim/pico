# =================================================================
# 🛡️ main.py : 시스템 코어 프레임워크 (Wi-Fi, 웹서버, LCD, 웹 에디터)
# =================================================================
# 이 파일은 피코의 핵심 인프라를 담당하는 불변 시스템 파일입니다.
# 사용자의 커스텀 로직(센서 측정, 구글시트 연동 등)은 'user_code.py'에서 실행되며,
# user_code.py에 오타나 오류가 발생해도 이 시스템 코어와 웹 에디터는 절대 다운되지 않습니다.
# =================================================================

import machine
import utime
import network
import socket
import json
import urequests
import gc
import os

# -----------------------------------------------------------------
# [1] 시스템 설정 및 기본 상수
# -----------------------------------------------------------------
CONFIG_FILE = "wifi_config.json"
USER_FILE = "user_code.py"
AP_SSID = "Pico-Dust-Setup"     # 피코 단독 핫스팟(AP) 이름
AP_PASS = ""                    # 비밀번호 (빈칸 = 공개 오픈 AP)
AP_IP = "192.168.4.1"

# -----------------------------------------------------------------
# [2] I2C 1602 LCD 드라이버 (PCF8574 I2C 어댑터용)
# -----------------------------------------------------------------
class I2cLcd:
    def __init__(self, i2c, i2c_addr):
        self.i2c = i2c
        self.addr = i2c_addr
        utime.sleep_ms(200)
        for _ in range(3):
            self._send_nibble(0x30)
            utime.sleep_ms(10)
        self._send_nibble(0x20)
        utime.sleep_ms(10)
        
        for cmd in [0x28, 0x0C, 0x06, 0x01]:
            self._write_cmd(cmd)
            utime.sleep_ms(5)

    def _send_nibble(self, nibble):
        b = (nibble & 0xF0) | 0x08  # 백라이트 ON
        try:
            self.i2c.writeto(self.addr, bytes([b | 0x04]))
            utime.sleep_us(50)
            self.i2c.writeto(self.addr, bytes([b]))
            utime.sleep_us(50)
        except Exception:
            pass

    def _write_cmd(self, cmd):
        buf = [
            (cmd & 0xF0) | 0x0C,
            (cmd & 0xF0) | 0x08,
            ((cmd << 4) & 0xF0) | 0x0C,
            ((cmd << 4) & 0xF0) | 0x08
        ]
        for b in buf:
            try:
                self.i2c.writeto(self.addr, bytes([b]))
            except Exception:
                pass
            utime.sleep_us(50)

    def _write_data(self, data):
        buf = [
            (data & 0xF0) | 0x0D,  # RS=1, 백라이트 ON
            (data & 0xF0) | 0x09,
            ((data << 4) & 0xF0) | 0x0D,
            ((data << 4) & 0xF0) | 0x09
        ]
        for b in buf:
            try:
                self.i2c.writeto(self.addr, bytes([b]))
            except Exception:
                pass
            utime.sleep_us(50)

    def clear(self):
        self._write_cmd(0x01)
        utime.sleep_ms(2)

    def move_to(self, col, row):
        addr = col + (0x40 if row == 1 else 0x00)
        self._write_cmd(0x80 | addr)

    def putstr(self, string):
        for char in string:
            self._write_data(ord(char))

    def display_2lines(self, line1, line2=""):
        self.move_to(0, 0)
        self.putstr("{:<16}".format(line1[:16]))
        self.move_to(0, 1)
        self.putstr("{:<16}".format(line2[:16]))


# -----------------------------------------------------------------
# [3] Wi-Fi 관리 및 유틸리티 함수
# -----------------------------------------------------------------
def disable_wifi_power_save(wlan_obj):
    """Wi-Fi 칩셋 절전 모드 강제 해제 (0xa11142)"""
    try:
        wlan_obj.config(pm=0xa11142)
    except Exception:
        pass

def set_custom_dns(wlan_obj, dns_ip="8.8.8.8"):
    """DNS 서버(구글 DNS 8.8.8.8) 강제 지정으로 학교/공공 Wi-Fi 도메인 해석 보정"""
    try:
        ip, subnet, gateway, _ = wlan_obj.ifconfig()
        wlan_obj.ifconfig((ip, subnet, gateway, dns_ip))
        print(f"🌐 [DNS 설정 완료] IP: {ip}, DNS: {dns_ip}")
    except Exception as e:
        print("DNS 설정 예외:", e)

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
        print("설정 저장 실패:", e)
        return False

def url_decode(s):
    s = s.replace('+', ' ')
    parts = s.split('%')
    res = parts[0]
    for part in parts[1:]:
        if len(part) >= 2:
            try:
                hex_val = int(part[:2], 16)
                res += chr(hex_val) + part[2:]
            except ValueError:
                res += '%' + part
        else:
            res += '%' + part
    return res

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
        print("Wi-Fi 스캔 오류:", e)
        return []

def connect_sta_wifi(ssid, password="", timeout_sec=8, lcd_ref=None):
    if not ssid:
        return False, None
    
    ap = network.WLAN(network.AP_IF)
    ap.active(False)
    
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    disable_wifi_power_save(sta)
    
    if password:
        sta.connect(ssid, password)
    else:
        sta.connect(ssid)
    
    print(f"⏳ Wi-Fi [{ssid}] 접속 시도 중...")
    if lcd_ref:
        lcd_ref.display_2lines("Connecting WiFi", ssid[:16])
        
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
        
    print("❌ Wi-Fi 연결 실패 (신호 없음 또는 비밀번호 불일치)")
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


# -----------------------------------------------------------------
# [4] 안전 모듈 로더 (user_code.py 격리 실행 및 오류 방어)
# -----------------------------------------------------------------
DEFAULT_USER_CODE_TEMPLATE = """# user_code.py 기본 템플릿
import machine, utime, json, urequests, gc

GAS_URL = "https://script.google.com/macros/s/AKfycbz0mxLfgUU8_3x96wTcjCaD11LrN_eW9oP5eeelkpMxDhKit6_jaKIDM19x8abxKXLt/exec"
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
    if density <= 30: return "GOOD", "쾌적", "#22c55e"
    elif density <= 80: return "NORMAL", "보통", "#eab308"
    elif density <= 150: return "POOR", "나쁨", "#f97316"
    else: return "BAD", "최악", "#ef4444"

def sync_with_google_sheets(dust_val, volt_val, status_str):
    global is_muted, alert_threshold, cloud_sync_status
    try:
        payload = json.dumps({"dust": round(dust_val, 1), "voltage": round(volt_val, 2), "status": status_str})
        res = urequests.post(GAS_URL, data=payload, headers={"Content-Type": "application/json"})
        if res.status_code in (301, 302, 303, 307):
            loc = res.headers.get("Location") or res.headers.get("location")
            res.close()
            if loc: res = urequests.get(loc)
        if res and res.status_code == 200:
            d = res.json()
            if "mute" in d: is_muted = bool(d["mute"])
            if "threshold" in d: alert_threshold = float(d["threshold"])
            cloud_sync_status = "연결 정상"
            res.close()
            return True
    except Exception as e:
        cloud_sync_status = f"오류: {e}"
    finally:
        gc.collect()
    return False
"""

def ensure_user_code_exists():
    try:
        os.stat(USER_FILE)
    except OSError:
        print(f"⚠️ {USER_FILE} 파일이 없어 기본 템플릿으로 생성합니다.")
        try:
            with open(USER_FILE, "w") as f:
                f.write(DEFAULT_USER_CODE_TEMPLATE)
        except Exception as e:
            print("템플릿 생성 실패:", e)

def load_user_module():
    """user_code.py를 안전하게 로드. 에러 발생 시 예외 격리."""
    ensure_user_code_exists()
    try:
        import user_code
        return user_code, None
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        print(f"❌ [{USER_FILE} 로드 오류] {err_msg}")
        return None, err_msg


# -----------------------------------------------------------------
# [5] HTML 생성 함수 (대시보드 UI & 웹 에디터 UI)
# -----------------------------------------------------------------
def generate_main_html(mode, current_ip, wifi_list, user_code_err, dust_val, volt_val, status_eng, status_kor, color_hex, cloud_msg, is_muted_val, thresh_val):
    is_offline = (mode == "OFFLINE_AP")
    mode_badge_text = "📡 오프라인 단독 AP 모드" if is_offline else "🌐 온라인 구글 시트 연동 모드"
    mode_badge_color = "#38bdf8" if is_offline else "#10b981"
    
    wifi_options = ""
    for w in wifi_list:
        wifi_options += f'<option value="{w}">{w}</option>'
    
    error_banner = ""
    if user_code_err:
        error_banner = f"""<div style="background:#ef444422; border:1px solid #ef4444; border-radius:16px; padding:14px; margin-bottom:16px; color:#fca5a5; font-size:13px; text-align:left; line-height:1.5;">
            <b>⚠️ user_code.py 실행 오류</b><br>
            <code style="color:#fff; word-break:break-all; display:block; margin:6px 0; background:#0f172a; padding:6px 8px; border-radius:6px;">{user_code_err}</code>
            👉 아래 <b>[📝 user_code.py 웹 에디터]</b>를 눌러 코드를 수정해 주세요.
        </div>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico 미세먼지 IoT 모니터</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; padding: 20px 16px; -webkit-text-size-adjust: 100%; }}
        .card {{ background: #1e293b; border-radius: 24px; padding: 28px 20px; max-width: 380px; margin: 0 auto 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }}
        .mode-badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; background: {mode_badge_color}; color: #000; font-size: 12px; font-weight: bold; margin-bottom: 12px; }}
        .status-badge {{ display: inline-block; padding: 8px 22px; border-radius: 50px; background: {color_hex}; color: #000; font-weight: 800; font-size: 15px; margin-bottom: 12px; transition: background 0.3s; }}
        .value {{ font-size: 52px; font-weight: 800; margin: 8px 0; color: #fff; }}
        .unit {{ font-size: 20px; color: #94a3b8; font-weight: normal; }}
        .sub-info {{ font-size: 13px; color: #94a3b8; margin-top: 16px; border-top: 1px solid #334155; padding-top: 14px; line-height: 1.8; text-align: left; }}
        .sub-info b {{ color: #f1f5f9; }}
        .live-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; margin-right: 6px; animation: pulse 1.5s infinite; }}
        @keyframes pulse {{ 0% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: 0.3; transform: scale(0.8); }} 100% {{ opacity: 1; transform: scale(1); }} }}
        
        details {{ background: #1e293b; border-radius: 16px; padding: 14px 18px; max-width: 380px; margin: 0 auto; border: 1px solid #334155; text-align: left; }}
        summary {{ font-size: 14px; font-weight: bold; color: #38bdf8; cursor: pointer; list-style: none; display: flex; justify-content: space-between; align-items: center; }}
        summary::-webkit-details-marker {{ display: none; }}
        summary::after {{ content: '⚙️'; font-size: 14px; }}
        .wifi-form {{ margin-top: 14px; border-top: 1px solid #334155; padding-top: 12px; }}
        label {{ display: block; font-size: 12px; font-weight: 600; margin-bottom: 4px; color: #cbd5e1; }}
        select, input[type="text"], input[type="password"] {{ width: 100%; padding: 10px 12px; margin-bottom: 12px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 16px; }}
        select:focus, input:focus {{ outline: none; border-color: #38bdf8; }}
        .btn {{ width: 100%; padding: 12px; background: #0284c7; color: #fff; border: none; border-radius: 10px; font-size: 14px; font-weight: bold; cursor: pointer; }}
    </style>
</head>
<body>
    <div class="card">
        {error_banner}
        <div class="mode-badge">{mode_badge_text}</div><br>
        <div class="status-badge" id="statusBadge">{status_kor} ({status_eng})</div>
        <div class="value"><span id="dustVal">{dust_val:.1f}</span> <span class="unit">µg/m³</span></div>
        <div class="sub-info">
            • <span class="live-dot"></span>실시간 로컬 연결: <b>정상</b><br>
            • 센서 출력 전압: <b id="voltVal">{volt_val:.2f} V</b><br>
            • ☁️ 구글 시트 동기화: <b id="cloudVal">{cloud_msg}</b><br>
            • 🔔 버저 제어 상태: <b id="controlVal">Mute: {is_muted_val} / 기준: {thresh_val:.0f}µg</b><br>
            • 기기 IP 주소: <b>{current_ip}</b>
        </div>
    </div>

    <details>
        <summary>📶 Wi-Fi 공유기 연결 설정</summary>
        <form action="/save" method="GET" class="wifi-form">
            <label>주변 Wi-Fi 선택</label>
            <select name="ssid_select" onchange="document.getElementById('ssid_in').value = this.value;">
                <option value="">-- 검색된 Wi-Fi 목록 --</option>
                {wifi_options}
            </select>
            <label>Wi-Fi 이름 (SSID)</label>
            <input type="text" name="ssid" id="ssid_in" placeholder="Wi-Fi 이름 직접 입력 가능" required>
            <label>Wi-Fi 비밀번호</label>
            <input type="password" name="password" placeholder="비밀번호 (공개 Wi-Fi는 빈칸)">
            <button type="submit" class="btn">저장 및 공유기 연결</button>
        </form>
    </details>

    <div style="margin-top: 14px; max-width: 380px; margin-left: auto; margin-right: auto;">
        <a href="/edit" style="display: block; text-decoration: none; padding: 12px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; color: #38bdf8; font-size: 14px; font-weight: bold; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">📝 user_code.py 웹 에디터 열기</a>
    </div>

    <script>
        async function updateData() {{
            try {{
                const res = await fetch('/data?t=' + Date.now());
                if(res.ok) {{
                    const d = await res.json();
                    document.getElementById('dustVal').innerText = d.density.toFixed(1);
                    document.getElementById('voltVal').innerText = d.voltage.toFixed(2) + ' V';
                    document.getElementById('cloudVal').innerText = d.cloud;
                    document.getElementById('controlVal').innerText = 'Mute: ' + d.mute + ' / 기준: ' + d.thresh + 'µg';
                    const badge = document.getElementById('statusBadge');
                    badge.innerText = d.kor + ' (' + d.eng + ')';
                    badge.style.background = d.color;
                }}
            }} catch(e) {{}}
        }}
        setInterval(updateData, 2000);
        updateData();
    </script>
</body>
</html>"""
    return html


def generate_editor_html(code_text):
    escaped_code = code_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Pico user_code.py 웹 에디터</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 12px; -webkit-text-size-adjust: 100%; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
        h3 {{ font-size: 15px; color: #38bdf8; }}
        .safe-tag {{ display: inline-block; font-size: 11px; background: #065f46; color: #34d399; padding: 3px 8px; border-radius: 6px; font-weight: bold; margin-bottom: 8px; }}
        .back-btn {{ color: #94a3b8; text-decoration: none; font-size: 12px; padding: 6px 10px; background: #1e293b; border-radius: 6px; border: 1px solid #334155; }}
        
        .toolbar {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 10px; }}
        .tool-btn {{ padding: 10px 4px; background: #1e293b; color: #cbd5e1; border: 1px solid #334155; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; text-align: center; }}
        .tool-btn:active {{ background: #334155; }}
        
        textarea {{ width: 100%; height: 65vh; background: #1e293b; color: #f1f5f9; font-family: Consolas, "Courier New", monospace; font-size: 16px; line-height: 1.4; border: 1px solid #334155; border-radius: 10px; padding: 12px; outline: none; resize: none; white-space: pre; tab-size: 4; -webkit-overflow-scrolling: touch; touch-action: pan-x pan-y; }}
        textarea:focus {{ border-color: #38bdf8; }}
        
        .btn-save {{ width: 100%; padding: 14px; margin-top: 10px; background: #0284c7; color: #fff; border: none; border-radius: 10px; font-size: 15px; font-weight: bold; cursor: pointer; }}
        .btn-save:active {{ background: #0369a1; }}
        
        #toast {{ display: none; position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: #22c55e; color: #000; padding: 10px 20px; border-radius: 25px; font-weight: bold; font-size: 13px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); z-index: 999; }}
        .note {{ font-size: 11px; color: #94a3b8; margin-top: 8px; text-align: center; line-height: 1.4; }}
    </style>
</head>
<body>
    <div id="toast"></div>
    <div class="header">
        <h3>📝 user_code.py 웹 에디터</h3>
        <a href="/" class="back-btn">⬅ 메인으로</a>
    </div>
    <div class="safe-tag">🛡️ 시스템 코어 보호 중 (코드를 다 지워도 웹 에디터는 안전합니다)</div>
    
    <div class="toolbar">
        <button type="button" class="tool-btn" onclick="copyAllCode()">📋 전체 복사</button>
        <button type="button" class="tool-btn" onclick="pasteFromClipboard()">📄 붙여넣기</button>
        <button type="button" class="tool-btn" style="color:#ef4444;" onclick="clearAllCode()">🗑️ 전체 지우기</button>
    </div>

    <form action="/save_code" method="POST" id="codeForm">
        <textarea name="code" id="codeArea" spellcheck="false" required>{escaped_code}</textarea>
        <button type="submit" class="btn-save" onclick="return confirm('user_code.py를 저장하고 피코를 재부팅하시겠습니까?');">💾 user_code.py 저장 및 피코 재부팅</button>
    </form>
    
    <div class="note">
        ※ 16px 폰트 고정으로 아이폰 자동 확대가 방지됩니다.<br>
        ※ 저장 시 user_code.py 파일로 덮어쓰고 피코가 자동 재부팅됩니다.
    </div>

    <script>
        function showToast(msg, isErr) {{
            const t = document.getElementById('toast');
            t.innerText = msg;
            t.style.background = isErr ? '#ef4444' : '#22c55e';
            t.style.color = isErr ? '#fff' : '#000';
            t.style.display = 'block';
            setTimeout(() => {{ t.style.display = 'none'; }}, 2200);
        }}
        
        function copyAllCode() {{
            const ta = document.getElementById('codeArea');
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(ta.value)
                    .then(() => showToast('✅ 전체 코드가 복사되었습니다!'))
                    .catch(() => fallbackCopy(ta));
            }} else {{
                fallbackCopy(ta);
            }}
        }}
        
        function fallbackCopy(ta) {{
            ta.focus();
            ta.select();
            ta.setSelectionRange(0, 99999);
            try {{
                document.execCommand('copy');
                showToast('✅ 전체 코드가 복사되었습니다!');
            }} catch(e) {{
                showToast('❌ 복사 실패: 직접 길게 눌러 복사해주세요.', true);
            }}
        }}
        
        async function pasteFromClipboard() {{
            try {{
                if (navigator.clipboard && navigator.clipboard.readText) {{
                    const text = await navigator.clipboard.readText();
                    if (text) {{
                        document.getElementById('codeArea').value = text;
                        showToast('✅ 클립보드 내용을 붙여넣었습니다!');
                        return;
                    }}
                }}
            }} catch(e) {{}}
            const ta = document.getElementById('codeArea');
            ta.focus();
            showToast('💡 입력창을 길게 눌러 [붙여넣기]를 해주세요.');
        }}
        
        function clearAllCode() {{
            if (confirm('에디터 내용을 모두 지우시겠습니까?\\n(다른 앱에서 수정한 코드를 붙여넣기 편리합니다)')) {{
                const ta = document.getElementById('codeArea');
                ta.value = '';
                ta.focus();
                showToast('🗑️ 내용이 모두 지워졌습니다.');
            }}
        }}
    </script>
</body>
</html>"""
    return html


# -----------------------------------------------------------------
# [6] POST 데이터 스트리밍 수신 및 user_code.py 저장 함수
# -----------------------------------------------------------------
def handle_save_code(conn, initial_body, content_length):
    """
    POST로 전송된 대용량 폼 데이터를 스트리밍 방식으로 수신 및 URL 디코딩하여 user_code.py에 안전하게 저장
    """
    body_stream = [initial_body]
    bytes_read = len(initial_body)
    
    state = 0
    hex_chars = b""
    is_first = True
    temp_file = "user_code_tmp.py"
    
    try:
        with open(temp_file, "wb") as f_out:
            while True:
                if body_stream:
                    chunk = body_stream.pop(0)
                elif bytes_read < content_length:
                    to_read = min(1024, content_length - bytes_read)
                    chunk = conn.recv(to_read)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                else:
                    break
                
                if not chunk:
                    continue
                    
                if is_first:
                    if chunk.startswith(b"code="):
                        chunk = chunk[5:]
                    is_first = False
                
                out_buf = bytearray()
                i = 0
                while i < len(chunk):
                    b = chunk[i]
                    if state == 0:
                        if b == ord(b'+'):
                            out_buf.append(ord(b' '))
                            i += 1
                        elif b == ord(b'%'):
                            state = 1
                            hex_chars = b""
                            i += 1
                        else:
                            out_buf.append(b)
                            i += 1
                    elif state == 1:
                        hex_chars += bytes([b])
                        i += 1
                        if len(hex_chars) == 2:
                            try:
                                val = int(hex_chars, 16)
                                out_buf.append(val)
                            except Exception:
                                out_buf.append(ord(b'%'))
                                out_buf.extend(hex_chars)
                            state = 0
                            hex_chars = b""
                
                if out_buf:
                    f_out.write(out_buf)
                    
            if state == 1 and hex_chars:
                f_out.write(b'%' + hex_chars)
                
        # 파일 저장 검증
        stat = os.stat(temp_file)
        if stat[6] > 10:
            with open(temp_file, "rb") as f_src:
                with open(USER_FILE, "wb") as f_dst:
                    while True:
                        buf = f_src.read(1024)
                        if not buf:
                            break
                        f_dst.write(buf)
            try:
                os.remove(temp_file)
            except Exception:
                pass
            return True, f"저장 성공 ({stat[6]} bytes)"
        else:
            return False, "저장된 파일 내용이 비어있습니다."
    except Exception as e:
        return False, f"파일 저장 오류: {e}"


# -----------------------------------------------------------------
# [7] 메인 실행 루프 (시스템 코어 + user_code 안전 연동)
# -----------------------------------------------------------------
def main():
    print("==========================================")
    print(" 🛡️ Pico Dust Core System v2.0 가동")
    print("==========================================")
    
    # 1. 1602 LCD 초기화
    lcd = None
    try:
        i2c0 = machine.I2C(0, sda=machine.Pin(8), scl=machine.Pin(9), freq=100000)
        addrs0 = i2c0.scan()
        if addrs0:
            lcd = I2cLcd(i2c0, addrs0[0])
            lcd.display_2lines("Pico Core System", "Starting...")
            print(f"LCD 연결 완료 (I2C 주소: {hex(addrs0[0])})")
        else:
            print("경고: I2C0에서 LCD를 찾지 못했습니다")
    except Exception as e:
        print(f"LCD 초기화 오류: {e}")

    # 2. 사용자 모듈(user_code.py) 로드
    user_mod, user_err = load_user_module()

    # 3. Wi-Fi 및 네트워크 루프
    while True:
        config = load_wifi_config()
        connected = False
        current_ip = None
        mode = "OFFLINE_AP"
        
        if config and "ssid" in config and config["ssid"]:
            connected, current_ip = connect_sta_wifi(config["ssid"], config.get("password", ""), timeout_sec=8, lcd_ref=lcd)
            
        if connected:
            mode = "ONLINE_STA"
        else:
            current_ip = start_ap_mode(lcd_ref=lcd)
            mode = "OFFLINE_AP"
            
        wifi_list = scan_nearby_wifis()
        
        # 고속 소켓 서버 오픈
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('', 80))
        server_socket.listen(5)
        server_socket.settimeout(0.02)
        
        print(f"🚀 웹 서버 가동! 접속: http://{current_ip}")
        
        wlan_sta = network.WLAN(network.STA_IF)
        display_toggle = 0
        
        # 기본 측정 초기화
        avg_v = 0.0
        avg_density = 0.0
        status_eng = "INIT"
        status_kor = "준비"
        color_hex = "#38bdf8"
        
        meas_interval = getattr(user_mod, "DISPLAY_UPDATE_INTERVAL_MS", 2000) if user_mod else 2000
        sync_interval = getattr(user_mod, "CLOUD_SYNC_INTERVAL_MS", 60000) if user_mod else 60000
        
        last_measure_time = utime.ticks_ms()
        last_cloud_sync_time = utime.ticks_ms() - sync_interval + 5000
        
        # 비동기 메인 루프
        try:
            while True:
                now = utime.ticks_ms()
                
                # A. Wi-Fi 끊김 감지 -> AP 모드 복귀
                if mode == "ONLINE_STA" and not wlan_sta.isconnected():
                    print("⚠️ 공유기 Wi-Fi 끊김 감지 -> 오프라인 AP 모드로 전환")
                    server_socket.close()
                    break
                
                # B. 주기적 센서 측정 & LCD & 버저 (user_code 안전 호출)
                if utime.ticks_diff(now, last_measure_time) >= meas_interval:
                    last_measure_time = now
                    
                    if user_mod and hasattr(user_mod, 'read_dust_sensor'):
                        try:
                            avg_v, avg_density = user_mod.read_dust_sensor()
                            status_eng, status_kor, color_hex = user_mod.get_status_info(avg_density)
                            
                            # 버저 알림 제어
                            is_muted = getattr(user_mod, "is_muted", False)
                            thresh = getattr(user_mod, "alert_threshold", 80.0)
                            if (not is_muted) and (avg_density >= thresh):
                                if hasattr(user_mod, 'play_alert_beep'):
                                    user_mod.play_alert_beep()
                            else:
                                if hasattr(user_mod, 'buzzer'):
                                    user_mod.buzzer.duty_u16(0)
                        except Exception as e:
                            user_err = f"측정 실행 오류: {e}"
                            status_eng, status_kor, color_hex = "ERR", "오류", "#ef4444"
                    
                    # 1602 LCD 출력
                    if lcd:
                        lcd.move_to(0, 0)
                        if user_err:
                            lcd.putstr("User Code Error ")
                        else:
                            lcd.putstr("Dust:{:5.1f} ug/m3 ".format(avg_density))
                            
                        lcd.move_to(0, 1)
                        if display_toggle % 2 == 0:
                            # 16자리 전체를 활용하여 IP 주소 잘림 방지 (예: IP:192.168.0.71)
                            tag = "IP:" if mode == "ONLINE_STA" else "AP:"
                            ip_str = str(current_ip) if current_ip else "No IP"
                            if len(ip_str) <= 13:
                                ip_disp = tag + ip_str
                            else:
                                ip_disp = ip_str
                            lcd.putstr("{:<16}".format(ip_disp[:16]))
                        else:
                            if user_err:
                                lcd.putstr("Check /edit Web ")
                            else:
                                is_muted = getattr(user_mod, "is_muted", False) if user_mod else False
                                mute_tag = "M" if is_muted else "S"
                                lcd.putstr("{:<4} {:>4.1f}V {:>1}".format(status_eng[:4], avg_v, mute_tag))
                    
                    display_toggle += 1
                
                # C. 주기적 구글 시트 클라우드 동기화 (user_code 안전 호출)
                if mode == "ONLINE_STA" and wlan_sta.isconnected():
                    if utime.ticks_diff(now, last_cloud_sync_time) >= sync_interval:
                        last_cloud_sync_time = now
                        if user_mod and hasattr(user_mod, 'sync_with_google_sheets'):
                            try:
                                user_mod.sync_with_google_sheets(avg_density, avg_v, status_eng)
                            except Exception as e:
                                print("구글 시트 연동 오류:", e)
                
                # D. 웹 요청 수신 및 즉각 처리
                try:
                    conn, addr = server_socket.accept()
                    conn.settimeout(1.0)
                    raw_req = conn.recv(1024)
                    if raw_req:
                        header_end = raw_req.find(b"\r\n\r\n")
                        if header_end == -1:
                            header_end = raw_req.find(b"\n\n")
                            header_bytes = raw_req[:header_end] if header_end != -1 else raw_req
                            initial_body = raw_req[header_end + 2:] if header_end != -1 else b""
                        else:
                            header_bytes = raw_req[:header_end]
                            initial_body = raw_req[header_end + 4:]
                            
                        req_str = header_bytes.decode('utf-8', 'ignore')
                        first_line = req_str.split('\n')[0].strip() if '\n' in req_str else req_str.strip()
                        
                        # 1) AJAX 실시간 데이터 요청 (/data)
                        if "GET /data" in first_line:
                            cloud_st = getattr(user_mod, "cloud_sync_status", "준비") if user_mod else "코드 오류"
                            is_mut = getattr(user_mod, "is_muted", False) if user_mod else False
                            th_val = getattr(user_mod, "alert_threshold", 80.0) if user_mod else 80.0
                            
                            data_json = json.dumps({
                                "density": round(avg_density, 1),
                                "voltage": round(avg_v, 2),
                                "eng": status_eng,
                                "kor": status_kor,
                                "color": color_hex,
                                "cloud": cloud_st,
                                "mute": is_mut,
                                "thresh": th_val,
                                "user_err": user_err
                            })
                            resp = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n" + data_json
                            conn.sendall(resp.encode('utf-8'))
                        
                        # 2) 파비콘 즉시 종결
                        elif "GET /favicon.ico" in first_line:
                            conn.sendall(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
                            
                        # 3) 웹 에디터 화면 요청 (/edit -> user_code.py 편집)
                        elif "GET /edit" in first_line:
                            try:
                                with open(USER_FILE, "r") as f:
                                    code_text = f.read()
                            except Exception as err:
                                code_text = f"# {USER_FILE} 읽기 실패: {err}"
                            
                            editor_html = generate_editor_html(code_text)
                            resp_bytes = editor_html.encode('utf-8')
                            header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_bytes)}\r\n\r\n"
                            conn.sendall(header.encode('utf-8'))
                            conn.sendall(resp_bytes)
                            gc.collect()

                        # 4) 수정한 코드 저장 및 재부팅 (/save_code -> user_code.py에 저장)
                        elif "POST /save_code" in first_line:
                            content_length = 0
                            for line in header_bytes.split(b"\r\n"):
                                if line.lower().startswith(b"content-length:"):
                                    try:
                                        content_length = int(line.split(b":")[1].strip())
                                    except Exception:
                                        pass
                            
                            success, msg = handle_save_code(conn, initial_body, content_length)
                            if success:
                                resp_html = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>저장 완료</title><style>body{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}h2{color:#22c55e;margin-bottom:15px;}.btn{display:inline-block;padding:10px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:8px;margin-top:20px;font-weight:bold;}</style></head><body><h2>✅ user_code.py 저장 완료!</h2><p>피코를 자동으로 재부팅합니다... (약 5초 후 새로고침)</p><a href="/" class="btn">메인으로 이동</a><script>setTimeout(()=>{location.href='/';}, 5000);</script></body></html>"""
                                resp_b = resp_html.encode('utf-8')
                                header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
                                conn.sendall(header.encode('utf-8') + resp_b)
                                conn.close()
                                server_socket.close()
                                print(f"🔄 {USER_FILE} 저장 완료! 1초 후 피코를 재부팅합니다...")
                                utime.sleep(1)
                                machine.reset()
                                break
                            else:
                                err_html = f"<!DOCTYPE html><html><body style='background:#0f172a;color:#fff;text-align:center;padding:50px 20px;'><h2>❌ 저장 실패</h2><p>{msg}</p><a href='/edit' style='color:#38bdf8;'>에디터로 돌아가기</a></body></html>"
                                resp_b = err_html.encode('utf-8')
                                header = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
                                conn.sendall(header.encode('utf-8') + resp_b)

                        # 5) Wi-Fi 설정 저장 (/save)
                        elif "GET /save" in first_line:
                            query = first_line.split('?')[1].split(' ')[0]
                            params = {}
                            for item in query.split('&'):
                                if '=' in item:
                                    k, v = item.split('=', 1)
                                    params[k] = url_decode(v)
                            new_ssid = params.get('ssid', '').strip()
                            new_pass = params.get('password', '').strip()
                            if new_ssid:
                                save_wifi_config(new_ssid, new_pass)
                                save_html = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n<h2>✅ Wi-Fi 저장 완료!</h2><p>[{new_ssid}] 연결을 시작합니다.</p>"
                                conn.sendall(save_html.encode('utf-8'))
                                conn.close()
                                utime.sleep(1)
                                server_socket.close()
                                break
                        
                        # 6) 메인 페이지 서빙 (GET /)
                        else:
                            cloud_st = getattr(user_mod, "cloud_sync_status", "대기 중") if user_mod else "코드 오류"
                            is_mut = getattr(user_mod, "is_muted", False) if user_mod else False
                            th_val = getattr(user_mod, "alert_threshold", 80.0) if user_mod else 80.0
                            
                            html_str = generate_main_html(
                                mode, current_ip, wifi_list, user_err,
                                avg_density, avg_v, status_eng, status_kor, color_hex,
                                cloud_st, is_mut, th_val
                            )
                            html_bytes = html_str.encode('utf-8')
                            header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: " + str(len(html_bytes)) + "\r\n\r\n"
                            conn.sendall(header.encode('utf-8'))
                            conn.sendall(html_bytes)
                    
                    conn.close()
                except OSError:
                    pass
                except Exception as e:
                    try:
                        conn.close()
                    except Exception:
                        pass
                
                gc.collect()
                utime.sleep_ms(10)
                
        except Exception as e:
            print("루프 오류:", e)
            try:
                server_socket.close()
            except Exception:
                pass
            utime.sleep(1)

if __name__ == "__main__":
    main()
