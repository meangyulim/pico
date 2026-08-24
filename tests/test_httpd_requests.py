"""httpd.handle()을 가짜 소켓으로 통과시키는 통합 테스트.

컴파일 검사만으로는 잡히지 않는 배선 오류(함수 이름 오타, 인자 개수,
제너레이터/문자열 혼동 등)를 실제 요청 경로로 확인합니다.
"""
import json

import httpd


class FakeConn:
    """recv/sendall만 흉내 내는 소켓 대역."""

    def __init__(self, request=b""):
        self._to_read = request
        self.sent = bytearray()

    def recv(self, n):
        chunk, self._to_read = self._to_read[:n], self._to_read[n:]
        return chunk

    def sendall(self, data):
        self.sent.extend(data)

    def settimeout(self, t):
        pass

    def close(self):
        pass

    # -- 테스트 편의 --
    def text(self):
        return bytes(self.sent).decode("utf-8", "replace")

    def status(self):
        return self.text().split("\r\n", 1)[0]

    def body(self):
        t = self.text()
        return t.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in t else ""


class FakeState:
    def __init__(self):
        self.mode = "ONLINE_STA"
        self.current_ip = "192.168.0.71"
        self.wifi_list = ["LIM_"]
        self.app_mod = None
        self.app_err = None
        self.value = 0.0
        self.avg_v = 0.0
        self.status_eng = "IDLE"
        self.status_kor = "대기"
        self.color_hex = "#64748b"
        self.power_mode = "ON"
        self.pending_action = None

    def uptime_str(self):
        return "1분"


def get(path):
    conn = FakeConn(("GET " + path + " HTTP/1.1\r\nHost: x\r\n\r\n").encode())
    state = FakeState()
    httpd.handle(conn, state)
    return conn, state


# -----------------------------------------------------------------
# 각 라우트가 응답을 실제로 만들어내는지
# -----------------------------------------------------------------
def test_root_serves_dashboard():
    conn, _ = get("/")
    assert "200 OK" in conn.status()
    assert "text/html" in conn.text()
    assert "<!DOCTYPE html>" in conn.body()


def test_data_returns_valid_json():
    conn, _ = get("/data")
    assert "application/json" in conn.text()
    d = json.loads(conn.body())
    for key in ("value", "eng", "kor", "color", "cloud", "mute", "thresh",
                "ota", "last_update", "mem_free", "uptime"):
        assert key in d, key


def test_data_keys_match_dashboard_javascript():
    """대시보드 JS가 읽는 필드와 /data가 주는 필드가 어긋나면
    화면이 조용히 '-' 로 남습니다. 그 어긋남을 여기서 잡습니다."""
    import web_ui
    html = "".join(web_ui.dashboard({
        "mode": "ONLINE_STA", "ip": "1", "wifis": [], "app_err": None,
        "value": 0.0, "status_eng": "I", "status_kor": "대", "color": "#fff",
        "cloud": "c", "mute": False, "thresh": 0.0, "ota": "o",
        "active_app": "a", "last_update": "u"}))
    payload = json.loads(get("/data")[0].body())
    for field in ("value", "cloud", "mute", "thresh", "ota",
                  "last_update", "mem_free", "uptime", "kor", "eng", "color"):
        assert "d." + field in html, "JS가 안 쓰는 필드: " + field
        assert field in payload, "/data가 안 주는 필드: " + field


def test_logs_page_and_text():
    assert "200 OK" in get("/logs")[0].status()
    conn, _ = get("/logs.txt")
    assert "text/plain" in conn.text()


def test_favicon_is_204():
    assert "204" in get("/favicon.ico")[0].status()


def test_edit_lists_files():
    conn, _ = get("/edit")
    assert "파일 브라우저" in conn.body()


def test_edit_streams_existing_file():
    conn, _ = get("/edit?file=netutil.py")
    body = conn.body()
    assert "<textarea" in body and "</textarea>" in body
    # 실제 파일 내용이 이스케이프되어 실려야 함
    assert "url_decode" in body


def test_edit_rejects_unsafe_filename():
    conn, _ = get("/edit?file=../boot.py")
    assert "400" in conn.status()


def test_edit_rejects_boot_py():
    conn, _ = get("/edit?file=boot.py")
    assert "400" in conn.status()


def test_edit_new_file_is_empty_editor():
    conn, _ = get("/edit?file=app_brand_new.py")
    body = conn.body()
    assert "<textarea" in body
    assert "></textarea>" in body.replace("\n", "")


def test_apps_page():
    assert "앱 전환" in get("/apps")[0].body()


def test_power_page():
    body = get("/power")[0].body()
    assert "전원 관리" in body and "/power/halt" in body
    assert "/power/freq" in body


def test_power_freq_valid_value_saves_and_reboots(tmp_path, monkeypatch):
    import cpu_config
    monkeypatch.chdir(tmp_path)
    # OFFLINE_AP 모드에서는 비교할 라우터가 없어 Wi-Fi 생존 검증을
    # 건너뛰므로, 저장 자체는 되는지를 이 상태에서 확인합니다. STA에서의
    # 검증 로직 자체는 test_logic.py의 _wifi_survives_freq 테스트가 맡습니다.
    conn = FakeConn(b"GET /power/freq?mhz=250 HTTP/1.1\r\nHost: x\r\n\r\n")
    state = FakeState()
    state.mode = "OFFLINE_AP"
    httpd.handle(conn, state)
    assert "200 OK" in conn.status()
    assert state.pending_action == "reboot"
    assert cpu_config.load_freq_mhz() == 250


def test_power_freq_invalid_value_redirects_without_change(tmp_path, monkeypatch):
    import cpu_config
    monkeypatch.chdir(tmp_path)
    cpu_config.save_freq_mhz(150)
    conn, state = get("/power/freq?mhz=9999")
    assert "303" in conn.status()
    assert state.pending_action is None
    assert cpu_config.load_freq_mhz() == 150


def test_ota_check_redirects_and_sets_flag():
    import ota
    ota._manual_requested = False
    conn, _ = get("/ota/check")
    assert "303" in conn.status()
    assert ota._manual_requested is True
    ota._manual_requested = False


# -----------------------------------------------------------------
# pending_action — 응답을 보낸 뒤 메인 루프가 실행하는 방식
# -----------------------------------------------------------------
def test_power_actions_set_pending_action():
    for path, expected in (("/power/reboot", "reboot"), ("/power/sleep", "sleep"),
                           ("/power/wake", "wake"), ("/power/halt", "halt")):
        conn, state = get(path)
        assert state.pending_action == expected, path
        assert conn.sent, path      # 응답을 먼저 내보내야 함


def test_reboot_response_sent_before_action():
    """재부팅 응답이 실제로 소켓에 실려야 브라우저가 안내 화면을 봅니다."""
    conn, state = get("/power/reboot")
    assert "200 OK" in conn.status()
    assert "다시 시작" in conn.body()
    assert state.pending_action == "reboot"


def test_unknown_get_falls_back_to_dashboard():
    conn, _ = get("/nope/nothing")
    assert "200 OK" in conn.status()
    assert "<!DOCTYPE html>" in conn.body()


def test_unknown_post_is_404():
    conn = FakeConn(b"POST /nope HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
    httpd.handle(conn, FakeState())
    assert "404" in conn.status()


def test_empty_request_does_not_crash():
    conn = FakeConn(b"")
    httpd.handle(conn, FakeState())
    assert conn.sent == bytearray()


def test_garbage_request_does_not_crash():
    conn = FakeConn(b"\x00\x01garbage\r\n\r\n")
    httpd.handle(conn, FakeState())   # 예외가 나지 않으면 성공


# -----------------------------------------------------------------
# 응답 헤더 형식
# -----------------------------------------------------------------
def test_streamed_responses_close_connection():
    # Content-Length 없이 스트리밍하므로 Connection: close로 끝을 알림
    conn, _ = get("/")
    head = conn.text().split("\r\n\r\n", 1)[0]
    assert "Connection: close" in head
    assert "Content-Length" not in head


def test_short_responses_have_content_length():
    conn, _ = get("/data")
    head = conn.text().split("\r\n\r\n", 1)[0]
    assert "Content-Length:" in head
