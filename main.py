# =================================================================
# 🛡️ main.py : 시스템 코어 프레임워크 (Wi-Fi, 웹서버, LCD, 웹 에디터)
# =================================================================
# 이 파일은 피코의 핵심 인프라를 담당하는 불변 시스템 파일입니다.
# 사용자의 커스텀 로직(센서 측정, 구글시트 연동 등)은 'user_code.py'에서 실행되며,
# user_code.py에 오타나 오류가 발생해도 이 시스템 코어와 웹 에디터는 절대 다운되지 않습니다.
#
# user_code.py가 갖춰야 하는 인터페이스는 REQUIRED_USER_ATTRS를 참고하세요.
# =================================================================

import machine
import utime
import network
import socket
import json
import urequests
import gc
import os

from netutil import url_decode

try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

try:
    import _thread
    _THREADING_AVAILABLE = True
except ImportError:
    _THREADING_AVAILABLE = False

# -----------------------------------------------------------------
# [1] 시스템 설정 및 기본 상수
# -----------------------------------------------------------------
CONFIG_FILE = "wifi_config.json"
USER_FILE = "user_code.py"
USER_FILE_TEMPLATE = "user_code.default.py"
MAX_USER_CODE_SIZE = 64 * 1024  # user_code.py 저장 최대 크기 (64KB)
AP_SSID = "Pico-Dust-Setup"     # 피코 단독 핫스팟(AP) 이름
AP_PASS = ""                    # 비밀번호 (빈칸 = 공개 오픈 AP)
AP_IP = "192.168.4.1"

# user_code.py가 구현해야 하는 인터페이스 계약.
# main.py는 이 항목들을 hasattr/getattr로 조회해서 있으면 쓰고 없으면
# 해당 기능만 조용히 건너뜁니다 (전체 시스템은 항상 살아있음).
REQUIRED_USER_ATTRS = [
    "read_dust_sensor",         # () -> (avg_voltage: float, density: float)
    "get_status_info",          # (density: float) -> (eng: str, kor: str, color_hex: str)
    "sync_with_google_sheets",  # (density, voltage, status_eng) -> bool
    "play_alert_beep",          # () -> None
    "is_muted",                 # bool
    "alert_threshold",          # float
    "cloud_sync_status",        # str
]


def log_error(context, exc):
    print(f"⚠️ [{context}] {type(exc).__name__}: {exc}")


def validate_user_module(user_mod):
    missing = [name for name in REQUIRED_USER_ATTRS if not hasattr(user_mod, name)]
    if missing:
        print(f"⚠️ user_code.py에 다음 항목이 없습니다 (해당 기능만 비활성화됩니다): {', '.join(missing)}")


# -----------------------------------------------------------------
# [2] I2C 1602 LCD 드라이버 (PCF8574 I2C 어댑터용)
# -----------------------------------------------------------------
class I2cLcd:
    def __init__(self, i2c, i2c_addr):
        self.i2c = i2c
        self.addr = i2c_addr
        self._error_logged = False
        utime.sleep_ms(200)
        for _ in range(3):
            self._send_nibble(0x30)
            utime.sleep_ms(10)
        self._send_nibble(0x20)
        utime.sleep_ms(10)

        for cmd in [0x28, 0x0C, 0x06, 0x01]:
            self._write_cmd(cmd)
            utime.sleep_ms(5)

    def _log_once(self, e):
        # LCD는 주기적으로 계속 쓰기 때문에, 연결이 끊기면 매번 로그를 남기지 않고
        # 부팅 후 최초 1회만 남깁니다 (콘솔 스팸 방지).
        if not self._error_logged:
            log_error("LCD I2C", e)
            self._error_logged = True

    def _send_nibble(self, nibble):
        b = (nibble & 0xF0) | 0x08  # 백라이트 ON
        try:
            self.i2c.writeto(self.addr, bytes([b | 0x04]))
            utime.sleep_us(50)
            self.i2c.writeto(self.addr, bytes([b]))
            utime.sleep_us(50)
        except Exception as e:
            self._log_once(e)

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
            except Exception as e:
                self._log_once(e)
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
            except Exception as e:
                self._log_once(e)
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
def ensure_user_code_exists():
    try:
        os.stat(USER_FILE)
        return
    except OSError:
        pass

    print(f"⚠️ {USER_FILE} 파일이 없어 기본 템플릿({USER_FILE_TEMPLATE})으로 생성합니다.")
    try:
        with open(USER_FILE_TEMPLATE, "r") as f_src, open(USER_FILE, "w") as f_dst:
            while True:
                chunk = f_src.read(512)
                if not chunk:
                    break
                f_dst.write(chunk)
    except Exception as e:
        log_error("템플릿 생성", e)

def load_user_module():
    """user_code.py를 안전하게 로드. 에러 발생 시 예외 격리."""
    ensure_user_code_exists()
    try:
        import user_code
        validate_user_module(user_code)
        return user_code, None
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        print(f"❌ [{USER_FILE} 로드 오류] {err_msg}")
        return None, err_msg


# -----------------------------------------------------------------
# [5] 클라우드 동기화 비동기 실행 (RP2350 두 번째 코어 활용)
# -----------------------------------------------------------------
# urequests에는 타임아웃이 없어서 네트워크가 불안정하면 요청이 오래 멈출 수
# 있습니다. 메인 루프(웹서버/LCD/센서 측정)가 이 때문에 함께 멈추지 않도록
# 동기화만 별도 코어(스레드)에서 수행합니다. _thread를 쓸 수 없는 빌드에서는
# 기존처럼 동기 호출로 자동 폴백합니다.
_sync_lock = _thread.allocate_lock() if _THREADING_AVAILABLE else None
_sync_busy = False

def _run_cloud_sync(user_mod, density, voltage, status_eng):
    global _sync_busy
    try:
        user_mod.sync_with_google_sheets(density, voltage, status_eng)
    except Exception as e:
        log_error("클라우드 동기화", e)
    finally:
        if _sync_lock:
            _sync_lock.acquire()
        _sync_busy = False
        if _sync_lock:
            _sync_lock.release()

def trigger_cloud_sync(user_mod, density, voltage, status_eng):
    global _sync_busy
    if not (user_mod and hasattr(user_mod, 'sync_with_google_sheets')):
        return

    if not _THREADING_AVAILABLE:
        _run_cloud_sync(user_mod, density, voltage, status_eng)
        return

    _sync_lock.acquire()
    already_busy = _sync_busy
    if not already_busy:
        _sync_busy = True
    _sync_lock.release()

    if already_busy:
        print("⏭️ 이전 클라우드 동기화가 아직 진행 중이라 이번 주기는 건너뜁니다.")
        return

    try:
        _thread.start_new_thread(_run_cloud_sync, (user_mod, density, voltage, status_eng))
    except Exception as e:
        log_error("클라우드 동기화 스레드 시작", e)
        _sync_lock.acquire()
        _sync_busy = False
        _sync_lock.release()


# -----------------------------------------------------------------
# [6] HTML 생성 함수 (대시보드 UI & 웹 에디터 UI)
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
        ※ 저장 시 user_code.py 파일로 덮어쓰고, 내용이 바뀐 경우에만 피코가 자동 재부팅됩니다.
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
# [7] POST 데이터 스트리밍 수신 및 user_code.py 저장 함수
# -----------------------------------------------------------------
def _file_hash(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                buf = f.read(512)
                if not buf:
                    break
                h.update(buf)
        return h.digest()
    except Exception:
        return None

def handle_save_code(conn, initial_body, content_length):
    """
    POST로 전송된 대용량 폼 데이터를 스트리밍 방식으로 수신 및 URL 디코딩하여 user_code.py에 안전하게 저장.
    반환값: (성공 여부, 메시지, 기존 파일과 내용이 달라졌는지)
    """
    if content_length > MAX_USER_CODE_SIZE:
        return False, f"코드 크기가 너무 큽니다 ({content_length} > {MAX_USER_CODE_SIZE} bytes)", False

    old_hash = _file_hash(USER_FILE)

    body_stream = [initial_body]
    bytes_read = len(initial_body)

    state = 0
    hex_chars = b""
    is_first = True
    temp_file = "user_code_tmp.py"
    new_hash_ctx = hashlib.sha256()

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
                    new_hash_ctx.update(out_buf)

            if state == 1 and hex_chars:
                tail = b'%' + hex_chars
                f_out.write(tail)
                new_hash_ctx.update(tail)

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
            new_hash = new_hash_ctx.digest()
            changed = (old_hash != new_hash)
            return True, f"저장 성공 ({stat[6]} bytes)", changed
        else:
            return False, "저장된 파일 내용이 비어있습니다.", False
    except Exception as e:
        return False, f"파일 저장 오류: {e}", False


# -----------------------------------------------------------------
# [8] HTTP 클라이언트 처리 (라우팅)
# -----------------------------------------------------------------
class LoopState:
    """웹 요청 핸들러가 참조하는, 매 측정 주기마다 갱신되는 공유 상태."""
    def __init__(self):
        self.mode = "OFFLINE_AP"
        self.current_ip = None
        self.wifi_list = []
        self.user_mod = None
        self.user_err = None
        self.avg_v = 0.0
        self.avg_density = 0.0
        self.status_eng = "INIT"
        self.status_kor = "준비"
        self.color_hex = "#38bdf8"


def handle_client(conn, state):
    """
    소켓 커넥션 하나를 처리합니다.
    Wi-Fi 설정이 저장되어 재연결이 필요한 경우 True를 반환합니다.
    """
    raw_req = conn.recv(1024)
    if not raw_req:
        return False

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

    user_mod = state.user_mod

    # 1) AJAX 실시간 데이터 요청 (/data)
    if "GET /data" in first_line:
        cloud_st = getattr(user_mod, "cloud_sync_status", "준비") if user_mod else "코드 오류"
        is_mut = getattr(user_mod, "is_muted", False) if user_mod else False
        th_val = getattr(user_mod, "alert_threshold", 80.0) if user_mod else 80.0

        data_json = json.dumps({
            "density": round(state.avg_density, 1),
            "voltage": round(state.avg_v, 2),
            "eng": state.status_eng,
            "kor": state.status_kor,
            "color": state.color_hex,
            "cloud": cloud_st,
            "mute": is_mut,
            "thresh": th_val,
            "user_err": state.user_err
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

        success, msg, changed = handle_save_code(conn, initial_body, content_length)
        if success and changed:
            resp_html = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>저장 완료</title><style>body{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}h2{color:#22c55e;margin-bottom:15px;}.btn{display:inline-block;padding:10px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:8px;margin-top:20px;font-weight:bold;}</style></head><body><h2>✅ user_code.py 저장 완료!</h2><p>피코를 자동으로 재부팅합니다... (약 5초 후 새로고침)</p><a href="/" class="btn">메인으로 이동</a><script>setTimeout(()=>{location.href='/';}, 5000);</script></body></html>"""
            resp_b = resp_html.encode('utf-8')
            header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
            conn.sendall(header.encode('utf-8') + resp_b)
            conn.close()
            print(f"🔄 {USER_FILE} 저장 완료! 1초 후 피코를 재부팅합니다...")
            utime.sleep(1)
            machine.reset()
        elif success and not changed:
            resp_html = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>저장 완료</title><style>body{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}h2{color:#38bdf8;margin-bottom:15px;}.btn{display:inline-block;padding:10px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:8px;margin-top:20px;font-weight:bold;}</style></head><body><h2>💾 저장 완료 (변경 없음)</h2><p>기존 내용과 동일해 재부팅은 하지 않았습니다.</p><a href="/edit" class="btn">에디터로 돌아가기</a></body></html>"""
            resp_b = resp_html.encode('utf-8')
            header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
            conn.sendall(header.encode('utf-8') + resp_b)
        else:
            err_html = f"<!DOCTYPE html><html><body style='background:#0f172a;color:#fff;text-align:center;padding:50px 20px;'><h2>❌ 저장 실패</h2><p>{msg}</p><a href='/edit' style='color:#38bdf8;'>에디터로 돌아가기</a></body></html>"
            resp_b = err_html.encode('utf-8')
            status_line = "413 Payload Too Large" if "너무 큽니다" in msg else "500 Internal Server Error"
            header = f"HTTP/1.1 {status_line}\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
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
            return True

    # 6) 메인 페이지 서빙 (GET /)
    else:
        cloud_st = getattr(user_mod, "cloud_sync_status", "대기 중") if user_mod else "코드 오류"
        is_mut = getattr(user_mod, "is_muted", False) if user_mod else False
        th_val = getattr(user_mod, "alert_threshold", 80.0) if user_mod else 80.0

        html_str = generate_main_html(
            state.mode, state.current_ip, state.wifi_list, state.user_err,
            state.avg_density, state.avg_v, state.status_eng, state.status_kor, state.color_hex,
            cloud_st, is_mut, th_val
        )
        html_bytes = html_str.encode('utf-8')
        header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: " + str(len(html_bytes)) + "\r\n\r\n"
        conn.sendall(header.encode('utf-8'))
        conn.sendall(html_bytes)

    return False


# -----------------------------------------------------------------
# [9] 센서 측정 + LCD 갱신 (user_code 안전 호출)
# -----------------------------------------------------------------
def measure_and_update_lcd(lcd, state, display_toggle):
    user_mod = state.user_mod

    if user_mod and hasattr(user_mod, 'read_dust_sensor'):
        try:
            state.avg_v, state.avg_density = user_mod.read_dust_sensor()
            state.status_eng, state.status_kor, state.color_hex = user_mod.get_status_info(state.avg_density)

            is_muted = getattr(user_mod, "is_muted", False)
            thresh = getattr(user_mod, "alert_threshold", 80.0)
            if (not is_muted) and (state.avg_density >= thresh):
                if hasattr(user_mod, 'play_alert_beep'):
                    user_mod.play_alert_beep()
            else:
                if hasattr(user_mod, 'buzzer'):
                    user_mod.buzzer.duty_u16(0)
        except Exception as e:
            state.user_err = f"측정 실행 오류: {e}"
            state.status_eng, state.status_kor, state.color_hex = "ERR", "오류", "#ef4444"
            log_error("센서 측정", e)

    if lcd:
        lcd.move_to(0, 0)
        if state.user_err:
            lcd.putstr("User Code Error ")
        else:
            lcd.putstr("Dust:{:5.1f} ug/m3 ".format(state.avg_density))

        lcd.move_to(0, 1)
        if display_toggle % 2 == 0:
            tag = "IP:" if state.mode == "ONLINE_STA" else "AP:"
            ip_str = str(state.current_ip) if state.current_ip else "No IP"
            if len(ip_str) <= 13:
                ip_disp = tag + ip_str
            else:
                ip_disp = ip_str
            lcd.putstr("{:<16}".format(ip_disp[:16]))
        else:
            if state.user_err:
                lcd.putstr("Check /edit Web ")
            else:
                is_muted = getattr(user_mod, "is_muted", False) if user_mod else False
                mute_tag = "M" if is_muted else "S"
                lcd.putstr("{:<4} {:>4.1f}V {:>1}".format(state.status_eng[:4], state.avg_v, mute_tag))

    return display_toggle + 1


# -----------------------------------------------------------------
# [10] 메인 실행 루프 (시스템 코어 + user_code 안전 연동)
# -----------------------------------------------------------------
def init_lcd():
    try:
        i2c0 = machine.I2C(0, sda=machine.Pin(8), scl=machine.Pin(9), freq=100000)
        addrs0 = i2c0.scan()
        if addrs0:
            lcd = I2cLcd(i2c0, addrs0[0])
            lcd.display_2lines("Pico Core System", "Starting...")
            print(f"LCD 연결 완료 (I2C 주소: {hex(addrs0[0])})")
            return lcd
        print("경고: I2C0에서 LCD를 찾지 못했습니다")
    except Exception as e:
        log_error("LCD 초기화", e)
    return None


def connect_network(lcd, state):
    config = load_wifi_config()
    connected = False
    current_ip = None

    if config and "ssid" in config and config["ssid"]:
        connected, current_ip = connect_sta_wifi(config["ssid"], config.get("password", ""), timeout_sec=8, lcd_ref=lcd)

    if connected:
        state.mode = "ONLINE_STA"
        state.current_ip = current_ip
    else:
        state.current_ip = start_ap_mode(lcd_ref=lcd)
        state.mode = "OFFLINE_AP"

    state.wifi_list = scan_nearby_wifis()


def serve_until_reconnect_needed(lcd, state):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('', 80))
    server_socket.listen(5)
    server_socket.settimeout(0.02)

    print(f"🚀 웹 서버 가동! 접속: http://{state.current_ip}")

    wlan_sta = network.WLAN(network.STA_IF)
    display_toggle = 0

    user_mod = state.user_mod
    meas_interval = getattr(user_mod, "DISPLAY_UPDATE_INTERVAL_MS", 2000) if user_mod else 2000
    sync_interval = getattr(user_mod, "CLOUD_SYNC_INTERVAL_MS", 60000) if user_mod else 60000

    last_measure_time = utime.ticks_ms()
    last_cloud_sync_time = utime.ticks_ms() - sync_interval + 5000

    try:
        while True:
            now = utime.ticks_ms()

            # A. Wi-Fi 끊김 감지 -> AP 모드 복귀
            if state.mode == "ONLINE_STA" and not wlan_sta.isconnected():
                print("⚠️ 공유기 Wi-Fi 끊김 감지 -> 오프라인 AP 모드로 전환")
                server_socket.close()
                return

            # B. 주기적 센서 측정 & LCD & 버저
            if utime.ticks_diff(now, last_measure_time) >= meas_interval:
                last_measure_time = now
                display_toggle = measure_and_update_lcd(lcd, state, display_toggle)

            # C. 주기적 구글 시트 클라우드 동기화 (별도 코어에서 실행, 메인 루프는 멈추지 않음)
            if state.mode == "ONLINE_STA" and wlan_sta.isconnected():
                if utime.ticks_diff(now, last_cloud_sync_time) >= sync_interval:
                    last_cloud_sync_time = now
                    trigger_cloud_sync(state.user_mod, state.avg_density, state.avg_v, state.status_eng)

            # D. 웹 요청 수신 및 즉각 처리
            conn = None
            try:
                conn, addr = server_socket.accept()
                conn.settimeout(1.0)
                wifi_saved = handle_client(conn, state)
                conn.close()
                if wifi_saved:
                    server_socket.close()
                    return
            except OSError:
                pass
            except Exception as e:
                log_error("클라이언트 처리", e)
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

            gc.collect()
            utime.sleep_ms(10)

    except Exception as e:
        log_error("서빙 루프", e)
        try:
            server_socket.close()
        except Exception:
            pass
        utime.sleep(1)


def main():
    print("==========================================")
    print(" 🛡️ Pico Dust Core System v2.0 가동")
    print("==========================================")

    lcd = init_lcd()
    user_mod, user_err = load_user_module()

    state = LoopState()
    state.user_mod = user_mod
    state.user_err = user_err

    while True:
        connect_network(lcd, state)
        serve_until_reconnect_needed(lcd, state)


if __name__ == "__main__":
    main()
