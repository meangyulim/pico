# =================================================================
# netutil.py : 하드웨어 의존성이 없는 순수 유틸리티
# =================================================================
# machine/network/socket 등을 import하지 않으므로 데스크톱 파이썬에서도
# 그대로 실행/테스트할 수 있습니다 (tests/test_netutil.py 참고).
# Pico에 업로드할 때 main.py와 함께 반드시 이 파일도 올려야 합니다.
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
