# =================================================================
# console_log.py : 원격 콘솔용 print 후킹 + 오류 로그 헬퍼
# =================================================================
# builtins.print를 감싸서 시리얼 출력은 그대로 두고, 최근 N줄을 메모리에도
# 저장합니다. main.py가 이 모듈을 가장 먼저 import해야 이후의 모든
# print() 호출(다른 모듈 것까지)이 잡힙니다.
# =================================================================
import builtins

LOG_BUFFER_MAX_LINES = 200
log_buffer = []
_real_print = builtins.print


def _tee_print(*args, **kwargs):
    _real_print(*args, **kwargs)
    try:
        line = " ".join(str(a) for a in args)
        log_buffer.append(line)
        if len(log_buffer) > LOG_BUFFER_MAX_LINES:
            del log_buffer[0]
    except Exception:
        pass


builtins.print = _tee_print


def log_error(context, exc):
    print(f"⚠️ [{context}] {type(exc).__name__}: {exc}")
