# =================================================================
# netutil.py : 하드웨어 의존성이 없는 순수 유틸리티
# =================================================================
# machine/network/socket 등을 import하지 않으므로 데스크톱 파이썬에서도
# 그대로 실행/테스트할 수 있습니다 (tests/test_netutil.py 참고).
# HTTP 요청 파싱처럼 "틀리기 쉬운데 눈으로는 확인이 어려운" 로직을
# 되도록 여기에 모아두고 테스트로 지킵니다.
# =================================================================


def url_decode(s):
    """application/x-www-form-urlencoded 값을 UTF-8 기준으로 디코딩합니다."""
    s = s.replace('+', ' ')
    raw = bytearray()
    i = 0
    length = len(s)
    while i < length:
        ch = s[i]
        if ch == '%' and i + 2 < length:
            try:
                raw.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        raw.extend(ch.encode('utf-8'))
        i += 1
    try:
        return raw.decode('utf-8')
    except Exception:
        return "".join(chr(b) for b in raw)


def esc(s):
    """HTML 본문에 값을 넣기 전 최소한의 이스케이프.

    파일명처럼 사용자가 준 값을 그대로 페이지에 찍으면 따옴표/꺾쇠가
    마크업을 깨뜨리므로(예: 에디터의 confirm() 문자열) 항상 거칩니다.
    """
    if not isinstance(s, str):
        s = str(s)
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;').replace("'", '&#39;'))


def parse_request_line(line):
    """'GET /edit?file=main.py HTTP/1.1' -> ('GET', '/edit', {'file': 'main.py'})

    메서드와 경로를 분리해서 돌려줍니다. 예전에는 라우팅을 첫 줄에 대한
    부분 문자열 검사("GET /apps" in line)로 했는데, 그러면 /apps 가
    /apps/set 을 가려버려서 등록 순서에 의존하는 함정이 있었습니다.
    여기서 경로를 정확히 뽑아내 정확 일치로 라우팅합니다.
    """
    parts = line.split(' ')
    if len(parts) < 2:
        return "", "", {}
    method = parts[0]
    target = parts[1]
    if '?' in target:
        path, query = target.split('?', 1)
    else:
        path, query = target, ""
    params = {}
    for item in query.split('&'):
        if '=' in item:
            k, v = item.split('=', 1)
            params[url_decode(k)] = url_decode(v)
        elif item:
            params[url_decode(item)] = ""
    return method, path, params


def content_length_of(header_bytes):
    """요청 헤더(bytes)에서 Content-Length 값을 찾아 int로 돌려줍니다.
    없거나 이상하면 0. 헤더 이름은 대소문자를 가리지 않습니다."""
    for line in header_bytes.split(b"\r\n"):
        if line[:15].lower() == b"content-length:":
            try:
                return int(line[15:].strip())
            except Exception:
                return 0
    return 0


def split_headers(raw):
    """수신한 첫 덩어리를 (헤더 bytes, 이미 딸려온 본문 bytes)로 나눕니다.

    CRLFCRLF가 정석이지만 LFLF만 보내는 클라이언트도 있어 둘 다 봅니다.
    구분자를 아직 못 찾았으면 전부 헤더로 간주하고 본문은 비웁니다.
    """
    idx = raw.find(b"\r\n\r\n")
    if idx != -1:
        return raw[:idx], raw[idx + 4:]
    idx = raw.find(b"\n\n")
    if idx != -1:
        return raw[:idx], raw[idx + 2:]
    return raw, b""


def is_safe_filename(name):
    """기기 최상위에 있는 평범한 파일 이름인지 검사합니다.
    경로 구분자나 상위 참조가 섞여 있으면 거부합니다."""
    if not name:
        return False
    if '/' in name or '\\' in name or '..' in name:
        return False
    return True
