# =================================================================
# 🛡️ main.py : 시스템 코어 글루 코드 (Wi-Fi, 웹서버, LCD, 앱 연동)
# =================================================================
# 이 파일은 피코를 부팅하고 각 기능 모듈(wifi_manager, web_ui, file_editor,
# ota, app_manager, bg_thread, lcd_driver, console_log)을 서로 연결해주는
# 얇은 글루 코드입니다. 실제 로직은 각 모듈 파일에 있습니다.
#
# 센서별 커스텀 로직(반응속도 게임, 미세먼지 모니터 등)은 app_*.py 중
# 하나가 담당하며, app_manager.load_active_app()으로 예외 격리해서 로드합니다
# — 활성 앱에 오타나 오류가 있어도 이 시스템 코어와 웹 에디터는 절대
# 다운되지 않습니다. 앱이 갖춰야 하는 인터페이스는 app_manager의
# REQUIRED_APP_ATTRS를 참고하세요.
# =================================================================

# console_log를 가장 먼저 import해야 이 시점 이후의 모든 print() 호출
# (다른 모듈 것까지)이 원격 콘솔(/logs) 버퍼에 잡힙니다.
from console_log import log_error, log_buffer, flush_log_to_file

import machine
import network
import socket
import json
import gc
import os
import utime

from netutil import url_decode
from bg_thread import register_periodic_task, start_background_worker
from lcd_driver import I2cLcd
from wifi_manager import (
    load_wifi_config, save_wifi_config, scan_nearby_wifis,
    connect_sta_wifi, start_ap_mode, AP_RETRY_INTERVAL_MS,
)
from web_ui import (
    generate_main_html, generate_logs_html, generate_file_list_html,
    generate_editor_html_head, generate_editor_html_tail, generate_app_list_html,
)
from file_editor import (
    handle_save_code, is_valid_editable_filename, list_editable_files, revert_file,
)
from ota import OTA_CHECK_INTERVAL_MS, trigger_ota_check, get_ota_status_text, get_last_update_text
from app_manager import (
    load_active_app, list_available_apps, get_active_app_name, set_active_app_name,
)

HEARTBEAT_INTERVAL_MS = 30 * 1000


# -----------------------------------------------------------------
# 요청 경로 파싱 + 요청 처리 루프 상태
# -----------------------------------------------------------------
def _parse_request_path(first_line):
    """'GET /edit?file=main.py HTTP/1.1' -> ('/edit', {'file': 'main.py'})"""
    try:
        target = first_line.split(' ')[1]
    except Exception:
        target = ""
    if '?' in target:
        path, query = target.split('?', 1)
    else:
        path, query = target, ""
    params = {}
    for item in query.split('&'):
        if '=' in item:
            k, v = item.split('=', 1)
            params[k] = url_decode(v)
    return path, params


class LoopState:
    """웹 요청 핸들러가 참조하는, 매 측정 주기마다 갱신되는 공유 상태."""
    def __init__(self):
        self.mode = "OFFLINE_AP"
        self.current_ip = None
        self.wifi_list = []
        self.app_mod = None
        self.app_err = None
        self.avg_v = 0.0
        self.value = 0.0
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

    app_mod = state.app_mod

    # 1) AJAX 실시간 데이터 요청 (/data)
    if "GET /data" in first_line:
        cloud_st = getattr(app_mod, "cloud_sync_status", "준비") if app_mod else "코드 오류"
        is_mut = getattr(app_mod, "is_muted", False) if app_mod else False
        th_val = getattr(app_mod, "alert_threshold", 80.0) if app_mod else 80.0

        data_json = json.dumps({
            "value": round(state.value, 1),
            "voltage": round(state.avg_v, 2),
            "eng": state.status_eng,
            "kor": state.status_kor,
            "color": state.color_hex,
            "cloud": cloud_st,
            "mute": is_mut,
            "thresh": th_val,
            "app_err": state.app_err,
            "ota": get_ota_status_text(),
            "last_update": get_last_update_text(),
            "mem_free": gc.mem_free()
        })
        resp = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n" + data_json
        conn.sendall(resp.encode('utf-8'))

    # 1b) 원격 콘솔 로그 (텍스트, /logs 페이지가 주기적으로 가져감)
    elif "GET /logs.txt" in first_line:
        body = "\n".join(log_buffer)
        body_bytes = body.encode('utf-8')
        header = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\nContent-Length: {len(body_bytes)}\r\n\r\n"
        conn.sendall(header.encode('utf-8'))
        conn.sendall(body_bytes)

    # 1c) 원격 콘솔 화면 (/logs) — Thonny 없이 Wi-Fi로만 붙어있어도 print() 로그를 봄
    elif "GET /logs" in first_line:
        logs_html = generate_logs_html()
        resp_bytes = logs_html.encode('utf-8')
        header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_bytes)}\r\n\r\n"
        conn.sendall(header.encode('utf-8'))
        conn.sendall(resp_bytes)
        gc.collect()

    # 2) 파비콘 즉시 종결
    elif "GET /favicon.ico" in first_line:
        conn.sendall(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")

    # 3) 웹 에디터 화면 요청 (/edit -> 파일 목록, 또는 /edit?file=<name> -> 편집)
    elif "GET /edit" in first_line:
        _, params = _parse_request_path(first_line)
        target_file = params.get('file', '').strip()

        if not target_file:
            listing_html = generate_file_list_html(list_editable_files())
            resp_bytes = listing_html.encode('utf-8')
            header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_bytes)}\r\n\r\n"
            conn.sendall(header.encode('utf-8'))
            conn.sendall(resp_bytes)
        elif not is_valid_editable_filename(target_file):
            err_html = f"<!DOCTYPE html><html><body style='background:#0f172a;color:#fff;text-align:center;padding:50px 20px;'><h2>❌ 편집할 수 없는 파일입니다</h2><p>{target_file}</p><a href='/edit' style='color:#38bdf8;'>파일 목록으로</a></body></html>"
            resp_b = err_html.encode('utf-8')
            header = f"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
            conn.sendall(header.encode('utf-8') + resp_b)
        else:
            try:
                with open(target_file, "r") as f:
                    code_text = f.read()
            except Exception:
                code_text = ""  # 아직 없는 파일 -> 새로 만드는 셈

            try:
                os.stat(target_file + ".bak")
                has_backup = True
            except OSError:
                has_backup = False

            # Content-Length를 미리 계산하려면 이스케이프된 전체 코드를 먼저
            # 메모리에 만들어야 해서, 큰 파일(main.py 등)에서 메모리 부담과
            # 첫 바이트 지연이 커집니다. 대신 길이 없이 Connection: close로
            # 보내고, 코드 본문은 작은 조각 단위로 이스케이프하며 스트리밍합니다.
            header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"
            conn.sendall(header.encode('utf-8'))
            conn.sendall(generate_editor_html_head(target_file, has_backup).encode('utf-8'))

            CHUNK = 512
            for i in range(0, len(code_text), CHUNK):
                piece = code_text[i:i + CHUNK]
                escaped = piece.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                conn.sendall(escaped.encode('utf-8'))

            conn.sendall(generate_editor_html_tail(target_file).encode('utf-8'))
            gc.collect()

    # 4) 수정한 코드 저장 및 재부팅 (/save_code?file=<name> -> 해당 파일에 저장)
    elif "POST /save_code" in first_line:
        _, params = _parse_request_path(first_line)
        target_file = params.get('file', '').strip()

        content_length = 0
        for line in header_bytes.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":")[1].strip())
                except Exception:
                    pass

        if not is_valid_editable_filename(target_file):
            err_html = f"<!DOCTYPE html><html><body style='background:#0f172a;color:#fff;text-align:center;padding:50px 20px;'><h2>❌ 저장 실패</h2><p>편집할 수 없는 파일명입니다: {target_file}</p><a href='/edit' style='color:#38bdf8;'>파일 목록으로</a></body></html>"
            resp_b = err_html.encode('utf-8')
            header = f"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
            conn.sendall(header.encode('utf-8') + resp_b)
        else:
            success, msg, changed = handle_save_code(conn, initial_body, content_length, target_file)
            if success and changed:
                resp_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>저장 완료</title><style>body{{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}}h2{{color:#22c55e;margin-bottom:15px;}}.btn{{display:inline-block;padding:10px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:8px;margin-top:20px;font-weight:bold;}}</style></head><body><h2>✅ {target_file} 저장 완료!</h2><p>피코를 자동으로 재부팅합니다... (약 5초 후 새로고침)</p><a href="/" class="btn">메인으로 이동</a><script>setTimeout(()=>{{location.href='/';}}, 5000);</script></body></html>"""
                resp_b = resp_html.encode('utf-8')
                header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
                conn.sendall(header.encode('utf-8') + resp_b)
                conn.close()
                print(f"🔄 {target_file} 저장 완료! 1초 후 피코를 재부팅합니다...")
                utime.sleep(1)
                machine.reset()
            elif success and not changed:
                resp_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>저장 완료</title><style>body{{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}}h2{{color:#38bdf8;margin-bottom:15px;}}.btn{{display:inline-block;padding:10px 20px;background:#0284c7;color:#fff;text-decoration:none;border-radius:8px;margin-top:20px;font-weight:bold;}}</style></head><body><h2>💾 저장 완료 (변경 없음)</h2><p>기존 내용과 동일해 재부팅은 하지 않았습니다.</p><a href="/edit?file={target_file}" class="btn">에디터로 돌아가기</a></body></html>"""
                resp_b = resp_html.encode('utf-8')
                header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
                conn.sendall(header.encode('utf-8') + resp_b)
            else:
                err_html = f"<!DOCTYPE html><html><body style='background:#0f172a;color:#fff;text-align:center;padding:50px 20px;'><h2>❌ 저장 실패</h2><p>{msg}</p><a href='/edit?file={target_file}' style='color:#38bdf8;'>에디터로 돌아가기</a></body></html>"
                resp_b = err_html.encode('utf-8')
                status_line = "413 Payload Too Large" if "너무 큽니다" in msg else "500 Internal Server Error"
                header = f"HTTP/1.1 {status_line}\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
                conn.sendall(header.encode('utf-8') + resp_b)

    # 4b) 이전 저장본으로 되돌리기 (/revert?file=<name>)
    elif "GET /revert" in first_line:
        _, params = _parse_request_path(first_line)
        target_file = params.get('file', '').strip()
        reverted = is_valid_editable_filename(target_file) and revert_file(target_file)
        if reverted:
            print(f"↩️ {target_file}을(를) 이전 버전으로 되돌렸습니다.")
            resp_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>복원 완료</title><style>body{{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}}h2{{color:#22c55e;margin-bottom:15px;}}</style></head><body><h2>↩️ {target_file} 이전 버전으로 복원 완료</h2><p>피코를 재부팅합니다...</p></body></html>"""
            resp_b = resp_html.encode('utf-8')
            header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
            conn.sendall(header.encode('utf-8') + resp_b)
            conn.close()
            utime.sleep(1)
            machine.reset()
        else:
            resp = f"HTTP/1.1 303 See Other\r\nLocation: /edit?file={target_file}\r\nConnection: close\r\n\r\n"
            conn.sendall(resp.encode('utf-8'))

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

    # 6) 앱 목록 화면 (/apps)
    elif "GET /apps/set" in first_line:
        _, params = _parse_request_path(first_line)
        target_name = params.get('name', '').strip()
        available = list_available_apps()
        if target_name and target_name in available:
            set_active_app_name(target_name)
            print(f"🔌 활성 앱을 {target_name}(으)로 전환합니다. 재부팅합니다...")
            resp_html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>앱 전환</title><style>body{{background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px 20px;}}h2{{color:#22c55e;margin-bottom:15px;}}</style></head><body><h2>🔌 {target_name}(으)로 전환 완료</h2><p>피코를 재부팅합니다...</p></body></html>"""
            resp_b = resp_html.encode('utf-8')
            header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_b)}\r\n\r\n"
            conn.sendall(header.encode('utf-8') + resp_b)
            conn.close()
            utime.sleep(1)
            machine.reset()
        else:
            resp = "HTTP/1.1 303 See Other\r\nLocation: /apps\r\nConnection: close\r\n\r\n"
            conn.sendall(resp.encode('utf-8'))

    elif "GET /apps" in first_line:
        apps_html = generate_app_list_html(list_available_apps(), get_active_app_name())
        resp_bytes = apps_html.encode('utf-8')
        header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: {len(resp_bytes)}\r\n\r\n"
        conn.sendall(header.encode('utf-8'))
        conn.sendall(resp_bytes)

    # 7) 메인 페이지 서빙 (GET /)
    else:
        cloud_st = getattr(app_mod, "cloud_sync_status", "대기 중") if app_mod else "코드 오류"
        is_mut = getattr(app_mod, "is_muted", False) if app_mod else False
        th_val = getattr(app_mod, "alert_threshold", 80.0) if app_mod else 80.0

        html_str = generate_main_html(
            state.mode, state.current_ip, state.wifi_list, state.app_err,
            state.value, state.avg_v, state.status_eng, state.status_kor, state.color_hex,
            cloud_st, is_mut, th_val, get_ota_status_text(), get_active_app_name(),
            get_last_update_text()
        )
        html_bytes = html_str.encode('utf-8')
        header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: " + str(len(html_bytes)) + "\r\n\r\n"
        conn.sendall(header.encode('utf-8'))
        conn.sendall(html_bytes)

    return False


# -----------------------------------------------------------------
# 센서 측정 + LCD 갱신 (앱 안전 호출)
# -----------------------------------------------------------------
def measure_and_update_lcd(lcd, state, display_toggle):
    app_mod = state.app_mod

    if app_mod and hasattr(app_mod, 'read_dust_sensor'):
        try:
            state.avg_v, state.value = app_mod.read_dust_sensor()
            state.status_eng, state.status_kor, state.color_hex = app_mod.get_status_info(state.value)

            is_muted = getattr(app_mod, "is_muted", False)
            thresh = getattr(app_mod, "alert_threshold", 80.0)
            if (not is_muted) and (state.value >= thresh):
                if hasattr(app_mod, 'play_alert_beep'):
                    app_mod.play_alert_beep()
            else:
                if hasattr(app_mod, 'buzzer'):
                    app_mod.buzzer.duty_u16(0)
        except Exception as e:
            state.app_err = f"측정 실행 오류: {e}"
            state.status_eng, state.status_kor, state.color_hex = "ERR", "오류", "#ef4444"
            log_error("센서 측정", e)

    if lcd:
        lcd.move_to(0, 0)
        if state.app_err:
            lcd.putstr("App Code Error  ")
        else:
            lcd.putstr("Val:{:6.0f}     ".format(state.value))

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
            if state.app_err:
                lcd.putstr("Check /edit Web ")
            else:
                is_muted = getattr(app_mod, "is_muted", False) if app_mod else False
                mute_tag = "M" if is_muted else "S"
                lcd.putstr("{:<4} {:>4.1f}V {:>1}".format(state.status_eng[:4], state.avg_v, mute_tag))

    return display_toggle + 1


# -----------------------------------------------------------------
# 메인 실행 루프
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

    app_mod = state.app_mod
    meas_interval = getattr(app_mod, "DISPLAY_UPDATE_INTERVAL_MS", 2000) if app_mod else 2000

    last_measure_time = utime.ticks_ms()
    last_ap_retry_time = utime.ticks_ms()
    last_heartbeat_time = utime.ticks_ms()

    try:
        while True:
            now = utime.ticks_ms()

            # A. Wi-Fi 끊김 감지 -> AP 모드 복귀
            if state.mode == "ONLINE_STA" and not wlan_sta.isconnected():
                print("⚠️ 공유기 Wi-Fi 끊김 감지 -> 오프라인 AP 모드로 전환")
                server_socket.close()
                return

            # A2. 오프라인 AP 모드에서도 저장된 Wi-Fi를 주기적으로 백그라운드
            # 재시도. 비밀번호를 이미 올바르게 입력해뒀는데 공유기/신호 문제로
            # 접속이 안 됐을 뿐이라면, 사용자가 다시 입력할 필요 없이 알아서
            # 복구되도록 함. (연결 시도 중 잠깐 AP가 끊기는 건 감수)
            if state.mode == "OFFLINE_AP":
                if utime.ticks_diff(now, last_ap_retry_time) >= AP_RETRY_INTERVAL_MS:
                    last_ap_retry_time = now
                    saved_config = load_wifi_config()
                    if saved_config and saved_config.get("ssid"):
                        print("🔁 오프라인 AP 모드에서 저장된 Wi-Fi 재접속 시도...")
                        server_socket.close()
                        return

            # B. 주기적 센서 측정 & LCD & 버저
            if utime.ticks_diff(now, last_measure_time) >= meas_interval:
                last_measure_time = now
                display_toggle = measure_and_update_lcd(lcd, state, display_toggle)

            # 클라우드 동기화/OTA 확인은 이제 bg_thread의 영구 백그라운드
            # 워커(core1)가 자체 타이밍으로 주기적으로 실행합니다
            # (main()에서 register_periodic_task로 한 번만 등록) — 메인
            # 루프는 그쪽 타이밍을 몰라도 됨.

            # C3. 주기적 하트비트 로그 + 파일 저장 (기기가 완전히 먹통이 돼서
            # 웹서버로 /logs를 못 보게 되더라도, 재부팅 후 웹 에디터로
            # debug.log를 열어 먹통 직전 상태(여유 메모리, Wi-Fi 연결 여부)를
            # 확인할 수 있게 함)
            if utime.ticks_diff(now, last_heartbeat_time) >= HEARTBEAT_INTERVAL_MS:
                last_heartbeat_time = now
                print(f"💓 [heartbeat] mem_free={gc.mem_free()} wifi={wlan_sta.isconnected()}")
                flush_log_to_file()

            # D. 웹 요청 수신 및 즉각 처리
            # accept() 자체의 OSError(대기 중 연결 없음 — settimeout(0.02)
            # 때문에 대부분의 루프에서 정상적으로 발생함)와, 연결을 실제로
            # 받은 뒤 처리 중 발생하는 오류를 분리해서 다룹니다. 예전엔 이
            # 둘을 같은 except OSError로 묶어서 처리하다가, handle_client()
            # 도중 OSError(클라이언트가 중간에 끊는 등, Wi-Fi 폴링이 잦을수록
            # 흔함)가 나면 conn.close()를 건너뛰어 소켓이 계속 새는 버그가
            # 있었습니다 — 대시보드를 열어두면(2초마다 폴링) 훨씬 빨리
            # 누적돼서 결국 소켓 풀이 고갈되면 accept() 자체가 멈춰버릴
            # 수 있습니다(메인 루프 전체 먹통, 여유 힙 메모리와는 무관).
            conn = None
            try:
                conn, addr = server_socket.accept()
            except OSError:
                conn = None

            if conn is not None:
                wifi_saved = False
                try:
                    # main.py를 웹 에디터로 열면 파일 전체(수십 KB)를 보내야
                    # 해서 느린 Wi-Fi에서는 여유가 필요함. /edit는 이제
                    # 스트리밍으로 보내 첫 바이트는 훨씬 빨리 나가지만, 전체
                    # 전송이 끝나기까지는 여전히 이 시간 안에 들어와야 해서
                    # 넉넉히 잡음.
                    conn.settimeout(10.0)
                    wifi_saved = handle_client(conn, state)
                except OSError:
                    pass
                except Exception as e:
                    log_error("클라이언트 처리", e)
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

                if wifi_saved:
                    server_socket.close()
                    return

            gc.collect()
            utime.sleep_ms(10)

    except Exception as e:
        log_error("서빙 루프", e)
        try:
            server_socket.close()
        except Exception:
            pass
        utime.sleep(1)


def _make_cloud_sync_task(state):
    """state를 클로저로 캡처해서, 호출될 때마다 그 시점의 최신 측정값으로
    동기화하는 무인자 함수를 만듭니다 (register_periodic_task는 무인자
    함수만 받음)."""
    def _cloud_sync_task():
        app_mod = state.app_mod
        if not (app_mod and hasattr(app_mod, 'sync_with_google_sheets')):
            return
        if state.mode != "ONLINE_STA" or not network.WLAN(network.STA_IF).isconnected():
            return
        try:
            app_mod.sync_with_google_sheets(state.value, state.avg_v, state.status_eng)
        except Exception as e:
            log_error("클라우드 동기화", e)
    return _cloud_sync_task


def main():
    print("==========================================")
    print(" 🛡️ Pico Core System 가동")
    print("==========================================")

    lcd = init_lcd()
    app_mod, app_err = load_active_app()

    state = LoopState()
    state.app_mod = app_mod
    state.app_err = app_err

    sync_interval = getattr(app_mod, "CLOUD_SYNC_INTERVAL_MS", 60000) if app_mod else 60000
    register_periodic_task("cloud_sync", _make_cloud_sync_task(state), sync_interval)
    register_periodic_task("ota_check", trigger_ota_check, OTA_CHECK_INTERVAL_MS)
    start_background_worker()

    while True:
        connect_network(lcd, state)
        serve_until_reconnect_needed(lcd, state)


if __name__ == "__main__":
    main()
