# =================================================================
# httpd.py : 웹 서버 — 요청 파싱, 라우팅, 응답 스트리밍
# =================================================================
# main.py에 뒤섞여 있던 HTTP 처리를 전부 여기로 옮겼습니다. main.py는
# 이제 부팅·연결·메인 루프만 담당합니다.
#
# 이전 구조에서 고친 것들:
#  * 라우팅이 첫 줄 부분 문자열 검사("GET /apps" in line)라, /apps가
#    /apps/set을 가려버리는 순서 의존성이 있었습니다. 이제 경로를 정확히
#    파싱해서 dict로 정확 일치 라우팅합니다 (ROUTES).
#  * 응답을 통째로 만들어 보내던 것을 조각 스트리밍으로 바꿨습니다.
#  * accept()의 정상적인 OSError와 요청 처리 중의 OSError를 구분하지
#    않아 소켓이 새던 문제 — 여기서는 무조건 finally에서 닫습니다.
#  * 재부팅이 필요한 동작은 곧바로 machine.reset()을 부르지 않고
#    state.pending_action에 남깁니다. 응답을 끝까지 흘려보내고 소켓을
#    닫은 뒤 메인 루프가 처리해야 브라우저가 결과 화면을 받습니다.
# =================================================================
import gc
import json
import socket

import web_ui
from netutil import parse_request_line, split_headers, content_length_of, esc
from file_editor import (
    handle_save_code, is_valid_editable_filename, list_editable_files, revert_file,
    file_exists,
)
from app_manager import (
    list_available_apps, get_active_app_name, set_active_app_name,
)
from ota import get_ota_status_text, get_last_update_text, request_manual_check
from wifi_manager import save_wifi_config
from console_log import log_buffer, log_error

try:
    import watchdog
except ImportError:
    class watchdog:
        @staticmethod
        def feed():
            pass

RECV_CHUNK = 1024
FILE_CHUNK = 512
# 워치독(watchdog.WDT_TIMEOUT_MS = 8000ms)보다 짧아야 합니다. conn.recv()나
# conn.sendall() 같은 블로킹 호출 하나가 이 시간만큼 걸릴 수 있는데, 호출이
# 끝나기 전에는 feed()를 부를 수 없습니다. 이 값이 워치독 타임아웃보다 길면
# 소켓이 스스로 타임아웃 예외를 던지기 전에 워치독이 먼저 강제 재부팅시킵니다
# (느린 Wi-Fi에서 첫 연결 시 실제로 발생 — 웹 페이지 열 때마다 재부팅되는 버그).
CONN_TIMEOUT_SEC = 5.0
SEND_BUFFER_SIZE = 1024


# -----------------------------------------------------------------
# 응답 헬퍼
# -----------------------------------------------------------------
def _send_head(conn, status, ctype, extra=""):
    conn.sendall(("HTTP/1.1 " + status +
                  "\r\nContent-Type: " + ctype +
                  "\r\nConnection: close\r\n" + extra + "\r\n").encode('utf-8'))


def send_stream(conn, chunks, status="200 OK", ctype="text/html; charset=utf-8"):
    """제너레이터가 내놓는 조각을 모아서 흘려보냅니다.
    Content-Length를 쓰지 않는 대신 Connection: close로 끝을 알립니다
    (길이를 알려면 페이지 전체를 메모리에 만들어야 하므로).

    web_ui의 페이지 하나가 짧은 조각을 40~50번 yield하는데, 조각마다
    바로 sendall()하면 그만큼 TCP round-trip이 생겨 느린 Wi-Fi에서
    페이지 하나 여는 데 체감상 느려집니다. SEND_BUFFER_SIZE만큼 모아서
    한 번에 보내면 메모리 사용량(버퍼 크기로 상한)은 그대로 낮게
    유지하면서 전송 횟수를 크게 줄일 수 있습니다.
    """
    _send_head(conn, status, ctype)
    buf = bytearray()
    for c in chunks:
        if isinstance(c, str):
            c = c.encode('utf-8')
        buf += c
        if len(buf) >= SEND_BUFFER_SIZE:
            conn.sendall(buf)
            watchdog.feed()  # 느린 Wi-Fi에서 전송이 길어질 수 있음
            buf = bytearray()
    if buf:
        conn.sendall(buf)
        watchdog.feed()


def send_body(conn, body, status="200 OK", ctype="text/plain; charset=utf-8"):
    """짧고 길이를 이미 아는 응답 (JSON, 로그 텍스트 등)."""
    if isinstance(body, str):
        body = body.encode('utf-8')
    _send_head(conn, status, ctype, "Content-Length: " + str(len(body)) + "\r\n")
    conn.sendall(body)


def redirect(conn, location):
    conn.sendall(("HTTP/1.1 303 See Other\r\nLocation: " + location +
                  "\r\nConnection: close\r\n\r\n").encode('utf-8'))


def _msg(conn, title, heading, body="", color="#38bdf8",
         redirect_to=None, delay_ms=5000, status="200 OK"):
    send_stream(conn, web_ui.message(title, heading, body, color, redirect_to, delay_ms),
                status=status)


# -----------------------------------------------------------------
# 라우트 핸들러 — 모두 (conn, state, params) 시그니처
# -----------------------------------------------------------------
def _r_data(conn, state, params):
    app = state.app_mod
    send_body(conn, json.dumps({
        "value": round(state.value, 1),
        "eng": state.status_eng,
        "kor": state.status_kor,
        "color": state.color_hex,
        "cloud": getattr(app, "cloud_sync_status", "준비") if app else "앱 오류",
        "mute": getattr(app, "is_muted", False) if app else False,
        "thresh": getattr(app, "alert_threshold", 0.0) if app else 0.0,
        "ota": get_ota_status_text(),
        "last_update": get_last_update_text(),
        "mem_free": gc.mem_free(),
        "uptime": state.uptime_str(),
    }), ctype="application/json")


def _r_logs_txt(conn, state, params):
    send_body(conn, "\n".join(log_buffer))


def _r_logs(conn, state, params):
    send_stream(conn, web_ui.logs_page())


def _r_favicon(conn, state, params):
    conn.sendall(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")


def _r_root(conn, state, params):
    app = state.app_mod
    send_stream(conn, web_ui.dashboard({
        "mode": state.mode,
        "ip": state.current_ip,
        "wifis": state.wifi_list,
        "app_err": state.app_err,
        "value": state.value,
        "status_eng": state.status_eng,
        "status_kor": state.status_kor,
        "color": state.color_hex,
        "cloud": getattr(app, "cloud_sync_status", "대기 중") if app else "앱 오류",
        "mute": getattr(app, "is_muted", False) if app else False,
        "thresh": getattr(app, "alert_threshold", 0.0) if app else 0.0,
        "ota": get_ota_status_text(),
        "active_app": get_active_app_name(),
        "last_update": get_last_update_text(),
    }))


def _escaped_file_chunks(path):
    """파일을 조각 단위로 읽어 HTML 이스케이프하며 흘려보냅니다.

    예전에는 f.read()로 파일 전체를 문자열 하나에 올린 뒤 잘라 보냈습니다
    — main.py처럼 35KB짜리 파일이면 그만한 연속 블록을 요구해서, 힙이
    조각난 상태에서는 실패할 수 있었습니다.
    """
    try:
        with open(path, "r") as f:
            while True:
                piece = f.read(FILE_CHUNK)
                if not piece:
                    break
                yield piece.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                watchdog.feed()
    except OSError:
        return  # 아직 없는 파일 -> 새로 만드는 셈이므로 빈 내용


def _r_edit(conn, state, params):
    name = params.get('file', '').strip()
    if not name:
        send_stream(conn, web_ui.file_list(list_editable_files()))
        return
    if not is_valid_editable_filename(name):
        _msg(conn, "편집 불가", "❌ 편집할 수 없는 파일입니다", esc(name),
             "#fca5a5", status="400 Bad Request")
        return
    # head -> (파일 내용 스트리밍) -> tail 을 하나의 흐름으로 이어 붙입니다.
    def body():
        yield from web_ui.editor_head(name, file_exists(name + ".bak"))
        yield from _escaped_file_chunks(name)
        yield from web_ui.editor_tail(name)
    send_stream(conn, body())
    gc.collect()


def _r_save_code(conn, state, params, header_bytes=b"", initial_body=b""):
    name = params.get('file', '').strip()
    if not is_valid_editable_filename(name):
        _msg(conn, "저장 실패", "❌ 저장 실패", "편집할 수 없는 파일명: " + esc(name),
             "#fca5a5", status="400 Bad Request")
        return
    ok, msg, changed = handle_save_code(
        conn, initial_body, content_length_of(header_bytes), name)
    if ok and changed:
        _msg(conn, "저장 완료", "✅ " + name + " 저장 완료",
             "재부팅합니다... 잠시 후 메인 화면으로 돌아갑니다.", "#22c55e",
             redirect_to="/", delay_ms=25000)
        state.pending_action = "reboot"
    elif ok:
        _msg(conn, "변경 없음", "💾 저장 완료 (변경 없음)",
             "기존 내용과 같아 재부팅하지 않았습니다.", "#38bdf8")
    else:
        _msg(conn, "저장 실패", "❌ 저장 실패", esc(msg), "#fca5a5",
             status="413 Payload Too Large" if "너무 큽니다" in msg
                    else "500 Internal Server Error")


def _r_revert(conn, state, params):
    name = params.get('file', '').strip()
    if is_valid_editable_filename(name) and revert_file(name):
        print("↩️ " + name + "을(를) 이전 버전으로 되돌렸습니다.")
        _msg(conn, "복원 완료", "↩️ " + name + " 복원 완료", "재부팅합니다...",
             "#22c55e", redirect_to="/", delay_ms=25000)
        state.pending_action = "reboot"
    else:
        redirect(conn, "/edit?file=" + name)


def _r_save_wifi(conn, state, params):
    ssid = params.get('ssid', '').strip()
    if not ssid:
        redirect(conn, "/")
        return
    save_wifi_config(ssid, params.get('password', '').strip())
    _msg(conn, "Wi-Fi 저장", "✅ Wi-Fi 저장 완료",
         "[" + esc(ssid) + "] 연결을 시작합니다.", "#22c55e")
    state.pending_action = "reconnect"


def _r_apps(conn, state, params):
    send_stream(conn, web_ui.app_list(list_available_apps(), get_active_app_name()))


def _r_apps_set(conn, state, params):
    name = params.get('name', '').strip()
    if name and name in list_available_apps():
        set_active_app_name(name)
        print("🔌 활성 앱을 " + name + "(으)로 전환합니다. 재부팅합니다...")
        _msg(conn, "앱 전환", "🔌 " + name + "(으)로 전환 완료", "재부팅합니다...",
             "#22c55e", redirect_to="/", delay_ms=25000)
        state.pending_action = "reboot"
    else:
        redirect(conn, "/apps")


def _r_ota_check(conn, state, params):
    request_manual_check()
    redirect(conn, "/")


def _r_power(conn, state, params):
    try:
        import machine
        mhz = machine.freq() // 1000000
    except Exception:
        mhz = "?"
    send_stream(conn, web_ui.power_page(
        state.power_mode, state.uptime_str(), mhz, watchdog_active()))


def watchdog_active():
    try:
        return watchdog.is_active()
    except AttributeError:
        return False


def _r_power_reboot(conn, state, params):
    print("🔄 [전원] 웹 요청으로 재부팅합니다...")
    _msg(conn, "다시 시작", "🔄 다시 시작 중...",
         "20~30초 후 자동으로 메인 화면을 엽니다.", "#38bdf8",
         redirect_to="/", delay_ms=30000)
    state.pending_action = "reboot"


def _r_power_sleep(conn, state, params):
    state.pending_action = "sleep"
    redirect(conn, "/power")


def _r_power_wake(conn, state, params):
    state.pending_action = "wake"
    redirect(conn, "/power")


def _r_power_halt(conn, state, params):
    print("⏻ [전원] 시스템을 종료합니다 (전원 재인가 전까지 정지)")
    _msg(conn, "시스템 종료", "⏻ 시스템을 종료했습니다",
         "이제 전원을 뽑아도 안전합니다.<br>다시 켜려면 전원을 뽑았다 꽂으세요.",
         "#fca5a5")
    state.pending_action = "halt"


ROUTES = {
    ("GET", "/"): _r_root,
    ("GET", "/data"): _r_data,
    ("GET", "/logs"): _r_logs,
    ("GET", "/logs.txt"): _r_logs_txt,
    ("GET", "/favicon.ico"): _r_favicon,
    ("GET", "/edit"): _r_edit,
    ("GET", "/revert"): _r_revert,
    ("GET", "/save"): _r_save_wifi,
    ("GET", "/apps"): _r_apps,
    ("GET", "/apps/set"): _r_apps_set,
    ("GET", "/ota/check"): _r_ota_check,
    ("GET", "/power"): _r_power,
    ("GET", "/power/reboot"): _r_power_reboot,
    ("GET", "/power/sleep"): _r_power_sleep,
    ("GET", "/power/wake"): _r_power_wake,
    ("GET", "/power/halt"): _r_power_halt,
    ("POST", "/save_code"): _r_save_code,
}


def handle(conn, state):
    """커넥션 하나를 끝까지 처리합니다. 소켓 닫기는 호출한 쪽 책임입니다."""
    raw = conn.recv(RECV_CHUNK)
    if not raw:
        return
    header_bytes, initial_body = split_headers(raw)
    first_line = header_bytes.split(b"\r\n", 1)[0].decode('utf-8', 'ignore').strip()
    method, path, params = parse_request_line(first_line)

    handler = ROUTES.get((method, path))
    if handler is None:
        if method == "GET":
            # 알 수 없는 GET은 대시보드로 (브라우저가 임의 경로를 찔러도 안전)
            _r_root(conn, state, params)
        else:
            _msg(conn, "없음", "❌ 없는 페이지", esc(path), "#fca5a5",
                 status="404 Not Found")
        return

    if method == "POST":
        handler(conn, state, params, header_bytes, initial_body)
    else:
        handler(conn, state, params)


def make_server(port=80):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('', port))
    s.listen(5)
    s.settimeout(0.02)  # 논블로킹에 가깝게 — 메인 루프가 계속 돌아야 함
    return s


def poll(server_socket, state):
    """대기 중인 요청이 있으면 하나 처리합니다.

    accept()가 던지는 OSError(대기 중인 연결 없음 — settimeout(0.02)
    때문에 거의 매 루프마다 정상적으로 발생)와, 연결을 받은 뒤 처리 중
    발생하는 오류를 분리합니다. 예전에는 둘을 같은 except로 묶어서,
    후자의 경우 conn.close()를 건너뛰어 소켓이 샜습니다 — 대시보드를
    열어두면(2초마다 폴링) 빠르게 누적돼 결국 accept()가 멈췄습니다.
    """
    try:
        conn, _ = server_socket.accept()
    except OSError:
        return

    try:
        conn.settimeout(CONN_TIMEOUT_SEC)
        handle(conn, state)
    except OSError:
        pass  # 클라이언트가 도중에 끊음 — 흔한 일이라 로그를 남기지 않음
    except Exception as e:
        log_error("요청 처리", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
