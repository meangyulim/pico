"""web_ui 페이지 제너레이터 테스트.

web_ui는 하드웨어 모듈을 import하지 않는 순수 문자열 생성이라
데스크톱에서 그대로 검증할 수 있습니다.
"""
import web_ui


DASH = {
    "mode": "ONLINE_STA", "ip": "192.168.0.71", "wifis": ["LIM_", "iptime"],
    "saved_wifis": ["LIM_", "iptime_5G"],
    "app_err": None, "value": 12.0, "status_eng": "IDLE", "status_kor": "대기",
    "color": "#64748b", "cloud": "대기 모드", "mute": True, "thresh": 0.0,
    "ota": "8초 전 - 변경 없음", "active_app": "app_idle",
    "last_update": "2026-08-23 16:22 (버전 abc)",
}


def render(gen):
    return "".join(gen)


ALL_PAGES = {
    "dashboard": lambda: web_ui.dashboard(DASH),
    "logs": web_ui.logs_page,
    "file_list": lambda: web_ui.file_list(["main.py", "ota.py"]),
    "file_list_empty": lambda: web_ui.file_list([]),
    "app_list": lambda: web_ui.app_list(["app_idle", "app_dust_monitor"], "app_idle"),
    "app_list_empty": lambda: web_ui.app_list([], "app_idle"),
    "power_on": lambda: web_ui.power_page("ON", "3시간 5분", 150, True, (150, 250), 150),
    "power_sleep": lambda: web_ui.power_page("SLEEP", "1분", 150, False, (150, 250), 150),
    "message": lambda: web_ui.message("t", "제목", "본문"),
    "editor_head": lambda: web_ui.editor_head("main.py", True),
    "editor_tail": lambda: web_ui.editor_tail("main.py"),
}


def test_every_page_renders_nonempty():
    for name, fn in ALL_PAGES.items():
        assert len(render(fn())) > 50, name


def test_pages_are_generators_not_strings():
    # 통째로 만들지 않고 조각을 흘려보내는 것이 이 설계의 핵심입니다.
    for name, fn in ALL_PAGES.items():
        g = fn()
        assert hasattr(g, "__next__"), name


def test_chunks_stay_small():
    """조각 하나가 커지면 스트리밍의 의미가 없어집니다.

    예전 구조는 대시보드 한 페이지를 7KB 문자열 하나로 만들었습니다.
    조각 상한을 테스트로 못박아 두면, 나중에 누가 페이지를 f-string
    하나로 되돌려도 여기서 걸립니다.
    """
    for name, fn in ALL_PAGES.items():
        biggest = max(len(c) for c in fn())
        assert biggest < 1600, "{}: 조각이 {}바이트로 너무 큼".format(name, biggest)


def test_full_html_document_structure():
    for name in ("dashboard", "logs", "file_list", "app_list", "power_on", "message"):
        html = render(ALL_PAGES[name]())
        assert html.startswith("<!DOCTYPE html>"), name
        assert html.rstrip().endswith("</html>"), name
        assert html.count("<body>") == 1, name


def test_editor_head_and_tail_form_one_document():
    html = render(web_ui.editor_head("main.py", True)) + "code" \
        + render(web_ui.editor_tail("main.py"))
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<textarea") == 1 and html.count("</textarea>") == 1
    assert html.count("<form") == 1 and html.count("</form>") == 1


def test_editor_shows_revert_only_with_backup():
    assert "/revert?file=" in render(web_ui.editor_head("main.py", True))
    assert "/revert?file=" not in render(web_ui.editor_head("main.py", False))


def test_dashboard_shows_app_error_banner():
    d = dict(DASH, app_err="SyntaxError: bad")
    html = render(web_ui.dashboard(d))
    assert "앱 실행 오류" in html and "SyntaxError: bad" in html
    assert "앱 실행 오류" not in render(web_ui.dashboard(DASH))


def test_dashboard_lists_wifis_and_links():
    html = render(web_ui.dashboard(DASH))
    for w in DASH["wifis"]:
        assert w in html
    for href in ("/ota/check", "/apps", "/edit", "/logs", "/power"):
        assert 'href="' + href + '"' in html


def test_dashboard_lists_saved_wifis_with_forget_link():
    html = render(web_ui.dashboard(DASH))
    for ssid in DASH["saved_wifis"]:
        assert "/wifi/forget?ssid=" + ssid in html


def test_dashboard_saved_wifis_optional():
    # 기존 호출자가 saved_wifis 없이 dict를 넘겨도 죽지 않아야 합니다.
    d = {k: v for k, v in DASH.items() if k != "saved_wifis"}
    html = render(web_ui.dashboard(d))
    assert "<!DOCTYPE html>" in html


def test_dashboard_saved_wifi_name_escaped():
    d = dict(DASH, saved_wifis=["<script>x</script>"])
    html = render(web_ui.dashboard(d))
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_app_list_marks_active_and_links_others():
    html = render(web_ui.app_list(["app_idle", "app_dust_monitor"], "app_idle"))
    assert "사용 중" in html
    assert "/apps/set?name=app_dust_monitor" in html
    # 현재 앱은 다시 전환할 링크를 주지 않음
    assert "/apps/set?name=app_idle" not in html


def test_power_page_reflects_state():
    on = render(web_ui.power_page("ON", "1분", 150, True, (150, 250), 150))
    assert "/power/sleep" in on and "/power/wake" not in on
    assert "켜짐" in on
    sl = render(web_ui.power_page("SLEEP", "1분", 150, False, (150, 250), 150))
    assert "/power/wake" in sl and "/power/sleep" not in sl
    assert "꺼짐" in sl


def test_power_page_freq_options_and_selection():
    html = render(web_ui.power_page("ON", "1분", 250, True, (150, 200, 250), 250))
    assert '<option value="150"' in html
    assert '<option value="200"' in html
    assert '<option value="250" selected>' in html
    assert "/power/freq" in html


def test_message_redirect_optional():
    assert "setTimeout" in render(web_ui.message("t", "h", redirect="/", delay_ms=1234))
    assert "1234" in render(web_ui.message("t", "h", redirect="/", delay_ms=1234))
    assert "setTimeout" not in render(web_ui.message("t", "h"))


# -----------------------------------------------------------------
# 이스케이프 — 파일명은 사용자가 정하므로 마크업을 깨뜨릴 수 있습니다
# -----------------------------------------------------------------
def test_filenames_are_escaped_in_file_list():
    html = render(web_ui.file_list(['<script>x</script>.py']))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_filename_escaped_in_editor_confirm_string():
    # confirm('...') 안에 들어가므로 작은따옴표가 특히 위험합니다
    html = render(web_ui.editor_head("it's.py", False)) \
        + render(web_ui.editor_tail("it's.py"))
    assert "it's.py" not in html
    assert "&#39;" in html


def test_app_names_escaped():
    html = render(web_ui.app_list(["app_<b>.py"], "other"))
    assert "<b>" not in html.split("<style>")[1]


def test_dashboard_escapes_error_text():
    d = dict(DASH, app_err='<img onerror="x">')
    html = render(web_ui.dashboard(d))
    assert '<img onerror=' not in html
    assert "&lt;img" in html


def test_css_defined_once_per_page():
    # BASE_CSS를 공유하므로 페이지마다 <style>은 한 번만 나와야 합니다
    for name in ("dashboard", "logs", "file_list", "app_list", "power_on"):
        assert render(ALL_PAGES[name]()).count("<style>") == 1, name
