from netutil import (
    url_decode, esc, parse_request_line, content_length_of, split_headers,
    is_safe_filename,
)


# -----------------------------------------------------------------
# url_decode
# -----------------------------------------------------------------
def test_plus_becomes_space():
    assert url_decode("hello+world") == "hello world"


def test_percent_encoded_ascii():
    assert url_decode("a%20b") == "a b"


def test_percent_encoded_multibyte_utf8():
    # "한" = EC 95 9C in UTF-8
    assert url_decode("%ED%95%9C") == "한"


def test_trailing_incomplete_percent():
    assert url_decode("abc%4") == "abc%4"


def test_percent_at_end_of_string():
    assert url_decode("abc%") == "abc%"


def test_invalid_hex_falls_back_to_literal():
    assert url_decode("%zz") == "%zz"


def test_empty_string():
    assert url_decode("") == ""


def test_combines_plus_and_percent():
    assert url_decode("a+b%2Bc") == "a b+c"


# -----------------------------------------------------------------
# esc — 파일명 등 사용자 값이 마크업을 깨뜨리지 않아야 함
# -----------------------------------------------------------------
def test_esc_escapes_angle_brackets_and_amp():
    assert esc("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_esc_escapes_quotes():
    # 에디터의 confirm('...') 문자열 안에 들어가므로 따옴표가 중요
    assert esc("it's \"x\"") == "it&#39;s &quot;x&quot;"


def test_esc_ampersand_first():
    # &lt; 를 다시 이스케이프해서 &amp;lt; 가 되면 안 됨
    assert esc("<") == "&lt;"


def test_esc_non_string():
    assert esc(42) == "42"


# -----------------------------------------------------------------
# parse_request_line — 라우팅의 근간
# -----------------------------------------------------------------
def test_parse_simple_get():
    assert parse_request_line("GET / HTTP/1.1") == ("GET", "/", {})


def test_parse_with_query():
    m, p, q = parse_request_line("GET /edit?file=main.py HTTP/1.1")
    assert (m, p) == ("GET", "/edit")
    assert q == {"file": "main.py"}


def test_parse_post():
    m, p, q = parse_request_line("POST /save_code?file=app_idle.py HTTP/1.1")
    assert (m, p, q) == ("POST", "/save_code", {"file": "app_idle.py"})


def test_parse_distinguishes_nested_paths():
    # 예전 부분 문자열 라우팅에서는 /apps 가 /apps/set 을 가렸음
    assert parse_request_line("GET /apps HTTP/1.1")[1] == "/apps"
    assert parse_request_line("GET /apps/set?name=x HTTP/1.1")[1] == "/apps/set"
    assert parse_request_line("GET /power HTTP/1.1")[1] == "/power"
    assert parse_request_line("GET /power/halt HTTP/1.1")[1] == "/power/halt"


def test_parse_decodes_query_values():
    q = parse_request_line("GET /save?ssid=my+net&password=a%40b HTTP/1.1")[2]
    assert q == {"ssid": "my net", "password": "a@b"}


def test_parse_multiple_params_and_valueless():
    q = parse_request_line("GET /x?a=1&b&c=3 HTTP/1.1")[2]
    assert q["a"] == "1" and q["c"] == "3" and q["b"] == ""


def test_parse_malformed_line():
    assert parse_request_line("garbage") == ("", "", {})
    assert parse_request_line("") == ("", "", {})


def test_parse_value_containing_equals():
    q = parse_request_line("GET /x?t=a=b HTTP/1.1")[2]
    assert q["t"] == "a=b"


# -----------------------------------------------------------------
# content_length_of
# -----------------------------------------------------------------
def test_content_length_basic():
    assert content_length_of(b"POST / HTTP/1.1\r\nContent-Length: 123\r\n") == 123


def test_content_length_case_insensitive():
    assert content_length_of(b"content-length: 7\r\n") == 7
    assert content_length_of(b"CONTENT-LENGTH: 8\r\n") == 8


def test_content_length_missing():
    assert content_length_of(b"GET / HTTP/1.1\r\nHost: x\r\n") == 0


def test_content_length_garbage():
    assert content_length_of(b"Content-Length: abc\r\n") == 0


# -----------------------------------------------------------------
# split_headers
# -----------------------------------------------------------------
def test_split_headers_crlf():
    h, b = split_headers(b"GET / HTTP/1.1\r\nHost: x\r\n\r\nBODY")
    assert h == b"GET / HTTP/1.1\r\nHost: x" and b == b"BODY"


def test_split_headers_lf_only():
    h, b = split_headers(b"GET / HTTP/1.1\nHost: x\n\nBODY")
    assert h == b"GET / HTTP/1.1\nHost: x" and b == b"BODY"


def test_split_headers_no_separator_yet():
    h, b = split_headers(b"GET / HTTP/1.1\r\nHost: x")
    assert h == b"GET / HTTP/1.1\r\nHost: x" and b == b""


def test_split_headers_empty_body():
    h, b = split_headers(b"GET / HTTP/1.1\r\n\r\n")
    assert b == b""


# -----------------------------------------------------------------
# is_safe_filename — 경로 탈출 방지
# -----------------------------------------------------------------
def test_safe_filename_accepts_plain():
    assert is_safe_filename("main.py")
    assert is_safe_filename("debug.log")


def test_safe_filename_rejects_traversal():
    for bad in ["../boot.py", "a/b.py", "a\\b.py", "..", ""]:
        assert not is_safe_filename(bad), bad
