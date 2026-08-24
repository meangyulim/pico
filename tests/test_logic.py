"""file_editor의 폼 디코더, ota의 대상 판정, httpd의 라우팅 표 테스트."""
import file_editor
import ota
import httpd
import cpu_config


# -----------------------------------------------------------------
# _FormDecoder — POST 본문을 조각 단위로 디코딩합니다.
# 조각 경계가 '%4' 처럼 이스케이프 중간을 자를 수 있어 상태 유지가 핵심.
# -----------------------------------------------------------------
def decode(chunks):
    d = file_editor._FormDecoder()
    out = bytearray()
    for c in chunks:
        out.extend(d.feed(c))
    out.extend(d.finish())
    return bytes(out)


def test_decoder_strips_code_prefix_once():
    assert decode([b"code=hello"]) == b"hello"


def test_decoder_prefix_only_on_first_chunk():
    # 두 번째 조각에 우연히 code= 가 나와도 지우면 안 됨
    assert decode([b"code=a", b"code=b"]) == b"acode=b"


def test_decoder_plus_and_percent():
    assert decode([b"code=a+b%20c"]) == b"a b c"


def test_decoder_utf8_multibyte():
    assert decode([b"code=%ED%95%9C"]).decode() == "한"


def test_decoder_split_across_percent():
    """'%ED' 가 세 조각으로 쪼개져 들어와도 같은 결과여야 합니다."""
    whole = decode([b"code=%ED%95%9C"])
    for cut in range(6, 17):
        s = b"code=%ED%95%9C"
        assert decode([s[:cut], s[cut:]]) == whole, cut


def test_decoder_split_every_single_byte():
    s = b"code=a+b%20c%ED%95%9C"
    one_by_one = decode([bytes([b]) for b in s])
    assert one_by_one == decode([s])


def test_decoder_incomplete_escape_at_end_preserved():
    assert decode([b"code=abc%4"]) == b"abc%4"
    assert decode([b"code=abc%"]) == b"abc%"


def test_decoder_invalid_hex_kept_literal():
    assert decode([b"code=%zz"]) == b"%zz"


def test_decoder_newlines_roundtrip():
    src = "def f():\n    return '한글'\n"
    enc = b"code=" + "".join(
        "%%%02X" % b for b in src.encode()).encode()
    assert decode([enc]).decode() == src


def test_decoder_empty():
    assert decode([b"code="]) == b""


# -----------------------------------------------------------------
# ota._is_ota_target — 새 파일도 통과해야 하고, 위험한 이름은 막아야 함
# -----------------------------------------------------------------
def test_ota_accepts_plain_py():
    for n in ["main.py", "web_ui.py", "watchdog.py", "httpd.py", "app_new.py"]:
        assert ota._is_ota_target(n), n


def test_ota_rejects_metadata_key():
    # manifest의 "_version" 같은 키가 파일로 오인되면 안 됨
    assert not ota._is_ota_target("_version")


def test_ota_rejects_path_traversal():
    for n in ["../boot.py", "/etc/passwd", "a/b.py", "a\\b.py", "..%2Fx.py"]:
        assert not ota._is_ota_target(n), n


def test_ota_rejects_device_local_files():
    for n in ota.OTA_PROTECTED_FILES:
        assert not ota._is_ota_target(n), n


def test_ota_rejects_non_py():
    for n in ["manifest.json", "notes.txt", "evil.sh", "", "README.md"]:
        assert not ota._is_ota_target(n), n


def test_ota_manual_by_default():
    # 자동 폴링을 껐다는 사실 자체를 고정해 둡니다
    assert ota.OTA_AUTO_CHECK is False


def test_ota_poll_does_nothing_without_request():
    ota._manual_requested = False
    before = ota._last_check_ms
    ota.poll()
    assert ota._last_check_ms is before   # 네트워크를 건드리지 않음


def test_ota_status_text_before_any_check():
    ota._last_check_ms = None
    ota._in_progress = False
    assert "수동" in ota.get_ota_status_text()


# -----------------------------------------------------------------
# httpd 라우팅 표
# -----------------------------------------------------------------
def test_routes_cover_expected_paths():
    expected = {
        ("GET", "/"), ("GET", "/data"), ("GET", "/logs"), ("GET", "/logs.txt"),
        ("GET", "/edit"), ("GET", "/revert"), ("GET", "/save"),
        ("GET", "/apps"), ("GET", "/apps/set"), ("GET", "/ota/check"),
        ("GET", "/power"), ("GET", "/power/reboot"), ("GET", "/power/sleep"),
        ("GET", "/power/wake"), ("GET", "/power/freq"), ("GET", "/power/halt"),
        ("POST", "/save_code"), ("GET", "/favicon.ico"),
    }
    assert expected == set(httpd.ROUTES)


# -----------------------------------------------------------------
# cpu_config — 오버클럭 선택 저장/불러오기
# -----------------------------------------------------------------
def test_cpu_config_defaults_to_150_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cpu_config.load_freq_mhz() == 150


def test_cpu_config_rejects_out_of_range_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(cpu_config.CONFIG_FILE, "w") as f:
        f.write('{"freq_mhz": 999}')
    assert cpu_config.load_freq_mhz() == 150


def test_cpu_config_roundtrips_valid_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cpu_config.save_freq_mhz(250)
    assert cpu_config.load_freq_mhz() == 250


def test_nested_routes_are_distinct_entries():
    """부분 문자열 라우팅이었다면 /apps 가 /apps/set 을 가렸습니다."""
    assert httpd.ROUTES[("GET", "/apps")] is not httpd.ROUTES[("GET", "/apps/set")]
    assert httpd.ROUTES[("GET", "/power")] is not httpd.ROUTES[("GET", "/power/halt")]


def test_all_route_handlers_callable():
    for key, fn in httpd.ROUTES.items():
        assert callable(fn), key
